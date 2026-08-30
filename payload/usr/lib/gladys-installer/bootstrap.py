#!/usr/bin/env python3
"""Idempotent first-boot provisioning for the Gladys appliance."""

from __future__ import annotations

import json
import logging
import os
import platform
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

try:
    import fcntl
except ImportError:  # pragma: no cover - the installed appliance is Linux-only
    fcntl = None  # type: ignore[assignment]

import common


LOG = logging.getLogger("gladys-firstboot")
PACKAGES = (
    "docker.io",
    "docker-compose-v2",
    "unattended-upgrades",
    "ca-certificates",
    "curl",
)
APT_OPTIONS = (
    "-o",
    "Acquire::Retries=3",
    "-o",
    "Acquire::http::Timeout=30",
    "-o",
    "Acquire::https::Timeout=30",
    "-o",
    "DPkg::Lock::Timeout=300",
)
APT_UPDATE_TIMEOUT = 600
PACKAGE_OPERATION_TIMEOUT = 3600
CONTAINER_PULL_TIMEOUT = 3600

T = TypeVar("T")
COMMAND_FAILURES = (common.CommandError, OSError, subprocess.TimeoutExpired)


@dataclass(frozen=True)
class InstallerPaths:
    state: Path = Path("/var/lib/gladys-installer/state.json")
    completed: Path = Path("/var/lib/gladys-installer/completed")
    lock: Path = Path("/run/gladys-installer/bootstrap.lock")
    compose: Path = Path("/opt/gladys/compose.yaml")
    version: Path = Path("/etc/gladys-installer/version")
    data: Path = Path("/var/lib/gladysassistant")
    test_mode: Path = Path("/etc/gladys-installer/test-mode")
    test_compose: Path = Path("/etc/gladys-installer/test-compose.yaml")
    ci_barrier_package: Path = Path(
        "/etc/gladys-installer/gladys-ci-dpkg-barrier_1.0_all.deb"
    )


def retry_operation(
    operation: Callable[[], T],
    *,
    sleep: Callable[[float], None] = time.sleep,
    max_attempts: int | None = None,
) -> T:
    delays = common.backoff_delays()
    attempts = 0
    while True:
        attempts += 1
        try:
            return operation()
        except common.RetryableError as exc:
            if max_attempts is not None and attempts >= max_attempts:
                raise
            delay = next(delays)
            LOG.warning("Retryable failure: %s; retrying in %ss", exc, delay)
            sleep(delay)


class Bootstrap:
    PROGRESS = {
        common.Phase.PREFLIGHT: 2,
        common.Phase.WAIT_NETWORK: 5,
        common.Phase.APT_UPDATE: 15,
        common.Phase.INSTALL_PACKAGES: 30,
        common.Phase.START_SYSTEM_SERVICES: 45,
        common.Phase.DOCKER_PREFLIGHT: 50,
        common.Phase.PULL_GLADYS: 60,
        common.Phase.START_GLADYS: 80,
        common.Phase.VERIFY_GLADYS: 90,
        common.Phase.START_WATCHTOWER: 95,
        common.Phase.FINALIZE: 98,
        common.Phase.READY: 100,
        common.Phase.FATAL: 0,
    }

    def __init__(
        self,
        *,
        paths: InstallerPaths = InstallerPaths(),
        runner: Callable[..., common.CommandResult] = common.run_command,
        sleep: Callable[[float], None] = time.sleep,
        network_checker: Callable[[], bool] = common.internet_available,
        http_checker: Callable[..., bool] = common.http_ready,
        port_checker: Callable[[], bool] | None = None,
        machine: Callable[[], str] = platform.machine,
    ) -> None:
        self.paths = paths
        self.runner = runner
        self.sleep = sleep
        self.network_checker = network_checker
        self.http_checker = http_checker
        self.port_checker = port_checker or self._port_80_available
        self.machine = machine
        self.test_config: dict[str, object] | None = None

    @property
    def application_service(self) -> str:
        if self.test_config is None:
            return "gladys"
        return str(self.test_config["service"])

    @property
    def application_image(self) -> str:
        if self.test_config is None:
            return "gladysassistant/gladys:v5"
        return str(self.test_config["image"])

    def state(self, phase: common.Phase, message: str) -> None:
        LOG.info("Phase %s: %s", phase.value, message)
        common.write_state(self.paths.state, phase, self.PROGRESS[phase], message)
        delay = 0
        if self.test_config is not None:
            delay = int(self.test_config.get("phase_delay_seconds", 0))
        if delay > 0:
            self.sleep(delay)

    def _compose(self, *arguments: str) -> list[str]:
        command = ["docker", "compose", "-f", str(self.paths.compose)]
        if self.test_config is not None:
            command.extend(["-f", str(self.paths.test_compose)])
        return [*command, *arguments]

    def _run_retryable(self, argv: list[str], **kwargs: object) -> common.CommandResult:
        try:
            return self.runner(argv, **kwargs)
        except COMMAND_FAILURES as exc:
            raise common.RetryableError(str(exc)) from exc

    def _retry_command(self, argv: list[str], **kwargs: object) -> common.CommandResult:
        return retry_operation(
            lambda: self._run_retryable(argv, **kwargs), sleep=self.sleep
        )

    def preflight(self) -> None:
        if self.machine().lower() not in {"x86_64", "amd64"}:
            raise common.FatalConfigurationError("unsupported architecture; amd64 is required")
        if not self.paths.compose.is_file():
            raise common.FatalConfigurationError(f"missing Compose file: {self.paths.compose}")
        if not self.paths.version.is_file() or not self.paths.version.read_text(encoding="utf-8").strip():
            raise common.FatalConfigurationError("missing installer version")
        self.paths.state.parent.mkdir(parents=True, exist_ok=True)
        self.paths.data.mkdir(parents=True, exist_ok=True)
        if self.paths.test_mode.exists():
            if not self.paths.test_compose.is_file():
                raise common.FatalConfigurationError("test mode requires the CI Compose override")
            try:
                config = json.loads(self.paths.test_mode.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise common.FatalConfigurationError("invalid test-mode configuration") from exc
            if set(config) - {
                "schema",
                "service",
                "image",
                "phase_delay_seconds",
                "dpkg_power_loss_barrier",
            }:
                raise common.FatalConfigurationError("unknown test-mode configuration field")
            if config.get("schema") != 1 or not config.get("service") or not config.get("image"):
                raise common.FatalConfigurationError("incomplete test-mode configuration")
            if "dpkg_power_loss_barrier" in config and config["dpkg_power_loss_barrier"] is not True:
                raise common.FatalConfigurationError("invalid test-mode dpkg barrier setting")
            self.test_config = config

    def wait_for_internet(self) -> None:
        delays = common.backoff_delays()
        while not self.network_checker():
            delay = next(delays)
            LOG.info("Internet unavailable; retrying in %ss", delay)
            self.sleep(delay)

    def apt_update(self) -> None:
        environment = dict(os.environ)
        environment["DEBIAN_FRONTEND"] = "noninteractive"
        self._retry_command(
            ["apt-get", "update", *APT_OPTIONS, "-o", "APT::Update::Error-Mode=any"],
            env=environment,
            timeout=APT_UPDATE_TIMEOUT,
        )

    def _missing_package_candidates(self) -> list[str]:
        missing: list[str] = []
        for package in PACKAGES:
            result = self._retry_command(
                ["apt-cache", "policy", package], timeout=60
            )
            if "Candidate: (none)" in result.stdout or "Candidate:" not in result.stdout:
                missing.append(package)
        return missing

    def install_packages(self) -> None:
        environment = dict(os.environ)
        environment["DEBIAN_FRONTEND"] = "noninteractive"
        self.state(
            common.Phase.INSTALL_PACKAGES,
            "Repairing interrupted package operations…",
        )
        apt_options = list(APT_OPTIONS)

        def repair_package_manager() -> None:
            try:
                self.runner(
                    ["dpkg", "--configure", "-a"],
                    env=environment,
                    timeout=PACKAGE_OPERATION_TIMEOUT,
                )
            except COMMAND_FAILURES:
                LOG.warning(
                    "Initial dpkg configuration failed; attempting dependency repair",
                    exc_info=True,
                )
            self._run_retryable(
                ["apt-get", "-f", "install", "-y", *apt_options],
                env=environment,
                timeout=PACKAGE_OPERATION_TIMEOUT,
            )
            self._run_retryable(
                ["dpkg", "--configure", "-a"],
                env=environment,
                timeout=PACKAGE_OPERATION_TIMEOUT,
            )

        retry_operation(repair_package_manager, sleep=self.sleep)

        missing: list[str] = []
        for inspection in range(3):
            missing = self._missing_package_candidates()
            if not missing:
                break
            if inspection < 2:
                LOG.warning(
                    "Required package candidates unavailable (%s); refreshing metadata",
                    ", ".join(missing),
                )
                self.apt_update()
        if missing:
            raise common.FatalConfigurationError(
                "required packages have no candidates after repeated metadata refresh: "
                + ", ".join(missing)
            )

        self.state(common.Phase.INSTALL_PACKAGES, "Installing required packages…")
        self._retry_command(
            ["apt-get", "install", "-y", *PACKAGES, *apt_options],
            env=environment,
            timeout=PACKAGE_OPERATION_TIMEOUT,
        )
        if self.test_config is not None and self.test_config.get("dpkg_power_loss_barrier") is True:
            if not self.paths.ci_barrier_package.is_file():
                raise common.FatalConfigurationError("CI dpkg barrier package is missing")
            self._retry_command(
                ["dpkg", "--install", str(self.paths.ci_barrier_package)],
                env=environment,
                timeout=PACKAGE_OPERATION_TIMEOUT,
            )

    def start_system_services(self) -> None:
        self._retry_command(
            ["systemctl", "enable", "--now", "docker.service"], timeout=120
        )

    def docker_preflight(self) -> None:
        self._retry_command(["docker", "version"], timeout=60)
        self._retry_command(["docker", "compose", "version"], timeout=60)
        try:
            self.runner(self._compose("config", "--quiet"), timeout=60)
        except COMMAND_FAILURES as exc:
            raise common.FatalConfigurationError("invalid Docker Compose configuration") from exc

    def pull_application(self) -> None:
        self._retry_command(
            self._compose("pull", self.application_service),
            timeout=CONTAINER_PULL_TIMEOUT,
        )

    @staticmethod
    def _port_available(port: int) -> bool:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("0.0.0.0", port))
        except OSError:
            return False
        finally:
            probe.close()
        return True

    def _port_80_available(self) -> bool:
        return self._port_available(80)

    def _service_running(self, service: str) -> bool:
        try:
            result = self.runner(
                self._compose("ps", "--status", "running", "--services"), timeout=30
            )
        except COMMAND_FAILURES:
            return False
        return service in result.stdout.split()

    def _wait_application_ready(self, timeout: int = 900) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._service_running(self.application_service) and self.http_checker(
                "http://127.0.0.1/", timeout=5
            ):
                return
            self.sleep(5)
        raise common.RetryableError("application did not become HTTP-ready before timeout")

    def _restore_status_page(self) -> None:
        try:
            self.runner(
                self._compose("stop", self.application_service), check=False, timeout=60
            )
            self.runner(
                ["systemctl", "start", "gladys-setup-web.service"],
                check=False,
                timeout=30,
            )
        except COMMAND_FAILURES:
            LOG.warning("Unable to restore the local setup page", exc_info=True)

    def start_application(self) -> None:
        def attempt() -> None:
            try:
                self._run_retryable(
                    ["systemctl", "stop", "gladys-setup-web.service"], timeout=30
                )
                if not self.port_checker():
                    raise common.RetryableError("port 80 is still occupied after status-server stop")
                self._run_retryable(
                    self._compose("up", "-d", self.application_service), timeout=300
                )
                self._wait_application_ready()
            except common.RetryableError:
                self._restore_status_page()
                raise

        retry_operation(attempt, sleep=self.sleep)

    def start_watchtower(self) -> None:
        self._retry_command(
            self._compose("pull", "watchtower"), timeout=CONTAINER_PULL_TIMEOUT
        )
        self._retry_command(self._compose("up", "-d", "watchtower"), timeout=300)
        def require_watchtower() -> None:
            if not self._service_running("watchtower"):
                raise common.RetryableError("Watchtower is not running")

        retry_operation(require_watchtower, sleep=self.sleep)

    def _system_service_active(self, service: str) -> bool:
        try:
            self.runner(["systemctl", "is-active", "--quiet", service], timeout=30)
        except COMMAND_FAILURES:
            return False
        return True

    def verify_final(self) -> None:
        checks = {
            "Docker": self._system_service_active("docker.service"),
            "application container": self._service_running(self.application_service),
            "application HTTP": self.http_checker("http://127.0.0.1/", timeout=5),
            "Watchtower": self._service_running("watchtower"),
        }
        failures = [name for name, passed in checks.items() if not passed]
        if failures:
            raise common.RetryableError("final checks failed: " + ", ".join(failures))

    def image_metadata(self) -> tuple[str, str]:
        try:
            result = self.runner(
                ["docker", "image", "inspect", self.application_image], timeout=60
            )
            metadata = json.loads(result.stdout)[0]
            digests = metadata.get("RepoDigests") or []
            image_id = metadata.get("Id")
            image_digest = digests[0] if digests else None
            if not isinstance(image_id, str) or not image_id:
                raise ValueError("image ID is absent")
            if not isinstance(image_digest, str) or not image_digest:
                raise ValueError("image digest is absent")
            return image_id, image_digest
        except (
            *COMMAND_FAILURES,
            json.JSONDecodeError,
            IndexError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise common.RetryableError("application image metadata is unavailable") from exc

    def run(self) -> int:
        if common.is_completed(self.paths.completed):
            LOG.info("Completion marker exists; provisioning is already complete")
            return 0

        self.state(common.Phase.PREFLIGHT, "Checking system configuration…")
        self.preflight()
        self.state(common.Phase.WAIT_NETWORK, "Waiting for an Ethernet Internet connection…")
        self.wait_for_internet()
        self.state(common.Phase.APT_UPDATE, "Refreshing Ubuntu package information…")
        self.apt_update()
        self.state(common.Phase.INSTALL_PACKAGES, "Installing Docker and system services…")
        self.install_packages()
        self.state(common.Phase.START_SYSTEM_SERVICES, "Starting Docker services…")
        self.start_system_services()
        self.state(common.Phase.DOCKER_PREFLIGHT, "Checking Docker and Compose…")
        self.docker_preflight()
        self.state(common.Phase.PULL_GLADYS, "Downloading Gladys Assistant…")
        self.pull_application()
        self.state(common.Phase.START_GLADYS, "Starting Gladys Assistant…")
        self.start_application()
        self.state(common.Phase.VERIFY_GLADYS, "Waiting for Gladys Assistant…")
        retry_operation(self.verify_final_without_watchtower, sleep=self.sleep)
        self.state(common.Phase.START_WATCHTOWER, "Enabling Gladys updates…")
        self.start_watchtower()
        self.state(common.Phase.FINALIZE, "Finishing appliance setup…")
        retry_operation(self.verify_final, sleep=self.sleep)
        image_id, image_digest = retry_operation(self.image_metadata, sleep=self.sleep)
        self.state(common.Phase.READY, "Gladys Assistant is ready.")
        common.write_completed_marker(
            self.paths.completed,
            self.paths.version.read_text(encoding="utf-8").strip(),
            image_id,
            image_digest,
        )
        return 0

    def verify_final_without_watchtower(self) -> None:
        if not self._service_running(self.application_service):
            raise common.RetryableError("application container is not running")
        if not self.http_checker("http://127.0.0.1/", timeout=5):
            raise common.RetryableError("application HTTP endpoint is not ready")


def execute(installer: Bootstrap) -> int:
    try:
        return installer.run()
    except common.FatalConfigurationError as exc:
        LOG.error("Fatal configuration error: %s", exc, exc_info=True)
        common.write_state(
            installer.paths.state,
            common.Phase.FATAL,
            installer.PROGRESS[common.Phase.FATAL],
            "Setup cannot continue. Run gladys-diagnostics for technical details.",
        )
        return 78


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    installer = Bootstrap()
    if fcntl is None:
        LOG.error("bootstrap requires a Linux system with fcntl support")
        return 78
    installer.paths.lock.parent.mkdir(parents=True, exist_ok=True)
    with installer.paths.lock.open("a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            LOG.info("Another bootstrap process owns the installer lock")
            return 0
        return execute(installer)


if __name__ == "__main__":
    sys.exit(main())

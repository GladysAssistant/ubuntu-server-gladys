from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[2] / "payload" / "usr" / "lib" / "gladys-installer"
sys.path.insert(0, str(RUNTIME))

import bootstrap  # noqa: E402
import common  # noqa: E402


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def __call__(self, argv: list[str], **kwargs: object) -> common.CommandResult:
        command = tuple(argv)
        self.commands.append(command)
        self.calls.append((command, dict(kwargs)))
        stdout = ""
        if command[:2] == ("apt-cache", "policy"):
            stdout = "  Candidate: 1.0\n"
        elif "ps" in command and "--services" in command:
            stdout = "gladys\nwatchtower\n"
        elif command[:3] == ("docker", "image", "inspect"):
            stdout = json.dumps(
                [{"Id": "sha256:image", "RepoDigests": ["gladysassistant/gladys@sha256:digest"]}]
            )
        return common.CommandResult(command, 0, stdout, "")


class BootstrapTests(unittest.TestCase):
    def make_installer(self, directory: str, runner: FakeRunner | None = None) -> bootstrap.Bootstrap:
        root = Path(directory)
        compose = root / "compose.yaml"
        compose.write_text("services: {}\n", encoding="utf-8")
        version = root / "version"
        version.write_text("1.0.0\n", encoding="utf-8")
        paths = bootstrap.InstallerPaths(
            state=root / "state.json",
            completed=root / "completed",
            lock=root / "bootstrap.lock",
            compose=compose,
            version=version,
            data=root / "data",
            test_mode=root / "test-mode",
            test_compose=root / "test-compose.yaml",
            ci_barrier_package=root / "gladys-ci-dpkg-barrier_1.0_all.deb",
        )
        return bootstrap.Bootstrap(
            paths=paths,
            runner=runner or FakeRunner(),
            sleep=lambda _seconds: None,
            network_checker=lambda: True,
            http_checker=lambda *_args, **_kwargs: True,
            port_checker=lambda: True,
            machine=lambda: "x86_64",
        )

    def test_completed_marker_skips_every_operation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner()
            installer = self.make_installer(directory, runner)
            installer.paths.completed.write_text("{}\n", encoding="utf-8")

            self.assertEqual(installer.run(), 0)
            self.assertEqual(runner.commands, [])

    def test_successful_run_orders_handoff_readiness_and_watchtower(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner()
            installer = self.make_installer(directory, runner)

            self.assertEqual(installer.run(), 0)

            commands = runner.commands
            stop_web = commands.index(("systemctl", "stop", "gladys-setup-web.service"))
            start_gladys = commands.index(
                ("docker", "compose", "-f", str(installer.paths.compose), "up", "-d", "gladys")
            )
            start_watchtower = commands.index(
                ("docker", "compose", "-f", str(installer.paths.compose), "up", "-d", "watchtower")
            )
            self.assertLess(stop_web, start_gladys)
            self.assertLess(start_gladys, start_watchtower)
            self.assertTrue(installer.paths.completed.is_file())
            state = json.loads(installer.paths.state.read_text(encoding="utf-8"))
            self.assertEqual((state["phase"], state["progress"]), ("READY", 100))

    def test_package_install_is_noninteractive_and_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner()
            installer = self.make_installer(directory, runner)

            installer.install_packages()

            recovery = runner.commands.index(("dpkg", "--configure", "-a"))
            repair = next(
                index
                for index, command in enumerate(runner.commands)
                if command[:4] == ("apt-get", "-f", "install", "-y")
            )
            install = next(
                command
                for command in runner.commands
                if command[:3] == ("apt-get", "install", "-y")
            )
            self.assertLess(recovery, repair)
            for package in bootstrap.PACKAGES:
                self.assertIn(package, install)
            critical = [
                kwargs.get("timeout")
                for command, kwargs in runner.calls
                if command[0] in {"apt-get", "apt-cache", "dpkg"}
            ]
            self.assertTrue(critical)
            self.assertTrue(
                all(isinstance(timeout, (int, float)) and timeout > 0 for timeout in critical)
            )

    def test_package_install_does_not_install_avahi(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner()
            installer = self.make_installer(directory, runner)

            installer.install_packages()

            install = next(
                command
                for command in runner.commands
                if command[:3] == ("apt-get", "install", "-y")
            )
            self.assertNotIn("avahi-daemon", install)

    def test_start_system_services_enables_only_docker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner()
            installer = self.make_installer(directory, runner)

            installer.start_system_services()

            self.assertEqual(
                runner.commands,
                [("systemctl", "enable", "--now", "docker.service")],
            )

    def test_final_verification_checks_docker_without_avahi(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner()
            installer = self.make_installer(directory, runner)

            installer.verify_final()

            active_checks = [
                command
                for command in runner.commands
                if command[:3] == ("systemctl", "is-active", "--quiet")
            ]
            self.assertEqual(
                active_checks,
                [("systemctl", "is-active", "--quiet", "docker.service")],
            )

    def test_unsupported_architecture_is_fatal_and_exit_78(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            installer = self.make_installer(directory)
            installer.machine = lambda: "aarch64"

            self.assertEqual(bootstrap.execute(installer), 78)
            state = json.loads(installer.paths.state.read_text(encoding="utf-8"))
            self.assertEqual(state["phase"], "FATAL")
            self.assertNotIn("Traceback", state["message"])

    def test_missing_package_candidate_is_fatal(self) -> None:
        class MissingRunner(FakeRunner):
            def __call__(self, argv: list[str], **kwargs: object) -> common.CommandResult:
                result = super().__call__(argv, **kwargs)
                if tuple(argv[:2]) == ("apt-cache", "policy"):
                    return common.CommandResult(tuple(argv), 0, "  Candidate: (none)\n", "")
                return result

        with tempfile.TemporaryDirectory() as directory:
            installer = self.make_installer(directory, MissingRunner())
            with self.assertRaises(common.FatalConfigurationError):
                installer.install_packages()
            refreshes = [
                command
                for command in installer.runner.commands
                if command[:2] == ("apt-get", "update")
            ]
            self.assertGreaterEqual(len(refreshes), 2)

    def test_transient_candidate_and_apt_cache_failures_recover(self) -> None:
        class FlakyRunner(FakeRunner):
            def __init__(self) -> None:
                super().__init__()
                self.policy_calls = 0

            def __call__(self, argv: list[str], **kwargs: object) -> common.CommandResult:
                if tuple(argv[:2]) == ("apt-cache", "policy"):
                    self.policy_calls += 1
                    if self.policy_calls == 1:
                        raise subprocess.TimeoutExpired(argv, 1)
                    if self.policy_calls <= 1 + len(bootstrap.PACKAGES):
                        super().__call__(argv, **kwargs)
                        return common.CommandResult(tuple(argv), 0, "  Candidate: (none)\n", "")
                return super().__call__(argv, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            runner = FlakyRunner()
            installer = self.make_installer(directory, runner)
            installer.install_packages()
            self.assertTrue(
                any(command[:2] == ("apt-get", "update") for command in runner.commands)
            )

    def test_interrupted_dpkg_recovery_runs_complete_repair_cycle(self) -> None:
        class InterruptedRunner(FakeRunner):
            def __init__(self) -> None:
                super().__init__()
                self.dpkg_attempts = 0

            def __call__(self, argv: list[str], **kwargs: object) -> common.CommandResult:
                if tuple(argv) == ("dpkg", "--configure", "-a"):
                    self.dpkg_attempts += 1
                    if self.dpkg_attempts == 1:
                        command = tuple(argv)
                        self.commands.append(command)
                        self.calls.append((command, dict(kwargs)))
                        raise common.CommandError(
                            common.CommandResult(command, 1, "", "dependency problems")
                        )
                return super().__call__(argv, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            runner = InterruptedRunner()
            installer = self.make_installer(directory, runner)
            installer.install_packages()

            first_dpkg = runner.commands.index(("dpkg", "--configure", "-a"))
            repair = next(
                index
                for index, command in enumerate(runner.commands)
                if command[:4] == ("apt-get", "-f", "install", "-y")
            )
            second_dpkg = runner.commands.index(
                ("dpkg", "--configure", "-a"), first_dpkg + 1
            )
            self.assertLess(first_dpkg, repair)
            self.assertLess(repair, second_dpkg)

    def test_apt_and_container_downloads_have_bounded_timeouts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner()
            installer = self.make_installer(directory, runner)
            installer.apt_update()
            installer.pull_application()
            installer.start_watchtower()

            timeout_by_command = {
                command: kwargs.get("timeout") for command, kwargs in runner.calls
            }
            apt_update = next(
                command for command in runner.commands if command[:2] == ("apt-get", "update")
            )
            self.assertIn("APT::Update::Error-Mode=any", apt_update)
            gladys_pull = next(
                command for command in runner.commands if command[-2:] == ("pull", "gladys")
            )
            watchtower_pull = next(
                command
                for command in runner.commands
                if command[-2:] == ("pull", "watchtower")
            )
            for command in (apt_update, gladys_pull, watchtower_pull):
                self.assertIsInstance(timeout_by_command[command], (int, float))
                self.assertGreater(timeout_by_command[command], 0)

    def test_ci_power_barrier_installs_a_postinst_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner()
            installer = self.make_installer(directory, runner)
            installer.paths.test_compose.write_text("services: {}\n", encoding="utf-8")
            installer.paths.test_mode.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "service": "test-app",
                        "image": "example.invalid/test:1",
                        "dpkg_power_loss_barrier": True,
                    }
                ),
                encoding="utf-8",
            )
            installer.paths.ci_barrier_package.write_bytes(b"deb")
            installer.preflight()
            installer.install_packages()

            self.assertIn(
                ("dpkg", "--install", str(installer.paths.ci_barrier_package)),
                runner.commands,
            )
            self.assertNotIn(
                "DPkg::Pre-Invoke",
                " ".join(word for command in runner.commands for word in command),
            )

    def test_retry_operation_uses_backoff_until_success(self) -> None:
        attempts = 0
        slept: list[int] = []

        def operation() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise common.RetryableError("temporary")
            return "ready"

        result = bootstrap.retry_operation(operation, sleep=slept.append, max_attempts=3)

        self.assertEqual(result, "ready")
        self.assertEqual(slept, [2, 4])

    def test_command_timeout_and_missing_image_metadata_are_retryable(self) -> None:
        class TimeoutRunner(FakeRunner):
            def __call__(self, argv: list[str], **_kwargs: object) -> common.CommandResult:
                raise subprocess.TimeoutExpired(argv, 1)

        with tempfile.TemporaryDirectory() as directory:
            installer = self.make_installer(directory, TimeoutRunner())
            with self.assertRaises(common.RetryableError):
                installer._run_retryable(["docker", "version"], timeout=1)

        class MissingMetadataRunner(FakeRunner):
            def __call__(self, argv: list[str], **kwargs: object) -> common.CommandResult:
                result = super().__call__(argv, **kwargs)
                if tuple(argv[:3]) == ("docker", "image", "inspect"):
                    return common.CommandResult(tuple(argv), 0, "[{}]", "")
                return result

        with tempfile.TemporaryDirectory() as directory:
            installer = self.make_installer(directory, MissingMetadataRunner())
            with self.assertRaises(common.RetryableError):
                installer.image_metadata()

    def test_port_probe_reuses_recent_setup_server_connections(self) -> None:
        class TimeWaitSocket:
            def __init__(self) -> None:
                self.reuse_address = False
                self.bound_to: tuple[str, int] | None = None
                self.closed = False

            def setsockopt(self, level: int, option: int, value: int) -> None:
                if (level, option, value) == (
                    socket.SOL_SOCKET,
                    socket.SO_REUSEADDR,
                    1,
                ):
                    self.reuse_address = True

            def bind(self, address: tuple[str, int]) -> None:
                self.bound_to = address
                if not self.reuse_address:
                    raise OSError("recent setup connection is still in TIME_WAIT")

            def close(self) -> None:
                self.closed = True

        with tempfile.TemporaryDirectory() as directory:
            installer = self.make_installer(directory)
            probe = TimeWaitSocket()
            original_socket = bootstrap.socket.socket
            bootstrap.socket.socket = lambda *_args, **_kwargs: probe
            try:
                available = installer._port_80_available()
            finally:
                bootstrap.socket.socket = original_socket

            self.assertTrue(available)
            self.assertEqual(probe.bound_to, ("0.0.0.0", 80))
            self.assertTrue(probe.closed)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux socket semantics")
    def test_port_probe_reuses_time_wait_but_rejects_a_live_listener(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            installer = self.make_installer(directory)
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            listener.listen(1)

            self.assertFalse(installer._port_available(port))

            client = socket.create_connection(("127.0.0.1", port))
            accepted, _address = listener.accept()
            accepted.shutdown(socket.SHUT_WR)
            self.assertEqual(client.recv(1), b"")
            client.close()
            accepted.close()
            listener.close()

            without_reuse = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                with self.assertRaises(OSError):
                    without_reuse.bind(("0.0.0.0", port))
            finally:
                without_reuse.close()

            self.assertTrue(installer._port_available(port))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Shared, standard-library-only helpers for the Gladys installer."""

from __future__ import annotations

import itertools
import json
import os
import signal
import socket
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Iterator, Mapping, Sequence


COMPOSE_FILE = "/opt/gladys/compose.yaml"
INTERNET_HOST = "archive.ubuntu.com"
INTERNET_PORT = 443
COMMAND_TERMINATION_GRACE = 2.0


class Phase(str, Enum):
    PREFLIGHT = "PREFLIGHT"
    WAIT_NETWORK = "WAIT_NETWORK"
    APT_UPDATE = "APT_UPDATE"
    INSTALL_PACKAGES = "INSTALL_PACKAGES"
    START_SYSTEM_SERVICES = "START_SYSTEM_SERVICES"
    DOCKER_PREFLIGHT = "DOCKER_PREFLIGHT"
    PULL_GLADYS = "PULL_GLADYS"
    START_GLADYS = "START_GLADYS"
    VERIFY_GLADYS = "VERIFY_GLADYS"
    START_WATCHTOWER = "START_WATCHTOWER"
    FINALIZE = "FINALIZE"
    READY = "READY"
    FATAL = "FATAL"


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class InstallerError(RuntimeError):
    """Base error for provisioning failures."""


class RetryableError(InstallerError):
    """An operation failed for a reason that can recover without reconfiguration."""


class FatalConfigurationError(InstallerError):
    """Provisioning cannot continue until the installed configuration changes."""


class CommandError(InstallerError):
    def __init__(self, result: CommandResult):
        self.result = result
        detail = result.stderr.strip() or result.stdout.strip() or "no command output"
        super().__init__(
            f"command {result.argv!r} exited with {result.returncode}: {detail}"
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def atomic_write_json(path: Path | str, data: Mapping[str, object], mode: int = 0o644) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}."
    )
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def write_state(
    path: Path | str, phase: Phase | str, progress: int, message: str
) -> dict[str, object]:
    try:
        normalized_phase = Phase(phase)
    except ValueError as exc:
        raise ValueError(f"unknown installer phase: {phase}") from exc
    if not isinstance(progress, int) or not 0 <= progress <= 100:
        raise ValueError("progress must be an integer between 0 and 100")
    state: dict[str, object] = {
        "schema": 1,
        "phase": normalized_phase.value,
        "progress": progress,
        "message": str(message),
        "updated_at": utc_now(),
    }
    atomic_write_json(path, state)
    return state


def write_completed_marker(
    path: Path | str,
    installer_version: str,
    image_id: str | None,
    image_digest: str | None,
) -> None:
    atomic_write_json(
        path,
        {
            "installer_version": installer_version,
            "completed_at": utc_now(),
            "image_id": image_id,
            "image_digest": image_digest,
        },
    )


def is_completed(path: Path | str) -> bool:
    return Path(path).is_file()


def backoff_delays() -> Iterator[int]:
    return itertools.chain((2, 4, 8, 15, 30), itertools.repeat(60))


def run_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    check: bool = True,
) -> CommandResult:
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise ValueError("command must be a non-empty sequence of non-empty strings")
    process = subprocess.Popen(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=dict(env) if env is not None else None,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            elif process.poll() is None:
                process.terminate()
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=COMMAND_TERMINATION_GRACE)
        except subprocess.TimeoutExpired:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                elif process.poll() is None:
                    process.kill()
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate(timeout=COMMAND_TERMINATION_GRACE)
        raise subprocess.TimeoutExpired(
            tuple(argv), timeout, output=stdout, stderr=stderr
        ) from exc
    result = CommandResult(tuple(argv), process.returncode, stdout, stderr)
    if check and result.returncode != 0:
        raise CommandError(result)
    return result


def compose_command(*arguments: str, compose_file: str = COMPOSE_FILE) -> list[str]:
    return ["docker", "compose", "-f", compose_file, *arguments]


def _verified_tls_probe(host: str, port: int, timeout: float) -> None:
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as connection:
        with context.wrap_socket(connection, server_hostname=host):
            return


def internet_available(
    *,
    host: str = INTERNET_HOST,
    port: int = INTERNET_PORT,
    timeout: float = 10.0,
    resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
    tls_probe: Callable[[str, int, float], None] = _verified_tls_probe,
) -> bool:
    try:
        addresses = resolver(host, port, type=socket.SOCK_STREAM)
        if not addresses:
            return False
        tls_probe(host, port, timeout)
    except (OSError, ssl.SSLError, socket.gaierror):
        return False
    return True


def http_ready(url: str = "http://127.0.0.1/", timeout: float = 5.0) -> bool:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "gladys-installer/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status) < 400
    except urllib.error.HTTPError as exc:
        return exc.code < 400
    except (OSError, urllib.error.URLError, ValueError):
        return False


def parse_global_ipv4(ip_json: str) -> list[str]:
    interfaces = json.loads(ip_json)
    addresses: set[str] = set()
    excluded_prefixes = ("lo", "docker", "br-", "veth")
    for interface in interfaces:
        ifname = str(interface.get("ifname", ""))
        if ifname.startswith(excluded_prefixes):
            continue
        for address in interface.get("addr_info", []):
            value = str(address.get("local", ""))
            if (
                address.get("family") == "inet"
                and address.get("scope") == "global"
                and value
                and not value.startswith("169.254.")
            ):
                addresses.add(value)
    return sorted(addresses)


def current_ipv4_addresses() -> list[str]:
    try:
        result = run_command(["ip", "-j", "-4", "address", "show"], timeout=10)
        return parse_global_ipv4(result.stdout)
    except (CommandError, json.JSONDecodeError, OSError, subprocess.TimeoutExpired):
        return []

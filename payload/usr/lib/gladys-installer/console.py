#!/usr/bin/env python3
"""Continuously render a small Gladys status display on tty1."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import common
import i18n


STATE_PATH = Path("/var/lib/gladys-installer/state.json")
PHASES = [
    "PREFLIGHT",
    "WAIT_NETWORK",
    "APT_UPDATE",
    "INSTALL_PACKAGES",
    "START_SYSTEM_SERVICES",
    "DOCKER_PREFLIGHT",
    "PULL_GLADYS",
    "START_GLADYS",
    "VERIFY_GLADYS",
    "START_WATCHTOWER",
    "FINALIZE",
    "READY",
]
STAGES = (
    ("step_system", frozenset({"PREFLIGHT"})),
    ("step_network", frozenset({"WAIT_NETWORK"})),
    (
        "step_packages",
        frozenset({"APT_UPDATE", "INSTALL_PACKAGES", "START_SYSTEM_SERVICES"}),
    ),
    ("step_docker", frozenset({"DOCKER_PREFLIGHT"})),
    ("step_download", frozenset({"PULL_GLADYS"})),
    (
        "step_services",
        frozenset(
            {
                "START_GLADYS",
                "VERIFY_GLADYS",
                "START_WATCHTOWER",
                "FINALIZE",
            }
        ),
    ),
)


def read_state(path: Path = STATE_PATH) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        common.Phase(value["phase"])
        return value
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return {"phase": "PREFLIGHT", "progress": 2, "message": "Preparing your system…"}


def _progress_bar(progress: int, width: int = 32) -> str:
    bounded = max(0, min(100, progress))
    filled = round(width * bounded / 100)
    return f"[{'#' * filled}{'-' * (width - filled)}] {bounded:>3}%"


def _activity(frame: int) -> str:
    return (".  ", ".. ", "...")[frame % 3]


def _animated(text: str, frame: int) -> str:
    return f"{text.rstrip('.… ')}{_activity(frame)}"


def _stage_lines(phase: str, language: str, frame: int) -> list[str]:
    try:
        current = PHASES.index(phase)
    except ValueError:
        current = 0
    lines: list[str] = []
    for key, phases in STAGES:
        indexes = [PHASES.index(item) for item in phases]
        active = phase in phases
        done = phase == "READY" or current > max(indexes)
        marker = "[OK]" if done else f"[{_activity(frame)}]" if active else "[  ]"
        lines.append(f"{marker} {i18n.text(language, key)}")
    return lines


def render_screen(
    state: dict[str, object],
    addresses: list[str],
    services: dict[str, bool],
    *,
    language: str = "en",
    frame: int = 0,
) -> str:
    language = i18n.console_language(language)
    phase = str(state.get("phase", "PREFLIGHT"))
    try:
        progress = int(state.get("progress", 2))
    except (TypeError, ValueError):
        progress = 2
    heading = i18n.phase_text(language, phase)
    separator = "-" * 56
    if phase == "WAIT_NETWORK":
        return "\n".join(
            (
                i18n.text(language, "brand"),
                separator,
                "",
                i18n.text(language, "network_title"),
                "",
                i18n.text(language, "network_body"),
                "",
                _animated(i18n.text(language, "waiting"), frame),
                "",
                _progress_bar(progress),
            )
        )
    if phase == "FATAL":
        return "\n".join(
            (
                i18n.text(language, "brand"),
                separator,
                "",
                i18n.text(language, "fatal_title"),
                "",
                i18n.text(language, "fatal_hint"),
            )
        )
    if phase == "READY":
        address_lines = [
            f"  {i18n.text(language, 'open_gladys')}",
            "  http://gladysassistant.local",
        ]
        if addresses:
            address_lines.extend(
                (
                    "",
                    f"  {i18n.text(language, 'fallback_address')}",
                    *(f"  http://{address}" for address in addresses),
                )
            )
        return "\n".join(
            (
                i18n.text(language, "brand"),
                separator,
                "",
                f"✓ {i18n.text(language, 'ready_title')}",
                "",
                *address_lines,
                "",
                separator,
                f"{i18n.text(language, 'ethernet')}: "
                f"{i18n.text(language, 'connected' if services.get('ethernet') else 'disconnected')}",
                f"{i18n.text(language, 'gladys')}: "
                f"{i18n.text(language, 'running' if services.get('gladys') else 'not_running')}",
            )
        )

    details: list[str] = []
    if phase == "PULL_GLADYS":
        details.extend(("", i18n.text(language, "download_note")))
    if addresses:
        details.extend(
            (
                "",
                f"{i18n.text(language, 'setup_address')}: http://{addresses[0]}",
            )
        )
    return "\n".join(
        (
            i18n.text(language, "brand"),
            separator,
            "",
            i18n.text(language, "preparing_title"),
            "",
            _progress_bar(progress),
            "",
            *_stage_lines(phase, language, frame),
            "",
            _animated(heading, frame),
            *details,
            "",
            i18n.text(language, "keep_connected"),
        )
    )


def service_states(addresses: list[str]) -> dict[str, bool]:
    def succeeds(argv: list[str]) -> bool:
        try:
            common.run_command(argv, timeout=5)
        except (common.CommandError, OSError):
            return False
        return True

    def gladys_running() -> bool:
        try:
            result = common.run_command(
                ["docker", "inspect", "--format", "{{.State.Running}}", "gladys"],
                timeout=5,
            )
        except (common.CommandError, OSError):
            return False
        return result.stdout.strip().lower() == "true"

    return {
        "ethernet": bool(addresses),
        "docker": succeeds(["systemctl", "is-active", "--quiet", "docker.service"]),
        "gladys": gladys_running(),
    }


def main() -> int:
    language = i18n.read_installed_language()
    frame = 0
    while True:
        state = read_state()
        addresses = common.current_ipv4_addresses()
        screen = render_screen(
            state,
            addresses,
            service_states(addresses),
            language=language,
            frame=frame,
        )
        sys.stdout.write("\x1b[2J\x1b[H" + screen + "\n")
        sys.stdout.flush()
        frame += 1
        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())

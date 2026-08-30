#!/usr/bin/env python3
"""Validate the dpkg state of the CI power-loss barrier package."""

from __future__ import annotations

import re
import sys
from pathlib import Path


BARRIER_PACKAGE = "gladys-ci-dpkg-barrier"
INTERRUPTED_STATES = {"install ok unpacked", "install ok half-configured"}
MAX_UPDATE_NAME_LENGTH = 10


def parse_stanzas(path: Path):
    for paragraph in path.read_text(encoding="utf-8").split("\n\n"):
        if not paragraph.strip():
            continue
        fields = {}
        for line in paragraph.splitlines():
            if line.startswith((" ", "\t")):
                if not fields:
                    raise ValueError(f"{path.name}: continuation without a field")
                continue
            if ":" not in line:
                raise ValueError(f"{path.name}: malformed control field")
            key, value = line.split(":", 1)
            key = key.rstrip().casefold()
            if not key:
                raise ValueError(f"{path.name}: empty control field name")
            if key in fields:
                raise ValueError(f"{path.name}: duplicate field {key!r}")
            fields[key] = value.lstrip()
        if "package" not in fields:
            raise ValueError(f"{path.name}: missing required 'Package' field")
        yield fields


def package_status(snapshot: Path) -> str:
    status_path = snapshot / "status"
    if not status_path.is_file():
        raise ValueError("dpkg status snapshot is missing")

    update_dir = snapshot / "updates"
    update_paths = []
    if update_dir.is_dir():
        update_paths = sorted(
            (
                path
                for path in update_dir.iterdir()
                if path.is_file() and re.fullmatch(r"[0-9]+", path.name)
            ),
            key=lambda path: path.name,
        )
        widths = {len(path.name) for path in update_paths}
        if len(widths) > 1:
            raise ValueError("numeric dpkg updates have different filename widths")
        if widths and next(iter(widths)) > MAX_UPDATE_NAME_LENGTH:
            raise ValueError("numeric dpkg update filename is too long")

    status = ""
    for path in (status_path, *update_paths):
        for fields in parse_stanzas(path):
            if fields.get("package") == BARRIER_PACKAGE:
                status = fields.get("status", "")
    return status


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[2] not in {"interrupted", "installed"}:
        print(f"usage: {argv[0]} SNAPSHOT interrupted|installed", file=sys.stderr)
        return 64

    try:
        status = package_status(Path(argv[1]))
    except (OSError, UnicodeError, ValueError) as error:
        print(f"unable to inspect dpkg barrier state: {error}", file=sys.stderr)
        return 1

    if argv[2] == "interrupted":
        if status not in INTERRUPTED_STATES:
            print(
                f"barrier package was not interrupted inside dpkg: {status!r}",
                file=sys.stderr,
            )
            return 1
    elif status != "install ok installed":
        print(f"barrier package was not recovered: {status!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

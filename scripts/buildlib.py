#!/usr/bin/env python3
"""Pure parsing helpers shared by fail-closed ISO build scripts."""

from __future__ import annotations

import re
import argparse
import json
from pathlib import Path
from typing import Any

import yaml


ISO_PATTERN = re.compile(
    r"^ubuntu-26\.04(?:\.(?P<point_release>\d+))?-live-server-amd64\.iso$"
)
SUM_LINE = re.compile(r"^([0-9a-fA-F]{64}) [ *](\S+)$")
CANDIDATE_TOKEN = re.compile(
    r"(?<!\S)ubuntu-26\.04(?:\.\d+)?-live-server-amd64\.iso(?!\S)"
)
ISO_INFO_PATTERN = re.compile(
    r'^Ubuntu-Server 26\.04(?:\.\d+ LTS)? "[^"\r\n]+" - Release amd64 '
    r"\(\d{8}(?:\.\d+)?\)$"
)


class BuildInputError(ValueError):
    """Authenticated or extracted build input does not meet strict expectations."""


def discover_server_iso(sha256sums: str) -> tuple[str, str]:
    candidates: list[tuple[int, str, str]] = []
    for line in sha256sums.splitlines():
        match = SUM_LINE.fullmatch(line)
        if match is None:
            if CANDIDATE_TOKEN.search(line):
                raise BuildInputError("malformed Ubuntu server ISO checksum entry")
            continue
        checksum, filename = match.groups()
        iso_match = ISO_PATTERN.fullmatch(filename)
        if iso_match:
            point_release = int(iso_match.group("point_release") or 0)
            candidates.append((point_release, filename, checksum.lower()))
    if not candidates:
        raise BuildInputError(
            "expected at least one Ubuntu 26.04 amd64 live-server ISO, found 0"
        )
    latest_point_release = max(candidate[0] for candidate in candidates)
    latest_candidates = [
        candidate for candidate in candidates if candidate[0] == latest_point_release
    ]
    if len(latest_candidates) != 1:
        raise BuildInputError(
            "expected exactly one latest Ubuntu 26.04 amd64 live-server ISO, "
            f"found {len(latest_candidates)}"
        )
    _, filename, checksum = latest_candidates[0]
    return filename, checksum


def require_ubuntu_iso_info(info_text: str) -> bool:
    if ISO_INFO_PATTERN.fullmatch(info_text) is None:
        raise BuildInputError("ISO is not an Ubuntu Server 26.04 amd64 release image")
    return True


def require_minimal_source(catalog_text: str) -> bool:
    try:
        document: Any = yaml.safe_load(catalog_text)
    except yaml.YAMLError as exc:
        raise BuildInputError("install source catalog is invalid YAML") from exc
    if isinstance(document, dict):
        sources = document.get("sources")
    else:
        sources = document
    if not isinstance(sources, list):
        raise BuildInputError("install source catalog must contain a source list")
    matches = [source for source in sources if isinstance(source, dict) and source.get("id") == "ubuntu-server-minimal"]
    if len(matches) != 1:
        raise BuildInputError(
            f"expected exactly one ubuntu-server-minimal source, found {len(matches)}"
        )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    discover = subparsers.add_parser("discover")
    discover.add_argument("input", type=Path)
    discover.add_argument("output", type=Path)
    catalog = subparsers.add_parser("catalog")
    catalog.add_argument("input", type=Path)
    iso_info = subparsers.add_parser("iso-info")
    iso_info.add_argument("input", type=Path)
    args = parser.parse_args()
    if args.command == "discover":
        filename, checksum = discover_server_iso(args.input.read_text(encoding="utf-8"))
        args.output.write_text(
            json.dumps({"filename": filename, "sha256": checksum}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    elif args.command == "catalog":
        require_minimal_source(args.input.read_text(encoding="utf-8"))
    else:
        require_ubuntu_iso_info(args.input.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

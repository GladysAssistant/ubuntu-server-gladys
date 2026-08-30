#!/usr/bin/env python3
"""Generate release build metadata without modifying source files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def create_build_info(
    *,
    installer_version: str,
    git_commit: str,
    ubuntu_iso_filename: str,
    ubuntu_iso_sha256: str,
    timestamp: str,
) -> dict[str, str]:
    return {
        "installer_version": installer_version,
        "git_commit": git_commit,
        "architecture": "amd64",
        "ubuntu_series": "26.04",
        "ubuntu_iso_filename": ubuntu_iso_filename,
        "ubuntu_iso_sha256": ubuntu_iso_sha256,
        "build_timestamp_utc": timestamp,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer-version", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--ubuntu-iso-filename", required=True)
    parser.add_argument("--ubuntu-iso-sha256", required=True)
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    document = create_build_info(
        installer_version=args.installer_version,
        git_commit=args.git_commit,
        ubuntu_iso_filename=args.ubuntu_iso_filename,
        ubuntu_iso_sha256=args.ubuntu_iso_sha256,
        timestamp=args.timestamp,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

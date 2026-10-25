#!/usr/bin/env python3
"""Update Ubuntu media checksums without disabling the list.

One modified file gets its checksum replaced, and entries for directories
pruned from the image are dropped so the media check stays meaningful.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


LINE = re.compile(r"^([0-9a-fA-F]{32})([ \t]+)(\*?)(.+)$")


class MediaChecksumError(ValueError):
    pass


def update_checksum(document: str, target: str, checksum: str) -> str:
    if re.fullmatch(r"[0-9a-fA-F]{32}", checksum) is None:
        raise MediaChecksumError("replacement must be an MD5 checksum")
    normalized_target = target.removeprefix("./")
    matches = 0
    output: list[str] = []
    for line in document.splitlines(keepends=True):
        ending = "\n" if line.endswith("\n") else ""
        content = line[:-1] if ending else line
        match = LINE.fullmatch(content)
        if match is not None and match.group(4).removeprefix("./") == normalized_target:
            matches += 1
            content = checksum.lower() + match.group(2) + match.group(3) + match.group(4)
        output.append(content + ending)
    if matches > 1:
        raise MediaChecksumError(f"duplicate media checksum entries for {target}")
    return "".join(output)


def drop_entries(document: str, prefix: str) -> str:
    normalized_prefix = prefix.removeprefix("./")
    if not normalized_prefix.endswith("/") or normalized_prefix == "/":
        raise MediaChecksumError("pruned prefix must name a directory ending in /")
    output: list[str] = []
    for line in document.splitlines(keepends=True):
        content = line.rstrip("\n")
        match = LINE.fullmatch(content)
        if match is not None and match.group(4).removeprefix("./").startswith(
            normalized_prefix
        ):
            continue
        output.append(line)
    return "".join(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--path", required=True)
    parser.add_argument("--checksum", required=True)
    parser.add_argument("--drop-prefix", action="append", default=[])
    args = parser.parse_args()
    updated = update_checksum(
        args.input.read_text(encoding="utf-8"), args.path, args.checksum
    )
    for prefix in args.drop_prefix:
        updated = drop_entries(updated, prefix)
    args.output.write_text(updated, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

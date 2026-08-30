#!/usr/bin/env python3
"""Generate the deterministic ci-smoke package used for dpkg power-loss tests."""

from __future__ import annotations

import argparse
import gzip
import io
import tarfile
from pathlib import Path


CONTROL = b"""Package: gladys-ci-dpkg-barrier
Version: 1.0
Section: misc
Priority: optional
Architecture: all
Maintainer: Gladys Installer CI <noreply@example.invalid>
Description: CI-only package for deterministic dpkg recovery testing
"""
POSTINST = b"""#!/bin/sh
set -eu
exec /usr/lib/gladys-installer/apt-power-loss-barrier.py
"""


def _compressed_tar(entries: tuple[tuple[str, bytes, int], ...]) -> bytes:
    uncompressed = io.BytesIO()
    with tarfile.open(fileobj=uncompressed, mode="w:", format=tarfile.USTAR_FORMAT) as archive:
        for name, content, mode in entries:
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = mode
            member.uid = 0
            member.gid = 0
            member.uname = "root"
            member.gname = "root"
            member.mtime = 0
            archive.addfile(member, io.BytesIO(content))
    compressed = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", compresslevel=9, fileobj=compressed, mtime=0
    ) as handle:
        handle.write(uncompressed.getvalue())
    return compressed.getvalue()


def _ar_member(name: str, content: bytes) -> bytes:
    encoded_name = f"{name}/"
    if len(encoded_name) > 16:
        raise ValueError(f"ar member name is too long: {name}")
    header = (
        f"{encoded_name:<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(content):<10}`\n"
    ).encode("ascii")
    if len(header) != 60:
        raise AssertionError("invalid ar header length")
    return header + content + (b"\n" if len(content) % 2 else b"")


def build_package() -> bytes:
    control_archive = _compressed_tar(
        (("./control", CONTROL, 0o644), ("./postinst", POSTINST, 0o755))
    )
    data_archive = _compressed_tar(())
    return b"!<arch>\n" + b"".join(
        (
            _ar_member("debian-binary", b"2.0\n"),
            _ar_member("control.tar.gz", control_archive),
            _ar_member("data.tar.gz", data_archive),
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(build_package())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

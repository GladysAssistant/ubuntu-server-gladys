#!/usr/bin/env python3
"""Inspect a payload archive without extracting it or trusting member paths."""

from __future__ import annotations

import argparse
import posixpath
import re
import tarfile
from pathlib import Path


class PayloadInspectionError(ValueError):
    pass


GLADYS_IMAGE = re.compile(r"gladysassistant/gladys:([^\s'\"]+)")
ARCHIVE_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.zst", ".oci", ".deb")


def _safe_name(name: str) -> str:
    if not name or name.startswith("/") or "\\" in name or "\x00" in name:
        raise PayloadInspectionError(f"unsafe payload member path: {name!r}")
    normalized = posixpath.normpath(name)
    if normalized in ("", "."):
        return "."
    if normalized == ".." or normalized.startswith("../"):
        raise PayloadInspectionError(f"payload member escapes archive root: {name!r}")
    return normalized.removeprefix("./")


def inspect_payload(archive: Path, profile: str) -> None:
    if profile not in {"production", "ci-smoke", "ci-real"}:
        raise PayloadInspectionError(f"unknown profile: {profile}")
    try:
        handle = tarfile.open(archive, mode="r:")
    except (OSError, tarfile.TarError) as exc:
        raise PayloadInspectionError("payload is not a readable uncompressed tar archive") from exc

    names: set[str] = set()
    text = bytearray()
    with handle:
        for member in handle:
            name = _safe_name(member.name)
            if name in names and name != ".":
                raise PayloadInspectionError(f"duplicate payload member: {name}")
            names.add(name)
            if member.uid != 0 or member.gid != 0:
                raise PayloadInspectionError(f"payload member is not numeric root owned: {name}")
            if not (member.isfile() or member.isdir()):
                raise PayloadInspectionError(f"unsupported payload member type: {name}")
            expected_mode = 0o755 if member.isdir() else 0o644
            if member.isfile() and (
                name.startswith("usr/lib/gladys-installer/") and name.endswith(".py")
                or name == "usr/local/sbin/gladys-diagnostics"
            ):
                expected_mode = 0o755
            if member.mode & 0o7777 != expected_mode:
                raise PayloadInspectionError(
                    f"unexpected mode for {name}: {member.mode & 0o7777:o}"
                )
            if member.isfile() and member.size <= 2 * 1024 * 1024:
                extracted = handle.extractfile(member)
                if extracted is None:
                    raise PayloadInspectionError(f"cannot read payload member: {name}")
                text.extend(extracted.read())
                text.extend(b"\n")

    if profile == "ci-smoke":
        required = {
            "etc/gladys-installer/test-mode",
            "etc/gladys-installer/test-compose.yaml",
            "etc/gladys-installer/gladys-ci-dpkg-barrier_1.0_all.deb",
            "usr/lib/gladys-installer/apt-power-loss-barrier.py",
        }
        missing = required - names
        if missing:
            raise PayloadInspectionError(f"ci-smoke payload misses fixtures: {sorted(missing)}")
        return

    forbidden_names = []
    for name in names:
        lowered = name.lower()
        basename = posixpath.basename(lowered)
        if (
            "test-mode" in lowered
            or "test-compose" in lowered
            or "__pycache__" in lowered.split("/")
            or lowered.endswith((".pyc", ".pyo"))
            or lowered.startswith("var/lib/docker/")
            or lowered.startswith("var/lib/containerd/")
            or basename == "layer.tar"
            or lowered.endswith(ARCHIVE_SUFFIXES)
        ):
            forbidden_names.append(name)
    if forbidden_names:
        raise PayloadInspectionError(
            f"production-compatible payload contains forbidden data: {sorted(forbidden_names)}"
        )

    rendered = text.decode("utf-8", errors="ignore")
    tags = GLADYS_IMAGE.findall(rendered)
    if not tags or any(tag != "v5" for tag in tags):
        raise PayloadInspectionError(
            "production-compatible payload must reference only gladysassistant/gladys:v5"
        )
    if "nickfedor/watchtower" not in rendered:
        raise PayloadInspectionError("production-compatible payload misses Watchtower")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument(
        "--profile", required=True, choices=("production", "ci-smoke", "ci-real")
    )
    args = parser.parse_args()
    inspect_payload(args.archive, args.profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Compare El Torito boot semantics while ignoring physical ISO block layout."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path


class BootMetadataError(ValueError):
    pass


APPENDED_PARTITION = re.compile(r"appended_partition_(\d+)")
ISO_BLOCK_SIZE = 2048
BootSemantics = tuple[str, str, tuple[tuple[str, ...], ...]]
HiddenExtents = tuple[tuple[str, int, int], ...]


def _image_path(value: str) -> str:
    match = APPENDED_PARTITION.search(value)
    if match is not None:
        return f"appended_partition_{match.group(1)}"
    return value


def parse_report(report: str) -> tuple[BootSemantics, HiddenExtents]:
    catalog_blocks = ""
    catalog_path = ""
    catalog_path_seen = False
    images: dict[str, dict[str, tuple[str, ...] | str]] = {}
    for raw_line in report.splitlines():
        line = raw_line.strip()
        if line.startswith("El Torito catalog  :"):
            fields = line.split(":", 1)[1].split()
            if (
                len(fields) != 2
                or not fields[0].isdigit()
                or int(fields[0]) <= 0
                or not fields[1].isdigit()
                or int(fields[1]) <= 0
            ):
                raise BootMetadataError(f"unexpected El Torito catalog record: {line}")
            if catalog_blocks:
                raise BootMetadataError("duplicate El Torito catalog record")
            # The first field is the physical LBA. The second is the catalog's
            # semantic extent length and must remain equal after remastering.
            catalog_blocks = str(int(fields[1]))
        elif line.startswith("El Torito cat path :"):
            if catalog_path_seen:
                raise BootMetadataError("duplicate El Torito catalog path")
            catalog_path = line.split(":", 1)[1].strip()
            catalog_path_seen = True
        elif line.startswith("El Torito boot img :"):
            fields = line.split(":", 1)[1].split()
            if (
                len(fields) != 8
                or not fields[0].isdigit()
                or int(fields[0]) <= 0
                or not fields[-1].isdigit()
                or int(fields[-1]) <= 0
            ):
                raise BootMetadataError(f"unexpected El Torito boot image record: {line}")
            index = str(int(fields[0]))
            if index in images and "boot" in images[index]:
                raise BootMetadataError(f"duplicate El Torito boot image: {index}")
            # LBA is physical layout. Platform, bootability, emulation, segment,
            # partition type and load size are semantic and must remain equal.
            images.setdefault(index, {})["boot"] = tuple(fields[:-1])
            images[index]["lba"] = str(int(fields[-1]))
        elif line.startswith("El Torito img path :"):
            fields = line.split(":", 1)[1].split(maxsplit=1)
            if len(fields) != 2 or not fields[0].isdigit() or int(fields[0]) <= 0:
                raise BootMetadataError(f"unexpected El Torito image path record: {line}")
            index = str(int(fields[0]))
            image = images.setdefault(index, {})
            if "path" in image:
                raise BootMetadataError(f"duplicate El Torito image path: {index}")
            image["path"] = _image_path(fields[1])
        elif line.startswith("El Torito img blks :"):
            fields = line.split(":", 1)[1].split()
            if (
                len(fields) != 2
                or not fields[0].isdigit()
                or int(fields[0]) <= 0
                or not fields[1].isdigit()
                or int(fields[1]) <= 0
            ):
                raise BootMetadataError(f"unexpected El Torito image block record: {line}")
            index = str(int(fields[0]))
            if index in images and "blocks" in images[index]:
                raise BootMetadataError(f"duplicate El Torito image block record: {index}")
            images.setdefault(index, {})["blocks"] = str(int(fields[1]))
        elif line.startswith(("El Torito img opts :", "El Torito img id   :")):
            fields = line.split(":", 1)[1].split(maxsplit=1)
            if not fields:
                raise BootMetadataError(f"unexpected El Torito image metadata: {line}")
            if not fields[0].isdigit() or int(fields[0]) <= 0:
                raise BootMetadataError(f"unexpected El Torito image metadata: {line}")
            index = str(int(fields[0]))
            key = "options" if "opts" in line else "id"
            image = images.setdefault(index, {})
            if key in image:
                raise BootMetadataError(
                    f"duplicate El Torito image {key} record: {index}"
                )
            image[key] = fields[1] if len(fields) == 2 else ""

    if not catalog_blocks or not catalog_path or not images:
        raise BootMetadataError("El Torito catalog or boot images are missing")
    normalized: list[tuple[str, ...]] = []
    hidden_extents: list[tuple[str, int, int]] = []
    platforms: set[str] = set()
    for index in sorted(images, key=lambda value: int(value)):
        image = images[index]
        boot = image.get("boot")
        path = image.get("path")
        blocks = image.get("blocks")
        lba = image.get("lba")
        if not isinstance(boot, tuple) or not (
            isinstance(path, str) or isinstance(blocks, str)
        ):
            raise BootMetadataError(f"incomplete El Torito image metadata: {index}")
        if not isinstance(path, str):
            if not isinstance(blocks, str) or not isinstance(lba, str):
                raise BootMetadataError(f"incomplete hidden El Torito image: {index}")
            hidden_extents.append((index, int(lba), int(blocks)))
        platforms.add(boot[1])
        normalized.append(
            (
                *boot,
                path if isinstance(path, str) else "",
                blocks if isinstance(blocks, str) else "",
                str(image.get("options", "")),
                str(image.get("id", "")),
            )
        )
    if "UEFI" not in platforms:
        raise BootMetadataError("El Torito metadata has no UEFI boot image")
    return (catalog_path, catalog_blocks, tuple(normalized)), tuple(hidden_extents)


def _extent_digest(iso: Path, lba: int, blocks: int) -> bytes:
    offset = lba * ISO_BLOCK_SIZE
    length = blocks * ISO_BLOCK_SIZE
    digest = hashlib.sha256()
    try:
        with iso.open("rb") as handle:
            handle.seek(0, 2)
            if offset + length > handle.tell():
                raise BootMetadataError(f"hidden El Torito extent exceeds ISO size: {iso}")
            handle.seek(offset)
            remaining = length
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise BootMetadataError(f"cannot read hidden El Torito extent: {iso}")
                digest.update(chunk)
                remaining -= len(chunk)
    except OSError as exc:
        raise BootMetadataError(f"cannot read ISO for boot comparison: {iso}") from exc
    return digest.digest()


def compare_reports(
    base: str,
    custom: str,
    *,
    base_iso: Path | None = None,
    custom_iso: Path | None = None,
) -> bool:
    base_metadata, base_hidden = parse_report(base)
    custom_metadata, custom_hidden = parse_report(custom)
    if base_metadata != custom_metadata:
        raise BootMetadataError("custom ISO boot semantics differ from authenticated base")
    if (base_iso is None) != (custom_iso is None):
        raise BootMetadataError("both ISO paths are required for hidden boot comparison")
    if base_iso is not None and custom_iso is not None:
        base_shape = tuple((index, blocks) for index, _lba, blocks in base_hidden)
        custom_shape = tuple((index, blocks) for index, _lba, blocks in custom_hidden)
        if base_shape != custom_shape:
            raise BootMetadataError("custom ISO hidden boot extents differ from base")
        for base_extent, custom_extent in zip(base_hidden, custom_hidden, strict=True):
            index, base_lba, blocks = base_extent
            _custom_index, custom_lba, _custom_blocks = custom_extent
            if _extent_digest(base_iso, base_lba, blocks) != _extent_digest(
                custom_iso, custom_lba, blocks
            ):
                raise BootMetadataError(
                    f"custom ISO hidden boot image differs from base: {index}"
                )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("custom", type=Path)
    parser.add_argument("--base-iso", required=True, type=Path)
    parser.add_argument("--custom-iso", required=True, type=Path)
    args = parser.parse_args()
    compare_reports(
        args.base.read_text(encoding="utf-8"),
        args.custom.read_text(encoding="utf-8"),
        base_iso=args.base_iso,
        custom_iso=args.custom_iso,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

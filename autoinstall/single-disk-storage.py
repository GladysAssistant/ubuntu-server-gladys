#!/usr/bin/env python3
"""Skip the storage screen when the machine has exactly one internal disk.

Runs as an Autoinstall early command, before the first installer screen.
Subiquity reloads the Autoinstall document afterwards, so dropping "storage"
from the interactive sections installs onto that disk with the configured
layout. The other sections stay interactive, so the installer still asks for
the destructive-action confirmation before erasing anything. Any doubt keeps
the storage screen.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


# Smaller disks keep the screen so Subiquity can explain why they do not fit.
MINIMUM_DISK_BYTES = 16 * 1024**3
EXTERNAL_TRANSPORTS = {"usb", "ieee1394"}
VIRTUAL_DISK_NAME = re.compile(r"^(?:loop|ram|zram|nbd)\d+$|^mmcblk\d+boot\d+$")
LSBLK = [
    "lsblk",
    "--json",
    "--bytes",
    "--output",
    "NAME,PATH,TYPE,RM,RO,HOTPLUG,TRAN,SIZE,MOUNTPOINTS",
]


def _flag(value: object) -> bool:
    # lsblk reports booleans or "0"/"1" strings depending on its version.
    return value is True or value in ("1", 1)


def _in_use(device: dict) -> bool:
    if any(mountpoint for mountpoint in device.get("mountpoints") or []):
        return True
    return any(_in_use(child) for child in device.get("children") or [])


def internal_disks(devices: list[dict]) -> list[str]:
    """Return the paths of the disks the installer could erase unattended."""
    disks = []
    for device in devices:
        if device.get("type") != "disk":
            continue
        if VIRTUAL_DISK_NAME.match(device.get("name") or ""):
            continue
        if _flag(device.get("rm")) or _flag(device.get("ro")) or _flag(device.get("hotplug")):
            continue
        if (device.get("tran") or "") in EXTERNAL_TRANSPORTS:
            continue
        if int(device.get("size") or 0) < MINIMUM_DISK_BYTES:
            continue
        # The installation media and anything else mounted stay untouched.
        if _in_use(device):
            continue
        disks.append(device["path"])
    return disks


def preselect_disk(config: dict, disk: str) -> bool:
    """Make storage non-interactive on the given disk; False if not applicable."""
    sections = config.get("interactive-sections") or []
    if "storage" not in sections:
        return False
    config["interactive-sections"] = [section for section in sections if section != "storage"]
    config["storage"]["layout"]["match"] = {"path": disk}
    return True


def _write_atomically(path: Path, document: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            yaml.safe_dump(document, handle, sort_keys=False)
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    except BaseException:
        os.unlink(temporary)
        raise


def run(autoinstall_path: Path, lsblk_output: str) -> str:
    disks = internal_disks(json.loads(lsblk_output)["blockdevices"])
    if len(disks) != 1:
        return f"{len(disks)} internal disks found, keeping the storage screen"
    document = yaml.safe_load(autoinstall_path.read_text(encoding="utf-8"))
    if not preselect_disk(document["autoinstall"], disks[0]):
        return "storage is not interactive, nothing to do"
    _write_atomically(autoinstall_path, document)
    return f"single internal disk {disks[0]}, skipping the storage screen"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"Usage: {argv[0]} AUTOINSTALL", file=sys.stderr)
        return 0
    # Never fail the installation: any error keeps the storage screen.
    try:
        lsblk = subprocess.run(LSBLK, check=True, capture_output=True, text=True)
        print(run(Path(argv[1]), lsblk.stdout))
    except Exception as exc:
        print(f"keeping the storage screen: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""Hold a CI-only APT transaction when QEMU explicitly requests a power cut."""

from __future__ import annotations

import sys
import time
from pathlib import Path


FIRMWARE_SIGNAL = Path(
    "/sys/firmware/qemu_fw_cfg/by_name/opt/gladys/power-loss-test/raw"
)
RUNTIME = Path("/usr/lib/gladys-installer")
STATE = Path("/var/lib/gladys-installer/state.json")


def main() -> int:
    try:
        requested = FIRMWARE_SIGNAL.read_bytes().rstrip(b"\x00\n") == b"1"
    except OSError:
        requested = False
    if not requested:
        return 0

    sys.path.insert(0, str(RUNTIME))
    import common  # pylint: disable=import-outside-toplevel

    common.write_state(
        STATE,
        common.Phase.INSTALL_PACKAGES,
        30,
        "Installing required packages (dpkg postinst active; package configuration pending).",
    )
    while True:
        time.sleep(60)


if __name__ == "__main__":
    raise SystemExit(main())

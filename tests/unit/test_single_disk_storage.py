from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "single_disk_storage", ROOT / "autoinstall" / "single-disk-storage.py"
)
single_disk_storage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(single_disk_storage)

GiB = 1024**3


def disk(name: str, *, size: int = 256 * GiB, tran: str | None = "nvme", **fields) -> dict:
    device = {
        "name": name,
        "path": f"/dev/{name}",
        "type": "disk",
        "rm": False,
        "ro": False,
        "hotplug": False,
        "tran": tran,
        "size": size,
        "mountpoints": [None],
    }
    device.update(fields)
    return device


def install_media() -> dict:
    return disk(
        "sda",
        size=8 * GiB,
        tran="usb",
        rm=True,
        hotplug=True,
        children=[
            {"name": "sda1", "path": "/dev/sda1", "type": "part", "mountpoints": ["/cdrom"]},
        ],
    )


def lsblk(*devices: dict) -> str:
    return json.dumps({"blockdevices": list(devices)})


class InternalDiskTests(unittest.TestCase):
    def test_single_nvme_beside_the_install_media(self) -> None:
        devices = [install_media(), disk("nvme0n1"), {"name": "sr0", "path": "/dev/sr0", "type": "rom"}]
        self.assertEqual(single_disk_storage.internal_disks(devices), ["/dev/nvme0n1"])

    def test_counts_every_internal_disk(self) -> None:
        devices = [disk("nvme0n1"), disk("sdb", tran="sata"), disk("mmcblk0", tran=None)]
        self.assertEqual(
            single_disk_storage.internal_disks(devices),
            ["/dev/nvme0n1", "/dev/sdb", "/dev/mmcblk0"],
        )

    def test_ignores_external_virtual_small_and_in_use_disks(self) -> None:
        devices = [
            disk("sdb", tran="usb"),
            disk("sdc", tran="sata", hotplug=True),
            disk("sdd", tran="sata", rm=True),
            disk("sde", tran="sata", ro=True),
            disk("zram0", tran=None),
            disk("loop0", tran=None),
            disk("mmcblk0boot0", tran=None),
            disk("sdf", tran="sata", size=8 * GiB),
            disk(
                "sdg",
                tran="sata",
                children=[{"name": "sdg1", "path": "/dev/sdg1", "type": "part", "mountpoints": ["[SWAP]"]}],
            ),
        ]
        self.assertEqual(single_disk_storage.internal_disks(devices), [])

    def test_accepts_legacy_string_flags(self) -> None:
        devices = [
            disk("vda", tran=None, rm="0", ro="0", hotplug="0", size=str(32 * GiB)),
            disk("sdb", tran="sata", rm="1"),
        ]
        self.assertEqual(single_disk_storage.internal_disks(devices), ["/dev/vda"])


class AutoinstallRewriteTests(unittest.TestCase):
    def _run(self, source: Path, *devices: dict) -> tuple[str, dict]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "autoinstall.yaml"
            path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            path.chmod(0o600)
            message = single_disk_storage.run(path, lsblk(*devices))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ["autoinstall.yaml"])
            return message, yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_single_disk_makes_storage_non_interactive_on_that_disk(self) -> None:
        source = ROOT / "autoinstall" / "autoinstall.yaml"
        expected = yaml.safe_load(source.read_text(encoding="utf-8"))
        expected["autoinstall"]["interactive-sections"].remove("storage")
        expected["autoinstall"]["storage"]["layout"]["match"] = {"path": "/dev/nvme0n1"}

        message, document = self._run(source, install_media(), disk("nvme0n1"))

        self.assertIn("/dev/nvme0n1", message)
        self.assertEqual(document, expected)
        self.assertEqual(
            document["autoinstall"]["interactive-sections"],
            ["locale", "keyboard", "identity", "ssh"],
        )
        self.assertEqual(
            document["autoinstall"]["storage"],
            {"layout": {"name": "lvm", "match": {"path": "/dev/nvme0n1"}}},
        )

    def test_several_or_no_disks_keep_the_storage_screen(self) -> None:
        source = ROOT / "autoinstall" / "autoinstall.yaml"
        original = yaml.safe_load(source.read_text(encoding="utf-8"))
        for devices in ((install_media(),), (disk("nvme0n1"), disk("sdb", tran="sata"))):
            with self.subTest(devices=[device["name"] for device in devices]):
                message, document = self._run(source, *devices)
                self.assertIn("keeping the storage screen", message)
                self.assertEqual(document, original)

    def test_unattended_ci_media_stay_unchanged(self) -> None:
        source = ROOT / "autoinstall" / "autoinstall-ci.yaml"
        original = yaml.safe_load(source.read_text(encoding="utf-8"))

        message, document = self._run(source, disk("vda", tran=None))

        self.assertIn("not interactive", message)
        self.assertEqual(document, original)

    def test_main_never_fails_the_installation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "autoinstall.yaml"
            path.write_text("autoinstall: {version: 1}\n", encoding="utf-8")
            with mock.patch.object(
                single_disk_storage.subprocess, "run", side_effect=FileNotFoundError("lsblk")
            ):
                self.assertEqual(single_disk_storage.main(["single-disk-storage.py", str(path)]), 0)
            completed = mock.Mock(stdout=lsblk(disk("nvme0n1")))
            with mock.patch.object(single_disk_storage.subprocess, "run", return_value=completed):
                self.assertEqual(single_disk_storage.main(["single-disk-storage.py", str(path)]), 0)
            self.assertEqual(path.read_text(encoding="utf-8"), "autoinstall: {version: 1}\n")


if __name__ == "__main__":
    unittest.main()

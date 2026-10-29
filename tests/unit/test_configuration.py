from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def load_autoinstall(name: str) -> dict:
    document = yaml.safe_load((ROOT / "autoinstall" / name).read_text(encoding="utf-8"))
    if set(document) != {"autoinstall"}:
        raise AssertionError("installation-media config must contain only autoinstall")
    return document["autoinstall"]


class AutoinstallConfigurationTests(unittest.TestCase):
    def test_production_prompts_for_operator_choices(self) -> None:
        config = load_autoinstall("autoinstall.yaml")

        self.assertEqual(config["version"], 1)
        self.assertEqual(
            config["interactive-sections"],
            ["locale", "keyboard", "storage", "identity", "ssh"],
        )
        self.assertEqual(config["locale"], "en_US.UTF-8")
        self.assertNotIn("keyboard", config)
        self.assertEqual(config["source"]["id"], "ubuntu-server-minimal")
        # LVM, as on the storage screen; the early command only adds the disk.
        self.assertEqual(
            config["storage"], {"layout": {"name": "lvm", "sizing-policy": "all"}}
        )
        self.assertEqual(
            config["early-commands"],
            [
                [
                    "sh",
                    "-c",
                    "python3 /cdrom/gladys/single-disk-storage.py /autoinstall.yaml || true",
                ]
            ],
        )
        self.assertEqual(config["ssh"], {"install-server": True})
        # The restricted driver pool is pruned from the ISO.
        self.assertEqual(config["drivers"], {"install": False})
        self.assertNotIn("identity", config)
        # Subiquity appends list values, so the identity screen account survives.
        self.assertEqual(config["user-data"]["users"], [])
        self.assertTrue(config["user-data"]["disable_root"])
        self.assertEqual(config["user-data"]["hostname"], "gladys")
        # The SSH screen decides password authentication.
        self.assertNotIn("ssh_pwauth", config["user-data"])

    def test_ci_differs_only_in_interactivity_and_identity(self) -> None:
        production = copy.deepcopy(load_autoinstall("autoinstall.yaml"))
        ci = copy.deepcopy(load_autoinstall("autoinstall-ci.yaml"))

        self.assertEqual(
            production.pop("interactive-sections"),
            ["locale", "keyboard", "storage", "identity", "ssh"],
        )
        self.assertEqual(ci.pop("interactive-sections"), [])
        identity = ci.pop("identity")
        self.assertEqual(identity["username"], "gladys-ci")
        self.assertNotEqual(identity["hostname"], "gladys")
        self.assertTrue(identity["password"].startswith("$6$"))
        self.assertEqual(ci, production)

    @unittest.skipUnless(shutil.which("sh"), "requires a POSIX shell")
    def test_late_command_grows_only_the_guided_root_volume(self) -> None:
        commands = load_autoinstall("autoinstall.yaml")["late-commands"]
        grow = next(command for command in commands if "lvextend" in " ".join(command))
        root = "/dev/mapper/ubuntu--vg-ubuntu--lv"
        cases = {
            # lv_name, vg_free_count, lvextend status: expected lvextend call
            "scaled guided volume": ("ubuntu-lv", "2048", 0, True),
            "whole-disk volume": ("ubuntu-lv", "0", 0, False),
            "custom volume": ("data", "2048", 0, False),
            "not a volume": (None, "", 0, False),
            "lvextend failure": ("ubuntu-lv", "2048", 5, True),
        }
        for name, (lv_name, free, status, expect_call) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                bin_dir = Path(directory)
                calls = bin_dir / "calls"
                lvs_name = f"echo '  {lv_name}'" if lv_name else "exit 5"
                scripts = {
                    "findmnt": f"echo {root}",
                    "lvs": (
                        'case "$*" in *lv_name*) '
                        f"{lvs_name};; *vg_free_count*) echo '  {free}';; esac"
                    ),
                    "lvextend": f'echo "$*" >> {calls}; exit {status}',
                }
                for tool, body in scripts.items():
                    (bin_dir / tool).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
                    (bin_dir / tool).chmod(0o755)
                environment = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

                result = subprocess.run(grow, env=environment, capture_output=True, text=True)

                self.assertEqual(result.returncode, 0, result.stderr)
                if expect_call:
                    self.assertEqual(
                        calls.read_text(encoding="utf-8"),
                        f"--resizefs -l +100%FREE {root}\n",
                    )
                else:
                    self.assertFalse(calls.exists())

    def test_late_commands_verify_before_extracting_payload(self) -> None:
        commands = load_autoinstall("autoinstall.yaml")["late-commands"]
        rendered = "\n".join(command if isinstance(command, str) else " ".join(command) for command in commands)

        verify_at = rendered.index("sha256sum -c gladys-payload.tar.sha256")
        extract_at = rendered.index("tar --extract")
        self.assertLess(verify_at, extract_at)
        self.assertLess(extract_at, rendered.index("> /target/etc/gladys-installer/language"))
        self.assertNotIn("daemon-reload", rendered)
        self.assertIn("systemctl --root=/target enable gladys-firstboot.service", rendered)
        self.assertIn("systemctl --root=/target mask getty@tty1.service", rendered)

    def test_installer_version_is_independent_from_gladys(self) -> None:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

        self.assertRegex(version, r"^\d+\.\d+\.\d+$")


class ComposeConfigurationTests(unittest.TestCase):
    def test_production_compose_uses_only_the_v5_channel(self) -> None:
        compose_path = ROOT / "payload" / "opt" / "gladys" / "compose.yaml"
        text = compose_path.read_text(encoding="utf-8")
        compose = yaml.safe_load(text)
        gladys = compose["services"]["gladys"]

        self.assertEqual(gladys["image"], "gladysassistant/gladys:v5")
        self.assertTrue(gladys["privileged"])
        self.assertEqual(gladys["network_mode"], "host")
        self.assertEqual(gladys["cgroup"], "host")
        self.assertIn("/var/lib/gladysassistant:/var/lib/gladysassistant", gladys["volumes"])
        self.assertIsNone(re.search(r"gladysassistant/gladys:v5\.\d", text))
        # First boot writes the detected zone to the .env file next to it.
        self.assertEqual(gladys["environment"]["TZ"], "${GLADYS_TZ:-UTC}")

    def test_watchtower_updates_only_gladys_and_itself(self) -> None:
        compose = yaml.safe_load(
            (ROOT / "payload" / "opt" / "gladys" / "compose.yaml").read_text(encoding="utf-8")
        )
        watchtower = compose["services"]["watchtower"]

        # A moving tag keeps Watchtower compatible with Docker API changes.
        self.assertEqual(watchtower["image"], "nickfedor/watchtower")
        self.assertIn("--label-enable", watchtower["command"])
        for name, service in compose["services"].items():
            with self.subTest(service=name):
                self.assertEqual(
                    service["labels"], {"com.centurylinklabs.watchtower.enable": "true"}
                )

    def test_container_logs_are_rotated(self) -> None:
        compose = yaml.safe_load(
            (ROOT / "payload" / "opt" / "gladys" / "compose.yaml").read_text(encoding="utf-8")
        )
        for name, service in compose["services"].items():
            with self.subTest(service=name):
                self.assertEqual(service["logging"]["driver"], "json-file")
                self.assertEqual(service["logging"]["options"]["max-size"], "10m")
        daemon = json.loads(
            (ROOT / "payload" / "etc" / "docker" / "daemon.json").read_text(encoding="utf-8")
        )
        self.assertEqual(daemon["log-driver"], "json-file")
        self.assertEqual(daemon["log-opts"], {"max-size": "10m", "max-file": "3"})


if __name__ == "__main__":
    unittest.main()

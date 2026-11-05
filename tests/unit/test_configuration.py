from __future__ import annotations

import copy
import json
import re
import shutil
import stat
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

    def test_late_commands_verify_before_extracting_payload(self) -> None:
        commands = load_autoinstall("autoinstall.yaml")["late-commands"]
        rendered = "\n".join(command if isinstance(command, str) else " ".join(command) for command in commands)

        verify_at = rendered.index("sha256sum -c gladys-payload.tar.sha256")
        extract_at = rendered.index("tar --extract")
        self.assertLess(verify_at, extract_at)
        self.assertLess(extract_at, rendered.index("> /target/etc/gladys-installer/language"))
        self.assertNotIn("daemon-reload", rendered)
        # The root volume keeps the size confirmed on the storage screen.
        self.assertNotIn("lvextend", rendered)
        self.assertIn("systemctl --root=/target enable gladys-firstboot.service", rendered)
        self.assertIn("systemctl --root=/target mask getty@tty1.service", rendered)

    def test_install_without_network_falls_back_to_dhcp_on_wired_interfaces(self) -> None:
        commands = load_autoinstall("autoinstall.yaml")["late-commands"]
        extract_at = next(i for i, command in enumerate(commands) if "tar --extract" in str(command))
        fallback_at = next(i for i, command in enumerate(commands) if "netplan-wired-dhcp.yaml" in str(command))
        self.assertLess(extract_at, fallback_at)
        fallback = commands[fallback_at]
        payload_file = ROOT / "payload" / "usr" / "share" / "gladys-installer" / "netplan-wired-dhcp.yaml"
        self.assertEqual(
            yaml.safe_load(payload_file.read_text(encoding="utf-8")),
            {"network": {"version": 2, "ethernets": {"wired": {"match": {"name": "en*"}, "dhcp4": True}}}},
        )

        # What Subiquity writes to the target. An interface without an address
        # after its DHCP wait loses dhcp4 and dhcp6 but keeps the rest of its
        # live-system definition, such as its MAC match, and is left out only
        # when nothing remains (subiquitycore/models/network.py and netplan.py).
        stripped = {"match": {"macaddress": "52:54:00:12:34:56"}, "set-name": "enp0s2"}
        cases = {
            "offline, interface left out": ({}, True),
            "offline, interface kept without DHCP": ({"enp0s2": stripped}, True),
            "online": ({"enp1s0": {"dhcp4": True}}, False),
        }
        for name, (ethernets, expect_fallback) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                netplan = target / "etc" / "netplan"
                netplan.mkdir(parents=True)
                installer_config = netplan / "00-installer-config.yaml"
                installer_config.write_text(
                    "# This is the network config written by 'subiquity'\n"
                    + yaml.dump({"network": {"version": 2, "ethernets": ethernets}}, default_flow_style=False),
                    encoding="utf-8",
                )
                installer_config.chmod(0o600)
                before = installer_config.read_bytes()
                share = target / "usr" / "share" / "gladys-installer"
                share.mkdir(parents=True)
                shutil.copyfile(payload_file, share / payload_file.name)

                script = fallback[2].replace("/target/", f"{target}/")
                subprocess.run([fallback[0], fallback[1], script], check=True)

                # Only one netplan file, so no leftover definition can shadow DHCP.
                self.assertEqual([path.name for path in netplan.iterdir()], ["00-installer-config.yaml"])
                expected = payload_file.read_bytes() if expect_fallback else before
                self.assertEqual(installer_config.read_bytes(), expected)
                self.assertEqual(stat.S_IMODE(installer_config.stat().st_mode), 0o600)

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
        self.assertIn("/run/udev:/run/udev:ro", gladys["volumes"])
        self.assertIn("/run/dbus:/run/dbus:ro", gladys["volumes"])
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

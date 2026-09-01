from __future__ import annotations

import copy
import re
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
        self.assertEqual(config["locale"], "fr_FR.UTF-8")
        self.assertEqual(config["keyboard"], {"layout": "fr"})
        self.assertEqual(config["source"]["id"], "ubuntu-server-minimal")
        self.assertEqual(config["storage"]["layout"]["name"], "direct")
        self.assertEqual(config["ssh"], {"install-server": False})
        self.assertNotIn("identity", config)
        self.assertTrue(config["user-data"]["disable_root"])
        # The account comes from the interactive identity screen; cloud-init
        # user-data must not override or suppress it.
        self.assertNotIn("users", config["user-data"])
        self.assertNotIn("ssh_pwauth", config["user-data"])

    def test_ci_differs_only_in_interactivity_and_account_pin(self) -> None:
        production = copy.deepcopy(load_autoinstall("autoinstall.yaml"))
        ci = copy.deepcopy(load_autoinstall("autoinstall-ci.yaml"))

        self.assertEqual(
            production.pop("interactive-sections"),
            ["locale", "keyboard", "storage", "identity", "ssh"],
        )
        self.assertEqual(ci.pop("interactive-sections"), [])
        self.assertEqual(ci["user-data"].pop("users"), [])
        self.assertIs(ci["user-data"].pop("ssh_pwauth"), False)
        self.assertEqual(ci, production)

    def test_late_commands_verify_before_extracting_payload(self) -> None:
        commands = load_autoinstall("autoinstall.yaml")["late-commands"]
        rendered = "\n".join(command if isinstance(command, str) else " ".join(command) for command in commands)

        verify_at = rendered.index("sha256sum -c gladys-payload.tar.sha256")
        extract_at = rendered.index("tar --extract")
        self.assertLess(verify_at, extract_at)
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
        self.assertEqual(compose["services"]["watchtower"]["image"], "nickfedor/watchtower")
        self.assertIsNone(re.search(r"gladysassistant/gladys:v5\.\d", text))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import subprocess
import unittest
from pathlib import Path
from unittest import mock


RUNTIME = Path(__file__).resolve().parents[2] / "payload" / "usr" / "lib" / "gladys-installer"
sys.path.insert(0, str(RUNTIME))

import console  # noqa: E402


class ConsoleRenderingTests(unittest.TestCase):
    def test_wait_network_screen_recommends_ethernet(self) -> None:
        screen = console.render_screen(
            {"phase": "WAIT_NETWORK", "progress": 5, "message": "Waiting"},
            [],
            {},
            language="fr",
        )

        self.assertIn("Connexion Internet requise", screen)
        self.assertIn("câble Ethernet", screen)
        self.assertIn("En attente de connexion", screen)
        self.assertNotIn("…. ", screen)
        self.assertNotIn("│", screen)

    def test_unrenderable_tty_script_uses_readable_english_fallback(self) -> None:
        screen = console.render_screen(
            {"phase": "WAIT_NETWORK", "progress": 5, "message": "Waiting"},
            [],
            {},
            language="ar",
        )

        self.assertIn("Internet connection required", screen)
        self.assertNotIn("يلزم اتصال بالإنترنت", screen)

    def test_ready_screen_displays_mdns_and_lan_addresses(self) -> None:
        screen = console.render_screen(
            {"phase": "READY", "progress": 100, "message": "Ready"},
            ["192.168.1.42"],
            {"docker": True, "gladys": True, "ethernet": True},
        )

        self.assertIn("✓ Gladys Assistant is ready", screen)
        self.assertIn("http://gladysassistant.local", screen)
        self.assertNotIn("http://gladys.local", screen)
        self.assertIn("http://192.168.1.42", screen)
        self.assertIn("Gladys: running", screen)
        self.assertNotIn("│", screen)

    def test_setup_screen_has_honest_animated_progress_without_fake_bytes(self) -> None:
        first = console.render_screen(
            {"phase": "PULL_GLADYS", "progress": 60, "message": "Downloading Gladys Assistant…"},
            ["10.0.0.8"],
            {},
            language="fr",
            frame=0,
        )
        second = console.render_screen(
            {"phase": "PULL_GLADYS", "progress": 60, "message": "Downloading Gladys Assistant…"},
            ["10.0.0.8"],
            {},
            language="fr",
            frame=1,
        )

        self.assertIn("60%", first)
        self.assertIn("Téléchargement de Gladys Assistant", first)
        self.assertIn("http://10.0.0.8", first)
        self.assertNotEqual(first, second)
        self.assertNotIn("MB", first)
        self.assertNotIn("bytes", first)
        self.assertNotIn("│", first)

    @mock.patch.object(console.common, "run_command")
    def test_stopped_gladys_container_is_not_reported_as_running(
        self, run_command: mock.Mock
    ) -> None:
        def result(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
            stdout = "false\n" if argv[0] == "docker" else ""
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

        run_command.side_effect = result

        states = console.service_states(["192.168.1.42"])

        self.assertFalse(states["gladys"])


if __name__ == "__main__":
    unittest.main()

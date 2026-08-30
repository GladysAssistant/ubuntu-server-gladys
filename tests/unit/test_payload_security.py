from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = ROOT / "payload"


class SystemdUnitTests(unittest.TestCase):
    def unit(self, name: str) -> str:
        return (PAYLOAD / "etc" / "systemd" / "system" / name).read_text(encoding="utf-8")

    def test_firstboot_retries_crashes_but_not_fatal_configuration(self) -> None:
        unit = self.unit("gladys-firstboot.service")
        self.assertIn("After=network-online.target gladys-setup-web.service", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("RestartSec=10", unit)
        self.assertIn("StartLimitIntervalSec=0", unit)
        self.assertIn("RestartPreventExitStatus=78", unit)
        self.assertIn("ExecStart=/usr/lib/gladys-installer/bootstrap.py", unit)

    def test_setup_web_cannot_start_after_completion_and_is_hardened(self) -> None:
        unit = self.unit("gladys-setup-web.service")
        self.assertIn("ConditionPathExists=!/var/lib/gladys-installer/completed", unit)
        self.assertIn("DynamicUser=yes", unit)
        self.assertIn("AmbientCapabilities=CAP_NET_BIND_SERVICE", unit)
        self.assertIn("NoNewPrivileges=yes", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("TasksMax=16", unit)
        self.assertIn("MemoryMax=64M", unit)
        self.assertIn("EnvironmentFile=-/etc/default/locale", unit)

    def test_console_owns_tty1_without_a_login_prompt(self) -> None:
        unit = self.unit("gladys-console.service")
        self.assertIn("Conflicts=getty@tty1.service", unit)
        self.assertIn("TTYPath=/dev/tty1", unit)
        self.assertIn("ExecStart=/usr/lib/gladys-installer/console.py", unit)
        self.assertIn("EnvironmentFile=-/etc/default/locale", unit)


class UpdateConfigurationTests(unittest.TestCase):
    def test_only_security_updates_are_automatic_and_reboots_are_disabled(self) -> None:
        periodic = (PAYLOAD / "etc" / "apt" / "apt.conf.d" / "20auto-upgrades").read_text(
            encoding="utf-8"
        )
        unattended = (
            PAYLOAD / "etc" / "apt" / "apt.conf.d" / "52gladys-unattended-upgrades"
        ).read_text(encoding="utf-8")
        release = (PAYLOAD / "etc" / "update-manager" / "release-upgrades").read_text(
            encoding="utf-8"
        )

        self.assertIn('APT::Periodic::Update-Package-Lists "1";', periodic)
        self.assertIn('APT::Periodic::Unattended-Upgrade "1";', periodic)
        self.assertIn('${distro_codename}-security', unattended)
        self.assertIn('Unattended-Upgrade::Automatic-Reboot "false";', unattended)
        self.assertNotIn("-updates", unattended)
        self.assertEqual(release.strip(), "[DEFAULT]\nPrompt=never")


class DiagnosticsTests(unittest.TestCase):
    def test_diagnostics_uses_allowlisted_safe_commands(self) -> None:
        script = (PAYLOAD / "usr" / "local" / "sbin" / "gladys-diagnostics").read_text(
            encoding="utf-8"
        )

        for expected in (
            "uname -r",
            "ip -brief address",
            "ip route show default",
            "df -h",
            "docker version",
            "docker compose -f /opt/gladys/compose.yaml ps",
            "systemctl is-active",
            "journalctl -u gladys-firstboot.service",
        ):
            self.assertIn(expected, script)
        for forbidden in (
            "docker inspect",
            "printenv",
            "/var/lib/gladysassistant/gladys-production.db",
            "avahi-daemon.service",
        ):
            self.assertNotIn(forbidden, script)

    def test_production_payload_has_no_test_mode_marker(self) -> None:
        self.assertFalse((PAYLOAD / "etc" / "gladys-installer" / "test-mode").exists())
        self.assertFalse((PAYLOAD / "etc" / "gladys-installer" / "test-compose.yaml").exists())


if __name__ == "__main__":
    unittest.main()

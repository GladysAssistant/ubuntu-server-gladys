# Hardware validation checklist

Physical validation is the only path to a hardware support claim. An unchecked item is untested, not supported by inference, and QEMU results never substitute for real firmware.

For every run, record:

| Field | Value |
| --- | --- |
| Device model | |
| Firmware version | |
| Installer version | |
| Ubuntu base ISO SHA256 | |
| Date | |
| Tester | |
| Logs attached | |

## Platforms and storage

- [ ] Intel N100/N150 class mini-PC installs and reboots.
- [ ] Intel Core class mini-PC installs and reboots.
- [ ] AMD Ryzen class mini-PC installs and reboots.
- [ ] NVMe target is displayed correctly and installation succeeds.
- [ ] SATA SSD target is displayed correctly and installation succeeds.
- [ ] Single internal disk system skips the storage screen, installs on that disk with LVM whose root volume fills the disk (`df -h /`), and still asks the destructive-action confirmation.
- [ ] Single internal disk system with an additional USB drive plugged in still skips the storage screen and leaves the USB drive untouched.
- [ ] Multi disk system shows the storage screen with LVM preselected, identifies every disk correctly, erases only the confirmed target, and ends with a root volume that fills that disk (`df -h /`).

## Firmware and boot

- [ ] UEFI boot with Secure Boot disabled.
- [ ] UEFI boot with Secure Boot enabled.
- [ ] **Install Gladys Assistant** is the only `/casper` installer entry and is selected by default.
- [ ] GRUB starts the Gladys installer automatically after a visible three second timeout.
- [ ] Media integrity checking remains enabled and passes.
- [ ] USB removal and installed disk boot behavior are clear to the tester.

Secure Boot is expected to remain compatible because the Ubuntu signed boot chain is preserved, but this requires validation. Never mark it supported based only on QEMU OVMF results.

## Networking and discovery

- [ ] Ethernet DHCP obtains a LAN address.
- [ ] The setup page is reachable through the IPv4 address displayed on tty1 before the application handoff.
- [ ] `gladysassistant.local` resolves after Gladys is ready, from the Linux, macOS/iOS, Windows, and Android clients available to the test.
- [ ] The tty1 screen displays the correct fallback IPv4 address.
- [ ] Installation completes without Internet, first boot shows `WAIT_NETWORK`, and connecting Internet resumes setup without a reboot.
- [ ] An already configured alternative interface is not prevented from working.

## Runtime and recovery

- [ ] Docker and Compose are installed from Ubuntu packages.
- [ ] Gladys answers HTTP and persists data across a reboot.
- [ ] Watchtower is running after Gladys readiness.
- [ ] Ubuntu security updates are enabled; automatic reboot and release upgrade remain disabled.
- [ ] Power interruption during APT package setup recovers.
- [ ] Power interruption during image pull recovers.
- [ ] Power interruption during application start recovers.
- [ ] Recovery leaves exactly one Gladys container, one Watchtower container, one data directory, and one completion marker.
- [ ] No default account, root password, or known password exists; the only account is the one created on the identity screen.
- [ ] With the default OpenSSH selection, the created account can log in over SSH with its password; unchecking the server leaves no SSH listener.
- [ ] The created account can log in on a virtual console (tty2+) while tty1 stays owned by the status display.
- [ ] `gladys-diagnostics` output is useful and contains no database, token, password, environment secret, or full inspection data.

## Usability

- [ ] The language screen appears first with English selected; choosing another language localizes the remaining supported Subiquity screens after confirmation.
- [ ] The keyboard screen appears before storage with the layout of the chosen language suggested; after choosing French AZERTY, typing on the next screens produces AZERTY characters.
- [ ] A non-technical tester can identify the destructive disk confirmation.
- [ ] The identity screen requires creating an account and the SSH screen presents the OpenSSH server selected by default.
- [ ] The machine is named `gladys` after installation, whatever server name was typed on the identity screen.
- [ ] After first boot reaches **Ready** and a fresh login, `docker ps` works without `sudo` or a password.
- [ ] `timedatectl` and the time shown by Gladys match the local time zone of the test site.
- [ ] The USB boot menu is white on blue with **Install Gladys Assistant** highlighted, with Secure Boot enabled and disabled.
- [ ] An SSH login and a Ctrl+Alt+F2 login show the Gladys status instead of Ubuntu's help links, `unminimize` notice, and Ubuntu Pro advertising, and the Ctrl+Alt+F2 prompt shows the Gladys address.
- [ ] The setup page follows the selected language; exercise French, English, a third Latin language, Chinese, and one right-to-left language.
- [ ] tty1 follows supported Latin, Greek, and Cyrillic selections and uses readable English for Arabic, Hebrew, Tibetan, Japanese, and Chinese rather than emitting unusable glyphs.
- [ ] Long package and container downloads keep a visible activity animation without displaying fabricated byte counts.
- [ ] The Ethernet requirement and the expected setup duration are clear.
- [ ] The browser transitions from the setup status to Gladys after the port 80 handoff.
- [ ] The fatal configuration state gives actionable local diagnostic guidance without exposing a stack trace.

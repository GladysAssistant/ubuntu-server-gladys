# Operations guide

This guide covers what happens after the Ubuntu installation reboots: the first boot phases, how to reach Gladys, how updates work, and what to do when something goes wrong. The installation steps themselves are in the [README](../README.md).

## What first boot does

On the first boot after installation, `gladys-firstboot.service` provisions the appliance: it installs Docker and Compose from Ubuntu packages, pulls the current `gladysassistant/gladys:v5` image, starts Gladys, verifies it answers HTTP, and only then starts Watchtower. While this runs, progress is visible in two places:

- the tty1 console on an attached screen, which also shows the machine's LAN IPv4 address;
- the setup web page served on port 80 at that IPv4 address.

When everything has succeeded, the console shows **Ready** and Gladys itself takes over port 80 and the `gladysassistant.local` mDNS name.

## First boot phases

| Phase | Meaning |
| --- | --- |
| `PREFLIGHT` | Local configuration and state checks before anything is changed |
| `WAIT_NETWORK` | Waiting for working Internet access, verified through DNS and TLS. The machine stays here safely until Ethernet provides connectivity |
| `APT_UPDATE` | Refreshing Ubuntu package indexes |
| `INSTALL_PACKAGES` | Installing Docker, Compose, and update tooling from Ubuntu packages |
| `START_SYSTEM_SERVICES` | Enabling and starting the required system services |
| `DOCKER_PREFLIGHT` | Verifying the Docker CLI, Compose, and the resolved Compose model |
| `PULL_GLADYS` | Downloading the Gladys `v5` image. This is usually the longest phase |
| `START_GLADYS` | Handing port 80 over from the setup page to Gladys |
| `VERIFY_GLADYS` | Waiting for Gladys to answer HTTP |
| `START_WATCHTOWER` | Pulling and starting Watchtower |
| `FINALIZE` | Recording the completion marker |
| `READY` | Provisioning is finished, Gladys is in charge |
| `FATAL` | A deterministic configuration problem stopped provisioning |

Transient problems (network drops, registry hiccups, APT mirrors) are retried automatically with capped backoff. Only deterministic configuration failures enter `FATAL`, and the service then stops instead of retrying forever.

The completion marker is `/var/lib/gladys-installer/completed`. The sibling `state.json` is informational only and is never used for decisions.

## Reaching Gladys

- Preferred address: `http://gladysassistant.local`, advertised by Gladys through its integrated mDNS support.
- Fallback: the IPv4 address shown on the tty1 console. Some networks (certain routers, VLANs, or Android configurations) do not carry mDNS; the IPv4 address always works from the same LAN.

## Data, updates, and lifecycle

- Gladys data persists in `/var/lib/gladysassistant` and survives container updates and reboots.
- Gladys follows the moving `v5` channel. Watchtower pulls compatible updates automatically and cleans replaced images. No manual action and no installer rebuild are needed for ordinary Gladys updates.
- Ubuntu installs security updates through `unattended-upgrades`. Automatic reboot and Ubuntu release upgrades are disabled.
- The only account is the one created during installation, and the OpenSSH server exists only if it was selected then. Root stays locked and no default credentials exist: see [SECURITY.md](SECURITY.md).

## Diagnostics

`gladys-diagnostics` prints safe, non-secret support information: installer and Ubuntu versions, boot mode, network state, disk usage, Docker and Compose status, service states, and recent first boot journal lines.

Log in with the account created during installation — on a virtual console with an attached keyboard (for example Ctrl+Alt+F2; tty1 is reserved for the status display) or over SSH if the OpenSSH server was selected — and run:

```bash
sudo gladys-diagnostics
```

It uses an allowlist of safe commands and never dumps the Gladys database, container inspection output, environment variables, tokens, or passwords, so its output can be shared in a support request.

Detailed logs live in journald:

```bash
journalctl -u gladys-firstboot.service
journalctl -u gladys-setup-web.service
journalctl -u gladys-console.service
```

## Troubleshooting

| Symptom | What it means and what to do |
| --- | --- |
| Console stays in `WAIT_NETWORK` | The machine has no verified Internet access. Check the Ethernet cable, DHCP, and upstream connectivity. Provisioning resumes automatically, without a reboot, as soon as the network works |
| `gladysassistant.local` does not resolve | The client network does not carry mDNS. Use the IPv4 address shown on the tty1 console instead |
| Browser still shows the setup page | The port 80 handoff has not happened yet. Wait for `READY` on the console, then refresh |
| Power was cut during provisioning | Just power the machine back on. Every phase is idempotent, package state is repaired automatically, and provisioning continues from where it left off |
| Console shows `FATAL` | A deterministic configuration problem was found. Run `sudo gladys-diagnostics` and read `journalctl -u gladys-firstboot.service` for the exact cause. The service exits with code 78 and does not retry |
| Gladys stops answering after months of use | Check `docker compose -f /opt/gladys/compose.yaml ps` and the diagnostics output. Gladys and Watchtower restart automatically on reboot |

## Reinstalling

Flashing and booting the installer again performs a fresh installation: the selected disk is erased after explicit confirmation, including `/var/lib/gladysassistant`. Back up Gladys data first if you need to keep it.

## Reporting a problem

Attach the output of `sudo gladys-diagnostics` and only the relevant, redacted `journalctl` lines. Never attach secrets, the Gladys database, or full Docker inspection output. For suspected security issues, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

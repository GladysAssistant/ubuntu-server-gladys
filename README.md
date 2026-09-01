# Gladys Assistant Installer x86-64

A bootable installer that turns an amd64 mini-PC into a dedicated [Gladys Assistant](https://gladysassistant.com) home automation appliance.

The installer is a remastered Ubuntu Server 26.04 LTS image. You flash it to a USB drive, boot the target machine, choose and confirm the internal disk to erase, and connect Ethernet. After the automated installation reboots, the machine installs Docker from Ubuntu packages, pulls the current `gladysassistant/gladys:v5` image, and starts Gladys together with Watchtower. Gladys then advertises its own address at `http://gladysassistant.local`.

## At a glance

| Topic | Summary |
| --- | --- |
| Base system | Ubuntu Server 26.04 LTS amd64, latest point release, authenticated against Canonical signatures on every build |
| Target hardware | UEFI amd64 mini-PCs with an internal SSD or NVMe and wired Ethernet |
| Application | `gladysassistant/gladys:v5`, pulled on first boot, never embedded in the ISO |
| Application updates | Watchtower follows the moving `v5` channel and cleans replaced images |
| System updates | `unattended-upgrades`, security updates only, no automatic reboot, no release upgrade |
| Data | Persistent Gladys data lives in `/var/lib/gladysassistant` |
| Remote access | Chosen at install time. You create your own administrator account and decide whether to install the OpenSSH server; root stays locked and the ISO embeds no account or password |

## What this is, and what it is not

This repository is an installer layer on top of a conventional Ubuntu Server installation. It is not a custom Linux distribution, not an immutable OS, and not a mirror of Gladys images.

The ISO never embeds a numbered Gladys release, a container image, a known password, or a remotely executable bootstrap. Gladys itself is downloaded on first boot from its official registry channel. The installed system remains a normal, supportable Ubuntu Server.

## Installing on a mini-PC

1. Download all the release assets: the numbered `.iso.part*` files, the `.sha256` file, and `build-info.json` (GitHub caps individual release assets at 2 GiB, so the ISO ships in parts).
2. Reassemble the ISO and verify its checksum and, when available, the GitHub artifact attestation as described in [docs/RELEASE.md](docs/RELEASE.md).
3. Flash the reassembled ISO to a USB drive with a raw image writer such as balenaEtcher or `dd`.
4. Boot the target machine in UEFI mode. GRUB shows **Install Gladys Assistant** and starts it automatically after a visible three second timeout.
5. Choose the installation language and keyboard layout. French and French AZERTY are preselected.
6. Select the intended internal SSD or NVMe and explicitly confirm its erasure. Production media never select or erase a disk unattended.
7. Create your administrator account (name, machine name, username, password) and choose whether to install the OpenSSH server. This account is yours for maintenance: the ISO ships no account or password of its own, and skipping OpenSSH keeps the appliance without remote shell access.
8. Let the installation finish, remove the USB drive, and leave Ethernet connected.
9. Follow the progress on the tty1 console or on the setup web page. Before Gladys starts, the console shows the IPv4 address of the setup page.
10. Wait for the console to show **Ready**, then open `http://gladysassistant.local`. The displayed IPv4 address remains the fallback when the client network does not support mDNS.

Provisioning requires Internet access over Ethernet. The Ubuntu installation itself can finish offline; first boot then waits safely in the `WAIT_NETWORK` phase and resumes automatically as soon as connectivity appears. Version 1 has no Wi-Fi setup UI.

Day to day operation, the first boot phases, diagnostics, and troubleshooting are covered in [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Building

Builds run on an amd64 Ubuntu/Linux host or under WSL2. The authenticated Ubuntu base ISO is cached under `build/`, and its hash is rechecked against freshly authenticated Canonical metadata on every build, including cache hits.

```bash
python3 -m venv --system-site-packages .venv
. .venv/bin/activate
pip install -r requirements-build.txt
validator="$(bash scripts/fetch-subiquity-validator.sh)"
make -C "$validator" install_deps
bash scripts/build-iso.sh
```

`build-iso.sh` builds the `production` profile by default. Two CI profiles exist for automated testing: `ci-smoke` uses automatic disposable disk storage and a pinned lightweight HTTP container, `ci-real` uses automatic disposable disk storage with the exact production payload and Compose contract. Test fixtures are injected only into a temporary `ci-smoke` overlay and never touch the working tree.

Full host setup, build profiles, and QEMU instructions are in [docs/BUILD.md](docs/BUILD.md).

## Testing

Fast local checks, none of which need root, Docker, or network:

```bash
python3 -m compileall -q payload/usr/lib/gladys-installer scripts tests
python3 -m unittest discover -s tests/unit -v
yamllint -c .yamllint.yml autoinstall payload/opt/gladys/compose.yaml .github
docker compose -f payload/opt/gladys/compose.yaml config --quiet
shellcheck scripts/*.sh tests/integration/*.sh
shfmt -d scripts/*.sh tests/integration/*.sh
```

End to end QEMU tests install the ISO under UEFI/OVMF, verify a real first boot, and exercise offline and power loss recovery. CI never logs in with a password: finished disks are inspected powered off and read only with libguestfs. See [docs/BUILD.md](docs/BUILD.md).

## Repository layout

```text
autoinstall/        Subiquity Autoinstall documents (production and CI)
payload/            Files installed into the target system: systemd units,
                    first boot bootstrap, setup web page, Compose contract
scripts/            Build, authentication, validation, and inspection tooling
tests/unit/         Python unit tests
tests/integration/  QEMU based installation and recovery tests
docs/               Project documentation
.github/workflows/  CI, weekly integration, and release pipelines
```

## Documentation

| Document | Contents |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Build pipeline, installed system, first boot state machine, trust boundaries |
| [docs/BUILD.md](docs/BUILD.md) | Host setup, build profiles, inspection, unit and QEMU testing |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | First boot walkthrough, diagnostics, updates, troubleshooting |
| [docs/RELEASE.md](docs/RELEASE.md) | Release gates, published artifacts, user verification |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, supply chain controls, hardening, reporting |
| [docs/HARDWARE-TEST-CHECKLIST.md](docs/HARDWARE-TEST-CHECKLIST.md) | Physical validation required before any hardware support claim |

## Versioning

The installer follows semantic versioning in [VERSION](VERSION) and is released as explicit `vX.Y.Z` tags. Gladys has its own independent moving `v5` channel: ordinary Gladys updates arrive through Watchtower and never require a new installer release. Rebuilding the installer only updates installer logic or refreshes the authenticated Ubuntu base.

# Build and test guide

## Supported build host

Use an amd64 Ubuntu/Linux system or WSL2. Native ISO and QEMU commands are not supported from PowerShell alone. Keep at least 30 GB free for the authenticated base ISO, the remastered media, multiple qcow2 disks, and the Subiquity sources.

Install the host tools (package availability can vary slightly between Ubuntu releases):

```bash
sudo apt-get update
sudo apt-get install --yes \
  curl git gnupg grub-common make python3 python3-venv \
  shellcheck shfmt squashfs-tools xorriso yamllint \
  docker.io docker-compose-v2
python3 -m venv --system-site-packages .venv
. .venv/bin/activate
pip install -r requirements-build.txt
validator="$(bash scripts/fetch-subiquity-validator.sh)"
make -C "$validator" install_deps
```

The Subiquity fetch is pinned to commit `9b41f1418858e38f88ba2724f540389b3fa41a0a`. Validation aborts if the checkout resolves anywhere else.

## Build profiles

```bash
bash scripts/build-iso.sh                        # production
bash scripts/build-iso.sh --profile ci-smoke     # automatic disk, tiny test app
bash scripts/build-iso.sh --profile ci-real      # automatic disk, production payload
```

| Profile | Storage | Payload | Purpose |
| --- | --- | --- | --- |
| `production` | Interactive selection with explicit erase confirmation | Production payload with the Gladys `v5` Compose contract | Release media |
| `ci-smoke` | Automatic disposable disk | Production payload plus test fixtures and a pinned lightweight HTTP container in place of Gladys | Fast pipeline and recovery tests |
| `ci-real` | Automatic disposable disk | The exact production payload and Compose contract | Real Gladys end to end validation |

Production output is `dist/gladys-assistant-installer-vX.Y.Z-amd64.iso`, its `.iso.sha256`, and `dist/build-info.json`. CI media are deliberately named with their profile and are never release assets.

Every build performs fresh signed-metadata authentication. A cached base ISO is accepted only after its SHA256 matches the checksum selected from the freshly verified `SHA256SUMS`. Build sources are never edited in place: the version file and any CI fixtures enter a temporary payload overlay.

## Inspecting a finished image

To inspect an existing custom image against its authenticated base:

```bash
bash scripts/fetch-ubuntu-base.sh
base="$(python3 -c 'import json; print(json.load(open("build/ubuntu-source.json"))["path"])')"
bash scripts/inspect-iso.sh --iso dist/gladys-assistant-installer-vX.Y.Z-amd64.iso \
  --base-iso "$base" --profile production
```

Inspection re-extracts the image, verifies the embedded payload hashes and build profile, checks the GRUB structure and repaired media checksum, compares the signed boot chain and El Torito metadata against the base ISO, and rejects production payload contamination such as test fixtures or container image archives.

## Static and unit tests

Run the fast checks listed in the [README](../README.md). Unit tests require no root, Docker daemon, ISO, or network. They cover atomic persistence, state validation, backoff, DNS/TLS detection, subprocess errors, Docker arguments, readiness, completion, error classification, IP discovery, exact HTTP paths, console rendering, profile isolation, signed-input parsing, source catalogs, GRUB ambiguity, checksum repair, payload contamination, QEMU contracts, and workflow security.

The production and CI Autoinstall documents are compared after removing `interactive-sections`; any unrelated drift fails validation. The actual ISO schema and the pinned Canonical validator act as additional build gates.

## QEMU integration tests

Install `ovmf`, `qemu-system-x86`, `qemu-utils`, `libguestfs-tools`, and `curl`. KVM and `/dev/kvm` are required by CI.

```bash
bash tests/integration/qemu-install.sh \
  --iso dist/gladys-assistant-installer-ci-smoke-vX.Y.Z-amd64.iso \
  --work-dir build/qemu-smoke
bash tests/integration/qemu-firstboot.sh \
  --disk build/qemu-smoke/system.qcow2 \
  --vars build/qemu-smoke/OVMF_VARS.fd \
  --work-dir build/qemu-smoke-firstboot \
  --profile ci-smoke
```

Two additional scripts exercise the failure paths that matter for an unattended appliance:

- `qemu-network-recovery.sh` boots an installed smoke disk with restricted slirp networking, observes the `WAIT_NETWORK` phase, powers off, and resumes the same disk online. Provisioning must finish without any reinstallation.
- `qemu-power-loss.sh` supplies a QEMU firmware test signal, waits until the `ci-smoke` barrier package is inside its blocking `postinst`, and kills QEMU. Read-only inspection proves the package is unpacked or half configured before reboot; the same disk then resumes without the signal, repairs dpkg, and must end with exactly one installed barrier package, one application container, one Watchtower container, one data directory, and one completion marker.

The scheduled and release jobs additionally install the `ci-real` profile, wait for a real Gladys HTTP handoff, and inspect the stopped disk for Gladys, Watchtower, completion metadata, locked accounts, and the absence of SSH. Tests never log in with a password: disks are inspected powered off, read only, with libguestfs.

## Continuous integration

Three workflows run in GitHub Actions:

| Workflow | Trigger | Jobs |
| --- | --- | --- |
| `ci.yml` | Push and pull request | lint, unit tests, config validation, authenticated builds, finished ISO inspection, QEMU install, QEMU first boot |
| `nightly.yml` | Weekly schedule | Full ecosystem integration against current Canonical metadata and the live Gladys channel |
| `release.yml` | `vX.Y.Z` tag push | Quality gates, build and inspection, real Gladys first boot, network recovery, power loss recovery, then publish |

A successful weekly run never publishes media automatically; publishing is an explicit tag operation described in [RELEASE.md](RELEASE.md).

# Security model

## Threat model

The primary threats this project defends against are:

- a substituted or tampered Ubuntu image;
- corrupted or ambiguous remaster input;
- an unverified target payload;
- accidental unattended disk erasure;
- embedded credentials or container layers inside the ISO;
- a remotely executable bootstrap compromise;
- first boot secret exposure;
- a release workflow with excessive authority.

The project does not defend against a malicious firmware or UEFI implementation, physical access after installation, compromise of Canonical's signing key, compromise of a published container image behind its trusted registry channel, or vulnerabilities inside Ubuntu, Docker, Gladys, or Watchtower.

Gladys intentionally runs privileged, with host networking, device access, and the Docker socket, because that is part of its hardware integration contract. A Gladys compromise must therefore be treated as a host compromise. This boundary is deliberate and documented rather than mitigated.

## Supply chain

### Ubuntu acquisition

Acquiring the base ISO requires all of the following, and HTTPS alone is never accepted:

- the Canonical archive keyring contains fingerprint `843938DF228D22F7B3742BC0D94AA3F0EFE21092` exactly once;
- a valid `gpgv` signature from that key over `SHA256SUMS`;
- a unique newest point release among strict 26.04 amd64 live-server entries, where the initial 26.04 image counts as point release zero when Canonical retains it beside newer media;
- ISO bytes that match the selected checksum.

Zero candidates, malformed candidate lines, and duplicate entries at the newest point release are all fatal. Cache hits repeat the signature discovery and the ISO hash check.

### Remaster inputs and outputs

The source catalog and Autoinstall schema are extracted from that verified ISO, and Canonical's Subiquity validator is checked out at a full commit SHA. The payload is a deterministic, numeric-root archive with a separate SHA256 that Autoinstall late commands verify before extraction. Finished ISO inspection rechecks the payload, profile, GRUB structure, media checksum, signed boot chain files, and base-equivalent El Torito metadata.

Production scanning rejects test-mode fixtures, Docker and containerd storage, image archives, layer tarballs, Debian archives, and any Gladys tag other than `v5`. Gladys itself is pulled on first boot; the ISO contains neither Gladys nor a Docker cache.

### Release workflow

GitHub Actions defaults to `contents: read`. External actions are pinned to immutable full commit SHAs and Dependabot maintains those pins. Only the final publishing job receives `contents: write`, `id-token: write`, and `attestations: write`, and only after every quality, build, inspection, real Gladys, and recovery dependency has succeeded.

## Installed system controls

- Production requires the user to select and confirm storage. Only disposable CI media select storage automatically.
- Root is locked and no known account or password exists: the only account is the one the operator creates interactively on the identity screen. The OpenSSH server is off by default and installed only when the operator opts in on the SSH screen; unattended CI media remain accountless and without SSH.
- TLS certificate verification remains enabled everywhere. Internet readiness combines DNS and verified TLS rather than ICMP alone.
- There is no `curl | sh`, no unsigned remote code, no telemetry, no remote asset, no public debug endpoint, no directory listing, no Docker TCP socket, and no arbitrary web filesystem path.
- The setup server runs as a systemd dynamic user with strict sandboxing (`ProtectSystem=strict`, private devices and tmp, memory and task limits) and holds only `CAP_NET_BIND_SERVICE`. Its state response is schema-limited; error details stay in journald.
- Atomic, synced state and completion writes plus an exclusive bootstrap lock prevent corruption and duplicate work after power loss.
- Package recovery retries a complete `dpkg --configure -a`, `apt-get -f install`, `dpkg --configure -a` cycle. A CI-only package blocks inside `postinst` for the forced power loss test; it is generated only in the temporary `ci-smoke` overlay, and production inspection rejects Debian archives outright.
- Unattended upgrades are restricted to security updates. Automatic reboot and distribution upgrades are disabled.
- `gladys-diagnostics` uses an allowlist of safe commands and never dumps the Gladys database, container inspection output, environment variables, tokens, or passwords.

## Secure Boot

Secure Boot is expected to remain compatible because the Ubuntu signed boot chain is preserved unmodified, but this requires validation on real hardware. The project does not claim Secure Boot certification. A release must not state hardware or Secure Boot support until the physical checklist in [HARDWARE-TEST-CHECKLIST.md](HARDWARE-TEST-CHECKLIST.md) has been completed and recorded.

## Reporting a vulnerability

Do not include secrets, databases, or full Docker inspection output in an issue. Attach the output of `sudo gladys-diagnostics` and only the relevant, redacted `journalctl` lines.

Until a public security contact is configured for the repository, disclose suspected vulnerabilities privately to the repository owner rather than opening an issue containing exploit or secret material.

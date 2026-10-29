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

GitHub Actions defaults to `contents: read`. External actions are pinned to immutable full commit SHAs and Dependabot maintains those pins. Only the final publishing job receives `contents: write`, `id-token: write`, and `attestations: write`, and only after every quality, build, inspection, real Gladys, and recovery dependency has succeeded. The Cloudflare R2 credentials are exposed only to the upload steps of the release and test image publishing jobs, which run after the same gates.

## Installed system controls

- Production never erases a disk before the user confirms Subiquity's destructive-action dialog. It selects the disk automatically only when exactly one internal disk exists (USB, removable, hot-plugged, read-only, virtual, mounted, and small disks never count), and otherwise asks the user to pick it on the storage screen. Only disposable CI media install without any confirmation.
- At the end of first boot, the administrator accounts (ordinary members of the `sudo` group, that is the operator's account) join the `docker` group so that `docker` works without `sudo` or a password. Docker group membership is root-equivalent, so anyone controlling that account, for example through a stolen SSH key or an unlocked console, gains root without knowing its password. This is an accepted risk for a single-operator appliance, in line with the privileged Gladys container.
- Root is locked and no known account or password exists: the only account is the one the operator creates interactively on the identity screen. Unattended CI media use a throwaway account instead; they are never published, and production validation and ISO inspection reject any embedded identity.
- The OpenSSH server is selected by default so that the appliance can be maintained without a screen. As on a standard Ubuntu Server, it accepts the operator's password unless keys are imported on the SSH screen. Password login over the local network is an accepted risk; operators who do not want it can uncheck the server or import keys.
- First boot masks `motd-news.timer`, so the appliance no longer fetches Ubuntu's login news from `motd.ubuntu.com` twice a day.
- First boot sends one HTTPS request to `geoip.ubuntu.com`, the service the Ubuntu installer already queries, to detect the time zone. It carries no identifier beyond the public IP address the request comes from, and its answer is only accepted when it names a zone known to `timedatectl`.
- TLS certificate verification remains enabled everywhere. Internet readiness combines DNS and verified TLS rather than ICMP alone.
- There is no `curl | sh`, no unsigned remote code, no telemetry, no remote asset, no public debug endpoint, no directory listing, no Docker TCP socket, and no arbitrary web filesystem path.
- The setup server runs as a systemd dynamic user with strict sandboxing (`ProtectSystem=strict`, private devices and tmp, memory and task limits) and holds only `CAP_NET_BIND_SERVICE`. Its state response is schema-limited; error details stay in journald.
- Atomic, synced state and completion writes plus an exclusive bootstrap lock prevent corruption and duplicate work after power loss.
- Package recovery retries a complete `dpkg --configure -a`, `apt-get -f install`, `dpkg --configure -a` cycle. A CI-only package blocks inside `postinst` for the forced power loss test; it is generated only in the temporary `ci-smoke` overlay, and production inspection rejects Debian archives outright.
- Unattended upgrades are restricted to security updates. Automatic reboot and distribution upgrades are disabled.
- Watchtower only updates containers explicitly labelled for it, which in the Compose contract are Gladys and Watchtower itself. Docker logs are size-capped for every container.
- `gladys-diagnostics` uses an allowlist of safe commands and never dumps the Gladys database, container inspection output, environment variables, tokens, or passwords.

## Secure Boot

Secure Boot is expected to remain compatible because the Ubuntu signed boot chain is preserved unmodified, but this requires validation on real hardware. The project does not claim Secure Boot certification. A release must not state hardware or Secure Boot support until the physical checklist in [HARDWARE-TEST-CHECKLIST.md](HARDWARE-TEST-CHECKLIST.md) has been completed and recorded.

## Reporting a vulnerability

Do not include secrets, databases, or full Docker inspection output in an issue. Attach the output of `sudo gladys-diagnostics` and only the relevant, redacted `journalctl` lines.

Until a public security contact is configured for the repository, disclose suspected vulnerabilities privately to the repository owner rather than opening an issue containing exploit or secret material.

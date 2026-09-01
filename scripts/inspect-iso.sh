#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ISO=""
BASE_ISO=""
PROFILE="production"

usage() {
	printf 'Usage: %s --iso FILE --base-iso FILE [--profile production|ci-smoke|ci-real]\n' \
		"${0##*/}"
}

while (($#)); do
	case "$1" in
	--iso)
		ISO="$2"
		shift 2
		;;
	--base-iso)
		BASE_ISO="$2"
		shift 2
		;;
	--profile)
		PROFILE="$2"
		shift 2
		;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		printf 'Unknown argument: %s\n' "$1" >&2
		exit 64
		;;
	esac
done
case "$PROFILE" in
production | ci-smoke | ci-real) ;;
*)
	printf 'Invalid inspection profile: %s\n' "$PROFILE" >&2
	exit 64
	;;
esac
[[ -f "$ISO" && -f "$BASE_ISO" ]] || {
	printf 'Both the custom ISO and authenticated base ISO are required\n' >&2
	exit 66
}
for command in cmp grub-script-check md5sum python3 sha256sum xorriso; do
	command -v "$command" >/dev/null || {
		printf 'Required command not found: %s\n' "$command" >&2
		exit 69
	}
done

temporary="$(mktemp -d)"
cleanup() {
	rm -rf -- "$temporary"
}
trap cleanup EXIT
mkdir -p -- "$temporary/custom/gladys" "$temporary/custom/boot/grub" \
	"$temporary/custom/EFI/boot" "$temporary/custom/casper" \
	"$temporary/base/boot/grub" "$temporary/base/EFI/boot" \
	"$temporary/base/casper"

xorriso -osirrox on -indev "$ISO" \
	-extract /autoinstall.yaml "$temporary/custom/autoinstall.yaml" \
	-extract /gladys/gladys-payload.tar "$temporary/custom/gladys/gladys-payload.tar" \
	-extract /gladys/gladys-payload.tar.sha256 "$temporary/custom/gladys/gladys-payload.tar.sha256" \
	-extract /gladys/build-profile.json "$temporary/custom/gladys/build-profile.json" \
	-extract /boot/grub/grub.cfg "$temporary/custom/boot/grub/grub.cfg" \
	-extract /md5sum.txt "$temporary/custom/md5sum.txt" \
	-extract /EFI/boot/bootx64.efi "$temporary/custom/EFI/boot/bootx64.efi" \
	-extract /EFI/boot/grubx64.efi "$temporary/custom/EFI/boot/grubx64.efi" \
	-extract /EFI/boot/mmx64.efi "$temporary/custom/EFI/boot/mmx64.efi" \
	-extract /casper/vmlinuz "$temporary/custom/casper/vmlinuz" \
	-extract /casper/initrd "$temporary/custom/casper/initrd"
xorriso -osirrox on -indev "$BASE_ISO" \
	-extract /boot/grub/grub.cfg "$temporary/base/boot/grub/grub.cfg" \
	-extract /EFI/boot/bootx64.efi "$temporary/base/EFI/boot/bootx64.efi" \
	-extract /EFI/boot/grubx64.efi "$temporary/base/EFI/boot/grubx64.efi" \
	-extract /EFI/boot/mmx64.efi "$temporary/base/EFI/boot/mmx64.efi" \
	-extract /casper/vmlinuz "$temporary/base/casper/vmlinuz" \
	-extract /casper/initrd "$temporary/base/casper/initrd"

for path in EFI/boot/bootx64.efi EFI/boot/grubx64.efi EFI/boot/mmx64.efi casper/vmlinuz casper/initrd; do
	cmp --silent "$temporary/base/$path" "$temporary/custom/$path" || {
		printf 'Signed boot-chain file changed: /%s\n' "$path" >&2
		exit 65
	}
done

(
	cd -- "$temporary/custom/gladys"
	sha256sum -c --status gladys-payload.tar.sha256
)
python3 "$SCRIPT_DIR/inspect-payload.py" \
	--archive "$temporary/custom/gladys/gladys-payload.tar" --profile "$PROFILE"
python3 "$SCRIPT_DIR/patch-grub.py" --check \
	--base "$temporary/base/boot/grub/grub.cfg" \
	"$temporary/custom/boot/grub/grub.cfg"

mapfile -t grub_md5_entries < <(
	awk '$2 == "./boot/grub/grub.cfg" || $2 == "boot/grub/grub.cfg" { print $1 }' \
		"$temporary/custom/md5sum.txt"
)
if ((${#grub_md5_entries[@]} > 1)); then
	printf 'Duplicate GRUB entries in md5sum.txt\n' >&2
	exit 65
fi
if ((${#grub_md5_entries[@]} == 1)); then
	actual_grub_md5="$(md5sum "$temporary/custom/boot/grub/grub.cfg" | awk '{print $1}')"
	[[ "${grub_md5_entries[0],,}" == "$actual_grub_md5" ]] || {
		printf 'GRUB media-integrity checksum is stale\n' >&2
		exit 65
	}
fi

python3 - "$temporary/custom/autoinstall.yaml" \
	"$temporary/custom/gladys/build-profile.json" "$PROFILE" \
	"$temporary/custom/gladys/gladys-payload.tar" <<'PY'
import hashlib
import json
import sys

import yaml

autoinstall_path, profile_path, expected_profile, payload_path = sys.argv[1:]
with open(autoinstall_path, encoding="utf-8") as handle:
    document = yaml.safe_load(handle)
if not isinstance(document, dict) or set(document) != {"autoinstall"}:
    raise SystemExit("finished ISO contains an invalid Autoinstall document")
config = document["autoinstall"]
expected_interactive = (
    ["locale", "keyboard", "storage", "identity", "ssh"]
    if expected_profile == "production"
    else []
)
if config.get("interactive-sections") != expected_interactive:
    raise SystemExit("finished ISO contains the wrong Autoinstall profile")
with open(profile_path, encoding="utf-8") as handle:
    metadata = json.load(handle)
if metadata.get("schema") != 1 or metadata.get("profile") != expected_profile:
    raise SystemExit("finished ISO build-profile metadata does not match")
with open(autoinstall_path, "rb") as handle:
    autoinstall_hash = hashlib.file_digest(handle, "sha256").hexdigest()
with open(payload_path, "rb") as handle:
    payload_hash = hashlib.file_digest(handle, "sha256").hexdigest()
if metadata.get("autoinstall_sha256") != autoinstall_hash:
    raise SystemExit("finished ISO Autoinstall hash does not match profile metadata")
if metadata.get("payload_sha256") != payload_hash:
    raise SystemExit("finished ISO payload hash does not match profile metadata")
PY

xorriso -indev "$BASE_ISO" -report_el_torito plain 2>&1 |
	awk '/^El Torito / { print }' >"$temporary/base-eltorito.txt"
xorriso -indev "$ISO" -report_el_torito plain 2>&1 |
	awk '/^El Torito / { print }' >"$temporary/custom-eltorito.txt"
python3 "$SCRIPT_DIR/compare-boot-metadata.py" \
	"$temporary/base-eltorito.txt" "$temporary/custom-eltorito.txt" \
	--base-iso "$BASE_ISO" --custom-iso "$ISO"

printf 'ISO inspection passed for %s profile: %s\n' "$PROFILE" "$ISO"

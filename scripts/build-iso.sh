#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
PROFILE="production"
DIST_DIR="$ROOT_DIR/dist"
CACHE_DIR="$ROOT_DIR/build/cache"

usage() {
	printf 'Usage: %s [--profile production|ci-smoke|ci-real] [--dist-dir DIR] [--cache-dir DIR]\n' \
		"${0##*/}"
}

while (($#)); do
	case "$1" in
	--profile)
		PROFILE="$2"
		shift 2
		;;
	--dist-dir)
		DIST_DIR="$2"
		shift 2
		;;
	--cache-dir)
		CACHE_DIR="$2"
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
	printf 'Invalid build profile: %s\n' "$PROFILE" >&2
	exit 64
	;;
esac
case "$(uname -m)" in
x86_64 | amd64) ;;
*)
	printf 'ISO builds are supported only on amd64/x86-64 hosts\n' >&2
	exit 69
	;;
esac
for command in grub-script-check md5sum python3 sha256sum xorriso; do
	command -v "$command" >/dev/null || {
		printf 'Required command not found: %s\n' "$command" >&2
		exit 69
	}
done

version="$(tr -d '\r\n' <"$ROOT_DIR/VERSION")"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
	printf 'VERSION is not a semantic x.y.z version\n' >&2
	exit 65
}
if [[ "$PROFILE" == "production" ]]; then
	autoinstall="$ROOT_DIR/autoinstall/autoinstall.yaml"
	output_name="gladys-assistant-installer-v${version}-amd64.iso"
	build_info_name="build-info.json"
else
	autoinstall="$ROOT_DIR/autoinstall/autoinstall-ci.yaml"
	output_name="gladys-assistant-installer-${PROFILE}-v${version}-amd64.iso"
	build_info_name="build-info-${PROFILE}.json"
fi

mkdir -p -- "$CACHE_DIR" "$DIST_DIR" "$ROOT_DIR/build"
temporary="$(mktemp -d "$ROOT_DIR/build/iso-${PROFILE}.XXXXXXXX")"
cleanup() {
	rm -rf -- "$temporary"
}
trap cleanup EXIT

source_metadata="$temporary/ubuntu-source.json"
bash "$SCRIPT_DIR/fetch-ubuntu-base.sh" \
	--cache-dir "$CACHE_DIR" --metadata-out "$source_metadata"
base_iso="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["path"])' "$source_metadata")"
ubuntu_filename="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["filename"])' "$source_metadata")"
ubuntu_sha256="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["sha256"])' "$source_metadata")"

bash "$SCRIPT_DIR/validate-autoinstall.sh" --base-iso "$base_iso"

payload_dir="$temporary/payload"
if git -c safe.directory="$ROOT_DIR" -C "$ROOT_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
	source_date_epoch="$(git -c safe.directory="$ROOT_DIR" -C "$ROOT_DIR" log -1 --format=%ct)"
	git_commit="$(git -c safe.directory="$ROOT_DIR" -C "$ROOT_DIR" rev-parse HEAD)"
else
	source_date_epoch=0
	git_commit=uncommitted
fi
bash "$SCRIPT_DIR/make-payload.sh" --profile "$PROFILE" \
	--output-dir "$payload_dir" --source-date-epoch "$source_date_epoch"
python3 "$SCRIPT_DIR/inspect-payload.py" \
	--archive "$payload_dir/gladys-payload.tar" --profile "$PROFILE"

xorriso -osirrox on -indev "$base_iso" \
	-extract /boot/grub/grub.cfg "$temporary/grub.cfg" \
	-extract /md5sum.txt "$temporary/md5sum.txt"
python3 "$SCRIPT_DIR/patch-grub.py" "$temporary/grub.cfg" "$temporary/grub.patched.cfg"
grub_md5="$(md5sum "$temporary/grub.patched.cfg" | awk '{print $1}')"
python3 "$SCRIPT_DIR/update-md5.py" \
	"$temporary/md5sum.txt" "$temporary/md5sum.patched.txt" \
	--path boot/grub/grub.cfg --checksum "$grub_md5"

payload_sha256="$(awk '{print $1}' "$payload_dir/gladys-payload.tar.sha256")"
autoinstall_sha256="$(sha256sum "$autoinstall" | awk '{print $1}')"
printf '{"autoinstall_sha256":"%s","payload_sha256":"%s","profile":"%s","schema":1}\n' \
	"$autoinstall_sha256" "$payload_sha256" "$PROFILE" >"$temporary/build-profile.json"

output_temporary="$temporary/$output_name"
xorriso -indev "$base_iso" -outdev "$output_temporary" \
	-mkdir /gladys -- \
	-map "$autoinstall" /autoinstall.yaml \
	-map "$payload_dir/gladys-payload.tar" /gladys/gladys-payload.tar \
	-map "$payload_dir/gladys-payload.tar.sha256" /gladys/gladys-payload.tar.sha256 \
	-map "$temporary/build-profile.json" /gladys/build-profile.json \
	-map "$temporary/grub.patched.cfg" /boot/grub/grub.cfg \
	-map "$temporary/md5sum.patched.txt" /md5sum.txt \
	-boot_image any replay

bash "$SCRIPT_DIR/inspect-iso.sh" \
	--iso "$output_temporary" --base-iso "$base_iso" --profile "$PROFILE"

output="$DIST_DIR/$output_name"
mv -- "$output_temporary" "$output"
(
	cd -- "$DIST_DIR"
	sha256sum "$output_name" >"$output_name.sha256"
)
timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 "$SCRIPT_DIR/generate-build-info.py" \
	--installer-version "$version" \
	--git-commit "$git_commit" \
	--ubuntu-iso-filename "$ubuntu_filename" \
	--ubuntu-iso-sha256 "$ubuntu_sha256" \
	--timestamp "$timestamp" \
	--output "$DIST_DIR/$build_info_name"

printf '%s\n' "$output"

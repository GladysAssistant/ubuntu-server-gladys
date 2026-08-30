#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

readonly SUBIQUITY_COMMIT="9b41f1418858e38f88ba2724f540389b3fa41a0a"
readonly INSTALLER_SQUASHFS="/casper/ubuntu-server-minimal.ubuntu-server.installer.squashfs"
readonly MINIMAL_SQUASHFS="/casper/ubuntu-server-minimal.squashfs"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
BASE_ISO=""
SUBIQUITY_DIR="$ROOT_DIR/build/cache/subiquity-$SUBIQUITY_COMMIT"

usage() {
	printf 'Usage: %s --base-iso FILE [--subiquity-dir DIR]\n' "${0##*/}"
}

while (($#)); do
	case "$1" in
	--base-iso)
		BASE_ISO="$2"
		shift 2
		;;
	--subiquity-dir)
		SUBIQUITY_DIR="$2"
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

[[ -n "$BASE_ISO" && -f "$BASE_ISO" ]] || {
	printf 'A verified Ubuntu base ISO is required\n' >&2
	exit 66
}
for command in awk find git python3 tar unsquashfs xorriso yamllint; do
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

xorriso -osirrox on -indev "$BASE_ISO" \
	-extract /.disk/info "$temporary/iso-info" \
	-extract /casper/install-sources.yaml "$temporary/install-sources.yaml" \
	-extract "$INSTALLER_SQUASHFS" "$temporary/installer.squashfs"
python3 "$SCRIPT_DIR/buildlib.py" iso-info "$temporary/iso-info"

unsquashfs -ll "$temporary/installer.squashfs" >"$temporary/installer-members.txt"
mapfile -t subiquity_snap_members < <(
	awk '$NF ~ /^squashfs-root\/var\/lib\/snapd\/seed\/snaps\/subiquity_[0-9]+\.snap$/ {
		sub(/^squashfs-root\//, "", $NF); print $NF
	}' "$temporary/installer-members.txt"
)
if ((${#subiquity_snap_members[@]} != 1)); then
	printf 'Expected exactly one seeded Subiquity snap in verified installer layer; found %d\n' \
		"${#subiquity_snap_members[@]}" >&2
	exit 65
fi
mapfile -t installer_core_snap_members < <(
	awk '$NF ~ /^squashfs-root\/var\/lib\/snapd\/seed\/snaps\/core24_[0-9]+\.snap$/ {
		sub(/^squashfs-root\//, "", $NF); print $NF
	}' "$temporary/installer-members.txt"
)

subiquity_snap_image="$temporary/installer.squashfs"
subiquity_snap_member="${subiquity_snap_members[0]}"
if ((${#installer_core_snap_members[@]} == 1)); then
	# Ubuntu 26.04 GA carries core24 in the installer delta.
	core_snap_image="$temporary/installer.squashfs"
	core_snap_member="${installer_core_snap_members[0]}"
elif ((${#installer_core_snap_members[@]} == 0)); then
	# Ubuntu 26.04.1 inherits core24 from the minimal base. Copy this large
	# layer only when the installer delta does not carry the runtime itself.
	xorriso -osirrox on -indev "$BASE_ISO" \
		-extract "$MINIMAL_SQUASHFS" "$temporary/minimal.squashfs"
	unsquashfs -ll "$temporary/minimal.squashfs" >"$temporary/minimal-members.txt"
	mapfile -t minimal_core_snap_members < <(
		awk '$NF ~ /^squashfs-root\/var\/lib\/snapd\/seed\/snaps\/core24_[0-9]+\.snap$/ {
			sub(/^squashfs-root\//, "", $NF); print $NF
		}' "$temporary/minimal-members.txt"
	)
	if ((${#minimal_core_snap_members[@]} != 1)); then
		printf 'Expected exactly one seeded core24 snap in verified minimal layer; found %d\n' \
			"${#minimal_core_snap_members[@]}" >&2
		exit 65
	fi
	core_snap_image="$temporary/minimal.squashfs"
	core_snap_member="${minimal_core_snap_members[0]}"
else
	printf 'Expected at most one seeded core24 snap in verified installer layer; found %d\n' \
		"${#installer_core_snap_members[@]}" >&2
	exit 65
fi

unsquashfs -cat "$subiquity_snap_image" "$subiquity_snap_member" \
	>"$temporary/subiquity.snap"
unsquashfs -cat "$core_snap_image" "$core_snap_member" \
	>"$temporary/core24.snap"
unsquashfs -no-progress -no-xattrs -d "$temporary/subiquity-snap" \
	"$temporary/subiquity.snap" >/dev/null

mapfile -t snap_pythons < <(
	find "$temporary/subiquity-snap/usr/bin" -maxdepth 1 -type f -name 'python3.*' -print |
		awk '/\/python3\.[0-9]+$/'
)
if ((${#snap_pythons[@]} != 1)); then
	printf 'Expected exactly one Python runtime in verified Subiquity snap; found %d\n' \
		"${#snap_pythons[@]}" >&2
	exit 65
fi
snap_python="${snap_pythons[0]}"
python_name="${snap_python##*/}"

# Core base snaps contain device nodes and privileged xattrs that an
# unprivileged CI runner must not recreate. Extract only the runtime trees
# needed to execute the authenticated Subiquity Python and ignore xattrs.
unsquashfs -no-progress -no-xattrs -d "$temporary/core24-snap" \
	"$temporary/core24.snap" \
	"usr/lib/$python_name" \
	usr/lib/python3/dist-packages \
	usr/lib/x86_64-linux-gnu >/dev/null

# Canonical's dry-run schema command retains this legacy fixture path, but the
# release snap omits examples. Recreate only the authenticated ISO series in
# the temporary extraction so schema defaults are evaluated as Ubuntu 26.04.
mkdir -p -- "$temporary/subiquity-snap/examples"
printf '%s\n' \
	'DISTRIB_ID=Ubuntu' \
	'DISTRIB_RELEASE=26.04' \
	'DISTRIB_CODENAME=resolute' \
	'DISTRIB_DESCRIPTION="Ubuntu 26.04 LTS"' \
	>"$temporary/subiquity-snap/examples/lsb-release-focal"

snap_site_packages="$temporary/subiquity-snap/lib/$python_name/site-packages"
snap_dist_packages="$temporary/subiquity-snap/usr/lib/python3/dist-packages"
core_python_home="$temporary/core24-snap/usr"
dynamic_loader="$temporary/core24-snap/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
for directory in "$snap_site_packages" "$snap_dist_packages" \
	"$core_python_home/lib/$python_name" \
	"$core_python_home/lib/python3/dist-packages" \
	"$core_python_home/lib/x86_64-linux-gnu"; do
	[[ -d "$directory" ]] || {
		printf 'Verified installer Python directory is missing: %s\n' "$directory" >&2
		exit 65
	}
done
[[ -x "$dynamic_loader" ]] || {
	printf 'Verified core24 dynamic loader is missing or not executable\n' >&2
	exit 65
}
snap_library_path="$temporary/core24-snap/usr/lib/x86_64-linux-gnu:$temporary/subiquity-snap/usr/lib/x86_64-linux-gnu:$temporary/subiquity-snap/lib/x86_64-linux-gnu"
(
	cd "$temporary/subiquity-snap"
	SNAP="$temporary/subiquity-snap" \
		PYTHONHOME="$core_python_home" \
		PYTHONPATH="$snap_site_packages:$snap_dist_packages" \
		"$dynamic_loader" --library-path "$snap_library_path" \
		"$snap_python" -m subiquity.cmd.schema
) >"$temporary/autoinstall-schema.json"
python3 -c 'import json, sys; json.load(open(sys.argv[1], encoding="utf-8"))' \
	"$temporary/autoinstall-schema.json"

yamllint -c "$ROOT_DIR/.yamllint.yml" \
	"$ROOT_DIR/autoinstall/autoinstall.yaml" \
	"$ROOT_DIR/autoinstall/autoinstall-ci.yaml"
python3 "$SCRIPT_DIR/validate-config.py" \
	--production "$ROOT_DIR/autoinstall/autoinstall.yaml" \
	--ci "$ROOT_DIR/autoinstall/autoinstall-ci.yaml" \
	--schema "$temporary/autoinstall-schema.json" \
	--catalog "$temporary/install-sources.yaml" \
	--canonical-output-dir "$temporary/canonical-validator"

bash "$SCRIPT_DIR/fetch-subiquity-validator.sh" --output-dir "$SUBIQUITY_DIR"
actual_commit="$(git -C "$SUBIQUITY_DIR" rev-parse HEAD)"
[[ "$actual_commit" == "$SUBIQUITY_COMMIT" ]] || {
	printf 'Subiquity validator commit mismatch: %s\n' "$actual_commit" >&2
	exit 65
}

# Execute a pristine export of the pinned commit rather than its cache working
# tree. This excludes uncommitted cache changes and avoids CRLF shebangs
# when the repository is built through WSL on a Windows-mounted filesystem.
validator_runtime="$temporary/subiquity-validator"
mkdir -p -- "$validator_runtime"
git -C "$SUBIQUITY_DIR" archive --format=tar \
	--output="$temporary/subiquity-validator.tar" "$SUBIQUITY_COMMIT"
tar -xf "$temporary/subiquity-validator.tar" -C "$validator_runtime"
for dependency in curtin probert; do
	expected_dependency_commit="$(
		python3 -c \
			'import sys, yaml; print(yaml.safe_load(open(sys.argv[1], encoding="utf-8"))["parts"][sys.argv[2]]["source-commit"])' \
			"$validator_runtime/snapcraft.yaml" "$dependency"
	)"
	[[ "$expected_dependency_commit" =~ ^[0-9a-f]{40}$ ]] || {
		printf 'Pinned Subiquity %s dependency commit is invalid\n' \
			"$dependency" >&2
		exit 65
	}
	actual_dependency_commit="$(git -C "$SUBIQUITY_DIR/$dependency" rev-parse HEAD)"
	[[ "$actual_dependency_commit" == "$expected_dependency_commit" ]] || {
		printf 'Pinned Subiquity %s dependency mismatch: %s\n' \
			"$dependency" "$actual_dependency_commit" >&2
		exit 65
	}
	mkdir -p -- "$validator_runtime/$dependency"
	git -C "$SUBIQUITY_DIR/$dependency" archive --format=tar \
		--output="$temporary/$dependency.tar" "$expected_dependency_commit"
	tar -xf "$temporary/$dependency.tar" -C "$validator_runtime/$dependency"
done
legacy_cloud_init_validator="$validator_runtime/system_scripts/subiquity-legacy-cloud-init-validate"
[[ -x "$legacy_cloud_init_validator" ]] || {
	printf 'Pinned Subiquity cloud-init validator is missing or not executable\n' >&2
	exit 65
}

# Canonical's controller validator loads checkout resources such as kbds/ and
# examples/ relative to its working directory. It also invokes its companion
# cloud-init validator from the same pristine export through its SNAP path.
(
	cd -- "$validator_runtime"
	for config in autoinstall.yaml autoinstall-ci.yaml; do
		python3 scripts/validate-autoinstall-user-data.py \
			--json-schema "$temporary/autoinstall-schema.json" \
			--no-expect-cloudconfig "$temporary/canonical-validator/$config"
	done
)

printf 'Autoinstall configuration is valid for Ubuntu 26.04 and Subiquity %s\n' \
	"$SUBIQUITY_COMMIT"

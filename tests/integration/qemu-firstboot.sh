#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=tests/integration/qemu-common.sh
source "$SCRIPT_DIR/qemu-common.sh"

DISK=""
VARS=""
WORK_DIR=""
PROFILE="ci-smoke"
HOST_PORT="${HOST_PORT:-18080}"
FIRSTBOOT_TIMEOUT="${FIRSTBOOT_TIMEOUT:-7200}"
FINALIZE_GRACE_SECONDS="${FINALIZE_GRACE_SECONDS:-120}"
while (($#)); do
	case "$1" in
	--disk)
		DISK="$2"
		shift 2
		;;
	--vars)
		VARS="$2"
		shift 2
		;;
	--work-dir)
		WORK_DIR="$2"
		shift 2
		;;
	--profile)
		PROFILE="$2"
		shift 2
		;;
	--host-port)
		HOST_PORT="$2"
		shift 2
		;;
	*)
		printf 'Unknown argument: %s\n' "$1" >&2
		exit 64
		;;
	esac
done
[[ -f "$DISK" && -f "$VARS" && -n "$WORK_DIR" ]] || {
	printf 'Usage: %s --disk FILE --vars FILE --work-dir DIR [--profile PROFILE]\n' "${0##*/}" >&2
	exit 64
}
case "$PROFILE" in ci-smoke | ci-real) ;; *) exit 64 ;; esac
require_qemu_tools
prepare_libguestfs "$WORK_DIR"
find_ovmf
trap stop_vm EXIT
start_vm "$DISK" "$VARS" "$WORK_DIR/firstboot-serial.log" "$HOST_PORT" online
wait_for_application_handoff "$HOST_PORT" "$FIRSTBOOT_TIMEOUT" "$WORK_DIR"
sleep "$FINALIZE_GRACE_SECONDS"
stop_vm
trap - EXIT

assert_completed_disk "$DISK"
assert_no_password_or_ssh "$DISK"
if [[ "$PROFILE" == "ci-smoke" ]]; then
	assert_single_resources "$DISK" gladys-test-app
else
	assert_single_resources "$DISK" gladys
fi
printf 'First-boot verification passed for %s\n' "$PROFILE"

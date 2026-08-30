#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=tests/integration/qemu-common.sh
source "$SCRIPT_DIR/qemu-common.sh"

DISK=""
VARS=""
WORK_DIR=""
HOST_PORT="${HOST_PORT:-18082}"
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
	--host-port)
		HOST_PORT="$2"
		shift 2
		;;
	*) exit 64 ;;
	esac
done
[[ -f "$DISK" && -f "$VARS" && -n "$WORK_DIR" ]] || exit 64
require_qemu_tools
prepare_libguestfs "$WORK_DIR"
find_ovmf
trap stop_vm EXIT
start_vm "$DISK" "$VARS" "$WORK_DIR/interrupted-serial.log" "$HOST_PORT" online dpkg-power-loss
# apt-power-loss-barrier.py reports only while its package postinst is running
# with QEMU's explicit firmware signal present.
wait_for_phase_message \
	"$HOST_PORT" INSTALL_PACKAGES "dpkg postinst active; package configuration pending" 5400
kill -KILL "$QEMU_PID"
set +e
wait "$QEMU_PID"
set -e
QEMU_PID=""
trap - EXIT
assert_dpkg_barrier_state "$DISK" interrupted

bash "$SCRIPT_DIR/qemu-firstboot.sh" \
	--disk "$DISK" --vars "$VARS" --work-dir "$WORK_DIR/recovered" \
	--profile ci-smoke --host-port "$HOST_PORT"
assert_dpkg_barrier_state "$DISK" installed
assert_single_resources "$DISK" gladys-test-app

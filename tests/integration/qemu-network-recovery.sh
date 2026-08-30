#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=tests/integration/qemu-common.sh
source "$SCRIPT_DIR/qemu-common.sh"

DISK=""
VARS=""
WORK_DIR=""
HOST_PORT="${HOST_PORT:-18081}"
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
start_vm "$DISK" "$VARS" "$WORK_DIR/offline-serial.log" "$HOST_PORT" offline # restrict=on
wait_for_phase "$HOST_PORT" WAIT_NETWORK 900
stop_vm
trap - EXIT
virt-cat -a "$DISK" /var/lib/gladys-installer/state.json |
	python3 -c 'import json,sys; assert json.load(sys.stdin)["phase"] == "WAIT_NETWORK"'

bash "$SCRIPT_DIR/qemu-firstboot.sh" \
	--disk "$DISK" --vars "$VARS" --work-dir "$WORK_DIR/network-resumed" \
	--profile ci-smoke --host-port "$HOST_PORT"

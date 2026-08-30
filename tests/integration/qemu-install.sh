#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=tests/integration/qemu-common.sh
source "$SCRIPT_DIR/qemu-common.sh"

ISO=""
WORK_DIR=""
INSTALL_TIMEOUT="${INSTALL_TIMEOUT:-3600}"
while (($#)); do
	case "$1" in
	--iso)
		ISO="$2"
		shift 2
		;;
	--work-dir)
		WORK_DIR="$2"
		shift 2
		;;
	*)
		printf 'Unknown argument: %s\n' "$1" >&2
		exit 64
		;;
	esac
done
[[ -f "$ISO" && -n "$WORK_DIR" ]] || {
	printf 'Usage: %s --iso FILE --work-dir DIR\n' "${0##*/}" >&2
	exit 64
}
require_qemu_tools
find_ovmf
mkdir -p -- "$WORK_DIR"
DISK="$WORK_DIR/system.qcow2"
VARS="$WORK_DIR/OVMF_VARS.fd"
SERIAL_LOG="$WORK_DIR/install-serial.log"
[[ ! -e "$DISK" && ! -e "$VARS" ]] || {
	printf 'Refusing to overwrite an existing QEMU disk or OVMF vars file\n' >&2
	exit 73
}
qemu-img create -f qcow2 "$DISK" 32G
cp -- "$OVMF_VARS_TEMPLATE" "$VARS"

set +e
timeout --signal=TERM --kill-after=30 "$INSTALL_TIMEOUT" \
	qemu-system-x86_64 \
	-name gladys-install \
	-machine q35,accel=kvm \
	-enable-kvm \
	-cpu host \
	-smp "$QEMU_CPUS" \
	-m "$QEMU_MEMORY_MB" \
	-drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE" \
	-drive "if=pflash,format=raw,file=$VARS" \
	-drive "if=virtio,format=qcow2,discard=unmap,file=$DISK" \
	-drive "media=cdrom,readonly=on,format=raw,file=$ISO" \
	-device virtio-net-pci,netdev=net0 \
	-netdev user,id=net0 \
	-boot once=d,menu=off \
	-no-reboot \
	-display none \
	-monitor none \
	-serial "file:$SERIAL_LOG"
status=$?
set -e
if ((status != 0)); then
	printf 'QEMU installer failed or timed out with status %d\n' "$status" >&2
	tail -n 200 "$SERIAL_LOG" >&2
	exit "$status"
fi
printf 'Installed disk: %s\nOVMF vars: %s\n' "$DISK" "$VARS"

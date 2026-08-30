#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

QEMU_COMMON_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
QEMU_MEMORY_MB="${QEMU_MEMORY_MB:-4096}"
QEMU_CPUS="${QEMU_CPUS:-2}"
QEMU_PID=""
OVMF_CODE=""
OVMF_VARS_TEMPLATE=""

require_qemu_tools() {
	local command
	for command in \
		curl flock guestfish libguestfs-make-fixed-appliance libguestfs-test-tool \
		python3 qemu-img qemu-system-x86_64 timeout virt-cat virt-ls; do
		command -v "$command" >/dev/null || {
			printf 'Required QEMU test command not found: %s\n' "$command" >&2
			return 69
		}
	done
}

fixed_libguestfs_appliance_complete() {
	local appliance_dir="$1"
	[[ -s "$appliance_dir/kernel" && -s "$appliance_dir/initrd" &&
		-s "$appliance_dir/root" && -r "$appliance_dir/README.fixed" ]]
}

prepare_libguestfs() {
	local diagnostics_dir="${1:-}"
	local boot_dir="${GLADYS_LIBGUESTFS_BOOT_DIR:-/boot}"
	local modules_root="${GLADYS_LIBGUESTFS_MODULES_ROOT:-/lib/modules}"
	local runtime_parent="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
	local runtime_uid runtime_dir
	local running_version source_kernel="" kernel_version="" candidate
	local candidate_version
	local copied_kernel appliance_dir preflight_log lock_fd staging_parent
	local staging_dir quarantine_dir

	runtime_uid="$(id -u)"
	runtime_dir="${GLADYS_LIBGUESTFS_RUNTIME_DIR:-$runtime_parent/gladys-libguestfs-$runtime_uid}"

	if [[ "${GLADYS_LIBGUESTFS_READY:-0}" == "1" ]]; then
		if [[ -z "${LIBGUESTFS_PATH:-}" ]] ||
			! fixed_libguestfs_appliance_complete "$LIBGUESTFS_PATH"; then
			printf 'The prepared libguestfs appliance is no longer readable\n' >&2
			return 70
		fi
		export LIBGUESTFS_BACKEND=direct
		return 0
	fi
	unset LIBGUESTFS_PATH

	running_version="$(uname -r)"
	if [[ -f "$boot_dir/vmlinuz-$running_version" &&
		-d "$modules_root/$running_version" ]]; then
		source_kernel="$boot_dir/vmlinuz-$running_version"
		kernel_version="$running_version"
	else
		while IFS= read -r candidate; do
			candidate_version="${candidate#vmlinuz-}"
			[[ -d "$modules_root/$candidate_version" ]] || continue
			source_kernel="$boot_dir/$candidate"
			kernel_version="$candidate_version"
		done < <(
			find "$boot_dir" -maxdepth 1 -type f -name 'vmlinuz-*' \
				-printf '%f\n' 2>/dev/null | sort -V
		)
	fi
	[[ -n "$source_kernel" && -n "$kernel_version" ]] || {
		printf 'Unable to find a host kernel with matching modules for libguestfs\n' >&2
		return 69
	}

	mkdir -p -- "$runtime_dir/cache"
	chmod 0700 "$runtime_dir" "$runtime_dir/cache"
	copied_kernel="$runtime_dir/vmlinuz-$kernel_version"
	if [[ -r "$source_kernel" ]]; then
		install -m 0644 -- "$source_kernel" "$copied_kernel"
	else
		command -v sudo >/dev/null || {
			printf 'Host kernel is unreadable and sudo is unavailable: %s\n' \
				"$source_kernel" >&2
			return 77
		}
		sudo -n install -m 0644 --owner="$(id -u)" --group="$(id -g)" -- \
			"$source_kernel" "$copied_kernel"
	fi
	[[ -s "$copied_kernel" && -r "$copied_kernel" ]] || {
		printf 'Failed to create a readable private kernel copy: %s\n' \
			"$copied_kernel" >&2
		return 74
	}

	export SUPERMIN_KERNEL="$copied_kernel"
	export SUPERMIN_MODULES="$modules_root/$kernel_version"
	export SUPERMIN_KERNEL_VERSION="$kernel_version"
	export LIBGUESTFS_BACKEND=direct
	export LIBGUESTFS_CACHEDIR="$runtime_dir/cache"
	appliance_dir="$runtime_dir/appliance-$kernel_version"
	if [[ -n "$diagnostics_dir" ]]; then
		mkdir -p -- "$diagnostics_dir"
		preflight_log="$diagnostics_dir/libguestfs-preflight.log"
	else
		preflight_log="$runtime_dir/libguestfs-preflight.log"
	fi
	{
		printf 'Preparing rootless libguestfs with kernel %s\n' "$kernel_version"
		stat -c 'source kernel: %A %a %U:%G %n' "$source_kernel"
		stat -c 'private kernel: %A %a %U:%G %n' "$copied_kernel"
		df -h "$runtime_dir"
	} >"$preflight_log" 2>&1

	exec {lock_fd}>"$runtime_dir/appliance.lock"
	flock "$lock_fd"
	if fixed_libguestfs_appliance_complete "$appliance_dir"; then
		printf 'Reusing fixed libguestfs appliance %s\n' "$appliance_dir" \
			>>"$preflight_log"
	else
		staging_parent="$(mktemp -d "$runtime_dir/appliance-build.XXXXXX")"
		staging_dir="$staging_parent/fixed"
		if ! libguestfs-make-fixed-appliance "$staging_dir" \
			>>"$preflight_log" 2>&1; then
			flock --unlock "$lock_fd"
			exec {lock_fd}>&-
			printf 'Fixed libguestfs appliance construction failed; collecting trace\n' \
				>>"$preflight_log"
			LIBGUESTFS_DEBUG=1 LIBGUESTFS_TRACE=1 libguestfs-test-tool \
				>>"$preflight_log" 2>&1 || true
			cat "$preflight_log" >&2
			return 70
		fi
		if ! fixed_libguestfs_appliance_complete "$staging_dir"; then
			flock --unlock "$lock_fd"
			exec {lock_fd}>&-
			printf 'Fixed libguestfs appliance construction was incomplete\n' |
				tee -a "$preflight_log" >&2
			return 70
		fi
		if [[ -e "$appliance_dir" ]]; then
			quarantine_dir="$runtime_dir/incomplete-$kernel_version-${BASHPID:-$$}"
			mv -T -- "$appliance_dir" "$quarantine_dir"
			printf 'Quarantined incomplete appliance as %s\n' "$quarantine_dir" \
				>>"$preflight_log"
		fi
		mv -T -- "$staging_dir" "$appliance_dir"
		rmdir -- "$staging_parent"
	fi
	flock --unlock "$lock_fd"
	exec {lock_fd}>&-
	export LIBGUESTFS_PATH="$appliance_dir"
	if ! libguestfs-test-tool >>"$preflight_log" 2>&1; then
		printf 'Fixed libguestfs appliance preflight failed; collecting trace\n' \
			>>"$preflight_log"
		LIBGUESTFS_DEBUG=1 LIBGUESTFS_TRACE=1 libguestfs-test-tool \
			>>"$preflight_log" 2>&1 || true
		cat "$preflight_log" >&2
		return 70
	fi
	export GLADYS_LIBGUESTFS_READY=1
	printf 'Rootless libguestfs preflight passed with kernel %s\n' \
		"$kernel_version"
}

find_ovmf() {
	local code vars
	for code in /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd; do
		[[ -r "$code" ]] || continue
		if [[ "$code" == *_4M.fd ]]; then
			vars=/usr/share/OVMF/OVMF_VARS_4M.fd
		else
			vars=/usr/share/OVMF/OVMF_VARS.fd
		fi
		[[ -r "$vars" ]] || continue
		OVMF_CODE="$code"
		# This shared value is consumed by qemu-install.sh after sourcing this file.
		# shellcheck disable=SC2034
		OVMF_VARS_TEMPLATE="$vars"
		return 0
	done
	printf 'Unable to locate a matching OVMF_CODE/OVMF_VARS firmware pair\n' >&2
	return 69
}

start_vm() {
	local disk="$1" vars="$2" serial_log="$3" host_port="$4" connectivity="${5:-online}"
	local test_signal="${6:-none}"
	local netdev="user,id=net0,hostfwd=tcp:127.0.0.1:${host_port}-:80"
	local -a extra_qemu_args=()
	[[ "$connectivity" == "online" || "$connectivity" == "offline" ]] || return 64
	[[ "$test_signal" == "none" || "$test_signal" == "dpkg-power-loss" ]] || return 64
	if [[ "$connectivity" == "offline" ]]; then
		netdev+=",restrict=on"
	fi
	if [[ "$test_signal" == "dpkg-power-loss" ]]; then
		extra_qemu_args=(-fw_cfg "name=opt/gladys/power-loss-test,string=1")
	fi
	mkdir -p -- "$(dirname -- "$serial_log")"
	qemu-system-x86_64 \
		-name gladys-firstboot \
		-machine q35,accel=kvm \
		-enable-kvm \
		-cpu host \
		-smp "$QEMU_CPUS" \
		-m "$QEMU_MEMORY_MB" \
		-drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE" \
		-drive "if=pflash,format=raw,file=$vars" \
		-drive "if=virtio,format=qcow2,discard=unmap,file=$disk" \
		-device virtio-net-pci,netdev=net0 \
		-netdev "$netdev" \
		"${extra_qemu_args[@]}" \
		-display none \
		-monitor none \
		-serial "file:$serial_log" &
	QEMU_PID=$!
}

stop_vm() {
	[[ -n "$QEMU_PID" ]] || return 0
	if kill -0 "$QEMU_PID" 2>/dev/null; then
		kill -TERM "$QEMU_PID"
		local deadline=$((SECONDS + 30))
		while kill -0 "$QEMU_PID" 2>/dev/null && ((SECONDS < deadline)); do
			sleep 1
		done
		if kill -0 "$QEMU_PID" 2>/dev/null; then
			kill -KILL "$QEMU_PID"
		fi
	fi
	set +e
	wait "$QEMU_PID"
	local status=$?
	set -e
	if ((status != 0 && status != 143 && status != 137)); then
		printf 'QEMU exited with status %d during controlled shutdown\n' "$status" >&2
	fi
	QEMU_PID=""
}

wait_for_phase() {
	local host_port="$1" expected_phase="$2" timeout_seconds="$3"
	local temporary deadline phase
	temporary="$(mktemp -d)"
	deadline=$((SECONDS + timeout_seconds))
	while ((SECONDS < deadline)); do
		kill -0 "$QEMU_PID" 2>/dev/null || {
			rm -rf -- "$temporary"
			printf 'QEMU exited before phase %s\n' "$expected_phase" >&2
			return 1
		}
		if curl --fail --silent --show-error --max-time 5 \
			"http://127.0.0.1:${host_port}/status.json" -o "$temporary/state.json"; then
			phase="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("phase", ""))' "$temporary/state.json")"
			if [[ "$phase" == "$expected_phase" ]]; then
				rm -rf -- "$temporary"
				return 0
			fi
		fi
		sleep 2
	done
	rm -rf -- "$temporary"
	printf 'Timed out waiting for phase %s\n' "$expected_phase" >&2
	return 1
}

wait_for_phase_message() {
	local host_port="$1" expected_phase="$2" expected_message="$3" timeout_seconds="$4"
	local temporary deadline matched
	temporary="$(mktemp -d)"
	deadline=$((SECONDS + timeout_seconds))
	while ((SECONDS < deadline)); do
		kill -0 "$QEMU_PID" 2>/dev/null || {
			rm -rf -- "$temporary"
			printf 'QEMU exited before phase %s reported %s\n' \
				"$expected_phase" "$expected_message" >&2
			return 1
		}
		if curl --fail --silent --show-error --max-time 5 \
			"http://127.0.0.1:${host_port}/status.json" -o "$temporary/state.json"; then
			matched="$(
				python3 - "$temporary/state.json" "$expected_phase" "$expected_message" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
print("yes" if state.get("phase") == sys.argv[2] and sys.argv[3] in state.get("message", "") else "no")
PY
			)"
			if [[ "$matched" == "yes" ]]; then
				rm -rf -- "$temporary"
				return 0
			fi
		fi
		sleep 2
	done
	rm -rf -- "$temporary"
	printf 'Timed out waiting for phase %s to report %s\n' \
		"$expected_phase" "$expected_message" >&2
	return 1
}

wait_for_application_handoff() {
	local host_port="$1" timeout_seconds="$2" diagnostics_dir="${3:-}"
	local temporary deadline cleanup_temporary=0
	local status_summary="" last_status_summary="" phase="" message=""
	if [[ -n "$diagnostics_dir" ]]; then
		mkdir -p -- "$diagnostics_dir"
		temporary="$diagnostics_dir"
	else
		temporary="$(mktemp -d)"
		cleanup_temporary=1
	fi
	deadline=$((SECONDS + timeout_seconds))
	while ((SECONDS < deadline)); do
		kill -0 "$QEMU_PID" 2>/dev/null || {
			if [[ -n "$last_status_summary" ]]; then
				printf 'Last first-boot status: %s\n' "$last_status_summary" >&2
			fi
			if ((cleanup_temporary)); then
				rm -rf -- "$temporary"
			fi
			printf 'QEMU exited before the application became available\n' >&2
			return 1
		}
		if curl --fail --silent --max-time 5 \
			"http://127.0.0.1:${host_port}/status.json" \
			--dump-header "$temporary/status-headers.tmp" \
			--output "$temporary/status.tmp"; then
			mv -- "$temporary/status.tmp" "$temporary/last-status.json"
			mv -- "$temporary/status-headers.tmp" \
				"$temporary/last-status-headers.txt"
			if grep -qi '^X-Gladys-Setup: active' \
				"$temporary/last-status-headers.txt"; then
				if status_summary="$(
					python3 - "$temporary/last-status.json" \
						2>"$temporary/status-error.tmp" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
phase = str(state.get("phase", ""))
message = " ".join(str(state.get("message", "")).split())
print(f"{phase}\t{message}")
PY
				)"; then
					rm -f -- "$temporary/status-error.tmp" \
						"$temporary/last-status-error.txt"
					IFS=$'\t' read -r phase message <<<"$status_summary"
					if [[ "$status_summary" != "$last_status_summary" ]]; then
						printf 'First-boot status: %s: %s\n' "$phase" "$message"
						last_status_summary="$status_summary"
					fi
					if [[ "$phase" == "FATAL" ]]; then
						if ((cleanup_temporary)); then
							rm -rf -- "$temporary"
						fi
						printf 'First boot reported FATAL: %s\n' "$message" >&2
						return 1
					fi
				else
					mv -- "$temporary/status-error.tmp" \
						"$temporary/last-status-error.txt"
				fi
			else
				rm -f -- "$temporary/status-error.tmp" \
					"$temporary/last-status-error.txt"
			fi
		else
			rm -f -- "$temporary/status.tmp" \
				"$temporary/status-headers.tmp"
		fi
		if curl --fail --silent --show-error --max-time 10 \
			--dump-header "$temporary/headers.tmp" --output "$temporary/body.tmp" \
			"http://127.0.0.1:${host_port}/"; then
			mv -- "$temporary/headers.tmp" "$temporary/last-headers.txt"
			mv -- "$temporary/body.tmp" "$temporary/last-body.txt"
			if ! grep -qi '^X-Gladys-Setup: active' "$temporary/last-headers.txt"; then
				if ((cleanup_temporary)); then
					rm -rf -- "$temporary"
				fi
				return 0
			fi
		else
			rm -f -- "$temporary/headers.tmp" "$temporary/body.tmp"
		fi
		sleep 5
	done
	if [[ -n "$last_status_summary" ]]; then
		printf 'Last first-boot status: %s\n' "$last_status_summary" >&2
	fi
	if ((cleanup_temporary)); then
		rm -rf -- "$temporary"
	fi
	printf 'Timed out waiting for the port-80 application handoff\n' >&2
	return 1
}

assert_completed_disk() {
	local disk="$1" temporary
	temporary="$(mktemp)"
	virt-cat -a "$disk" /var/lib/gladys-installer/completed >"$temporary"
	python3 - "$temporary" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    marker = json.load(handle)
required = {"installer_version", "completed_at", "image_id", "image_digest"}
if set(marker) != required or not marker["installer_version"] or not marker["completed_at"]:
    raise SystemExit("invalid completion marker")
PY
	rm -f -- "$temporary"
}

assert_dpkg_barrier_state() {
	local disk="$1" expected="$2" temporary update_name
	[[ "$expected" == "interrupted" || "$expected" == "installed" ]] || return 64
	temporary="$(mktemp -d)"
	(
		trap 'rm -rf -- "$temporary"' EXIT
		mkdir -p -- "$temporary/updates"
		virt-cat -a "$disk" /var/lib/dpkg/status >"$temporary/status"
		guestfish --ro -a "$disk" -i ls /var/lib/dpkg/updates \
			>"$temporary/update-names"
		LC_ALL=C sort "$temporary/update-names" \
			>"$temporary/sorted-update-names"
		while IFS= read -r update_name; do
			[[ "$update_name" =~ ^[0-9]+$ ]] || continue
			virt-cat -a "$disk" "/var/lib/dpkg/updates/$update_name" \
				>"$temporary/updates/$update_name"
		done <"$temporary/sorted-update-names"
		python3 "$QEMU_COMMON_DIR/assert-dpkg-barrier-state.py" \
			"$temporary" "$expected"
	)
}

assert_no_password_or_ssh() {
	local disk="$1" temporary
	temporary="$(mktemp -d)"
	virt-cat -a "$disk" /etc/passwd >"$temporary/passwd"
	virt-cat -a "$disk" /etc/shadow >"$temporary/shadow"
	python3 - "$temporary/passwd" "$temporary/shadow" <<'PY'
import sys

passwd = [line.rstrip("\n").split(":") for line in open(sys.argv[1], encoding="utf-8")]
shadow = {
    fields[0]: fields[1]
    for line in open(sys.argv[2], encoding="utf-8")
    if len(fields := line.rstrip("\n").split(":")) >= 2
}
ordinary = [fields[0] for fields in passwd if 1000 <= int(fields[2]) < 65534]
if ordinary:
    raise SystemExit(f"unexpected ordinary accounts: {ordinary}")
for fields in passwd:
    name, uid = fields[0], int(fields[2])
    if uid == 0 or 1000 <= uid < 65534:
        password = shadow.get(name, "")
        if not password.startswith(("!", "*")):
            raise SystemExit(f"account is not locked: {name}")
PY
	rm -rf -- "$temporary"
	if virt-ls -a "$disk" /usr/sbin/sshd >/dev/null 2>&1; then
		printf 'OpenSSH server is unexpectedly installed\n' >&2
		return 1
	fi
}

assert_single_resources() {
	local disk="$1" application_name="$2" paths path guest_path name
	local application_count=0 watchtower_count=0 completed_count=0
	paths="$(guestfish --ro -a "$disk" -i find /var/lib/docker/containers)"
	while IFS= read -r path; do
		[[ "$path" == */config.v2.json ]] || continue
		guest_path="/var/lib/docker/containers${path}"
		name="$(virt-cat -a "$disk" "$guest_path" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("Name", ""))')"
		if [[ "$name" == "/$application_name" ]]; then
			((application_count += 1))
		fi
		if [[ "$name" == "/watchtower" ]]; then
			((watchtower_count += 1))
		fi
	done <<<"$paths"
	((application_count == 1 && watchtower_count == 1)) || {
		printf 'Expected one %s and one Watchtower container; found %d and %d\n' \
			"$application_name" "$application_count" "$watchtower_count" >&2
		return 1
	}
	[[ "$(guestfish --ro -a "$disk" -i is-dir /var/lib/gladysassistant)" == "true" ]] || {
		printf 'Persistent Gladys data directory is missing\n' >&2
		return 1
	}
	while IFS= read -r path; do
		[[ "$path" == "/completed" ]] && ((completed_count += 1))
	done < <(guestfish --ro -a "$disk" -i find /var/lib/gladys-installer)
	((completed_count == 1)) || {
		printf 'Expected one completion marker; found %d\n' "$completed_count" >&2
		return 1
	}
}

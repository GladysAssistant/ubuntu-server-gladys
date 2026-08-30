from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
INTEGRATION = ROOT / "tests" / "integration"


class QemuContractTests(unittest.TestCase):
    def test_dpkg_barrier_state_replays_numeric_update_journal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory)
            updates = snapshot / "updates"
            updates.mkdir()
            (snapshot / "status").write_text(
                "Package: base-files\nStatus: install ok installed\n",
                encoding="utf-8",
            )
            (updates / "0000").write_text(
                "Package: gladys-ci-dpkg-barrier\n"
                "Status: install ok installed\n",
                encoding="utf-8",
            )
            (updates / "0001").write_text(
                "Package: gladys-ci-dpkg-barrier\n"
                "Status: install ok half-configured\n",
                encoding="utf-8",
            )
            (updates / "tmp.i").write_text(
                "Package: gladys-ci-dpkg-barrier\n"
                "Status: install ok installed\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(INTEGRATION / "assert-dpkg-barrier-state.py"),
                    str(snapshot),
                    "interrupted",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dpkg_barrier_state_accepts_checkpoint_and_fails_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "updates").mkdir()
            status_path = snapshot / "status"
            status_path.write_text(
                "Package: gladys-ci-dpkg-barrier\n"
                "Status: install ok installed\n",
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(INTEGRATION / "assert-dpkg-barrier-state.py"),
                str(snapshot),
                "installed",
            ]

            installed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
            status_path.write_text(
                "Package: base-files\nStatus: install ok installed\n",
                encoding="utf-8",
            )
            absent = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertNotEqual(absent.returncode, 0)
        self.assertIn("barrier package was not recovered: ''", absent.stderr)

    def test_dpkg_barrier_state_rejects_malformed_numeric_journals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory)
            updates = snapshot / "updates"
            updates.mkdir()
            (snapshot / "status").write_text(
                "Package: base-files\nStatus: install ok installed\n",
                encoding="utf-8",
            )
            (updates / "0000").write_text(
                "Package: gladys-ci-dpkg-barrier\n"
                "Status: install ok installed\n",
                encoding="utf-8",
            )
            (updates / "001").write_text(
                "Package: gladys-ci-dpkg-barrier\n"
                "Status: install ok half-configured\n",
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(INTEGRATION / "assert-dpkg-barrier-state.py"),
                str(snapshot),
                "interrupted",
            ]

            mixed_widths = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
            (updates / "001").unlink()
            (updates / "0000").write_text(
                "Package: gladys-ci-dpkg-barrier\n"
                "Status: install ok installed\n"
                "Status: install ok half-configured\n",
                encoding="utf-8",
            )
            duplicate_field = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
            (updates / "0000").write_text(
                "Status: install ok half-configured\n",
                encoding="utf-8",
            )
            missing_package = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
            (updates / "0000").unlink()
            (snapshot / "status").write_text(
                "Package: base-files\n"
                "Package: gladys-ci-dpkg-barrier\n"
                "Status: install ok half-configured\n",
                encoding="utf-8",
            )
            duplicate_status_field = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(mixed_widths.returncode, 0)
        self.assertIn("different filename widths", mixed_widths.stderr)
        self.assertNotEqual(duplicate_field.returncode, 0)
        self.assertIn("duplicate field", duplicate_field.stderr)
        self.assertNotEqual(missing_package.returncode, 0)
        self.assertIn("missing required 'Package' field", missing_package.stderr)
        self.assertNotEqual(duplicate_status_field.returncode, 0)
        self.assertIn("duplicate field", duplicate_status_field.stderr)

    @unittest.skipIf(
        os.name == "nt" or shutil.which("bash") is None,
        "the QEMU shell helper is exercised on Linux CI",
    )
    def test_qemu_helper_snapshots_numeric_dpkg_updates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            snapshot = root / "snapshot"
            fake_bin.mkdir()
            snapshot.mkdir()
            (snapshot / "status").write_text(
                "Package: base-files\nStatus: install ok installed\n",
                encoding="utf-8",
            )
            (snapshot / "0000").write_text(
                "Package: gladys-ci-dpkg-barrier\n"
                "Status: install ok unpacked\n",
                encoding="utf-8",
            )
            (snapshot / "0001").write_text(
                "Package: gladys-ci-dpkg-barrier\n"
                "Status: install ok half-configured\n",
                encoding="utf-8",
            )
            fake_guestfish = fake_bin / "guestfish"
            fake_guestfish.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' tmp.i 0001 0000\n",
                encoding="utf-8",
            )
            fake_virt_cat = fake_bin / "virt-cat"
            fake_virt_cat.write_text(
                """#!/usr/bin/env bash
set -Eeuo pipefail
guest_path="${@: -1}"
printf '%s\n' "$guest_path" >> "$FAKE_VIRT_CAT_LOG"
case "$guest_path" in
    /var/lib/dpkg/status) source_path="$FAKE_DPKG_SNAPSHOT/status" ;;
    /var/lib/dpkg/updates/0000) source_path="$FAKE_DPKG_SNAPSHOT/0000" ;;
    /var/lib/dpkg/updates/0001) source_path="$FAKE_DPKG_SNAPSHOT/0001" ;;
    *) exit 65 ;;
esac
cat "$source_path"
""",
                encoding="utf-8",
            )
            fake_guestfish.chmod(0o755)
            fake_virt_cat.chmod(0o755)
            command = "\n".join(
                (
                    "set -Eeuo pipefail",
                    f"source {shlex.quote(str(INTEGRATION / 'qemu-common.sh'))}",
                    "assert_dpkg_barrier_state fake-disk interrupted",
                )
            )
            environment = dict(os.environ)
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
            environment["FAKE_DPKG_SNAPSHOT"] = str(snapshot)
            environment["FAKE_VIRT_CAT_LOG"] = str(root / "virt-cat.log")

            result = subprocess.run(
                ["bash", "-c", command],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                env=environment,
            )

            inspected_paths = (root / "virt-cat.log").read_text(
                encoding="utf-8"
            ).splitlines()
            fake_sort = fake_bin / "sort"
            fake_sort.write_text("#!/usr/bin/env bash\nexit 23\n", encoding="utf-8")
            fake_sort.chmod(0o755)
            (snapshot / "status").write_text(
                "Package: gladys-ci-dpkg-barrier\n"
                "Status: install ok half-configured\n",
                encoding="utf-8",
            )
            sort_failure = subprocess.run(
                ["bash", "-c", command],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                env=environment,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(sort_failure.returncode, 0)
        self.assertEqual(
            inspected_paths,
            [
                "/var/lib/dpkg/status",
                "/var/lib/dpkg/updates/0000",
                "/var/lib/dpkg/updates/0001",
            ],
        )

    def _run_application_handoff_fixture(
        self, status_body: str, *, setup_status: bool
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            diagnostics = root / "diagnostics"
            fake_bin.mkdir()
            diagnostics.mkdir()
            fake_curl = fake_bin / "curl"
            fake_curl.write_text(
                """#!/usr/bin/env bash
set -Eeuo pipefail
output=""
headers=""
url=""
while (($#)); do
    case "$1" in
        -o|--output) output="$2"; shift 2 ;;
        --dump-header) headers="$2"; shift 2 ;;
        http://*) url="$1"; shift ;;
        *) shift ;;
    esac
done
if [[ "$url" == */status.json ]]; then
    if [[ "$FAKE_SETUP_STATUS" == yes ]]; then
        printf '%s\n' 'HTTP/1.0 200 OK' 'X-Gladys-Setup: active' > "$headers"
    else
        printf '%s\n' 'HTTP/1.0 200 OK' > "$headers"
    fi
    printf '%s\n' "$FAKE_STATUS_BODY" > "$output"
else
    printf '%s\n' 'HTTP/1.0 200 OK' > "$headers"
    printf '%s\n' 'application ready' > "$output"
fi
""",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)
            command = "\n".join(
                (
                    "set -Eeuo pipefail",
                    f"source {shlex.quote(str(INTEGRATION / 'qemu-common.sh'))}",
                    "QEMU_PID=$$",
                    "wait_for_application_handoff "
                    f"18080 1 {shlex.quote(str(diagnostics))}",
                )
            )
            environment = dict(os.environ)
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
            environment["FAKE_SETUP_STATUS"] = "yes" if setup_status else "no"
            environment["FAKE_STATUS_BODY"] = status_body
            result = subprocess.run(
                ["bash", "-c", command],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                env=environment,
            )
            diagnostics_by_name = {
                path.name: path.read_text(encoding="utf-8")
                for path in diagnostics.iterdir()
                if path.is_file()
            }
        return result, diagnostics_by_name

    def test_shellcheck_resolves_shared_qemu_source_and_exported_state(self) -> None:
        source_directive = "# shellcheck source=tests/integration/qemu-common.sh"
        for name in (
            "qemu-install.sh",
            "qemu-firstboot.sh",
            "qemu-network-recovery.sh",
            "qemu-power-loss.sh",
        ):
            with self.subTest(name=name):
                self.assertIn(
                    source_directive,
                    (INTEGRATION / name).read_text(encoding="utf-8"),
                )
        common = (INTEGRATION / "qemu-common.sh").read_text(encoding="utf-8")
        self.assertIn("# shellcheck disable=SC2034", common)

    def test_install_uses_uefi_kvm_sparse_disk_serial_and_timeout(self) -> None:
        text = "\n".join(
            (INTEGRATION / name).read_text(encoding="utf-8")
            for name in ("qemu-common.sh", "qemu-install.sh")
        )
        for contract in (
            "qemu-img create -f qcow2",
            "-enable-kvm",
            "OVMF_CODE",
            "-serial",
            "timeout",
            "-no-reboot",
        ):
            self.assertIn(contract, text)

    def test_firstboot_uses_http_and_read_only_disk_inspection_without_login(self) -> None:
        text = "\n".join(
            (INTEGRATION / name).read_text(encoding="utf-8")
            for name in ("qemu-common.sh", "qemu-firstboot.sh")
        )
        self.assertIn("hostfwd=tcp:127.0.0.1", text)
        self.assertIn("assert_completed_disk", text)
        self.assertIn("assert_no_password_or_ssh", text)
        self.assertNotIn("sshpass", text)

    @unittest.skipIf(
        os.name == "nt" or shutil.which("bash") is None or os.geteuid() == 0,
        "the unreadable Ubuntu kernel fixture requires an unprivileged Linux user",
    )
    def test_libguestfs_preflight_copies_unreadable_kernel_and_builds_fixed_appliance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boot = root / "boot"
            modules = root / "modules"
            runtime = root / "runtime"
            diagnostics = root / "diagnostics"
            fake_bin = root / "bin"
            version = "6.8.0-test-generic"
            boot.mkdir()
            (modules / version).mkdir(parents=True)
            fake_bin.mkdir()
            kernel = boot / f"vmlinuz-{version}"
            kernel.write_bytes(b"test kernel\n")
            kernel.chmod(0o000)
            (boot / "vmlinuz-6.9.0-unmatched").write_bytes(b"newer kernel\n")

            fake_sudo = fake_bin / "sudo"
            fake_sudo.write_text(
                """#!/usr/bin/env bash
set -Eeuo pipefail
[[ "$1" == -n ]]
shift
source="${@: -2:1}"
target="${@: -1}"
[[ "$1" == install ]]
[[ "$source" == "$EXPECTED_SOURCE" ]]
printf 'test kernel\n' > "$target"
chmod 0644 "$target"
""",
                encoding="utf-8",
            )
            fake_builder = fake_bin / "libguestfs-make-fixed-appliance"
            fake_builder.write_text(
                """#!/usr/bin/env bash
set -Eeuo pipefail
[[ -r "$SUPERMIN_KERNEL" ]]
[[ "$SUPERMIN_MODULES" == "$EXPECTED_MODULES" ]]
[[ "$SUPERMIN_KERNEL_VERSION" == "$EXPECTED_VERSION" ]]
printf 'built\n' >> "$BUILD_COUNT"
mkdir -p "$1"
printf 'kernel\n' > "$1/kernel"
printf 'initrd\n' > "$1/initrd"
printf 'root\n' > "$1/root"
printf 'fixed\n' > "$1/README.fixed"
""",
                encoding="utf-8",
            )
            fake_preflight = fake_bin / "libguestfs-test-tool"
            fake_preflight.write_text(
                """#!/usr/bin/env bash
set -Eeuo pipefail
[[ "$LIBGUESTFS_BACKEND" == direct ]]
[[ -r "$LIBGUESTFS_PATH/kernel" ]]
[[ -r "$LIBGUESTFS_PATH/initrd" ]]
[[ -r "$LIBGUESTFS_PATH/root" ]]
printf 'preflight ok\n'
""",
                encoding="utf-8",
            )
            for executable in (fake_sudo, fake_builder, fake_preflight):
                executable.chmod(0o755)

            command = "\n".join(
                (
                    "set -Eeuo pipefail",
                    f"source {shlex.quote(str(INTEGRATION / 'qemu-common.sh'))}",
                    f"prepare_libguestfs {shlex.quote(str(diagnostics))}",
                    "LIBGUESTFS_BACKEND=libvirt",
                    f"prepare_libguestfs {shlex.quote(str(diagnostics))}",
                    "printf '%s\\n' \"$GLADYS_LIBGUESTFS_READY\"",
                    "printf '%s\\n' \"$LIBGUESTFS_BACKEND\"",
                    "printf '%s\\n' \"$SUPERMIN_KERNEL\"",
                    "printf '%s\\n' \"$SUPERMIN_MODULES\"",
                    "printf '%s\\n' \"$LIBGUESTFS_PATH\"",
                )
            )
            environment = dict(os.environ)
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
            environment["GLADYS_LIBGUESTFS_BOOT_DIR"] = str(boot)
            environment["GLADYS_LIBGUESTFS_MODULES_ROOT"] = str(modules)
            environment["GLADYS_LIBGUESTFS_RUNTIME_DIR"] = str(runtime)
            environment["EXPECTED_SOURCE"] = str(kernel)
            environment["EXPECTED_MODULES"] = str(modules / version)
            environment["EXPECTED_VERSION"] = version
            environment["BUILD_COUNT"] = str(root / "build-count.txt")

            result = subprocess.run(
                ["bash", "-c", command],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            output = result.stdout.splitlines()[-5:]
            self.assertEqual(output[0:2], ["1", "direct"])
            self.assertEqual(Path(output[2]).read_bytes(), b"test kernel\n")
            self.assertEqual(kernel.stat().st_mode & 0o777, 0o000)
            self.assertEqual(Path(output[3]), modules / version)
            appliance = Path(output[4])
            self.assertTrue((appliance / "README.fixed").is_file())
            self.assertIn(
                "preflight ok",
                (diagnostics / "libguestfs-preflight.log").read_text(
                    encoding="utf-8"
                ),
            )

            second_diagnostics = root / "second-diagnostics"
            second_command = "\n".join(
                (
                    "set -Eeuo pipefail",
                    f"source {shlex.quote(str(INTEGRATION / 'qemu-common.sh'))}",
                    f"prepare_libguestfs {shlex.quote(str(second_diagnostics))}",
                    "printf '%s\\n' \"$LIBGUESTFS_PATH\"",
                )
            )
            second = subprocess.run(
                ["bash", "-c", second_command],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(Path(second.stdout.splitlines()[-1]), appliance)
            self.assertEqual(
                (root / "build-count.txt").read_text(encoding="utf-8"),
                "built\n",
            )

    def test_every_disk_inspection_prepares_rootless_fixed_appliance(self) -> None:
        for name in (
            "qemu-firstboot.sh",
            "qemu-network-recovery.sh",
            "qemu-power-loss.sh",
        ):
            with self.subTest(name=name):
                text = (INTEGRATION / name).read_text(encoding="utf-8")
                self.assertIn('prepare_libguestfs "$WORK_DIR"', text)
                self.assertLess(text.index("prepare_libguestfs"), text.index("start_vm"))
        common = (INTEGRATION / "qemu-common.sh").read_text(encoding="utf-8")
        self.assertIn("libguestfs-make-fixed-appliance", common)
        self.assertIn("export LIBGUESTFS_BACKEND=direct", common)
        self.assertIn('export LIBGUESTFS_PATH="$appliance_dir"', common)
        self.assertNotIn("qemu-nbd", common)
        self.assertNotIn("sudo virt-", common)

    @unittest.skipIf(
        os.name == "nt" or shutil.which("bash") is None,
        "the QEMU shell helper is exercised on Linux CI",
    )
    def test_handoff_fails_immediately_on_fatal_and_preserves_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            diagnostics = root / "diagnostics"
            fake_bin.mkdir()
            diagnostics.mkdir()
            fake_curl = fake_bin / "curl"
            fake_curl.write_text(
                """#!/usr/bin/env bash
set -Eeuo pipefail
output=""
headers=""
url=""
while (($#)); do
    case "$1" in
        -o|--output) output="$2"; shift 2 ;;
        --dump-header) headers="$2"; shift 2 ;;
        http://*) url="$1"; shift ;;
        *) shift ;;
    esac
done
/bin/sleep 0.05
if [[ "$url" == */status.json ]]; then
    printf '%s\n' 'HTTP/1.0 200 OK' 'X-Gladys-Setup: active' > "$headers"
    printf '%s\n' '{"schema":1,"phase":"FATAL","progress":0,"message":"simulated fatal failure","updated_at":"2026-08-24T00:00:00Z"}' > "$output"
else
    printf '%s\n' 'HTTP/1.0 200 OK' 'X-Gladys-Setup: active' > "$headers"
    printf '%s\n' 'setup' > "$output"
fi
""",
                encoding="utf-8",
            )
            fake_sleep = fake_bin / "sleep"
            fake_sleep.write_text(
                "#!/usr/bin/env bash\nprintf 'called\\n' >> \"$FAKE_SLEEP_LOG\"\n",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)
            fake_sleep.chmod(0o755)
            command = "\n".join(
                (
                    "set -Eeuo pipefail",
                    f"source {shlex.quote(str(INTEGRATION / 'qemu-common.sh'))}",
                    "QEMU_PID=$$",
                    "wait_for_application_handoff "
                    f"18080 1 {shlex.quote(str(diagnostics))}",
                )
            )
            environment = dict(os.environ)
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
            environment["FAKE_SLEEP_LOG"] = str(root / "sleep.log")

            result = subprocess.run(
                ["bash", "-c", command],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FATAL: simulated fatal failure", result.stderr)
            state = yaml.safe_load(
                (diagnostics / "last-status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                state,
                {
                    "schema": 1,
                    "phase": "FATAL",
                    "progress": 0,
                    "message": "simulated fatal failure",
                    "updated_at": "2026-08-24T00:00:00Z",
                },
            )
            self.assertFalse((root / "sleep.log").exists())

    @unittest.skipIf(
        os.name == "nt" or shutil.which("bash") is None,
        "the QEMU shell helper is exercised on Linux CI",
    )
    def test_handoff_timeout_preserves_last_setup_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            diagnostics = root / "diagnostics"
            fake_bin.mkdir()
            diagnostics.mkdir()
            fake_curl = fake_bin / "curl"
            fake_curl.write_text(
                """#!/usr/bin/env bash
set -Eeuo pipefail
output=""
headers=""
url=""
while (($#)); do
    case "$1" in
        -o|--output) output="$2"; shift 2 ;;
        --dump-header) headers="$2"; shift 2 ;;
        http://*) url="$1"; shift ;;
        *) shift ;;
    esac
done
if [[ "$url" == */status.json ]]; then
    printf '%s\n' 'HTTP/1.0 200 OK' 'X-Gladys-Setup: active' > "$headers"
    printf '%s\n' '{"schema":1,"phase":"START_GLADYS","progress":80,"message":"Waiting for port 80","updated_at":"2026-08-24T00:00:00Z"}' > "$output"
else
    printf '%s\n' 'HTTP/1.0 200 OK' 'X-Gladys-Setup: active' > "$headers"
    printf '%s\n' 'setup page' > "$output"
fi
""",
                encoding="utf-8",
            )
            fake_sleep = fake_bin / "sleep"
            fake_sleep.write_text(
                "#!/usr/bin/env bash\n/bin/sleep 0.1\n", encoding="utf-8"
            )
            fake_curl.chmod(0o755)
            fake_sleep.chmod(0o755)
            command = "\n".join(
                (
                    "set -Eeuo pipefail",
                    f"source {shlex.quote(str(INTEGRATION / 'qemu-common.sh'))}",
                    "QEMU_PID=$$",
                    "wait_for_application_handoff "
                    f"18080 1 {shlex.quote(str(diagnostics))}",
                )
            )
            environment = dict(os.environ)
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"

            result = subprocess.run(
                ["bash", "-c", command],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Last first-boot status: START_GLADYS", result.stderr)
            self.assertEqual(
                yaml.safe_load(
                    (diagnostics / "last-status.json").read_text(encoding="utf-8")
                )["phase"],
                "START_GLADYS",
            )
            self.assertIn(
                "X-Gladys-Setup: active",
                (diagnostics / "last-headers.txt").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                (diagnostics / "last-body.txt").read_text(encoding="utf-8"),
                "setup page\n",
            )

    @unittest.skipIf(
        os.name == "nt" or shutil.which("bash") is None,
        "the QEMU shell helper is exercised on Linux CI",
    )
    def test_handoff_ignores_fatal_json_from_application(self) -> None:
        body = '{"phase":"FATAL","message":"application payload"}'
        result, diagnostics = self._run_application_handoff_fixture(
            body, setup_status=False
        )

        self.assertEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(diagnostics["last-status.json"], f"{body}\n")
        self.assertNotIn("last-status-error.txt", diagnostics)

    @unittest.skipIf(
        os.name == "nt" or shutil.which("bash") is None,
        "the QEMU shell helper is exercised on Linux CI",
    )
    def test_handoff_accepts_plaintext_from_application(self) -> None:
        result, diagnostics = self._run_application_handoff_fixture(
            "application response", setup_status=False
        )

        self.assertEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(diagnostics["last-status.json"], "application response\n")
        self.assertNotIn("last-status-error.txt", diagnostics)

    @unittest.skipIf(
        os.name == "nt" or shutil.which("bash") is None,
        "the QEMU shell helper is exercised on Linux CI",
    )
    def test_handoff_preserves_malformed_setup_status_error(self) -> None:
        result, diagnostics = self._run_application_handoff_fixture(
            "malformed setup status", setup_status=True
        )

        self.assertEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(diagnostics["last-status.json"], "malformed setup status\n")
        self.assertIn("JSONDecodeError", diagnostics["last-status-error.txt"])

    def test_firstboot_diagnostics_are_uploaded_even_after_failure(self) -> None:
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        )
        steps = workflow["jobs"]["qemu-firstboot"]["steps"]
        upload = next(
            step
            for step in steps
            if str(step.get("uses", "")).startswith("actions/upload-artifact@")
            and step.get("with", {}).get("name") == "qemu-firstboot-diagnostics"
        )

        self.assertEqual(upload["if"], "always()")
        self.assertEqual(upload["with"]["path"], "build/qemu-firstboot")

    def test_release_qemu_gates_upload_serial_diagnostics_even_after_failure(self) -> None:
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        )
        for job in ("real-gladys", "network-recovery", "power-loss-recovery"):
            with self.subTest(job=job):
                upload = next(
                    step
                    for step in workflow["jobs"][job]["steps"]
                    if str(step.get("uses", "")).startswith("actions/upload-artifact@")
                )
                self.assertEqual(upload["if"], "always()")
                self.assertIn("build/**/*serial.log", upload["with"]["path"])

    def test_recovery_scripts_reuse_disk_and_power_test_forces_termination(self) -> None:
        network = (INTEGRATION / "qemu-network-recovery.sh").read_text(encoding="utf-8")
        power = (INTEGRATION / "qemu-power-loss.sh").read_text(encoding="utf-8")
        self.assertIn("restrict=on", network)
        self.assertIn("WAIT_NETWORK", network)
        self.assertIn('start_vm "$DISK"', network)
        self.assertIn('start_vm "$DISK"', power)
        self.assertIn("kill -KILL", power)
        self.assertIn("INSTALL_PACKAGES", power)
        self.assertIn("dpkg postinst active; package configuration pending", power)
        self.assertIn("-fw_cfg", "\n".join((INTEGRATION / name).read_text(encoding="utf-8") for name in ("qemu-common.sh", "qemu-power-loss.sh")))
        self.assertIn("apt-power-loss-barrier.py", power)
        self.assertIn('assert_dpkg_barrier_state "$DISK" interrupted', power)
        self.assertIn('assert_dpkg_barrier_state "$DISK" installed', power)
        self.assertIn("assert_single_resources", power)


if __name__ == "__main__":
    unittest.main()

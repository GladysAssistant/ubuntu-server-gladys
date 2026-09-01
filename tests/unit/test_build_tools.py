from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

if os.name == "nt":
    _git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    BASH = str(_git_bash) if _git_bash.is_file() else None
else:
    BASH = shutil.which("bash")


def bash_path(path: Path) -> str:
    # Do not dereference virtual-environment interpreter symlinks: doing so
    # silently drops the venv's installed validation dependencies.
    resolved = path.absolute().as_posix()
    if os.name == "nt" and len(resolved) >= 3 and resolved[1:3] == ":/":
        return f"/{resolved[0].lower()}/{resolved[3:]}"
    return resolved


@unittest.skipIf(os.name == "nt", "requires POSIX symbolic-link semantics")
class TestHarnessPathTests(unittest.TestCase):
    def test_bash_path_preserves_the_active_python_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            interpreter = root / "python3.12"
            interpreter.touch()
            venv_python = root / "python"
            venv_python.symlink_to(interpreter.name)

            self.assertEqual(venv_python.absolute().as_posix(), bash_path(venv_python))


def load_script(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / filename)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


patch_grub = load_script("patch_grub", "patch-grub.py")
buildlib = load_script("buildlib", "buildlib.py")
build_info = load_script("generate_build_info", "generate-build-info.py")
update_md5 = load_script("update_md5", "update-md5.py")
validate_config = load_script("validate_config", "validate-config.py")
inspect_payload = load_script("inspect_payload", "inspect-payload.py")
compare_boot = load_script("compare_boot_metadata", "compare-boot-metadata.py")
ci_barrier_deb = load_script("generate_ci_barrier_deb", "generate-ci-barrier-deb.py")


SINGLE_GRUB = """set timeout=30
menuentry "Try or Install Ubuntu Server" {
    set gfxpayload=keep
    linux /casper/vmlinuz quiet ---
    initrd /casper/initrd
}
if [ "$grub_platform" = "efi" ]; then
    menuentry 'UEFI Firmware Settings' {
        fwsetup
    }
fi
"""


class UbuntuDiscoveryTests(unittest.TestCase):
    def test_iso_info_requires_ubuntu_server_2604_amd64(self) -> None:
        self.assertTrue(
            buildlib.require_ubuntu_iso_info(
                'Ubuntu-Server 26.04 "Resolute Raccoon" - Release amd64 '
                "(20260420.1)"
            )
        )
        self.assertTrue(
            buildlib.require_ubuntu_iso_info(
                'Ubuntu-Server 26.04.1 LTS "Resolute Raccoon" - Release amd64 '
                "(20260826)"
            )
        )
        for invalid in (
            'Ubuntu-Server 25.10 "Questing Quokka" - Release amd64 (20251001)',
            'Ubuntu-Desktop 26.04 "Resolute Raccoon" - Release amd64 (20260420)',
            'Ubuntu-Server 26.04 "Resolute Raccoon" - Release arm64 (20260420)',
            'Ubuntu-Server 26.04.1 "Resolute Raccoon" - Release amd64 '
            "(20260826)",
            'Ubuntu-Server 26.04 LTS "Resolute Raccoon" - Release amd64 '
            "(20260420)",
            'Ubuntu-Server 26.04.1 Daily "Resolute Raccoon" - Release amd64 '
            "(20260826)",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(
                buildlib.BuildInputError
            ):
                buildlib.require_ubuntu_iso_info(invalid)

    def test_signed_sums_select_single_ga_or_point_release(self) -> None:
        checksum = "a" * 64
        for filename in (
            "ubuntu-26.04-live-server-amd64.iso",
            "ubuntu-26.04.2-live-server-amd64.iso",
        ):
            text = "\n".join(
                (
                    f"{'b' * 64} *ubuntu-26.04-desktop-amd64.iso",
                    f"{checksum} *{filename}",
                )
            )
            with self.subTest(filename=filename):
                self.assertEqual(
                    buildlib.discover_server_iso(text),
                    (filename, checksum),
                )

    def test_signed_sums_select_latest_point_release_from_cumulative_metadata(self) -> None:
        current_metadata = (
            "dec49008a71f6098d0bcfc822021f4d042d5f2db279e4d75bdd981304f1ca5d9 "
            "*ubuntu-26.04-live-server-amd64.iso\n"
            "cc8a95cde20f6ced61a322420de00f10cc3c90ced545daa46cb9c1a117f1d927 "
            "*ubuntu-26.04.1-live-server-amd64.iso\n"
        )
        future_metadata = (
            f"{'c' * 64} *ubuntu-26.04.10-live-server-amd64.iso\n"
        ) + current_metadata + (
            f"{'d' * 64} *ubuntu-26.04.2-live-server-amd64.iso\n"
        )

        for metadata, expected in (
            (
                current_metadata,
                (
                    "ubuntu-26.04.1-live-server-amd64.iso",
                    "cc8a95cde20f6ced61a322420de00f10cc3c90ced545daa46cb9c1a117f1d927",
                ),
            ),
            (future_metadata, ("ubuntu-26.04.10-live-server-amd64.iso", "c" * 64)),
        ):
            with self.subTest(expected=expected[0]):
                self.assertEqual(buildlib.discover_server_iso(metadata), expected)

    def test_signed_sums_reject_zero_or_ambiguous_latest_candidates(self) -> None:
        with self.assertRaises(buildlib.BuildInputError):
            buildlib.discover_server_iso(f"{'a' * 64} *ubuntu-26.04-desktop-amd64.iso\n")
        with self.assertRaises(buildlib.BuildInputError):
            buildlib.discover_server_iso(
                f"{'a' * 64} *ubuntu-26.04.1-live-server-amd64.iso\n"
                f"{'b' * 64} *ubuntu-26.04.1-live-server-amd64.iso\n"
            )

    def test_signed_sums_reject_malformed_candidate_lines(self) -> None:
        malformed = (
            f"{'a' * 64} *ubuntu-26.04-live-server-amd64.iso\n"
            "not-a-checksum ubuntu-26.04.1-live-server-amd64.iso\n"
        )
        with self.assertRaises(buildlib.BuildInputError):
            buildlib.discover_server_iso(malformed)

    def test_source_catalog_requires_one_minimal_source(self) -> None:
        catalog = """
sources:
  - id: ubuntu-server-minimal
    name: Ubuntu Server (minimized)
  - id: ubuntu-server
    name: Ubuntu Server
"""
        self.assertTrue(buildlib.require_minimal_source(catalog))
        with self.assertRaises(buildlib.BuildInputError):
            buildlib.require_minimal_source("sources:\n  - id: ubuntu-server\n")
        with self.assertRaises(buildlib.BuildInputError):
            buildlib.require_minimal_source(
                "sources:\n  - id: ubuntu-server-minimal\n  - id: ubuntu-server-minimal\n"
            )


class GrubPatchTests(unittest.TestCase):
    @staticmethod
    def _syntax_checker_environment(root: Path) -> dict[str, str]:
        fake_bin = root / "bin"
        fake_bin.mkdir()
        checker = fake_bin / "grub-script-check"
        checker.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                from pathlib import Path
                import sys

                config = Path(sys.argv[1]).read_text(encoding="utf-8")
                if config.startswith("if true; then\\n"):
                    print("unclosed if", file=sys.stderr)
                    raise SystemExit(1)
                """
            ),
            encoding="utf-8",
            newline="\n",
        )
        checker.chmod(0o755)
        environment = os.environ.copy()
        environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
        return environment

    def test_replaces_canonical_entry_with_single_autoboot_gladys_entry(self) -> None:
        patched = patch_grub.patch_grub(SINGLE_GRUB)

        self.assertEqual(patched.count("Install Gladys Assistant"), 1)
        self.assertNotIn("Try or Install Ubuntu Server", patched)
        self.assertEqual(patched.count("linux /casper/vmlinuz quiet autoinstall ---"), 1)
        self.assertEqual(patched.count("initrd /casper/initrd"), 1)
        lines = patched.splitlines()
        self.assertEqual(lines.count("set default=0"), 1)
        self.assertEqual(lines.count("set timeout_style=menu"), 1)
        self.assertEqual(lines.count("set timeout=3"), 1)
        self.assertIn("menuentry 'UEFI Firmware Settings'", patched)

    def test_handles_blank_line_before_canonical_entry(self) -> None:
        upstream = SINGLE_GRUB.replace(
            'menuentry "Try or Install Ubuntu Server"',
            '\nmenuentry "Try or Install Ubuntu Server"',
            1,
        )

        patched = patch_grub.patch_grub(upstream)

        self.assertEqual(1, patched.count("Install Gladys Assistant"))
        self.assertNotIn("Try or Install Ubuntu Server", patched)

    def test_rejects_ambiguous_canonical_entries(self) -> None:
        ambiguous = SINGLE_GRUB.replace(
            "if [", SINGLE_GRUB[SINGLE_GRUB.index("menuentry") :] + "\nif [", 1
        )
        with self.assertRaises(patch_grub.GrubPatchError):
            patch_grub.patch_grub(ambiguous)

    def test_rejects_noncanonical_casper_candidate(self) -> None:
        unexpected = SINGLE_GRUB.replace(
            "Try or Install Ubuntu Server", "Unexpected Casper entry"
        )

        with self.assertRaises(patch_grub.GrubPatchError):
            patch_grub.patch_grub(unexpected)

    def test_rejects_missing_or_unbalanced_entries(self) -> None:
        with self.assertRaises(patch_grub.GrubPatchError):
            patch_grub.patch_grub("menuentry 'Memory test' {\n  linux /memtest\n}\n")
        with self.assertRaises(patch_grub.GrubPatchError):
            patch_grub.patch_grub(SINGLE_GRUB.rsplit("}", 1)[0])

    def test_finished_grub_requires_single_gladys_entry_and_autoboot_policy(self) -> None:
        patched = patch_grub.patch_grub(SINGLE_GRUB)
        self.assertTrue(patch_grub.validate_patched_grub(patched))

        contaminated = patched + SINGLE_GRUB[
            SINGLE_GRUB.index('menuentry "Try or Install Ubuntu Server"') :
            SINGLE_GRUB.index('if [ "$grub_platform"')
        ]
        with self.assertRaises(patch_grub.GrubPatchError):
            patch_grub.validate_patched_grub(contaminated)

        for original, invalid in (
            ("set default=0", "set default=1"),
            ("set timeout_style=menu", "set timeout_style=hidden"),
            ("set timeout=3", "set timeout=30"),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(
                patch_grub.GrubPatchError
            ):
                patch_grub.validate_patched_grub(patched.replace(original, invalid))

    def test_finished_grub_rejects_commands_after_the_boot_policy(self) -> None:
        patched = patch_grub.patch_grub(SINGLE_GRUB)

        for suffix in (
            "unset timeout\n",
            "set timeout=0; set timeout_style=hidden; set default=1\n",
        ):
            with self.subTest(suffix=suffix), self.assertRaises(
                patch_grub.GrubPatchError
            ):
                patch_grub.validate_patched_grub(patched + suffix)
        self.assertTrue(patch_grub.validate_patched_grub(patched + "# comment\n"))

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires executable checker")
    def test_check_against_base_rejects_non_deterministic_grub(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.cfg"
            custom = root / "custom.cfg"
            base.write_text(SINGLE_GRUB, encoding="utf-8")
            custom.write_text(patch_grub.patch_grub(SINGLE_GRUB), encoding="utf-8")
            command = [
                sys.executable,
                str(SCRIPTS / "patch-grub.py"),
                "--check",
                "--base",
                str(base),
                str(custom),
            ]

            environment = self._syntax_checker_environment(root)
            valid = subprocess.run(
                command,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, valid.returncode, valid.stderr)

            custom.write_text(
                custom.read_text(encoding="utf-8") + "unset timeout\n",
                encoding="utf-8",
            )
            tampered = subprocess.run(
                command,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(0, tampered.returncode)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires executable checker")
    def test_cli_rejects_invalid_global_grub_syntax(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "invalid.cfg"
            output = root / "patched.cfg"
            source.write_text("if true; then\n" + SINGLE_GRUB, encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "patch-grub.py"),
                    str(source),
                    str(output),
                ],
                env=self._syntax_checker_environment(root),
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertFalse(output.exists())


class BuildInfoTests(unittest.TestCase):
    def test_build_info_contains_required_release_fields(self) -> None:
        result = build_info.create_build_info(
            installer_version="1.0.0",
            git_commit="0123456789abcdef",
            ubuntu_iso_filename="ubuntu-26.04-live-server-amd64.iso",
            ubuntu_iso_sha256="a" * 64,
            timestamp="2026-08-23T12:00:00Z",
        )

        self.assertEqual(
            result,
            {
                "installer_version": "1.0.0",
                "git_commit": "0123456789abcdef",
                "architecture": "amd64",
                "ubuntu_series": "26.04",
                "ubuntu_iso_filename": "ubuntu-26.04-live-server-amd64.iso",
                "ubuntu_iso_sha256": "a" * 64,
                "build_timestamp_utc": "2026-08-23T12:00:00Z",
            },
        )


class MediaChecksumTests(unittest.TestCase):
    def test_updates_only_the_modified_grub_checksum(self) -> None:
        original = (
            "11111111111111111111111111111111  ./boot/grub/grub.cfg\n"
            "22222222222222222222222222222222  ./casper/initrd\n"
        )

        updated = update_md5.update_checksum(
            original, "boot/grub/grub.cfg", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )

        self.assertEqual(
            updated,
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  ./boot/grub/grub.cfg\n"
            "22222222222222222222222222222222  ./casper/initrd\n",
        )

    def test_rejects_duplicate_checksum_entries(self) -> None:
        duplicate = (
            "11111111111111111111111111111111  boot/grub/grub.cfg\n"
            "22222222222222222222222222222222  ./boot/grub/grub.cfg\n"
        )
        with self.assertRaises(update_md5.MediaChecksumError):
            update_md5.update_checksum(
                duplicate, "boot/grub/grub.cfg", "a" * 32
            )


class AutoinstallValidatorTests(unittest.TestCase):
    @staticmethod
    def _authoritative_schema() -> dict:
        properties = {
            name: {}
            for name in (
                "apt",
                "interactive-sections",
                "late-commands",
                "shutdown",
                "source",
                "ssh",
                "storage",
                "user-data",
            )
        }
        properties["version"] = {
            "type": "integer",
            "minimum": 1,
            "maximum": 1,
        }
        return {"type": "object", "properties": properties, "required": ["version"]}

    def test_schema_comes_from_primary_installer_layer_not_generic_overlay(self) -> None:
        script = (SCRIPTS / "validate-autoinstall.sh").read_text(encoding="utf-8")
        self.assertIn(
            'readonly INSTALLER_SQUASHFS="/casper/'
            'ubuntu-server-minimal.ubuntu-server.installer.squashfs"',
            script,
        )
        self.assertNotIn("installer.generic.squashfs", script)
        self.assertIn("-extract /.disk/info", script)
        self.assertIn("buildlib.py\" iso-info", script)
        self.assertIn("seed\\/snaps\\/subiquity_[0-9]+", script)
        self.assertIn("examples/lsb-release-focal", script)
        self.assertIn("DISTRIB_RELEASE=26.04", script)
        self.assertIn('cd "$temporary/subiquity-snap"', script)
        self.assertIn("-m subiquity.cmd.schema", script)

    @unittest.skipUnless(BASH, "requires Bash")
    def test_validates_canonical_iso_inputs_as_an_unprivileged_runner(self) -> None:
        build_directory = ROOT / "build"
        build_directory.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_directory) as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            trace = root / "core-extraction-argv"
            base_iso = root / "ubuntu.iso"
            base_iso.touch()
            validator = root / "validator"
            (validator / ".git").mkdir(parents=True)
            (validator / "scripts").mkdir()
            (validator / "system_scripts").mkdir()
            (validator / "curtin").mkdir()
            (validator / "probert").mkdir()
            (validator / "kbds").mkdir()
            (validator / "snapcraft.yaml").write_text(
                "parts:\n"
                "  curtin:\n"
                f'    source-commit: "{"c" * 40}"\n'
                "  probert:\n"
                f'    source-commit: "{"d" * 40}"\n',
                encoding="utf-8",
                newline="\n",
            )
            (validator / "kbds" / "keyboard-configuration.yaml").write_text(
                "en_US.UTF-8:\n  layout: us\n  variant: ''\n",
                encoding="utf-8",
                newline="\n",
            )
            validator_script = validator / "scripts" / "validate-autoinstall-user-data.py"
            validator_script.write_text(
                textwrap.dedent(
                    """
                    import argparse
                    import json
                    import os
                    import subprocess
                    import sys
                    from pathlib import Path

                    import yaml

                    parser = argparse.ArgumentParser()
                    parser.add_argument("input", type=argparse.FileType("r"))
                    parser.add_argument("--no-expect-cloudconfig", action="store_true")
                    parser.add_argument(
                        "--json-schema",
                        type=argparse.FileType("r"),
                        default="autoinstall-schema.json",
                    )
                    arguments = parser.parse_args()
                    schema_path = Path(arguments.json_schema.name)
                    if not schema_path.is_absolute():
                        parser.error("--json-schema must be an absolute path")
                    json.load(arguments.json_schema)
                    input_document = yaml.safe_load(arguments.input)
                    source_id = input_document["autoinstall"]["source"]["id"]
                    if source_id != "synthesized":
                        parser.error(
                            "Canonical controller validation requires its "
                            "synthetic source catalog"
                        )
                    tracked_input = (
                        Path(os.environ["TRACKED_AUTOINSTALL_DIR"])
                        / Path(arguments.input.name).name
                    )
                    expected_document = yaml.safe_load(
                        tracked_input.read_text(encoding="utf-8")
                    )
                    expected_document["autoinstall"]["source"]["id"] = "synthesized"
                    if input_document != expected_document:
                        parser.error(
                            "Canonical projection changed fields other than source.id"
                        )
                    Path("kbds/keyboard-configuration.yaml").read_text(
                        encoding="utf-8"
                    )
                    validator_root = Path(__file__).resolve().parents[1]
                    legacy_command = [
                        str(
                            validator_root
                            / "system_scripts"
                            / "subiquity-legacy-cloud-init-validate"
                        )
                    ]
                    if os.name == "nt":
                        legacy_command.insert(0, sys.executable)
                    subprocess.run(
                        legacy_command,
                        check=True,
                    )
                    with Path(os.environ["VALIDATOR_SCHEMA_TRACE"]).open(
                        "a", encoding="utf-8"
                    ) as trace:
                        trace.write(f"{schema_path}\\n")
                    with Path(os.environ["VALIDATOR_CWD_TRACE"]).open(
                        "a", encoding="utf-8"
                    ) as trace:
                        trace.write(f"{Path.cwd()}\\n")
                    with Path(os.environ["VALIDATOR_INPUT_TRACE"]).open(
                        "a", encoding="utf-8"
                    ) as trace:
                        trace.write(f"{Path(arguments.input.name)}\\n")
                    """
                ).lstrip(),
                encoding="utf-8",
                newline="\n",
            )
            legacy_validator = (
                validator / "system_scripts" / "subiquity-legacy-cloud-init-validate"
            )
            legacy_validator.write_text(
                textwrap.dedent(
                    """
                    #!/usr/bin/env python3
                    import os
                    from pathlib import Path

                    Path(os.environ["LEGACY_VALIDATOR_TRACE"]).touch()
                    """
                ).lstrip(),
                encoding="utf-8",
                newline="\r\n",
            )
            legacy_validator.chmod(0o755)
            legacy_validator_trace = root / "legacy-validator-trace"
            validator_schema_trace = root / "validator-schema-trace"
            validator_cwd_trace = root / "validator-cwd-trace"
            validator_input_trace = root / "validator-input-trace"
            xorriso_extraction_trace = root / "xorriso-extraction-trace"
            tracked_configs = [
                ROOT / "autoinstall" / "autoinstall.yaml",
                ROOT / "autoinstall" / "autoinstall-ci.yaml",
            ]
            tracked_config_contents = {
                path: path.read_text(encoding="utf-8") for path in tracked_configs
            }

            def executable(name: str, body: str) -> None:
                path = fake_bin / name
                content = textwrap.dedent(body).lstrip()
                if os.name == "nt" and content.startswith("#!/usr/bin/env python3\n"):
                    content = f"#!{bash_path(Path(sys.executable))}\n" + content.split(
                        "\n", 1
                    )[1]
                path.write_text(content, encoding="utf-8", newline="\n")
                path.chmod(0o755)

            executable(
                "xorriso",
                """
                #!/usr/bin/env python3
                import os
                import pathlib
                import sys

                content = {
                    "/.disk/info": os.environ["ISO_INFO"],
                    "/casper/install-sources.yaml": (
                        "sources:\\n- id: ubuntu-server-minimal\\n"
                    ),
                    (
                        "/casper/ubuntu-server-minimal.ubuntu-server."
                        "installer.squashfs"
                    ): "installer",
                    "/casper/ubuntu-server-minimal.squashfs": "minimal",
                }
                args = sys.argv[1:]
                for index, argument in enumerate(args):
                    if argument != "-extract":
                        continue
                    source, destination = args[index + 1 : index + 3]
                    with pathlib.Path(
                        os.environ["XORRISO_EXTRACTION_TRACE"]
                    ).open("a", encoding="utf-8") as trace:
                        trace.write(f"{source}\\n")
                    path = pathlib.Path(destination)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    extracted = next(
                        value
                        for member, value in content.items()
                        if source.endswith(member)
                    )
                    path.write_text(extracted, encoding="utf-8", newline="\\n")
                """,
            )
            schema_json = json.dumps(self._authoritative_schema(), separators=(",", ":"))
            executable(
                "unsquashfs",
                f"""
                #!/usr/bin/env python3
                import os
                import pathlib
                import shlex
                import sys

                args = sys.argv[1:]
                if "-ll" in args:
                    filesystem = pathlib.Path(args[-1]).read_text(encoding="utf-8")
                    if filesystem == "installer":
                        print(
                            "-rw-r--r-- root/root 1 2026-04-20 "
                            "squashfs-root/var/lib/snapd/seed/snaps/"
                            "subiquity_1.snap"
                        )
                    if filesystem == os.environ["CORE_SNAP_LAYER"]:
                        print(
                            "-rw-r--r-- root/root 1 2026-04-20 "
                            "squashfs-root/var/lib/snapd/seed/snaps/core24_1.snap"
                        )
                    raise SystemExit(0)
                if "-cat" in args:
                    filesystem = pathlib.Path(args[args.index("-cat") + 1]).read_text(
                        encoding="utf-8"
                    )
                    member = args[args.index("-cat") + 2]
                    if member.endswith("subiquity_1.snap"):
                        expected_filesystem = "installer"
                    elif member.endswith("core24_1.snap"):
                        expected_filesystem = os.environ["CORE_SNAP_LAYER"]
                    else:
                        raise SystemExit(f"unexpected snap member: {{member}}")
                    if filesystem != expected_filesystem:
                        raise SystemExit(
                            f"snap {{member}} read from {{filesystem}}, expected "
                            f"{{expected_filesystem}}"
                        )
                    sys.stdout.buffer.write(b"snap")
                    raise SystemExit(0)

                destination = pathlib.Path(args[args.index("-d") + 1])
                destination.mkdir(parents=True, exist_ok=True)
                if any(value.endswith("subiquity.snap") for value in args):
                    if "-no-xattrs" not in args:
                        print("Subiquity extraction requested xattrs", file=sys.stderr)
                        raise SystemExit(2)
                    python = destination / "usr/bin/python3.12"
                    python.parent.mkdir(parents=True)
                    python.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
                    python.chmod(0o755)
                    (destination / "lib/python3.12/site-packages").mkdir(parents=True)
                    (destination / "usr/lib/python3/dist-packages").mkdir(parents=True)
                    raise SystemExit(0)

                pathlib.Path(os.environ["CORE_EXTRACTION_TRACE"]).write_text(
                    "\\n".join(args), encoding="utf-8"
                )
                expected_members = (
                    "usr/lib/python3.12",
                    "usr/lib/python3/dist-packages",
                    "usr/lib/x86_64-linux-gnu",
                )
                filesystem_index = next(
                    index
                    for index, value in enumerate(args)
                    if value.endswith("core24.snap")
                )
                if (
                    "-no-xattrs" not in args
                    or tuple(args[filesystem_index + 1 :]) != expected_members
                    or any(value in args for value in ("-no-exit-code", "-ignore-errors"))
                ):
                    print("core extraction requested privileged metadata", file=sys.stderr)
                    raise SystemExit(2)
                (destination / "usr/lib/python3.12").mkdir(parents=True)
                (destination / "usr/lib/python3/dist-packages").mkdir(parents=True)
                loader = destination / (
                    "usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
                )
                loader.parent.mkdir(parents=True)
                loader.write_text(
                    "#!/bin/sh\\nprintf '%s\\n' " + shlex.quote({schema_json!r}) + "\\n",
                    encoding="utf-8",
                )
                loader.chmod(0o755)
                """,
            )
            executable(
                "git",
                """
                #!/usr/bin/env python3
                import io
                import pathlib
                import sys
                import tarfile

                arguments = sys.argv[1:]
                source = pathlib.Path.cwd()
                if arguments[:1] == ["-C"]:
                    source = pathlib.Path(arguments[1])
                    arguments = arguments[2:]

                if arguments[:1] in (["fetch"], ["checkout"]):
                    raise SystemExit(0)
                if arguments[:2] == ["submodule", "update"]:
                    raise SystemExit(0)
                if arguments[:1] == ["rev-parse"]:
                    target = arguments[1]
                    if target.endswith(":curtin") or source.name == "curtin":
                        print("c" * 40)
                    elif target.endswith(":probert") or source.name == "probert":
                        print("d" * 40)
                    else:
                        print("9b41f1418858e38f88ba2724f540389b3fa41a0a")
                    raise SystemExit(0)
                if arguments[:1] == ["archive"]:
                    output_argument = next(
                        value for value in arguments if value.startswith("--output=")
                    )
                    output = pathlib.Path(output_argument.split("=", 1)[1])
                    with tarfile.open(output, "w") as archive:
                        for path in sorted(source.rglob("*")):
                            relative = path.relative_to(source)
                            if ".git" in relative.parts:
                                continue
                            info = archive.gettarinfo(
                                str(path), arcname=relative.as_posix()
                            )
                            if path.is_file():
                                data = path.read_bytes().replace(b"\\r\\n", b"\\n")
                                info.size = len(data)
                                archive.addfile(info, io.BytesIO(data))
                            else:
                                archive.addfile(info)
                    raise SystemExit(0)

                raise SystemExit(f"unexpected fake git invocation: {arguments}")
                """,
            )
            if os.name == "nt":
                executable(
                    "tar",
                    """
                    #!/usr/bin/env python3
                    import pathlib
                    import sys
                    import tarfile

                    arguments = sys.argv[1:]
                    if arguments[:1] != ["-xf"] or "-C" not in arguments:
                        raise SystemExit(
                            f"unexpected fake tar invocation: {arguments}"
                        )
                    archive = pathlib.Path(arguments[1])
                    destination = pathlib.Path(arguments[arguments.index("-C") + 1])
                    with tarfile.open(archive, "r") as source:
                        source.extractall(destination)
                    """,
                )
            executable(
                "yamllint",
                "#!/bin/sh\nexec python3 -m yamllint \"$@\"\n",
            )
            executable(
                "mkdir",
                """
                #!/usr/bin/env python3
                import pathlib
                import sys

                for argument in sys.argv[1:]:
                    if argument in {"-p", "--"}:
                        continue
                    pathlib.Path(argument).mkdir(parents=True, exist_ok=True)
                """,
            )
            executable(
                "python3",
                f'#!/bin/sh\nexec "{bash_path(Path(sys.executable))}" "$@"\n',
            )

            environment = os.environ.copy()
            environment["CORE_EXTRACTION_TRACE"] = str(trace)
            # Ubuntu 26.04.1 moved core24 from the installer delta into the
            # minimal base filesystem. Model that verified ISO topology first.
            environment["CORE_SNAP_LAYER"] = "minimal"
            environment["ISO_INFO"] = (
                'Ubuntu-Server 26.04.1 LTS "Resolute Raccoon" - Release '
                "amd64 (20260826)"
            )
            environment["LEGACY_VALIDATOR_TRACE"] = str(legacy_validator_trace)
            environment["TRACKED_AUTOINSTALL_DIR"] = str(tracked_configs[0].parent)
            environment["VALIDATOR_CWD_TRACE"] = str(validator_cwd_trace)
            environment["VALIDATOR_INPUT_TRACE"] = str(validator_input_trace)
            environment["VALIDATOR_SCHEMA_TRACE"] = str(validator_schema_trace)
            environment["XORRISO_EXTRACTION_TRACE"] = str(
                xorriso_extraction_trace
            )
            environment["TMPDIR"] = root.resolve().as_posix()
            result = subprocess.run(
                [
                    BASH,
                    "-c",
                    'PATH="$1:/usr/bin:/bin:$PATH"; export PATH; '
                    'if command -v cygpath >/dev/null; then '
                    'TMPDIR="$(cygpath -m "$5")"; export TMPDIR; fi; '
                    'exec bash "$2" --base-iso "$3" --subiquity-dir "$4"',
                    "autoinstall-validator-test",
                    bash_path(fake_bin),
                    bash_path(SCRIPTS / "validate-autoinstall.sh"),
                    bash_path(base_iso),
                    bash_path(validator),
                    bash_path(root),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr + result.stdout)
            self.assertTrue(legacy_validator_trace.is_file())
            arguments = trace.read_text(encoding="utf-8").splitlines()
            filesystem_index = next(
                index
                for index, value in enumerate(arguments)
                if value.endswith("core24.snap")
            )
            self.assertIn("-no-xattrs", arguments)
            self.assertNotIn("-no-exit-code", arguments)
            self.assertNotIn("-ignore-errors", arguments)
            validator_schema_paths = validator_schema_trace.read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(2, len(validator_schema_paths))
            self.assertTrue(
                all(
                    Path(path).name == "autoinstall-schema.json"
                    for path in validator_schema_paths
                )
            )
            validator_working_directories = validator_cwd_trace.read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(2, len(validator_working_directories))
            self.assertEqual(
                validator_working_directories[0], validator_working_directories[1]
            )
            runtime_directory = Path(validator_working_directories[0])
            self.assertEqual("subiquity-validator", runtime_directory.name)
            self.assertNotEqual(validator.resolve(), runtime_directory.resolve())
            validator_input_paths = [
                Path(path).resolve()
                for path in validator_input_trace.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(
                {"autoinstall.yaml", "autoinstall-ci.yaml"},
                {path.name for path in validator_input_paths},
            )
            self.assertTrue(
                all(
                    path.parent != tracked_configs[0].parent
                    for path in validator_input_paths
                )
            )
            self.assertEqual(
                tracked_config_contents,
                {path: path.read_text(encoding="utf-8") for path in tracked_configs},
            )
            self.assertEqual(
                [
                    "usr/lib/python3.12",
                    "usr/lib/python3/dist-packages",
                    "usr/lib/x86_64-linux-gnu",
                ],
                arguments[filesystem_index + 1 :],
            )
            minimal_layer = "/casper/ubuntu-server-minimal.squashfs"
            self.assertTrue(
                any(
                    path.endswith(minimal_layer)
                    for path in xorriso_extraction_trace.read_text(
                        encoding="utf-8"
                    ).splitlines()
                )
            )

            # Ubuntu 26.04 GA kept core24 beside Subiquity in the installer
            # layer. Keep that authenticated layout covered as well.
            xorriso_extraction_trace.unlink()
            environment["CORE_SNAP_LAYER"] = "installer"
            environment["ISO_INFO"] = (
                'Ubuntu-Server 26.04 "Resolute Raccoon" - Release '
                "amd64 (20260420.1)"
            )
            ga_result = subprocess.run(
                [
                    BASH,
                    "-c",
                    'PATH="$1:/usr/bin:/bin:$PATH"; export PATH; '
                    'if command -v cygpath >/dev/null; then '
                    'TMPDIR="$(cygpath -m "$5")"; export TMPDIR; fi; '
                    'exec bash "$2" --base-iso "$3" --subiquity-dir "$4"',
                    "autoinstall-validator-ga-test",
                    bash_path(fake_bin),
                    bash_path(SCRIPTS / "validate-autoinstall.sh"),
                    bash_path(base_iso),
                    bash_path(validator),
                    bash_path(root),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                0, ga_result.returncode, ga_result.stderr + ga_result.stdout
            )
            self.assertFalse(
                any(
                    path.endswith(minimal_layer)
                    for path in xorriso_extraction_trace.read_text(
                        encoding="utf-8"
                    ).splitlines()
                )
            )

    def test_schema_validator_is_an_explicit_build_dependency(self) -> None:
        requirements = (ROOT / "requirements-build.txt").read_text(encoding="utf-8")
        self.assertIn("jsonschema", requirements)
        self.assertIn("PyYAML", requirements)

    def test_validator_rejects_a_vacuous_iso_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schema = root / "schema.json"
            catalog = root / "catalog.yaml"
            schema.write_text("{}", encoding="utf-8")
            catalog.write_text("- id: ubuntu-server-minimal\n", encoding="utf-8")

            with self.assertRaises(validate_config.ConfigurationError):
                validate_config.validate_repository_configs(
                    ROOT / "autoinstall" / "autoinstall.yaml",
                    ROOT / "autoinstall" / "autoinstall-ci.yaml",
                    schema,
                    catalog,
                )

    def test_repository_configs_validate_against_supplied_iso_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schema = root / "schema.json"
            catalog = root / "catalog.yaml"
            schema.write_text(
                json.dumps(self._authoritative_schema()), encoding="utf-8"
            )
            catalog.write_text("- id: ubuntu-server-minimal\n", encoding="utf-8")

            validate_config.validate_repository_configs(
                ROOT / "autoinstall" / "autoinstall.yaml",
                ROOT / "autoinstall" / "autoinstall-ci.yaml",
                schema,
                catalog,
            )

    def test_validator_enforces_french_language_and_keyboard_defaults(self) -> None:
        production_source = (
            ROOT / "autoinstall" / "autoinstall.yaml"
        ).read_text(encoding="utf-8")
        ci_source = (ROOT / "autoinstall" / "autoinstall-ci.yaml").read_text(
            encoding="utf-8"
        )
        for original, invalid in (
            ("locale: fr_FR.UTF-8", "locale: en_US.UTF-8"),
            ("layout: fr", "layout: us"),
        ):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                production = root / "production.yaml"
                ci = root / "ci.yaml"
                schema = root / "schema.json"
                catalog = root / "catalog.yaml"
                production.write_text(
                    production_source.replace(original, invalid), encoding="utf-8"
                )
                ci.write_text(ci_source.replace(original, invalid), encoding="utf-8")
                schema.write_text(
                    json.dumps(self._authoritative_schema()), encoding="utf-8"
                )
                catalog.write_text(
                    "- id: ubuntu-server-minimal\n", encoding="utf-8"
                )

                with self.assertRaises(validate_config.ConfigurationError):
                    validate_config.validate_repository_configs(
                        production, ci, schema, catalog
                    )

    def test_validator_rejects_unrelated_ci_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            production = root / "production.yaml"
            ci = root / "ci.yaml"
            schema = root / "schema.json"
            catalog = root / "catalog.yaml"
            production.write_text(
                "autoinstall:\n  version: 1\n"
                "  interactive-sections: [locale, keyboard, storage, identity, ssh]\n"
                "  user-data: {}\n",
                encoding="utf-8",
            )
            ci.write_text(
                "autoinstall:\n  version: 1\n  interactive-sections: []\n"
                "  user-data: {users: [], ssh_pwauth: false}\n"
                "  locale: en_US.UTF-8\n",
                encoding="utf-8",
            )
            schema.write_text(
                json.dumps(self._authoritative_schema()), encoding="utf-8"
            )
            catalog.write_text("- id: ubuntu-server-minimal\n", encoding="utf-8")

            with self.assertRaises(validate_config.ConfigurationError):
                validate_config.validate_repository_configs(
                    production, ci, schema, catalog, enforce_safety=False
                )


class PayloadInspectionTests(unittest.TestCase):
    @staticmethod
    def _archive(root: Path, files: dict[str, bytes]) -> Path:
        archive = root / "payload.tar"
        with tarfile.open(archive, "w") as handle:
            for name, body in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(body)
                info.uid = 0
                info.gid = 0
                info.mode = 0o644
                handle.addfile(info, io.BytesIO(body))
        return archive

    def test_production_payload_accepts_only_moving_v5_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = self._archive(
                Path(directory),
                {
                    "./opt/gladys/compose.yaml": (
                        b"services:\n  gladys:\n    image: gladysassistant/gladys:v5\n"
                        b"  watchtower:\n    image: nickfedor/watchtower\n"
                    ),
                    "./etc/gladys-installer/version": b"1.0.0\n",
                },
            )
            inspect_payload.inspect_payload(archive, "production")

    def test_production_payload_rejects_test_data_images_and_numeric_tags(self) -> None:
        forbidden = (
            ("./etc/gladys-installer/test-mode", b"enabled\n"),
            ("./var/lib/docker/image/overlay2/layerdb/x", b"layer"),
            ("./opt/cache/gladys-image.tar", b"archive"),
            ("./opt/gladys/compose.yaml", b"image: gladysassistant/gladys:5.99.0\n"),
        )
        for name, body in forbidden:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                archive = self._archive(Path(directory), {name: body})
                with self.assertRaises(inspect_payload.PayloadInspectionError):
                    inspect_payload.inspect_payload(archive, "production")

    def test_production_payload_rejects_python_build_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = self._archive(
                Path(directory),
                {
                    "./opt/gladys/compose.yaml": (
                        b"image: gladysassistant/gladys:v5\n"
                        b"image: nickfedor/watchtower\n"
                    ),
                    "./usr/lib/gladys-installer/__pycache__/bootstrap.cpython-311.pyc": b"cache",
                },
            )
            with self.assertRaisesRegex(
                inspect_payload.PayloadInspectionError, "cache"
            ):
                inspect_payload.inspect_payload(archive, "production")

    def test_payload_rejects_traversal_and_non_root_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = self._archive(Path(directory), {"../escape": b"bad"})
            with self.assertRaises(inspect_payload.PayloadInspectionError):
                inspect_payload.inspect_payload(archive, "ci-smoke")
        with tempfile.TemporaryDirectory() as directory:
            archive = self._archive(Path(directory), {"./safe": b"bad"})
            with tarfile.open(archive, "a") as handle:
                info = tarfile.TarInfo("./non-root")
                info.uid = 1000
                info.gid = 1000
                handle.addfile(info, io.BytesIO())
            with self.assertRaises(inspect_payload.PayloadInspectionError):
                inspect_payload.inspect_payload(archive, "ci-smoke")


class BuildContractTests(unittest.TestCase):
    @staticmethod
    def _stage_iso_build(
        root: Path, *, inspector_source: str, xorriso_source: str
    ) -> tuple[Path, Path]:
        scripts = root / "scripts"
        autoinstall = root / "autoinstall"
        fake_bin = root / "bin"
        scripts.mkdir()
        autoinstall.mkdir()
        fake_bin.mkdir()

        shutil.copyfile(SCRIPTS / "build-iso.sh", scripts / "build-iso.sh")
        (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        (autoinstall / "autoinstall.yaml").write_text(
            "autoinstall:\n  version: 1\n", encoding="utf-8"
        )

        base_iso = root / "ubuntu.iso"
        base_iso.touch()
        metadata = json.dumps(
            {
                "path": str(base_iso),
                "filename": "ubuntu-26.04-live-server-amd64.iso",
                "sha256": "a" * 64,
            },
            separators=(",", ":"),
        )
        (scripts / "fetch-ubuntu-base.sh").write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                set -eu
                metadata_out=
                while (($#)); do
                    case "$1" in
                        --metadata-out) metadata_out="$2"; shift 2 ;;
                        *) shift 2 ;;
                    esac
                done
                printf '%s\\n' '{metadata}' >"$metadata_out"
                """
            ),
            encoding="utf-8",
            newline="\n",
        )
        (scripts / "validate-autoinstall.sh").write_text(
            "#!/usr/bin/env bash\nexit 0\n",
            encoding="utf-8",
            newline="\n",
        )
        (scripts / "make-payload.sh").write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -eu
                output_dir=
                while (($#)); do
                    case "$1" in
                        --output-dir) output_dir="$2"; shift 2 ;;
                        *) shift 2 ;;
                    esac
                done
                mkdir -p -- "$output_dir"
                : >"$output_dir/gladys-payload.tar"
                printf 'a  gladys-payload.tar\\n' \
                    >"$output_dir/gladys-payload.tar.sha256"
                """
            ),
            encoding="utf-8",
            newline="\n",
        )
        (scripts / "inspect-payload.py").write_text(
            textwrap.dedent(inspector_source),
            encoding="utf-8",
            newline="\n",
        )
        (scripts / "inspect-payload.py").chmod(0o644)

        copy_input = textwrap.dedent(
            """\
            from pathlib import Path
            import sys

            Path(sys.argv[2]).write_bytes(Path(sys.argv[1]).read_bytes())
            """
        )
        for filename in ("patch-grub.py", "update-md5.py"):
            (scripts / filename).write_text(
                copy_input, encoding="utf-8", newline="\n"
            )
        (scripts / "inspect-iso.sh").write_text(
            "#!/usr/bin/env bash\nexit 0\n",
            encoding="utf-8",
            newline="\n",
        )
        (scripts / "generate-build-info.py").write_text(
            textwrap.dedent(
                """\
                from pathlib import Path
                import sys

                output = Path(sys.argv[sys.argv.index("--output") + 1])
                output.write_text("{}\\n", encoding="utf-8")
                """
            ),
            encoding="utf-8",
            newline="\n",
        )

        xorriso = fake_bin / "xorriso"
        xorriso.write_text(
            textwrap.dedent(xorriso_source),
            encoding="utf-8",
            newline="\n",
        )
        xorriso.chmod(0o755)
        grub_script_check = fake_bin / "grub-script-check"
        grub_script_check.write_text(
            "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n"
        )
        grub_script_check.chmod(0o755)
        return scripts, fake_bin

    @unittest.skipUnless(
        sys.platform.startswith("linux") and BASH,
        "requires Linux execute-permission semantics",
    )
    def test_iso_build_runs_payload_inspector_without_execute_permission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "inspector-ran"
            scripts, fake_bin = self._stage_iso_build(
                root,
                inspector_source="""\
                    from pathlib import Path
                    import os

                    Path(os.environ["INSPECT_MARKER"]).write_text(
                        "ran", encoding="utf-8"
                    )
                    raise SystemExit(73)
                """,
                xorriso_source="""\
                    #!/usr/bin/env bash
                    exit 99
                """,
            )

            environment = os.environ.copy()
            environment["INSPECT_MARKER"] = str(marker)
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
            result = subprocess.run(
                [BASH, str(scripts / "build-iso.sh"), "--profile", "production"],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(73, result.returncode, result.stderr + result.stdout)
            self.assertEqual("ran", marker.read_text(encoding="utf-8"))

    @unittest.skipUnless(
        sys.platform.startswith("linux") and BASH,
        "requires the Linux ISO build launcher",
    )
    def test_iso_build_terminates_xorriso_mkdir_before_mapping_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts, fake_bin = self._stage_iso_build(
                root,
                inspector_source="""\
                    raise SystemExit(0)
                """,
                xorriso_source="""\
                    #!/usr/bin/env python3
                    from pathlib import Path
                    import sys

                    arguments = sys.argv[1:]
                    if "-osirrox" in arguments:
                        for index, argument in enumerate(arguments):
                            if argument != "-extract":
                                continue
                            iso_path = arguments[index + 1]
                            output = Path(arguments[index + 2])
                            output.parent.mkdir(parents=True, exist_ok=True)
                            if iso_path == "/boot/grub/grub.cfg":
                                output.write_text("grub\\n", encoding="utf-8")
                            elif iso_path == "/md5sum.txt":
                                output.write_text(
                                    "0" * 32 + "  ./boot/grub/grub.cfg\\n",
                                    encoding="utf-8",
                                )
                            else:
                                raise SystemExit(f"unexpected extraction: {iso_path}")
                        raise SystemExit(0)

                    output_index = arguments.index("-outdev") + 1
                    output_iso = Path(arguments[output_index])
                    mkdir_index = arguments.index("-mkdir")
                    if arguments[mkdir_index + 1 : mkdir_index + 3] != [
                        "/gladys",
                        "--",
                    ]:
                        raise SystemExit(
                            "xorriso -mkdir list is missing its -- terminator"
                        )

                    destinations = []
                    index = mkdir_index + 3
                    while index < len(arguments):
                        command = arguments[index]
                        if command == "-map":
                            source = Path(arguments[index + 1])
                            if not source.is_file():
                                raise SystemExit(f"missing map source: {source}")
                            destinations.append(arguments[index + 2])
                            index += 3
                        elif command == "-boot_image":
                            if arguments[index + 1 : index + 3] != ["any", "replay"]:
                                raise SystemExit("unexpected boot replay arguments")
                            index += 3
                        else:
                            raise SystemExit(f"unexpected assembly command: {command}")

                    expected = [
                        "/autoinstall.yaml",
                        "/gladys/gladys-payload.tar",
                        "/gladys/gladys-payload.tar.sha256",
                        "/gladys/build-profile.json",
                        "/boot/grub/grub.cfg",
                        "/md5sum.txt",
                    ]
                    if destinations != expected:
                        raise SystemExit(f"unexpected map destinations: {destinations}")
                    output_iso.touch()
                """,
            )

            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
            result = subprocess.run(
                [BASH, str(scripts / "build-iso.sh"), "--profile", "production"],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr + result.stdout)
            self.assertTrue(
                (root / "dist/gladys-assistant-installer-v1.0.0-amd64.iso").is_file()
            )

    def test_iso_build_defaults_to_production_and_replays_boot_equipment(self) -> None:
        script = (SCRIPTS / "build-iso.sh").read_text(encoding="utf-8")
        self.assertIn('PROFILE="production"', script)
        self.assertIn("production | ci-smoke | ci-real", script)
        self.assertIn("-boot_image any replay", script)
        self.assertIn("/autoinstall.yaml", script)
        self.assertIn("inspect-iso.sh", script)

    def test_subiquity_validator_is_full_sha_pinned(self) -> None:
        script = (SCRIPTS / "validate-autoinstall.sh").read_text(encoding="utf-8")
        self.assertIn("9b41f1418858e38f88ba2724f540389b3fa41a0a", script)
        self.assertIn("rev-parse HEAD", script)

    def test_ubuntu_keyring_fingerprints_use_an_isolated_gpg_home(self) -> None:
        script = (SCRIPTS / "fetch-ubuntu-base.sh").read_text(encoding="utf-8")
        self.assertIn('mkdir -p -- "$temporary/gnupg"', script)
        self.assertIn('chmod 0700 "$temporary/gnupg"', script)
        self.assertIn('--homedir "$temporary/gnupg"', script)
        self.assertIn("--show-keys --with-fingerprint", script)
        self.assertNotIn("--show-keys --fingerprint", script)
        self.assertIn('>"$temporary/keyring-colons"', script)


class BootMetadataTests(unittest.TestCase):
    BASE = """El Torito catalog  : 489  1
El Torito cat path : /boot.catalog
El Torito images   :   N  Pltf  B   Emul  Ld_seg  Hdpt  Ldsiz         LBA
El Torito boot img :   1  BIOS  y   none  0x0000  0x00      4         490
El Torito boot img :   2  UEFI  y   none  0x0000  0x00  10296     1422048
El Torito img path :   1  /boot/grub/i386-pc/eltorito.img
El Torito img opts :   1  boot-info-table grub2-boot-info
El Torito img blks :   2  2574
"""

    def test_equivalence_ignores_only_layout_lbas(self) -> None:
        remastered = (
            self.BASE.replace("489  1", "900  1")
            .replace("490\n", "901\n")
            .replace("1422048\n", "1422088\n")
        )
        self.assertTrue(compare_boot.compare_reports(self.BASE, remastered))

    def test_equivalence_rejects_missing_uefi_or_changed_boot_shape(self) -> None:
        with self.assertRaises(compare_boot.BootMetadataError):
            compare_boot.compare_reports(self.BASE, self.BASE.replace("UEFI", "BIOS"))
        with self.assertRaises(compare_boot.BootMetadataError):
            compare_boot.compare_reports(self.BASE, self.BASE.replace("10296", "20480"))
        with self.assertRaises(compare_boot.BootMetadataError):
            compare_boot.compare_reports(
                self.BASE, self.BASE.replace("489  1", "900  2")
            )

    def test_hidden_boot_image_requires_matching_block_metadata(self) -> None:
        with self.assertRaises(compare_boot.BootMetadataError):
            compare_boot.compare_reports(self.BASE, self.BASE.replace("2574", "2575"))
        with self.assertRaises(compare_boot.BootMetadataError):
            compare_boot.compare_reports(
                self.BASE,
                self.BASE.replace("El Torito img blks :   2  2574\n", ""),
            )

    def test_rejects_non_positive_physical_lbas(self) -> None:
        with self.assertRaises(compare_boot.BootMetadataError):
            compare_boot.parse_report(self.BASE.replace("489  1", "0  1"))
        with self.assertRaises(compare_boot.BootMetadataError):
            compare_boot.parse_report(self.BASE.replace("1422048\n", "0\n"))

    def test_rejects_duplicate_el_torito_records(self) -> None:
        duplicate_reports = (
            self.BASE.replace(
                "El Torito cat path : /boot.catalog\n",
                "El Torito cat path : /boot.catalog\n" * 2,
            ),
            self.BASE.replace(
                "El Torito img path :   1  /boot/grub/i386-pc/eltorito.img\n",
                "El Torito img path :   1  /boot/grub/i386-pc/eltorito.img\n" * 2,
            ),
            self.BASE.replace(
                "El Torito img opts :   1  boot-info-table grub2-boot-info\n",
                "El Torito img opts :   1  boot-info-table grub2-boot-info\n" * 2,
            ),
            self.BASE
            + "El Torito img id   :   1  legacy\n"
            + "El Torito img id   :   1  legacy\n",
        )
        for report in duplicate_reports:
            with self.subTest(report=report):
                with self.assertRaises(compare_boot.BootMetadataError):
                    compare_boot.parse_report(report)

    def test_hidden_boot_extent_matches_authenticated_base_bytes(self) -> None:
        base_report = self.BASE.replace(
            "0x00  10296     1422048\n", "0x00      4           2\n"
        ).replace("El Torito img blks :   2  2574", "El Torito img blks :   2  1")
        custom_report = base_report.replace(
            "0x00      4           2\n", "0x00      4           3\n"
        )
        sector = b"\0" * 2048

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_iso = root / "base.iso"
            custom_iso = root / "custom.iso"
            base_iso.write_bytes(sector * 2 + b"E" * 2048)
            custom_iso.write_bytes(sector * 3 + b"E" * 2048)

            self.assertTrue(
                compare_boot.compare_reports(
                    base_report,
                    custom_report,
                    base_iso=base_iso,
                    custom_iso=custom_iso,
                )
            )

            custom_iso.write_bytes(sector * 3 + b"X" + b"E" * 2047)
            with self.assertRaises(compare_boot.BootMetadataError):
                compare_boot.compare_reports(
                    base_report,
                    custom_report,
                    base_iso=base_iso,
                    custom_iso=custom_iso,
                )

            custom_iso.write_bytes(sector * 3 + b"E" * 2047)
            with self.assertRaises(compare_boot.BootMetadataError):
                compare_boot.compare_reports(
                    base_report,
                    custom_report,
                    base_iso=base_iso,
                    custom_iso=custom_iso,
                )


class CiBarrierPackageTests(unittest.TestCase):
    def test_debian_package_is_deterministic_and_has_blocking_postinst(self) -> None:
        package = ci_barrier_deb.build_package()
        self.assertEqual(package, ci_barrier_deb.build_package())
        self.assertTrue(package.startswith(b"!<arch>\n"))

        members: dict[str, bytes] = {}
        offset = 8
        while offset < len(package):
            header = package[offset : offset + 60]
            self.assertEqual(header[58:60], b"`\n")
            name = header[:16].decode("ascii").strip().removesuffix("/")
            size = int(header[48:58].decode("ascii").strip())
            offset += 60
            members[name] = package[offset : offset + size]
            offset += size + size % 2

        self.assertEqual(
            set(members), {"debian-binary", "control.tar.gz", "data.tar.gz"}
        )
        self.assertEqual(members["debian-binary"], b"2.0\n")
        with tarfile.open(
            fileobj=io.BytesIO(members["control.tar.gz"]), mode="r:gz"
        ) as archive:
            postinst = archive.extractfile("./postinst")
            self.assertIsNotNone(postinst)
            self.assertIn(
                b"/usr/lib/gladys-installer/apt-power-loss-barrier.py",
                postinst.read(),
            )


if __name__ == "__main__":
    unittest.main()

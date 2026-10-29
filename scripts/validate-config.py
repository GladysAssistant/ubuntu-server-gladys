#!/usr/bin/env python3
"""Validate production and CI Autoinstall against authenticated ISO inputs."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import jsonschema
import yaml

import buildlib


class ConfigurationError(ValueError):
    pass


CANONICAL_VALIDATOR_SOURCE_ID = "synthesized"
PRODUCTION_INTERACTIVE_SECTIONS = ["locale", "keyboard", "storage", "identity", "ssh"]
# Skips the storage screen on single internal disk machines, and never fails.
SINGLE_DISK_EARLY_COMMAND = [
    "sh",
    "-c",
    "python3 /cdrom/gladys/single-disk-storage.py /autoinstall.yaml || true",
]
# Grows the guided root volume, which the storage screen sizes to half the
# disk, over its whole volume group. Never fails.
GROW_ROOT_LATE_COMMAND = [
    "sh",
    "-c",
    'lv=$(findmnt -n -o SOURCE /target) && '
    '[ "$(lvs --noheadings -o lv_name "$lv" 2>/dev/null | tr -d " ")" = ubuntu-lv ] && '
    '[ "$(lvs --noheadings -o vg_free_count "$lv" | tr -d " ")" -gt 0 ] && '
    'lvextend --resizefs -l +100%FREE "$lv" || true',
]


def _load(path: Path) -> dict:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"unable to load {path}") from exc
    if not isinstance(document, dict) or set(document) != {"autoinstall"}:
        raise ConfigurationError(f"{path} must contain only the top-level autoinstall key")
    if not isinstance(document["autoinstall"], dict):
        raise ConfigurationError(f"{path} autoinstall value must be a mapping")
    return document["autoinstall"]


def _safety_checks(config: dict) -> None:
    if config.get("interactive-sections") != PRODUCTION_INTERACTIVE_SECTIONS:
        raise ConfigurationError(
            "production must make locale, keyboard, storage, identity, and ssh interactive"
        )
    if config.get("locale") != "en_US.UTF-8":
        raise ConfigurationError("production locale default must be English")
    # Subiquity applies an interactive keyboard choice only when it differs
    # from the Autoinstall value, so a preset layout could never be loaded.
    if "keyboard" in config:
        raise ConfigurationError("production must leave the keyboard to the interactive screen")
    if config.get("source", {}).get("id") != "ubuntu-server-minimal":
        raise ConfigurationError("production must select ubuntu-server-minimal")
    # The early command adds the disk match itself, and only for a single
    # internal disk: production must never preselect a disk or a passphrase.
    if config.get("storage") != {"layout": {"name": "lvm", "sizing-policy": "all"}}:
        raise ConfigurationError(
            "production storage layout must be unencrypted whole-disk LVM with no disk match"
        )
    if config.get("early-commands") != [SINGLE_DISK_EARLY_COMMAND]:
        raise ConfigurationError("production early commands must only run the single disk storage check")
    if "identity" in config:
        raise ConfigurationError("production must not embed a known identity")
    if config.get("ssh") != {"install-server": True}:
        raise ConfigurationError("the SSH screen default must install the server")
    if config.get("drivers") != {"install": False}:
        raise ConfigurationError(
            "third-party drivers must stay uninstalled: the ISO ships without them"
        )
    user_data = config.get("user-data", {})
    if user_data.get("users") != [] or user_data.get("disable_root") is not True:
        raise ConfigurationError("production must contain no ordinary user and must lock root")
    if "ssh_pwauth" in user_data:
        raise ConfigurationError("SSH password authentication must follow the SSH screen")
    commands = config.get("late-commands", [])
    if GROW_ROOT_LATE_COMMAND not in commands:
        raise ConfigurationError("late commands must grow the root volume over its volume group")
    rendered = "\n".join(
        command if isinstance(command, str) else " ".join(command) for command in commands
    )
    try:
        verify_index = rendered.index("sha256sum -c gladys-payload.tar.sha256")
        extract_index = rendered.index("tar --extract")
    except ValueError as exc:
        raise ConfigurationError("late commands must verify and extract the payload") from exc
    if verify_index >= extract_index:
        raise ConfigurationError("payload extraction occurs before verification")


def _require_authoritative_iso_schema(schema: object) -> dict:
    required_properties = {
        "apt",
        "identity",
        "interactive-sections",
        "late-commands",
        "shutdown",
        "source",
        "ssh",
        "storage",
        "user-data",
        "version",
    }
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ConfigurationError("ISO Autoinstall schema must describe an object")
    properties = schema.get("properties")
    required = schema.get("required")
    if (
        not isinstance(properties, dict)
        or not required_properties.issubset(properties)
        or not isinstance(required, list)
        or "version" not in required
    ):
        raise ConfigurationError("ISO Autoinstall schema is missing required structure")
    version = properties.get("version")
    if not isinstance(version, dict) or version.get("type") != "integer":
        raise ConfigurationError("ISO Autoinstall schema has an invalid version property")
    if version.get("minimum") != 1 or version.get("maximum") != 1:
        raise ConfigurationError("ISO Autoinstall schema has an unsupported version range")
    return schema


def validate_repository_configs(
    production_path: Path,
    ci_path: Path,
    schema_path: Path,
    catalog_path: Path,
    *,
    enforce_safety: bool = True,
) -> None:
    production = _load(production_path)
    ci = _load(ci_path)
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError("unable to load ISO Autoinstall schema") from exc
    schema = _require_authoritative_iso_schema(schema)
    for path, config in ((production_path, production), (ci_path, ci)):
        try:
            jsonschema.validate(config, schema)
        except jsonschema.ValidationError as exc:
            raise ConfigurationError(f"{path} failed the ISO Subiquity schema: {exc.message}") from exc
    production_comparable = copy.deepcopy(production)
    ci_comparable = copy.deepcopy(ci)
    if (
        production_comparable.pop("interactive-sections", None)
        != PRODUCTION_INTERACTIVE_SECTIONS
    ):
        raise ConfigurationError(
            "production interactive sections must contain locale, keyboard, storage, "
            "identity, and ssh"
        )
    if ci_comparable.pop("interactive-sections", None) != []:
        raise ConfigurationError("CI interactive sections must be empty")
    # Unattended CI media answer the identity screen with a throwaway account.
    if not isinstance(ci_comparable.pop("identity", None), dict):
        raise ConfigurationError("CI must provide the account the identity screen collects")
    if production_comparable != ci_comparable:
        raise ConfigurationError(
            "production and CI Autoinstall differ outside interactivity and the CI identity"
        )
    try:
        buildlib.require_minimal_source(catalog_path.read_text(encoding="utf-8"))
    except buildlib.BuildInputError as exc:
        raise ConfigurationError(str(exc)) from exc
    if enforce_safety:
        _safety_checks(production)


def write_canonical_validator_projections(
    production_path: Path,
    ci_path: Path,
    output_dir: Path,
) -> None:
    """Write temporary inputs for Canonical's synthetic source catalog.

    The pinned controller validator cannot accept the authenticated ISO source
    catalog and documents ``synthesized`` as its only valid source ID. The real
    configurations are validated first against the ISO schema and catalog; these
    projections change only that already-validated ID.
    """

    try:
        output_dir.mkdir(mode=0o700)
    except OSError as exc:
        raise ConfigurationError(
            f"unable to create Canonical validator output directory {output_dir}"
        ) from exc

    for input_path in (production_path, ci_path):
        config = copy.deepcopy(_load(input_path))
        source = config.get("source")
        if not isinstance(source, dict) or source.get("id") != "ubuntu-server-minimal":
            raise ConfigurationError(
                f"{input_path} must select ubuntu-server-minimal before projection"
            )
        source["id"] = CANONICAL_VALIDATOR_SOURCE_ID
        output_path = output_dir / input_path.name
        try:
            output_path.write_text(
                yaml.safe_dump({"autoinstall": config}, sort_keys=False),
                encoding="utf-8",
                newline="\n",
            )
        except OSError as exc:
            raise ConfigurationError(f"unable to write {output_path}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production", required=True, type=Path)
    parser.add_argument("--ci", required=True, type=Path)
    parser.add_argument("--schema", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--canonical-output-dir", type=Path)
    args = parser.parse_args()
    validate_repository_configs(args.production, args.ci, args.schema, args.catalog)
    if args.canonical_output_dir is not None:
        write_canonical_validator_projections(
            args.production,
            args.ci,
            args.canonical_output_dir,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

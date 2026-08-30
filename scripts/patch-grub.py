#!/usr/bin/env python3
"""Replace the unambiguous Canonical server entry with the Gladys entry."""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


MENU_START = re.compile(
    r"^[ \t]*menuentry[ \t]+(['\"])(.*?)\1[^\n]*\{", re.MULTILINE
)
LINUX_LINE = re.compile(
    r"^(?P<indent>[ \t]*)linux(?:efi)?[ \t]+/casper/vmlinuz(?:[ \t].*)?$",
    re.MULTILINE,
)
INITRD_LINE = re.compile(
    r"^[ \t]*initrd(?:efi)?[ \t]+/casper/initrd(?:[ \t].*)?$", re.MULTILINE
)
CANONICAL_INSTALLER_TITLE = "Try or Install Ubuntu Server"
BOOT_SETTING = re.compile(
    r"^[ \t]*set[ \t]+(?P<name>default|timeout_style|timeout)[ \t]*=[ \t]*"
    r"(?P<value>[^\s#;]+)\s*(?:#.*)?$",
    re.MULTILINE,
)
BOOT_POLICY = {
    "default": "0",
    "timeout_style": "menu",
    "timeout": "3",
}
BOOT_POLICY_LINES = (
    "set default=0",
    "set timeout_style=menu",
    "set timeout=3",
)


class GrubPatchError(ValueError):
    """The upstream GRUB configuration cannot be patched unambiguously."""


@dataclass(frozen=True)
class MenuEntry:
    start: int
    end: int
    title: str
    text: str


def _brace_delta(text: str) -> int:
    delta = 0
    quote: str | None = None
    escaped = False
    comment = False
    for character in text:
        if comment:
            if character == "\n":
                comment = False
            continue
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if quote is not None:
            if character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == "#":
            comment = True
        elif character == "{":
            delta += 1
        elif character == "}":
            delta -= 1
    return delta


def parse_menuentries(config: str) -> list[MenuEntry]:
    entries: list[MenuEntry] = []
    cursor = 0
    while True:
        match = MENU_START.search(config, cursor)
        if match is None:
            break
        depth = 0
        end = None
        for line_match in re.finditer(r".*(?:\n|$)", config[match.start() :]):
            line = line_match.group(0)
            if not line:
                break
            depth += _brace_delta(line)
            if depth < 0:
                raise GrubPatchError("GRUB menuentry has an unexpected closing brace")
            if depth == 0:
                end = match.start() + line_match.end()
                break
        if end is None:
            raise GrubPatchError(f"unbalanced GRUB menuentry: {match.group(2)}")
        entries.append(MenuEntry(match.start(), end, match.group(2), config[match.start() : end]))
        cursor = end
    return entries


def _add_autoinstall(entry: str) -> str:
    matches = list(LINUX_LINE.finditer(entry))
    if len(matches) != 1:
        raise GrubPatchError("Canonical installer entry must contain exactly one casper linux line")
    line = matches[0].group(0)
    if re.search(r"(?:^|\s)autoinstall(?:\s|$)", line):
        raise GrubPatchError("upstream Canonical entry unexpectedly already uses autoinstall")
    if re.search(r"\s---(?:\s|$)", line):
        replacement = re.sub(r"\s---(?:\s|$)", " autoinstall ---", line, count=1)
    else:
        replacement = line.rstrip() + " autoinstall"
    return entry[: matches[0].start()] + replacement + entry[matches[0].end() :]


def _non_menu_text(config: str, entries: list[MenuEntry]) -> str:
    chunks: list[str] = []
    cursor = 0
    for entry in entries:
        chunks.append(config[cursor : entry.start])
        cursor = entry.end
    chunks.append(config[cursor:])
    return "".join(chunks)


def _add_boot_policy(config: str) -> str:
    return config.rstrip("\n") + "\n" + "\n".join(BOOT_POLICY_LINES) + "\n"


def patch_grub(config: str) -> str:
    entries = parse_menuentries(config)
    candidates = [
        entry
        for entry in entries
        if entry.title == CANONICAL_INSTALLER_TITLE
        and len(LINUX_LINE.findall(entry.text)) == 1
        and len(INITRD_LINE.findall(entry.text)) == 1
    ]
    if len(candidates) != 1:
        raise GrubPatchError(
            f"expected exactly one Canonical casper installer entry, found {len(candidates)}"
        )
    source = candidates[0]
    gladys_entry = re.sub(
        r"^([ \t]*menuentry[ \t]+)(['\"])(.*?)\2",
        r'\1"Install Gladys Assistant"',
        source.text,
        count=1,
    )
    if gladys_entry == source.text:
        raise GrubPatchError("unable to replace Canonical installer menu title")
    gladys_entry = _add_autoinstall(gladys_entry)
    patched = config[: source.start] + gladys_entry + config[source.end :]
    patched = _add_boot_policy(patched)
    validate_patched_grub(patched)
    return patched


def validate_patched_grub(config: str) -> bool:
    entries = parse_menuentries(config)
    if not entries or entries[0].title != "Install Gladys Assistant":
        raise GrubPatchError("Gladys installer must be the first GRUB menuentry")
    gladys_entries = [entry for entry in entries if entry.title == "Install Gladys Assistant"]
    if len(gladys_entries) != 1:
        raise GrubPatchError(f"expected one Gladys installer entry, found {len(gladys_entries)}")
    if any(entry.title == CANONICAL_INSTALLER_TITLE for entry in entries):
        raise GrubPatchError("Canonical installer entry must not remain in Gladys media")
    casper_entries = [
        entry
        for entry in entries
        if len(LINUX_LINE.findall(entry.text)) == 1 and len(INITRD_LINE.findall(entry.text)) == 1
    ]
    if len(casper_entries) != 1 or casper_entries[0].title != "Install Gladys Assistant":
        raise GrubPatchError(
            f"expected only the Gladys casper entry, found {len(casper_entries)}"
        )
    linux_line = LINUX_LINE.search(casper_entries[0].text)
    if linux_line is None:
        raise GrubPatchError("Gladys casper entry misses its kernel line")
    if re.search(r"(?:^|\s)autoinstall(?:\s|$)", linux_line.group(0)) is None:
        raise GrubPatchError("Gladys entry does not enable Autoinstall")

    effective_settings: dict[str, str] = {}
    for match in BOOT_SETTING.finditer(_non_menu_text(config, entries)):
        effective_settings[match.group("name")] = match.group("value")
    for name, expected in BOOT_POLICY.items():
        if effective_settings.get(name) != expected:
            raise GrubPatchError(
                f"GRUB {name} must be {expected}, found {effective_settings.get(name)!r}"
            )
    meaningful_lines = [
        line.strip()
        for line in _non_menu_text(config, entries).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if tuple(meaningful_lines[-len(BOOT_POLICY_LINES) :]) != BOOT_POLICY_LINES:
        raise GrubPatchError("GRUB boot policy must be the final effective configuration")
    return True


def validate_against_base(base_config: str, patched_config: str) -> bool:
    expected = patch_grub(base_config)
    if patched_config != expected:
        raise GrubPatchError(
            "patched GRUB does not match the deterministic authenticated-base transformation"
        )
    return True


def check_grub_syntax(path: Path) -> None:
    try:
        result = subprocess.run(
            ["grub-script-check", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GrubPatchError("required command not found: grub-script-check") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "syntax check failed"
        raise GrubPatchError(f"invalid GRUB configuration {path}: {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--base", type=Path)
    args = parser.parse_args()
    check_grub_syntax(args.input)
    source = args.input.read_text(encoding="utf-8")
    if args.check:
        if args.output is not None:
            parser.error("output cannot be used with --check")
        if args.base is None:
            validate_patched_grub(source)
        else:
            check_grub_syntax(args.base)
            validate_against_base(args.base.read_text(encoding="utf-8"), source)
        return 0
    if args.base is not None:
        parser.error("--base can be used only with --check")
    if args.output is None:
        parser.error("output is required unless --check is used")
    patched = patch_grub(source)
    args.output.write_text(patched, encoding="utf-8", newline="\n")
    check_grub_syntax(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

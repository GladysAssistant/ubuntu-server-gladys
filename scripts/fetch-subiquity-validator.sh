#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

readonly SUBIQUITY_COMMIT="9b41f1418858e38f88ba2724f540389b3fa41a0a"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
OUTPUT_DIR="$ROOT_DIR/build/cache/subiquity-$SUBIQUITY_COMMIT"

if (($# == 2)) && [[ "$1" == "--output-dir" ]]; then
	OUTPUT_DIR="$2"
elif (($# != 0)); then
	printf 'Usage: %s [--output-dir DIR]\n' "${0##*/}" >&2
	exit 64
fi
command -v git >/dev/null || {
	printf 'Required command not found: git\n' >&2
	exit 69
}
if [[ ! -d "$OUTPUT_DIR/.git" ]]; then
	mkdir -p -- "$(dirname -- "$OUTPUT_DIR")"
	git clone --filter=blob:none --no-checkout \
		https://github.com/canonical/subiquity.git "$OUTPUT_DIR"
fi
git -C "$OUTPUT_DIR" fetch --depth 1 origin "$SUBIQUITY_COMMIT"
git -C "$OUTPUT_DIR" checkout --detach "$SUBIQUITY_COMMIT"
git -C "$OUTPUT_DIR" submodule update --init --recursive --depth 1
actual_commit="$(git -C "$OUTPUT_DIR" rev-parse HEAD)"
[[ "$actual_commit" == "$SUBIQUITY_COMMIT" ]] || {
	printf 'Subiquity validator commit mismatch: %s\n' "$actual_commit" >&2
	exit 65
}
printf '%s\n' "$OUTPUT_DIR"

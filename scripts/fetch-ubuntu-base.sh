#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

readonly EXPECTED_FINGERPRINT="843938DF228D22F7B3742BC0D94AA3F0EFE21092"
readonly DEFAULT_RELEASE_URL="https://releases.ubuntu.com/26.04"
readonly DEFAULT_KEYRING_URL="https://archive.ubuntu.com/ubuntu/project/ubuntu-archive-keyring.gpg"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
CACHE_DIR="$ROOT_DIR/build/cache"
METADATA_OUT="$ROOT_DIR/build/ubuntu-source.json"
METADATA_ONLY=false
RELEASE_URL="${UBUNTU_RELEASE_URL:-$DEFAULT_RELEASE_URL}"
KEYRING_URL="${UBUNTU_KEYRING_URL:-$DEFAULT_KEYRING_URL}"

usage() {
	printf 'Usage: %s [--cache-dir DIR] [--metadata-out FILE] [--metadata-only]\n' "${0##*/}"
}

while (($#)); do
	case "$1" in
	--cache-dir)
		CACHE_DIR="$2"
		shift 2
		;;
	--metadata-out)
		METADATA_OUT="$2"
		shift 2
		;;
	--metadata-only)
		METADATA_ONLY=true
		shift
		;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		printf 'Unknown argument: %s\n' "$1" >&2
		usage >&2
		exit 64
		;;
	esac
done

for command in curl gpg gpgv sha256sum python3; do
	command -v "$command" >/dev/null || {
		printf 'Required command not found: %s\n' "$command" >&2
		exit 69
	}
done

mkdir -p -- "$CACHE_DIR" "$(dirname -- "$METADATA_OUT")"
temporary="$(mktemp -d)"
cleanup() {
	rm -rf -- "$temporary"
}
trap cleanup EXIT
mkdir -p -- "$temporary/gnupg"
chmod 0700 "$temporary/gnupg"

curl_args=(--fail --show-error --silent --location --proto '=https' --tlsv1.2 --retry 4 --retry-all-errors)
curl "${curl_args[@]}" --output "$temporary/ubuntu.gpg" "$KEYRING_URL"
curl "${curl_args[@]}" --output "$temporary/SHA256SUMS" "$RELEASE_URL/SHA256SUMS"
curl "${curl_args[@]}" --output "$temporary/SHA256SUMS.gpg" "$RELEASE_URL/SHA256SUMS.gpg"

gpg --batch --quiet --no-options --homedir "$temporary/gnupg" \
	--with-colons --show-keys --with-fingerprint "$temporary/ubuntu.gpg" \
	>"$temporary/keyring-colons"
mapfile -t matching_fingerprints < <(
	awk -F: -v expected="$EXPECTED_FINGERPRINT" \
		'$1 == "fpr" && toupper($10) == expected { print toupper($10) }' \
		"$temporary/keyring-colons"
)
if ((${#matching_fingerprints[@]} != 1)); then
	printf 'Expected Ubuntu CD image signing fingerprint exactly once; found %d\n' "${#matching_fingerprints[@]}" >&2
	exit 65
fi

gpgv --status-fd 1 --keyring "$temporary/ubuntu.gpg" \
	"$temporary/SHA256SUMS.gpg" "$temporary/SHA256SUMS" >"$temporary/gpg-status"
if ! awk -v expected="$EXPECTED_FINGERPRINT" '$2 == "VALIDSIG" && toupper($3) == expected { found=1 } END { exit(found ? 0 : 1) }' "$temporary/gpg-status"; then
	printf 'SHA256SUMS was not validly signed by the expected Ubuntu CD image key\n' >&2
	exit 65
fi

python3 "$SCRIPT_DIR/buildlib.py" discover "$temporary/SHA256SUMS" "$temporary/discovery.json"
iso_filename="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["filename"])' "$temporary/discovery.json")"
iso_sha256="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["sha256"])' "$temporary/discovery.json")"
iso_path="$CACHE_DIR/$iso_filename"

if [[ "$METADATA_ONLY" == false ]]; then
	if [[ ! -f "$iso_path" ]]; then
		curl "${curl_args[@]}" --output "$temporary/$iso_filename" "$RELEASE_URL/$iso_filename"
		printf '%s  %s\n' "$iso_sha256" "$temporary/$iso_filename" | sha256sum -c --status -
		mv -- "$temporary/$iso_filename" "$iso_path"
	fi

	# A cache hit is never trusted without re-verification against authenticated metadata.
	printf '%s  %s\n' "$iso_sha256" "$iso_path" | sha256sum -c --status -
fi

python3 - "$METADATA_OUT.tmp" "$iso_filename" "$iso_sha256" "$RELEASE_URL/$iso_filename" "$iso_path" "$EXPECTED_FINGERPRINT" <<'PY'
import json
import sys

output, filename, checksum, url, path, fingerprint = sys.argv[1:]
with open(output, "w", encoding="utf-8", newline="\n") as handle:
    json.dump(
        {
            "series": "26.04",
            "architecture": "amd64",
            "filename": filename,
            "sha256": checksum,
            "url": url,
            "path": path,
            "signing_fingerprint": fingerprint,
        },
        handle,
        indent=2,
        sort_keys=True,
    )
    handle.write("\n")
PY
mv -- "$METADATA_OUT.tmp" "$METADATA_OUT"
printf '%s\n' "$iso_path"

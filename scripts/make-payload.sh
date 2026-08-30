#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
PROFILE="production"
OUTPUT_DIR="$ROOT_DIR/build/payload"
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-0}"

usage() {
	printf 'Usage: %s [--profile production|ci-smoke|ci-real] [--output-dir DIR] [--source-date-epoch EPOCH]\n' "${0##*/}"
}

while (($#)); do
	case "$1" in
	--profile)
		PROFILE="$2"
		shift 2
		;;
	--output-dir)
		OUTPUT_DIR="$2"
		shift 2
		;;
	--source-date-epoch)
		SOURCE_DATE_EPOCH="$2"
		shift 2
		;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		printf 'Unknown argument: %s\n' "$1" >&2
		exit 64
		;;
	esac
done

case "$PROFILE" in
production | ci-smoke | ci-real) ;;
*)
	printf 'Invalid build profile: %s\n' "$PROFILE" >&2
	exit 64
	;;
esac
[[ "$SOURCE_DATE_EPOCH" =~ ^[0-9]+$ ]] || {
	printf 'SOURCE_DATE_EPOCH must be a non-negative integer\n' >&2
	exit 64
}

for command in cp find install sha256sum tar; do
	command -v "$command" >/dev/null || {
		printf 'Required command not found: %s\n' "$command" >&2
		exit 69
	}
done
PYTHON_BIN="$(command -v python3 || command -v python || true)"
[[ -n "$PYTHON_BIN" ]] || {
	printf 'Required command not found: python3 or python\n' >&2
	exit 69
}

temporary="$(mktemp -d)"
cleanup() {
	rm -rf -- "$temporary"
}
trap cleanup EXIT
overlay="$temporary/root"
mkdir -p -- "$overlay" "$OUTPUT_DIR"
cp -a -- "$ROOT_DIR/payload/." "$overlay/"
find "$overlay" -name .gitkeep -type f -delete
find "$overlay" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
find "$overlay" -type d -name __pycache__ -empty -delete
install -D -m 0644 "$ROOT_DIR/VERSION" "$overlay/etc/gladys-installer/version"

if [[ "$PROFILE" == "ci-smoke" ]]; then
	install -d -m 0755 "$overlay/etc/gladys-installer"
	install -m 0644 "$ROOT_DIR/tests/fixtures/ci/test-mode" \
		"$overlay/etc/gladys-installer/test-mode"
	install -m 0644 "$ROOT_DIR/tests/fixtures/ci/test-compose.yaml" \
		"$overlay/etc/gladys-installer/test-compose.yaml"
	install -D -m 0755 "$ROOT_DIR/tests/fixtures/ci/apt-power-loss-barrier.py" \
		"$overlay/usr/lib/gladys-installer/apt-power-loss-barrier.py"
	"$PYTHON_BIN" "$ROOT_DIR/scripts/generate-ci-barrier-deb.py" \
		--output "$overlay/etc/gladys-installer/gladys-ci-dpkg-barrier_1.0_all.deb"
fi

find "$overlay" -type d -exec chmod 0755 {} +
find "$overlay" -type f -exec chmod 0644 {} +
chmod 0755 "$overlay/usr/lib/gladys-installer/"*.py "$overlay/usr/local/sbin/gladys-diagnostics"

archive="$OUTPUT_DIR/gladys-payload.tar"
tar --create --file="$archive" --directory="$overlay" \
	--sort=name --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 --numeric-owner \
	--format=gnu .
(
	cd -- "$OUTPUT_DIR"
	sha256sum gladys-payload.tar >gladys-payload.tar.sha256
)
printf '%s\n' "$archive"

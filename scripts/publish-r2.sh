#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

# Each channel has one fixed object that every publication overwrites, so the
# bucket never accumulates old images.
readonly BUCKET="gladys-assistant-os-images"
readonly OBJECT_NAME="gladys-assistant-installer-amd64.iso"

ISO=""
CHANNEL=""
while (($#)); do
	case "$1" in
	--iso)
		ISO="${2:?}"
		shift 2
		;;
	--channel)
		CHANNEL="${2:?}"
		shift 2
		;;
	*)
		printf 'Usage: %s --iso FILE --channel production|test\n' "${0##*/}" >&2
		exit 64
		;;
	esac
done
case "$CHANNEL" in
production) prefix="" ;;
test) prefix="test/" ;;
*)
	printf 'Usage: %s --iso FILE --channel production|test\n' "${0##*/}" >&2
	exit 64
	;;
esac
[[ -f "$ISO" && -f "$ISO.sha256" ]] || {
	printf 'Missing ISO or its .sha256: %s\n' "$ISO" >&2
	exit 66
}
: "${R2_ACCOUNT_ID:?}" "${R2_STORAGE_TOKEN:?}"
for command in aws curl; do
	command -v "$command" >/dev/null || {
		printf 'Required command not found: %s\n' "$command" >&2
		exit 69
	}
done

(cd -- "$(dirname -- "$ISO")" && sha256sum -c --status -- "$(basename -- "$ISO").sha256")
temporary="$(mktemp -d)"
trap 'rm -rf -- "$temporary"' EXIT
# The published checksum names the fixed object so that `sha256sum -c` works
# on the downloaded file.
printf '%s  %s\n' "$(cut -d ' ' -f 1 <"$ISO.sha256")" "$OBJECT_NAME" \
	>"$temporary/$OBJECT_NAME.sha256"

# R2 derives S3 credentials from the API token: the key ID is the token id,
# the secret is the SHA-256 of the token value. The header goes through stdin
# so that the token never appears in the process list.
token_id() {
	printf 'Authorization: Bearer %s\n' "$R2_STORAGE_TOKEN" |
		curl -fsS -H @- "https://api.cloudflare.com/client/v4/$1/tokens/verify" |
		python3 -c 'import json, sys; print(json.load(sys.stdin)["result"]["id"])'
}
AWS_ACCESS_KEY_ID="$(token_id "accounts/$R2_ACCOUNT_ID" 2>/dev/null || token_id user 2>/dev/null)" || {
	printf 'Cloudflare did not confirm R2_STORAGE_TOKEN as an active API token\n' >&2
	exit 77
}
AWS_SECRET_ACCESS_KEY="$(printf '%s' "$R2_STORAGE_TOKEN" | sha256sum | cut -d ' ' -f 1)"
[[ -z "${GITHUB_ACTIONS:-}" ]] || printf '::add-mask::%s\n' "$AWS_SECRET_ACCESS_KEY"
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION=auto
endpoint="https://$R2_ACCOUNT_ID.r2.cloudflarestorage.com"
# no-cache keeps the CDN from serving a checksum that no longer matches.
aws s3 cp "$ISO" "s3://$BUCKET/$prefix$OBJECT_NAME" \
	--endpoint-url "$endpoint" --cache-control no-cache \
	--content-type application/x-iso9660-image --only-show-errors
aws s3 cp "$temporary/$OBJECT_NAME.sha256" "s3://$BUCKET/$prefix$OBJECT_NAME.sha256" \
	--endpoint-url "$endpoint" --cache-control no-cache \
	--content-type text/plain --only-show-errors
printf 'https://download.gladysassistant.com/%s%s\n' "$prefix" "$OBJECT_NAME"

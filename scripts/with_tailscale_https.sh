#!/usr/bin/env bash
# Prepare a Tailscale HTTPS certificate, export project-specific TLS env vars,
# then exec the command passed after `--`.
set -euo pipefail

if [[ "${1:-}" == "--" ]]; then
  shift
fi

if [[ "$#" -eq 0 ]]; then
  echo "usage: $0 -- <command> [args...]" >&2
  exit 2
fi

DOMAIN="${TAILSCALE_HTTPS_DOMAIN:-${HTTPS_DOMAIN:-}}"
CERT_DIR="${TAILSCALE_HTTPS_CERT_DIR:-${HTTPS_CERT_DIR:-/private/tmp/terminal/tailscale-certs}}"
CERT_FILE="${TAILSCALE_HTTPS_CERT_FILE:-${HTTPS_CERT_FILE:-$CERT_DIR/$DOMAIN.crt}}"
KEY_FILE="${TAILSCALE_HTTPS_KEY_FILE:-${HTTPS_KEY_FILE:-$CERT_DIR/$DOMAIN.key}}"
MIN_VALIDITY="${TAILSCALE_HTTPS_MIN_VALIDITY:-336h}"
TAILSCALE_BIN="${TAILSCALE_BIN:-/Applications/Tailscale.app/Contents/MacOS/Tailscale}"

ENABLED_ENV="${TAILSCALE_HTTPS_ENABLED_ENV:-${HTTPS_ENABLED_ENV:-HTTPS}}"
CERT_ENV="${TAILSCALE_HTTPS_CERT_ENV:-${HTTPS_CERT_ENV:-SSL_CERT_FILE}}"
KEY_ENV="${TAILSCALE_HTTPS_KEY_ENV:-${HTTPS_KEY_ENV:-SSL_KEY_FILE}}"
ENABLED_VALUE="${TAILSCALE_HTTPS_ENABLED_VALUE:-1}"

if [[ -z "$DOMAIN" ]]; then
  echo "[!] TAILSCALE_HTTPS_DOMAIN (or HTTPS_DOMAIN) is required." >&2
  exit 2
fi

validate_env_name() {
  local name="$1"
  if [[ ! "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    echo "[!] invalid env var name: $name" >&2
    exit 2
  fi
}

validate_env_name "$ENABLED_ENV"
validate_env_name "$CERT_ENV"
validate_env_name "$KEY_ENV"

mkdir -p "$CERT_DIR"
mkdir -p "$(dirname "$CERT_FILE")" "$(dirname "$KEY_FILE")"

if [[ ! -x "$TAILSCALE_BIN" ]]; then
  echo "[!] Tailscale CLI not found: $TAILSCALE_BIN" >&2
  exit 1
fi

combined="$(mktemp "$CERT_DIR/combined.XXXXXX")"
cert_tmp="$(mktemp "$CERT_DIR/cert.XXXXXX")"
key_tmp="$(mktemp "$CERT_DIR/key.XXXXXX")"
trap 'rm -f "$combined" "$cert_tmp" "$key_tmp"' EXIT

"$TAILSCALE_BIN" cert \
  --min-validity "$MIN_VALIDITY" \
  --cert-file - \
  --key-file - \
  "$DOMAIN" > "$combined"

awk -v cert="$cert_tmp" -v key="$key_tmp" '
  /-----BEGIN .*PRIVATE KEY-----/ { out = key }
  /-----BEGIN CERTIFICATE-----/ { out = cert }
  out != "" { print > out }
  /-----END .*PRIVATE KEY-----/ { out = "" }
  /-----END CERTIFICATE-----/ { out = "" }
' "$combined"
rm -f "$combined"
combined=""

if [[ ! -s "$cert_tmp" || ! -s "$key_tmp" ]]; then
  echo "[!] Tailscale cert output did not include both certificate and key PEM blocks." >&2
  exit 1
fi

chmod 0644 "$cert_tmp"
chmod 0600 "$key_tmp"
mv "$cert_tmp" "$CERT_FILE"
mv "$key_tmp" "$KEY_FILE"

export "$ENABLED_ENV=$ENABLED_VALUE"
export "$CERT_ENV=$CERT_FILE"
export "$KEY_ENV=$KEY_FILE"
export TAILSCALE_HTTPS_CERT_FILE="$CERT_FILE"
export TAILSCALE_HTTPS_KEY_FILE="$KEY_FILE"
export TAILSCALE_HTTPS_DOMAIN="$DOMAIN"

if [[ -n "${TAILSCALE_HTTPS_CWD:-}" ]]; then
  cd "$TAILSCALE_HTTPS_CWD"
fi

exec "$@"

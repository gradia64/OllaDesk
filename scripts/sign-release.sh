#!/bin/bash
# Firma GPG degli artefatti di release.
#
# Per ogni file in <cartella> (default dist/) crea la firma staccata <file>.sig
# (binaria: è il formato che pacman e makepkg si aspettano), poi SHA256SUMS
# con i checksum di tutti gli artefatti e la sua firma SHA256SUMS.sig. Alla
# fine verifica tutto con la SOLA chiave pubblica del repository
# (packaging/olladesk-release-key.asc): una firma che nessuno potrebbe
# verificare con la chiave distribuita non esce da qui.
#
# Firma SEMPRE con la sottochiave della CI (CI_SIGNING_SUBKEY in
# scripts/release-keys.sh), mai con quella dei tag né con la primaria: i
# ruoli sono separati, e la verifica qui sotto rifiuta una firma fatta da
# un'altra chiave.
#
# Chiave privata, in ordine di preferenza:
#   GPG_PRIVATE_KEY   secret dell'environment «release»: la sola sottochiave
#                     della CI, esportata con «gpg --export-secret-subkeys
#                     'ID!'». Passphrase opzionale in GPG_PASSPHRASE.
#                     Importata in un portachiavi temporaneo.
#   portachiavi locale (GNUPGHOME o ~/.gnupg), per firmare a mano.
#
# Uso: scripts/sign-release.sh [cartella]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PUBKEY="${OLLADESK_RELEASE_KEY:-$ROOT/packaging/olladesk-release-key.asc}"
DIR="${1:-$ROOT/dist}"
# shellcheck source=scripts/release-keys.sh
. "$ROOT/scripts/release-keys.sh"

if [ ! -f "$PUBKEY" ]; then
    echo "chiave pubblica di release mancante: $PUBKEY" >&2
    exit 1
fi

# prima di qualunque comando gpg: con la chiave nel secret si lavora in un
# portachiavi temporaneo, indipendente da quello (eventuale) della macchina
if [ -n "${GPG_PRIVATE_KEY:-}" ]; then
    GNUPGHOME="$(mktemp -d)"
    export GNUPGHOME
    trap 'gpgconf --kill gpg-agent >/dev/null 2>&1 || true; rm -rf "$GNUPGHOME"' EXIT
    chmod 700 "$GNUPGHOME"
    printf '%s\n' "$GPG_PRIVATE_KEY" | gpg --batch --quiet --import
fi

PRIMARY="$RELEASE_PRIMARY"
# la parte privata della sottochiave della CI deve esserci davvero: con
# --export-secret-subkeys la primaria è solo uno «stub» (sec#)
if ! gpg --batch --with-colons --list-secret-keys "$PRIMARY" 2>/dev/null \
        | awk -F: -v k="$CI_SIGNING_SUBKEY" '
            $1 == "ssb" && $15 != "#" { ssb = 1; next }
            $1 == "fpr" && ssb && $10 == k { found = 1 }
            { ssb = 0 }
            END { exit !found }'; then
    echo "nessuna chiave privata della sottochiave della CI $CI_SIGNING_SUBKEY (né GPG_PRIVATE_KEY né portachiavi)" >&2
    exit 1
fi

# «!»: proprio questa sottochiave, non quella che gpg sceglierebbe da sé.
GPG=(gpg --batch --yes --local-user "$CI_SIGNING_SUBKEY!")
if [ -n "${GPG_PASSPHRASE:-}" ]; then
    GPG+=(--pinentry-mode loopback --passphrase-fd 3)
fi

cd "$DIR"
shopt -s nullglob
files=()
for f in *; do
    case "$f" in
        *.sig|*.asc|SHA256SUMS) continue ;;
    esac
    [ -f "$f" ] && files+=("$f")
done
if [ ${#files[@]} -eq 0 ]; then
    echo "nessun artefatto da firmare in $DIR" >&2
    exit 1
fi

sign() {   # firma staccata binaria: $1 → $1.sig
    if [ -n "${GPG_PASSPHRASE:-}" ]; then
        "${GPG[@]}" --detach-sign --output "$1.sig" "$1" 3<<<"$GPG_PASSPHRASE"
    else
        "${GPG[@]}" --detach-sign --output "$1.sig" "$1"
    fi
}

for f in "${files[@]}"; do
    sign "$f"
done
sha256sum "${files[@]}" > SHA256SUMS
sign SHA256SUMS

# Verifica con la sola chiave pubblica: firmata dalla sottochiave della CI
# (terzo campo di VALIDSIG) della chiave di rilascio (ultimo campo).
VERIFY_HOME="$(mktemp -d)"
chmod 700 "$VERIFY_HOME"
gpg --homedir "$VERIFY_HOME" --batch --quiet --import "$PUBKEY"
for f in "${files[@]}" SHA256SUMS; do
    if ! gpg --homedir "$VERIFY_HOME" --batch --status-fd 1 --verify "$f.sig" "$f" 2>/dev/null \
            | grep -qE "^\[GNUPG:\] VALIDSIG $CI_SIGNING_SUBKEY .* $PRIMARY\$"; then
        echo "verifica fallita: $f (firma non della sottochiave della CI $CI_SIGNING_SUBKEY)" >&2
        gpgconf --homedir "$VERIFY_HOME" --kill gpg-agent >/dev/null 2>&1 || true
        rm -rf "$VERIFY_HOME"
        exit 1
    fi
done
gpgconf --homedir "$VERIFY_HOME" --kill gpg-agent >/dev/null 2>&1 || true
rm -rf "$VERIFY_HOME"

echo "firmati con la sottochiave della CI $CI_SIGNING_SUBKEY (chiave di rilascio $PRIMARY):"
printf '  %s\n' "${files[@]}" SHA256SUMS

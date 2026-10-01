#!/bin/bash
# Firma GPG degli artefatti di release.
#
# Per ogni file in <cartella> (default dist/) crea la firma staccata <file>.sig
# (binaria: è il formato che pacman e makepkg si aspettano), poi SHA256SUMS
# con i checksum di tutti gli artefatti e la sua firma SHA256SUMS.sig.
#
# La chiave usata deve coincidere con quella pubblica del repository
# (packaging/olladesk-release-key.asc): il controllo evita di pubblicare
# firme che nessuno potrebbe verificare con la chiave distribuita.
#
# Chiave privata, in ordine di preferenza:
#   GPG_PRIVATE_KEY   chiave armored (secret della CI); passphrase opzionale
#                     in GPG_PASSPHRASE. Importata in un GNUPGHOME temporaneo.
#   portachiavi locale (GNUPGHOME o ~/.gnupg): la chiave con l'impronta della
#                     chiave pubblica del repository; gpg chiede la passphrase.
#
# Uso: scripts/sign-release.sh [cartella]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PUBKEY="${OLLADESK_RELEASE_KEY:-$ROOT/packaging/olladesk-release-key.asc}"
DIR="${1:-$ROOT/dist}"

if [ ! -f "$PUBKEY" ]; then
    echo "chiave pubblica di release mancante: $PUBKEY" >&2
    echo "generala con scripts/gen-release-key.sh (vedi packaging/README.md)" >&2
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

FPR="$(gpg --show-keys --with-colons "$PUBKEY" | awk -F: '$1 == "fpr" { print $10; exit }')"
if [ -z "$FPR" ]; then
    echo "impronta non leggibile da $PUBKEY" >&2
    exit 1
fi

GPG=(gpg --batch --yes --local-user "$FPR!")
if [ -n "${GPG_PASSPHRASE:-}" ]; then
    GPG+=(--pinentry-mode loopback --passphrase-fd 3)
fi
if ! gpg --list-secret-keys "$FPR" >/dev/null 2>&1; then
    echo "chiave privata $FPR non disponibile (né GPG_PRIVATE_KEY né portachiavi)" >&2
    exit 1
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

# verifica immediata con la SOLA chiave pubblica del repository
VERIFY_HOME="$(mktemp -d)"
chmod 700 "$VERIFY_HOME"
gpg --homedir "$VERIFY_HOME" --batch --quiet --import "$PUBKEY"
for f in "${files[@]}" SHA256SUMS; do
    gpg --homedir "$VERIFY_HOME" --batch --quiet --verify "$f.sig" "$f" 2>/dev/null \
        || { echo "verifica fallita: $f" >&2; rm -rf "$VERIFY_HOME"; exit 1; }
done
gpgconf --homedir "$VERIFY_HOME" --kill gpg-agent >/dev/null 2>&1 || true
rm -rf "$VERIFY_HOME"

echo "firmati con $FPR:"
printf '  %s\n' "${files[@]}" SHA256SUMS

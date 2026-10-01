#!/bin/bash
# Pubblica PKGBUILD e .SRCINFO (da build/aur/, vedi prepare-aur.sh e
# build-arch.sh) nel repository AUR del pacchetto «olladesk».
#
# Chiave SSH dell'account AUR:
#   AUR_SSH_PRIVATE_KEY   chiave privata (secret della CI), scritta in un file
#                         temporaneo 0600 e cancellata alla fine
#   altrimenti            la configurazione SSH dell'utente (uso locale)
#
# La chiave host di aur.archlinux.org è fissata qui sotto (verificata contro le
# impronte pubblicate su https://aur.archlinux.org): niente fiducia al primo
# contatto. Il primo push crea il pacchetto su AUR.
#
# Uso: scripts/publish-aur.sh [cartella]   (default build/aur)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
AUR="${1:-build/aur}"
AUR_REMOTE="${AUR_REMOTE:-ssh://aur@aur.archlinux.org/olladesk.git}"   # override solo per i test
AUR_HOSTKEY="aur.archlinux.org ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEuBKrPzbawxA/k2g6NcyV5jmqwJ2s+zpgZGZ7tpLIcN"

for f in PKGBUILD .SRCINFO; do
    [ -f "$AUR/$f" ] || { echo "manca $AUR/$f (prepare-aur.sh + build-arch.sh)" >&2; exit 1; }
done
VERSION="$(sed -n 's/^pkgver=//p' "$AUR/PKGBUILD")"

# le sorgenti devono essere scaricabili da CHIUNQUE (senza token): con un
# repository GitHub privato gli URL della release rispondono 404 e il
# pacchetto AUR sarebbe impossibile da costruire. Meglio non pubblicarlo.
if [ -z "${AUR_SKIP_SOURCE_CHECK:-}" ]; then
    while read -r url; do
        code="$(curl -s -o /dev/null -w '%{http_code}' -L -r 0-0 "$url" || true)"
        case "$code" in
            200|206) ;;
            *) echo "sorgente non scaricabile pubblicamente (HTTP $code): $url" >&2
               echo "il repository GitHub è pubblico e la release è pubblicata?" >&2
               exit 1 ;;
        esac
    done < <(sed -n 's/^\tsource = //p' "$AUR/.SRCINFO")
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
printf '%s\n' "$AUR_HOSTKEY" > "$TMP/known_hosts"
SSH_CMD="ssh -o UserKnownHostsFile=$TMP/known_hosts -o StrictHostKeyChecking=yes"
if [ -n "${AUR_SSH_PRIVATE_KEY:-}" ]; then
    (umask 077 && printf '%s\n' "$AUR_SSH_PRIVATE_KEY" > "$TMP/id_aur")
    SSH_CMD="$SSH_CMD -i $TMP/id_aur -o IdentitiesOnly=yes"
fi
export GIT_SSH_COMMAND="$SSH_CMD"

git clone --quiet "$AUR_REMOTE" "$TMP/repo"
cp "$AUR/PKGBUILD" "$AUR/.SRCINFO" "$TMP/repo/"
cd "$TMP/repo"
git add PKGBUILD .SRCINFO
if git diff --cached --quiet; then
    echo "AUR già aggiornato a olladesk $VERSION: niente da pubblicare"
    exit 0
fi
git -c user.name="${AUR_GIT_NAME:-gradia}" -c user.email="${AUR_GIT_EMAIL:-gradia@disroot.org}" \
    commit --quiet -m "olladesk $VERSION"
git push --quiet origin HEAD:master
echo "pubblicato su AUR: olladesk $VERSION"

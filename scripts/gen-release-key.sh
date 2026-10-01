#!/bin/bash
# Genera la chiave GPG dedicata alle release di OllaDesk (da eseguire UNA volta,
# in locale, da chi pubblica le release).
#
# - portachiavi separato (default ~/.local/share/olladesk-release-gnupg), così
#   la chiave di release non si mescola con quelle personali;
# - ed25519, solo firma, scadenza 3 anni (prorogabile con gpg --quick-set-expire);
# - gpg chiede la passphrase con il suo pinentry: la stessa andrà nel secret
#   GPG_PASSPHRASE della CI;
# - scrive la chiave pubblica in packaging/olladesk-release-key.asc (da
#   committare) e la privata in un file 0600 da caricare come secret e poi
#   CANCELLARE (vedi le istruzioni stampate alla fine).
#
# Uso: scripts/gen-release-key.sh "Nome <email>"
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UID_STR="${1:-}"
if [ -z "$UID_STR" ]; then
    echo "uso: $0 \"Nome <email>\"" >&2
    exit 1
fi

KEYRING="${OLLADESK_KEYRING:-$HOME/.local/share/olladesk-release-gnupg}"
PUBOUT="$ROOT/packaging/olladesk-release-key.asc"
SECOUT="$KEYRING/olladesk-release-private.asc"

if [ -e "$PUBOUT" ]; then
    echo "esiste già $PUBOUT: la chiave di release è già stata generata." >&2
    echo "Cambiare chiave invalida le firme verificate dagli utenti: rimuovi il" >&2
    echo "file a mano solo se è proprio quello che vuoi." >&2
    exit 1
fi

mkdir -p "$KEYRING"
chmod 700 "$KEYRING"
export GNUPGHOME="$KEYRING"

gpg --quick-generate-key "$UID_STR (OllaDesk release signing)" ed25519 sign 3y
FPR="$(gpg --list-secret-keys --with-colons | awk -F: '$1 == "fpr" { print $10 }' | tail -1)"

gpg --armor --export "$FPR" > "$PUBOUT"
umask 077
gpg --armor --export-secret-keys "$FPR" > "$SECOUT"

cat <<EOF

Chiave di release creata: $FPR
  portachiavi:      $KEYRING
  chiave pubblica:  $PUBOUT   (da committare)
  chiave privata:   $SECOUT   (NON committare)

Prossimi passi:
  1. carica i secret nel repository GitHub:
       gh secret set GPG_PRIVATE_KEY < "$SECOUT"
       gh secret set GPG_PASSPHRASE          # incolla la passphrase scelta
  2. cancella la copia esportata della privata (resta nel portachiavi):
       shred -u "$SECOUT"
  3. fai un backup del portachiavi $KEYRING in un luogo sicuro e offline
  4. committa packaging/olladesk-release-key.asc

Per firmare in locale: GNUPGHOME="$KEYRING" scripts/sign-release.sh
EOF

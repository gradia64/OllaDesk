#!/bin/bash
# Verifica che un tag di rilascio sia annotato e firmato da una sottochiave
# di firma dei TAG (scripts/release-keys.sh), cioè dal maintainer. Non basta
# una chiave qualunque della chiave di rilascio: né la sottochiave della CI,
# che firma gli allegati, né la primaria, che resta offline, devono poter
# firmare un tag che passi da qui.
#
# Il pacchetto AUR si costruisce dal tag (?signed nel PKGBUILD): la CI non
# pubblica nulla per un tag non firmato, o firmato con un'altra chiave.
#
# La verifica usa un portachiavi temporaneo con la SOLA chiave pubblica del
# repository: una chiave presente nel portachiavi della macchina non conta.
#
# Uso: scripts/verify-tag.sh v0.2.5
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
TAG="${1:?uso: scripts/verify-tag.sh <tag>}"
PUBKEY="${OLLADESK_RELEASE_KEY:-$ROOT/packaging/olladesk-release-key.asc}"
# shellcheck source=scripts/release-keys.sh
. "$ROOT/scripts/release-keys.sh"

if [ "$(git cat-file -t "refs/tags/$TAG" 2>/dev/null)" != "tag" ]; then
    echo "$TAG non è un tag annotato (git tag -s): niente da verificare" >&2
    exit 1
fi

GNUPGHOME="$(mktemp -d)"
export GNUPGHOME
trap 'gpgconf --kill gpg-agent >/dev/null 2>&1 || true; rm -rf "$GNUPGHOME"' EXIT
chmod 700 "$GNUPGHOME"
gpg --batch --quiet --import "$PUBKEY"

# VALIDSIG <impronta di chi ha firmato> ... <impronta della primaria>: si
# controllano entrambe. Una firma revocata o scaduta non produce VALIDSIG.
status="$(git verify-tag --raw "$TAG" 2>&1 || true)"
signer="$(printf '%s\n' "$status" | awk -v p="$RELEASE_PRIMARY" '$2 == "VALIDSIG" && $NF == p { print $3 }')"
for allowed in $TAG_SIGNING_SUBKEYS; do
    if [ -n "$signer" ] && [ "$signer" = "$allowed" ]; then
        echo "$TAG: firmato dal maintainer (sottochiave $signer della chiave di rilascio $RELEASE_PRIMARY)"
        exit 0
    fi
done
if [ -n "$signer" ]; then
    echo "il tag $TAG è firmato da $signer, che non è una sottochiave dei tag ($TAG_SIGNING_SUBKEYS)" >&2
else
    echo "il tag $TAG non ha una firma valida della chiave di rilascio $RELEASE_PRIMARY" >&2
    printf '%s\n' "$status" | grep -E '^\[GNUPG:\] (ERRSIG|BADSIG|NO_PUBKEY|VALIDSIG|EXPKEYSIG|REVKEYSIG)' >&2 || true
fi
exit 1

#!/bin/bash
# Stampa le note di rilascio di una versione: packaging/release-notes/<X.Y.Z>.md.
#
# Sono la descrizione della release GitHub (job publish, --notes-file): si
# scrivono e si rileggono PRIMA del tag, nello stile delle release
# precedenti (sezioni «## …», voci in grassetto, link «Full Changelog» in
# fondo). Il job verify fallisce se mancano, così una release non esce più
# con la sola lista generata da GitHub.
#
# Uso: scripts/release-notes.sh 0.2.6     (anche v0.2.6)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:?uso: scripts/release-notes.sh <versione>}"
VERSION="${VERSION#v}"
NOTES="${OLLADESK_RELEASE_NOTES_DIR:-$ROOT/packaging/release-notes}/$VERSION.md"

if [ ! -s "$NOTES" ]; then
    echo "note di rilascio mancanti o vuote: $NOTES" >&2
    echo "scrivile prima del tag, nello stile delle release precedenti" >&2
    exit 1
fi
if ! grep -q '^## ' "$NOTES"; then
    echo "$NOTES: nessuna sezione «## …» (sono ancora in bozza?)" >&2
    exit 1
fi
if ! grep -qF "compare/" "$NOTES" || ! grep -qF "...v$VERSION" "$NOTES"; then
    echo "$NOTES: manca il link «Full Changelog» che termina in ...v$VERSION" >&2
    exit 1
fi
cat "$NOTES"

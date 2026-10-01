#!/bin/bash
# Tarball dei sorgenti di una release: dist/olladesk-<versione>.tar.gz
#
# È la sorgente del PKGBUILD Arch/AUR (firmata con la chiave di release e
# verificata da makepkg tramite validpgpkeys). Viene preferita all'archivio
# automatico di GitHub, i cui checksum non sono garantiti stabili nel tempo.
#
# Contenuto: i file tracciati da git al commit indicato (default HEAD), con
# prefisso olladesk-<versione>/. Riproducibile: git archive usa la data del
# commit per tutti i file e gzip -n non registra nome e data.
#
# Uso: scripts/build-source.sh [commit]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REF="${1:-HEAD}"
VERSION="$(git show "$REF:olladesk/__init__.py" | sed -n 's/^__version__ = "\(.*\)"$/\1/p')"
if [ -z "$VERSION" ]; then
    echo "versione non trovata in olladesk/__init__.py ($REF)" >&2
    exit 1
fi

# i sorgenti vengono dal commit, non dalla cartella di lavoro: modifiche non
# committate NON finiscono nel tarball, meglio saperlo subito
if [ "$REF" = "HEAD" ] && ! git diff --quiet HEAD -- 2>/dev/null; then
    echo "attenzione: ci sono modifiche non committate, il tarball usa HEAD" >&2
fi

mkdir -p dist
OUT="dist/olladesk-$VERSION.tar.gz"
git archive --format=tar --prefix="olladesk-$VERSION/" "$REF" | gzip -9n > "$OUT"
echo "$OUT"

#!/bin/bash
# Prepara il PKGBUILD per AUR dal modello packaging/arch/PKGBUILD.in.
#
# Sostituisce la versione, l'impronta della primaria di rilascio
# (RELEASE_PRIMARY in scripts/release-keys.sh) e le sottochiavi che possono
# firmare i tag (TAG_SIGNING_SUBKEYS), controllate da prepare() nel PKGBUILD. Il sorgente è il tag
# v<versione> firmato, che makepkg clona da GitHub e verifica: il tag deve
# essere già pubblicato per costruire il pacchetto. Se makepkg è disponibile
# genera anche .SRCINFO.
#
# Uso: scripts/prepare-aur.sh [cartella di uscita]   (default build/aur)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT="${1:-build/aur}"
# shellcheck source=scripts/release-keys.sh
. "$ROOT/scripts/release-keys.sh"

VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' olladesk/__init__.py)"
if [ -z "$VERSION" ]; then
    echo "versione non leggibile da olladesk/__init__.py" >&2
    exit 1
fi

mkdir -p "$OUT"
# («A» «B») per l'array _tag_subkeys del PKGBUILD; solo impronte esadecimali
SUBKEYS=""
for sub in $TAG_SIGNING_SUBKEYS; do
    if ! [[ $sub =~ ^[0-9A-F]{40}$ ]]; then
        echo "impronta della sottochiave dei tag non valida: $sub" >&2
        exit 1
    fi
    SUBKEYS="${SUBKEYS:+$SUBKEYS }'$sub'"
done
[ -n "$SUBKEYS" ] || { echo "TAG_SIGNING_SUBKEYS vuoto" >&2; exit 1; }
# solo i segnaposto del modello: @VERSION@/@DATE@ in package() appartengono
# alla manpage e vengono sostituiti da makepkg al momento della build
sed -e '/^#@/d' \
    -e "s/@PKGVER@/$VERSION/" \
    -e "s/@FINGERPRINT@/$RELEASE_PRIMARY/" \
    -e "s/@TAG_SUBKEYS@/$SUBKEYS/" \
    packaging/arch/PKGBUILD.in > "$OUT/PKGBUILD"
if grep -qE '@(PKGVER|FINGERPRINT|TAG_SUBKEYS)@' "$OUT/PKGBUILD"; then
    echo "segnaposto non sostituiti in $OUT/PKGBUILD" >&2
    exit 1
fi

# makepkg rifiuta di girare come root (anche solo per --printsrcinfo): nel
# container della CI .SRCINFO lo genera build-arch.sh con l'utente builder
rm -f "$OUT/.SRCINFO"
if command -v makepkg >/dev/null 2>&1 && [ "$(id -u)" -ne 0 ]; then
    (cd "$OUT" && makepkg --printsrcinfo > .SRCINFO.tmp && mv .SRCINFO.tmp .SRCINFO)
fi
echo "$OUT/PKGBUILD (olladesk $VERSION dal tag v$VERSION firmato, chiave $RELEASE_PRIMARY)"

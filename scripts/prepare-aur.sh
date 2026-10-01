#!/bin/bash
# Prepara il PKGBUILD per AUR dal modello packaging/arch/PKGBUILD.in.
#
# Sostituisce versione, sha256 del tarball sorgente (scripts/build-source.sh)
# e impronta della chiave di release; copia accanto tarball e firma, così
# makepkg li usa in locale invece di scaricarli (utile in CI prima che la
# release sia pubblicata). Se makepkg è disponibile genera anche .SRCINFO.
#
# Uso: scripts/prepare-aur.sh [cartella di uscita]   (default build/aur)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT="${1:-build/aur}"
PUBKEY="${OLLADESK_RELEASE_KEY:-packaging/olladesk-release-key.asc}"

VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' olladesk/__init__.py)"
TARBALL="dist/olladesk-$VERSION.tar.gz"
for f in "$TARBALL" "$TARBALL.sig" "$PUBKEY"; do
    if [ ! -f "$f" ]; then
        echo "manca $f (build-source.sh, sign-release.sh, gen-release-key.sh)" >&2
        exit 1
    fi
done

FPR="$(gpg --show-keys --with-colons "$PUBKEY" | awk -F: '$1 == "fpr" { print $10; exit }')"
SHA256="$(sha256sum "$TARBALL" | cut -d' ' -f1)"

mkdir -p "$OUT"
# solo i segnaposto del modello: @VERSION@/@DATE@ in package() appartengono
# alla manpage e vengono sostituiti da makepkg al momento della build
sed -e '/^#@/d' \
    -e "s/@PKGVER@/$VERSION/" \
    -e "s/@SHA256@/$SHA256/" \
    -e "s/@FINGERPRINT@/$FPR/" \
    packaging/arch/PKGBUILD.in > "$OUT/PKGBUILD"
if grep -qE '@(PKGVER|SHA256|FINGERPRINT)@' "$OUT/PKGBUILD"; then
    echo "segnaposto non sostituiti in $OUT/PKGBUILD" >&2
    exit 1
fi
cp "$TARBALL" "$TARBALL.sig" "$OUT/"

# makepkg rifiuta di girare come root (anche solo per --printsrcinfo): nel
# container della CI .SRCINFO lo genera build-arch.sh con l'utente builder
rm -f "$OUT/.SRCINFO"
if command -v makepkg >/dev/null 2>&1 && [ "$(id -u)" -ne 0 ]; then
    (cd "$OUT" && makepkg --printsrcinfo > .SRCINFO.tmp && mv .SRCINFO.tmp .SRCINFO)
fi
echo "$OUT/PKGBUILD (olladesk $VERSION, chiave $FPR)"

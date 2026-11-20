#!/bin/bash
# Bundle Flatpak di OllaDesk: dist/OllaDesk-X.Y.Z.flatpak.
#
# Costruisce il manifest packaging/flatpak/io.github.gradia64.OllaDesk.yaml
# dal checkout corrente (in CI: il tag già verificato) con flatpak-builder,
# poi esporta il bundle a file singolo. Runtime org.kde.Platform, SDK e base
# app io.qt.PySide.BaseApp arrivano da Flathub (--install-deps-from), nella
# installazione --user: niente sudo.
#
# Il bundle porta l'indirizzo del repository Flathub (--runtime-repo): chi lo
# installa con «flatpak install --user OllaDesk-X.Y.Z.flatpak» riceve da lì
# il runtime KDE, se non lo ha già.
#
# Strumenti richiesti: flatpak e flatpak-builder; senza flatpak-builder di
# sistema si usa quello di org.flatpak.Builder (Flathub), se installato.
# Uso: scripts/build-flatpak.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP_ID="io.github.gradia64.OllaDesk"
MANIFEST="packaging/flatpak/$APP_ID.yaml"
FLATHUB_REPO="https://dl.flathub.org/repo/flathub.flatpakrepo"

VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' olladesk/__init__.py)"
if [ -z "$VERSION" ]; then
    echo "versione non trovata in olladesk/__init__.py" >&2
    exit 1
fi
BUNDLE="dist/OllaDesk-$VERSION.flatpak"

command -v flatpak >/dev/null || { echo "manca flatpak" >&2; exit 1; }
if command -v flatpak-builder >/dev/null; then
    BUILDER=(flatpak-builder)
elif flatpak info org.flatpak.Builder >/dev/null 2>&1; then
    BUILDER=(flatpak run --filesystem="$ROOT" org.flatpak.Builder)
else
    echo "manca flatpak-builder (o l'app org.flatpak.Builder di Flathub)" >&2
    exit 1
fi

flatpak remote-add --user --if-not-exists flathub "$FLATHUB_REPO"

# build/ fuori dai sorgenti copiati: il manifest prende il checkout intero
# (type: dir), e flatpak-builder salta da solo .flatpak-builder
rm -rf build/flatpak build/flatpak-repo
mkdir -p dist
"${BUILDER[@]}" --user --install-deps-from=flathub --force-clean --disable-rofiles-fuse \
    --repo=build/flatpak-repo build/flatpak "$MANIFEST"
flatpak build-bundle --runtime-repo="$FLATHUB_REPO" \
    build/flatpak-repo "$BUNDLE" "$APP_ID"

echo "scritto $BUNDLE ($(du -h "$BUNDLE" | cut -f1))"

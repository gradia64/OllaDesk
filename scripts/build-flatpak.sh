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
# Strumenti richiesti: flatpak, flatpak-builder.
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

for tool in flatpak flatpak-builder; do
    command -v "$tool" >/dev/null || { echo "manca $tool" >&2; exit 1; }
done

flatpak remote-add --user --if-not-exists flathub "$FLATHUB_REPO"

# build/ fuori dai sorgenti copiati: il manifest prende il checkout intero
# (type: dir), e flatpak-builder salta da solo .flatpak-builder
rm -rf build/flatpak build/flatpak-repo
mkdir -p dist
flatpak-builder --user --install-deps-from=flathub --force-clean --disable-rofiles-fuse \
    --repo=build/flatpak-repo build/flatpak "$MANIFEST"
flatpak build-bundle --runtime-repo="$FLATHUB_REPO" \
    build/flatpak-repo "$BUNDLE" "$APP_ID"

echo "scritto $BUNDLE ($(du -h "$BUNDLE" | cut -f1))"

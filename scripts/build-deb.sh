#!/bin/bash
# Costruisce il pacchetto Debian di OllaDesk: dist/olladesk_<versione>_all.deb
#
# Layout installato:
#   /usr/share/olladesk/olladesk/    pacchetto Python (con assets)
#   /usr/share/olladesk/launcher.py  avvio (sys.path[0] = /usr/share/olladesk)
#   /usr/bin/olladesk                launcher shell → python3 launcher.py
#   /usr/share/applications/olladesk.desktop
#   /usr/share/icons/hicolor/scalable/apps/olladesk.svg
#   /usr/share/doc/olladesk/copyright
#
# La versione è letta da olladesk/__init__.py (unica fonte, come pyproject).
# Launcher e manpage sono in packaging/common/, condivisi con il PKGBUILD Arch.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' olladesk/__init__.py)"
if [ -z "$VERSION" ]; then
    echo "versione non trovata in olladesk/__init__.py" >&2
    exit 1
fi

# build riproducibile: date di changelog/manpage/mtime dall'ultimo commit
if [ -z "${SOURCE_DATE_EPOCH:-}" ]; then
    SOURCE_DATE_EPOCH="$(git log -1 --format=%ct 2>/dev/null || date +%s)"
fi
export SOURCE_DATE_EPOCH

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
PKG="$STAGE/olladesk"

mkdir -p "$PKG/usr/share/olladesk" \
         "$PKG/usr/bin" \
         "$PKG/usr/share/applications" \
         "$PKG/usr/share/icons/hicolor/scalable/apps" \
         "$PKG/usr/share/doc/olladesk" \
         "$PKG/usr/share/man/man1" \
         "$PKG/DEBIAN"

# --- codice ---------------------------------------------------------------
cp -r olladesk "$PKG/usr/share/olladesk/olladesk"
find "$PKG/usr/share/olladesk" -type d -name __pycache__ -exec rm -rf {} +
find "$PKG/usr/share/olladesk" -type f -name '*.pyc' -delete
# marcatore letto da app_update.install_method(): suggerimenti di
# aggiornamento specifici per il .deb
printf 'deb\n' > "$PKG/usr/share/olladesk/olladesk/_packaging"

# --- launcher -------------------------------------------------------------
install -m 644 packaging/common/launcher.py "$PKG/usr/share/olladesk/launcher.py"
install -m 755 packaging/common/olladesk.sh "$PKG/usr/bin/olladesk"

# --- desktop e icona -------------------------------------------------------
install -m 644 olladesk.desktop "$PKG/usr/share/applications/olladesk.desktop"
install -m 644 olladesk/assets/olladesk.svg \
    "$PKG/usr/share/icons/hicolor/scalable/apps/olladesk.svg"

# --- metadati pacchetto ----------------------------------------------------
cat > "$PKG/DEBIAN/control" <<EOF
Package: olladesk
Version: $VERSION
Section: net
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-pyside6.qtcore, python3-pyside6.qtgui, python3-pyside6.qtwidgets, python3-pyside6.qtnetwork
Recommends: python3-pypdf, python3-keyring, qt6-svg-plugins
Installed-Size: @INSTALLED_SIZE@
Maintainer: gradia <gradia@disroot.org>
Homepage: https://github.com/gradia64/OllaDesk
Description: Client desktop per Ollama in stile ChatGPT
 OllaDesk è un'interfaccia grafica nativa (PySide6/Qt) per chattare con i
 modelli LLM locali serviti da Ollama: conversazioni salvate per file,
 allegati (testo, immagini, PDF), ricerca web (DuckDuckGo/SearXNG), gestione
 dei modelli (scaricamento ed eliminazione) e tema chiaro/scuro.
 .
 Richiede un server Ollama locale o remoto (https://ollama.com).
EOF

# nessun postinst: cache di .desktop e icone sono aggiornate dai trigger di
# dpkg (desktop-file-utils, hicolor-icon-theme)

cat > "$PKG/usr/share/doc/olladesk/copyright" <<EOF
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: OllaDesk
Source: https://github.com/gradia64/OllaDesk

Files: *
Copyright: 2026 gradia <gradia@disroot.org>
License: GPL-3.0-or-later
 OllaDesk è software libero: puoi ridistribuirlo e modificarlo secondo i
 termini della GNU General Public License pubblicata dalla Free Software
 Foundation, versione 3 o successiva.
 .
 Il testo completo della licenza è disponibile in /usr/share/common-licenses/GPL-3
 oppure su https://www.gnu.org/licenses/gpl-3.0.html
EOF

# changelog e manpage compressi (policy Debian)
cat > "$STAGE/changelog" <<EOF
olladesk ($VERSION) unstable; urgency=medium

  * Release $VERSION: vedi https://github.com/gradia64/OllaDesk/releases

 -- gradia <gradia@disroot.org>  $(date -R -u -d "@$SOURCE_DATE_EPOCH")
EOF
gzip -9n -c "$STAGE/changelog" > "$PKG/usr/share/doc/olladesk/changelog.gz"

sed -e "s/@VERSION@/$VERSION/" \
    -e "s/@DATE@/$(date -u -d "@$SOURCE_DATE_EPOCH" +%Y-%m-%d)/" \
    packaging/common/olladesk.1.in > "$STAGE/olladesk.1"
gzip -9n -c "$STAGE/olladesk.1" > "$PKG/usr/share/man/man1/olladesk.1.gz"

# permessi conforme a policy: file 644, directory 755 (gli eseguibili,
# già marcati tali, conservano il bit x)
chmod -R u=rwX,go=rX "$PKG"

# dimensione installata (KiB) di TUTTO il contenuto, metadati esclusi, come
# la calcola dpkg-gencontrol: ogni file arrotondato al KiB superiore, 1 KiB
# per directory e collegamenti. Non «du»: conta i blocchi allocati, che
# cambiano con il filesystem (tmpfs, ext4…) e rendevano il .deb diverso a
# parità di contenuto.
INSTALLED_SIZE="$(find "$PKG" -path "$PKG/DEBIAN" -prune -o -mindepth 1 -printf '%y %s\n' \
    | awk '$1 == "f" { s += int(($2 + 1023) / 1024); next } { s += 1 } END { print s + 0 }')"
sed -i "s/@INSTALLED_SIZE@/$INSTALLED_SIZE/" "$PKG/DEBIAN/control"

# md5sums: permette a debsums di verificare i file installati
(cd "$PKG" && find usr -type f -print0 | LC_ALL=C sort -z | xargs -0 md5sum) \
    > "$PKG/DEBIAN/md5sums"
chmod 644 "$PKG/DEBIAN/md5sums"

# mtime uniformi: stesso .deb a ogni build dello stesso commit
find "$PKG" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +

mkdir -p dist
# compressione esplicita: il default di dpkg-deb cambia tra distribuzioni
# (xz su Debian, zstd su Ubuntu e quindi sui runner della CI): così il .deb
# pubblicato ha lo stesso formato di quello costruito in locale
dpkg-deb --build --root-owner-group -Zxz "$PKG" "dist/olladesk_${VERSION}_all.deb"

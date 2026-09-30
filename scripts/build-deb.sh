#!/bin/bash
# Costruisce il pacchetto Debian di OllaDesk: dist/olladesk_<versione>_all.deb
#
# Layout installato:
#   /usr/share/olladesk/olladesk/    pacchetto Python (con assets)
#   /usr/bin/olladesk                launcher (PYTHONPATH + python3 -m olladesk)
#   /usr/share/applications/olladesk.desktop
#   /usr/share/icons/hicolor/scalable/apps/olladesk.svg
#   /usr/share/doc/olladesk/copyright
#
# La versione è letta da olladesk/__init__.py (unica fonte, come pyproject).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' olladesk/__init__.py)"
if [ -z "$VERSION" ]; then
    echo "versione non trovata in olladesk/__init__.py" >&2
    exit 1
fi

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

# --- launcher -------------------------------------------------------------
cat > "$PKG/usr/bin/olladesk" <<'EOF'
#!/bin/sh
# Launcher OllaDesk: punta il pacchetto Python installato in /usr/share.
PYTHONPATH=/usr/share/olladesk${PYTHONPATH:+:$PYTHONPATH}
export PYTHONPATH
exec /usr/bin/python3 -m olladesk "$@"
EOF
chmod 755 "$PKG/usr/bin/olladesk"

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
Depends: python3 (>= 3.10), python3-pyside6.qtcore, python3-pyside6.qtgui, python3-pyside6.qtwidgets
Recommends: python3-pypdf, python3-keyring
Installed-Size: $(du -sk --exclude=__pycache__ olladesk | cut -f1)
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

cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database >/dev/null 2>&1 || true
fi
EOF
chmod 755 "$PKG/DEBIAN/postinst"

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

 -- gradia <gradia@disroot.org>  $(date -R)
EOF
gzip -9n -c "$STAGE/changelog" > "$PKG/usr/share/doc/olladesk/changelog.gz"

cat > "$STAGE/olladesk.1" <<EOF
.TH OLLADESK 1 "$(date +%Y-%m-%d)" "olladesk $VERSION" "Manuale utente"
.SH NOME
olladesk \\- client desktop per Ollama in stile ChatGPT
.SH SINTASSI
.B olladesk
.SH DESCRIZIONE
.B OllaDesk
è un'interfaccia grafica (PySide6/Qt) per chattare con i modelli LLM locali
serviti da un server Ollama: conversazioni salvate per file, allegati (testo,
immagini, PDF), ricerca web (DuckDuckGo/SearXNG), gestione dei modelli
(scaricamento ed eliminazione) e tema chiaro/scuro.
.PP
L'indirizzo del server e le preferenze sono configurati nell'app
(CTRL+, oppure la voce «Impostazioni» della barra laterale) e salvati
in ~/.config/olladesk/.
.SH FILE
.TP
.I ~/.config/olladesk/
Impostazioni, conversazioni e allegati incollati.
.SH VEDI ANCHE
 https://github.com/gradia64/OllaDesk
EOF
gzip -9n -c "$STAGE/olladesk.1" > "$PKG/usr/share/man/man1/olladesk.1.gz"

# permessi conforme a policy: file 644, directory 755 (gli eseguibili,
# già marcati tali, conservano il bit x)
chmod -R u=rwX,go=rX "$PKG"

mkdir -p dist
dpkg-deb --build --root-owner-group "$PKG" "dist/olladesk_${VERSION}_all.deb"

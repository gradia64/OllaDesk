#!/bin/bash
# Prova di un'AppImage già costruita, senza FUSE e senza rete.
#
#   1. controlli con il Python dell'AppImage (smoke_check.py): tipo di
#      installazione «appimage», icona SVG, formati immagine, portachiavi,
#      QR code, PDF, SSL
#   2. i test offline del repository eseguiti con lo stesso Python
#   3. avvio dell'app completa (offscreen), che deve essere ancora viva
#      dopo qualche secondo
#
# La configurazione è sempre in una cartella temporanea: niente chat,
# impostazioni o companion dell'utente.
#
# Uso: packaging/appimage/smoke-test.sh dist/OllaDesk-X.Y.Z-x86_64.AppImage
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APPIMAGE_FILE="$(readlink -f "${1:?uso: smoke-test.sh <file .AppImage>}")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export QT_QPA_PLATFORM=offscreen

cd "$TMP"
"$APPIMAGE_FILE" --appimage-extract >/dev/null
APPDIR_X="$TMP/squashfs-root"
PY="$APPDIR_X/usr/python/bin/python3"

echo "== controlli nell'AppImage"
XDG_CONFIG_HOME="$TMP/config-check" APPIMAGE="$APPIMAGE_FILE" APPDIR="$APPDIR_X" \
    "$PY" -E -s "$ROOT/packaging/appimage/smoke_check.py"

echo "== test offline con il Python dell'AppImage"
cd "$ROOT"
for t in unit_test.py engine_test.py companion_test.py companion_send_test.py \
         sync_test.py companion_models_test.py offline_app_update.py \
         repro_qthread_crash.py; do
    XDG_CONFIG_HOME="$TMP/config-$t" "$PY" -s "tests/$t" > "$TMP/$t.log" 2>&1 || {
        tail -20 "$TMP/$t.log"
        echo "$t fallito con il Python dell'AppImage" >&2
        exit 1
    }
    echo "$t: $(tail -1 "$TMP/$t.log")"
done

echo "== avvio dell'app completa"
set +e
# TMPDIR: la copia estratta dal runtime resta se il processo viene ucciso
mkdir -p "$TMP/run"
XDG_CONFIG_HOME="$TMP/config-app" APPIMAGE_EXTRACT_AND_RUN=1 TMPDIR="$TMP/run" \
    timeout 10 "$APPIMAGE_FILE" > "$TMP/app.log" 2>&1
rc=$?
set -e
if [ "$rc" -ne 124 ]; then
    cat "$TMP/app.log"
    echo "l'app è uscita da sola (codice $rc) invece di restare aperta" >&2
    exit 1
fi
if grep -Eq 'Traceback|Error|error' "$TMP/app.log"; then
    cat "$TMP/app.log"
    echo "errori nell'output dell'app" >&2
    exit 1
fi
echo "avvio OK"

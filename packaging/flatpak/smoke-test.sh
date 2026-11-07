#!/bin/bash
# Prova di un bundle Flatpak già costruito, senza rete verso l'esterno.
#
#   1. installazione del bundle nell'installazione --user (il runtime KDE
#      deve esserci già: in CI lo ha installato scripts/build-flatpak.sh)
#   2. controlli con il Python della sandbox (smoke_check.py): versione, tipo
#      di installazione «flatpak», PySide6 dalla base app, icona SVG, formati
#      immagine, portachiavi, portale, QR code, PDF, SSL
#   3. i test offline del repository eseguiti con lo stesso Python
#   4. avvio dell'app completa (offscreen), che deve essere ancora viva dopo
#      qualche secondo
#
# La configurazione è sempre in una cartella temporanea: niente chat,
# impostazioni o companion dell'utente.
#
# Uso: packaging/flatpak/smoke-test.sh dist/OllaDesk-X.Y.Z.flatpak
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUNDLE="$(readlink -f "${1:?uso: smoke-test.sh <file .flatpak>}")"
APP_ID="io.github.gradia64.OllaDesk"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$ROOT/olladesk/__init__.py")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# senza una sessione D-Bus (CI) flatpak run non ha il bus di sessione per i
# portali: se manca, tutto gira dentro una sessione temporanea
if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -z "${OLLADESK_SMOKE_DBUS:-}" ]; then
    exec env OLLADESK_SMOKE_DBUS=1 dbus-run-session -- "$0" "$@"
fi

echo "== installazione del bundle"
flatpak install --user -y --noninteractive --reinstall "$BUNDLE"
flatpak info --user --show-permissions "$APP_ID"

# run_py <cartella config> <argomenti per python3…>: il Python della sandbox,
# con il repository e la cartella temporanea visibili
run_py() {
    local cfg="$1"
    shift
    mkdir -p "$cfg"
    flatpak run --user --command=python3 \
        --filesystem="$ROOT:ro" --filesystem="$TMP" --cwd="$ROOT" \
        --env=QT_QPA_PLATFORM=offscreen --env=OLLADESK_TEST=1 --env=OLLADESK_LANG=it \
        --env=XDG_CONFIG_HOME="$cfg" --env=OLLADESK_EXPECTED_VERSION="$VERSION" \
        "$APP_ID" "$@"
}

echo "== controlli nel Flatpak"
run_py "$TMP/config-check" -s packaging/flatpak/smoke_check.py

echo "== test offline con il Python del Flatpak"
for t in unit_test.py engine_test.py companion_test.py companion_send_test.py \
         sync_test.py companion_models_test.py offline_app_update.py \
         repro_qthread_crash.py; do
    run_py "$TMP/config-$t" -s "tests/$t" > "$TMP/$t.log" 2>&1 || {
        tail -20 "$TMP/$t.log"
        echo "$t fallito con il Python del Flatpak" >&2
        exit 1
    }
    echo "$t: $(tail -1 "$TMP/$t.log")"
done

echo "== avvio dell'app completa (offscreen)"
mkdir -p "$TMP/config-app"
set +e
timeout 10 flatpak run --user --filesystem="$TMP" \
    --env=QT_QPA_PLATFORM=offscreen --env=OLLADESK_TEST=1 \
    --env=XDG_CONFIG_HOME="$TMP/config-app" "$APP_ID" > "$TMP/app.log" 2>&1
rc=$?
set -e
if [ "$rc" -ne 124 ]; then
    cat "$TMP/app.log"
    echo "l'app è uscita da sola (codice $rc) invece di restare aperta" >&2
    exit 1
fi
if grep -Eq 'Traceback|Segmentation fault|core dumped' "$TMP/app.log"; then
    cat "$TMP/app.log"
    echo "errori nell'output dell'app" >&2
    exit 1
fi
echo "avvio OK"
echo "Flatpak $VERSION: prova superata"

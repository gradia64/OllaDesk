#!/bin/sh
# Rigenera le icone della companion web (olladesk/web/) dall'icona dell'app.
#
#   icon-192.png, icon-512.png   icona normale (angoli arrotondati trasparenti)
#   icon-maskable-512.png        Android: fondo pieno, logo nella zona sicura
#                                (cerchio dell'80%), il sistema ritaglia la forma
#   apple-touch-icon.png         iOS (180 px): fondo pieno, angoli fatti dal sistema
#
# Le PNG sono nel repository: questo script serve solo se cambia l'icona.
# Richiede rsvg-convert (pacchetto librsvg2-bin).
set -eu
cd "$(dirname "$0")/.."

SRC=olladesk/assets/olladesk.svg
OUT=olladesk/web
command -v rsvg-convert >/dev/null || { echo "serve rsvg-convert (librsvg2-bin)" >&2; exit 1; }

rsvg-convert -w 192 -h 192 "$SRC" -o "$OUT/icon-192.png"
rsvg-convert -w 512 -h 512 "$SRC" -o "$OUT/icon-512.png"

TMP="$(mktemp --suffix=.svg)"
trap 'rm -f "$TMP"' EXIT
BODY="$(sed -n '/<path\|<circle/p' "$SRC")"

# versione a fondo pieno: il disegno originale (centrato in 64,64) su un
# quadrato del colore di fondo dell'icona, in scala $1
full() {
    printf '%s\n' \
        '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">' \
        '  <rect width="128" height="128" fill="#1a1d21"/>' \
        "  <g transform=\"translate(64 64) scale($1) translate(-64 -64)\">" \
        "$BODY" \
        '  </g>' \
        '</svg>' > "$TMP"
}

full 0.9   # il fumetto resta dentro il cerchio sicuro dell'80%
rsvg-convert -w 512 -h 512 "$TMP" -o "$OUT/icon-maskable-512.png"
full 1.0   # iOS arrotonda gli angoli ma non ritaglia oltre
rsvg-convert -w 180 -h 180 "$TMP" -o "$OUT/apple-touch-icon.png"
echo "icone rigenerate in $OUT"

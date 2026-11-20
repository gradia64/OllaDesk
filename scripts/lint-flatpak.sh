#!/bin/bash
# Lint del Flatpak con flatpak-builder-lint, lo strumento che usa Flathub.
#
# Controlla manifest, metadati AppStream e, dopo scripts/build-flatpak.sh,
# la cartella di build (build/flatpak) e il repository esportato
# (build/flatpak-repo). Il linter viene da org.flatpak.Builder (Flathub):
# se manca, si installa nell'installazione --user.
#
# Le eccezioni in packaging/flatpak/lint-exceptions.json valgono solo qui:
# riguardano ciò che fa solo l'infrastruttura di Flathub (copia degli
# screenshot su dl.flathub.org/media). Ognuna ha il suo motivo nel file.
#
# Uso: scripts/lint-flatpak.sh [--gha-format]   (annotazioni in GitHub Actions)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP_ID="io.github.gradia64.OllaDesk"
EXCEPTIONS="packaging/flatpak/lint-exceptions.json"
EXTRA=()
[ "${1:-}" = "--gha-format" ] && EXTRA=(--gha-format)

if ! flatpak info org.flatpak.Builder >/dev/null 2>&1; then
    flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
    flatpak install --user -y --noninteractive flathub org.flatpak.Builder
fi

lint() {
    echo "== flatpak-builder-lint $1 $2"
    flatpak run --filesystem="$ROOT" --command=flatpak-builder-lint org.flatpak.Builder \
        "${EXTRA[@]}" --exceptions --user-exceptions "$EXCEPTIONS" "$1" "$2"
}

fail=0
lint manifest "packaging/flatpak/$APP_ID.yaml" || fail=1
lint appstream "$APP_ID.metainfo.xml" || fail=1
if [ -d build/flatpak ] && [ -d build/flatpak-repo ]; then
    lint builddir build/flatpak || fail=1
    lint repo build/flatpak-repo || fail=1
else
    echo "build/flatpak assente: lancia prima scripts/build-flatpak.sh" >&2
    fail=1
fi
exit $fail

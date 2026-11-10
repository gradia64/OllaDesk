#!/bin/bash
# Pubblica su AUR il PKGBUILD di una release, dalla macchina del maintainer.
#
# La chiave SSH dell'account AUR non sta nella CI: chi ne ottenesse i secret
# potrebbe pubblicare un PKGBUILD qualsiasi per tutti i pacchetti
# dell'account. La CI costruisce e prova il pacchetto (job «arch»); il push
# lo fa il maintainer con questo script, dopo la release:
#
# 1. verifica il tag con scripts/verify-tag.sh (firmato da una sottochiave
#    dei tag, non da quella della CI);
# 2. rigenera PKGBUILD dal contenuto del tag (git archive + prepare-aur.sh);
# 3. scarica l'artefatto «arch» della run del tag riuscita e controlla che il
#    suo PKGBUILD sia identico: si pubblica solo ciò che la CI ha costruito e
#    provato, e la CI non può cambiare ciò che si pubblica;
# 4. controlla che .SRCINFO (generato da makepkg nella CI) descriva lo stesso
#    PKGBUILD: versione, sorgente e chiavi;
# 5. pubblica con scripts/publish-aur.sh e la chiave SSH dell'utente
#    (~/.ssh/config, Host aur.archlinux.org).
#
# Uso: scripts/aur-release.sh vX.Y.Z
# Per i test: OLLADESK_ARCH_ARTIFACT=<cartella> salta il download con gh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
TAG="${1:?uso: scripts/aur-release.sh vX.Y.Z}"
VERSION="${TAG#v}"
[[ $TAG =~ ^v[0-9]+(\.[0-9]+)*$ ]] || { echo "tag non valido: $TAG" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 1. il tag è del maintainer
scripts/verify-tag.sh "$TAG"

# 2. PKGBUILD dal contenuto del tag, con gli script di quel tag
mkdir "$TMP/src"
git archive "refs/tags/$TAG" | tar -x -C "$TMP/src"
"$TMP/src/scripts/prepare-aur.sh" "$TMP/aur" >/dev/null
grep -qx "pkgver=$VERSION" "$TMP/aur/PKGBUILD" \
    || { echo "il PKGBUILD del tag non è alla versione $VERSION" >&2; exit 1; }

# 3. artefatto della CI per lo stesso tag
if [ -n "${OLLADESK_ARCH_ARTIFACT:-}" ]; then
    ART="$OLLADESK_ARCH_ARTIFACT"
else
    sha="$(git rev-parse "refs/tags/$TAG^{commit}")"
    run="$(gh run list --workflow release.yml --branch "$TAG" --status success \
            --json databaseId,headSha --jq "map(select(.headSha == \"$sha\"))[0].databaseId")"
    if [ -z "$run" ] || [ "$run" = "null" ]; then
        echo "nessuna run riuscita di release.yml per $TAG ($sha): la release è stata pubblicata?" >&2
        exit 1
    fi
    gh run download "$run" --name arch --dir "$TMP/artifact"
    ART="$TMP/artifact/build/aur"
fi
for f in PKGBUILD .SRCINFO; do
    [ -f "$ART/$f" ] || { echo "manca $f nell'artefatto della CI ($ART)" >&2; exit 1; }
done
if ! cmp -s "$TMP/aur/PKGBUILD" "$ART/PKGBUILD"; then
    echo "il PKGBUILD della CI è diverso da quello generato dal tag:" >&2
    diff -u "$TMP/aur/PKGBUILD" "$ART/PKGBUILD" >&2 || true
    exit 1
fi

# 4. .SRCINFO coerente con il PKGBUILD (è quello che AUR mostra e indicizza)
srcinfo="$ART/.SRCINFO"
primary="$(sed -n "s/^validpgpkeys=('\([0-9A-F]*\)').*/\1/p" "$TMP/aur/PKGBUILD")"
url="$(sed -n 's/^url="\(.*\)"$/\1/p' "$TMP/aur/PKGBUILD")"
expect=(
    "pkgbase = olladesk"
    "	pkgver = $VERSION"
    "	source = olladesk::git+$url.git#tag=v$VERSION?signed"
    "	validpgpkeys = $primary"
)
for line in "${expect[@]}"; do
    grep -qxF "$line" "$srcinfo" || { echo ".SRCINFO della CI senza «${line#	}»" >&2; exit 1; }
done
if [ "$(grep -c '^	source = ' "$srcinfo")" -ne 1 ] || [ "$(grep -c '^	validpgpkeys = ' "$srcinfo")" -ne 1 ]; then
    echo ".SRCINFO della CI con sorgenti o chiavi in più" >&2
    exit 1
fi
cp "$srcinfo" "$TMP/aur/.SRCINFO"

# 5. pubblicazione con la chiave SSH dell'utente
scripts/publish-aur.sh "$TMP/aur"

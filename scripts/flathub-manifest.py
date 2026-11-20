#!/usr/bin/env python3
"""Manifest per Flathub, generato da quello del bundle per un tag.

Il manifest in packaging/flatpak costruisce dal checkout (`type: dir`); su
Flathub il sorgente deve essere fisso. Questo script scrive in
build/flathub/ (o --out):

- io.github.gradia64.OllaDesk.yaml: lo stesso manifest, con il sorgente
  dell'app `type: git` (url, tag, commit del tag) più x-checker-data, così
  il bot di Flathub propone da solo l'aggiornamento a ogni nuovo tag;
  il marcatore di install_method diventa "flathub" (niente avviso
  di nuove versioni dall'app: aggiorna il software center);
- flathub.json: solo x86_64 (cffi e cryptography sono wheel x86_64).

Sono i file da proporre al repository flathub/flathub (ramo new-pr) e poi
da tenere nel repository dell'app su Flathub.

Uso: scripts/flathub-manifest.py [TAG] [--out DIR]   (TAG: v<versione in uso>)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_ID = "io.github.gradia64.OllaDesk"
MANIFEST = ROOT / "packaging" / "flatpak" / f"{APP_ID}.yaml"
GIT_URL = "https://github.com/gradia64/OllaDesk.git"
MARKER_LINE = "printf 'flatpak\\n' > /app/share/olladesk/olladesk/_packaging"
FLATHUB_JSON = {"only-arches": ["x86_64"]}


def flathub_manifest(text: str, tag: str, commit: str) -> str:
    """Il manifest con il sorgente git del tag e il marcatore "flathub"."""
    if not re.fullmatch(r"v\d+(\.\d+)*", tag):
        raise ValueError(f"tag non valido: {tag!r}")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError(f"commit non valido: {commit!r}")
    if text.count(MARKER_LINE) != 1:
        raise ValueError("marcatore _packaging non trovato una sola volta")
    text = text.replace(MARKER_LINE, MARKER_LINE.replace("'flatpak", "'flathub"))

    lines = text.split("\n")
    starts = [i for i, l in enumerate(lines) if l.strip() == "- type: dir"]
    if len(starts) != 1:
        raise ValueError("sorgente `type: dir` non trovato una sola volta")
    i = starts[0]
    indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
    # via i commenti subito sopra (parlano del checkout) …
    first = i
    while first > 0 and lines[first - 1].startswith(indent + "#"):
        first -= 1
    # … e le righe della voce (più rientrate)
    last = i + 1
    while last < len(lines) and lines[last].startswith(indent + "  "):
        last += 1
    git = [
        f"{indent}# sorgente fisso: il tag della release (scripts/flathub-manifest.py)",
        f"{indent}- type: git",
        f"{indent}  url: {GIT_URL}",
        f"{indent}  tag: {tag}",
        f"{indent}  commit: {commit}",
        f"{indent}  x-checker-data:",
        f"{indent}    type: git",
        f"{indent}    tag-pattern: ^v([\\d.]+)$",
    ]
    return "\n".join(lines[:first] + git + lines[last:])


def _tag_commit(tag: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise SystemExit(f"tag {tag} non trovato: crealo (o scaricalo con git fetch --tags) prima")
    return out.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tag", nargs="?")
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "flathub")
    args = ap.parse_args(argv)
    tag = args.tag
    if not tag:
        sys.path.insert(0, str(ROOT))
        from olladesk import __version__
        tag = f"v{__version__}"
    text = flathub_manifest(MANIFEST.read_text(encoding="utf-8"), tag, _tag_commit(tag))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / MANIFEST.name).write_text(text, encoding="utf-8")
    (args.out / "flathub.json").write_text(json.dumps(FLATHUB_JSON, indent=2) + "\n", encoding="utf-8")
    print(f"scritti {args.out / MANIFEST.name} e flathub.json ({tag})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

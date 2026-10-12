#!/usr/bin/env python3
"""Blocco <releases> dei metadati AppStream dalle note di rilascio.

Una voce per ogni packaging/release-notes/<X.Y.Z>.md, dalla più recente,
con il link alla release GitHub. La data viene dal tag vX.Y.Z; senza tag
(note scritte prima del tag) resta quella già nel file, e per una versione
nuova si usa --date oppure oggi. Così il controllo non dipende dai tag
presenti nel clone della CI.

Uso:  scripts/metainfo-releases.py [--date AAAA-MM-GG]   riscrive il file
      scripts/metainfo-releases.py --check                fallisce se non è aggiornato
"""
import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METAINFO = ROOT / "io.github.gradia64.OllaDesk.metainfo.xml"
NOTES = ROOT / "packaging" / "release-notes"
REPO = "https://github.com/gradia64/OllaDesk"
# a inizio riga: la parola <releases> può comparire anche nei commenti
BLOCK_RE = re.compile(r"^( *)<releases>.*?^\1</releases>", re.S | re.M)
RELEASE_RE = re.compile(r'<release version="([^"]+)" date="([^"]+)"')


def versions() -> list[str]:
    found = [p.stem for p in NOTES.glob("*.md") if re.fullmatch(r"\d+\.\d+\.\d+", p.stem)]
    return sorted(found, key=lambda v: tuple(map(int, v.split("."))), reverse=True)


def tag_date(version: str) -> str | None:
    r = subprocess.run(
        ["git", "-C", str(ROOT), "for-each-ref", "--format=%(creatordate:short)",
         f"refs/tags/v{version}"],
        capture_output=True, text=True, check=False)
    return r.stdout.strip() or None


def render(text: str, new_date: str) -> str:
    m = BLOCK_RE.search(text)
    if not m:
        sys.exit(f"{METAINFO.name}: manca il blocco <releases>")
    known = dict(RELEASE_RE.findall(m.group(0)))
    pad = m.group(1)
    lines = [f"{pad}<releases>"]
    for v in versions():
        date = tag_date(v) or known.get(v) or new_date
        lines += [f'{pad}  <release version="{v}" date="{date}">',
                  f'{pad}    <url type="details">{REPO}/releases/tag/v{v}</url>',
                  f"{pad}  </release>"]
    lines.append(f"{pad}</releases>")
    return text[:m.start()] + "\n".join(lines) + text[m.end():]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    args = ap.parse_args()
    text = METAINFO.read_text(encoding="utf-8")
    new = render(text, args.date)
    if args.check:
        if new != text:
            print(f"{METAINFO.name}: <releases> non aggiornato, "
                  "lancia scripts/metainfo-releases.py", file=sys.stderr)
            return 1
        return 0
    if new != text:
        METAINFO.write_text(new, encoding="utf-8")
        print(f"aggiornato {METAINFO.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

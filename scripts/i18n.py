#!/usr/bin/env python3
"""Cataloghi delle traduzioni di OllaDesk (olladesk/locale).

  scripts/i18n.py extract   rigenera olladesk/locale/olladesk.pot dai sorgenti
  scripts/i18n.py update    estrae e allinea ogni <lingua>.po al modello
                            (le traduzioni restano, le voci nuove arrivano vuote,
                            quelle non più usate spariscono)
  scripts/i18n.py check     esce con errore se un .po non è allineato, ha voci
                            non tradotte o segnaposto {nome} diversi dall'originale

I testi sorgente sono in italiano. Si estraggono:
- in Python le chiamate _("…") e N_("…") con un testo letterale;
- nella companion web tr("…") / trf("…") di app.js (anche nella forma
  trf(cond ? "…" : "…")) e, in index.html, il testo degli elementi con
  data-i18n e gli attributi elencati in data-i18n-attr.
"""
from __future__ import annotations

import ast
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "olladesk"
LOCALE = PKG / "locale"
POT = LOCALE / "olladesk.pot"
LANGS = ("en", "fr", "de", "es")

sys.path.insert(0, str(ROOT))
from olladesk.i18n import parse_po  # noqa: E402

_PLACEHOLDER = re.compile(r"(?<!\{)\{(\w+)[^{}]*\}(?!\})")
_JS_STR = r'"((?:[^"\\]|\\.)*)"'
_JS_CALL = re.compile(
    r"\btrf?\(\s*(?:[\w.!]+\s*\?\s*)?" + _JS_STR + r"(?:\s*:\s*" + _JS_STR + r")?", re.S
)


def _python_messages() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("_", "N_") and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                out.setdefault(node.args[0].value, []).append(f"{rel}:{node.lineno}")
    return out


def _js_unescape(s: str) -> str:
    return ast.literal_eval('"' + s + '"')


def _web_messages() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    js = PKG / "web" / "app.js"
    text = js.read_text(encoding="utf-8")
    for m in _JS_CALL.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        for g in m.groups():
            if g:
                out.setdefault(_js_unescape(g), []).append(f"olladesk/web/app.js:{line}")
    page = (PKG / "web" / "index.html").read_text(encoding="utf-8")
    ref = "olladesk/web/index.html"
    for m in re.finditer(r"<(\w+)\b[^>]*\sdata-i18n[\s>][^>]*?>?([^<]*)</\1>", page):
        msg = html.unescape(m.group(2).strip())
        if msg:
            out.setdefault(msg, []).append(f"{ref}:{page.count(chr(10), 0, m.start()) + 1}")
    for m in re.finditer(r"<\w+\b[^>]*\sdata-i18n-attr=\"([^\"]+)\"[^>]*>", page):
        tag = m.group(0)
        for attr in m.group(1).split():
            v = re.search(r"\s" + re.escape(attr) + r"=\"([^\"]*)\"", tag)
            if v and v.group(1):
                out.setdefault(html.unescape(v.group(1)), []).append(
                    f"{ref}:{page.count(chr(10), 0, m.start()) + 1}")
    return out


def messages() -> dict[str, list[str]]:
    out = _python_messages()
    for msg, refs in _web_messages().items():
        out.setdefault(msg, []).extend(refs)
    return out


def _quote(s: str) -> str:
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")
    parts = s.split("\n")
    if len(parts) == 1:
        return f'"{s}"'
    # una riga del .po per ogni riga del testo, come fanno gli strumenti gettext
    lines = [p + "\\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
    return '""\n' + "\n".join(f'"{x}"' for x in lines)


def _header(lang: str | None) -> str:
    lang_line = f'"Language: {lang}\\n"\n' if lang else '"Language: \\n"\n'
    return (
        'msgid ""\nmsgstr ""\n'
        '"Project-Id-Version: OllaDesk\\n"\n'
        '"Report-Msgid-Bugs-To: https://github.com/gradia64/OllaDesk/issues\\n"\n'
        + lang_line +
        '"MIME-Version: 1.0\\n"\n'
        '"Content-Type: text/plain; charset=UTF-8\\n"\n'
        '"Content-Transfer-Encoding: 8bit\\n"\n'
        '"X-Source-Language: it\\n"\n'
    )


def render(msgs: dict[str, list[str]], lang: str | None, tr: dict[str, str]) -> str:
    out = [_header(lang)]
    for msg in sorted(msgs, key=lambda m: (msgs[m][0], m)):
        refs = "\n".join("#: " + r for r in msgs[msg])
        flags = "#, python-brace-format\n" if _PLACEHOLDER.search(msg) else ""
        out.append(f"{refs}\n{flags}msgid {_quote(msg)}\nmsgstr {_quote(tr.get(msg, ''))}\n")
    return "\n".join(out)


def extract() -> dict[str, list[str]]:
    msgs = messages()
    LOCALE.mkdir(exist_ok=True)
    POT.write_text(render(msgs, None, {}), encoding="utf-8")
    print(f"{POT.relative_to(ROOT)}: {len(msgs)} testi")
    return msgs


def update() -> None:
    msgs = extract()
    for lang in LANGS:
        po = LOCALE / f"{lang}.po"
        old = parse_po(po.read_text(encoding="utf-8")) if po.exists() else {}
        po.write_text(render(msgs, lang, old), encoding="utf-8")
        missing = sum(1 for m in msgs if not old.get(m))
        print(f"{po.relative_to(ROOT)}: {len(msgs) - missing} tradotti, {missing} da tradurre")


def check() -> int:
    msgs = messages()
    errors = 0
    for lang in LANGS:
        po = LOCALE / f"{lang}.po"
        if not po.exists():
            print(f"{lang}.po mancante")
            errors += 1
            continue
        cat = parse_po(po.read_text(encoding="utf-8"))
        for msg in msgs:
            tr = cat.get(msg)
            if not tr:
                print(f"{lang}: non tradotto: {msg!r}")
                errors += 1
            elif set(_PLACEHOLDER.findall(tr)) != set(_PLACEHOLDER.findall(msg)):
                print(f"{lang}: segnaposto diversi: {msg!r} → {tr!r}")
                errors += 1
        for msg in cat:
            if msg not in msgs:
                print(f"{lang}: voce non più usata (scripts/i18n.py update): {msg!r}")
                errors += 1
    print("ok" if not errors else f"{errors} problemi")
    return 1 if errors else 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "extract":
        extract()
    elif cmd == "update":
        update()
    elif cmd == "check":
        sys.exit(check())
    else:
        print(__doc__)
        sys.exit(2)

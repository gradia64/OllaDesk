"""Conversione Markdown → HTML (subset Qt rich text) per i messaggi.

Copre: blocchi di codice, inline code, grassetto (** e __), corsivo (* e _),
intestazioni, elenchi anche annidati, tabelle GFM, citazioni, link e righe
orizzontali. Durante lo streaming un blocco di codice non ancora chiuso viene
comunque renderizzato come blocco.
"""
from __future__ import annotations

import html
import re

_FENCE_SPLIT_RE = re.compile(r"```([A-Za-z0-9_+\-]*)[^\S\n]*\n(.*?)```", re.S)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC_RE = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
_UNDER_BOLD_RE = re.compile(r"(?<![\w\\])__([^_\n]+)__(?![\w])")
_UNDER_ITALIC_RE = re.compile(r"(?<![\w\\])_([^_\n]+)_(?![\w])")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_HR_RE = re.compile(r"^\s{0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$")
_UL_RE = re.compile(r"^(\s*)[-*+]\s+(.+)$")
_OL_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.+)$")
_BLOCK_JOIN_RE = re.compile(
    r"^<(?:li|ul|ol|/ul|/ol|h[1-6]|hr|table|/table|blockquote|/blockquote)\b"
)

_MAX_CODE_LINE = 110  # lunghezza massima di una riga di codice prima dell'a-capo


def _wrap_code(code: str) -> str:
    out_lines = []
    for line in code.split("\n"):
        while len(line) > _MAX_CODE_LINE:
            out_lines.append(line[:_MAX_CODE_LINE] + " ⏎")
            line = "    " + line[_MAX_CODE_LINE:]
        out_lines.append(line)
    return "\n".join(out_lines)


def _code_block_html(code: str, code_bg: str, code_fg: str) -> str:
    # Qt rich text supporta bgcolor su <table>/<td>: usato per lo sfondo dei blocchi.
    return (
        f'<table width="100%" cellspacing="0" cellpadding="8" bgcolor="{code_bg}">'
        f"<tr><td><font color=\"{code_fg}\"><pre>{html.escape(_wrap_code(code))}</pre></font></td></tr>"
        "</table>"
    )


def _inline(seg: str, inline_code_color: str) -> str:
    """Escape HTML + formattazione inline (codice, link, grassetto, corsivo)."""
    inline: list[str] = []

    def _stash(m: re.Match) -> str:
        inline.append(
            f"<code><font color=\"{inline_code_color}\">{html.escape(m.group(1))}</font></code>"
        )
        return f"\x00I{len(inline) - 1}\x00"

    def _emphasis(t: str) -> str:
        t = _BOLD_RE.sub(r"<b>\1</b>", t)
        t = _UNDER_BOLD_RE.sub(r"<b>\1</b>", t)
        t = _ITALIC_RE.sub(r"<i>\1</i>", t)
        return _UNDER_ITALIC_RE.sub(r"<i>\1</i>", t)

    def _stash_link(m: re.Match) -> str:
        # l'URL resta intatto: grassetto/corsivo solo sull'etichetta
        # (un URL come https://x.com/_a_ altrimenti diventerebbe href=".../<i>a</i>")
        inline.append(f'<a href="{m.group(2)}">{_emphasis(m.group(1))}</a>')
        return f"\x00I{len(inline) - 1}\x00"

    seg = _INLINE_CODE_RE.sub(_stash, seg)
    seg = html.escape(seg)
    seg = _LINK_RE.sub(_stash_link, seg)
    seg = _emphasis(seg)
    # più passate: un link può contenere a sua volta inline code
    while "\x00I" in seg:
        seg = re.sub(r"\x00I(\d+)\x00", lambda m: inline[int(m.group(1))], seg)
    return seg


def _is_table_sep(line: str) -> bool:
    s = line.strip().strip("|")
    if not s:
        return False
    cells = s.split("|")
    return bool(cells) and all(re.fullmatch(r"\s*:?-+:?\s*", c) for c in cells)


def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _table_html(rows: list[str], inline_code_color: str, header_bg: str) -> str:
    # rows = [intestazione, righe di dati...] (il separatore non è incluso)
    out = ['<table width="100%" cellspacing="0" cellpadding="5" border="1">']
    out.append(
        f'<tr bgcolor="{header_bg}">'
        + "".join(f"<td><b>{_inline(c, inline_code_color)}</b></td>" for c in _split_row(rows[0]))
        + "</tr>"
    )
    for r in rows[1:]:
        out.append(
            "<tr>"
            + "".join(f"<td>{_inline(c, inline_code_color)}</td>" for c in _split_row(r))
            + "</tr>"
        )
    out.append("</table>")
    return "".join(out)


def _list_open(kind: str, start: int | None) -> str:
    if kind == "ol" and start not in (None, 1):
        return f'<ol start="{start}">'
    return f"<{kind}>"


def _list_html(items: list[tuple[int, str, str, int | None]], pos: int, indent: int,
               kind: str, inline_code_color: str) -> tuple[str, int]:
    """Costruisce <ul>/<ol> (annidati) da [(indent, kind, testo, numero)]."""
    out = [_list_open(kind, items[pos][3] if pos < len(items) else None)]
    while pos < len(items):
        ind, k, text, _num = items[pos]
        if ind < indent:
            break
        if ind > indent:
            sub, pos = _list_html(items, pos, ind, k, inline_code_color)
            if out[-1].endswith("</li>"):
                out[-1] = out[-1][:-5] + sub + "</li>"
            else:
                out.append(sub)
            continue
        if k != kind:
            # stesso livello, tipo diverso (puntato → numerato): chiude questo
            # elenco e prosegue con un elenco fratello, che si chiude da sé
            out.append(f"</{kind}>")
            sub, pos = _list_html(items, pos, ind, k, inline_code_color)
            out.append(sub)
            return "".join(out), pos
        out.append(f"<li>{_inline(text, inline_code_color)}</li>")
        pos += 1
    out.append(f"</{kind}>")
    return "".join(out), pos


def _render_segment(seg: str, inline_code_color: str, header_bg: str) -> str:
    lines = seg.split("\n")
    parts: list[tuple[str, str]] = []   # ("h", html già pronto) | ("t", testo grezzo)
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i]
        # tabella GFM: riga con pipe seguita da un separatore con lo stesso
        # numero di celle («testo | x» seguito da «---» NON è una tabella)
        if ("|" in ln and i + 1 < n and _is_table_sep(lines[i + 1])
                and len(_split_row(lines[i + 1])) == len(_split_row(ln))):
            rows = [ln]
            i += 2   # salta il separatore
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(lines[i])
                i += 1
            parts.append(("h", _table_html(rows, inline_code_color, header_bg)))
            continue
        # blockquote: righe consecutive che iniziano con «>»
        if ln.lstrip().startswith(">"):
            quote = []
            while i < n and lines[i].lstrip().startswith(">"):
                quote.append(lines[i].lstrip()[1:].lstrip())
                i += 1
            inner = _render_segment("\n".join(quote), inline_code_color, header_bg)
            parts.append(("h", f"<blockquote>{inner}</blockquote>"))
            continue
        if _HR_RE.match(ln):
            parts.append(("h", "<hr>"))
            i += 1
            continue
        # elenchi, anche annidati
        if _UL_RE.match(ln) or _OL_RE.match(ln):
            items = []
            while i < n:
                m = _UL_RE.match(lines[i])
                if m and not _HR_RE.match(lines[i]):
                    items.append((len(m.group(1)), "ul", m.group(2), None))
                    i += 1
                    continue
                m = _OL_RE.match(lines[i])
                if m:
                    items.append((len(m.group(1)), "ol", m.group(3), int(m.group(2))))
                    i += 1
                    continue
                break
            base = min(it[0] for it in items)
            html_list, _pos = _list_html(items, 0, base, items[0][1], inline_code_color)
            parts.append(("h", html_list))
            continue
        m = _HEADING_RE.match(ln)
        if m:
            level = len(m.group(1))
            # anche il testo delle intestazioni passa da _inline: escape HTML
            # (un modello non deve poter iniettare tag o link file://)
            parts.append(
                ("h", f"<h{level}>{_inline(m.group(2), inline_code_color)}</h{level}>")
            )
            i += 1
            continue
        parts.append(("t", ln))
        i += 1

    joined = ""
    prev_block = False
    for kind, val in parts:
        piece = val if kind == "h" else _inline(val, inline_code_color)
        cur_block = kind == "h"
        if joined and not (prev_block or cur_block):
            joined += "<br>"
        joined += piece
        prev_block = cur_block
    return joined


def md_to_html(
    text: str,
    code_bg: str = "#1a1d21",
    code_fg: str = "#e8eaed",
    inline_code_color: str = "#79b8ff",
) -> str:
    if not text:
        return ""
    # streaming: se le righe di APERTURA fence sono dispari, chiudo l'ultimo
    # blocco (i ``` scritti in mezzo al testo non generano più fence spurie)
    openers = len(re.findall(r"^[^\S\n]*```", text, re.M))
    if openers % 2 == 1:
        text = text + "\n```"

    parts: list[str] = []
    pos = 0
    for m in _FENCE_SPLIT_RE.finditer(text):
        before = text[pos:m.start()]
        if before.strip():
            parts.append(_render_segment(before, inline_code_color, code_bg))
        parts.append(_code_block_html(m.group(2).rstrip("\n"), code_bg, code_fg))
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        parts.append(_render_segment(tail, inline_code_color, code_bg))
    return "".join(parts)

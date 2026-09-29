"""Conversione minima Markdown → HTML (subset Qt rich text) per i messaggi.

Copre gli elementi più comuni nelle risposte dei modelli: blocchi di codice,
inline code, grassetto, corsivo, intestazioni, elenchi, link e righe orizzontali.
Durante lo streaming un blocco di codice non ancora chiuso viene comunque
renderizzato come blocco.
"""
from __future__ import annotations

import html
import re

_FENCE_SPLIT_RE = re.compile(r"```([A-Za-z0-9_+\-]*)[^\S\n]*\n(.*?)```", re.S)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC_RE = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_HR_RE = re.compile(r"^\s{0,3}(?:-{3,}|\*{3,})\s*$")
_UL_RE = re.compile(r"^\s{0,3}[-*+]\s+(.+)$")
_OL_RE = re.compile(r"^\s{0,3}\d+[.)]\s+(.+)$")

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


def _render_segment(seg: str, inline_code_color: str) -> str:
    # inline code viene messo da parte prima dell'escape HTML
    inline: list[str] = []

    def _stash_inline(m: re.Match) -> str:
        inline.append(f"<code><font color=\"{inline_code_color}\">{html.escape(m.group(1))}</font></code>")
        return f"\x00I{len(inline) - 1}\x00"

    seg = _INLINE_CODE_RE.sub(_stash_inline, seg)
    seg = html.escape(seg)

    seg = _LINK_RE.sub(r'<a href="\2">\1</a>', seg)
    seg = _BOLD_RE.sub(r"<b>\1</b>", seg)
    seg = _ITALIC_RE.sub(r"<i>\1</i>", seg)

    lines = seg.split("\n")
    out: list[str] = []
    ul = ol = False

    def close_lists() -> None:
        nonlocal ul, ol
        if ul:
            out.append("</ul>")
            ul = False
        if ol:
            out.append("</ol>")
            ol = False

    for ln in lines:
        m = _HEADING_RE.match(ln)
        if m:
            close_lists()
            level = len(m.group(1))
            out.append(f"<h{level}>{m.group(2)}</h{level}>")
            continue
        if _HR_RE.match(ln):
            close_lists()
            out.append("<hr>")
            continue
        m = _UL_RE.match(ln)
        if m:
            if ol:
                out.append("</ol>")
                ol = False
            if not ul:
                out.append("<ul>")
                ul = True
            out.append(f"<li>{m.group(1)}</li>")
            continue
        m = _OL_RE.match(ln)
        if m:
            if ul:
                out.append("</ul>")
                ul = False
            if not ol:
                out.append("<ol>")
                ol = True
            out.append(f"<li>{m.group(1)}</li>")
            continue
        close_lists()
        out.append(ln)
    close_lists()

    # unisce i blocchi: tra elementi di elenco, intestazioni e righe orizzontali
    # non vanno <br> (creavano righe vuote in più)
    block_re = re.compile(r"^<(?:li|ul|ol|/ul|/ol|h[1-6]|hr)\b")
    joined = out[0] if out else ""
    for i in range(1, len(out)):
        if not (block_re.match(out[i - 1]) or block_re.match(out[i])):
            joined += "<br>"
        joined += out[i]
    seg = joined
    seg = re.sub(r"\x00I(\d+)\x00", lambda m: inline[int(m.group(1))], seg)
    return seg


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
            parts.append(_render_segment(before, inline_code_color))
        parts.append(_code_block_html(m.group(2).rstrip("\n"), code_bg, code_fg))
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        parts.append(_render_segment(tail, inline_code_color))
    return "".join(parts)

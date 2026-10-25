"""Testo dei documenti d'ufficio: Word (.docx, .doc), Excel (.xlsx, .xls),
OpenDocument (.odt, .ods).

Solo libreria standard, come il resto dell'app: i formati moderni sono zip
con dentro XML; quelli vecchi (.doc, .xls) sono file OLE2 «compound
document», letti con il piccolo lettore qui sotto. Si estrae il testo, non
la formattazione: per i fogli di calcolo una riga per riga del foglio, con
le celle separate da tabulazioni.

Ogni funzione restituisce (testo, nota di errore o None), come
`context.extract_text`, e si ferma a `limit` caratteri o alla scadenza
`deadline` (time.monotonic).
"""
from __future__ import annotations

import datetime
import re
import struct
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .i18n import _

DOCX_EXTS = {".docx", ".docm"}
XLSX_EXTS = {".xlsx", ".xlsm"}
DOC_EXTS = {".doc"}
XLS_EXTS = {".xls"}
ODT_EXTS = {".odt"}
ODS_EXTS = {".ods"}
OFFICE_EXTS = DOCX_EXTS | XLSX_EXTS | DOC_EXTS | XLS_EXTS | ODT_EXTS | ODS_EXTS

# un .xlsx da 1 MB può contenere XML per centinaia di MB (bomba zip): oltre
# questa soglia la lettura di una parte si interrompe
MAX_PART_BYTES = 64 * 1024 * 1024
# i file OLE2 si leggono interi in memoria
MAX_OLE_BYTES = 64 * 1024 * 1024
# celle vuote ripetute (ODS) o colonne lontane: una riga non esplode
MAX_COLS = 256


class _Stop(Exception):
    """Limite di caratteri o scadenza raggiunti: il testo raccolto basta."""


class _Out:
    """Testo raccolto, fino a `limit` caratteri (poi solleva _Stop)."""

    def __init__(self, limit: int, deadline: float | None):
        self.parts: list[str] = []
        self.size = 0
        self.limit = limit
        self.deadline = deadline

    def add(self, s: str) -> None:
        self.parts.append(s)
        self.size += len(s)
        if self.size > self.limit:
            raise _Stop
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise _Stop

    def text(self) -> str:
        return "".join(self.parts)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class _Limited:
    """File in lettura che smette oltre MAX_PART_BYTES."""

    def __init__(self, fh):
        self.fh = fh
        self.read_bytes = 0

    def read(self, n: int = -1) -> bytes:
        data = self.fh.read(n if n >= 0 else MAX_PART_BYTES)
        self.read_bytes += len(data)
        if self.read_bytes > MAX_PART_BYTES:
            raise _Stop
        return data


def _iterparse(zf: zipfile.ZipFile, name: str, events=("start", "end")):
    with zf.open(name) as fh:
        yield from ET.iterparse(_Limited(fh), events=events)


def _fmt_number(v: float) -> str:
    if v != v or v in (float("inf"), float("-inf")):
        return str(v)
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return "%.15g" % v


# ------------------------------------------------------------------- date

# formati numerici predefiniti di Excel che sono date o ore
_BUILTIN_DATE_FMTS = set(range(14, 23)) | {45, 46, 47}


def _is_date_code(code: str) -> bool:
    # toglie le parti tra virgolette, gli escape e i colori/condizioni tra []
    code = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]', "", code).lower()
    return bool(re.search(r"[dmyhs]", code)) and "general" not in code


def _serial_to_date(v: float, date1904: bool) -> str:
    try:
        base = datetime.datetime(1904, 1, 1) if date1904 else datetime.datetime(1899, 12, 30)
        dt = base + datetime.timedelta(days=v)
    except (OverflowError, ValueError):
        return _fmt_number(v)
    if v == int(v):
        return dt.date().isoformat()
    if 0 <= v < 1:
        return dt.time().isoformat(timespec="seconds")
    return dt.isoformat(sep=" ", timespec="seconds")


# ------------------------------------------------------------- righe/celle

def _col_index(ref: str) -> int:
    """«C7» → 2 (colonne da zero)."""
    n = 0
    for ch in ref:
        if "A" <= ch <= "Z":
            n = n * 26 + ord(ch) - 64
        elif "a" <= ch <= "z":
            n = n * 26 + ord(ch) - 96
        else:
            break
    return n - 1


def _emit_row(out: _Out, cells: dict[int, str]) -> None:
    cells = {c: v for c, v in cells.items() if v.strip() and 0 <= c < MAX_COLS}
    if not cells:
        return
    row = [""] * (max(cells) + 1)
    for c, v in cells.items():
        row[c] = v.replace("\t", " ").replace("\r", " ").replace("\n", " ")
    out.add("\t".join(row) + "\n")


# ------------------------------------------------------------------ .docx

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx(path: Path, out: _Out) -> None:
    with zipfile.ZipFile(path) as zf:
        cell_depth = 0   # dentro una cella di tabella i paragrafi restano sulla riga
        for ev, el in _iterparse(zf, "word/document.xml"):
            tag = el.tag
            if ev == "start":
                if tag == _W + "tc":
                    cell_depth += 1
                continue
            if tag == _W + "t":
                out.add(el.text or "")
            elif tag == _W + "tab":
                out.add("\t")
            elif tag in (_W + "br", _W + "cr"):
                out.add(" " if cell_depth else "\n")
            elif tag == _W + "p":
                out.add(" " if cell_depth else "\n")
            elif tag == _W + "tc":
                cell_depth -= 1
                out.add("\t")
            elif tag == _W + "tr":
                out.add("\n")
            el.clear()


# ------------------------------------------------------------------ .xlsx

_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _xlsx_date_styles(zf: zipfile.ZipFile) -> list[bool]:
    """Per ogni stile di cella (indice di «s»): è un formato data?"""
    try:
        zf.getinfo("xl/styles.xml")
    except KeyError:
        return []
    custom: dict[int, str] = {}
    styles: list[bool] = []
    in_xfs = False
    for ev, el in _iterparse(zf, "xl/styles.xml"):
        name = _local(el.tag)
        if ev == "start":
            if name == "cellXfs":
                in_xfs = True
            continue
        if name == "numFmt":
            try:
                custom[int(el.get("numFmtId", "-1"))] = el.get("formatCode", "")
            except ValueError:
                pass
        elif name == "xf" and in_xfs:
            try:
                fid = int(el.get("numFmtId", "0"))
            except ValueError:
                fid = 0
            styles.append(fid in _BUILTIN_DATE_FMTS
                          or (fid in custom and _is_date_code(custom[fid])))
        elif name == "cellXfs":
            in_xfs = False
    return styles


def _xlsx(path: Path, out: _Out) -> None:
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        # fogli in ordine, con il nome e il file
        rels: dict[str, str] = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for _ev, el in _iterparse(zf, "xl/_rels/workbook.xml.rels", ("end",)):
                if _local(el.tag) == "Relationship":
                    target = el.get("Target", "").lstrip("/")
                    if not target.startswith("xl/"):
                        target = "xl/" + target
                    rels[el.get("Id", "")] = target
        sheets: list[tuple[str, str]] = []
        date1904 = False
        for _ev, el in _iterparse(zf, "xl/workbook.xml", ("end",)):
            name = _local(el.tag)
            if name == "sheet":
                target = rels.get(el.get(_R_ID, ""), "")
                if target in names:
                    sheets.append((el.get("name", ""), target))
            elif name == "workbookPr":
                date1904 = el.get("date1904", "").lower() in ("1", "true")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            buf: list[str] = []
            for ev, el in _iterparse(zf, "xl/sharedStrings.xml"):
                name = _local(el.tag)
                if ev == "start":
                    if name == "si":
                        buf = []
                    continue
                if name == "t":
                    buf.append(el.text or "")
                elif name == "rPh":
                    buf.append("")   # pronuncia fonetica (giapponese): non è testo
                elif name == "si":
                    shared.append("".join(buf))
                    el.clear()
        date_styles = _xlsx_date_styles(zf)

        for sheet_name, target in sheets:
            out.add(_("## Foglio: {name}\n").format(name=sheet_name))
            cells: dict[int, str] = {}
            col = -1
            ctype = style = ""
            value: str | None = None
            inline: list[str] = []
            in_is = False
            for ev, el in _iterparse(zf, target):
                name = _local(el.tag)
                if ev == "start":
                    if name == "c":
                        col = _col_index(el.get("r", "")) if el.get("r") else col + 1
                        ctype = el.get("t", "n")
                        style = el.get("s", "0")
                        value, inline = None, []
                    elif name == "is":
                        in_is = True
                    elif name == "row":
                        cells, col = {}, -1
                    continue
                if name == "v":
                    value = el.text or ""
                elif name == "t" and in_is:
                    inline.append(el.text or "")
                elif name == "is":
                    in_is = False
                elif name == "c":
                    cells[col] = _xlsx_cell(ctype, style, value, inline, shared,
                                            date_styles, date1904)
                elif name == "row":
                    _emit_row(out, cells)
                    cells = {}
                    el.clear()
            out.add("\n")


def _xlsx_cell(ctype, style, value, inline, shared, date_styles, date1904) -> str:
    if ctype == "inlineStr":
        return "".join(inline)
    if value is None:
        return ""
    if ctype == "s":
        try:
            return shared[int(value)]
        except (ValueError, IndexError):
            return ""
    if ctype == "b":
        return "TRUE" if value == "1" else "FALSE"
    if ctype in ("str", "e", "d"):
        return value
    try:
        v = float(value)
    except ValueError:
        return value
    try:
        is_date = date_styles[int(style)]
    except (ValueError, IndexError):
        is_date = False
    return _serial_to_date(v, date1904) if is_date else _fmt_number(v)


# ------------------------------------------------------------ .odt / .ods

_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"


def _odf_inline(el: ET.Element) -> str:
    """Testo di un paragrafo ODF, con spazi, tabulazioni e a capo."""
    parts = [el.text or ""]
    for child in el:
        tag = child.tag
        if tag == _TEXT + "s":
            try:
                parts.append(" " * min(int(child.get(_TEXT + "c", "1")), 100))
            except ValueError:
                parts.append(" ")
        elif tag == _TEXT + "tab":
            parts.append("\t")
        elif tag == _TEXT + "line-break":
            parts.append("\n")
        elif tag != _OFFICE + "annotation":
            parts.append(_odf_inline(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _odt(path: Path, out: _Out) -> None:
    with zipfile.ZipFile(path) as zf:
        depth = 0   # paragrafi annidati (note, cornici): si scrive il più esterno
        cell_depth = 0
        for ev, el in _iterparse(zf, "content.xml"):
            if el.tag == _TABLE + "table-cell":
                cell_depth += 1 if ev == "start" else -1
                if ev == "end":
                    out.add("\t")
                continue
            if el.tag not in (_TEXT + "p", _TEXT + "h"):
                if ev == "end" and el.tag == _TABLE + "table-row":
                    out.add("\n")
                continue
            if ev == "start":
                depth += 1
                continue
            depth -= 1
            if depth == 0:
                out.add(_odf_inline(el) + (" " if cell_depth else "\n"))
                el.clear()


def _ods(path: Path, out: _Out) -> None:
    with zipfile.ZipFile(path) as zf:
        cells: dict[int, str] = {}
        col = 0
        for ev, el in _iterparse(zf, "content.xml"):
            tag = el.tag
            if ev == "start":
                if tag == _TABLE + "table":
                    out.add(_("## Foglio: {name}\n").format(name=el.get(_TABLE + "name", "")))
                elif tag == _TABLE + "table-row":
                    cells, col = {}, 0
                continue
            if tag in (_TABLE + "table-cell", _TABLE + "covered-table-cell"):
                try:
                    rep = int(el.get(_TABLE + "number-columns-repeated", "1"))
                except ValueError:
                    rep = 1
                text = "\n".join(_odf_inline(p) for p in el.iter(_TEXT + "p"))
                if text.strip():
                    for i in range(min(rep, MAX_COLS)):
                        cells[col + i] = text
                col += rep
                el.clear()
            elif tag == _TABLE + "table-row":
                # una riga ripetuta mille volte è quasi sempre vuota: si
                # scrive una volta sola
                _emit_row(out, cells)
                el.clear()
            elif tag == _TABLE + "table":
                out.add("\n")
                el.clear()


# ------------------------------------------------------------------- OLE2

class _OleError(Exception):
    pass


class _Ole:
    """Lettore minimo dei file OLE2 (Compound File Binary): solo lettura dei
    flussi per nome, nella radice."""

    def __init__(self, data: bytes):
        if len(data) < 512 or data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise _OleError("not an OLE2 file")
        self.data = data
        ss, mss = struct.unpack_from("<HH", data, 0x1E)
        if ss not in (9, 12) or mss != 6:
            raise _OleError("bad sector size")
        self.ssz = 1 << ss
        self.mssz = 1 << mss
        n_fat, first_dir, _tx, self.cutoff, first_mini, n_mini, first_difat, n_difat = \
            struct.unpack_from("<IIIIIIII", data, 0x2C)
        self.nsect = (len(data) - 512) // self.ssz
        # DIFAT: settori della FAT
        difat = list(struct.unpack_from("<109I", data, 0x4C))
        sid, per = first_difat, self.ssz // 4 - 1
        for _i in range(min(n_difat, self.nsect)):
            if sid >= self.nsect:
                break
            vals = struct.unpack_from(f"<{per + 1}I", data, self._off(sid))
            difat.extend(vals[:per])
            sid = vals[per]
        fat_sects = [s for s in difat[:n_fat] if s < self.nsect]
        self.fat: list[int] = []
        for s in fat_sects:
            self.fat.extend(struct.unpack_from(f"<{self.ssz // 4}I", data, self._off(s)))
        dir_data = self._chain_bytes(first_dir)
        self.entries: dict[str, tuple[int, int]] = {}
        root_start = root_size = None
        for i in range(len(dir_data) // 128):
            e = dir_data[i * 128:(i + 1) * 128]
            nlen = struct.unpack_from("<H", e, 64)[0]
            etype = e[66]
            start, size = struct.unpack_from("<II", e, 116)
            name = e[:max(0, min(nlen, 64) - 2)].decode("utf-16-le", "replace")
            if etype == 5:
                root_start, root_size = start, size
            elif etype == 2:
                self.entries.setdefault(name, (start, size))
        self.minifat: list[int] = []
        mf = self._chain_bytes(first_mini) if n_mini else b""
        self.minifat = list(struct.unpack_from(f"<{len(mf) // 4}I", mf)) if mf else []
        self.ministream = (self._chain_bytes(root_start)[:root_size]
                           if root_start is not None and root_start < self.nsect else b"")

    def _off(self, sid: int) -> int:
        return 512 + sid * self.ssz

    def _chain(self, sid: int, table: list[int], limit: int):
        seen = 0
        while sid < len(table) and seen <= limit:
            yield sid
            seen += 1
            sid = table[sid]

    def _chain_bytes(self, sid: int) -> bytes:
        parts = []
        for s in self._chain(sid, self.fat, self.nsect):
            if s >= self.nsect:
                break
            o = self._off(s)
            parts.append(self.data[o:o + self.ssz])
        return b"".join(parts)

    def stream(self, name: str) -> bytes | None:
        entry = self.entries.get(name)
        if entry is None:
            return None
        start, size = entry
        if size < self.cutoff:
            ms, parts = self.ministream, []
            for s in self._chain(start, self.minifat, len(ms) // self.mssz):
                o = s * self.mssz
                if o >= len(ms):
                    break
                parts.append(ms[o:o + self.mssz])
            return b"".join(parts)[:size]
        return self._chain_bytes(start)[:size]


def _read_ole(path: Path) -> _Ole:
    if path.stat().st_size > MAX_OLE_BYTES:
        raise _OleError("too large")
    return _Ole(path.read_bytes())


# ------------------------------------------------------------------- .doc

def _doc_clean(raw: str) -> str:
    """Caratteri speciali di Word: fine paragrafo, celle, campi."""
    out: list[str] = []
    # campi: 0x13 inizio, 0x14 separatore, 0x15 fine; il codice del campo
    # (tra 0x13 e 0x14) non è testo, il risultato (tra 0x14 e 0x15) sì
    stack: list[bool] = []   # per campo aperto: siamo nella parte del codice?
    for ch in raw:
        o = ord(ch)
        if o == 0x13:
            stack.append(True)
            continue
        if o == 0x14:
            if stack:
                stack[-1] = False
            continue
        if o == 0x15:
            if stack:
                stack.pop()
            continue
        if any(stack):
            continue
        if ch in "\r\x0b\x0c":
            out.append("\n")
        elif o == 0x07:      # fine cella / fine riga di tabella
            out.append("\t")
        elif ch == "\t" or o >= 0x20:
            out.append(ch)
    text = "".join(out)
    return re.sub(r"\t\n", "\n", text)


def _doc(path: Path, ole: _Ole, out: _Out) -> str | None:
    wd = ole.stream("WordDocument")
    if wd is None or len(wd) < 0x200:
        return _('{name} non sembra un documento Word').format(name=path.name)
    ident, _nfib = struct.unpack_from("<HH", wd, 0)
    if ident != 0xA5EC:
        return _('{name}: formato Word troppo vecchio (prima di Word 97)').format(name=path.name)
    flags = struct.unpack_from("<H", wd, 0x0A)[0]
    if flags & 0x0100:
        return _('{name} è protetto da password').format(name=path.name)
    table = ole.stream("1Table" if flags & 0x0200 else "0Table")
    if table is None:
        return _('{name} non sembra un documento Word').format(name=path.name)
    # FIB: 32 byte fissi, poi fibRgW, fibRgLw e fibRgFcLcb di lunghezza variabile
    pos = 32
    csw = struct.unpack_from("<H", wd, pos)[0]
    pos += 2 + csw * 2
    cslw = struct.unpack_from("<H", wd, pos)[0]
    rglw = pos + 2
    ccp_text = struct.unpack_from("<i", wd, rglw + 3 * 4)[0]
    pos = rglw + cslw * 4
    pos += 2   # cbRgFcLcb
    fc_clx, lcb_clx = struct.unpack_from("<II", wd, pos + 33 * 8)
    clx = table[fc_clx:fc_clx + lcb_clx]
    # Clx: zero o più Prc (0x01), poi un Pcdt (0x02) con la tabella dei pezzi
    i = 0
    while i < len(clx) and clx[i] == 0x01:
        i += 3 + struct.unpack_from("<h", clx, i + 1)[0]
    if i + 5 > len(clx) or clx[i] != 0x02:
        return _('{name} non sembra un documento Word').format(name=path.name)
    lcb = struct.unpack_from("<I", clx, i + 1)[0]
    plc = clx[i + 5:i + 5 + lcb]
    n = (len(plc) - 4) // 12
    cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
    remaining = ccp_text if ccp_text > 0 else None
    buf: list[str] = []
    budget = out.limit + 1
    for k in range(n):
        _f, fc, _prm = struct.unpack_from("<HIH", plc, (n + 1) * 4 + k * 8)
        count = cps[k + 1] - cps[k]
        if remaining is not None:
            count = min(count, remaining)
        if count <= 0:
            break
        if fc & 0x40000000:   # testo «compresso»: un byte per carattere
            start = (fc & 0x3FFFFFFF) // 2
            buf.append(wd[start:start + count].decode("cp1252", "replace"))
        else:
            buf.append(wd[fc:fc + count * 2].decode("utf-16-le", "replace"))
        if remaining is not None:
            remaining -= count
        budget -= count
        if budget <= 0 or (remaining is not None and remaining <= 0):
            break
    out.add(_doc_clean("".join(buf)))
    return None


# ------------------------------------------------------------------- .xls

class _Biff:
    """Record BIFF8 di un flusso Workbook: (posizione, tipo, dati), con i
    record CONTINUE (0x003C) uniti al precedente; per lo SST servono i
    confini dei pezzi."""

    def __init__(self, data: bytes):
        self.data = data

    def records(self):
        data, pos, n = self.data, 0, len(self.data)
        while pos + 4 <= n:
            rtype, size = struct.unpack_from("<HH", data, pos)
            start = pos
            body = data[pos + 4:pos + 4 + size]
            pos += 4 + size
            bounds = []
            while pos + 4 <= n and struct.unpack_from("<H", data, pos)[0] == 0x003C:
                csize = struct.unpack_from("<H", data, pos + 2)[0]
                bounds.append(len(body))
                body += data[pos + 4:pos + 4 + csize]
                pos += 4 + csize
            yield start, rtype, body, bounds


def _xl_string(data: bytes, pos: int, cch: int, flags: int,
               bounds: list[int]) -> tuple[str, int]:
    """Caratteri di una stringa BIFF8 da `pos`: a ogni confine di CONTINUE
    c'è un nuovo byte di opzioni (compressa o UTF-16)."""
    parts = []
    high = flags & 1
    while cch > 0 and pos < len(data):
        end = next((b for b in bounds if b > pos), len(data))
        width = 2 if high else 1
        n = min(cch, (end - pos) // width)
        chunk = data[pos:pos + n * width]
        parts.append(chunk.decode("utf-16-le" if high else "latin-1", "replace"))
        pos += n * width
        cch -= n
        if cch > 0:
            pos = end
            if pos >= len(data):
                break
            high = data[pos] & 1
            pos += 1
    return "".join(parts), pos


def _xl_unicode(data: bytes, pos: int, bounds: list[int], short: bool = False) -> tuple[str, int]:
    """XLUnicodeRichExtendedString (o la versione corta, cch su un byte)."""
    if short:
        cch = data[pos]
        pos += 1
    else:
        cch = struct.unpack_from("<H", data, pos)[0]
        pos += 2
    flags = data[pos]
    pos += 1
    runs = ext = 0
    if flags & 0x08:
        runs = struct.unpack_from("<H", data, pos)[0]
        pos += 2
    if flags & 0x04:
        ext = struct.unpack_from("<I", data, pos)[0]
        pos += 4
    s, pos = _xl_string(data, pos, cch, flags, bounds)
    return s, pos + runs * 4 + ext


def _rk(v: int) -> float:
    if v & 2:
        num = float(v >> 2 if v < 0x80000000 else (v >> 2) - (1 << 30))
    else:
        num = struct.unpack("<d", struct.pack("<Q", (v & 0xFFFFFFFC) << 32))[0]
    return num / 100 if v & 1 else num


def _xls(path: Path, ole: _Ole, out: _Out) -> str | None:
    wb = ole.stream("Workbook")
    if wb is None:
        if ole.stream("Book") is not None:
            return _('{name}: formato Excel troppo vecchio (prima di Excel 97)').format(name=path.name)
        return _('{name} non sembra un foglio Excel').format(name=path.name)
    sheets: dict[int, str] = {}
    sst: list[str] = []
    formats: dict[int, str] = {}
    xf_date: list[bool] = []
    date1904 = False
    current: str | None = None
    rows: dict[int, dict[int, str]] = {}
    pending_string: tuple[int, int] | None = None   # FORMULA in attesa di STRING

    def num(r: int, c: int, xf: int, v: float) -> None:
        is_date = xf < len(xf_date) and xf_date[xf]
        rows.setdefault(r, {})[c] = _serial_to_date(v, date1904) if is_date else _fmt_number(v)

    def flush() -> None:
        for r in sorted(rows):
            _emit_row(out, rows[r])
        out.add("\n")
        rows.clear()

    for start, rtype, d, bounds in _Biff(wb).records():
        try:
            if rtype == 0x0809:   # BOF
                if current is not None:
                    flush()
                current = sheets.get(start)
                if current is not None:
                    out.add(_("## Foglio: {name}\n").format(name=current))
            elif rtype == 0x000A:   # EOF
                if current is not None:
                    flush()
                current = None
            elif rtype == 0x002F:   # FILEPASS
                return _('{name} è protetto da password').format(name=path.name)
            elif rtype == 0x0085:   # BOUNDSHEET
                pos, _vis, kind = struct.unpack_from("<IBB", d, 0)
                if kind == 0:
                    sheets[pos] = _xl_unicode(d, 6, bounds, short=True)[0]
            elif rtype == 0x0022:   # DATEMODE
                date1904 = struct.unpack_from("<H", d, 0)[0] == 1
            elif rtype == 0x041E:   # FORMAT
                ifmt = struct.unpack_from("<H", d, 0)[0]
                formats[ifmt] = _xl_unicode(d, 2, bounds)[0]
            elif rtype == 0x00E0:   # XF
                ifmt = struct.unpack_from("<H", d, 2)[0]
                xf_date.append(ifmt in _BUILTIN_DATE_FMTS
                               or (ifmt in formats and _is_date_code(formats[ifmt])))
            elif rtype == 0x00FC:   # SST
                _total, unique = struct.unpack_from("<II", d, 0)
                pos = 8
                for _k in range(unique):
                    if pos + 3 > len(d):
                        break
                    s, pos = _xl_unicode(d, pos, bounds)
                    sst.append(s)
            elif current is None:
                continue
            elif rtype == 0x00FD:   # LABELSST
                r, c, _xf, isst = struct.unpack_from("<HHHI", d, 0)
                if isst < len(sst):
                    rows.setdefault(r, {})[c] = sst[isst]
            elif rtype == 0x0204:   # LABEL
                r, c, _xf = struct.unpack_from("<HHH", d, 0)
                rows.setdefault(r, {})[c] = _xl_unicode(d, 6, bounds)[0]
            elif rtype == 0x0203:   # NUMBER
                r, c, xf, v = struct.unpack_from("<HHHd", d, 0)
                num(r, c, xf, v)
            elif rtype == 0x027E:   # RK
                r, c, xf, v = struct.unpack_from("<HHHI", d, 0)
                num(r, c, xf, _rk(v))
            elif rtype == 0x00BD:   # MULRK
                r, c = struct.unpack_from("<HH", d, 0)
                for k in range((len(d) - 6) // 6):
                    xf, v = struct.unpack_from("<HI", d, 4 + k * 6)
                    num(r, c + k, xf, _rk(v))
            elif rtype == 0x0205:   # BOOLERR
                r, c, _xf, v, err = struct.unpack_from("<HHHBB", d, 0)
                if not err:
                    rows.setdefault(r, {})[c] = "TRUE" if v else "FALSE"
            elif rtype == 0x0006:   # FORMULA: si usa il risultato memorizzato
                r, c, xf = struct.unpack_from("<HHH", d, 0)
                res = d[6:14]
                if res[6:8] == b"\xff\xff":
                    if res[0] == 0:
                        pending_string = (r, c)
                    elif res[0] == 1:
                        rows.setdefault(r, {})[c] = "TRUE" if res[2] else "FALSE"
                else:
                    num(r, c, xf, struct.unpack("<d", res)[0])
            elif rtype == 0x0207 and pending_string:   # STRING
                r, c = pending_string
                rows.setdefault(r, {})[c] = _xl_unicode(d, 0, bounds)[0]
                pending_string = None
        except (struct.error, IndexError):
            continue   # record troncato: si salta
    if current is not None:
        flush()
    return None


# ------------------------------------------------------------- ingresso

def office_text(path: Path, limit: int, deadline: float | None = None) -> tuple[str, str | None]:
    """Testo di un documento d'ufficio, al massimo circa `limit` caratteri."""
    ext = path.suffix.lower()
    sheet = ext in XLSX_EXTS | XLS_EXTS | ODS_EXTS
    out = _Out(limit, deadline)
    note = None
    try:
        # il formato si decide dal contenuto: un .docx salvato come .doc (o
        # il contrario) capita spesso
        with open(path, "rb") as fh:
            magic = fh.read(8)
        if magic.startswith(b"PK"):
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
            if sheet:
                (_xlsx if "xl/workbook.xml" in names else _ods)(path, out)
            else:
                (_docx if "word/document.xml" in names else _odt)(path, out)
        elif magic == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            ole = _read_ole(path)
            if ole.stream("EncryptedPackage") is not None:   # .docx/.xlsx cifrato
                return "", _('{name} è protetto da password').format(name=path.name)
            note = (_xls if sheet else _doc)(path, ole, out)
        else:
            raise _OleError("unknown format")
    except _Stop:
        pass
    except KeyError:
        return "", _('impossibile leggere {name}: parti del documento mancanti').format(name=path.name)
    except (_OleError, zipfile.BadZipFile):
        return "", _('impossibile leggere {name}: non è un file Office valido').format(name=path.name)
    except (ET.ParseError, struct.error, ValueError, IndexError) as e:
        return "", _('impossibile leggere {name}: {error}').format(name=path.name, error=e)
    except OSError as e:
        return "", _('impossibile leggere {name}: {error}').format(name=path.name, error=e.strerror or e)
    # spazi a fine cella e a fine riga: rumore per il modello
    text = re.sub(r" +\t", "\t", out.text())
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, note

"""Tool di lettura: elencare, leggere e cercare nei file condivisi in chat.

Il modello vede solo ciò che l'utente ha aggiunto alla conversazione: le
cartelle e i file allegati con 📎 o trascinati (in Flatpak arrivano dal
portale, come per gli allegati). Niente scrittura, niente comandi.

Le regole sono quelle degli allegati di `context.py`: dentro una cartella si
saltano i nascosti, le cartelle di dipendenze e cache, ciò che il
.gitignore esclude, i probabili file di credenziali e i collegamenti che
escono dalla cartella. Un file allegato da solo si legge anche se ha un
nome «da credenziali»: l'utente l'ha scelto.

I percorsi per il modello hanno la forma `<nome condiviso>/<relativo>`, come
nel blocco di una cartella allegata (`progetto/src/main.py`). Testi e
errori sono per il modello, in inglese (vedi `tools.py`).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from . import context
from .tools import Tool, ToolError

MAX_LIST_ENTRIES = 200        # voci di una cartella in list_files
MAX_READ_LINES = 400          # righe restituite da read_file per chiamata
MAX_SEARCH_MATCHES = 100      # righe trovate da search_text
MAX_SEARCH_FILE_BYTES = 2 * 1024 * 1024   # file più grandi saltati nella ricerca
MAX_TOOL_SECONDS = 5.0        # tetto di tempo di una chiamata (visita, PDF…)


@dataclass(frozen=True)
class Shared:
    """Una cartella o un file condiviso nella chat, con il nome per il modello."""

    label: str
    path: Path
    is_dir: bool


def shared_from_chat(chat: dict | None) -> list[Shared]:
    """Cartelle e file (non immagini) allegati nei messaggi della chat.

    Un percorso allegato più volte conta una volta; nomi uguali si
    distinguono con « (2)», « (3)»… Le voci che non esistono più si saltano.
    """
    out: list[Shared] = []
    seen: set[Path] = set()
    labels: set[str] = set()
    for m in (chat or {}).get("messages", []):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        for att in m.get("attachments_meta") or []:
            if not isinstance(att, dict) or att.get("kind") == "image" or not att.get("path"):
                continue
            try:
                p = Path(att["path"]).resolve()
            except (OSError, RuntimeError):
                continue
            if p in seen:
                continue
            is_dir = p.is_dir()
            if not is_dir and not p.is_file():
                continue
            seen.add(p)
            base = p.name or str(p)
            label, n = base, 2
            while label in labels:
                label, n = f"{base} ({n})", n + 1
            labels.add(label)
            out.append(Shared(label, p, is_dir))
    return out


def _deadline() -> float:
    return time.monotonic() + MAX_TOOL_SECONDS


class _Files:
    """Risoluzione dei percorsi del modello dentro le voci condivise."""

    def __init__(self, shared: list[Shared]):
        self.shared = shared
        self._by_label = {s.label: s for s in shared}
        self._rules: dict[Path, list] = {}

    def rules(self, root: Path) -> list:
        if root not in self._rules:
            self._rules[root] = context._gitignore_rules(root)
        return self._rules[root]

    def overview(self) -> str:
        if not self.shared:
            return "Nothing is shared in this conversation."
        lines = ["Shared in this conversation:"]
        lines += [s.label + ("/" if s.is_dir else "") for s in self.shared]
        return "\n".join(lines)

    def resolve(self, raw) -> tuple[Shared, str, Path]:
        """(voce condivisa, percorso relativo, percorso reale) o ToolError."""
        text = str(raw or "").strip().strip("/")
        if not text:
            raise ToolError("Missing path. " + self.overview())
        # il nome condiviso più lungo che fa da prefisso: «a b/x» con «a b»
        match = None
        for label in sorted(self._by_label, key=len, reverse=True):
            if text == label or text.startswith(label + "/"):
                match = label
                break
        if match is None:
            raise ToolError(f"'{text}' is not shared. " + self.overview())
        s = self._by_label[match]
        rel = text[len(match):].strip("/")
        if not s.is_dir:
            if rel:
                raise ToolError(f"'{match}' is a file, not a folder.")
            return s, "", s.path
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if ".." in parts:
            raise ToolError("Paths with '..' are not allowed.")
        rel = "/".join(parts)
        target = s.path.joinpath(*parts) if parts else s.path
        try:
            real = target.resolve()
            real.relative_to(s.path)
        except (OSError, RuntimeError, ValueError):
            raise ToolError(f"'{text}' points outside the shared folder.") from None
        # le stesse esclusioni della cartella allegata, a ogni livello
        rules = self.rules(s.path)
        for i, name in enumerate(parts):
            sub = "/".join(parts[:i + 1])
            last = i == len(parts) - 1
            is_dir = not last or real.is_dir()
            if name.startswith("."):
                raise ToolError(f"'{text}' is hidden: not shared.")
            if is_dir and name in context.FOLDER_SKIP_DIRS:
                raise ToolError(f"'{text}' is a dependency or cache folder: not shared.")
            if context._ignored(sub, is_dir, rules):
                raise ToolError(f"'{text}' is excluded by .gitignore: not shared.")
            if last and not is_dir and context._looks_secret(name):
                raise ToolError(f"'{text}' looks like a credentials file: not shared.")
        if not real.exists():
            raise ToolError(f"'{text}' does not exist.")
        return s, rel, real


def _label_path(s: Shared, rel: str) -> str:
    return s.label + ("/" + rel if rel else "")


def make_tools(shared: list[Shared]) -> list[Tool]:
    """I tre tool di lettura sulle voci condivise; nessuno se non c'è nulla."""
    if not shared:
        return []
    files = _Files(shared)
    names = ", ".join(s.label + ("/" if s.is_dir else "") for s in shared)

    def list_files(args: dict) -> str:
        raw = args.get("path") or ""
        if not str(raw).strip().strip("/"):
            return files.overview()
        s, rel, real = files.resolve(raw)
        if not real.is_dir():
            return f"{_label_path(s, rel)} is a file. Use read_file to read it."
        rules = files.rules(s.path)
        prefix = rel + "/" if rel else ""
        dirs, items, hidden = [], [], 0
        try:
            entries = sorted(os.scandir(real), key=lambda e: e.name.lower())
        except OSError as e:
            raise ToolError(f"cannot list '{_label_path(s, rel)}': {e.strerror or e}") from None
        for e in entries:
            is_dir = e.is_dir(follow_symlinks=False)
            if (e.name.startswith(".") or e.is_symlink()
                    or (is_dir and e.name in context.FOLDER_SKIP_DIRS)
                    or context._ignored(prefix + e.name, is_dir, rules)
                    or (not is_dir and context._looks_secret(e.name))):
                hidden += 1
                continue
            if is_dir:
                dirs.append(e.name + "/")
            else:
                try:
                    size = e.stat(follow_symlinks=False).st_size
                except OSError:
                    size = 0
                items.append(f"{e.name}  ({_size(size)}, {context.classify(Path(e.name))})")
        listing = dirs + items
        lines = [f"{_label_path(s, rel)}/: {len(dirs)} folders, {len(items)} files"]
        lines += listing[:MAX_LIST_ENTRIES]
        if len(listing) > MAX_LIST_ENTRIES:
            lines.append(f"[... {len(listing) - MAX_LIST_ENTRIES} more entries not shown]")
        if hidden:
            lines.append(f"[{hidden} hidden, ignored or credential entries not shown]")
        return "\n".join(lines)

    def read_file(args: dict) -> str:
        s, rel, real = files.resolve(args.get("path"))
        if real.is_dir():
            return f"{_label_path(s, rel)} is a folder. Use list_files to see its content."
        kind = context.classify(real)
        if kind == "image":
            return f"{_label_path(s, rel)} is an image: it cannot be read as text."
        if kind == "unknown":
            raise ToolError(f"'{_label_path(s, rel)}' is not a supported text, PDF or document file.")
        start = _int(args.get("start_line"), 1, 1)
        count = _int(args.get("max_lines"), MAX_READ_LINES, 1, MAX_READ_LINES)
        text, note = context.extract_text(real, deadline=_deadline())
        if note and not text:
            raise ToolError(note)
        lines = text.splitlines()
        total = len(lines)
        chunk = lines[start - 1:start - 1 + count]
        end = start + len(chunk) - 1
        head = f"{_label_path(s, rel)} — lines {start}-{end} of {total}" if chunk \
            else f"{_label_path(s, rel)} — no lines from {start} (the file has {total})"
        body = "\n".join(f"{start + i:>5}  {line}" for i, line in enumerate(chunk))
        tail = []
        if end < total:
            tail.append(f"[more: call read_file with start_line={end + 1}]")
        if note:
            tail.append(f"[note: {note}]")
        return "\n".join([head, body, *tail]) if body else "\n".join([head, *tail])

    def search_text(args: dict) -> str:
        query = str(args.get("query") or "")
        if not query.strip():
            raise ToolError("Missing query.")
        needle = query.lower()
        where = args.get("path") or ""
        if str(where).strip().strip("/"):
            targets = [files.resolve(where)]
        else:
            targets = [(s, "", s.path) for s in shared]
        deadline = _deadline()
        found: list[str] = []
        searched = 0
        stopped = ""
        for s, rel, real in targets:
            if real.is_dir():
                walked, _skipped = context.folder_files(real, deadline)
                base = rel + "/" if rel else ""
                candidates = [(base + r, p) for r, p in walked]
            else:
                candidates = [(rel, real)]
            for r, p in candidates:
                if time.monotonic() > deadline:
                    stopped = f"stopped after {MAX_TOOL_SECONDS:g} seconds"
                    break
                if context.classify(p) != "text":
                    continue   # PDF e documenti sono lenti: si leggono con read_file
                try:
                    if p.stat().st_size > MAX_SEARCH_FILE_BYTES:
                        continue
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        searched += 1
                        for n, line in enumerate(fh, 1):
                            if needle in line.lower():
                                found.append(f"{_label_path(s, r)}:{n}: {line.strip()[:200]}")
                                if len(found) >= MAX_SEARCH_MATCHES:
                                    stopped = f"first {MAX_SEARCH_MATCHES} matches only"
                                    break
                except OSError:
                    continue
                if stopped:
                    break
            if stopped:
                break
        head = f"'{query}': {len(found)} matching lines in {searched} text files searched"
        if stopped:
            head += f" ({stopped})"
        return "\n".join([head, *found])

    path_prop = {"type": "string",
                 "description": f"Path starting with a shared name. Shared: {names}"}
    return [
        Tool("list_files",
             "List a shared folder (files with size and type). Without a path, "
             f"list what is shared in this conversation: {names}.",
             list_files,
             {"type": "object", "properties": {"path": path_prop}}),
        Tool("read_file",
             "Read a shared text, code, PDF or office file, with line numbers. "
             f"At most {MAX_READ_LINES} lines per call: use start_line to continue.",
             read_file,
             {"type": "object", "required": ["path"], "properties": {
                 "path": path_prop,
                 "start_line": {"type": "integer", "description": "First line (from 1)"},
                 "max_lines": {"type": "integer",
                               "description": f"Lines to read (at most {MAX_READ_LINES})"},
             }}),
        Tool("search_text",
             "Search text (case-insensitive) in the shared text and code files; "
             "returns path:line: matching line.",
             search_text,
             {"type": "object", "required": ["query"], "properties": {
                 "query": {"type": "string", "description": "Text to look for"},
                 "path": {"type": "string",
                          "description": f"Optional folder or file to search in. Shared: {names}"},
             }}),
    ]


def _int(value, default: int, low: int, high: int | None = None) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    n = max(low, n)
    return min(n, high) if high is not None else n


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n} {unit}" if unit == "B" else f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"

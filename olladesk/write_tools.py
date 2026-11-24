"""Tool di scrittura sui file condivisi in chat (0.5, voce W2).

Il modello può creare o sostituire un file di testo (`write_file`),
cambiarne un pezzo esatto (`edit_file`) e creare cartelle (`make_dir`),
solo dentro le cartelle e i file allegati alla conversazione e con le
stesse esclusioni della lettura (`file_tools`): nascosti, dipendenze,
`.gitignore`, credenziali, collegamenti. Un file allegato da solo si può
modificare, ma accanto non si crea nulla.

Ogni modifica passa da `approve`: riceve la `Change` (con il diff) e
risponde True per applicarla. Nel motore è `ApprovalGate.ask`, che
aspetta la scelta dell'utente; gli script di prova e i test la approvano da
soli. Prima di toccare
un file esistente se ne tiene una copia nel `WriteJournal`, che può
annullare le modifiche di una risposta.

Testi e errori sono per il modello, in inglese (vedi `tools.py`).
"""
from __future__ import annotations

import difflib
import os
import shutil
import stat
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import context
from .file_tools import Shared, _Files, _label_path
from .tools import Tool, ToolError

MAX_WRITE_BYTES = 512 * 1024     # dimensione massima di un file scritto
MAX_DIFF_LINES = 400             # righe del diff mostrate per la conferma
MAX_REPLY_CONTEXT = 3            # righe di contesto nella risposta di edit_file

# nota di sistema al posto di file_tools.READ_ONLY_NOTE quando la chat
# permette la scrittura
WRITE_NOTE = (
    "You can create and change text files in the folders and files shared in this "
    "conversation with write_file, edit_file and make_dir. Read a file with read_file "
    "before changing it. For a small change use edit_file with an exact piece of the "
    "current text; use write_file only for new files or to rewrite a whole short file. "
    "The user sees and approves every change: if a change is rejected, do not try the "
    "same change again. You cannot move, rename or delete files and you cannot run "
    "commands. Never claim a change that a tool did not confirm."
)


@dataclass
class Change:
    """Una modifica proposta, per la conferma."""

    kind: str          # "create", "replace", "edit", "mkdir"
    label: str         # percorso per il modello e per l'utente (progetto/src/a.py)
    path: Path         # percorso vero
    diff: str = ""     # diff unificato (vuoto per mkdir)


@dataclass
class WriteJournal:
    """Modifiche applicate in una risposta, per annullarle.

    Per ogni file toccato la prima volta si tiene una copia (o il segno che
    non esisteva); `undo` rimette tutto com'era, dall'ultima modifica alla
    prima. Le copie stanno in una cartella temporanea privata.
    """

    backups: dict[Path, Path | None] = field(default_factory=dict)
    created_dirs: list[Path] = field(default_factory=list)
    _dir: Path | None = None

    def remember(self, path: Path) -> None:
        if path in self.backups:
            return
        if not path.exists():
            self.backups[path] = None
            return
        if self._dir is None:
            self._dir = Path(tempfile.mkdtemp(prefix="olladesk-undo-"))
        copy = self._dir / str(len(self.backups))
        shutil.copy2(path, copy)
        self.backups[path] = copy

    def undo(self) -> list[Path]:
        """Ripristina i file; restituisce quelli che non è riuscito a toccare."""
        failed = []
        for path, copy in reversed(list(self.backups.items())):
            try:
                if copy is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_write(path, copy.read_bytes())
            except OSError:
                failed.append(path)
        for d in reversed(self.created_dirs):
            try:
                d.rmdir()          # solo se vuota: niente perdite
            except OSError:
                pass
        self.backups.clear()
        self.created_dirs.clear()
        self.discard()
        return failed

    def discard(self) -> None:
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None


# scelte dell'utente su una modifica proposta
APPLY, REJECT, ALLOW_REST = "apply", "reject", "allow_rest"
DECISIONS = (APPLY, REJECT, ALLOW_REST)


@dataclass
class Request:
    """Una modifica che aspetta la scelta dell'utente."""

    id: int
    change: Change
    decision: str = ""
    event: threading.Event = field(default_factory=threading.Event)

    def view(self) -> dict:
        """La richiesta per l'interfaccia e il telefono (solo testo)."""
        added = removed = 0
        for line in self.change.diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        return {"id": self.id, "kind": self.change.kind, "label": self.change.label,
                "diff": self.change.diff, "added": added, "removed": removed}


class ApprovalGate:
    """Conferma delle modifiche di una risposta, tra il thread dei tool e la UI.

    `ask` gira nel thread dei tool: registra la richiesta, chiama `notify`
    (che deve solo accodare un segnale verso il thread principale) e aspetta
    `resolve`, chiamato dalla UI, o `close` (Stop, chiusura dell'app), che
    vale come rifiuto. Una modifica identica a una già rifiutata non si
    ripropone all'utente: il modello riceve subito il rifiuto. Con
    «consenti per il resto della risposta» le modifiche successive passano
    senza chiedere.
    """

    def __init__(self, notify: Callable[[Request], None] | None = None):
        self._notify = notify
        self._lock = threading.Lock()
        self._pending: Request | None = None
        self._closed = False
        self._allow_rest = False
        self._rejected: set[str] = set()
        self._next = 0

    @staticmethod
    def _key(change: Change) -> str:
        return "\0".join((change.kind, change.label, change.diff))

    def ask(self, change: Change) -> bool:
        key = self._key(change)
        with self._lock:
            if self._closed:
                raise ToolError("the answer was stopped by the user: nothing was changed.")
            if key in self._rejected:
                raise ToolError(f"the user already rejected this same change to {change.label} "
                                "earlier in this answer, so it was not proposed again. Do not "
                                "repeat it: tell the user or ask what they want instead.")
            if self._allow_rest:
                return True
            self._next += 1
            req = self._pending = Request(self._next, change)
        if self._notify is not None:
            self._notify(req)
        req.event.wait()
        with self._lock:
            if self._pending is req:
                self._pending = None
            if req.decision == REJECT:
                self._rejected.add(key)
            elif req.decision == ALLOW_REST:
                self._allow_rest = True
        if not req.decision:
            raise ToolError("the answer was stopped by the user: nothing was changed.")
        return req.decision in (APPLY, ALLOW_REST)

    def pending(self) -> Request | None:
        with self._lock:
            return self._pending

    def resolve(self, req_id: int, decision: str) -> bool:
        """Scelta dell'utente sulla richiesta `req_id`; False se non è più
        quella in attesa (già decisa, risposta fermata)."""
        if decision not in DECISIONS:
            return False
        with self._lock:
            req = self._pending
            if req is None or req.id != req_id or req.event.is_set():
                return False
            req.decision = decision
            req.event.set()
            return True

    def close(self) -> Request | None:
        """Nessuna nuova richiesta; quella in attesa finisce senza scelta
        (rifiutata). Restituisce la richiesta chiusa, se c'era."""
        with self._lock:
            self._closed = True
            req = self._pending
            if req is not None and not req.event.is_set():
                req.event.set()
                return req
            return None


def _atomic_write(path: Path, data: bytes) -> None:
    """File temporaneo nella stessa cartella + rename; mantiene i permessi."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = 0o644 & ~_umask()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".olladesk-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _umask() -> int:
    old = os.umask(0)
    os.umask(old)
    return old


def _diff(label: str, old: str, new: str) -> str:
    lines = list(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=label if old else "/dev/null", tofile=label, n=2))
    if len(lines) > MAX_DIFF_LINES:
        lines = lines[:MAX_DIFF_LINES] + [f"[... {len(lines) - MAX_DIFF_LINES} more diff lines]\n"]
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


class _Targets:
    """Percorsi di scrittura: come `_Files.resolve`, ma il file può non esistere."""

    def __init__(self, files: _Files):
        self.files = files

    def resolve(self, raw, want_dir: bool = False) -> tuple[Shared, str, Path]:
        files = self.files
        text = str(raw or "").strip().strip("/")
        if not text:
            raise ToolError("Missing path. " + files.overview())
        match = None
        for label in sorted(files._by_label, key=len, reverse=True):
            if text == label or text.startswith(label + "/"):
                match = label
                break
        if match is None:
            raise ToolError(f"'{text}' is not shared. " + files.overview())
        s = files._by_label[match]
        rel = text[len(match):].strip("/")
        if not s.is_dir:
            # un file allegato da solo: si modifica lui, accanto non si crea nulla
            if rel or want_dir:
                raise ToolError(f"'{match}' is a single shared file: only that file can be "
                                "changed, nothing can be created next to it.")
            if s.path.is_symlink():
                raise ToolError(f"'{match}' is a link: it cannot be changed.")
            return s, "", s.path
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if not parts:
            raise ToolError(f"'{match}' is the shared folder itself: give a path inside it.")
        if ".." in parts:
            raise ToolError("Paths with '..' are not allowed.")
        if any("\\" in p or "\0" in p for p in parts):
            raise ToolError("Invalid characters in the path.")
        rel = "/".join(parts)
        root = s.path.resolve()
        target = s.path.joinpath(*parts)
        # la cartella che lo contiene deve stare dentro la condivisione anche
        # seguendo i link; il file stesso non può essere un link
        try:
            parent_real = target.parent.resolve()
            parent_parts = list(parent_real.relative_to(root).parts)
        except (OSError, RuntimeError, ValueError):
            raise ToolError(f"'{text}' points outside the shared folder.") from None
        if target.is_symlink():
            raise ToolError(f"'{text}' is a link: it cannot be changed.")
        for checked, is_dir in ((parts, want_dir), (parent_parts + [parts[-1]], want_dir)):
            reason = files.excluded(s, checked, is_dir)
            if reason:
                # una regola fissa: i modelli piccoli altrimenti ritentano
                # la stessa scrittura fino al tetto dei giri
                raise ToolError(f"'{text}' {reason.replace('not shared', 'it cannot be changed')}"
                                " This is a fixed rule: do not try again, tell the user.")
        return s, rel, parent_real / parts[-1]


def make_write_tools(shared: list[Shared], approve: Callable[[Change], bool] | None = None,
                     journal: WriteJournal | None = None) -> list[Tool]:
    """I tool di scrittura sulle voci condivise; nessuno se non c'è nulla.

    `approve` decide ogni modifica (None = approvate tutte: solo prototipo e
    test). `journal` raccoglie le copie per l'annullamento.
    """
    if not shared:
        return []
    targets = _Targets(_Files(shared))
    jr = journal if journal is not None else WriteJournal()
    names = ", ".join(s.label + ("/" if s.is_dir else "") for s in shared)
    pending_dirs: list[Callable[[], None]] = []   # cartelle da creare dopo la conferma

    def ask(change: Change) -> None:
        if approve is not None and not approve(change):
            raise ToolError(f"the user rejected this change to {change.label}. "
                            "Do not try the same change again.")

    def current_text(real: Path, label: str) -> str:
        if not stat.S_ISREG(real.stat().st_mode):
            raise ToolError(f"'{label}' is not a regular file.")
        if real.stat().st_size > MAX_WRITE_BYTES:
            raise ToolError(f"'{label}' is too large to change ({real.stat().st_size} bytes).")
        try:
            return real.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            raise ToolError(f"'{label}' is not UTF-8 text: it cannot be changed.") from None

    def check_kind(real: Path, label: str) -> None:
        if context.classify(real) != "text":
            raise ToolError(f"'{label}' is not a text or code file: only text files "
                            "can be written.")

    def apply(real: Path, label: str, old: str | None, new: str, kind: str) -> None:
        data = new.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            raise ToolError(f"the new content is too large ({len(data)} bytes, at most "
                            f"{MAX_WRITE_BYTES}).")
        ask(Change(kind, label, real, _diff(label, old or "", new)))
        for make in pending_dirs:
            try:
                make()
            except OSError as e:
                raise ToolError(f"cannot create the folder of '{label}': "
                                f"{e.strerror or e}") from None
        jr.remember(real)
        try:
            _atomic_write(real, data)
        except OSError as e:
            raise ToolError(f"cannot write '{label}': {e.strerror or e}") from None

    def write_file(args: dict) -> str:
        s, rel, real = targets.resolve(args.get("path"))
        label = _label_path(s, rel)
        content = args.get("content")
        if not isinstance(content, str):
            raise ToolError("Missing content (the whole text of the file).")
        check_kind(real, label)
        old = current_text(real, label) if real.exists() else None
        if old == content:
            return f"{label} already has exactly this content: nothing changed."
        # le cartelle mancanti si creano con il file (tutti i modelli provati
        # scrivevano prima il file e solo dopo l'errore chiamavano make_dir)
        missing = _missing_dirs(real.parent, s.path.resolve())
        if any(p.exists() for p in missing):
            raise ToolError(f"the folder of '{label}' is a file.")
        if missing:
            def make_parents() -> None:
                real.parent.mkdir(parents=True, exist_ok=True)
                jr.created_dirs.extend(reversed(missing))

            pending_dirs.append(make_parents)
        try:
            apply(real, label, old, content, "replace" if old is not None else "create")
        finally:
            pending_dirs.clear()
        n = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
        verb = "Replaced" if old is not None else "Created"
        return f"{verb} {label} ({n} lines, {len(content.encode('utf-8'))} bytes)."

    def edit_file(args: dict) -> str:
        s, rel, real = targets.resolve(args.get("path"))
        label = _label_path(s, rel)
        old_text = args.get("old_text")
        new_text = args.get("new_text")
        if not isinstance(old_text, str) or not old_text:
            raise ToolError("Missing old_text: an exact piece of the current file to replace.")
        if not isinstance(new_text, str):
            raise ToolError("Missing new_text (use an empty string to remove old_text).")
        if not real.exists():
            raise ToolError(f"'{label}' does not exist: create it with write_file.")
        check_kind(real, label)
        old = current_text(real, label)
        count = old.count(old_text)
        replace_all = args.get("replace_all") is True
        note = ""
        if count == 0:
            # i modelli contano male i rientri copiando da read_file (le righe
            # hanno il numero davanti): stesse righe a meno del rientro, una
            # sola volta, si accettano rientrando new_text come il file
            fixed = _indent_match(old, old_text, new_text)
            if fixed is None:
                raise ToolError(_not_found(label, old, old_text))
            begin, end, new_text = fixed
            new = old[:begin] + new_text + old[end:]
            first = begin
            note = " (old_text matched with a different indentation: new_text was re-indented)"
        else:
            if count > 1 and not replace_all:
                raise ToolError(f"old_text appears {count} times in {label}: add more of the "
                                "surrounding lines so it is unique, or set replace_all to true.")
            new = old.replace(old_text, new_text) if replace_all \
                else old.replace(old_text, new_text, 1)
            first = old.find(old_text)
        if new == old:
            return "old_text and new_text are the same: nothing changed."
        apply(real, label, old, new, "edit")
        start = old[:first].count("\n") + 1   # prima sostituzione
        lines = new.splitlines()
        lo = max(1, start - MAX_REPLY_CONTEXT)
        hi = min(len(lines), start + new_text.count("\n") + MAX_REPLY_CONTEXT)
        snippet = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(lo, hi + 1))
        times = f"{count} occurrences" if replace_all and count > 1 else "1 occurrence"
        return f"Replaced {times} in {label}{note}. Lines {lo}-{hi} now:\n{snippet}"

    def make_dir(args: dict) -> str:
        s, rel, real = targets.resolve(args.get("path"), want_dir=True)
        label = _label_path(s, rel)
        if real.is_dir():
            return f"{label}/ already exists."
        if real.exists():
            raise ToolError(f"'{label}' exists and is a file.")
        ask(Change("mkdir", label, real))
        missing = _missing_dirs(real, s.path.resolve())
        try:
            real.mkdir(parents=True)
        except OSError as e:
            raise ToolError(f"cannot create '{label}': {e.strerror or e}") from None
        jr.created_dirs.extend(reversed(missing))
        return f"Created folder {label}/."

    path_prop = {"type": "string",
                 "description": f"Path starting with a shared name. Shared: {names}"}
    return [
        Tool("write_file",
             "Create a new text file, or replace the WHOLE content of an existing one, in "
             f"the shared folders ({names}); missing folders are created. For small changes to an existing file use "
             "edit_file instead. The user approves every change.",
             write_file,
             {"type": "object", "required": ["path", "content"], "properties": {
                 "path": path_prop,
                 "content": {"type": "string", "description": "The complete text of the file"},
             }},
             read_only=False),
        Tool("edit_file",
             "Change part of a shared text file: replace old_text, an exact piece of the "
             "current file (copy it from read_file, without the line numbers), with new_text. "
             "old_text must appear exactly once unless replace_all is true. The user "
             "approves every change.",
             edit_file,
             {"type": "object", "required": ["path", "old_text", "new_text"], "properties": {
                 "path": path_prop,
                 "old_text": {"type": "string", "description": "Exact text to replace"},
                 "new_text": {"type": "string", "description": "Replacement text"},
                 "replace_all": {"type": "boolean",
                                 "description": "Replace every occurrence (default false)"},
             }},
             read_only=False),
        Tool("make_dir",
             f"Create a folder (and missing parent folders) inside a shared folder ({names}).",
             make_dir,
             {"type": "object", "required": ["path"], "properties": {"path": path_prop}},
             read_only=False),
    ]


def _missing_dirs(path: Path, root: Path) -> list[Path]:
    """Cartelle da `path` in su che non esistono ancora (dentro `root`)."""
    out = []
    p = path
    while p != root and not p.is_dir():
        out.append(p)
        p = p.parent
    return out


def _indent_match(text: str, old_text: str, new_text: str) -> tuple[int, int, str] | None:
    """(inizio, fine, new_text rientrato) se old_text corrisponde a righe
    intere del file a meno del rientro, in un punto solo; altrimenti None.

    Solo spazi o solo tab, come nel file: con rientri misti non si indovina.
    """
    want = old_text.split("\n")
    tail_nl = want[-1] == ""
    if tail_nl:
        want = want[:-1]
    if not any(w.strip() for w in want):
        return None
    lines = text.splitlines(keepends=True)
    bare = [ln.rstrip("\r\n") for ln in lines]
    n = len(want)
    hits = [i for i in range(len(bare) - n + 1)
            if all(bare[i + k].strip() == want[k].strip() for k in range(n))]
    if len(hits) != 1:
        return None
    i = hits[0]
    k0 = next(k for k in range(n) if want[k].strip())
    have_ind = bare[i + k0][:len(bare[i + k0]) - len(bare[i + k0].lstrip())]
    want_ind = want[k0][:len(want[k0]) - len(want[k0].lstrip())]
    chars = set(have_ind + want_ind)
    if len(chars) > 1:
        return None
    delta = len(have_ind) - len(want_ind)
    unit = chars.pop() if chars else " "

    def shift(line: str) -> str:
        if not line.strip():
            return line
        if delta >= 0:
            return unit * delta + line
        lead = len(line) - len(line.lstrip(unit))
        return line[min(-delta, lead):]

    new = "\n".join(shift(ln) for ln in new_text.split("\n"))
    begin = sum(len(ln) for ln in lines[:i])
    end = begin + sum(len(ln) for ln in lines[i:i + n])
    if not tail_nl:
        # old_text finiva senza a capo: l'a capo dell'ultima riga resta
        end -= len(lines[i + n - 1]) - len(bare[i + n - 1])
    return begin, end, new


def _not_found(label: str, text: str, old_text: str) -> str:
    """Errore di edit_file quando old_text non c'è, con un aiuto per il modello:
    i numeri di riga copiati da read_file, spazi diversi, la riga più simile."""
    msg = f"old_text was not found in {label}."
    first = next((ln for ln in old_text.splitlines() if ln.strip()), "")
    if first and first.lstrip()[:1].isdigit() and "  " in first:
        msg += " It seems to include line numbers from read_file: remove them."
    squash = " ".join(old_text.split())
    if squash and squash in " ".join(text.split()):
        msg += " The same words exist with different spaces or line breaks: copy them exactly."
    elif first.strip():
        lines = text.splitlines()
        close = difflib.get_close_matches(first.strip(), [ln.strip() for ln in lines], n=1,
                                          cutoff=0.6)
        if close:
            n = next(i for i, ln in enumerate(lines, 1) if ln.strip() == close[0])
            msg += f" The most similar line is {n}: {lines[n - 1].strip()[:200]!r}."
    return msg + " Read the file again with read_file and copy old_text exactly."

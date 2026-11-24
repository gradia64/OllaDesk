"""Tool di scrittura sui file condivisi in chat (0.5, voci W2 e W3).

Il modello può creare o sostituire un file di testo (`write_file`),
cambiarne un pezzo esatto (`edit_file`) e creare cartelle (`make_dir`);
con «Accesso completo» anche spostare o rinominare (`move_file`) e
mandare nel cestino (`delete_file`) file e cartelle,
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
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import context
from .file_tools import Shared, _Files, _label_path
from .tools import Tool, ToolError

MAX_WRITE_BYTES = 512 * 1024     # dimensione massima di un file scritto
MAX_DIFF_LINES = 400             # righe del diff mostrate per la conferma
DIFF_CUT_MARK = "[... "          # inizio dell'ultima riga di un diff tagliato
MAX_REPLY_CONTEXT = 3            # righe di contesto nella risposta di edit_file
# righe più lunghe nella scheda: di una riga cambiata si mostra solo il pezzo
# attorno alla modifica, con EXCERPT_MARK al posto delle parti uguali
LONG_LINE = 500
EXCERPT_CONTEXT = 120
EXCERPT_MARK = "⟪…⟫"
MAX_REJECTS_PER_PATH = 3         # rifiuti su un percorso, poi non si chiede più

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

# con il livello «full»: anche spostare e cestinare
FULL_NOTE = (
    "You can create and change text files in the folders and files shared in this "
    "conversation with write_file, edit_file and make_dir, move or rename files and "
    "folders with move_file and move them to the trash with delete_file. Read a file with "
    "read_file before changing it. For a small change use edit_file with an exact piece "
    "of the current text; use write_file only for new files or to rewrite a whole short "
    "file. The user sees and approves every change: if a change is rejected, do not try "
    "the same change again. Move or delete only what the user asked for. You cannot run "
    "commands. Never claim a change that a tool did not confirm."
)

# modifiche coperte da «consenti per il resto della risposta»: spostare e
# cestinare chiedono sempre
ALLOW_REST_KINDS = frozenset({"create", "replace", "edit", "mkdir"})
# voci di una cartella da spostare o cestinare: oltre, la si rifiuta
MAX_TREE_ENTRIES = 2000
MAX_LISTED_ENTRIES = 60          # voci elencate nella conferma di una cartella


@dataclass
class Change:
    """Una modifica proposta, per la conferma."""

    kind: str          # "create", "replace", "edit", "mkdir", "move", "delete"
    label: str         # percorso per il modello e per l'utente (progetto/src/a.py)
    path: Path         # percorso vero
    diff: str = ""     # diff unificato (vuoto per mkdir e move; contenuto per delete)
    target: str = ""   # destinazione di move (etichetta)


@dataclass
class WriteJournal:
    """Modifiche applicate in una risposta, per annullarle.

    Un elenco di operazioni nell'ordine in cui sono avvenute: prima di ogni
    scrittura una copia del file (o il segno che non esisteva), le cartelle
    create, gli spostamenti, i file mandati nel cestino (con il posto nel
    cestino, se noto). `undo` le disfa dall'ultima alla prima.

    Durante la risposta le copie stanno in una cartella temporanea; a fine
    risposta `save` le sposta nella cartella dell'app con `journal.json`, e
    `load` le rilegge: il pulsante «Annulla» vale anche dopo un riavvio.
    `save` annota anche lo stato finale di ogni percorso toccato, così
    `changed_since` sa dire quali file sono cambiati dopo la risposta.
    """

    ops: list[dict] = field(default_factory=list)
    final: dict[str, str | None] = field(default_factory=dict)   # percorso → impronta
    _dir: Path | None = None
    _saved: bool = False
    # il thread dei tool scrive tenendo `lock`; il motore lo prende per
    # salvare o scartare il registro e lo chiude: dopo, nessuna scrittura
    # (Stop o chiusura subito dopo «Applica»)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    closed: bool = False
    in_trash: list[str] = field(default_factory=list)   # rimasti nel cestino dopo undo

    def close(self) -> None:
        with self.lock:
            self.closed = True

    def remember(self, path: Path, label: str = "", data: bytes | None = None) -> None:
        """Stato di `path` prima di una scrittura: `data` è il contenuto
        attuale già letto (None: lo legge qui, se il file c'è)."""
        op = {"kind": "write", "path": str(path), "label": label, "copy": None}
        if data is None and path.exists():
            data = path.read_bytes()
        if data is not None:
            if self._dir is None:
                self._dir = Path(tempfile.mkdtemp(prefix="olladesk-undo-"))
            copy = self._dir / f"copy-{len(self.ops)}"
            fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            op["copy"] = copy.name
        self.ops.append(op)

    def created_dir(self, path: Path, label: str = "") -> None:
        self.ops.append({"kind": "mkdir", "path": str(path), "label": label})

    def moved(self, src: Path, dst: Path, label: str = "", target: str = "") -> None:
        self.ops.append({"kind": "move", "path": str(src), "dst": str(dst),
                         "label": label, "target": target})

    def trashed(self, path: Path, where: Path | None, label: str = "") -> None:
        self.ops.append({"kind": "trash", "path": str(path), "label": label,
                         "where": str(where) if where is not None else None})

    def __bool__(self) -> bool:
        return bool(self.ops)

    # -- persistenza

    def _touched(self) -> dict[str, str]:
        """Percorsi toccati (vero → etichetta) da controllare prima di annullare."""
        out: dict[str, str] = {}
        for op in self.ops:
            out.setdefault(op["path"], op.get("label") or Path(op["path"]).name)
            if op["kind"] == "move":
                out.setdefault(op["dst"], op.get("target") or Path(op["dst"]).name)
        return out

    def save(self, folder: Path) -> None:
        """Copie e `journal.json` in `folder` (nuova), con lo stato finale."""
        folder.mkdir(parents=True, exist_ok=False)
        os.chmod(folder, 0o700)
        for op in self.ops:
            if op.get("copy") and self._dir is not None:
                shutil.move(str(self._dir / op["copy"]), str(folder / op["copy"]))
        self.final = {p: _fingerprint(Path(p)) for p in self._touched()}
        data = {"version": 1, "ops": self.ops, "final": self.final}
        (folder / "journal.json").write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                             encoding="utf-8")
        self.discard()
        self._dir = folder
        self._saved = True

    @classmethod
    def load(cls, folder: Path) -> WriteJournal | None:
        """Registro salvato, o None se manca o non è valido. Il file sta
        nella cartella dell'app ma si tratta come non attendibile: un
        registro con un campo strano si rifiuta tutto, e `undo` controlla
        comunque che ogni percorso stia nelle cartelle condivise."""
        try:
            data = json.loads((folder / "journal.json").read_text(encoding="utf-8"))
            if data.get("version") != 1:
                return None
            ops = data["ops"]
            final = data.get("final") or {}
            if not isinstance(ops, list) or not isinstance(final, dict) \
                    or not all(_valid_op(op) for op in ops) \
                    or not all(isinstance(k, str) and (v is None or isinstance(v, str))
                               for k, v in final.items()):
                return None
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None
        return cls(ops=ops, final=dict(final), _dir=folder, _saved=True)

    def changed_since(self, roots: list[Path] | None = None) -> list[str]:
        """Etichette dei percorsi cambiati dopo `save` (annullando si perdono
        quelle modifiche); con `roots`, solo quelli dentro le condivisioni."""
        touched = self._touched()
        return [touched[p] for p, fp in self.final.items()
                if p in touched and (roots is None or _inside(Path(p), roots))
                and _fingerprint(Path(p)) != fp]

    def undo(self, roots: list[Path] | None = None) -> list[str]:
        """Rimette tutto com'era; restituisce le etichette dei percorsi che non
        è riuscito a ripristinare (una destinazione occupata nel frattempo, un
        cestino svuotato, un percorso fuori da `roots`, le cartelle condivise
        dalla chat). I file cestinati senza posto noto nel cestino (portale
        del Flatpak) finiscono in `in_trash`: si ripristinano dal cestino."""
        failed = []
        self.in_trash = []
        for op in reversed(self.ops):
            kind, path = op["kind"], Path(op["path"])
            try:
                if roots is not None and not (
                        _inside(path, roots)
                        and (kind != "move" or _inside(Path(op["dst"]), roots))):
                    raise OSError("outside the shared folders")
                if kind == "trash" and op.get("where") is None:
                    self.in_trash.append(op.get("label") or path.name)
                    continue
                if kind == "write":
                    if op.get("copy") is None:
                        path.unlink(missing_ok=True)
                    else:
                        if self._dir is None:
                            raise OSError("copy missing")
                        _atomic_write(path, (self._dir / op["copy"]).read_bytes())
                elif kind == "mkdir":
                    try:
                        path.rmdir()          # solo se vuota: niente perdite
                    except OSError:
                        pass
                elif kind == "move":
                    if path.exists() or path.is_symlink():
                        raise OSError("destination taken")
                    shutil.move(op["dst"], str(path))
                elif kind == "trash":
                    where = op.get("where")
                    _restore_from_trash(path, Path(where) if where else None)
            except Exception:  # noqa: BLE001 — un'operazione non blocca le altre
                failed.append(op.get("label") or path.name)
        self.ops.clear()
        self.discard()
        return failed

    def discard(self) -> None:
        """Toglie le copie (temporanee o salvate)."""
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None


_COPY_NAME = re.compile(r"^copy-[0-9]{1,6}$")


def _valid_op(op) -> bool:
    """Un'operazione di `journal.json` con i campi del tipo giusto."""
    if not isinstance(op, dict) or not isinstance(op.get("label", ""), str):
        return False
    kind, path = op.get("kind"), op.get("path")
    if not (isinstance(path, str) and os.path.isabs(path)):
        return False
    if kind == "write":
        copy = op.get("copy")
        return copy is None or (isinstance(copy, str) and bool(_COPY_NAME.match(copy)))
    if kind == "mkdir":
        return True
    if kind == "move":
        dst = op.get("dst")
        return isinstance(dst, str) and os.path.isabs(dst) and isinstance(op.get("target", ""), str)
    if kind == "trash":
        where = op.get("where")
        if where is None:
            return True
        # solo una voce della cartella files/ di un cestino
        return isinstance(where, str) and os.path.isabs(where) and \
            Path(where).parent.name == "files"
    return False


def _inside(path: Path, roots: list[Path]) -> bool:
    """`path` è una delle voci condivise o sta dentro una cartella condivisa,
    confrontando i percorsi veri (nel Flatpak /run/user/<uid>/doc è un
    link a /run/flatpak/doc)."""
    try:
        path = Path(os.path.realpath(path.parent)) / path.name
        roots = [Path(os.path.realpath(r)) for r in roots]
    except (OSError, ValueError):
        return False
    return any(path == r or r in path.parents for r in roots)


def _fingerprint(path: Path) -> str | None:
    """Stato di un percorso: None se non c'è, "dir" per una cartella,
    l'hash del contenuto per un file."""
    try:
        if path.is_symlink():
            return "link"
        if path.is_dir():
            return "dir"
        if not path.exists():
            return None
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return "?"


def _stat_key(path: Path) -> tuple | None:
    """Identità di un file da spostare o cestinare (senza leggerlo tutto)."""
    try:
        st = path.lstat()
    except OSError:
        return None
    return (st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns)


class TrashRefused(OSError):
    """Il portale Trash del Flatpak rifiuta il file. Con xdg-desktop-portal
    1.22 succede a ogni file arrivato dal portale dei documenti (cioè a
    tutto ciò che si allega alla chat), anche a GLib: il limite è del
    portale, non dell'app."""


def move_to_trash(path: Path) -> Path | None:
    """Manda `path` nel cestino del sistema; restituisce dove è finito (None
    se il cestino non lo dice, come il portale del Flatpak). OSError se non
    riesce: non si cancella mai per sempre."""
    if _in_flatpak():
        _portal_trash(path)
        return None
    from PySide6.QtCore import QFile

    f = QFile(str(path))
    if not f.moveToTrash():
        raise OSError(f.errorString() or "the trash is not available")
    return Path(f.fileName())


def _in_flatpak() -> bool:
    return os.path.exists("/.flatpak-info")


def _portal_trash(path: Path) -> None:
    """Cestino attraverso il portale Trash (Flatpak): dentro la sandbox il
    cestino dell'host non si raggiunge con i file."""
    try:
        from jeepney import DBusAddress, MessageType, new_method_call
        from jeepney.io.blocking import open_dbus_connection
    except ImportError:
        raise OSError("the trash portal is not available") from None
    # come GLib (gtrashportal.c): un file aperto in scrittura, una cartella
    # con O_PATH; il portale rifiuta ciò che l'app non può scrivere
    if path.is_dir():
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY)
    else:
        fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    addr = DBusAddress("/org/freedesktop/portal/desktop",
                       bus_name="org.freedesktop.portal.Desktop",
                       interface="org.freedesktop.portal.Trash")
    try:
        with open_dbus_connection(bus="SESSION", enable_fds=True) as conn:
            reply = conn.send_and_get_reply(
                new_method_call(addr, "TrashFile", "h", (fd,)), timeout=10)
    except OSError:
        raise
    except Exception as e:  # noqa: BLE001 — bus assente, tempo scaduto
        raise OSError(str(e) or "the trash portal did not answer") from None
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    if reply.header.message_type == MessageType.error or not reply.body or reply.body[0] != 1:
        raise TrashRefused("the trash portal refused the file")


def _restore_from_trash(path: Path, where: Path | None) -> None:
    if where is None or not (where.exists() or where.is_symlink()):
        raise OSError("not found in the trash")
    if path.exists() or path.is_symlink():
        raise OSError("destination taken")
    if where.parent.name != "files" or not (where.parent.parent / "info").is_dir():
        raise OSError("not a trash folder")
    shutil.move(str(where), str(path))     # il cestino può stare su un altro disco
    info = where.parent.parent / "info" / (where.name + ".trashinfo")
    info.unlink(missing_ok=True)


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
        """La richiesta per l'interfaccia e il telefono (solo testo).

        I caratteri invisibili o di controllo (bidi, larghezza zero, a capo
        dentro un nome) si mostrano come ⟦U+XXXX⟧: chi approva deve vedere
        il testo com'è, non come lo dispone il bidi (Trojan Source).
        """
        added = removed = 0
        lines = self.change.diff.splitlines()
        head = 2 if lines[:1] and lines[0].startswith("--- ") and \
            lines[1:2] and lines[1].startswith("+++ ") else 0
        for line in lines[head:]:
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1
        diff, n_diff = visible(self.change.diff)
        label, n_label = visible(self.change.label)
        target, n_target = visible(self.change.target)
        return {"id": self.id, "kind": self.change.kind, "label": label,
                "target": target, "diff": diff,
                "added": added, "removed": removed,
                # diff (o elenco) tagliato: la scheda lo dice chiaramente
                "truncated": any(line.startswith(DIFF_CUT_MARK) for line in lines[-1:]),
                # righe lunghe o accorciate attorno alla modifica
                "long_lines": EXCERPT_MARK in diff or any(len(line) > LONG_LINE for line in lines),
                "hidden": n_diff + n_label + n_target,
                # spostare e cestinare chiedono sempre: niente «consenti per il resto»
                "allow_rest": self.change.kind in ALLOW_REST_KINDS}


def _hidden_char(ch: str) -> bool:
    return ch not in "\n\t" and unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp")


def visible(text: str) -> tuple[str, int]:
    """`text` con i caratteri invisibili o di controllo scritti come
    ⟦U+XXXX⟧ (un a capo CRLF resta a capo) e quanti sono."""
    out, n = [], 0
    for i, ch in enumerate(text):
        if ch == "\r" and text[i + 1:i + 2] == "\n":
            continue
        if _hidden_char(ch):
            out.append(f"⟦U+{ord(ch):04X}⟧")
            n += 1
        else:
            out.append(ch)
    return "".join(out), n


def bad_name(name: str) -> bool:
    """Un nome di file con caratteri invisibili o di controllo."""
    return any(_hidden_char(ch) or ch == "\n" or ch == "\t" for ch in name)


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
        self._rejects: dict[str, int] = {}      # rifiuti per percorso
        self._next = 0

    @staticmethod
    def _key(change: Change) -> str:
        return "\0".join((change.kind, change.label, change.target, change.diff))

    def ask(self, change: Change) -> bool:
        key = self._key(change)
        with self._lock:
            if self._closed:
                raise ToolError("the answer was stopped by the user: nothing was changed.")
            if key in self._rejected:
                raise ToolError(f"the user already rejected this same change to {change.label} "
                                "earlier in this answer, so it was not proposed again. Do not "
                                "repeat it: tell the user or ask what they want instead.")
            if self._rejects.get(change.label, 0) >= MAX_REJECTS_PER_PATH:
                raise ToolError(f"the user rejected {MAX_REJECTS_PER_PATH} changes to "
                                f"{change.label} in this answer, so no more changes to it are "
                                "proposed. Stop and ask the user what they want.")
            if self._allow_rest and change.kind in ALLOW_REST_KINDS:
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
                self._rejects[change.label] = self._rejects.get(change.label, 0) + 1
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
    """Scrittura atomica di `path` (per l'annullamento)."""
    dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        _atomic_write_at(dfd, path.name, data)
    finally:
        os.close(dfd)


def _atomic_write_at(dfd: int, name: str, data: bytes) -> None:
    """File temporaneo nella cartella aperta `dfd` + rename; mantiene
    permessi e gruppo. Tutto relativo al descrittore: un link messo al
    posto di una cartella del percorso non si segue."""
    try:
        st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        mode, gid = stat.S_IMODE(st.st_mode), st.st_gid
    except FileNotFoundError:
        mode, gid = 0o644 & ~_umask(), None
    tmp = f".olladesk-{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=dfd)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            if gid is not None:
                try:
                    os.fchown(fh.fileno(), -1, gid)
                except OSError:
                    pass
            os.fchmod(fh.fileno(), mode)
            os.fsync(fh.fileno())
        os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dfd)
        except OSError:
            pass
        raise


def _open_dir(root: Path, parts, create: bool = False) -> tuple[int, list[Path]]:
    """Apre la cartella `root/parts` scendendo un nome alla volta senza
    seguire link (O_NOFOLLOW); con `create` crea quelle che mancano.
    Restituisce il descrittore e le cartelle create, dall'alto in basso."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    created: list[Path] = []
    cur = root
    try:
        for name in parts:
            cur = cur / name
            if create:
                try:
                    os.mkdir(name, 0o777, dir_fd=fd)
                    created.append(cur)
                except FileExistsError:
                    pass
            nfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                          dir_fd=fd)
            os.close(fd)
            fd = nfd
    except BaseException:
        os.close(fd)
        raise
    return fd, created


def _read_at(dfd: int, name: str) -> bytes | None:
    """Contenuto del file `name` nella cartella `dfd` (None se non c'è);
    OSError se è un link o non è un file regolare."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=dfd)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise OSError("not a regular file")
        return fh.read()


def _umask() -> int:
    old = os.umask(0)
    os.umask(old)
    return old


def _diff(label: str, old: str, new: str) -> str:
    lines = _excerpt_long(list(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=label if old else "/dev/null", tofile=label, n=2)))
    if len(lines) > MAX_DIFF_LINES:
        lines = lines[:MAX_DIFF_LINES] + [f"{DIFF_CUT_MARK}{len(lines) - MAX_DIFF_LINES} more diff lines]\n"]
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


def _excerpt_long(lines: list[str]) -> list[str]:
    """Una riga lunga cambiata (una «-» seguita dalla sua «+») si accorcia
    attorno al punto che cambia: in un JSON o un file minimizzato su una
    riga la modifica altrimenti sta migliaia di caratteri più in là."""
    out = lines[:2]
    i = 2
    while i < len(lines):
        j = i
        while j < len(lines) and lines[j].startswith("-"):
            j += 1
        k = j
        while k < len(lines) and lines[k].startswith("+"):
            k += 1
        if j > i and k - j == j - i:
            pairs = [_cut_pair(a, b) if max(len(a), len(b)) > LONG_LINE else (a, b)
                     for a, b in zip(lines[i:j], lines[j:k])]
            out += [a for a, _b in pairs] + [b for _a, b in pairs]
            i = k
        else:
            out.extend(lines[i:max(k, i + 1)])
            i = max(k, i + 1)
    return out


def _cut_pair(a: str, b: str) -> tuple[str, str]:
    """Le due righe ("-…", "+…") ridotte alla parte diversa, con
    EXCERPT_CONTEXT caratteri attorno e EXCERPT_MARK dove si taglia."""
    ta, tb = a[1:].rstrip("\n"), b[1:].rstrip("\n")
    p = 0
    while p < min(len(ta), len(tb)) and ta[p] == tb[p]:
        p += 1
    q = 0
    while q < min(len(ta), len(tb)) - p and ta[-1 - q] == tb[-1 - q]:
        q += 1
    start = max(0, p - EXCERPT_CONTEXT)

    def cut(t: str) -> str:
        end = min(len(t), len(t) - q + EXCERPT_CONTEXT)
        return (EXCERPT_MARK if start else "") + t[start:end] + (EXCERPT_MARK if end < len(t) else "")

    return a[0] + cut(ta) + "\n", b[0] + cut(tb) + "\n"


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
        if any("\\" in p or "\0" in p or bad_name(p) for p in parts):
            raise ToolError("Invalid characters in the path (control or invisible "
                            "characters are not allowed in file names).")
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
                     journal: WriteJournal | None = None, *, full: bool = False,
                     trash: Callable[[Path], Path | None] | None = None) -> list[Tool]:
    """I tool di scrittura sulle voci condivise; nessuno se non c'è nulla.

    `approve` decide ogni modifica (None = approvate tutte: solo prototipo e
    test). `journal` raccoglie le copie per l'annullamento. Con `full`
    (livello «Accesso completo») anche `move_file` e `delete_file`; `trash`
    manda nel cestino (predefinito: `move_to_trash`; i test ne passano uno
    finto, per non riempire il cestino vero).
    """
    if not shared:
        return []
    targets = _Targets(_Files(shared, writable=True))
    jr = journal if journal is not None else WriteJournal()
    names = ", ".join(s.label + ("/" if s.is_dir else "") for s in shared)
    stopped = "the answer was stopped by the user: nothing was changed."

    def ask(change: Change) -> None:
        if approve is not None and not approve(change):
            raise ToolError(f"the user rejected this change to {change.label}. "
                            "Do not try the same change again.")

    def changed(label: str) -> ToolError:
        return ToolError(f"'{label}' changed while the user was deciding, so nothing was "
                         "changed. Look at it again (read_file or list_files) before "
                         "proposing a change.")

    def again(raw, real: Path, want_dir: bool = False) -> None:
        # dopo l'attesa della conferma (anche minuti) il percorso deve
        # portare allo stesso posto, sempre dentro la condivisione
        try:
            _s, _rel, now = targets.resolve(raw, want_dir=want_dir)
        except ToolError:
            now = None
        if now != real:
            raise changed(str(raw))

    def dir_of(s: Shared, real: Path) -> tuple[Path, tuple]:
        """Cartella condivisa da cui scendere e nomi fino alla cartella di `real`."""
        root = s.path.resolve() if s.is_dir else real.parent
        return root, real.parent.relative_to(root).parts

    def current_text(real: Path, label: str) -> str:
        if not stat.S_ISREG(real.stat().st_mode):
            raise ToolError(f"'{label}' is not a regular file.")
        if real.stat().st_size > MAX_WRITE_BYTES:
            raise ToolError(f"'{label}' is too large to change ({real.stat().st_size} bytes).")
        if not os.access(real, os.W_OK):
            raise ToolError(f"'{label}' is read-only: it cannot be changed. Tell the user.")
        try:
            return real.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            raise ToolError(f"'{label}' is not UTF-8 text: it cannot be changed.") from None

    def check_kind(real: Path, label: str) -> None:
        if context.classify(real) != "text":
            raise ToolError(f"'{label}' is not a text or code file: only text files "
                            "can be written.")

    def apply(s: Shared, raw, real: Path, label: str, old: str | None, new: str,
              kind: str) -> None:
        data = new.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            raise ToolError(f"the new content is too large ({len(data)} bytes, at most "
                            f"{MAX_WRITE_BYTES}).")
        ask(Change(kind, label, real, _diff(label, old or "", new)))
        again(raw, real)
        root, parts = dir_of(s, real)
        with jr.lock:
            if jr.closed:
                raise ToolError(stopped)
            try:
                dfd, created = _open_dir(root, parts, create=True)
            except OSError as e:
                raise ToolError(f"cannot create the folder of '{label}': "
                                f"{e.strerror or e}") from None
            try:
                for d in created:
                    jr.created_dir(d)
                try:
                    now = _read_at(dfd, real.name)
                except OSError:
                    raise changed(label) from None
                # il file è ancora quello del diff mostrato all'utente?
                if now != (old.encode("utf-8") if old is not None else None):
                    raise changed(label)
                jr.remember(real, label, now)
                _atomic_write_at(dfd, real.name, data)
            except OSError as e:
                raise ToolError(f"cannot write '{label}': {e.strerror or e}") from None
            finally:
                os.close(dfd)

    def write_file(args: dict) -> str:
        raw = args.get("path")
        s, rel, real = targets.resolve(raw)
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
        apply(s, raw, real, label, old, content, "replace" if old is not None else "create")
        n = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
        verb = "Replaced" if old is not None else "Created"
        return f"{verb} {label} ({n} lines, {len(content.encode('utf-8'))} bytes)."

    def edit_file(args: dict) -> str:
        raw = args.get("path")
        s, rel, real = targets.resolve(raw)
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
        if "\r\n" in old and old.count("\r\n") == old.count("\n"):
            # file con a capo CRLF: il modello scrive «\n», il file resta CRLF
            old_text, new_text = _crlf(old_text), _crlf(new_text)
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
        apply(s, raw, real, label, old, new, "edit")
        start = old[:first].count("\n") + 1   # prima sostituzione
        lines = new.splitlines()
        lo = max(1, start - MAX_REPLY_CONTEXT)
        hi = min(len(lines), start + new_text.count("\n") + MAX_REPLY_CONTEXT)
        snippet = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(lo, hi + 1))
        times = f"{count} occurrences" if replace_all and count > 1 else "1 occurrence"
        return f"Replaced {times} in {label}{note}. Lines {lo}-{hi} now:\n{snippet}"

    def make_dirs(s: Shared, real: Path, label: str) -> None:
        """Crea `real` e le cartelle che mancano, senza seguire link."""
        root = s.path.resolve()
        try:
            dfd, created = _open_dir(root, real.relative_to(root).parts, create=True)
        except OSError as e:
            raise ToolError(f"cannot create '{label}': {e.strerror or e}") from None
        os.close(dfd)
        for d in created:
            jr.created_dir(d)

    def make_dir(args: dict) -> str:
        raw = args.get("path")
        s, rel, real = targets.resolve(raw, want_dir=True)
        label = _label_path(s, rel)
        if real.is_dir():
            return f"{label}/ already exists."
        if real.exists():
            raise ToolError(f"'{label}' exists and is a file.")
        ask(Change("mkdir", label, real))
        again(raw, real, want_dir=True)
        with jr.lock:
            if jr.closed:
                raise ToolError(stopped)
            make_dirs(s, real, label)
        return f"Created folder {label}/."

    trash_fn = trash or move_to_trash

    def existing(raw) -> tuple[Shared, str, Path, str]:
        # una cartella si controlla con le regole delle cartelle
        # (`node_modules/`, `.git/`), un file con quelle dei file
        dir_error: ToolError | None = None
        try:
            s, rel, real = targets.resolve(raw, want_dir=True)
            is_dir = s.is_dir and real.is_dir() and not real.is_symlink()
        except ToolError as e:
            dir_error, is_dir = e, False
        if not is_dir:
            s, rel, real = targets.resolve(raw)
            if dir_error is not None and real.is_dir():
                raise dir_error
        label = _label_path(s, rel)
        if not (real.exists() or real.is_symlink()):
            raise ToolError(f"'{label}' does not exist.")
        if not is_dir and not stat.S_ISREG(real.stat().st_mode):
            raise ToolError(f"'{label}' is not a regular file or folder.")
        return s, rel, real, label

    def check_tree(s: Shared, rel: str, real: Path, label: str) -> list[str]:
        """Voci di una cartella da spostare o cestinare. Dentro non ci deve
        essere nulla che il modello non vede (nascosti, `.git/`, dipendenze,
        credenziali, link, file speciali): sposterebbe o cancellerebbe alla
        cieca."""
        out: list[str] = []
        base = rel.split("/") if rel else []
        for dirpath, dirnames, filenames in os.walk(real):
            sub = list(Path(dirpath).relative_to(real).parts)
            for name, is_dir in [(n, True) for n in sorted(dirnames)] + \
                    [(n, False) for n in sorted(filenames)]:
                item = "/".join(sub + [name])
                entry = Path(dirpath) / name
                if entry.is_symlink():
                    raise ToolError(f"'{label}' contains a link ({item}): it cannot be moved "
                                    "or deleted. This is a fixed rule: do not try again, "
                                    "tell the user.")
                if not is_dir and not stat.S_ISREG(entry.lstat().st_mode):
                    raise ToolError(f"'{label}' contains a special file ({item}): it cannot "
                                    "be moved or deleted. This is a fixed rule: do not try "
                                    "again, tell the user.")
                reason = targets.files.excluded(s, base + sub + [name], is_dir)
                if reason:
                    why = reason.split(":")[0]
                    raise ToolError(f"'{label}' contains {item}, which {why}: the folder "
                                    "cannot be moved or deleted. This is a fixed rule: do "
                                    "not try again, tell the user.")
                out.append(item + ("/" if is_dir else ""))
                if len(out) > MAX_TREE_ENTRIES:
                    raise ToolError(f"'{label}' has more than {MAX_TREE_ENTRIES} files and "
                                    "folders: it is too large to move or delete here.")
        return out

    def snapshot(s: Shared, rel: str, real: Path, label: str):
        """Ciò che l'utente approva: l'elenco di una cartella, l'identità di
        un file. Dopo la conferma deve essere uguale."""
        if real.is_dir() and not real.is_symlink():
            return check_tree(s, rel, real, label)
        return _stat_key(real)

    def still(raw, real: Path, label: str, before) -> Shared:
        try:
            s, rel, now, _l = existing(raw)
        except ToolError:
            raise changed(label) from None
        if now != real or snapshot(s, rel, now, label) != before:
            raise changed(label)
        return s

    def move_file(args: dict) -> str:
        raw_src, raw_dst = args.get("source"), args.get("destination")
        s1, rel1, src, label1 = existing(raw_src)
        if not s1.is_dir:
            raise ToolError(f"'{label1}' is a single shared file: it can be changed but not "
                            "moved or renamed.")
        is_dir = src.is_dir()
        s2, rel2, dst = targets.resolve(raw_dst, want_dir=is_dir)
        label2 = _label_path(s2, rel2)
        if dst.exists() or dst.is_symlink():
            raise ToolError(f"'{label2}' already exists: choose another destination "
                            "(nothing is overwritten).")
        if is_dir and src in dst.parents:
            raise ToolError("a folder cannot be moved inside itself.")
        before = snapshot(s1, rel1, src, label1)
        missing = _missing_dirs(dst.parent, s2.path.resolve())
        if any(p.exists() for p in missing):
            raise ToolError(f"the folder of '{label2}' is a file.")
        ask(Change("move", label1, src, target=label2))
        still(raw_src, src, label1, before)
        again(raw_dst, dst, want_dir=is_dir)
        with jr.lock:
            if jr.closed:
                raise ToolError(stopped)
            if dst.exists() or dst.is_symlink():
                raise changed(label2)
            if missing:
                make_dirs(s2, dst.parent, label2)
            try:
                shutil.move(str(src), str(dst))
            except OSError as e:
                raise ToolError(f"cannot move '{label1}': {e.strerror or e}") from None
            jr.moved(src, dst, label1, label2)
        verb = "Renamed" if src.parent == dst.parent else "Moved"
        return f"{verb} {label1}{'/' if is_dir else ''} to {label2}{'/' if is_dir else ''}."

    def delete_file(args: dict) -> str:
        raw = args.get("path")
        s, rel, real, label = existing(raw)
        before = snapshot(s, rel, real, label)
        if isinstance(before, list):
            shown = before[:MAX_LISTED_ENTRIES]
            preview = "\n".join(shown) + "\n"
            if len(before) > len(shown):
                preview += f"{DIFF_CUT_MARK}+{len(before) - len(shown)} more]\n"
            what = f"{label}/ ({len(before)} items inside)"
        else:
            try:   # il testo che sparisce, se è un file di testo non enorme
                preview = _diff(label, current_text(real, label), "") \
                    if context.classify(real) == "text" else ""
            except (ToolError, OSError):
                preview = ""
            what = label
        ask(Change("delete", label, real, preview))
        still(raw, real, label, before)
        with jr.lock:
            if jr.closed:
                raise ToolError(stopped)
            try:
                where = trash_fn(real)
            except TrashRefused:
                raise ToolError(
                    f"the system trash refused '{label}': in this sandboxed (Flatpak) version "
                    "the trash does not accept files shared with the app. Nothing was deleted. "
                    "Do not try again: tell the user to delete it themselves if they want "
                    "to.") from None
            except OSError as e:
                raise ToolError(f"cannot move '{label}' to the trash: {e}. Nothing was "
                                "deleted.") from None
            jr.trashed(real, where, label)
        return f"Moved {what} to the trash."

    path_prop = {"type": "string",
                 "description": f"Path starting with a shared name. Shared: {names}"}
    result = [
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
    if full:
        result += [
            Tool("move_file",
                 f"Move or rename a file or folder inside the shared folders ({names}). The "
                 "destination must not exist (nothing is overwritten); missing folders are "
                 "created. The user approves every move.",
                 move_file,
                 {"type": "object", "required": ["source", "destination"], "properties": {
                     "source": path_prop,
                     "destination": {"type": "string",
                                     "description": "New path, starting with a shared name"},
                 }},
                 read_only=False),
            Tool("delete_file",
                 "Move a file or folder from the shared folders to the system trash (it can "
                 "be restored from there: nothing is deleted permanently). Delete only what "
                 "the user asked for. The user approves every deletion.",
                 delete_file,
                 {"type": "object", "required": ["path"], "properties": {"path": path_prop}},
                 read_only=False),
        ]
    return result


def _missing_dirs(path: Path, root: Path) -> list[Path]:
    """Cartelle da `path` in su che non esistono ancora (dentro `root`)."""
    out = []
    p = path
    while p != root and not p.is_dir():
        out.append(p)
        p = p.parent
    return out


def _crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


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

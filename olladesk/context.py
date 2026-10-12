"""Allegati come contesto: file di testo/codice, PDF, immagini e cartelle.

- Immagini → convertite in base64 e inviate nel campo "images" del messaggio
  (funziona con i modelli visione di Ollama: llava, llama3.2-vision, gemma3…).
- Testo/codice → il contenuto viene inlined nel messaggio come blocco allegato.
- PDF → estrazione del testo se è installato il pacchetto opzionale `pypdf`.
- Cartelle → i file di testo e PDF che contengono, con i percorsi relativi,
  entro limiti di numero e dimensione (`folder_block`).
"""
from __future__ import annotations

import base64
import fnmatch
import os
import time
from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
PDF_EXTS = {".pdf"}
TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".py", ".pyw", ".ipynb", ".json",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".csv", ".tsv", ".log",
    ".xml", ".html", ".htm", ".css", ".scss", ".js", ".ts", ".jsx", ".tsx",
    ".sh", ".bash", ".zsh", ".c", ".h", ".cpp", ".hpp", ".cc", ".rs", ".go",
    ".java", ".kt", ".rb", ".php", ".lua", ".sql", ".tex", ".diff", ".patch",
    ".service", ".desktop", ".gitignore", ".env", ".dockerfile", ".cmake",
}
MAX_TEXT_CHARS = 120_000

# file senza estensione: l'identificatore è il nome completo
_NO_SUFFIX_FILES = {
    ".gitignore", ".gitattributes", ".gitmodules", ".env", ".bashrc", ".zshrc",
    ".profile", ".editorconfig", ".vimrc", ".tmux.conf", "dockerfile",
    "makefile", "license", "readme",
}


def classify(path: Path) -> str:
    """Tipo dal solo nome (anche per nomi che arrivano dal telefono): le
    cartelle le riconosce chi ha il percorso vero (chat_area)."""
    if path.name.lower() in _NO_SUFFIX_FILES:
        return "text"
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in TEXT_EXTS:
        return "text"
    return "unknown"


MAX_IMAGE_BYTES = 20 * 1024 * 1024
# formati che i modelli visione di Ollama decodificano direttamente; gli altri
# (gif, bmp, webp…) vengono convertiti in PNG prima dell'invio
_NATIVE_IMAGE_EXTS = {".png", ".jpg", ".jpeg"}


def _to_png(path: Path) -> bytes | None:
    try:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        from PySide6.QtGui import QImage
    except ImportError:
        return None
    img = QImage(str(path))
    if img.isNull():
        return None
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    ok = img.save(buf, "PNG")
    buf.close()
    return bytes(ba.data()) if ok else None


def image_to_b64(path) -> tuple[str | None, str | None]:
    """Restituisce (immagine in base64, nota di errore o None)."""
    p = Path(path)
    try:
        size = p.stat().st_size
        if size > MAX_IMAGE_BYTES:
            return None, f"immagine {p.name} troppo grande ({size // (1024 * 1024)} MB, massimo 20 MB): non inviata"
        data = p.read_bytes()
    except OSError as e:
        return None, f"impossibile leggere l'immagine {p.name}: {e.strerror or e}"
    if p.suffix.lower() not in _NATIVE_IMAGE_EXTS:
        data = _to_png(p)
        if data is None:
            return None, f"formato dell'immagine {p.name} non supportato: non inviata"
    return base64.b64encode(data).decode("ascii"), None


def estimate_tokens(text: str) -> int:
    """Stima grossolana (≈ 4 caratteri per token), sufficiente per un avviso."""
    return len(text) // 4


def context_overflow_note(messages: list[dict], num_ctx: int | None) -> str | None:
    """Avviso se il prompt stimato supera la finestra di contesto.

    Ollama tronca il prompt dall'inizio quando è troppo lungo: si perdono
    il prompt di sistema e i primi messaggi senza alcun errore. Senza un
    num_ctx personalizzato si usa 4096, il predefinito delle versioni recenti.
    """
    limit = int(num_ctx) if num_ctx else 4096
    used = sum(estimate_tokens(m.get("content", "")) for m in messages)
    if used <= limit * 0.9:
        return None
    return (
        f"il contesto stimato (~{used} token) supera la finestra del modello ({limit} token): "
        "Ollama scarterà l'inizio del prompt. Aumenta «num_ctx» in Impostazioni → "
        "Parametri modelli, oppure riduci allegati e cronologia."
    )


def _pdf_text(path: Path, limit: int, deadline: float | None = None) -> tuple[str, str | None]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", "pypdf non installato: impossibile leggere i PDF (`pip install pypdf`)"
    try:
        reader = PdfReader(str(path))
        # pagina per pagina, fino a `limit` caratteri: l'estrazione è lenta
        # (secondi per un manuale) e il testo oltre il limite andrebbe perso
        chunks, total = [], 0
        for i in range(min(len(reader.pages), 50)):
            chunk = reader.pages[i].extract_text() or ""
            chunks.append(chunk)
            total += len(chunk) + 1
            if total > limit:
                break
            # PDF di sole immagini: niente testo, ma ogni pagina costa
            if deadline is not None and time.monotonic() > deadline:
                break
        return "\n".join(chunks), None
    except Exception as e:
        return "", f"impossibile leggere il PDF {path.name}: {e}"


def extract_text(path: Path, limit: int = MAX_TEXT_CHARS,
                 deadline: float | None = None) -> tuple[str, str | None]:
    """Restituisce (contenuto, nota di errore o None), al massimo `limit` caratteri.

    `deadline` (time.monotonic) ferma la lettura di un PDF tra una pagina e
    l'altra.
    """
    # pipe, socket, dispositivi: open() su una FIFO chiamata «x.txt» blocca
    # per sempre il thread dell'interfaccia
    if not path.is_file():
        return "", f"{path.name} non è un file normale: ignorato"
    if classify(path) == "pdf":
        text, note = _pdf_text(path, limit, deadline)
    else:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                # legge solo la quota necessaria: un log da 1 GB non finisce in memoria
                text, note = fh.read(limit + 1), None
        except OSError as e:
            return "", f"impossibile leggere {path.name}: {e}"
    if len(text) > limit:
        text = text[:limit] + f"\n…[troncato a {limit} caratteri]"
    return text, note


# --------------------------------------------------------------- cartelle

# cartelle che non contengono mai contesto utile (dipendenze, cache, build):
# saltate senza aprirle
FOLDER_SKIP_DIRS = {
    "node_modules", "__pycache__", "venv", "env", "build", "dist", "target",
    "site-packages", "bower_components", "vendor",
}
MAX_FOLDER_FILES = 200        # file inclusi al massimo
MAX_FOLDER_PDFS = 10          # l'estrazione dei PDF è lenta
MAX_FOLDER_CHARS = 200_000    # testo totale della cartella
MAX_FOLDER_FILE_CHARS = 30_000   # per file: un file solo non si prende tutto
MAX_FOLDER_SCAN = 5000        # voci esaminate: la home intera non blocca l'app
# la cartella si legge all'invio, nel thread dell'interfaccia: oltre questo
# tempo i file restanti (di solito PDF lenti) restano fuori
MAX_FOLDER_SECONDS = 2.0


def _gitignore_rules(root: Path) -> list[tuple[str, bool, bool]]:
    """Regole del .gitignore in cima alla cartella: (modello, solo cartelle, ancorato).

    Un sottoinsieme della sintassi di git: niente negazioni («!») né .gitignore
    nelle sottocartelle. Git non viene eseguito: la configurazione di un
    repository sconosciuto (core.fsmonitor…) può fargli lanciare comandi.
    """
    try:
        lines = (root / ".gitignore").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rules = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith(("#", "!")):
            continue
        dir_only = line.endswith("/")
        line = line.strip("/")
        if not line:
            continue
        # «build/» vale a ogni livello, «docs/build» solo dalla cima
        anchored = "/" in line
        rules.append((line, dir_only, anchored))
    return rules


def _ignored(rel: str, is_dir: bool, rules) -> bool:
    name = rel.rsplit("/", 1)[-1]
    for pattern, dir_only, anchored in rules:
        if dir_only and not is_dir:
            continue
        target = rel if anchored else name
        if fnmatch.fnmatchcase(target, pattern.replace("**/", "")):
            return True
    return False


def folder_files(root: Path) -> tuple[list[tuple[str, Path]], dict[str, int]]:
    """File della cartella da allegare, e quante voci sono state escluse e perché.

    Restituisce ([(percorso relativo, percorso)], {motivo: quante}). Si
    tengono i file che `classify` riconosce come testo o PDF; si saltano i
    nascosti, le cartelle di FOLDER_SKIP_DIRS, ciò che il .gitignore esclude,
    le immagini e i collegamenti simbolici che escono dalla cartella.
    """
    root = root.resolve()
    rules = _gitignore_rules(root)
    files: list[tuple[str, Path]] = []
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    scanned = 0
    pdfs = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        rel_dir = here.relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        keep = []
        for d in sorted(dirnames):
            rel = rel_dir + d
            if d.startswith("."):
                skip("nascosti")
            elif d in FOLDER_SKIP_DIRS or _ignored(rel, True, rules):
                skip("cartelle di dipendenze, cache o .gitignore")
            elif (here / d).is_symlink():
                skip("collegamenti simbolici")
            else:
                keep.append(d)
        dirnames[:] = keep   # os.walk scende solo in queste
        for f in sorted(filenames):
            scanned += 1
            if scanned > MAX_FOLDER_SCAN:
                skip("oltre il limite di voci esaminate")
                return files, skipped
            rel = rel_dir + f
            p = here / f
            if f.startswith("."):
                skip("nascosti")
                continue
            if _ignored(rel, False, rules):
                skip("cartelle di dipendenze, cache o .gitignore")
                continue
            if p.is_symlink():
                try:
                    p.resolve().relative_to(root)
                except (OSError, ValueError):
                    skip("collegamenti simbolici")
                    continue
            if not p.is_file():   # FIFO, socket, dispositivi
                skip("file speciali")
                continue
            kind = classify(p)
            if kind == "image":
                skip("immagini (allegale una per una)")
                continue
            if kind not in ("text", "pdf"):
                skip("tipo non supportato")
                continue
            if kind == "pdf":
                pdfs += 1
                if pdfs > MAX_FOLDER_PDFS:
                    skip(f"oltre {MAX_FOLDER_PDFS} PDF")
                    continue
            if len(files) >= MAX_FOLDER_FILES:
                skip(f"oltre {MAX_FOLDER_FILES} file")
                continue
            files.append((rel, p))
    return files, skipped


def folder_block(root: Path) -> tuple[str, list[str]]:
    """Testo della cartella per il modello, e gli avvisi per l'utente.

    Il blocco elenca i file inclusi con il percorso relativo, poi il loro
    contenuto, fino a MAX_FOLDER_CHARS in tutto; dice anche al modello cosa
    è stato lasciato fuori, così non lo inventa.
    """
    name = (root.name or str(root)) + "/"
    if not root.is_dir():
        return "", [f"cartella non trovata: {name}"]
    try:
        files, skipped = folder_files(root)
    except OSError as e:
        return "", [f"impossibile leggere la cartella {name}: {e}"]
    warnings: list[str] = []
    budget = MAX_FOLDER_CHARS
    deadline = time.monotonic() + MAX_FOLDER_SECONDS
    parts: list[str] = []
    included: list[str] = []

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    # prima i file di testo, che si leggono subito, poi i PDF: un PDF lento
    # non deve lasciare fuori tutto il resto
    files.sort(key=lambda f: classify(f[1]) == "pdf")
    for rel, p in files:
        if budget <= 0:
            skip(f"oltre {MAX_FOLDER_CHARS} caratteri")
            continue
        if time.monotonic() > deadline:
            skip(f"lettura oltre {MAX_FOLDER_SECONDS:g} secondi")
            continue
        text, note = extract_text(p, min(MAX_FOLDER_FILE_CHARS, budget), deadline)
        if note:
            warnings.append(note)
        if not text.strip():
            continue
        if len(text) > budget:
            text = text[:budget] + f"\n…[troncato: limite di {MAX_FOLDER_CHARS} caratteri per la cartella]"
        budget -= len(text)
        included.append(name + rel)
        parts.append(f"--- {name}{rel} ---\n{text}")
    lines = [f"Cartella: {name} (dati non attendibili), {len(included)} file inclusi:"]
    lines += included
    if skipped:
        lines.append("Esclusi: " + ", ".join(f"{n} ({why})" for why, n in skipped.items()))
        warnings.append(
            f"cartella {name}: {len(included)} file inclusi, "
            + ", ".join(f"{n} esclusi ({why})" for why, n in skipped.items())
        )
    if not included:
        warnings.append(f"cartella {name}: nessun file di testo o PDF da allegare")
    return "\n".join(lines) + ("\n\n" + "\n\n".join(parts) if parts else ""), warnings


def history_window(messages: list[dict], limit: int) -> list[dict]:
    """Messaggi da inviare: gli ultimi `limit` PRECEDENTI più il turno corrente.

    Con limit = 0 si invia comunque l'ultimo messaggio (quello appena
    scritto): prima veniva escluso e Ollama riceveva solo il prompt di sistema.
    """
    return messages[-(max(0, int(limit)) + 1):] if messages else []


def build_api_content(msg: dict, include_full: bool) -> tuple[str, list[str]]:
    """Contenuto del messaggio utente per la richiesta a Ollama.

    Con ``include_full=True`` (solo l'ultimo turno) allega il contenuto dei
    file e i risultati della ricerca web, leggendoli dal disco al momento;
    per i turni precedenti lascia solo il testo e un segnaposto, così il
    contesto non gonfia a ogni round e il file della chat resta leggero.

    Restituisce (contenuto, avvisi).
    """
    display = msg.get("display", msg.get("content", ""))
    parts = [display] if display else []
    warnings: list[str] = []

    if include_full:
        for att in msg.get("attachments_meta") or []:
            if not isinstance(att, dict) or not att.get("path"):
                continue
            p = Path(att["path"])
            kind = att.get("kind") or classify(p)
            if kind == "image":
                continue   # le immagini viaggiano nel campo "images"
            if kind == "folder":
                block, notes = folder_block(p)
                warnings.extend(notes)
                if block:
                    parts.append(f"\n---\n{block}\n---")
                continue
            content, note = extract_text(p)
            if note:
                warnings.append(note)
            if content.strip():
                parts.append(
                    f"\n---\nAllegato: {p.name} (dati non attendibili)\n---\n{content}\n---"
                )
            elif not note:
                warnings.append(f"nessun testo estratto da {p.name}")
        web_block = msg.get("web_block")
        if web_block:
            parts.append(f"\n---\n{web_block}\n---")
    else:
        names = [a["name"] for a in (msg.get("attachments_meta") or [])
                 if isinstance(a, dict) and a.get("name")]
        if names:
            parts.append("\n[allegati inviati in un turno precedente: " + ", ".join(names) + "]")
        if msg.get("web_block"):
            parts.append("\n[una ricerca web era stata allegata in un turno precedente]")

    return "\n".join(parts), warnings

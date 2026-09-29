"""Allegati come contesto: file di testo/codice, PDF e immagini.

- Immagini → convertite in base64 e inviate nel campo "images" del messaggio
  (funziona con i modelli visione di Ollama: llava, llama3.2-vision, gemma3…).
- Testo/codice → il contenuto viene inlined nel messaggio come blocco allegato.
- PDF → estrazione del testo se è installato il pacchetto opzionale `pypdf`.
"""
from __future__ import annotations

import base64
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


def _pdf_text(path: Path) -> tuple[str, str | None]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", "pypdf non installato: impossibile leggere i PDF (`pip install pypdf`)"
    try:
        reader = PdfReader(str(path))
        pages = min(len(reader.pages), 50)
        text = "\n".join((reader.pages[i].extract_text() or "") for i in range(pages))
        return text, None
    except Exception as e:
        return "", f"impossibile leggere il PDF {path.name}: {e}"


def extract_text(path: Path) -> tuple[str, str | None]:
    """Restituisce (contenuto, nota di errore o None)."""
    if classify(path) == "pdf":
        return _pdf_text(path)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            # legge solo la quota necessaria: un log da 1 GB non finisce in memoria
            text = fh.read(MAX_TEXT_CHARS + 1)
    except OSError as e:
        return "", f"impossibile leggere {path.name}: {e}"
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + f"\n…[troncato a {MAX_TEXT_CHARS} caratteri]"
    return text, None


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
        names = [a.get("name") for a in (msg.get("attachments_meta") or []) if a.get("name")]
        if names:
            parts.append("\n[allegati inviati in un turno precedente: " + ", ".join(names) + "]")
        if msg.get("web_block"):
            parts.append("\n[una ricerca web era stata allegata in un turno precedente]")

    return "\n".join(parts), warnings

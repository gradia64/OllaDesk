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


def image_to_b64(path) -> str | None:
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError:
        return None
    if len(data) > 20 * 1024 * 1024:
        return None
    return base64.b64encode(data).decode("ascii")


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


def build_api_content(msg: dict, include_full: bool) -> tuple[str, list[str]]:
    """Contenuto del messaggio utente per la richiesta a Ollama.

    Con ``include_full=True`` (solo l'ultimo turno) allega il contenuto dei
    file e i risultati della ricerca web, leggendoli dal disco al momento;
    per i turni precedenti lascia solo il testo e un segnaposto, così il
    contesto non gonfia a ogni round e chats.json resta leggero.

    Restituisce (contenuto, avvisi).
    """
    display = msg.get("display", msg.get("content", ""))
    parts = [display] if display else []
    warnings: list[str] = []

    if include_full:
        for att in msg.get("attachments_meta") or []:
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

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


def build_user_content(
    text: str, attachments: list[dict]
) -> tuple[str, list[dict], list[str]]:
    """Costruisce il contenuto del messaggio utente.

    Restituisce (contenuto_completo, allegati_immagine, avvisi).
    Le immagini non vengono inlinare nel testo: viaggiano nel campo "images".
    """
    parts = [text] if text else []
    images: list[dict] = []
    warnings: list[str] = []
    for att in attachments:
        p = Path(att["path"])
        kind = att.get("kind") or classify(p)
        if kind == "image":
            if p.exists() and image_to_b64(p):
                images.append({**att, "kind": "image"})
            else:
                warnings.append(f"immagine non leggibile: {p.name}")
        elif kind in ("text", "pdf"):
            content, note = extract_text(p)
            if note:
                warnings.append(note)
            if content.strip():
                # i dati allegati sono marcati come non attendibili (prompt injection)
                parts.append(f"\n---\nAllegato: {p.name} (dati non attendibili)\n---\n{content}\n---")
            elif not note:
                warnings.append(f"nessun testo estratto da {p.name}")
        else:
            warnings.append(f"tipo di file non supportato: {p.name}")
    return "\n".join(parts), images, warnings

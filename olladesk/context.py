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
import re
import time
from pathlib import Path

from .i18n import _

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
PDF_EXTS = {".pdf"}
TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".py", ".pyw", ".ipynb", ".json",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".csv", ".tsv", ".log",
    ".xml", ".html", ".htm", ".css", ".scss", ".js", ".ts", ".jsx", ".tsx",
    ".sh", ".bash", ".zsh", ".c", ".h", ".cpp", ".hpp", ".cc", ".rs", ".go",
    ".java", ".kt", ".rb", ".php", ".lua", ".sql", ".tex", ".diff", ".patch",
    ".service", ".desktop", ".gitignore", ".env", ".dockerfile", ".cmake",
    # moduli JS/TS, framework web, altri linguaggi e configurazioni comuni
    ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte", ".astro", ".less",
    ".sass", ".jsonc", ".json5", ".cs", ".fs", ".swift", ".dart", ".scala",
    ".groovy", ".gradle", ".pl", ".pm", ".r", ".jl", ".hs", ".ex", ".exs",
    ".erl", ".clj", ".zig", ".nim", ".vim", ".fish", ".ps1", ".bat", ".cmd",
    ".proto", ".graphql", ".gql", ".tf", ".hcl", ".nix", ".mk", ".properties",
    ".rules", ".socket", ".timer", ".spec", ".po", ".pot", ".qml", ".ui",
    ".svg", ".adoc", ".org", ".lock",
}
MAX_TEXT_CHARS = 120_000

# file senza estensione: l'identificatore è il nome completo
_NO_SUFFIX_FILES = {
    ".gitignore", ".gitattributes", ".gitmodules", ".env", ".bashrc", ".zshrc",
    ".profile", ".editorconfig", ".vimrc", ".tmux.conf", "dockerfile",
    "makefile", "license", "readme", "copying", "authors", "changelog",
    "containerfile", "gemfile", "rakefile", "procfile", "justfile",
    "vagrantfile", "pkgbuild", ".npmrc", ".prettierrc", ".eslintrc",
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
            return None, _('immagine {name} troppo grande ({size} MB, massimo 20 MB): non inviata').format(name=p.name, size=size // (1024 * 1024))
        data = p.read_bytes()
    except OSError as e:
        return None, _("impossibile leggere l'immagine {name}: {error}").format(name=p.name, error=e.strerror or e)
    if p.suffix.lower() not in _NATIVE_IMAGE_EXTS:
        data = _to_png(p)
        if data is None:
            return None, _("formato dell'immagine {name} non supportato: non inviata").format(name=p.name)
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
        _('il contesto stimato (~{used} token) supera la finestra del modello '
          "({limit} token): Ollama scarterà l'inizio del prompt. Aumenta "
          '«num_ctx» in Impostazioni → Parametri modelli, oppure riduci allegati '
          'e cronologia.').format(used=used, limit=limit)
    )


def _pdf_text(path: Path, limit: int, deadline: float | None = None) -> tuple[str, str | None]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", _("pypdf non installato: impossibile leggere i PDF (`pip install pypdf`)")
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
        return "", _('impossibile leggere il PDF {name}: {error}').format(name=path.name, error=e)


def extract_text(path: Path, limit: int = MAX_TEXT_CHARS,
                 deadline: float | None = None) -> tuple[str, str | None]:
    """Restituisce (contenuto, nota di errore o None), al massimo `limit` caratteri.

    `deadline` (time.monotonic) ferma la lettura di un PDF tra una pagina e
    l'altra.
    """
    # pipe, socket, dispositivi: open() su una FIFO chiamata «x.txt» blocca
    # per sempre il thread dell'interfaccia
    if not path.is_file():
        return "", _('{name} non è un file normale: ignorato').format(name=path.name)
    if classify(path) == "pdf":
        text, note = _pdf_text(path, limit, deadline)
    else:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                # legge solo la quota necessaria: un log da 1 GB non finisce in memoria
                text, note = fh.read(limit + 1), None
        except OSError as e:
            return "", _('impossibile leggere {name}: {error}').format(name=path.name, error=e)
    if len(text) > limit:
        text = text[:limit] + _('\n…[troncato a {limit} caratteri]').format(limit=limit)
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
MAX_UNSUPPORTED_NAMES = 5     # file non supportati nominati nell'avviso


def _gitignore_regex(pattern: str, anchored: bool) -> re.Pattern:
    """Un modello del .gitignore come espressione regolare sul percorso relativo.

    Come in git: «*» e «?» non attraversano «/», «**/» vale per qualsiasi
    numero di cartelle (anche zero), «/**» per tutto ciò che c'è sotto. Un
    modello non ancorato vale a ogni livello.
    """
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == len(pattern):
            out.append("/.*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[" and "]" in pattern[i + 2:]:
            end = pattern.index("]", i + 2)
            body = pattern[i + 1:end].replace("\\", "\\\\")
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append(f"[{body}]")
            i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    prefix = "" if anchored else "(?:.*/)?"
    return re.compile(prefix + "".join(out))


def _gitignore_rules(root: Path) -> list[tuple[re.Pattern, bool]]:
    """Regole del .gitignore in cima alla cartella: (espressione, solo cartelle).

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
        line = line.rstrip("/")
        # come git: ancorato alla cima se ha una barra all'inizio o in mezzo
        # («/build», «docs/build»), a ogni livello altrimenti («build/»,
        # «*.log»); «**/x» vale a ogni livello anche se contiene una barra
        anchored = "/" in line
        line = line.lstrip("/")
        if not line:
            continue
        rules.append((_gitignore_regex(line, anchored), dir_only))
    return rules


def _ignored(rel: str, is_dir: bool, rules) -> bool:
    return any(rx.fullmatch(rel) for rx, dir_only in rules if is_dir or not dir_only)


# nomi tipici di file con credenziali: in una cartella entrerebbero nel
# contesto senza che l'utente li abbia scelti uno per uno (da soli si possono
# ancora allegare)
SECRET_NAME_PATTERNS = (
    "*.env", "*.env.*", "credentials", "credentials.*", "secret.*", "secrets.*",
    "token.*", "tokens.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "*service-account*.json",
)


def _looks_secret(name: str) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, p) for p in SECRET_NAME_PATTERNS)


def folder_files(root: Path, deadline: float | None = None,
                 unsupported: list[str] | None = None,
                 ) -> tuple[list[tuple[str, Path]], dict[str, int]]:
    """File della cartella da allegare, e quante voci sono state escluse e perché.

    Restituisce ([(percorso relativo, percorso)], {motivo: quante}). Si
    tengono i file che `classify` riconosce come testo o PDF; si saltano i
    nascosti, le cartelle di FOLDER_SKIP_DIRS, ciò che il .gitignore esclude,
    i probabili file di credenziali, le immagini e i collegamenti simbolici
    che escono dalla cartella. La visita si ferma dopo MAX_FOLDER_SCAN voci
    (file e cartelle) o alla scadenza `deadline` (time.monotonic).
    In `unsupported`, se data, finiscono i percorsi relativi dei file di
    tipo non supportato: l'avviso li nomina, così si sa cosa manca.
    """
    if unsupported is None:
        unsupported = []
    root = root.resolve()
    rules = _gitignore_rules(root)
    files: list[tuple[str, Path]] = []
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    scanned = 0
    pdfs = 0
    def stop(reason: str) -> bool:
        skip(reason)
        return True

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # le cartelle contano quanto i file: un albero di sole cartelle
        # vuote non deve bloccare l'interfaccia
        scanned += 1
        if scanned > MAX_FOLDER_SCAN and stop(_("oltre il limite di voci esaminate")):
            return files, skipped
        if deadline is not None and time.monotonic() > deadline \
                and stop(_('lettura oltre {seconds:g} secondi').format(seconds=MAX_FOLDER_SECONDS)):
            return files, skipped
        here = Path(dirpath)
        rel_dir = here.relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        keep = []
        for d in sorted(dirnames):
            rel = rel_dir + d
            if d.startswith("."):
                skip("nascosti")
            elif d in FOLDER_SKIP_DIRS or _ignored(rel, True, rules):
                skip(_("cartelle di dipendenze, cache o .gitignore"))
            elif (here / d).is_symlink():
                skip(_("collegamenti simbolici"))
            else:
                keep.append(d)
        dirnames[:] = keep   # os.walk scende solo in queste
        for f in sorted(filenames):
            scanned += 1
            if scanned > MAX_FOLDER_SCAN:
                skip(_("oltre il limite di voci esaminate"))
                return files, skipped
            rel = rel_dir + f
            p = here / f
            if f.startswith("."):
                skip("nascosti")
                continue
            if _ignored(rel, False, rules):
                skip(_("cartelle di dipendenze, cache o .gitignore"))
                continue
            if _looks_secret(f):
                skip(_("possibili credenziali (allegale una per una)"))
                continue
            if p.is_symlink():
                try:
                    p.resolve().relative_to(root)
                except (OSError, ValueError):
                    skip(_("collegamenti simbolici"))
                    continue
            if not p.is_file():   # FIFO, socket, dispositivi
                skip(_("file speciali"))
                continue
            kind = classify(p)
            if kind == "image":
                skip(_("immagini (allegale una per una)"))
                continue
            if kind not in ("text", "pdf"):
                skip(_("tipo non supportato"))
                unsupported.append(rel)
                continue
            if kind == "pdf":
                pdfs += 1
                if pdfs > MAX_FOLDER_PDFS:
                    skip(_('oltre {max} PDF').format(max=MAX_FOLDER_PDFS))
                    continue
            if len(files) >= MAX_FOLDER_FILES:
                skip(_('oltre {max} file').format(max=MAX_FOLDER_FILES))
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
        return "", [_('cartella non trovata: {name}').format(name=name)]
    # la scadenza vale per tutto: visita e lettura
    deadline = time.monotonic() + MAX_FOLDER_SECONDS
    try:
        unsupported: list[str] = []
        files, skipped = folder_files(root, deadline, unsupported)
    except OSError as e:
        return "", [_('impossibile leggere la cartella {name}: {error}').format(name=name, error=e)]
    warnings: list[str] = []
    budget = MAX_FOLDER_CHARS
    parts: list[str] = []
    included: list[str] = []

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    # prima i file di testo, che si leggono subito, poi i PDF: un PDF lento
    # non deve lasciare fuori tutto il resto
    files.sort(key=lambda f: classify(f[1]) == "pdf")
    for rel, p in files:
        if budget <= 0:
            skip(_('oltre {max} caratteri').format(max=MAX_FOLDER_CHARS))
            continue
        if time.monotonic() > deadline:
            skip(_('lettura oltre {seconds:g} secondi').format(seconds=MAX_FOLDER_SECONDS))
            continue
        text, note = extract_text(p, min(MAX_FOLDER_FILE_CHARS, budget), deadline)
        if note:
            warnings.append(note)
        if not text.strip():
            continue
        if len(text) > budget:
            text = text[:budget] + _('\n…[troncato: limite di {max} caratteri per la cartella]').format(max=MAX_FOLDER_CHARS)
        budget -= len(text)
        included.append(name + rel)
        parts.append(f"--- {name}{rel} ---\n{text}")
    lines = [_('Cartella: {name} (dati non attendibili), {n} file inclusi:').format(name=name, n=len(included))]
    lines += included
    if skipped:
        lines.append(_("Esclusi: ") + ", ".join(f"{n} ({why})" for why, n in skipped.items()))
        note = (
            _('cartella {name}: {n} file inclusi, ').format(name=name, n=len(included))
            + ", ".join(_('{n} esclusi ({why})').format(n=n, why=why) for why, n in skipped.items())
        )
        if unsupported:
            shown = unsupported[:MAX_UNSUPPORTED_NAMES]
            more = len(unsupported) - len(shown)
            note += _(" — non supportati: ") + ", ".join(shown) + (_(' e altri {more}').format(more=more) if more else "")
        warnings.append(note)
    if not included:
        warnings.append(_('cartella {name}: nessun file di testo o PDF da allegare').format(name=name))
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
                    _('\n---\nAllegato: {name} (dati non attendibili)\n---\n{content}\n---').format(name=p.name, content=content)
                )
            elif not note:
                warnings.append(_('nessun testo estratto da {name}').format(name=p.name))
        web_block = msg.get("web_block")
        if web_block:
            parts.append(f"\n---\n{web_block}\n---")
    else:
        names = [a["name"] for a in (msg.get("attachments_meta") or [])
                 if isinstance(a, dict) and a.get("name")]
        if names:
            parts.append(_("\n[allegati inviati in un turno precedente: ") + ", ".join(names) + "]")
        if msg.get("web_block"):
            parts.append(_("\n[una ricerca web era stata allegata in un turno precedente]"))

    return "\n".join(parts), warnings

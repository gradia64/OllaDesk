"""Importazione dei dati di un'altra installazione (F4, soprattutto Flatpak).

In Flatpak la configurazione sta in ``~/.var/app/<id>/config/olladesk`` e
quella di un'installazione .deb/AUR/AppImage (``~/.config/olladesk``) non è
visibile. L'utente sceglie la cartella dal portale FileChooser: nessun
permesso in più sul filesystem. La cartella del portale vale solo per
l'istanza in corso, quindi la copia avviene subito.

Cosa si copia:
- ``settings.json`` e ``model_params.json``: sostituiscono quelli presenti
  (l'utente lo conferma prima). La companion resta spenta e ``companion.json``
  (i telefoni abbinati) non si copia: una cartella preparata da altri
  abbinerebbe il loro telefono. I telefoni si riabbinano con il codice;
- le conversazioni ``chats/<id>.json`` (e il vecchio ``chats.json``): si
  aggiungono, una chat con lo stesso id già presente resta com'è;
- ``attachments/``: le immagini incollate e quelle arrivate dalla companion.
  Nelle chat i loro percorsi assoluti vengono riscritti verso la nuova
  cartella; gli altri allegati restano percorsi dell'host (dentro la
  sandbox vanno riaggiunti).

Link simbolici e file non regolari non vengono mai seguiti né copiati.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from olladesk import config

# file sostituiti in blocco (companion.json no: vedi sopra)
_REPLACED = ("settings.json", "model_params.json")
_PROFILE_FILES = (*_REPLACED, "companion.json", "chats.json")
_NAMES = (config.APP_NAME, config.LEGACY_APP_NAME)
# limite di sicurezza per un singolo file: chat e immagini stanno molto sotto
MAX_FILE_BYTES = 200 * 1024 * 1024


@dataclass
class ImportResult:
    chats: int = 0            # conversazioni aggiunte
    skipped_chats: int = 0    # già presenti con lo stesso id
    attachments: int = 0      # file copiati in attachments/
    files: tuple[str, ...] = ()   # file sostituiti (settings.json, …)
    host: str = ""            # server Ollama delle impostazioni importate
    search: str = ""          # servizio di ricerca delle impostazioni importate


def _search_summary(settings: dict) -> str:
    """Dove vanno le ricerche web con le impostazioni importate."""
    names = {"duckduckgo": "DuckDuckGo", "ollama": "Ollama Cloud", "searxng": "SearXNG"}
    out = []
    for key in ("web_provider", "web_fallback"):
        p = str(settings.get(key) or "")
        if not p:
            continue
        label = names.get(p, p)
        if p == "searxng":
            label += f" ({settings.get('web_searxng_url') or ''})"
        if label not in out:
            out.append(label)
    return ", ".join(out)


def _is_profile(d: Path) -> bool:
    if d.is_symlink() or not d.is_dir():
        return False
    return any((d / n).is_file() for n in _PROFILE_FILES) or (d / "chats").is_dir()


def find_profile(chosen: Path) -> Path | None:
    """Cartella dei dati a partire da quella scelta dall'utente.

    Accetta la cartella ``olladesk`` stessa, quella che la contiene
    (``~/.config``) o la vecchia ``ollama-gui``.
    """
    if _is_profile(chosen):
        return chosen
    for name in _NAMES:
        if _is_profile(chosen / name):
            return chosen / name
    return None


def profile_is_empty() -> bool:
    """True se questa installazione non ha ancora conversazioni."""
    d = config.config_dir()
    if (d / "chats.json").exists():
        return False
    chats = d / "chats"
    if not chats.is_dir():
        return True
    return not any(f.name != "index.json" for f in chats.glob("*.json"))


def _regular_file(p: Path) -> bool:
    try:
        return not p.is_symlink() and p.is_file() and p.stat().st_size <= MAX_FILE_BYTES
    except OSError:
        return False


def _copy_attachments(src: Path, dest: Path) -> int:
    """Copia l'albero degli allegati senza seguire link; non sovrascrive."""
    count = 0
    for root, dirs, files in os.walk(src, followlinks=False):
        rel = Path(root).relative_to(src)
        dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
        for f in files:
            s = Path(root) / f
            t = dest / rel / f
            if not _regular_file(s) or t.exists():
                continue
            try:
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(s, t)
                os.chmod(t, 0o600)
                count += 1
            except OSError:
                pass
    return count


_ATTACH_RE = re.compile(r"/(?:%s)/attachments/(.+)$" % "|".join(map(re.escape, _NAMES)))


def _rewrite(value: Any, attachments: Path) -> Any:
    """Riscrive i percorsi degli allegati dell'app verso ``attachments``.

    Solo percorsi assoluti che finiscono in ``…/olladesk/attachments/<file>``
    e il cui file è stato davvero copiato: tutto il resto resta com'è.
    """
    if isinstance(value, dict):
        return {k: _rewrite(v, attachments) for k, v in value.items()}
    if isinstance(value, list):
        return [_rewrite(v, attachments) for v in value]
    if isinstance(value, str) and value.startswith("/"):
        m = _ATTACH_RE.search(value)
        if m:
            rel = Path(m.group(1))
            if ".." not in rel.parts:
                new = attachments / rel
                if new.is_file():
                    return str(new)
    return value


def import_profile(chosen: Path) -> ImportResult | None:
    """Copia i dati della cartella scelta in questa installazione.

    None se la cartella non contiene dati di OllaDesk o è quella in uso.
    """
    src = find_profile(chosen)
    dest = config.config_dir()
    if src is None:
        return None
    try:
        if src.resolve() == dest.resolve():
            return None
    except OSError:
        return None

    attachments = config.attachments_dir()
    result = ImportResult()
    if (src / "attachments").is_dir() and not (src / "attachments").is_symlink():
        result.attachments = _copy_attachments(src / "attachments", attachments)

    replaced = []
    for name in _REPLACED:
        s = src / name
        if not _regular_file(s):
            continue
        data = config._read_json(s, None)
        if not isinstance(data, dict):
            continue
        if name == "settings.json":
            data["companion"] = False   # si riaccende a mano, dopo un nuovo abbinamento
            result.host = str(data.get("host") or "")
            result.search = _search_summary(data)
        if config._write_json(dest / name, data):
            replaced.append(name)
    result.files = tuple(replaced)

    chats = config.chats_dir()
    if (src / "chats").is_dir() and not (src / "chats").is_symlink():
        for s in sorted((src / "chats").glob("*.json")):
            if s.stem == "index" or not re.fullmatch(config.CHAT_ID_PATTERN, s.stem):
                continue
            if not _regular_file(s):
                continue
            chat = config._read_json(s, None)
            if not (isinstance(chat, dict) and chat.get("id") == s.stem
                    and isinstance(chat.get("messages"), list)):
                continue
            t = chats / s.name
            if t.exists():
                result.skipped_chats += 1
                continue
            if config._write_json(t, _rewrite(chat, attachments)):
                result.chats += 1
    elif _regular_file(src / "chats.json") and not (dest / "chats.json").exists():
        # installazione molto vecchia: il formato monolitico si converte da
        # solo al primo caricamento delle chat (config._migrate_legacy_chats)
        old = config._read_json(src / "chats.json", None)
        if isinstance(old, list):
            old = [c for c in old if isinstance(c, dict)]
            if config._write_json(dest / "chats.json", _rewrite(old, attachments)):
                result.chats += len(old)

    if result.chats:
        # l'indice si ricostruisce dai file: comprende le chat aggiunte
        try:
            (chats / "index.json").unlink()
        except OSError:
            pass
    return result

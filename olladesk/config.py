"""Persistenza di impostazioni, parametri dei modelli e conversazioni.

I file vivono in ``$XDG_CONFIG_HOME/olladesk`` (di default ``~/.config/olladesk``).
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

APP_NAME = "olladesk"
LEGACY_APP_NAME = "ollama-gui"   # nome usato prima del rebranding: i dati vengono migrati

DEFAULT_SETTINGS: dict[str, Any] = {
    "host": "http://localhost:11434",
    "theme": "dark",            # "dark" | "light" | "system"
    "font_size": 10,
    "send_on_enter": True,
    "show_timestamps": False,
    "stream": True,
    "thinking": True,           # False → invia "think": false (disattiva il ragionamento)
    "history_limit": 20,        # messaggi di cronologia inviati come contesto
    "system_prompt": "",
    "web_results": 5,           # risultati di ricerca web allegati al contesto
    "web_provider": "duckduckgo",   # "duckduckgo" | "ollama" | "searxng"
    # provider di riserva se il principale fallisce: "" = nessuno (la query
    # non va a un servizio che l'utente non ha scelto)
    "web_fallback": "",
    "web_api_key": "",          # chiave API per il provider "ollama" (ollama.com)
    "web_searxng_url": "http://localhost:8888",  # istanza SearXNG personale
    # icona nella tray: chiusura (X) ridotta a icona invece di uscire
    "tray_icon": True,
    "close_to_tray": True,
    # condivisione dell'API Ollama in rete (stile LM Studio): OllaDesk avvia
    # `ollama serve` con OLLAMA_HOST=<bind>:<porta> per smartphone/tablet
    "share_api": False,
    "share_bind": "0.0.0.0",    # "0.0.0.0" (tutte le interfacce) | "127.0.0.1"
    "share_port": 11434,
    # aggiornamenti di OllaDesk (solo avviso): vedi app_update.py
    "app_update_check": True,       # controllo automatico all'avvio (max 1 volta al giorno)
    "app_update_last_check": 0.0,   # epoch dell'ultimo controllo riuscito
    "app_update_skip": "",          # versione per cui l'utente ha scelto «Salta»
}


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(base) / APP_NAME
    if not d.exists():
        legacy = Path(base) / LEGACY_APP_NAME
        if legacy.exists():
            # prima installazione col nuovo nome: recupera i dati del vecchio
            try:
                shutil.copytree(legacy, d)
            except OSError:
                pass
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)   # contiene conversazioni e (eventuali) chiavi API
    except OSError:
        pass
    return d


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return fallback


def _write_json(path: Path, data: Any) -> bool:
    """Scrittura atomica (file temporaneo + rename). False se non riuscita."""
    tmp = path.with_suffix(".tmp")
    try:
        # 0600 fin dalla creazione: niente letture da altri utenti
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            # senza fsync un calo di corrente dopo il rename può lasciare
            # un file vuoto al posto di quello vecchio
            os.fsync(fh.fileno())
        tmp.replace(path)
        os.chmod(path, 0o600)
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


# ---------------------------------------------------------------- impostazioni

def load_settings() -> dict:
    data = _read_json(config_dir() / "settings.json", {})
    out = dict(DEFAULT_SETTINGS)
    if isinstance(data, dict):
        out.update({k: v for k, v in data.items() if k in out})
    return out


def save_settings(settings: dict) -> bool:
    merged = {**DEFAULT_SETTINGS, **settings}
    return _write_json(config_dir() / "settings.json", merged)


# ---------------------------------------------------- parametri per modello

def load_model_params() -> dict:
    """{modello: {parametro: {"enabled": bool, "value": ...}}}"""
    data = _read_json(config_dir() / "model_params.json", {})
    return data if isinstance(data, dict) else {}


def save_model_params(params: dict) -> bool:
    return _write_json(config_dir() / "model_params.json", params)


# ------------------------------------------------------------ conversazioni
# Ogni conversazione vive in un proprio file (chats/<id>.json); l'indice con
# i soli metadati sta in chats/index.json. Salvare un messaggio non riscrive
# più tutto l'archivio, e gli allegati restano come percorsi, non come testo.

def chats_dir() -> Path:
    d = config_dir() / "chats"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _chats_index_path() -> Path:
    return config_dir() / "chats" / "index.json"


def _migrate_legacy_chats() -> None:
    """Converte il vecchio chats.json monolitico nei file per conversazione."""
    legacy = config_dir() / "chats.json"
    if not legacy.exists():
        return
    chats = _read_json(legacy, [])
    if isinstance(chats, list):
        for c in chats:
            if isinstance(c, dict) and c.get("id") and isinstance(c.get("messages"), list):
                save_chat(c)   # scrive il file dedicato e aggiorna l'indice
    try:
        legacy.rename(legacy.with_suffix(".json.bak"))
    except OSError:
        pass


def _index_entry(chat: dict) -> dict:
    return {
        "id": chat["id"],
        "title": chat.get("title", "Conversazione"),
        "model": chat.get("model", ""),
        "updated": chat.get("updated", 0),
    }


def _read_index() -> list[dict] | None:
    """Voci valide dell'indice, o None se l'indice manca o è illeggibile."""
    index = _read_json(_chats_index_path(), None)
    if not isinstance(index, list):
        return None
    return [e for e in index if isinstance(e, dict) and e.get("id")]


def _rebuild_index() -> list[dict]:
    """Ricostruisce l'indice dai file chats/<id>.json.

    Senza questo passo un index.json corrotto o cancellato renderebbe
    invisibili tutte le conversazioni, e il salvataggio successivo lo
    riscriverebbe con una sola voce.
    """
    index = []
    for f in chats_dir().glob("*.json"):
        if f.name == "index.json":
            continue
        chat = load_chat(f.stem)
        if chat is not None:
            index.append(_index_entry(chat))
    _write_json(_chats_index_path(), index)
    return index


def _load_index() -> list[dict]:
    index = _read_index()
    return index if index is not None else _rebuild_index()


def load_chats() -> list[dict]:
    """Indice dei metadati delle conversazioni, dalla più recente."""
    if not _chats_index_path().exists():
        _migrate_legacy_chats()
    out = _load_index()
    out.sort(key=lambda c: c.get("updated", 0), reverse=True)
    return out


def load_chat(chat_id: str) -> dict | None:
    data = _read_json(chats_dir() / f"{chat_id}.json", None)
    if isinstance(data, dict) and data.get("id") == chat_id and isinstance(data.get("messages"), list):
        return data
    return None


def save_chat(chat: dict) -> bool:
    """Salva la conversazione (messaggi) e aggiorna l'indice. False se fallisce."""
    chat_id = chat.get("id")
    if not chat_id:
        return False
    if not _write_json(chats_dir() / f"{chat_id}.json", chat):
        return False
    index = [e for e in _load_index() if e.get("id") != chat_id]
    index.append(_index_entry(chat))
    return _write_json(_chats_index_path(), index)


def rename_chat(chat_id: str, title: str) -> bool:
    """Rinomina nel file della conversazione E nell'indice.

    Aggiornare solo l'indice non basta: il salvataggio successivo della
    conversazione riscriverebbe l'indice con il titolo vecchio del file.
    """
    chat = load_chat(chat_id)
    if chat is None:
        return False
    chat["title"] = title
    return save_chat(chat)


def delete_chat(chat_id: str) -> None:
    try:
        (chats_dir() / f"{chat_id}.json").unlink()
    except OSError:
        pass
    index = [e for e in _load_index() if e.get("id") != chat_id]
    _write_json(_chats_index_path(), index)


def attachments_dir() -> Path:
    """Cartella privata per gli allegati generati dall'app (immagini incollate)."""
    d = config_dir() / "attachments"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def new_chat_id() -> str:
    return uuid.uuid4().hex[:12]


def now() -> float:
    return time.time()

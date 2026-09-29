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
    "history_limit": 20,        # messaggi di cronologia inviati come contesto
    "system_prompt": "",
    "web_results": 5,           # risultati di ricerca web allegati al contesto
    "web_provider": "duckduckgo",   # "duckduckgo" | "ollama" | "searxng"
    "web_api_key": "",          # chiave API per il provider "ollama" (ollama.com)
    "web_searxng_url": "http://localhost:8888",  # istanza SearXNG personale
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


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        tmp.replace(path)
        os.chmod(path, 0o600)   # niente letture da altri utenti
    except OSError:
        pass


# ---------------------------------------------------------------- impostazioni

def load_settings() -> dict:
    data = _read_json(config_dir() / "settings.json", {})
    out = dict(DEFAULT_SETTINGS)
    if isinstance(data, dict):
        out.update({k: v for k, v in data.items() if k in out})
    return out


def save_settings(settings: dict) -> None:
    merged = {**DEFAULT_SETTINGS, **settings}
    _write_json(config_dir() / "settings.json", merged)


# ---------------------------------------------------- parametri per modello

def load_model_params() -> dict:
    """{modello: {parametro: {"enabled": bool, "value": ...}}}"""
    data = _read_json(config_dir() / "model_params.json", {})
    return data if isinstance(data, dict) else {}


def save_model_params(params: dict) -> None:
    _write_json(config_dir() / "model_params.json", params)


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
    legacy.rename(legacy.with_suffix(".json.bak"))


def load_chats() -> list[dict]:
    """Indice dei metadati delle conversazioni, dalla più recente."""
    if not _chats_index_path().exists():
        _migrate_legacy_chats()
    index = _read_json(_chats_index_path(), [])
    out = [c for c in index if isinstance(c, dict) and c.get("id")]
    out.sort(key=lambda c: c.get("updated", 0), reverse=True)
    return out


def load_chat(chat_id: str) -> dict | None:
    data = _read_json(chats_dir() / f"{chat_id}.json", None)
    if isinstance(data, dict) and data.get("id") == chat_id and isinstance(data.get("messages"), list):
        return data
    return None


def save_chat(chat: dict) -> None:
    """Salva la conversazione (messaggi) e aggiorna l'indice."""
    chat_id = chat.get("id")
    if not chat_id:
        return
    _write_json(chats_dir() / f"{chat_id}.json", chat)
    index = _read_json(_chats_index_path(), [])
    index = [e for e in index if isinstance(e, dict) and e.get("id") != chat_id]
    index.append(
        {
            "id": chat_id,
            "title": chat.get("title", "Conversazione"),
            "model": chat.get("model", ""),
            "updated": chat.get("updated", 0),
        }
    )
    _write_json(_chats_index_path(), index)


def rename_chat(chat_id: str, title: str) -> None:
    index = _read_json(_chats_index_path(), [])
    for e in index:
        if isinstance(e, dict) and e.get("id") == chat_id:
            e["title"] = title
    _write_json(_chats_index_path(), index)


def delete_chat(chat_id: str) -> None:
    try:
        (chats_dir() / f"{chat_id}.json").unlink()
    except OSError:
        pass
    index = _read_json(_chats_index_path(), [])
    index = [e for e in index if isinstance(e, dict) and e.get("id") != chat_id]
    _write_json(_chats_index_path(), index)


def new_chat_id() -> str:
    return uuid.uuid4().hex[:12]


def now() -> float:
    return time.time()

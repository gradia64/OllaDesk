"""Controllo e installazione degli aggiornamenti di Ollama.

- Controllo versione: confronta la versione del server locale con l'ultima
  release pubblicata su GitHub (api.github.com).
- Aggiornamento: esegue lo script ufficiale di installazione tramite `pkexec`
  (richiede la password di amministratore, mostrata da KDE Polkit).
"""
from __future__ import annotations

import json
import re
import shutil
import urllib.request

from PySide6.QtCore import QThread, Signal

RELEASES_URL = "https://api.github.com/repos/ollama/ollama/releases/latest"
INSTALL_CMD = "curl -fsSL https://ollama.com/install.sh | sh"


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def update_command() -> list[str] | None:
    """Comando per l'aggiornamento (pkexec) o None se non disponibile."""
    pkexec = shutil.which("pkexec")
    if pkexec:
        return [pkexec, "sh", "-c", INSTALL_CMD]
    return None


class UpdateCheckWorker(QThread):
    """Scarica il numero dell'ultima release di Ollama da GitHub."""

    ready = Signal(str)    # versione più recente (senza 'v')
    failed = Signal(str)

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                RELEASES_URL,
                headers={"User-Agent": "olladesk", "Accept": "application/vnd.github+json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                tag = json.loads(resp.read().decode("utf-8")).get("tag_name", "")
        except Exception as e:
            self.failed.emit(f"impossibile controllare gli aggiornamenti: {e}")
            return
        self.ready.emit(tag.lstrip("vV"))

"""Controllo e installazione degli aggiornamenti di Ollama.

- Controllo versione: confronta la versione del server locale con l'ultima
  release pubblicata su GitHub (api.github.com).
- Aggiornamento: scarica lo script ufficiale in un file temporaneo, poi lo
  esegue tramite `pkexec` (richiede la password di amministratore, mostrata
  da KDE Polkit).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import urllib.request

from PySide6.QtCore import QThread, Signal

RELEASES_URL = "https://api.github.com/repos/ollama/ollama/releases/latest"
INSTALL_SCRIPT_URL = "https://ollama.com/install.sh"
INSTALL_CMD = "curl -fsSL https://ollama.com/install.sh | sh"

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def is_local_host(host: str) -> bool:
    """True se l'URL del server punta alla macchina locale."""
    from urllib.parse import urlparse

    if "//" not in (host or ""):
        host = "http://" + (host or "")
    try:
        hostname = (urlparse(host).hostname or "").lower()
    except ValueError:
        return False
    return hostname in _LOCAL_HOSTS


def update_command(local_path: str | None = None) -> list[str] | None:
    """Comando per l'aggiornamento (pkexec) o None se non disponibile."""
    pkexec = shutil.which("pkexec")
    if not pkexec:
        return None
    if local_path:
        return [pkexec, "sh", local_path]
    return [pkexec, "sh", "-c", INSTALL_CMD]


def update_command_stdin() -> list[str] | None:
    """`pkexec sh -s`: lo script arriva via stdin.

    Meglio di un file temporaneo in /tmp, che resterebbe di proprietà
    dell'utente mentre viene eseguito da root.
    """
    pkexec = shutil.which("pkexec")
    if not pkexec:
        return None
    return [pkexec, "sh", "-s"]


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


class UpdateDownloadWorker(QThread):
    """Scarica lo script ufficiale in un file temporaneo PRIMA di eseguirlo.

    Così l'utente vede cosa viene eseguito e con quale dimensione, invece di
    un `curl | sh` alla cieca.
    """

    ready = Signal(str, int)   # percorso del file, dimensione in byte
    failed = Signal(str)

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                INSTALL_SCRIPT_URL, headers={"User-Agent": "olladesk"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            fd, path = tempfile.mkstemp(prefix="ollama-install-", suffix=".sh")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.chmod(path, 0o755)
        except Exception as e:
            self.failed.emit(str(e))
            return
        self.ready.emit(path, len(data))

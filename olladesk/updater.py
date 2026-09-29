"""Controllo e installazione degli aggiornamenti di Ollama.

- Controllo versione: confronta la versione del server locale con l'ultima
  release pubblicata su GitHub (api.github.com).
- Aggiornamento: scarica in memoria lo script ufficiale, poi lo passa a
  `pkexec sh -s` via stdin (richiede la password di amministratore, mostrata
  da KDE Polkit). Nessun file temporaneo viene letto da root.
"""
from __future__ import annotations

import json
import re
import shutil
import socket
import urllib.request

from PySide6.QtCore import QThread, Signal

RELEASES_URL = "https://api.github.com/repos/ollama/ollama/releases/latest"
INSTALL_SCRIPT_URL = "https://ollama.com/install.sh"
INSTALL_CMD = "curl -fsSL https://ollama.com/install.sh | sh"

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "localhost.localdomain"}


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
    if hostname in _LOCAL_HOSTS or hostname.startswith("127."):
        return True   # Debian associa il nome macchina a 127.0.1.1
    try:
        names = {socket.gethostname().lower(), socket.getfqdn().lower()}
    except OSError:
        names = set()
    return hostname in names


def pkexec_available() -> bool:
    return shutil.which("pkexec") is not None


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
    """Scarica lo script ufficiale in memoria (nessun file su disco).

    Il contenuto viene poi passato a `pkexec sh -s` via stdin: root non legge
    mai un file in /tmp di proprietà dell'utente.
    """

    ready = Signal(bytes)   # contenuto dello script
    failed = Signal(str)

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                INSTALL_SCRIPT_URL, headers={"User-Agent": "olladesk"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
        except Exception as e:
            self.failed.emit(str(e))
            return
        if not data.startswith(b"#!"):
            self.failed.emit("il contenuto scaricato non sembra uno script di installazione")
            return
        self.ready.emit(data)

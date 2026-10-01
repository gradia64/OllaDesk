"""Controllo degli aggiornamenti di OllaDesk (solo avviso, nessuna installazione).

Confronta la versione in esecuzione con l'ultima release pubblicata su
GitHub e suggerisce come aggiornare in base a come l'app è stata installata:

- ``deb``    pacchetto .deb (scripts/build-deb.sh scrive il marcatore)
- ``arch``   pacchetto Arch/AUR (il PKGBUILD scrive il marcatore)
- ``source`` checkout git (cartella .git accanto al pacchetto)
- ``pip``    tutto il resto (pipx/pip)

Il controllo automatico parte all'avvio al massimo una volta ogni 24 ore
(disattivabile nelle impostazioni) e contatta solo api.github.com.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from . import __version__
from .updater import is_newer

REPO = "gradia64/OllaDesk"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
AUR_PAGE = "https://aur.archlinux.org/packages/olladesk"

CHECK_INTERVAL = 24 * 3600   # secondi tra due controlli automatici

_PACKAGE_DIR = Path(__file__).resolve().parent
MARKER_NAME = "_packaging"   # contenuto: "deb" | "arch"


def install_method(package_dir: Path = _PACKAGE_DIR) -> str:
    """Come è stata installata l'app: "deb" | "arch" | "source" | "pip"."""
    try:
        marker = (package_dir / MARKER_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        marker = ""
    if marker in ("deb", "arch"):
        return marker
    if (package_dir.parent / ".git").exists():
        return "source"
    return "pip"


def update_hint(method: str, version: str) -> str:
    """Istruzioni (testo semplice) per installare la versione `version`."""
    if method == "deb":
        return (
            f"Scarica olladesk_{version}_all.deb dalla pagina della release e "
            f"installalo con: sudo apt install ./olladesk_{version}_all.deb"
        )
    if method == "arch":
        return (
            "Aggiorna dal pacchetto AUR «olladesk» con il tuo helper "
            "(es. yay -Syu olladesk) oppure ricostruiscilo con makepkg."
        )
    if method == "source":
        return "Aggiorna il checkout: git pull, poi riavvia OllaDesk."
    return "Aggiorna il pacchetto Python: pipx upgrade olladesk (oppure pip install -U)."


def download_page(method: str) -> str:
    return AUR_PAGE if method == "arch" else RELEASES_PAGE


def parse_release(data: object) -> dict | None:
    """Estrae {"version", "url"} dalla risposta di /releases/latest.

    Le bozze e le pre-release non contano come aggiornamenti.
    """
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "").strip()
    version = tag.lstrip("vV")
    if not version:
        return None
    return {"version": version, "url": str(data.get("html_url") or RELEASES_PAGE)}


def newer_release(release: dict | None, current: str = __version__) -> dict | None:
    """La release se è più recente della versione in esecuzione, altrimenti None."""
    if release and is_newer(release["version"], current):
        return release
    return None


def due_for_check(settings: dict, now: float | None = None) -> bool:
    """True se il controllo automatico è attivo e sono passate 24 ore."""
    if not settings.get("app_update_check", True):
        return False
    now = time.time() if now is None else now
    try:
        last = float(settings.get("app_update_last_check") or 0)
    except (TypeError, ValueError):
        last = 0.0
    # orologio spostato indietro: ricontrolla invece di aspettare anni
    return now - last >= CHECK_INTERVAL or last > now


class AppUpdateCheckWorker(QThread):
    """Legge l'ultima release di OllaDesk da GitHub.

    ready(release | None): dict {"version", "url"} dell'ultima release
    pubblicata (anche se non più recente: il confronto lo fa il chiamante),
    None se il repository non ha ancora release.
    """

    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None, url: str | None = None):
        super().__init__(parent)
        self._url = url or RELEASES_API   # letto qui: sostituibile nei test
        self._resp = None
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True
        resp, self._resp = self._resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    def run(self) -> None:
        req = urllib.request.Request(
            self._url,
            headers={
                "User-Agent": f"olladesk/{__version__}",
                "Accept": "application/vnd.github+json",
            },
        )
        try:
            self._resp = urllib.request.urlopen(req, timeout=10)
            if self._stopped:
                return
            data = json.loads(self._resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if self._stopped:
                return
            if e.code == 404:
                self.ready.emit(None)   # nessuna release pubblicata
            elif e.code in (403, 429):
                self.failed.emit("limite di richieste a GitHub raggiunto, riprova più tardi")
            else:
                self.failed.emit(f"errore HTTP {e.code} da GitHub")
            return
        except Exception as e:
            if not self._stopped:
                reason = getattr(e, "reason", None) or e.__class__.__name__
                self.failed.emit(f"GitHub non raggiungibile ({reason})")
            return
        finally:
            resp, self._resp = self._resp, None
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass
        if not self._stopped:
            self.ready.emit(parse_release(data))

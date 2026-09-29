"""Avvio dell'applicazione (usato da main.py e da `python3 -m olladesk`)."""
import os
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from olladesk import theme
from olladesk.config import load_settings
from olladesk.main_window import MainWindow

ICON = Path(__file__).resolve().parent / "assets" / "olladesk.svg"
DESKTOP_FILE_NAME = "olladesk.desktop"


def _desktop_file_installed() -> bool:
    """True se olladesk.desktop è installato nelle cartelle applicazioni XDG.

    Qt registra l'app presso xdg-desktop-portal solo se l'ID dichiarato ha un
    .desktop installato: senza, il portal risponde «App info not found» e Qt
    stampa un warning all'avvio.
    """
    data_dirs = [os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")]
    data_dirs += os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    return any(
        (Path(d) / "applications" / DESKTOP_FILE_NAME).exists() for d in data_dirs if d
    )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("OllaDesk")
    app.setApplicationDisplayName("OllaDesk")
    app.setOrganizationDomain("olladesk.local")
    if _desktop_file_installed():
        app.setDesktopFileName("olladesk")
    if ICON.exists():
        app.setWindowIcon(QIcon(str(ICON)))

    settings = load_settings()
    theme.apply_theme(app, settings["theme"], settings["font_size"])

    win = MainWindow()
    win.show()
    return app.exec()

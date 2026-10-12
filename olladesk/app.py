"""Avvio dell'applicazione (usato da main.py e da `python3 -m olladesk`)."""
import os
import signal
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from olladesk import app_update, theme
from olladesk.config import load_settings
from olladesk.main_window import MainWindow

ICON = Path(__file__).resolve().parent / "assets" / "olladesk.svg"
# ID dell'app in forma DNS inversa: nome del .desktop e dell'icona installati,
# app_id su Wayland (setDesktopFileName) e, nella 0.4.0, ID del Flatpak
APP_ID = "io.github.gradia64.OllaDesk"
DESKTOP_FILE_NAME = f"{APP_ID}.desktop"


def _desktop_file_installed() -> bool:
    """True se il .desktop dell'app è installato nelle cartelle applicazioni XDG.

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
    # l'uscita passa SOLO dal quit esplicito nel closeEvent della finestra
    # principale («Esci» dalla tray, SIGTERM, logout o X senza tray): così la
    # chiusura di un dialogo con la finestra ridotta nella tray non può
    # terminare l'app per sbaglio
    app.setQuitOnLastWindowClosed(False)
    # nell'AppImage l'ID va sempre dichiarato: il .desktop lo installa
    # dopo un'integrazione (a mano, Gear Lever, AppImageLauncher), e senza
    # l'ID Plasma non lo collegherebbe alla finestra (app_id di ripiego
    # «local.olladesk.python3», icona generica). Il prezzo, finché l'AppImage
    # non è integrata, è l'avviso «App info not found» del portale
    if _desktop_file_installed() or app_update.install_method() == "appimage":
        app.setDesktopFileName(APP_ID)
    if ICON.exists():
        app.setWindowIcon(QIcon(str(ICON)))

    settings = load_settings()
    theme.apply_theme(app, settings["theme"], settings["font_size"])

    win = MainWindow()

    # sessione in chiusura (logout/spegnimento di Plasma): la finestra deve
    # accettare la chiusura reale, altrimenti ignorarla nel closeEvent per
    # ridurla nella tray farebbe bloccare la sessione
    app.commitDataRequest.connect(lambda *_a: setattr(win, "_really_quit", True))

    # SIGTERM/SIGINT (systemctl --user stop, Ctrl+C, kill): chiusura pulita
    # via closeEvent, così la companion web e i worker vengono fermati e la
    # porta della companion non resta in ascolto sulla rete
    def _graceful_shutdown(signum, _frame) -> None:  # noqa: ANN001 (handler segnali)
        win._really_quit = True
        try:
            win.close()
        except RuntimeError:
            pass   # finestra già distrutta

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    # i gestori Python dei segnali scattano solo quando l'interprete riprende
    # il controllo: il ciclo eventi di Qt non lo fa da solo, quindi senza un
    # risveglio periodico un SIGTERM resterebbe in attesa del primo evento
    # utile (anche 30 s: il tick del controllo stato)
    wake = QTimer()
    wake.start(250)
    wake.timeout.connect(lambda: None)

    win.show()
    ret = app.exec()
    # rete di sicurezza: un thread di rete ancora bloccato su un socket al
    # timeout farebbe abortire l'interprete alla pulizia di Qt; lo stato è
    # già stato salvato su disco a questo punto
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(ret)

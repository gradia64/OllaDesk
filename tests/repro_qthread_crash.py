"""Regressione «QThread: Destroyed while thread still running» (core dump).

Un server locale accetta la connessione e poi non risponde mai: i worker
restano bloccati in attesa della risposta, dove `stop()` non li può
interrompere (è il caso reale di un host lento o in blackhole).

Per il gestore modelli e per le impostazioni lo scenario è lo stesso:
chiusura del dialogo con il worker bloccato, poi distruzione del dialogo
(`deleteLater`, come fa MainWindow). Il worker deve essere parcheggiato
(senza genitore), sopravvivere al dialogo e terminare da solo al timeout di
rete, senza abort del processo e senza eccezioni negli slot.

Uso:  python3 tests/repro_qthread_crash.py   (nessuna rete, nessun Ollama)
"""
import os
import socket
import sys
import tempfile
import threading

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="olladesk_repro_config_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from olladesk import config, ollama_client, updater
from olladesk.widgets.model_manager import ModelManagerDialog
from olladesk.widgets.settings_dialog import SettingsDialog

# eccezioni sollevate dentro gli slot Qt: PySide le passa a sys.excepthook
slot_errors = []
sys.excepthook = lambda *exc: slot_errors.append(exc)


def silent_server() -> str:
    """Accetta connessioni e non risponde mai (le tiene aperte)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    held = []

    def loop():
        while True:
            conn, _ = srv.accept()
            held.append(conn)

    threading.Thread(target=loop, daemon=True).start()
    return f"http://127.0.0.1:{srv.getsockname()[1]}"


def wait_ms(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def close_destroy_and_check(dlg, worker, label):
    assert worker is not None and worker.isRunning(), f"[{label}] worker non in corsa"
    dlg.close()
    wait_ms(50)
    assert worker.isRunning(), f"[{label}] il worker bloccato non può morire subito"
    assert worker.parent() is None, f"[{label}] worker non sganciato dal dialogo"
    assert worker in ollama_client._parked, f"[{label}] worker non parcheggiato"
    dlg.deleteLater()
    wait_ms(100)   # esegue la distruzione del dialogo
    print(f"[{label}] dialogo distrutto con il worker ancora bloccato: nessun abort")
    assert worker.wait(15000), f"[{label}] il worker non è uscito al timeout di rete"
    wait_ms(100)   # deleteLater del worker
    print(f"[{label}] worker uscito al timeout e liberato")


app = QApplication(sys.argv)
host = silent_server()

# --- gestore modelli: /api/tags bloccato -----------------------------------
dlg = ModelManagerDialog(host)
dlg.show()
wait_ms(200)
close_destroy_and_check(dlg, dlg._list_worker, "modelli")

# --- impostazioni: verifica aggiornamenti bloccata (server e «GitHub») -------
updater.RELEASES_URL = host + "/releases/latest"
settings = config.load_settings()
settings["host"] = host
sdlg = SettingsDialog(settings, lambda: [], "dark", "?")
sdlg.show()
sdlg._check_updates()
wait_ms(200)
release = sdlg._release_worker
close_destroy_and_check(sdlg, sdlg._check_worker, "impostazioni")
assert release.wait(15000)

wait_ms(200)
assert not ollama_client._parked, f"worker parcheggiati non liberati: {ollama_client._parked}"
assert not slot_errors, f"eccezioni negli slot: {slot_errors}"
print("REPRO OK — nessun core dump, nessuna eccezione")

"""Verifica del fix «QThread: Destroyed while thread still running» nel
ModelManagerDialog (core dump riprodotto in sess_182e38d2).

Scenario 1 — server locale veloce: apertura, chiusura rapida, uscita pulita.
Scenario 2 — host irraggiungibile (connect appeso al timeout di rete):
la chiusura del dialogo deve fermare il worker e, se è ancora in corsa,
sganciarlo dal dialogo (parent None) così che nessuna distruzione lo tocchi.

Il driver attende la fine del worker residuo PRIMA di uscire: nel'app vera
lo stesso ruolo lo copre `os._exit` in app.py (vedi commento lì).
"""
import os
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="olladesk_repro_config_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PySide6.QtCore import QTimer, QEventLoop
from PySide6.QtWidgets import QApplication

from olladesk.widgets.model_manager import ModelManagerDialog


def wait_ms(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


app = QApplication(sys.argv)

# --- scenario 1: server locale, chiusura rapida --------------------------------
dlg = ModelManagerDialog("http://localhost:11434")
dlg.show()
wait_ms(300)
app.processEvents()
dlg.close()
app.processEvents()
wait_ms(500)
print("[1] server locale: chiusura rapida OK")

# --- scenario 2: host irraggiungibile, worker in corsa alla chiusura -----------
dlg2 = ModelManagerDialog("http://10.255.255.1:11434")
dlg2.show()
wait_ms(300)
app.processEvents()
w = dlg2._list_worker
if w is None or not w.isRunning():
    # ambiente in cui 10.255.255.1 non è blackholed (connect rifiutato subito):
    # lo scenario 1 copre comunque la chiusura, questo non è verificabile qui
    print("[2] SKIP: l'host di prova non appende il connect in questo ambiente")
else:
    dlg2.close()   # → reject() → _shutdown_workers()
    app.processEvents()
    assert w.isRunning(), "il worker bloccato nel connect non può morire subito"
    assert w.parent() is None, "il worker residuo deve essere sganciato dal dialogo"
    print("[2] host irraggiungibile: worker fermato, sganciato dal dialogo; attesa uscita…")
    assert w.wait(12000), "il worker non è uscito entro il timeout di rete (8 s)"
    print("[2] worker residuo uscito al timeout di rete: nessuna distruzione in corsa")

print("REPRO OK — nessun core dump")

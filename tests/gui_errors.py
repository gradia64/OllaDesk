"""Test del percorso di errore con server Ollama non raggiungibile."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile

# configurazione isolata in una cartella nuova: niente rmtree su percorsi fissi
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="olladesk_test_config_")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.makedirs("/tmp/olladesk_shots", exist_ok=True)

from PySide6.QtCore import QTimer, QEventLoop
from PySide6.QtWidgets import QApplication

from olladesk.main_window import MainWindow
from olladesk import config as _cfg

# il controllo aggiornamenti di OllaDesk (verso GitHub) ha il suo test
# offline, tests/offline_app_update.py: qui resta spento
_s = _cfg.load_settings()
_s["app_update_check"] = False
_cfg.save_settings(_s)


def wait_ms(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


app = QApplication(sys.argv)
win = MainWindow()
win.show()
wait_ms(2000)
app.processEvents()

# nessun modello (host sbagliato → elenco vuoto) → invio mostra la nota
win.settings["host"] = "http://127.0.0.1:9"
win._set_models([])
win.chat_area.set_input_text("ciao")
win.chat_area._emit_send()
wait_ms(400)
app.processEvents()
win.grab().save("/tmp/olladesk_shots/8_no_models.png")

# generazione verso un server inesistente → nota di errore
win.current_chat = {
    "id": "t1", "title": "t", "model": "test", "updated": 0,
    "messages": [{"role": "user", "content": "ciao", "ts": 0}],
}
win.chat_area.add_message("user", "ciao", 0)
win._start_generation()
wait_ms(3000)
app.processEvents()
assert not win._busy(), "il worker non è fallito come previsto"
win.grab().save("/tmp/olladesk_shots/9_offline.png")
print("ERROR PATH OK")

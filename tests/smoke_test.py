"""Smoke test offscreen: avvia la GUI, invia un messaggio reale a Ollama,
apre le impostazioni, salva/ripristina i parametri e salva screenshot.

Uso:  python3 tests/smoke_test.py
La configurazione viene isolata in $XDG_CONFIG_HOME per non toccare i file reali.
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_CONFIG_HOME"] = "/tmp/olladesk_test_config"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PySide6.QtCore import QTimer, QEventLoop
from PySide6.QtWidgets import QApplication

from olladesk.main_window import MainWindow
from olladesk.widgets.settings_dialog import SettingsDialog

SHOTS = "/tmp/olladesk_shots"
os.makedirs(SHOTS, exist_ok=True)

import shutil

shutil.rmtree(os.environ["XDG_CONFIG_HOME"], ignore_errors=True)


def wait_ms(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


app = QApplication(sys.argv)
win = MainWindow()
win.show()
wait_ms(2500)
app.processEvents()

print("online:", win._online)
print("models:", win.model_names())
win.grab().save(f"{SHOTS}/1_welcome.png")

chat_models = [m for m in win.model_names() if "embed" not in m.lower()]
assert chat_models, "nessun modello chat installato sul server"
win.model_combo.setCurrentIndex(win.model_combo.findData(chat_models[0]))

# --- invio un messaggio reale e attendo lo streaming -----------------------
win.chat_area.set_input_text("Rispondi in UNA sola frase: che cos'è un LLM?")
win.chat_area._emit_send()
wait_ms(1500)
app.processEvents()
win.grab().save(f"{SHOTS}/2_streaming.png")
print("streaming in corso:", win._busy())

t0 = time.time()
while win._busy() and time.time() - t0 < 120:
    wait_ms(200)
app.processEvents()
win.grab().save(f"{SHOTS}/3_done.png")
assert win.current_chat and len(win.current_chat["messages"]) == 2, "conversazione non salvata"
print("risposta completata, messaggi:", len(win.current_chat["messages"]))

# --- interruzione della generazione ----------------------------------------
win.chat_area.set_input_text("Conta fino a 100 lentamente, un numero per riga.")
win.chat_area._emit_send()
wait_ms(2500)
if win._busy():
    win._on_stop()
    wait_ms(1000)
    assert not win._busy(), "stop non ha fermato la generazione"
    print("stop OK")
else:
    print("stop SKIPPED (gia' terminata)")
app.processEvents()
win.grab().save(f"{SHOTS}/4_stopped.png")

# --- impostazioni -----------------------------------------------------------
dlg = SettingsDialog(win.settings, win.model_names, win.settings["theme"], win._version, win)
dlg.show()
wait_ms(400)
dlg.grab().save(f"{SHOTS}/5_settings_gui.png")

dlg.tabs.setCurrentWidget(dlg.params_tab)
wait_ms(400)
dlg.grab().save(f"{SHOTS}/6_settings_params.png")

# modifica, reset e salvataggio parametri
tab = dlg.params_tab
row_t = next(r for r in tab.rows if r.defn["key"] == "temperature")
row_t.set_value(0.35)
row_t.set_enabled(True)
assert tab.is_dirty()
dlg.params_tab.save_profile()
assert not tab.is_dirty()
from olladesk.widgets.model_params import options_for_model
opts = options_for_model(chat_models[0])
assert opts.get("temperature") == 0.35, opts
print("parametri salvati:", opts)

dlg.params_tab.reset_defaults()
dlg.params_tab.save_profile()
assert options_for_model(chat_models[0]) == {}, "reset non riuscito"
print("ripristino default OK")
dlg.close()

# --- cambio tema ------------------------------------------------------------
win._apply_settings({**win.settings, "theme": "light"})
wait_ms(400)
win.grab().save(f"{SHOTS}/7_light.png")
win._apply_settings({**win.settings, "theme": "dark"})
wait_ms(300)

# --- verifica persistenza ---------------------------------------------------
import json

cfg = os.environ["XDG_CONFIG_HOME"]
chats = json.load(open(f"{cfg}/olladesk/chats/index.json"))
assert chats, "chats.json vuoto"
params = json.load(open(f"{cfg}/olladesk/model_params.json"))
print("chats salvate:", len(chats), "| profili parametri:", list(params.keys()))

print("SMOKE OK")

"""Sincronizzazione dal vivo telefono ↔ PC (fase 3): finto Ollama, nessuna rete.

Copre lo stream globale /api/events (elenco e stato occupato con la
conversazione attiva), una risposta partita dal PC vista dal telefono e,
sulla finestra, la navigazione durante una generazione partita dal
telefono: pulsante ➤ sulle altre chat, invio rifiutato con un avviso,
risposta in corso ricostruita riaprendo la sua chat, stop con ■.

Uso:  python3 tests/sync_test.py   (nessuna rete, nessun Ollama)
"""
import os
import socket
import sys
import tempfile
import time
import traceback

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="olladesk_sync_config_")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from fake_ollama import MODELS, FakeOllama
from olladesk import config

slot_errors = []


def _hook(*exc):
    slot_errors.append(exc)
    traceback.print_exception(*exc)


sys.excepthook = _hook

app = QApplication([])
fake = FakeOllama()


def wait_until(pred, ms=5000):
    deadline = time.monotonic() + ms / 1000
    while not pred() and time.monotonic() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(10, loop.quit)
        loop.exec()
    return pred()


probe = socket.socket()
probe.bind(("127.0.0.1", 0))
PORT = probe.getsockname()[1]
probe.close()
s = config.load_settings()
s.update(host=fake.host, companion=True, companion_port=PORT,
         app_update_check=False, tray_icon=False)
config.save_settings(s)
for cid, title in (("chat_a", "Chat A"), ("chat_b", "Chat B")):
    config.save_chat({"id": cid, "title": title, "model": "finto", "updated": 1,
                      "messages": [{"role": "user", "display": f"inizio {title}", "ts": 1},
                                   {"role": "assistant", "content": "ok", "ts": 2}]})

import companion_client as h  # noqa: E402

from olladesk.main_window import MainWindow  # noqa: E402

win = MainWindow()
win._sync_companion()
assert win._companion.state() == "running"
assert wait_until(lambda: win.engine.model_names() == MODELS)
h.setup(PORT, wait_until)
TOKEN = h.pair(win._companion.auth)
engine = win.engine


def bubbles():
    return win.chat_area.msgs.count() - 1   # l'indice 0 è lo stretch


# ------------------------------------------- 1. stream globale dell'elenco

st, data = h.get_json("/api/chats", TOKEN)
assert st == 200 and data["active"] is None and isinstance(data["seq"], int)
glob = h.SSE(f"/api/events?after={data['seq']}", TOKEN)
assert wait_until(lambda: glob.of("busy"))          # stato iniziale
win._open_chat("chat_a")
win.chat_area.input.setPlainText("dal PC")
win.chat_area._emit_send()
assert wait_until(lambda: not engine.busy())
assert wait_until(lambda: glob.of("chats"))
# busy:false arriva dopo chats (prima si salva, poi si torna idle): va atteso
assert wait_until(lambda: len(glob.of("busy")) >= 3), glob.of("busy")   # iniziale, True, False
busy = glob.of("busy")[1:]
assert busy[0] == {"busy": True, "chat_id": "chat_a"} and busy[-1] == {"busy": False, "chat_id": ""}
assert not set(glob.kinds()) & {"start", "answer", "user", "done"}, glob.kinds()
glob.close()
print("1. stream globale: elenco e conversazione attiva OK")

# ------------------- 2. il telefono vede una risposta partita dal PC

st, snap = h.get_json("/api/chats/chat_a", TOKEN)
phone = h.SSE(f"/api/chats/chat_a/events?after={snap['seq']}", TOKEN)
assert wait_until(lambda: phone.of("busy"))
win.chat_area.input.setPlainText("ancora dal PC")
win.chat_area._emit_send()
assert wait_until(lambda: "done" in phone.kinds())
k = phone.kinds()
assert k.index("user") < k.index("start") < k.index("done"), k
assert "ancora dal PC" in phone.of("user")[0]["html"]
assert "<b>mondo</b>" in phone.of("answer")[-1]["html"]
phone.close()
print("2. risposta partita dal PC vista dal telefono OK")

# --------- 3. il telefono genera in B mentre il PC guarda A: navigazione

fake.release.clear()
st, res = h.post_json("/api/send", {"text": "rispondi lento", "model": "finto",
                                    "chat_id": "chat_b"}, TOKEN)
assert st == 200, res
assert wait_until(lambda: engine.partial()[0] == "parziale ")
st, data = h.get_json("/api/chats", TOKEN)
assert data["active"] == "chat_b"

# A è ancora aperta: ➤, elenco attivo, invio rifiutato con un avviso
assert win._view_id == "chat_a"
assert win.chat_area.send_btn.text() == "➤"
assert win.sidebar.list.isEnabled() and win.sidebar.new_btn.isEnabled()
n = bubbles()
win.chat_area.input.setPlainText("non deve partire")
win.chat_area._emit_send()
assert bubbles() == n + 1, "manca l'avviso «sta già rispondendo»"
assert win.chat_area.input.toPlainText() == "non deve partire"
assert engine.active_chat_id() == "chat_b"

# nuova chat durante la generazione: consentito
win._new_chat()
assert win._view_id is None and win.chat_area.send_btn.text() == "➤"

# riaprendo B la risposta in corso riappare con il testo già ricevuto
win._open_chat("chat_b")
assert win.chat_area.send_btn.text() == "■"
assert wait_until(lambda: win.chat_area._stream_widget is not None
                  and "parziale" in win.chat_area._stream_widget.raw)
# il cambio tema la ricostruisce (prima si saltava durante la generazione)
win._rerender_messages()
assert wait_until(lambda: win.chat_area._stream_widget is not None
                  and "parziale" in win.chat_area._stream_widget.raw)

# ■ dal PC ferma la risposta del telefono e conserva il testo
win.chat_area._on_button()
assert not engine.busy()
assert config.load_chat("chat_b")["messages"][-1]["content"] == "parziale "
assert win.chat_area.send_btn.text() == "➤"
fake.release.set()
print("3. PC libero di navigare durante la risposta del telefono OK")

# ------- 4. il PC lascia la sua chat durante la risposta e la ritrova

fake.release.clear()
win._open_chat("chat_a")
win.chat_area.input.setPlainText("rispondi lento")
win.chat_area._emit_send()
assert wait_until(lambda: engine.partial()[0] == "parziale ")
win._open_chat("chat_b")
assert win.chat_area.send_btn.text() == "➤"
fake.release.set()
assert wait_until(lambda: not engine.busy())
assert config.load_chat("chat_a")["messages"][-1]["content"] == "parziale e finale"
win._open_chat("chat_a")
assert win.current_chat["messages"][-1]["content"] == "parziale e finale"
assert win.chat_area._stream_widget is None and win.chat_area.send_btn.text() == "➤"
print("4. risposta del PC completata in background OK")

win._really_quit = True
win.close()
wait_until(lambda: False, 200)
fake.close()
assert not slot_errors, f"eccezioni negli slot: {slot_errors}"
print("SYNC OK")

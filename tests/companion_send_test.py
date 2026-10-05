"""Test dell'invio dal telefono (fase 2): finto Ollama, nessuna rete.

Copre /api/models, validazione di /api/send, streaming SSE (eventi,
Markdown reso, statistiche), risposta «occupato», ricollegamento a metà
risposta, stop, think=False, arresto del server con stream aperti e
l'effetto sulla finestra desktop (messaggio visibile, bozza conservata).

Uso:  python3 tests/companion_send_test.py   (nessuna rete, nessun Ollama)
"""
import json
import os
import socket
import sys
import tempfile
import time
import traceback

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="olladesk_send_config_")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from fake_ollama import MODELS, FakeOllama
from olladesk import companion, config
from olladesk.engine import ChatEngine

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


settings = config.load_settings()
settings.update(host=fake.host, app_update_check=False, tray_icon=False)
config.save_settings(settings)

engine = ChatEngine(config.load_settings())
engine.check_server()
assert wait_until(lambda: engine.model_names() == MODELS), "modelli non caricati"

srv = companion.CompanionServer(engine)
srv.start(0, "127.0.0.1")
PORT = srv.port


import companion_client as cc  # noqa: E402

cc.setup(PORT, wait_until)
request, SSE = cc.request, cc.SSE


def jpost(path, body, cookie):
    return cc.post_json(path, body, cookie)


# token di un dispositivo abbinato
code, _ = srv.auth.new_code()
st, hd, _b = request("POST", "/api/pair", {"code": code})
assert st == 200
TOKEN = hd["Set-Cookie"].split(";")[0].split("=", 1)[1]

# ------------------------------------------------------------ 1. modelli

st, _h, raw = request("GET", "/api/models", cookie=TOKEN)
info = json.loads(raw)
assert st == 200 and [m["name"] for m in info["models"]] == MODELS
assert info["online"] is True and info["busy"] is False and info["thinking"] is True
assert info["models"][0]["details"] == {"parameter_size": "1B", "quantization_level": "Q4"}
assert request("GET", "/api/models")[0] == 401
print("1. modelli e stato del server OK")

# ---------------------------------------------------------- 2. validazione

for body, status in (
    ({"text": "   ", "model": "finto"}, 400),
    ({"text": "x" * (companion.MAX_TEXT + 1), "model": "finto"}, 400),
    ({"text": "ciao", "model": "inesistente"}, 400),
    ({"text": "ciao"}, 400),
    ({"text": "ciao", "model": "finto", "chat_id": "../etc"}, 400),
    ({"text": "ciao", "model": "finto", "chat_id": 5}, 400),
):
    got, data = jpost("/api/send", body, TOKEN)
    assert got == status, (body, got, data)
assert request("POST", "/api/send", {"text": "ciao", "model": "finto"})[0] == 401
# id di una conversazione che non esiste (eliminata sul PC): 404, niente chat ricreata
got, data = jpost("/api/send", {"text": "ciao", "model": "finto", "chat_id": "eliminata"}, TOKEN)
assert got == 404 and "inesistente" in data["error"], (got, data)
assert not engine.busy() and engine.chats() == []
print("2. validazione dell'invio OK")

# ------------------------------------------------- 3. invio e streaming SSE

st, res = jpost("/api/send", {"text": "ciao <b>", "model": "finto"}, TOKEN)
assert st == 200 and res["chat_id"] and isinstance(res["after"], int), res
cid = res["chat_id"]
s = SSE(f"/api/chats/{cid}/events?after={res['after']}", TOKEN)
assert wait_until(lambda: "done" in s.kinds()), s.kinds()
k = s.kinds()
assert s.status == 200
assert k.index("user") < k.index("start") < k.index("answer") < k.index("done"), k
user = s.of("user")[0]
assert "&lt;b&gt;" in user["html"] and "<b>" not in user["html"].replace("&lt;b&gt;", "")
last = s.of("answer")[-1]
assert "<b>mondo</b>" in last["html"] and "ragiono" in last["thinking_html"]
done = s.of("done")[0]
assert done["outcome"] == "done" and done["stats"] and done["error"] == ""
assert abs(done["ts"] - time.time()) < 60, "orario della risposta dal PC mancante"
assert [b["busy"] for b in s.of("busy")][-1] is False
ids = [e[0] for e in s.events if e[0] is not None]
assert ids == sorted(ids) and all(i > res["after"] for i in ids)
saved = config.load_chat(cid)
assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
assert fake.payloads[-1]["model"] == "finto" and "think" not in fake.payloads[-1]
s.close()
print("3. invio, eventi SSE e Markdown reso OK")

# -------------------------------- 4. occupato, ricollegamento e stop a metà

fake.release.clear()
st, res = jpost("/api/send", {"text": "rispondi lento", "model": "finto-due",
                              "chat_id": cid, "think": False}, TOKEN)
assert st == 200 and res["chat_id"] == cid
assert fake.payloads and wait_until(lambda: fake.payloads[-1]["messages"][-1]["content"] == "rispondi lento")
assert fake.payloads[-1]["think"] is False and fake.payloads[-1]["model"] == "finto-due"
st, data = jpost("/api/send", {"text": "altro", "model": "finto"}, TOKEN)
assert st == 409 and "occupato" in data["error"]

# un telefono che apre la conversazione a metà risposta
assert wait_until(lambda: engine._pending_stream == "parziale ")
st, _h, raw = request("GET", f"/api/chats/{cid}", cookie=TOKEN)
snap = json.loads(raw)
assert snap["state"] == "chat" and isinstance(snap["seq"], int)
late = SSE(f"/api/chats/{cid}/events?after={snap['seq']}", TOKEN)
assert wait_until(lambda: late.of("answer")), late.kinds()
assert "parziale" in late.of("answer")[0]["html"]
assert "user" not in late.kinds() and "start" not in late.kinds(), "eventi già noti rispediti"

# lo stop vale solo per la conversazione che il telefono guarda
st, data = jpost("/api/stop", {"chat_id": "altra"}, TOKEN)
assert st == 200 and data["stopped"] is False and engine.busy()
st, data = jpost("/api/stop", {"chat_id": cid}, TOKEN)
assert st == 200 and data["stopped"] is True and not engine.busy()
assert wait_until(lambda: "done" in late.kinds())
assert late.of("done")[-1]["outcome"] == "stopped"
assert config.load_chat(cid)["messages"][-1]["content"] == "parziale "
fake.release.set()
late.close()
print("4. occupato, ricollegamento a metà risposta e stop OK")

# ---------------------------------------- 5. Last-Event-ID dopo una caduta

st, res = jpost("/api/send", {"text": "ancora", "model": "finto", "chat_id": cid}, TOKEN)
assert st == 200
s1 = SSE(f"/api/chats/{cid}/events?after={res['after']}", TOKEN)
assert wait_until(lambda: "done" in s1.kinds())
s1.close()
start_id = next(e[0] for e in s1.events if e[1] == "start")


# ricollegamento automatico di EventSource: Last-Event-ID = ultimo id ricevuto
again = SSE(f"/api/chats/{cid}/events?after={res['after']}", TOKEN,
            headers={"Last-Event-ID": str(start_id)})
assert wait_until(lambda: "done" in again.kinds()), again.kinds()
assert "start" not in again.kinds() and "user" not in again.kinds(), again.kinds()
assert "<b>mondo</b>" in again.of("answer")[-1]["html"], "testo perso nel ricollegamento"
again.close()
print("5. Last-Event-ID: niente eventi doppi OK")

# HEAD sugli stream: rifiutata (prima apriva uno stream senza fine)
assert request("HEAD", f"/api/chats/{cid}/events", cookie=TOKEN)[0] == 405
assert request("HEAD", "/api/events", cookie=TOKEN)[0] == 405
assert request("HEAD", "/", cookie=TOKEN)[0] == 200

# ------------------------------------------------ 6. arresto con stream aperti

idle = SSE(f"/api/chats/{cid}/events?after=0", TOKEN)
assert wait_until(lambda: idle.status == 200)
srv.stop()
assert wait_until(lambda: idle.ended, 5000), "lo stream SSE non si è chiuso con il server"
print("6. stream chiusi all'arresto del server OK")

# -------------------------------------------------- 7. finestra desktop

probe = socket.socket()
probe.bind(("127.0.0.1", 0))
WIN_PORT = probe.getsockname()[1]
probe.close()
s2 = config.load_settings()
s2.update(companion=True, companion_port=WIN_PORT)
config.save_settings(s2)

from olladesk.main_window import MainWindow  # noqa: E402

win = MainWindow()
win._sync_companion()
assert win._companion.state() == "running"
assert wait_until(lambda: win.engine.model_names() == MODELS)
win._open_chat(cid)
before = win.chat_area.msgs.count()
win.chat_area.input.setPlainText("bozza sul PC")
PORT = WIN_PORT
cc.setup(PORT, wait_until)
st, res = jpost("/api/send", {"text": "dal telefono", "model": "finto", "chat_id": cid}, TOKEN)
assert st == 200, res
assert wait_until(lambda: not win.engine.busy() and win.chat_area.msgs.count() == before + 2)
assert win.current_chat["messages"][-2]["display"] == "dal telefono"
assert win.chat_area.input.toPlainText() == "bozza sul PC", "la bozza del PC è stata cancellata"
assert win.chat_area.send_btn.text() == "➤"
win._really_quit = True
win.close()
wait_until(lambda: False, 200)
fake.close()

assert not slot_errors, f"eccezioni negli slot: {slot_errors}"
print("7. messaggio dal telefono visibile sul PC, bozza conservata OK")
print("COMPANION SEND OK")

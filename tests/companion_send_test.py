"""Test dell'invio dal telefono (fase 2): finto Ollama, nessuna rete.

Copre /api/models, validazione di /api/send, streaming SSE (eventi,
Markdown reso, statistiche), risposta «occupato», ricollegamento a metà
risposta, stop, think=False, ricerca web dal telefono (#8), arresto del
server con stream aperti e
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

from PySide6.QtCore import QEventLoop, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication

from fake_ollama import MODELS, FakeOllama
from olladesk import companion, config, web_search
from olladesk.engine import ChatEngine

slot_errors = []


def _hook(*exc):
    slot_errors.append(exc)
    traceback.print_exception(*exc)


sys.excepthook = _hook

app = QApplication([])
fake = FakeOllama()


class FakeSearch(QThread):
    """Ricerca web finta: nessuna rete, risultati fissi."""
    ready = Signal(str, str)
    failed = Signal(str)
    notice = Signal(str)
    queries: list = []

    def __init__(self, query, *_a, **_kw):
        super().__init__(_kw.get("parent"))
        self._query = query
        FakeSearch.queries.append(query)

    def stop(self):
        pass

    def run(self):
        self.ready.emit("RISULTATI WEB DAL TELEFONO", self._query)


web_search.WebSearchWorker = FakeSearch


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
    # booleani veri: la stringa "false" non deve accendere nulla
    ({"text": "ciao", "model": "finto", "web": "false"}, 400),
    ({"text": "ciao", "model": "finto", "web": 1}, 400),
    ({"text": "ciao", "model": "finto", "think": "false"}, 400),
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
assert saved["messages"][0]["web"] is False and FakeSearch.queries == []   # spenta di default
s.close()
print("3. invio, eventi SSE e Markdown reso OK")

# ------------------------------------------ 3b. ricerca web dal telefono (#8)

st, res = jpost("/api/send", {"text": "notizie dal telefono", "model": "finto", "web": True,
                              "chat_id": cid}, TOKEN)
assert st == 200 and res["chat_id"] == cid, res
s = SSE(f"/api/chats/{cid}/events?after={res['after']}", TOKEN)
assert wait_until(lambda: "done" in s.kinds()), s.kinds()
k = s.kinds()
# come sul PC (0.2.6): il messaggio compare prima della ricerca
assert k.index("user") < k.index("search") < k.index("search_done") < k.index("start"), k
assert s.of("user")[0]["web"] is True
assert FakeSearch.queries == ["notizie dal telefono"]
saved = config.load_chat(cid)["messages"]
assert saved[-2]["web"] is True and saved[-2]["web_block"] == "RISULTATI WEB DAL TELEFONO"
assert "RISULTATI WEB DAL TELEFONO" in fake.payloads[-1]["messages"][-1]["content"]
s.close()
print("3b. ricerca web dal telefono OK")

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

# ------------------------------------------------- 5b. allegati dal telefono

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
       b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01"
       b"\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")
st, img = cc.upload("foto.png", PNG, TOKEN)
assert st == 200 and img["kind"] == "image" and img["name"] == "foto.png", (st, img)
st, txt = cc.upload("../../etc/note segrete.txt", "contenuto ALLEGATO".encode(), TOKEN)
assert st == 200 and txt["kind"] == "text" and txt["name"] == "note segrete.txt", txt
folder = srv.uploads.folder()
saved = sorted(folder.iterdir())
assert len(saved) == 2 and all(p.parent == folder for p in saved), "file fuori dalla cartella privata"
assert all(p.stat().st_mode & 0o077 == 0 for p in saved), "allegati leggibili da altri utenti"

# rifiuti: niente login, tipo sbagliato, estensione ignota, troppo grande, vuoto
assert cc.upload("a.png", PNG, "token-falso")[0] == 401
assert cc.upload("a.png", PNG, TOKEN, ctype="image/png")[0] == 415
assert cc.upload("virus.exe", b"MZ", TOKEN)[0] == 415
assert cc.upload("enorme.png", b"x", TOKEN, length=companion.MAX_UPLOAD + 1)[0] == 413
assert cc.upload("vuoto.txt", b"", TOKEN)[0] == 413
assert srv.uploads.pending() == 2

# nome lungo in byte (100 caratteri cinesi): accettato, troncato sotto NAME_MAX
st, cjk = cc.upload("回" * 100 + ".png", PNG, TOKEN)
assert st == 200 and cjk["name"].endswith(".png"), (st, cjk)
assert len(f"{'0' * 16}_{cjk['name']}".encode()) <= 255
# file col punto iniziale: riconosciuti come sul PC
st, env = cc.upload(".env", b"CHIAVE=1", TOKEN)
assert st == 200 and env["kind"] == "text" and env["name"] == ".env", (st, env)
# il PC non riesce a salvare (cartella non scrivibile): 500, non un silenzio
folder.chmod(0o500)
try:
    st, data = cc.upload("bloccato.txt", b"x", TOKEN)
finally:
    folder.chmod(0o700)
assert st == 500 and "salvare" in data["error"], (st, data)
got, _d = jpost("/api/send", {"text": "", "model": "finto", "attachments": [cjk["id"], cjk["id"]]}, TOKEN)
assert got == 400, "id ripetuti accettati"
assert srv.uploads.take([cjk["id"], cjk["id"]]) is None
assert srv.uploads.take([cjk["id"], env["id"]]) and srv.uploads.pending() == 2   # toglie i due di prova

# id sconosciuto: rifiutato senza consumare gli altri
got, data = jpost("/api/send", {"text": "", "model": "finto", "attachments": [img["id"], "nonesiste"]}, TOKEN)
assert got == 400 and "allegato" in data["error"] and srv.uploads.pending() == 2

# PC occupato: 409 e gli allegati restano disponibili
fake.release.clear()
st, res = jpost("/api/send", {"text": "rispondi lento", "model": "finto", "chat_id": cid}, TOKEN)
assert st == 200
got, data = jpost("/api/send", {"text": "", "model": "finto", "attachments": [img["id"]]}, TOKEN)
assert got == 409 and srv.uploads.pending() == 2
jpost("/api/stop", {"chat_id": cid}, TOKEN)
fake.release.set()
assert wait_until(lambda: not engine.busy())

# messaggio fatto di soli allegati: immagine in base64, testo nel contenuto
st, res = jpost("/api/send", {"text": "", "model": "finto", "chat_id": cid,
                              "attachments": [img["id"], txt["id"]]}, TOKEN)
assert st == 200, res
assert wait_until(lambda: not engine.busy())
last = fake.payloads[-1]["messages"][-1]
assert last["images"] and "contenuto ALLEGATO" in last["content"]
user = config.load_chat(cid)["messages"][-2]
assert user["attachments"] == ["foto.png", "note segrete.txt"]
assert srv.uploads.pending() == 0
# monouso: lo stesso id non vale una seconda volta
got, _d = jpost("/api/send", {"text": "di nuovo", "model": "finto", "chat_id": cid,
                              "attachments": [img["id"]]}, TOKEN)
assert got == 400

# chat nuova con 🌐 acceso e soli allegati: la ricerca salta e il telefono,
# che apre lo stream solo dopo la risposta, deve vedere l'avviso (prima
# finiva nel backlog che busy_changed azzera subito dopo)
st, solo = cc.upload("solo.png", PNG, TOKEN)
st, res = jpost("/api/send", {"text": "", "model": "finto", "web": True,
                              "attachments": [solo["id"]]}, TOKEN)
assert st == 200, res
s = SSE(f"/api/chats/{res['chat_id']}/events?after={res['after']}", TOKEN)
assert wait_until(lambda: "done" in s.kinds()), s.kinds()
assert any("Ricerca web saltata" in n["text"] for n in s.of("notice")), s.events
assert "search" not in s.kinds()
s.close()

# caricamenti mai inviati: spariscono alla scadenza e all'arresto del server
st, orfano = cc.upload("orfano.txt", b"x", TOKEN)
orfano_path = next(p for p in folder.iterdir() if p.name.endswith("_orfano.txt"))
srv.uploads.purge(max_age=0)
assert not orfano_path.exists() and srv.uploads.pending() == 0
st, orfano = cc.upload("orfano2.txt", b"x", TOKEN)
assert srv.uploads.pending() == 1
# spegnimento durante un caricamento: nessun file resta orfano nella cartella
import io  # noqa: E402


class StopMidway(io.BytesIO):
    """Corpo che a metà lettura spegne la companion (come uno stop dall'utente)."""

    def read(self, n=-1):
        srv.uploads.clear()
        return super().read(n)


before = set(folder.iterdir())
try:
    srv.uploads.add("a-meta.txt", "text", StopMidway(b"x" * 10), 10)
    raise AssertionError("il caricamento doveva fallire")
except OSError:
    pass
after = set(folder.iterdir())
assert after <= before and not any(p.name.endswith("_a-meta.txt") for p in after), \
    "file orfano dopo lo spegnimento"

# limite dei caricamenti in attesa controllato sotto lock
saved_max = companion.MAX_PENDING
companion.MAX_PENDING = srv.uploads.pending()
try:
    st, data = cc.upload("troppi.txt", b"x", TOKEN)
    assert st == 429, (st, data)
finally:
    companion.MAX_PENDING = saved_max
print("5b. allegati: caricamento, limiti, invio, monouso e pulizia OK")

# HEAD sugli stream: rifiutata (prima apriva uno stream senza fine)
assert request("HEAD", f"/api/chats/{cid}/events", cookie=TOKEN)[0] == 405
assert request("HEAD", "/api/events", cookie=TOKEN)[0] == 405
assert request("HEAD", "/", cookie=TOKEN)[0] == 200

# ------------------------------------------------ 6. arresto con stream aperti

idle = SSE(f"/api/chats/{cid}/events?after=0", TOKEN)
assert wait_until(lambda: idle.status == 200)
srv.stop()
assert wait_until(lambda: idle.ended, 5000), "lo stream SSE non si è chiuso con il server"
assert srv.uploads.pending() == 0 and not any(p.name.endswith("_orfano2.txt") for p in folder.iterdir())
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

"""Test del ChatEngine senza widget: finto server Ollama su una porta casuale.

Copre invio, streaming (testo e ragionamento), persistenza, stop a metà,
errore con testo parziale, ricerca web simulata e rifiuto quando occupato.

Uso:  python3 tests/engine_test.py   (nessuna rete, nessun Ollama)
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="olladesk_engine_config_")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication, QEventLoop, QThread, QTimer, Signal

from olladesk import config, web_search
from olladesk.engine import ChatEngine

# eccezioni sollevate dentro gli slot Qt: PySide le passa a sys.excepthook
slot_errors = []
sys.excepthook = lambda *exc: slot_errors.append(exc)

# ------------------------------------------------------------ finto Ollama

payloads: list[dict] = []
release = threading.Event()   # sblocca le risposte «lente»


class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def _line(self, obj) -> None:
        self.wfile.write((json.dumps(obj) + "\n").encode())
        self.wfile.flush()

    def do_POST(self):  # noqa: N802 (API http.server)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        payloads.append(body)
        last = body["messages"][-1]["content"]
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        try:
            if "lento" in last:
                self._line({"message": {"content": "parziale "}})
                release.wait(10)
            elif "pensa" in last:
                self._line({"message": {"thinking": "sto pensando"}})
                release.wait(10)
            elif "errore" in last:
                self._line({"message": {"content": "mezza risposta"}})
                self._line({"error": "modello esploso"})
            else:
                self._line({"message": {"thinking": "ragiono"}})
                self._line({"message": {"content": "Ciao, "}})
                self._line({"message": {"content": "mondo"}})
                self._line({"done": True, "eval_count": 10, "eval_duration": 1_000_000_000})
        except OSError:
            pass   # connessione chiusa dallo stop


server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
threading.Thread(target=server.serve_forever, daemon=True).start()
HOST = f"http://127.0.0.1:{server.server_address[1]}"


# ------------------------------------------------------- finta ricerca web

class FakeSearch(QThread):
    ready = Signal(str, str)
    failed = Signal(str)
    hang = False

    def __init__(self, query, *_a, **_kw):
        super().__init__(_kw.get("parent"))
        self._query = query
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        if FakeSearch.hang:
            while not self._stopped:
                time.sleep(0.02)
            return
        self.ready.emit("RISULTATI WEB DI PROVA", self._query)


web_search.WebSearchWorker = FakeSearch

# ------------------------------------------------------------------ helper

app = QCoreApplication([])


def wait_until(pred, ms=5000):
    deadline = time.monotonic() + ms / 1000
    while not pred() and time.monotonic() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(20, loop.quit)
        loop.exec()
    return pred()


settings = config.load_settings()
settings.update(host=HOST, stream=True, system_prompt="sei un test", history_limit=20)
engine = ChatEngine(settings)

events: list[tuple] = []
for name in ("chats_changed", "user_message_added", "busy_changed", "search_started",
             "search_finished", "generation_started", "text_chunk", "think_chunk",
             "generation_finished", "notice"):
    getattr(engine, name).connect(lambda *a, n=name: events.append((n, *a)))


def names():
    return [e[0] for e in events]


def finished():
    return [e for e in events if e[0] == "generation_finished"]


# ---------------------------------------------------- 1. invio completo

cid = engine.new_chat_id()
assert engine.send(cid, "ciao", "finto") == cid
assert engine.busy() and engine.active_chat_id() == cid
# una sola elaborazione alla volta: il secondo invio è rifiutato
assert engine.send(None, "altro", "finto") is None
assert wait_until(lambda: not engine.busy()), "la generazione non è terminata"

f = finished()
assert len(f) == 1 and f[0][1:3] == (cid, "done") and f[0][4] == "", f
assert f[0][3], "statistiche mancanti"
assert "".join(e[2] for e in events if e[0] == "text_chunk") == "Ciao, mondo"
assert [e[2] for e in events if e[0] == "think_chunk"] == ["ragiono"]
order = names()
assert order.index("user_message_added") < order.index("generation_started") < order.index("text_chunk")
assert order[-1] == "busy_changed" and events[-1][1] is False
assert payloads[-1]["messages"][0] == {"role": "system", "content": "sei un test"}
assert "think" not in payloads[-1]

saved = config.load_chat(cid)
assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
assert saved["messages"][1]["content"] == "Ciao, mondo"
assert saved["messages"][1]["thinking"] == "ragiono"
assert saved["title"] == "ciao"
assert [c["id"] for c in engine.chats()] == [cid]
print("1. invio, streaming e salvataggio OK")

# ---------------------------------------------- 2. 🧠 spento e secondo turno

events.clear()
assert engine.send(cid, "di nuovo", "finto", think=False) == cid
assert wait_until(lambda: not engine.busy())
assert payloads[-1]["think"] is False
assert [m["role"] for m in payloads[-1]["messages"]] == ["system", "user", "assistant", "user"]
assert len(engine.chat(cid)["messages"]) == 4
print("2. think=False e cronologia OK")

# --------------------------------------------- 3. stop con testo parziale

events.clear()
release.clear()
c3 = engine.send(None, "rispondi lento", "finto")
assert wait_until(lambda: "text_chunk" in names())
engine.stop()
assert not engine.busy()
assert finished()[-1][1:3] == (c3, "stopped")
msgs = config.load_chat(c3)["messages"]
assert (msgs[-1]["role"], msgs[-1]["content"]) == ("assistant", "parziale ")
release.set()
print("3. stop con testo parziale conservato OK")

# ------------------------------------------ 4. stop durante il solo pensiero

events.clear()
release.clear()
c4 = engine.send(None, "pensa e basta", "finto")
assert wait_until(lambda: "think_chunk" in names())
engine.stop()
assert finished()[-1][1:3] == (c4, "stopped")
assert [m["role"] for m in config.load_chat(c4)["messages"]] == ["user"]
release.set()
print("4. stop durante il ragionamento: nessun messaggio OK")

# ------------------------------------------------ 5. errore dopo testo parziale

events.clear()
c5 = engine.send(None, "provoca un errore", "finto")
assert wait_until(lambda: not engine.busy())
f = finished()[-1]
assert f[1:3] == (c5, "failed") and "modello esploso" in f[4]
assert config.load_chat(c5)["messages"][-1]["content"] == "mezza risposta"
print("5. errore con testo parziale conservato OK")

# ---------------------------------------------------------- 6. ricerca web

events.clear()
c6 = engine.send(None, "notizie di oggi", "finto", web=True)
assert wait_until(lambda: not engine.busy())
order = names()
assert order.index("search_started") < order.index("search_finished") < order.index("user_message_added")
user = config.load_chat(c6)["messages"][0]
assert user["web"] and user["web_block"] == "RISULTATI WEB DI PROVA"
assert "RISULTATI WEB DI PROVA" in payloads[-1]["messages"][-1]["content"]
print("6. ricerca web prima del messaggio utente OK")

# --------------------------------------- 7. stop durante la ricerca web

events.clear()
FakeSearch.hang = True
c7 = engine.send(None, "cerca e aspetta", "finto", web=True)
assert engine.busy()
engine.stop()
FakeSearch.hang = False
assert not engine.busy()
assert names() == ["busy_changed", "search_started", "search_finished", "busy_changed"]
assert engine.chat(c7) is None and c7 not in [c["id"] for c in engine.chats()]
print("7. stop durante la ricerca: nessuna conversazione creata OK")

# ------------------------------------------- 8. avvisi, rinomina, eliminazione

events.clear()
c8 = engine.send(None, "", "finto", web=True,
                 attachments=[{"path": "/non/esiste.txt", "name": "esiste.txt", "kind": "text"}])
assert wait_until(lambda: not engine.busy())
notes = [e[2] for e in events if e[0] == "notice" and e[1] == c8]
assert any("file non leggibile" in n for n in notes)
assert any("Ricerca web saltata" in n for n in notes)

engine.rename_chat(cid, "  rinominata  ")
assert engine.chat(cid)["title"] == "rinominata"
assert config.load_chat(cid)["title"] == "rinominata"
assert next(c for c in engine.chats() if c["id"] == cid)["title"] == "rinominata"
engine.delete_chat(cid)
assert config.load_chat(cid) is None and engine.chat(cid) is None
assert cid not in [c["id"] for c in engine.chats()]
print("8. avvisi, rinomina ed eliminazione OK")

# ---------------------------------------------------------------- chiusura

from olladesk.ollama_client import shutdown_workers  # noqa: E402

shutdown_workers(engine.shutdown())
assert engine.send(None, "dopo la chiusura", "finto") is None
wait_until(lambda: False, 200)   # consegna dei segnali in coda
server.shutdown()
assert not slot_errors, f"eccezioni negli slot: {slot_errors}"
print("ENGINE OK")

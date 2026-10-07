"""Test del ChatEngine senza widget: finto server Ollama su una porta casuale.

Copre invio, streaming (testo e ragionamento), persistenza, stop a metà,
errore con testo parziale, ricerca web simulata (messaggio subito in chat,
riserva, portachiavi) e rifiuto quando occupato.

Uso:  python3 tests/engine_test.py   (nessuna rete, nessun Ollama)
"""
import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="olladesk_engine_config_")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QCoreApplication, QEventLoop, QThread, QTimer, Signal

from olladesk import config, secrets_store, web_search
from olladesk.engine import ChatEngine

# eccezioni sollevate dentro gli slot Qt: PySide le passa a sys.excepthook
slot_errors = []
sys.excepthook = lambda *exc: slot_errors.append(exc)

# ------------------------------------------------------------ finto Ollama

from fake_ollama import FakeOllama  # noqa: E402

fake = FakeOllama()
payloads = fake.payloads
release = fake.release
HOST = fake.host


# ------------------------------------------------------- finta ricerca web

class FakeSearch(QThread):
    ready = Signal(str, str)
    failed = Signal(str)
    notice = Signal(str)
    hang = False          # resta in attesa finché il test non lo sblocca
    outcome = "ok"        # "ok" | "fail" | "notice" (riserva usata, poi risultati)
    created: list = []

    def __init__(self, query, *_a, **_kw):
        super().__init__(_kw.get("parent"))
        self._query = query
        self.kwargs = _kw
        self._stopped = False
        FakeSearch.created.append(self)

    def stop(self):
        self._stopped = True

    def run(self):
        while FakeSearch.hang and not self._stopped:
            time.sleep(0.02)
        if self._stopped:
            return
        if FakeSearch.outcome == "fail":
            self.failed.emit("nessun risultato")
            return
        if FakeSearch.outcome == "notice":
            self.notice.emit("DuckDuckGo non ha risposto: risultati da SearXNG")
        self.ready.emit("RISULTATI WEB DI PROVA", self._query)


web_search.WebSearchWorker = FakeSearch

# il portachiavi non va toccato nei test: si contano solo le letture
keyring_reads: list[int] = []
secrets_store.load_api_key = lambda: keyring_reads.append(1) or "chiave-finta"

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


# il suggerimento «riattiva 🧠» della finestra legge think_off_sent() mentre
# riceve generation_finished: lo stato deve essere ancora quello del turno
think_flags: list[bool] = []
engine.generation_finished.connect(lambda *_a: think_flags.append(engine.think_off_sent()))


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
assert "".join(e[2] for e in events if e[0] == "text_chunk") == "Ciao, **mondo**"
assert [e[2] for e in events if e[0] == "think_chunk"] == ["ragiono"]
order = names()
assert order.index("user_message_added") < order.index("generation_started") < order.index("text_chunk")
assert order[-1] == "busy_changed" and events[-1][1] is False
assert payloads[-1]["messages"][0] == {"role": "system", "content": "sei un test"}
assert "think" not in payloads[-1]
assert think_flags[-1] is False

saved = config.load_chat(cid)
assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
assert saved["messages"][1]["content"] == "Ciao, **mondo**"
assert saved["messages"][1]["thinking"] == "ragiono"
assert saved["title"] == "ciao"
assert [c["id"] for c in engine.chats()] == [cid]
print("1. invio, streaming e salvataggio OK")

# ---------------------------------------------- 2. 🧠 spento e secondo turno

events.clear()
assert engine.send(cid, "di nuovo", "finto", think=False) == cid
assert wait_until(lambda: not engine.busy())
assert payloads[-1]["think"] is False
assert think_flags[-1] is True and engine.think_off_sent() is False   # azzerato a fine turno
assert [m["role"] for m in payloads[-1]["messages"]] == ["system", "user", "assistant", "user"]
assert len(engine.chat(cid)["messages"]) == 4
print("2. think=False e cronologia OK")

# --------------------------------------------- 3. stop con testo parziale

events.clear()
release.clear()
c3 = engine.send(None, "rispondi lento", "finto")
assert wait_until(lambda: "text_chunk" in names())
# regressione: close() dal thread principale attendeva il lock del buffer,
# tenuto dal worker fermo in lettura, fino all'arrivo di altri dati (UI bloccata)
t0 = time.monotonic()
engine.stop()
assert time.monotonic() - t0 < 1.0, "lo stop ha bloccato il thread principale"
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

# regressione 0.2.6: il messaggio compariva solo a ricerca finita (secondi
# con SearXNG). Ora va in chat e su disco subito, i risultati si agganciano
# a lui e solo allora parte la generazione.
events.clear()
FakeSearch.hang = True
n_payloads = len(payloads)
c6 = engine.send(None, "notizie di oggi", "finto", web=True)
order = names()
assert order.index("user_message_added") < order.index("search_started"), order
assert "generation_started" not in order
user = config.load_chat(c6)["messages"][0]
assert user["display"] == "notizie di oggi" and user["web"] and "web_block" not in user
assert engine.phase() == "search" and len(payloads) == n_payloads
FakeSearch.hang = False
assert wait_until(lambda: not engine.busy())
order = names()
assert order.count("user_message_added") == 1, order   # nessun doppione
assert order.index("search_started") < order.index("search_finished") < order.index("generation_started")
saved = config.load_chat(c6)["messages"]
assert saved[0]["web_block"] == "RISULTATI WEB DI PROVA"
assert [m["role"] for m in saved] == ["user", "assistant"]
assert "RISULTATI WEB DI PROVA" in payloads[-1]["messages"][-1]["content"]
print("6. ricerca web: messaggio subito, risultati poi OK")

# ------------------------- 6b. ricerca fallita, riserva e portachiavi

events.clear()
FakeSearch.outcome = "fail"
c6b = engine.send(None, "cerca invano", "finto", web=True)
assert wait_until(lambda: not engine.busy())
assert any("Ricerca web non riuscita" in e[2] for e in events if e[0] == "notice")
saved = config.load_chat(c6b)["messages"]
assert [m["role"] for m in saved] == ["user", "assistant"] and "web_block" not in saved[0]

# provider di riserva passato al worker, il suo avviso arriva come notice
events.clear()
FakeSearch.outcome = "notice"
settings.update(web_provider="duckduckgo", web_fallback="searxng")
c6c = engine.send(None, "cerca con riserva", "finto", web=True)
assert wait_until(lambda: not engine.busy())
assert FakeSearch.created[-1].kwargs.get("fallback") == "searxng"
assert ("notice", c6c, "🌐 DuckDuckGo non ha risposto: risultati da SearXNG") in events
FakeSearch.outcome = "ok"

# chiave API letta dal portachiavi solo se serve a ollama.com (principale o riserva)
for provider, fallback, letta in (("searxng", "", False), ("duckduckgo", "", False),
                                  ("ollama", "", True), ("searxng", "ollama", True)):
    keyring_reads.clear()
    settings.update(web_provider=provider, web_fallback=fallback)
    engine.send(None, "domanda", "finto", web=True)
    assert wait_until(lambda: not engine.busy())
    assert bool(keyring_reads) is letta, (provider, fallback)
    key = FakeSearch.created[-1].kwargs.get("api_key")
    assert key == ("chiave-finta" if letta else ""), (provider, fallback, key)
settings.update(web_provider="duckduckgo", web_fallback="")
print("6b. ricerca fallita, provider di riserva e portachiavi OK")

# --------------------------------------- 7. stop durante la ricerca web

events.clear()
FakeSearch.hang = True
c7 = engine.send(None, "cerca e aspetta", "finto", web=True)
assert engine.busy()
engine.stop()
FakeSearch.hang = False
assert not engine.busy()
assert [n for n in names() if n != "chats_changed"] == [
    "busy_changed", "user_message_added", "search_started", "search_finished",
    "busy_changed"], names()
# il messaggio resta in chat, senza risposta, come uno stop prima del testo
assert [m["role"] for m in config.load_chat(c7)["messages"]] == ["user"]
assert c7 in [c["id"] for c in engine.chats()]
assert wait_until(lambda: False, 200) is False
assert len(config.load_chat(c7)["messages"]) == 1   # nessuna generazione tardiva
print("7. stop durante la ricerca: il messaggio resta, nessuna risposta OK")

# ------------------------------------------- 8. avvisi, rinomina, eliminazione

events.clear()
c8 = engine.send(None, "", "finto", web=True,
                 attachments=[{"path": "/non/esiste.txt", "name": "esiste.txt", "kind": "text"}])
assert wait_until(lambda: not engine.busy())
notes = [e[2] for e in events if e[0] == "notice" and e[1] == c8]
assert any("file non leggibile" in n for n in notes)
assert any("Ricerca web saltata" in n for n in notes)
# dopo busy_changed(True): la companion azzera lì il backlog degli eventi
order = names()
assert order.index("busy_changed") < order.index("notice") < order.index("user_message_added"), order

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
fake.close()
assert not slot_errors, f"eccezioni negli slot: {slot_errors}"
print("ENGINE OK")

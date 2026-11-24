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

# i test confrontano i testi in italiano (la lingua sorgente), qualunque
# sia la lingua del sistema: anche nella prova dell'AppImage e in inglese
os.environ["OLLADESK_LANG"] = "it"

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

# archivio: la copia in memoria e il file restano allineati, una chat
# ripresa esce dall'archivio, l'archiviazione automatica guarda l'inattività
def archived(chat_id):
    return next(c for c in engine.chats() if c["id"] == chat_id)["archived"]


engine.set_archived(cid, True)
assert archived(cid) and engine.chat(cid)["archived"] is True
assert config.load_chat(cid)["archived"] is True
engine.send(cid, "di nuovo", "finto")
assert wait_until(lambda: not engine.busy())
assert not archived(cid) and "archived" not in config.load_chat(cid)
now = config.now()
assert engine.archive_idle(0) == 0
assert engine.archive_idle(30, now=now + 31 * 86400) == len(engine.chats())
assert all(c["archived"] for c in engine.chats())
assert engine.archive_idle(30, now=now + 31 * 86400) == 0   # già archiviate
for c in list(engine.chats()):
    engine.set_archived(c["id"], False)
assert not any(c["archived"] for c in engine.chats())
print("8a. archivio: flag, ripresa, archiviazione automatica OK")

# cartella come contesto: un solo allegato, espanso nel testo per Ollama
folder = os.path.join(tempfile.mkdtemp(), "progetto")
os.makedirs(os.path.join(folder, "src"))
with open(os.path.join(folder, "src", "main.py"), "w", encoding="utf-8") as fh:
    fh.write("print('dalla cartella')\n")
with open(os.path.join(folder, "foto.png"), "wb") as fh:
    fh.write(b"\x89PNG")
events.clear()
cf = engine.send(None, "cosa fa?", "finto",
                 attachments=[{"path": folder, "name": "progetto/", "kind": "folder"}])
assert wait_until(lambda: not engine.busy())
sent = payloads[-1]["messages"][-1]
assert "--- progetto/src/main.py ---\nprint('dalla cartella')" in sent["content"]
assert "images" not in sent   # le immagini della cartella non partono
saved = config.load_chat(cf)["messages"][0]
assert saved["attachments_meta"] == [{"path": folder, "name": "progetto/", "kind": "folder"}]
assert "dalla cartella" not in str(saved)   # nel file della chat solo il percorso
assert any("1 esclusi (immagini" in e[2] for e in events if e[0] == "notice" and e[1] == cf)
engine.delete_chat(cf)
assert os.path.isdir(folder)   # eliminare la chat non tocca la cartella
print("8b. cartella come contesto OK")

engine.rename_chat(cid, "  rinominata  ")
assert engine.chat(cid)["title"] == "rinominata"
assert config.load_chat(cid)["title"] == "rinominata"
assert next(c for c in engine.chats() if c["id"] == cid)["title"] == "rinominata"
engine.delete_chat(cid)
assert config.load_chat(cid) is None and engine.chat(cid) is None
assert cid not in [c["id"] for c in engine.chats()]
print("8. avvisi, rinomina ed eliminazione OK")

# ------------------------------------------------------- 9. ciclo dei tool

import threading  # noqa: E402

from olladesk import tools  # noqa: E402

tool_gate = threading.Event()   # trattiene il tool «lento»
tool_runs: list[dict] = []


def _eco(args):
    tool_runs.append(args)
    return "eco: " + args.get("testo", "")


def _rotto(_args):
    raise ValueError("disco sparito")


def _lento(_args):
    tool_gate.wait(10)
    return "arrivato tardi"


engine.tools = tools.ToolRegistry([
    tools.Tool("eco", "Ripete il testo", _eco,
               {"type": "object", "properties": {"testo": {"type": "string"}}}),
    tools.Tool("rotto", "Fallisce sempre", _rotto),
    tools.Tool("lento", "Aspetta", _lento),
])
activity: list[tuple] = []
engine.tool_activity.connect(lambda cid_, ev: activity.append((cid_, ev)))
# i tool partono solo con modelli che li supportano (capacità da /api/show)
fake.model_caps["finto"] = ["completion", "tools"]
engine._caps.clear()
engine.refresh_models()
assert wait_until(lambda: engine.tools_supported("finto"))
assert not engine.tools_supported("finto-due") and not engine.tools_supported("assente")

# 9a. senza tools=True la richiesta resta quella di sempre
engine.send(None, "usa lo strumento", "finto")
assert wait_until(lambda: not engine.busy())
assert "tools" not in payloads[-1] and not tool_runs

# 9b. un giro: chiamata, risultato al modello, risposta finale nella stessa bolla
events.clear()
activity.clear()
n0 = len(payloads)
c9 = engine.send(None, "usa lo strumento", "finto", tools=True)
assert wait_until(lambda: not engine.busy())
assert len(payloads) == n0 + 2, len(payloads) - n0
first, second = payloads[n0], payloads[n0 + 1]
assert [t["function"]["name"] for t in first["tools"]] == ["eco", "lento", "rotto"]
assert tool_runs == [{"testo": "ciao"}]
assert second["messages"][-2] == {
    "role": "assistant", "content": "Controllo. ",
    "tool_calls": [{"function": {"name": "eco", "arguments": {"testo": "ciao"}}}]}
# il risultato arriva al modello con i giri rimasti; salvato e mostrato senza
assert second["messages"][-1] == {"role": "tool", "tool_name": "eco",
                                  "content": "eco: ciao\n\n[tool round 1 of 8]"}
assert names().count("generation_started") == 1 and len(finished()) == 1
assert finished()[-1][1:3] == (c9, "done") and finished()[-1][3], finished()
assert "".join(e[2] for e in events if e[0] == "text_chunk") == "Controllo. Ho letto: eco: ciao"
assert [(a[0], a[1]["state"], a[1]["name"]) for a in activity] == [
    (c9, "started", "eco"), (c9, "done", "eco")]
assert activity[-1][1]["result"] == "eco: ciao" and activity[-1][1]["error"] is False
saved = config.load_chat(c9)["messages"]
assert [m["role"] for m in saved] == ["user", "assistant"]
assert saved[1]["content"] == "Controllo. Ho letto: eco: ciao"
assert saved[1]["tool_rounds"] == [{"content": "Controllo. ", "calls": [
    {"name": "eco", "arguments": {"testo": "ciao"}, "result": "eco: ciao", "error": False}]}]
assert engine.partial_tools() == []   # fuori da una risposta: nessuna chiamata
print("9b. un giro di tool nella stessa risposta, salvato OK")

# 9c. turno successivo: la cronologia ricostruisce chiamate e risultati
engine.send(c9, "e adesso?", "finto", tools=True)
assert wait_until(lambda: not engine.busy())
hist = payloads[-1]["messages"]
assert [m["role"] for m in hist] == ["system", "user", "assistant", "tool", "assistant", "user"]
assert hist[2]["content"] == "Controllo. " and hist[2]["tool_calls"][0]["function"]["name"] == "eco"
assert hist[3]["content"] == "eco: ciao" and hist[4]["content"] == "Ho letto: eco: ciao"

# i risultati restano interi solo nell'ultima risposta, prima si accorciano
lungo = "x" * (tools.OLD_RESULT_CHARS + 500)
chat9 = engine.chat(c9)
chat9["messages"][1]["tool_rounds"][0]["calls"][0]["result"] = lungo
chat9["messages"].append({"role": "assistant", "content": "ultima", "tool_rounds": [
    {"content": "", "calls": [{"name": "eco", "arguments": {}, "result": lungo}]}]})
chat9["messages"].append({"role": "user", "display": "domanda"})
built, _w = engine._build_messages(chat9)
tool_contents = [m["content"] for m in built if m["role"] == "tool"]
assert len(tool_contents[0]) < len(lungo) and "truncated" in tool_contents[0]
assert tool_contents[-1] == lungo
# giri malformati (file modificato a mano) non rompono la richiesta
chat9["messages"].insert(1, {"role": "assistant", "content": "a mano",
                             "tool_rounds": [None, {"calls": "rotto"}, {"calls": [{"x": 1}]}]})
built, _w = engine._build_messages(chat9)
assert {"role": "assistant", "content": "a mano"} in built
engine.chat(c9)["messages"][1:] = config.load_chat(c9)["messages"][1:]   # ripristina
print("9c. cronologia con i tool e risultati vecchi accorciati OK")

# 9d. tool che fallisce e tool inventato: errore al modello, l'app va avanti
for text, expect in (("strumento rotto", "Error: ValueError: disco sparito"),
                     ("strumento sconosciuto", "Error: unknown tool 'inesistente'")):
    engine.send(None, text, "finto", tools=True)
    assert wait_until(lambda: not engine.busy())
    assert payloads[-1]["messages"][-1]["content"].startswith(expect), payloads[-1]["messages"][-1]
assert "Available tools: eco, lento, rotto" in payloads[-1]["messages"][-1]["content"]
assert activity[-1][1]["error"] is True
print("9d. errori dei tool restituiti al modello OK")

# 9e. tetto dei giri: dopo MAX_ROUNDS la richiesta parte senza tool
events.clear()
n0 = len(payloads)
tool_runs.clear()
c9e = engine.send(None, "strumento infinito", "finto", tools=True)
assert wait_until(lambda: not engine.busy(), 10000)
sent = payloads[n0:]
assert len(sent) == tools.MAX_ROUNDS + 1, len(sent)
assert all("tools" in p for p in sent[:-1]) and "tools" not in sent[-1]
# la stessa chiamata non si riesegue: dal secondo giro il modello riceve un rimando
assert len(tool_runs) == 1, tool_runs
assert any("limite di 8 giri" in e[2] for e in events if e[0] == "notice")
assert finished()[-1][1:3] == (c9e, "done")
rounds9e = config.load_chat(c9e)["messages"][1]["tool_rounds"]
assert len(rounds9e) == tools.MAX_ROUNDS
assert rounds9e[0]["calls"][0]["result"] == "eco: ciao"
assert all(r["calls"][0]["result"].startswith("Same call as earlier in this answer (tool round 1)")
           and r["calls"][0]["error"] is False for r in rounds9e[1:])
# i giri rimasti, nell'ultimo messaggio tool di ogni richiesta
# all'ultima richiesta, senza tool, si chiede in chiaro la risposta finale
assert sent[-1]["messages"][-1] == {"role": "user", "content": tools.FINAL_ANSWER_REQUEST}
notes = [p["messages"][-1 if i < 7 else -2]["content"].rsplit("\n\n", 1)[-1]
         for i, p in enumerate(sent[1:])]
assert notes[0] == "[tool round 1 of 8]", notes
assert notes[5].startswith("[tool round 6 of 8: only 2 left.") and \
    notes[6].startswith("[tool round 7 of 8: only 1 left.")
assert notes[7].startswith("[tool round 8 of 8: no tool rounds left."), notes[7]
print("9e. tetto di 8 giri OK")

# 9e2. chiamata scritta come testo: tolta dalla risposta, con un avviso
events.clear()
c9x = engine.send(None, "risposta grezza", "finto")
assert wait_until(lambda: not engine.busy())
shown = "".join(e[2] for e in events if e[0] == "text_chunk")
assert shown == "Risposta. ", shown
assert config.load_chat(c9x)["messages"][-1]["content"] == "Risposta. "
assert any("strumento non disponibile" in e[2] for e in events if e[0] == "notice")
assert finished()[-1][1:3] == (c9x, "done")
events.clear()
engine.send(c9x, "di nuovo", "finto")   # lo stato non resta «trattenuto»
assert wait_until(lambda: not engine.busy())
assert "".join(e[2] for e in events if e[0] == "text_chunk")
print("9e2. chiamata grezza nascosta OK")

# 9f. stop mentre un tool lavora: nessuna richiesta dopo, giro incompleto scartato
events.clear()
tool_gate.clear()
n0 = len(payloads)
c9f = engine.send(None, "strumento lento", "finto", tools=True)
assert wait_until(lambda: any(a[1]["state"] == "started" and a[1]["name"] == "lento"
                              for a in activity[-1:]))
# T5: la vista del giro in corso (bolla riaperta, telefono) mostra il tool attivo
assert engine.partial_tools() == [{"name": "lento", "arguments": {"testo": "ciao"},
                                   "result": "", "error": False, "state": "running"}]
t0 = time.monotonic()
engine.stop()
assert time.monotonic() - t0 < 1.0, "lo stop ha atteso il tool"
assert not engine.busy() and finished()[-1][1:3] == (c9f, "stopped")
tool_gate.set()
assert wait_until(lambda: False, 300) is False   # il risultato tardivo arriva e si scarta
assert len(payloads) == n0 + 1, "richiesta partita dopo lo stop"
saved = config.load_chat(c9f)["messages"]
assert saved[-1]["content"] == "Controllo. " and "tool_rounds" not in saved[-1]
assert not any(a[1].get("result") == "arrivato tardi" for a in activity)
print("9f. stop durante un tool OK")

# 9g. interruttore per chat (T2): spento di default, salvato, None = scelta salvata
n0 = len(payloads)
c9g = engine.send(None, "usa lo strumento", "finto")            # chat nuova: spenti
assert wait_until(lambda: not engine.busy())
assert "tools" not in payloads[n0] and "tools" not in config.load_chat(c9g)
engine.set_chat_tools(c9g, True)                                # salvato subito
assert config.load_chat(c9g)["tools"] is True
n0 = len(payloads)
engine.send(c9g, "usa lo strumento", "finto")                   # None: vale la chat
assert wait_until(lambda: not engine.busy())
assert "tools" in payloads[n0]
n0 = len(payloads)
engine.send(c9g, "usa lo strumento", "finto-due", tools=True)   # modello senza tool
assert wait_until(lambda: not engine.busy())
assert "tools" not in payloads[n0] and config.load_chat(c9g)["tools"] is True
n0 = len(payloads)
engine.send(c9g, "usa lo strumento", "finto", tools=False)      # spento dall'invio
assert wait_until(lambda: not engine.busy())
assert "tools" not in payloads[n0] and "tools" not in config.load_chat(c9g)
engine.set_chat_tools("non-esiste", True)                       # nessun errore
print("9g. interruttore dei tool per chat OK")

# 9h. tool sui file (T3): offerti solo se la chat condivide cartelle o file,
# anche con il registro fisso vuoto; si leggono dal disco nel giro dei tool
from pathlib import Path  # noqa: E402

from olladesk import file_tools  # noqa: E402

engine.tools = tools.ToolRegistry()
shared_dir = Path(tempfile.mkdtemp()) / "condivisa"
shared_dir.mkdir()
(shared_dir / "nota.txt").write_text("contenuto della nota\n")
n0 = len(payloads)
c9h = engine.send(None, "ciao", "finto", tools=True)             # niente condiviso
assert wait_until(lambda: not engine.busy())
assert "tools" not in payloads[n0]
assert payloads[n0]["messages"][0] == {"role": "system", "content": "sei un test"}
n0 = len(payloads)
engine.send(c9h, "guarda la cartella", "finto", tools=True,
            attachments=[{"path": str(shared_dir), "name": "condivisa/", "kind": "folder"}])
assert wait_until(lambda: not engine.busy())
assert [t["function"]["name"] for t in payloads[n0]["tools"]] == [
    "list_files", "read_file", "search_text"]
# con qualcosa di condiviso il prompt di sistema dice che è in sola lettura
assert payloads[n0]["messages"][0] == {
    "role": "system", "content": "sei un test\n\n" + file_tools.READ_ONLY_NOTE}
n0 = len(payloads)
engine.send(c9h, "e adesso?", "finto")                           # la cartella resta
assert wait_until(lambda: not engine.busy())
assert "list_files" in str(payloads[n0]["tools"])
n0 = len(payloads)
engine.set_chat_tools(c9h, False)                                # anche a tool spenti
engine.send(c9h, "scrivi nel file", "finto")
assert wait_until(lambda: not engine.busy())
assert "tools" not in payloads[n0]
assert payloads[n0]["messages"][0]["content"].endswith(file_tools.READ_ONLY_NOTE)
engine.set_chat_tools(c9h, True)
reg = engine._tools_for(config.load_chat(c9h))
out, err = tools.run_call(reg, {"name": "read_file", "arguments": {"path": "condivisa/nota.txt"}})
assert not err and "contenuto della nota" in out, out
print("9h. tool sui file condivisi nella chat OK")

# 9h2. livelli dei tool (0.5, W1): lettura salvata come True (leggibile dalla
# 0.4), scrittura come stringa; con la scrittura i tool e la nota cambiano
from olladesk import write_tools  # noqa: E402

engine.set_chat_tools(c9h, "read")
assert config.load_chat(c9h)["tools"] is True
n0 = len(payloads)
engine.send(c9h, "correggi la nota", "finto", tools="write")
assert wait_until(lambda: not engine.busy())
assert config.load_chat(c9h)["tools"] == "write"
assert [t["function"]["name"] for t in payloads[n0]["tools"]] == [
    "edit_file", "list_files", "make_dir", "read_file", "search_text", "write_file"]
assert payloads[n0]["messages"][0]["content"] == "sei un test\n\n" + write_tools.WRITE_NOTE
assert "read-only" not in str(payloads[n0]["tools"])
n0 = len(payloads)
engine.send(c9h, "e con un modello senza tool?", "finto-due")    # None: vale la chat
assert wait_until(lambda: not engine.busy())
assert "tools" not in payloads[n0]
assert payloads[n0]["messages"][0]["content"].endswith(file_tools.READ_ONLY_NOTE)
engine.set_chat_tools(c9h, "full")
assert config.load_chat(c9h)["tools"] == "full"
engine.set_chat_tools(c9h, "boh")                                # valore ignoto = spento
assert "tools" not in config.load_chat(c9h)
engine.set_chat_tools(c9h, True)
print("9h2. livelli dei tool per chat OK")

# 9h3. conferma delle modifiche (0.5, W2): la risposta aspetta la scelta
# dell'utente; Applica, Rifiuta (e niente seconda richiesta identica),
# «consenti per il resto della risposta», Stop durante l'attesa
requests: list[tuple] = []
resolved: list[tuple] = []
engine.change_requested.connect(lambda cid, v: requests.append((cid, v)))
engine.change_resolved.connect(lambda cid, info: resolved.append((cid, info)))
nota = shared_dir / "nota.txt"
EDIT = {"name": "edit_file", "arguments": {"path": "condivisa/nota.txt",
                                           "old_text": "contenuto", "new_text": "testo"}}
engine.set_chat_tools(c9h, "write")

# Applica
fake.write_rounds = [[EDIT]]
requests.clear()
resolved.clear()
n0 = len(payloads)
engine.send(c9h, "modifica la nota", "finto")
assert wait_until(lambda: engine.pending_change() is not None)
view = engine.pending_change()
assert requests and requests[-1] == (c9h, view)
assert view["kind"] == "edit" and view["label"] == "condivisa/nota.txt", view
assert "-contenuto della nota" in view["diff"] and "+testo della nota" in view["diff"]
assert (view["added"], view["removed"]) == (1, 1)
assert engine.partial_tools()[-1]["state"] == "waiting"
assert any(a[1]["state"] == "waiting" for a in activity)
assert engine.busy() and nota.read_text() == "contenuto della nota\n"
assert wait_until(lambda: False, 200) is False and len(payloads) == n0 + 1   # nessun giro avanti
assert not engine.resolve_change(view["id"] + 1, "apply")      # richiesta sbagliata
assert not engine.resolve_change(view["id"], "boh")            # scelta ignota
assert engine.resolve_change(view["id"], "apply")
assert not engine.resolve_change(view["id"], "apply")          # già decisa
assert resolved[-1] == (c9h, {"id": view["id"], "decision": "apply"})
assert wait_until(lambda: not engine.busy())
assert nota.read_text() == "testo della nota\n"
saved = config.load_chat(c9h)["messages"][-1]
call = saved["tool_rounds"][0]["calls"][0]
assert not call["error"] and "waiting" not in call, call
# con la scrittura il tetto dei giri sale
assert payloads[n0 + 1]["messages"][-1]["content"].endswith(
    f"[tool round 1 of {tools.MAX_WRITE_ROUNDS}]")
print("9h3. conferma: Applica OK")

# Rifiuta: la stessa modifica rifatta nel giro dopo non si ripropone
nota.write_text("contenuto della nota\n")
fake.write_rounds = [[EDIT], [EDIT]]
requests.clear()
engine.send(c9h, "modifica di nuovo", "finto")
assert wait_until(lambda: engine.pending_change() is not None)
assert engine.resolve_change(engine.pending_change()["id"], "reject")
assert wait_until(lambda: not engine.busy())
assert len(requests) == 1, requests
calls = [c for r in config.load_chat(c9h)["messages"][-1]["tool_rounds"] for c in r["calls"]]
assert calls[0]["error"] and "rejected this change" in calls[0]["result"], calls[0]
assert calls[1]["error"] and "already rejected" in calls[1]["result"], calls[1]
assert nota.read_text() == "contenuto della nota\n"
print("9h3. conferma: Rifiuta e niente richiesta ripetuta OK")

# consenti per il resto della risposta: la seconda scrittura non chiede
fake.write_rounds = [
    [{"name": "write_file", "arguments": {"path": "condivisa/a.txt", "content": "a\n"}}],
    [{"name": "write_file", "arguments": {"path": "condivisa/b.txt", "content": "b\n"}}]]
requests.clear()
engine.send(c9h, "modifica: crea due file", "finto")
assert wait_until(lambda: engine.pending_change() is not None)
assert engine.pending_change()["kind"] == "create"
assert engine.resolve_change(engine.pending_change()["id"], "allow_rest")
assert wait_until(lambda: not engine.busy())
assert len(requests) == 1
assert (shared_dir / "a.txt").read_text() == "a\n" and (shared_dir / "b.txt").read_text() == "b\n"
# la risposta dopo chiede di nuovo
fake.write_rounds = [[{"name": "write_file", "arguments": {"path": "condivisa/c.txt",
                                                           "content": "c\n"}}]]
engine.send(c9h, "modifica ancora", "finto")
assert wait_until(lambda: engine.pending_change() is not None)
assert engine.resolve_change(engine.pending_change()["id"], "reject")
assert wait_until(lambda: not engine.busy())
assert len(requests) == 2 and not (shared_dir / "c.txt").exists()
print("9h3. conferma: consenti per il resto della risposta OK")

# Stop durante l'attesa: vale come rifiuto, nessun giro dopo, niente file
fake.write_rounds = [[{"name": "write_file", "arguments": {"path": "condivisa/d.txt",
                                                           "content": "d\n"}}]]
resolved.clear()
n0 = len(payloads)
engine.send(c9h, "modifica e fermati", "finto")
assert wait_until(lambda: engine.pending_change() is not None)
rid = engine.pending_change()["id"]
t0 = time.monotonic()
engine.stop()
assert time.monotonic() - t0 < 1.0, "lo stop ha atteso la conferma"
assert not engine.busy() and engine.pending_change() is None
assert resolved == [(c9h, {"id": rid, "decision": ""})], resolved
assert not engine.resolve_change(rid, "apply")
assert wait_until(lambda: False, 300) is False
assert len(payloads) == n0 + 1 and not (shared_dir / "d.txt").exists()
fake.write_rounds = []
engine.set_chat_tools(c9h, True)
print("9h3. conferma: Stop durante l'attesa OK")

# 9i. ricerca web come tool (T4): con 🌐 e i tool attivi niente ricerca prima
# della risposta, il modello chiama web_search; senza 🌐 il tool non c'è
searches: list[str] = []
search_gate = threading.Event()
search_gate.set()
search_broken = threading.Event()   # ricerca che fallisce sempre


class FakeSearcher:
    def __init__(self, *_a, **_kw):
        self.kwargs = _kw
        self.stopped = False

    def find(self, query):
        searches.append(query)
        search_gate.wait(10)
        if search_broken.is_set():
            raise web_search.WebSearchError("motori in timeout")
        if self.stopped:
            return [], None
        return [("Rame", "https://esempio.it/rame", "prezzo del rame")], None

    def stop(self):
        self.stopped = True
        search_gate.set()


web_search.Searcher = FakeSearcher
FakeSearch.created.clear()
n0 = len(payloads)
c9i = engine.send(None, "cerca con lo strumento", "finto", web=True, tools=True)
assert wait_until(lambda: not engine.busy())
assert not FakeSearch.created, "ricerca prima della risposta con i tool attivi"
assert [t["function"]["name"] for t in payloads[n0]["tools"]] == ["fetch_page", "web_search"]
assert searches == ["rame"]
# fetch_page può aprire solo le pagine uscite dalle ricerche di questa risposta
assert engine._page_urls == {"https://esempio.it/rame"}, engine._page_urls
# con la ricerca il modello riceve la data di oggi nel prompt di sistema
import datetime  # noqa: E402

assert payloads[n0]["messages"][0]["role"] == "system"
assert payloads[n0]["messages"][0]["content"].endswith(tools.today_note(datetime.date.today()))
assert payloads[n0 + 1]["messages"][-1]["tool_name"] == "web_search"
assert "esempio.it/rame" in payloads[n0 + 1]["messages"][-1]["content"]
msg = config.load_chat(c9i)["messages"]
assert msg[0]["web"] is True and "web_block" not in msg[0]
assert msg[1]["tool_rounds"][0]["calls"][0]["name"] == "web_search"
# senza 🌐: nessun tool di rete, e senza nulla di condiviso nessun tool
n0 = len(payloads)
engine.send(c9i, "cerca con lo strumento", "finto")
assert wait_until(lambda: not engine.busy())
assert "tools" not in payloads[n0] and searches == ["rame"]
# modello senza tool: torna la ricerca prima della risposta
FakeSearch.created.clear()
n0 = len(payloads)
engine.send(c9i, "cerca il rame", "finto-due", web=True)
assert wait_until(lambda: not engine.busy())
assert len(FakeSearch.created) == 1 and "tools" not in payloads[n0]
# stop durante la ricerca del tool: la connessione si chiude, niente risposta
search_gate.clear()
searches.clear()
engine.send(c9i, "cerca con lo strumento", "finto", web=True)
assert wait_until(lambda: searches == ["rame"])
engine.stop()
assert wait_until(lambda: not engine.busy()) and search_gate.is_set()
print("9i. ricerca web come tool OK")

# 9j. ricerca che non risponde: dopo 2 fallimenti i tool web escono dalla richiesta
search_broken.set()
searches.clear()
events.clear()
n0 = len(payloads)
c9j = engine.send(None, "cerca con lo strumento infinito", "finto", web=True, tools=True)
assert wait_until(lambda: not engine.busy(), 10000)
sent = payloads[n0:]
assert searches == ["rame", "rame"], searches        # niente terza ricerca
assert len(sent) == 3 and "tools" in sent[1] and "tools" not in sent[2], len(sent)
assert sent[2]["messages"][-1] == {"role": "user", "content": tools.WEB_UNAVAILABLE_REQUEST}
assert any("non risponde (2 tentativi" in e[2] for e in events if e[0] == "notice")
assert finished()[-1][1:3] == (c9j, "done")
assert len(config.load_chat(c9j)["messages"][1]["tool_rounds"]) == 2
search_broken.clear()
print("9j. ricerca guasta: tool web tolti OK")

# ---------------------------------------------------------------- chiusura

from olladesk.ollama_client import shutdown_workers  # noqa: E402

shutdown_workers(engine.shutdown())
assert engine.send(None, "dopo la chiusura", "finto") is None
wait_until(lambda: False, 200)   # consegna dei segnali in coda
fake.close()
assert not slot_errors, f"eccezioni negli slot: {slot_errors}"
print("ENGINE OK")

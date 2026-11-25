"""Finto server Ollama per i test offline (engine_test, companion_test).

Risponde a /api/version, /api/tags e /api/chat (NDJSON). La risposta di
/api/chat dipende dall'ultimo messaggio utente:
  «lento»   → un frammento, poi attende `release`
  «pensa»   → solo ragionamento, poi attende `release`
  «errore»  → un frammento, poi un errore
  altro     → ragionamento, «Ciao, mondo» e statistiche

Con i tool nella richiesta e «strumento» nell'ultimo messaggio utente, il
finto modello scrive «Controllo. » e chiama un tool: «sconosciuto» →
`inesistente`, «rotto» → `rotto`, «lento» → `lento`, «cerca» → `web_search` (query «rame»), altrimenti `eco`.
Dopo i risultati (ultimo messaggio `tool`) risponde «Ho letto: <risultato>»;
con «infinito» richiama `eco` finché la richiesta offre i tool.
Con «modifica» nell'ultimo messaggio utente e i tool nella richiesta, il giro
n-esimo della risposta chiama le chiamate di `write_rounds[n]` (lista di
giri, impostata dal test); finiti i giri risponde come sopra.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = ["finto", "finto-due"]


class FakeOllama:
    def __init__(self):
        self.payloads: list[dict] = []
        self.write_rounds: list[list[dict]] = []   # giri di chiamate per «modifica»
        self.release = threading.Event()   # sblocca le risposte «lente»
        self.models = list(MODELS)         # installati: /api/pull li aggiunge, /api/delete li toglie
        self.pull_release = threading.Event()   # sblocca un /api/pull in attesa
        self.pull_release.set()
        self.delete_release = threading.Event()   # sblocca un /api/delete in attesa
        self.delete_release.set()
        self.tags_release = threading.Event()   # trattiene /api/tags (elenco già letto)
        self.tags_release.set()
        self.deleted: list[str] = []
        self.capabilities = ["completion", "vision", "thinking"]   # risposta di /api/show
        self.model_caps: dict[str, list[str]] = {}   # capacità diverse per un modello
        self.shows: list[str] = []         # modelli chiesti a /api/show
        self.ps_ctx = 8192                 # finestra di contesto in /api/ps
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _json(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _line(self, obj):
                self.wfile.write((json.dumps(obj) + "\n").encode())
                self.wfile.flush()

            def do_GET(self):  # noqa: N802 (API http.server)
                if self.path == "/api/version":
                    self._json({"version": "0.0.0-finto"})
                elif self.path == "/api/tags":
                    tags = {"models": [
                        {"name": n, "details": {"parameter_size": "1B", "quantization_level": "Q4"}}
                        for n in fake.models
                    ]}
                    fake.tags_release.wait(10)   # risposta con l'elenco di prima
                    self._json(tags)
                elif self.path == "/api/ps":
                    # il modello dell'ultima richiesta, caricato con ps_ctx
                    name = fake.payloads[-1]["model"] if fake.payloads else ""
                    self._json({"models": [{"name": name, "model": name,
                                            "context_length": fake.ps_ctx}] if name else []})
                else:
                    self.send_error(404)

            def do_DELETE(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.delete_release.wait(10)
                if body.get("model") in fake.models:
                    fake.models.remove(body["model"])
                    fake.deleted.append(body["model"])
                    self._json({})
                else:
                    self.send_error(404)

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/show":
                    # capacità del modello (badge accanto al selettore)
                    fake.shows.append(body.get("model"))
                    caps = fake.model_caps.get(body.get("model"), fake.capabilities)
                    self._json({"capabilities": list(caps)})
                    return
                if self.path == "/api/pull":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.end_headers()
                    try:
                        if "errore" in body["model"]:
                            self._line({"error": "pull rifiutato"})
                            return
                        self._line({"status": "pulling manifest"})
                        self._line({"status": "pulling abc", "total": 100, "completed": 40})
                        fake.pull_release.wait(10)
                        if body["model"] not in fake.models:
                            fake.models.append(body["model"])
                        self._line({"status": "success"})
                    except OSError:
                        pass
                    return
                fake.payloads.append(body)
                last = body["messages"][-1]["content"]
                last_role = body["messages"][-1]["role"]
                user = next((m["content"] for m in reversed(body["messages"])
                             if m["role"] == "user"), "")
                offers = "tools" in body
                if "troppolunga" in user:
                    # come Ollama 0.40: la richiesta oltre la finestra si rifiuta
                    data = json.dumps({"error": "request (16639 tokens) exceeds the available "
                                       "context size (16384 tokens), try increasing it"}).encode()
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                done_rounds = 0
                for m in reversed(body["messages"]):
                    if m["role"] == "user":
                        break
                    if m["role"] == "assistant" and m.get("tool_calls"):
                        done_rounds += 1
                try:
                    if "modifica" in user and offers and done_rounds < len(fake.write_rounds):
                        self._line({"message": {"content": "", "tool_calls": [
                            {"function": c} for c in fake.write_rounds[done_rounds]]}})
                        self._line({"done": True})
                    elif "strumento" in user and offers and (
                            last_role == "user" or "infinito" in user):
                        name = ("inesistente" if "sconosciuto" in user else
                                "rotto" if "rotto" in user else
                                "lento" if "lento" in user else
                                "web_search" if "cerca" in user else "eco")
                        args = {"query": "rame"} if name == "web_search" else {"testo": "ciao"}
                        self._line({"message": {"content": "Controllo. "}})
                        self._line({"message": {"content": "", "tool_calls": [
                            {"function": {"name": name, "arguments": args}}]}})
                        self._line({"done": True})
                    elif "grezza" in user and last_role == "user":
                        # chiamata scritta come testo (formato nativo non riconosciuto)
                        self._line({"message": {"content": "Risposta. "}})
                        self._line({"message": {"content": "<|tool_call_start|>"}})
                        self._line({"message": {"content": "[web_fetch(url='x')]"}})
                        self._line({"message": {"content": "<|tool_call_end|>"}})
                        self._line({"done": True})
                    elif last_role == "tool":
                        # senza la riga dei giri rimasti («[tool round …]»)
                        last = last.split("\n\n[tool round ")[0]
                        self._line({"message": {"content": "Ho letto: " + last}})
                        self._line({"done": True, "eval_count": 5,
                                    "eval_duration": 1_000_000_000})
                    elif "lento" in last:
                        self._line({"message": {"content": "parziale "}})
                        fake.release.wait(10)
                        self._line({"message": {"content": "e finale"}})
                        self._line({"done": True})
                    elif "vuota" in last:
                        # template che chiude subito la risposta: niente testo
                        if "ragionata" in last:
                            self._line({"message": {"thinking": "ci penso"}})
                        self._line({"done": True, "eval_count": 1})
                    elif "pensa" in last:
                        self._line({"message": {"thinking": "sto pensando"}})
                        fake.release.wait(10)
                    elif "errore" in last:
                        self._line({"message": {"content": "mezza risposta"}})
                        self._line({"error": "modello esploso"})
                    else:
                        self._line({"message": {"thinking": "ragiono"}})
                        self._line({"message": {"content": "Ciao, "}})
                        self._line({"message": {"content": "**mondo**"}})
                        self._line({"done": True, "eval_count": 10,
                                    "eval_duration": 1_000_000_000})
                except OSError:
                    pass   # connessione chiusa dallo stop

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.host = f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self):
        self.release.set()
        self.pull_release.set()
        self.delete_release.set()
        self.tags_release.set()
        self._server.shutdown()

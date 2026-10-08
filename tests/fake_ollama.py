"""Finto server Ollama per i test offline (engine_test, companion_test).

Risponde a /api/version, /api/tags e /api/chat (NDJSON). La risposta di
/api/chat dipende dall'ultimo messaggio utente:
  «lento»   → un frammento, poi attende `release`
  «pensa»   → solo ragionamento, poi attende `release`
  «errore»  → un frammento, poi un errore
  altro     → ragionamento, «Ciao, mondo» e statistiche
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = ["finto", "finto-due"]


class FakeOllama:
    def __init__(self):
        self.payloads: list[dict] = []
        self.release = threading.Event()   # sblocca le risposte «lente»
        self.models = list(MODELS)         # installati: /api/pull li aggiunge, /api/delete li toglie
        self.pull_release = threading.Event()   # sblocca un /api/pull in attesa
        self.pull_release.set()
        self.deleted: list[str] = []
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
                    self._json({"models": [
                        {"name": n, "details": {"parameter_size": "1B", "quantization_level": "Q4"}}
                        for n in fake.models
                    ]})
                else:
                    self.send_error(404)

            def do_DELETE(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if body.get("model") in fake.models:
                    fake.models.remove(body["model"])
                    fake.deleted.append(body["model"])
                    self._json({})
                else:
                    self.send_error(404)

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
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
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                try:
                    if "lento" in last:
                        self._line({"message": {"content": "parziale "}})
                        fake.release.wait(10)
                        self._line({"message": {"content": "e finale"}})
                        self._line({"done": True})
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
        self._server.shutdown()

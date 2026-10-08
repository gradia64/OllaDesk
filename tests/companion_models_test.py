"""Test della gestione dei modelli dal telefono (0.3.1): finto Ollama.

Copre scarica (avanzamento, esito, errore, annulla), elimina (modello
inesistente, durante una risposta, durante un'altra operazione), validazione
dei nomi e autenticazione.

Uso:  python3 tests/companion_models_test.py   (nessuna rete, nessun Ollama)
"""
import os
import sys
import tempfile
import time
import traceback

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="olladesk_models_config_")

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

import companion_client as cc  # noqa: E402

cc.setup(srv.port, wait_until)
TOKEN = cc.pair(srv.auth)


def info():
    st, d = cc.get_json("/api/models", TOKEN)
    assert st == 200, d
    return d


def op(name, body=None):
    return cc.post_json("/api/models/" + name, body or {}, TOKEN)


# 0. autenticazione e validazione
st, _h, _b = cc.request("POST", "/api/models/pull", {"name": "x:1"})
assert st == 401, st
st, _h, _b = cc.request("POST", "/api/models/delete", {"name": "finto"})
assert st == 401, st
for bad in ("", "../x", "a b", "-x", "x;rm", "a" * 201, 5, None):
    st, res = op("pull", {"name": bad})
    assert st == 400, (bad, st, res)
    st, res = op("delete", {"name": bad})
    assert st == 400, (bad, st, res)
assert info()["task"] is None
assert all("size" in m for m in info()["models"])
print("0. autenticazione e nomi non validi OK")

# 1. scaricamento con avanzamento
fake.pull_release.clear()
st, res = op("pull", {"name": "nuovo:1b"})
assert st == 200, res
assert wait_until(lambda: (info()["task"] or {}).get("pct") == 40), info()["task"]
t = info()["task"]
assert t["op"] == "pull" and t["state"] == "running" and t["name"] == "nuovo:1b", t
# una sola operazione alla volta
st, res = op("pull", {"name": "altro:1b"})
assert st == 409, (st, res)
st, res = op("delete", {"name": "finto"})
assert st == 409, (st, res)
fake.pull_release.set()
assert wait_until(lambda: (info()["task"] or {}).get("state") == "done"), info()["task"]
assert wait_until(lambda: "nuovo:1b" in [m["name"] for m in info()["models"]]), info()["models"]
assert "nuovo:1b" in engine.model_names()
assert info()["task"]["ended"] > 0
print("1. scaricamento, avanzamento e esito OK")

# 2. errore del server
st, res = op("pull", {"name": "errore:1"})
assert st == 200, res
assert wait_until(lambda: (info()["task"] or {}).get("state") == "failed"), info()["task"]
assert "pull rifiutato" in info()["task"]["error"], info()["task"]
print("2. scaricamento fallito OK")

# 3. annulla
fake.pull_release.clear()
assert op("cancel")[0] == 409, "niente da annullare"
st, res = op("pull", {"name": "lungo:7b"})
assert st == 200, res
assert wait_until(lambda: (info()["task"] or {}).get("pct") == 40)
st, res = op("cancel")
assert st == 200, res
t = info()["task"]
assert t["state"] == "failed" and "annullato" in t["error"], t
fake.pull_release.set()
wait_until(lambda: False, 300)
assert info()["task"]["state"] == "failed", "segnale tardivo di un worker annullato"
assert "lungo:7b" not in engine.model_names()
# dopo l'annullamento se ne può avviare un altro
st, res = op("pull", {"name": "dopo:1b"})
assert st == 200, res
assert wait_until(lambda: (info()["task"] or {}).get("state") == "done"), info()["task"]
print("3. annulla e nuovo scaricamento OK")

# 4. elimina
st, res = op("delete", {"name": "non-esiste:1"})
assert st == 404, (st, res)
st, res = op("delete", {"name": "nuovo:1b"})
assert st == 200, res
assert wait_until(lambda: (info()["task"] or {}).get("state") == "done"), info()["task"]
assert info()["task"]["op"] == "delete"
assert wait_until(lambda: "nuovo:1b" not in engine.model_names())
assert fake.deleted == ["nuovo:1b"], fake.deleted
print("4. eliminazione OK")

# 5. non si elimina mentre il PC risponde
st, res = cc.post_json("/api/send", {"text": "lento", "model": "finto"}, TOKEN)
assert st == 200, res
assert wait_until(engine.busy)
st, res = op("delete", {"name": "dopo:1b"})
assert st == 409 and "rispondendo" in res["error"], (st, res)
assert info()["busy"] is True
fake.release.set()
assert wait_until(lambda: not engine.busy())
assert "dopo:1b" in engine.model_names()
print("5. eliminazione rifiutata durante una risposta OK")

# 6. eliminazione che fallisce lato server (modello già sparito)
fake.models.remove("dopo:1b")
st, res = op("delete", {"name": "dopo:1b"})
assert st == 200, res
assert wait_until(lambda: (info()["task"] or {}).get("state") == "failed"), info()["task"]
assert wait_until(lambda: not engine.model_task() or engine.model_task()["state"] == "failed")
print("6. eliminazione non riuscita OK")

srv.stop()
wait_until(lambda: False, 200)
fake.close()
assert not slot_errors, f"eccezioni negli slot: {slot_errors}"
print("MODELLI OK")

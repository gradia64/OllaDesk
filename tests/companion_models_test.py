"""Test della gestione dei modelli dal telefono (0.3.1): finto Ollama.

Copre scarica (avanzamento, esito, errore, annulla), elimina (modello
inesistente, durante una risposta, durante un'altra operazione), validazione
dei nomi e autenticazione. Dalla 0.3.2: invio rifiutato con il modello in
eliminazione e gestore modelli del PC coordinato con il telefono.

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

# 7. niente invio con il modello in eliminazione (revisione 0.3.1: la DELETE
# in corso non rende il PC occupato, e l'invio partiva)
fake.models.append("da-togliere:1b")
engine.refresh_models()
assert wait_until(lambda: "da-togliere:1b" in engine.model_names())
fake.delete_release.clear()
st, res = op("delete", {"name": "da-togliere:1b"})
assert st == 200, res
assert engine.send_refusal("da-togliere:1b") and engine.send_refusal("finto") is None
st, res = cc.post_json("/api/send", {"text": "ciao", "model": "da-togliere:1b"}, TOKEN)
assert st == 409 and "eliminazione" in res["error"], (st, res)
assert engine.send(None, "ciao", "da-togliere:1b") is None and not engine.busy()
# un altro modello si usa normalmente
st, res = cc.post_json("/api/send", {"text": "ciao", "model": "finto"}, TOKEN)
assert st == 200, res
assert wait_until(lambda: not engine.busy())
fake.delete_release.set()
assert wait_until(lambda: (info()["task"] or {}).get("state") == "done"), info()["task"]
assert engine.send_refusal("da-togliere:1b") is None
print("7. invio rifiutato con il modello in eliminazione OK")

# 8. gestore modelli del PC: stesso stato del telefono (revisione 0.3.1:
# usava worker propri, e le regole valevano solo per il telefono)
from olladesk.widgets.model_manager import ModelManagerDialog  # noqa: E402

fake.pull_release.clear()
st, res = op("pull", {"name": "dal-telefono:1b"})
assert st == 200, res
assert wait_until(lambda: (info()["task"] or {}).get("pct") == 40)
dlg = ModelManagerDialog(engine)
assert wait_until(lambda: dlg.tree.topLevelItemCount() > 0)
# operazione del telefono già in corso: visibile, e niente seconda operazione
assert dlg.progress.isVisibleTo(dlg) and "dal-telefono:1b" in dlg.progress_label.text()
assert dlg.cancel_btn.isVisibleTo(dlg)
assert not dlg.pull_btn.isEnabled() and not dlg.delete_btn.isEnabled()
assert engine.pull_model("altro:1b") is not None
# «Annulla» del PC ferma lo scaricamento del telefono
dlg.cancel_btn.click()
assert info()["task"]["state"] == "failed" and "annullato" in info()["task"]["error"]
assert "annullato" in dlg.progress_label.text() and dlg.pull_btn.isEnabled()
fake.pull_release.set()
# scaricamento dal PC: il telefono lo vede
dlg.name_edit.setText("dal-pc:1b")
fake.pull_release.clear()
dlg._start_pull()
assert wait_until(lambda: (info()["task"] or {}).get("pct") == 40), info()["task"]
assert info()["task"]["name"] == "dal-pc:1b"
st, res = op("pull", {"name": "altro:1b"})
assert st == 409, (st, res)
fake.pull_release.set()
assert wait_until(lambda: "dal-pc:1b" in engine.model_names())
assert wait_until(lambda: dlg.changed and "completato" in dlg.progress_label.text())
assert wait_until(lambda: any(dlg.tree.topLevelItem(i).data(0, 0x0100) == "dal-pc:1b"
                              for i in range(dlg.tree.topLevelItemCount())))
# eliminazione dal PC: rifiutata mentre il PC risponde, anche se avviata dal telefono
fake.release.clear()
st, res = cc.post_json("/api/send", {"text": "lento", "model": "finto"}, TOKEN)
assert st == 200, res
assert wait_until(engine.busy)
dlg.tree.setCurrentItem(next(dlg.tree.topLevelItem(i) for i in range(dlg.tree.topLevelItemCount())
                             if dlg.tree.topLevelItem(i).data(0, 0x0100) == "dal-pc:1b"))
assert not dlg.delete_btn.isEnabled()
assert engine.delete_model("dal-pc:1b") is not None
fake.release.set()
assert wait_until(lambda: not engine.busy())
assert dlg.delete_btn.isEnabled()
# chiudere il dialogo non annulla uno scaricamento: continua nel motore
fake.pull_release.clear()
dlg.name_edit.setText("in-fondo:1b")
dlg._start_pull()
assert wait_until(lambda: (info()["task"] or {}).get("pct") == 40)
dlg.reject()
dlg.deleteLater()
wait_until(lambda: False, 100)
assert info()["task"]["state"] == "running"
fake.pull_release.set()
assert wait_until(lambda: "in-fondo:1b" in engine.model_names())
print("8. gestore modelli del PC coordinato con il telefono OK")

# 9. aggiornamento chiesto mentre un elenco è in volo: si rifà alla fine
# (revisione 0.3.2: veniva ignorato e l'elenco restava quello di prima)
fake.tags_release.clear()
dlg = ModelManagerDialog(engine)          # /api/tags in volo con l'elenco di adesso
assert wait_until(lambda: dlg._list_worker is not None)
wait_until(lambda: False, 200)            # la richiesta ha già letto l'elenco
fake.models.append("tardivo:1b")
dlg.refresh_models()                      # come a fine operazione
fake.tags_release.set()


def rows(d):
    return [d.tree.topLevelItem(i).data(0, 0x0100) for i in range(d.tree.topLevelItemCount())]


assert wait_until(lambda: "tardivo:1b" in rows(dlg)), rows(dlg)
assert wait_until(lambda: dlg._list_worker is None) and not dlg._refresh_again
# a dialogo chiuso non parte nessun nuovo elenco
fake.tags_release.clear()
dlg.refresh_models()
assert wait_until(lambda: dlg._list_worker is not None)
dlg.refresh_models()                      # rinviato alla fine di quello in volo
in_flight = dlg._list_worker
dlg.reject()
fake.tags_release.set()
wait_until(lambda: False, 300)
assert dlg._list_worker in (None, in_flight), "nuovo elenco dopo la chiusura"
dlg.deleteLater()
wait_until(lambda: False, 100)
print("9. elenco del gestore aggiornato anche con una richiesta in volo OK")

srv.stop()
wait_until(lambda: False, 200)
fake.close()
assert not slot_errors, f"eccezioni negli slot: {slot_errors}"
print("MODELLI OK")

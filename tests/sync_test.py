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

# i test confrontano i testi in italiano (la lingua sorgente), qualunque
# sia la lingua del sistema: anche nella prova dell'AppImage e in inglese
os.environ["OLLADESK_LANG"] = "it"

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
# solo «finto-due» sa usare i tool: serve al pulsante degli strumenti (7)
fake.model_caps["finto-due"] = ["completion", "tools"]


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
# niente controlli di rete (vedi companion_send_test: il worker della release
# di Ollama, ancora vivo all'uscita, faceva abortire il processo)
s.update(host=fake.host, companion=True, companion_port=PORT,
         app_update_check=False, ollama_update_check=False, tray_icon=False)
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
# all'avvio nessun modello è scelto: il selettore mostra il segnaposto
assert win.model_combo.isEnabled() and win.model_combo.count() == len(MODELS)
assert win.model_combo.currentIndex() == -1 and win.current_model() is None
assert win.model_combo.placeholderText() == "Seleziona il modello"
# le capacità arrivano per tutti i modelli (servono a filtrare il
# selettore), ma senza modello scelto non c'è nessun badge
assert wait_until(lambda: sorted(fake.shows) == sorted(MODELS)), fake.shows
assert all(b.isHidden() for b in win.cap_badges.values())
# l'invio senza modello è rifiutato con un avviso, senza creare chat
n_chats = len(win.engine.chats())
notes = []
real_note = win.chat_area.add_system_note
win.chat_area.add_system_note = notes.append
win.chat_area.input.setPlainText("senza modello")
win.chat_area._emit_send()
win.chat_area.add_system_note = real_note
assert len(notes) == 1 and notes[0].startswith("Nessun modello selezionato"), notes
assert not win.engine.busy() and win._view_id is None
assert len(win.engine.chats()) == n_chats
assert win.chat_area.input.toPlainText() == "senza modello"
win.chat_area.input.clear()
# ricaricare l'elenco non sceglie un modello da solo
win._set_models(win.engine.model_names())
assert win.model_combo.currentIndex() == -1
win.model_combo.setCurrentIndex(win.model_combo.findData(MODELS[0]))
assert win.current_model() == MODELS[0]
# ricaricare l'elenco tiene il modello scelto
win._set_models(win.engine.model_names())
assert win.current_model() == MODELS[0]
# capacità del modello scelto da /api/show: badge accanto al selettore
assert wait_until(lambda: not win.cap_badges["vision"].isHidden())
assert not win.cap_badges["thinking"].isHidden() and win.cap_badges["tools"].isHidden()
assert win.model_combo.currentData() in fake.shows
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
assert win.chat_area.send_btn.property("mode") == "send"
assert win.sidebar.list.isEnabled() and win.sidebar.new_btn.isEnabled()
n = bubbles()
win.chat_area.input.setPlainText("non deve partire")
win.chat_area._emit_send()
assert bubbles() == n + 1, "manca l'avviso «sta già rispondendo»"
assert win.chat_area.input.toPlainText() == "non deve partire"
assert engine.active_chat_id() == "chat_b"

# nuova chat durante la generazione: consentito
win._new_chat()
assert win._view_id is None and win.chat_area.send_btn.property("mode") == "send"
# la nuova chat non eredita il modello di quella vista prima
assert win.model_combo.currentIndex() == -1 and win.current_model() is None
assert all(b.isHidden() for b in win.cap_badges.values())
win._set_models(win.engine.model_names())   # ↻: resta sul segnaposto
assert win.current_model() is None

# riaprendo B la risposta in corso riappare con il testo già ricevuto
win._open_chat("chat_b")
assert win.chat_area.send_btn.property("mode") == "stop"
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
assert win.chat_area.send_btn.property("mode") == "send"
fake.release.set()
print("3. PC libero di navigare durante la risposta del telefono OK")

# ------- 4. il PC lascia la sua chat durante la risposta e la ritrova

fake.release.clear()
win._open_chat("chat_a")
win.chat_area.input.setPlainText("rispondi lento")
win.chat_area._emit_send()
assert wait_until(lambda: engine.partial()[0] == "parziale ")
win._open_chat("chat_b")
assert win.chat_area.send_btn.property("mode") == "send"
fake.release.set()
assert wait_until(lambda: not engine.busy())
assert config.load_chat("chat_a")["messages"][-1]["content"] == "parziale e finale"
win._open_chat("chat_a")
assert win.current_chat["messages"][-1]["content"] == "parziale e finale"
assert win.chat_area._stream_widget is None and win.chat_area.send_btn.property("mode") == "send"
print("4. risposta del PC completata in background OK")

# ------------------------------- 5. rinomina ed elimina dal telefono

for body, status in (({"chat_id": "chat_a", "title": "   "}, 400),
                     ({"chat_id": "chat_a", "title": "x" * 201}, 400),
                     ({"chat_id": "nessuna", "title": "ok"}, 404),
                     ({"chat_id": "../x", "title": "ok"}, 400)):
    assert h.post_json("/api/rename", body, TOKEN)[0] == status, body
st, _d = h.post_json("/api/rename", {"chat_id": "chat_a", "title": "  Nuovo titolo  "}, TOKEN)
assert st == 200
assert config.load_chat("chat_a")["title"] == "Nuovo titolo"
assert wait_until(lambda: any(
    win.sidebar.list.item(i).toolTip() == "Nuovo titolo" or "Nuovo titolo" in win.sidebar.list.item(i).text()
    for i in range(win.sidebar.list.count())))

# il PC guarda B, il telefono la elimina mentre risponde: risposta
# fermata, file cancellato, finestra tornata alla pagina iniziale
fake.release.clear()
win._open_chat("chat_b")
win.chat_area.input.setPlainText("rispondi lento")
win.chat_area._emit_send()
assert wait_until(lambda: engine.partial()[0] == "parziale ")
st, _d = h.post_json("/api/delete", {"chat_id": "chat_b"}, TOKEN)
assert st == 200
assert not engine.busy() and config.load_chat("chat_b") is None
assert win._view_id is None and win.current_chat is None
assert win.chat_area.send_btn.property("mode") == "send"
assert "chat_b" not in [c["id"] for c in engine.chats()]
assert h.post_json("/api/delete", {"chat_id": "chat_b"}, TOKEN)[0] == 404
fake.release.set()
print("5. rinomina ed eliminazione dal telefono, PC aggiornato OK")

# ------------------------------- 6. archivio dal telefono

for body, status in (({"chat_id": "chat_a"}, 400),
                     ({"chat_id": "chat_a", "archived": "sì"}, 400),
                     ({"chat_id": "nessuna", "archived": True}, 404)):
    assert h.post_json("/api/archive", body, TOKEN)[0] == status, body


def sidebar_ids():
    return [d for i in range(win.sidebar.list.count()) if (d := win.sidebar.list.item(i).data(256))]


assert "chat_a" in sidebar_ids() and win.sidebar.archive_btn.isHidden()
assert h.post_json("/api/archive", {"chat_id": "chat_a", "archived": True}, TOKEN)[0] == 200
assert config.load_chat("chat_a")["archived"] is True
assert wait_until(lambda: "chat_a" not in sidebar_ids())
assert not win.sidebar.archive_btn.isHidden()
st, data = h.get_json("/api/chats", TOKEN)
assert st == 200 and next(c for c in data["chats"] if c["id"] == "chat_a")["archived"] is True
st, data = h.get_json("/api/chats/chat_a", TOKEN)
assert st == 200 and data["archived"] is True
# vista «Archivio» sul PC, poi ripristino dal telefono
win.sidebar.archive_btn.click()
assert win.sidebar.showing_archive() and sidebar_ids() == ["chat_a"]
assert h.post_json("/api/archive", {"chat_id": "chat_a", "archived": False}, TOKEN)[0] == 200
assert wait_until(lambda: not win.sidebar.showing_archive() and "chat_a" in sidebar_ids())
assert "archived" not in config.load_chat("chat_a")
print("6. archivio dal telefono, PC aggiornato OK")

# ------------------------------- 7. livello degli strumenti (T2, 0.5 W1)

from olladesk import tools as tools_mod  # noqa: E402

btn = win.chat_area.tools_btn
level = win.chat_area.tools_level
pick = lambda lv: win.chat_area._tools_actions[lv].trigger()  # noqa: E731 — voce del menu 🔧
win._new_chat()
# il pulsante dipende solo dal modello: i tool sui file condivisi ci sono sempre
engine.tools = tools_mod.ToolRegistry([tools_mod.Tool("eco", "Ripete", lambda a: "eco")])
win.model_combo.setCurrentIndex(win.model_combo.findData("finto"))
assert btn.isHidden() and level() == "off"           # modello senza tool: niente pulsante
win.model_combo.setCurrentIndex(win.model_combo.findData("finto-due"))
assert wait_until(lambda: not btn.isHidden()) and level() == "off"   # spento di default
assert btn.menu() is not None and list(win.chat_area._tools_actions) == [
    "off", "read", "write", "full"]
pick("read")
n0 = len(fake.payloads)
win.chat_area.input.setPlainText("usa lo strumento")
win.chat_area._emit_send()
assert wait_until(lambda: not engine.busy())
cid7 = win._view_id
assert "tools" in fake.payloads[n0] and fake.payloads[n0]["model"] == "finto-due"
assert config.load_chat(cid7)["tools"] is True


def last_answer():
    row = win.chat_area.msgs.itemAt(bubbles()).widget()
    return win.chat_area._row_widget(row)


# T5: la bolla mostra le chiamate, richiuse; riaprendo la chat restano
ans = last_answer()
assert not ans.tools_btn.isHidden() and ans.tools_label.isHidden()
assert "Strumenti usati (1)" in ans.tools_btn.text(), ans.tools_btn.text()
ans.tools_btn.click()
assert not ans.tools_label.isHidden() and "✓ eco(testo=ciao)" in ans.tools_label.text()
win._open_chat("chat_a")
win._open_chat(cid7)
assert "Strumenti usati (1)" in last_answer().tools_btn.text()
# il telefono le vede nella conversazione (solo testo, già ridotte)
st, snap = h.get_json(f"/api/chats/{cid7}", TOKEN)
assert st == 200 and snap["messages"][-1]["tools"] == [
    {"name": "eco", "args": "testo=ciao", "result": "eco", "error": False, "running": False,
     "waiting": False, "outcome": ""}]
# stesso chat con un modello senza tool: pulsante nascosto, scelta salvata, niente tool
win.model_combo.setCurrentIndex(win.model_combo.findData("finto"))
assert btn.isHidden() and level() == "read"
win.chat_area.input.setPlainText("ciao senza")
win.chat_area._emit_send()
assert wait_until(lambda: not engine.busy())
assert "tools" not in fake.payloads[-1] and config.load_chat(cid7)["tools"] is True
# la scelta è della conversazione: un'altra chat ha la sua
win._open_chat("chat_a")
assert level() == "off"
win._open_chat(cid7)
assert level() == "read"
# dal telefono (senza interruttore) vale la scelta salvata nella chat
st, snap = h.get_json(f"/api/chats/{cid7}", TOKEN)
phone = h.SSE(f"/api/chats/{cid7}/events?after={snap['seq']}", TOKEN)
assert wait_until(lambda: phone.of("busy"))
n0 = len(fake.payloads)
st, res = h.post_json("/api/send", {"text": "usa lo strumento", "model": "finto-due",
                                    "chat_id": cid7}, TOKEN)
assert st == 200, res
assert wait_until(lambda: not engine.busy() and len(fake.payloads) > n0)
assert "tools" in fake.payloads[n0]
# T5 in diretta: le chiamate arrivano con la risposta e nel «done»
assert wait_until(lambda: "done" in phone.kinds())
assert any(a.get("tools") for a in phone.of("answer")), phone.of("answer")
assert phone.of("done")[-1]["tools"][0]["name"] == "eco"
phone.close()
# cambiarlo in una chat salvata vale subito, anche senza inviare
win.model_combo.setCurrentIndex(win.model_combo.findData("finto-due"))
pick("write")
assert config.load_chat(cid7)["tools"] == "write" and "Scrivere file" in btn.toolTip()
win._open_chat("chat_a")
win._open_chat(cid7)
assert level() == "write" and win.chat_area._tools_actions["write"].isChecked()
pick("off")
assert "tools" not in config.load_chat(cid7)
win._new_chat()
assert level() == "off"
print("7. livello degli strumenti per chat OK")

# ------------------------- 8. conferma delle modifiche nella bolla (0.5 W2)

from pathlib import Path  # noqa: E402

folder = Path(tempfile.mkdtemp()) / "progetto"
folder.mkdir()
(folder / "nota.txt").write_text("prima riga\n")
fake.write_rounds = [[{"name": "edit_file", "arguments": {
    "path": "progetto/nota.txt", "old_text": "prima", "new_text": "nuova"}}]]
win.model_combo.setCurrentIndex(win.model_combo.findData("finto-due"))
cid8 = engine.new_chat_id()
win._view_id = cid8
engine.send(cid8, "modifica la nota", "finto-due", tools="write",
            attachments=[{"path": str(folder), "name": "progetto/", "kind": "folder"}])
assert wait_until(lambda: engine.pending_change() is not None)
win._open_chat(cid8)                              # la chat nasce col primo messaggio


def card():
    w = win.chat_area._stream_widget
    return w.change_card if w is not None else None


assert wait_until(lambda: card() is not None)
assert "progetto/nota.txt" in card().title.text() and "+nuova riga" in card().diff.toPlainText()
assert "attende la tua conferma" in win.chat_area._stream_widget.tools_btn.text()
# il telefono vede l'attesa, ma non può approvare
st, snap = h.get_json(f"/api/chats/{cid8}", TOKEN)
phone = h.SSE(f"/api/chats/{cid8}/events?after={snap['seq']}", TOKEN)
assert wait_until(lambda: phone.of("answer")), phone.kinds()
assert phone.of("answer")[-1]["tools"][0]["waiting"] is True, phone.of("answer")
phone.close()
# cambiare conversazione non chiude la richiesta: riaprendo la chat torna
win._open_chat("chat_a")
assert card() is None and engine.pending_change() is not None
win._open_chat(cid8)
assert wait_until(lambda: card() is not None)
card().apply_btn.click()
assert wait_until(lambda: not engine.busy())
assert (folder / "nota.txt").read_text() == "nuova riga\n"
assert win.chat_area._stream_widget is None


def answers_with_undo():
    out = []
    for j in range(1, win.chat_area.msgs.count()):
        w = win.chat_area._row_widget(win.chat_area.msgs.itemAt(j).widget())
        if getattr(w, "role", "") == "assistant" and not w.undo_btn.isHidden():
            out.append(w)
    return out


# la risposta che ha cambiato il file riceve «Annulla le modifiche» (W4)
assert wait_until(lambda: len(answers_with_undo()) == 1)
# richiesta che arriva mentre si guarda un'altra chat: un avviso lo dice
notes = []
win.chat_area.add_system_note = notes.append
win._open_chat("chat_a")
fake.write_rounds = [[{"name": "edit_file", "arguments": {
    "path": "progetto/nota.txt", "old_text": "nuova", "new_text": "terza"}}]]
engine.send(cid8, "modifica ancora", "finto-due")
assert wait_until(lambda: engine.pending_change() is not None)
assert wait_until(lambda: notes) and "aspetta la tua conferma" in notes[0], notes
win.chat_area.add_system_note = real_note
# Rifiuta dalla scheda
win._open_chat(cid8)
assert wait_until(lambda: card() is not None)
card().reject_btn.click()
assert wait_until(lambda: not engine.busy())
assert (folder / "nota.txt").read_text() == "nuova riga\n"
# 9. annullare dalla bolla: conferma, file ripristinato, chiamata «annullata»
from PySide6.QtWidgets import QMessageBox  # noqa: E402

asked = []
real_exec = QMessageBox.exec
QMessageBox.exec = lambda box: asked.append(box.text()) or QMessageBox.StandardButton.Yes
assert len(answers_with_undo()) == 1
answers_with_undo()[0].undo_btn.click()
QMessageBox.exec = real_exec
assert asked and "Operazioni da annullare: 1" in asked[0], asked
assert (folder / "nota.txt").read_text() == "prima riga\n"
assert wait_until(lambda: not answers_with_undo())
undone_bubbles = [win.chat_area._row_widget(win.chat_area.msgs.itemAt(j).widget())
                  for j in range(1, win.chat_area.msgs.count())]
assert any("annullata" in w._tools_text() for w in undone_bubbles
           if getattr(w, "_tool_calls", None)), "esito «annullata» assente"
print("8b. annullare le modifiche dalla bolla OK")
# chiusura dell'app con una richiesta aperta (sotto): vale come rifiuto
fake.write_rounds = [[{"name": "edit_file", "arguments": {
    "path": "progetto/nota.txt", "old_text": "prima", "new_text": "altra"}}]]
engine.send(cid8, "modifica per l'ultima volta", "finto-due")
assert wait_until(lambda: engine.pending_change() is not None)
print("8. conferma delle modifiche nella bolla OK")

win._really_quit = True
win.close()
wait_until(lambda: False, 200)
fake.close()
assert not slot_errors, f"eccezioni negli slot: {slot_errors}"
print("SYNC OK")

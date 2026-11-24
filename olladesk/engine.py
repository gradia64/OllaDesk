"""Motore di chat: stato delle conversazioni, generazione e persistenza.

QObject senza widget: la finestra (e, dalla 0.3, il server companion) ne
sono client. Ogni segnale porta il `chat_id`: un client mostra solo gli
eventi della conversazione che sta guardando.

Regola dei thread: i metodi si chiamano solo dal thread Qt principale.
Una sola elaborazione alla volta, globale (ricerca web + generazione).

Ciclo dei tool: con `send(..., tools=True)` e un registro non vuoto, una
risposta può durare più giri. Il modello chiede dei tool, `ToolWorker` li
esegue, i risultati tornano al modello e la generazione riprende, sempre
nella stessa risposta (un solo generation_started/generation_finished). La
fase resta "chat" per tutto il ciclo. Le chiamate di ogni giro completo si
salvano nel messaggio dell'assistente, in "tool_rounds".

Conferma delle modifiche (0.5): con il livello «write» o «full» i tool di
scrittura chiedono il permesso a `write_tools.ApprovalGate` dal thread dei
tool, che resta fermo finché l'utente non sceglie (`resolve_change`). Il
motore emette `change_requested` e, a scelta fatta o richiesta chiusa da uno
Stop, `change_resolved`; la fase resta "chat". Cambiare conversazione non
chiude la richiesta: riaprendo la chat la si ritrova (`pending_change`).
"""
from __future__ import annotations

import datetime
import re
import shutil
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import config, context, file_tools, secrets_store, tools, web_search, write_tools
from .i18n import _
from .ollama_client import (
    ApiWorker, CapsWorker, ChatWorker, PostWorker, PullWorker, chat_capable, format_stats,
    visible_models,
)
from .workers import WorkerRegistry


class ChatEngine(QObject):
    # --- conversazioni
    chats_changed = Signal()                  # indice cambiato (titoli, ordine, eliminazioni)
    chat_deleted = Signal(str)                # chat_id eliminata (dal PC o dal telefono)
    answer_undone = Signal(str, int)          # chat_id, indice della risposta annullata
    user_message_added = Signal(str, dict)    # chat_id, messaggio utente salvato
    # --- elaborazione (una sola alla volta, globale)
    busy_changed = Signal(bool)
    search_started = Signal(str)              # chat_id: ricerca web in corso
    search_finished = Signal(str)             # chat_id: ricerca conclusa o annullata
    generation_started = Signal(str)          # chat_id: si apre la risposta dell'assistente
    text_chunk = Signal(str, str)             # chat_id, frammento
    think_chunk = Signal(str, str)            # chat_id, frammento di ragionamento
    # chat_id, esito ("done" | "failed" | "stopped"), statistiche, errore
    generation_finished = Signal(str, str, str, str)
    notice = Signal(str, str)                 # chat_id ("" = generale), testo di avviso
    # chat_id, {"state": "started"|"done", "round", "index", "name",
    # "arguments", e a fine chiamata "result", "error"}
    tool_activity = Signal(str, dict)
    # modifica ai file in attesa della conferma: chat_id, Request.view()
    change_requested = Signal(str, dict)
    # richiesta chiusa: chat_id, {"id", "decision"} ("" = chiusa da uno stop)
    change_resolved = Signal(str, dict)
    # dal thread dei tool (coda verso il thread principale)
    _change_asked = Signal(object)
    # --- server Ollama e modelli
    status_changed = Signal(bool, str)        # online, versione
    models_loading = Signal(bool)             # elenco modelli in caricamento
    models_changed = Signal(list)             # modelli da chat da mostrare ([] = nessuno)
    caps_changed = Signal()                   # arrivate le capacità di qualche modello
    model_task_changed = Signal()             # scarica/elimina avviato, avanzato o concluso

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._chats: list[dict] = config.load_chats()
        self._cache: dict[str, dict] = {}     # conversazioni già caricate
        self._workers = WorkerRegistry()
        self._worker: ChatWorker | None = None
        self._search_worker: web_search.WebSearchWorker | None = None
        self._active_id: str | None = None   # chat in elaborazione
        self._phase: str | None = None       # None | "search" | "chat"
        self._pending_user: dict | None = None
        # messaggio già in chat che attende i risultati della ricerca web
        self._search_msg: tuple[dict, dict, bool] | None = None   # chat, messaggio, think
        self._think_off_sent = False          # la richiesta in corso ha "think": false
        self._pending_stream = ""
        self._pending_think = ""              # ragionamento ricevuto nel turno corrente
        self._chat_stopped = False            # scarta i segnali del worker dopo uno stop
        self._search_stopped = False
        # ciclo dei tool: registro (vuoto finché non arrivano i tool veri),
        # scelta dell'invio in corso, richiesta del turno che cresce a ogni
        # giro, giri completi e giro le cui chiamate sono in esecuzione
        self.tools = tools.ToolRegistry()
        self._use_tools = False
        self._tools_level = "off"
        # copie dei file modificati nella risposta in corso (annullamento, W4)
        self._journal = write_tools.WriteJournal()
        # conferma delle modifiche della risposta in corso (None = senza scrittura)
        self._gate: write_tools.ApprovalGate | None = None
        self._tool_index = -1            # chiamata del giro in esecuzione
        self._max_rounds = tools.MAX_ROUNDS
        self._change_asked.connect(self._on_change_asked)
        self._turn_tools = self.tools   # registro del turno: fissi + file della chat
        self._web_tool = False          # 🌐 come tool in questo turno
        self._tool_worker: tools.ToolWorker | None = None
        self._payload: dict | None = None
        self._rounds: list[dict] = []
        self._open_round: dict | None = None
        self._stream_mark = 0                 # inizio del testo del giro in corso
        self._page_urls: set[str] = set()     # pagine che fetch_page può aprire
        self._held = ""                       # chiamata scritta come testo, non mostrata
        self._tail = ""                       # fine di un frammento che può iniziare un marcatore
        self._think_mark = 0
        self._closed = False
        self._status_worker: ApiWorker | None = None
        self._models_worker: ApiWorker | None = None
        self._models: list[dict] = []
        # capacità da /api/show: nome → (digest, capacità); un modello
        # aggiornato cambia digest e si richiede
        self._caps: dict[str, tuple[str, list[str]]] = {}
        self._caps_worker: CapsWorker | None = None
        self._caps_again = False              # elenco ricaricato mentre si chiedevano
        # scarica/elimina avviato dal telefono (una sola operazione alla volta):
        # {"op": "pull"|"delete", "name", "state": "running"|"done"|"failed",
        #  "pct": int|None, "status": str, "error": str, "ended": ts (a fine operazione)}
        self._task: dict | None = None
        self._task_worker = None
        self._online: bool | None = None
        self._version = "?"

    def set_settings(self, settings: dict) -> None:
        """Le impostazioni si leggono a ogni invio: valgono dal prossimo."""
        self.settings = settings

    # ---------------------------------------------------------------- lettura

    @staticmethod
    def new_chat_id() -> str:
        return config.new_chat_id()

    def chats(self) -> list[dict]:
        """Indice delle conversazioni (id, titolo, modello, aggiornamento)."""
        return self._chats

    def chat(self, chat_id: str | None) -> dict | None:
        """Conversazione completa; il chiamante non deve modificarla."""
        if not chat_id:
            return None
        c = self._cache.get(chat_id)
        if c is None:
            c = config.load_chat(chat_id)
            if c is not None:
                self._cache[chat_id] = c
        return c

    def busy(self) -> bool:
        return self._phase is not None

    def active_chat_id(self) -> str | None:
        return self._active_id

    def phase(self) -> str | None:
        """Elaborazione in corso: None, "search" (ricerca web) o "chat"."""
        return self._phase

    def partial(self) -> tuple[str, str]:
        """Testo e ragionamento già ricevuti nella risposta in corso."""
        return self._pending_stream, self._pending_think

    def partial_tools(self) -> list[dict]:
        """Chiamate dei tool della risposta in corso (`tools.calls_view`):
        per ridisegnare la bolla riaprendo la chat e per le fonti alla fine."""
        return tools.calls_view(self._rounds, self._open_round)

    def think_off_sent(self) -> bool:
        """True se la generazione in corso ha inviato "think": false."""
        return self._think_off_sent

    # ------------------------------------------------- server Ollama e modelli

    def online(self) -> bool | None:
        """True/False dopo il primo controllo, None se ancora da verificare."""
        return self._online

    def version(self) -> str:
        return self._version

    def models(self) -> list[dict]:
        """Modelli installati: nome, dettagli e dimensione, in ordine alfabetico."""
        return self._models

    def model_names(self) -> list[str]:
        return [m["name"] for m in self._models]

    def model_caps(self, name: str) -> list[str] | None:
        """Capacità di un modello installato, None se non ancora note."""
        m = next((m for m in self._models if m["name"] == name), None)
        got = self._caps.get(name)
        if m is None or got is None or got[0] != m["digest"]:
            return None
        return got[1]

    def chat_models(self) -> list[dict]:
        """Modelli da proporre per la chat: senza quelli che non sanno
        chattare (embedding, decisionali, vocali…). Il gestore dei modelli
        li mostra tutti, per poterli eliminare."""
        return [m for m in self._models if chat_capable(self.model_caps(m["name"]))]

    def chat_model_names(self) -> list[str]:
        return [m["name"] for m in self.chat_models()]

    def tools_supported(self, name: str) -> bool:
        """Il modello sa chiamare i tool (capacità «tools» di /api/show).

        Capacità non ancora note = no: i modelli che non li supportano
        rispondono con un errore se la richiesta porta `tools`.
        """
        return "tools" in (self.model_caps(name) or [])

    def _tools_for(self, chat: dict, web: bool = False) -> tools.ToolRegistry:
        """Tool del turno: quelli fissi, la lettura di ciò che la chat
        condivide (e la scrittura, con il livello «write» o «full» della
        chat) e, con 🌐 acceso nell'invio, la ricerca web.

        Le cartelle e i file (non immagini) allegati nella conversazione sono
        l'unica parte del disco che il modello può leggere (`file_tools`).
        Senza 🌐 il modello non va in rete.
        """
        # testo letto dai file in questa risposta: web_search rifiuta una
        # query che ne ripete un pezzo (non deve uscire verso il motore)
        read_log: list[str] = []
        shared = file_tools.shared_from_chat(chat)
        level = chat_tools_level(chat)
        writable = level in tools.WRITE_LEVELS
        extra = file_tools.make_tools(shared, read_log, writable=writable)
        self._close_gate()
        if writable:
            self._journal.close()
            self._journal.discard()   # copie di una risposta mai salvata
            self._journal = write_tools.WriteJournal()
            self._gate = write_tools.ApprovalGate(self._change_asked.emit)
            extra += write_tools.make_write_tools(shared, self._gate.ask, self._journal,
                                                  full=level == "full")
        if web:
            extra.append(web_search.web_tool(**self._search_settings(), file_texts=read_log))
            # fetch_page apre solo le pagine uscite dalle ricerche di questa
            # risposta: l'insieme si riempie a ogni giro (_on_tools_finished)
            self._page_urls = set()
            extra.append(web_search.page_tool(self._page_urls))
        if not extra:
            return self.tools
        return tools.ToolRegistry([*(self.tools.get(n) for n in self.tools.names()), *extra])

    def reset_status(self) -> None:
        """Host cambiato: il prossimo controllo riuscito ricarica i modelli."""
        self._online = None

    def check_server(self) -> None:
        if self._status_worker is not None or self._closed:
            return
        w = ApiWorker(self.settings["host"], "/api/version", self)
        w.ready.connect(self._on_server_ok)
        w.failed.connect(self._on_server_fail)
        w.finished.connect(w.deleteLater)
        w.finished.connect(self._workers.clear_ref(self, "_status_worker", w))
        self._status_worker = w
        self._workers.track(w)
        w.start()

    def _on_server_ok(self, data: object) -> None:
        version = (data or {}).get("version", "?") if isinstance(data, dict) else "?"
        self._version = version
        was_online = self._online
        self._online = True
        self.status_changed.emit(True, version)
        if not was_online:
            self.refresh_models()

    def _on_server_fail(self, _err: str) -> None:
        self._online = False
        self.status_changed.emit(False, self._version)
        if not self._models:
            self.models_changed.emit([])

    def refresh_models(self) -> None:
        if self._models_worker is not None or self._closed:
            return
        self.models_loading.emit(True)
        w = ApiWorker(self.settings["host"], "/api/tags", self)
        w.ready.connect(self._on_models)
        w.failed.connect(self._on_models_fail)
        w.finished.connect(w.deleteLater)
        w.finished.connect(self._workers.clear_ref(self, "_models_worker", w))
        self._models_worker = w
        self._workers.track(w)
        w.start()

    def _on_models(self, data: object) -> None:
        self.models_loading.emit(False)
        if not isinstance(data, dict):
            return
        self._models = [
            {
                "name": m.get("name") or m.get("model") or "?",
                "details": m.get("details", {}),
                "size": m.get("size", 0),
                "digest": m.get("digest") or "",
            }
            for m in visible_models(data.get("models"))
        ]
        self._models.sort(key=lambda m: m["name"].lower())
        self.models_changed.emit(self.chat_model_names())
        self._fetch_caps()

    def _fetch_caps(self) -> None:
        """Chiede a /api/show le capacità dei modelli che non si conoscono."""
        if self._closed:
            return
        if self._caps_worker is not None:
            self._caps_again = True   # si riprende quando ha finito
            return
        want = {m["name"]: m["digest"] for m in self._models if self.model_caps(m["name"]) is None}
        if not want:
            return
        w = CapsWorker(self.settings["host"], list(want), self)
        w.ready.connect(lambda caps, want=want: self._on_caps(want, caps))
        w.finished.connect(w.deleteLater)
        w.finished.connect(self._workers.clear_ref(self, "_caps_worker", w))
        w.finished.connect(self._after_caps)
        self._caps_worker = w
        self._workers.track(w)
        w.start()

    def _on_caps(self, want: dict, caps: dict) -> None:
        before = self.chat_model_names()
        for name, c in caps.items():
            if name in want:
                self._caps[name] = (want[name], c)
        after = self.chat_model_names()
        if after != before:
            self.models_changed.emit(after)
        self.caps_changed.emit()

    def _after_caps(self) -> None:
        # dopo clear_ref (collegato prima): _caps_worker è già None
        if self._caps_again:
            self._caps_again = False
            self._fetch_caps()

    def _on_models_fail(self, _err: str) -> None:
        self.models_loading.emit(False)
        self.models_changed.emit([])

    # ------------------------------------------------ scarica / elimina modelli

    def model_task(self) -> dict | None:
        return dict(self._task) if self._task else None

    def _task_running(self) -> bool:
        return bool(self._task and self._task["state"] == "running")

    def _start_task(self, op: str, name: str, worker) -> None:
        self._task = {"op": op, "name": name, "state": "running",
                      "pct": None, "status": "", "error": ""}
        self._task_worker = worker
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(self._workers.clear_ref(self, "_task_worker", worker))
        self._workers.track(worker)
        self.model_task_changed.emit()
        worker.start()

    def _task_update(self, worker, **fields) -> None:
        # segnali tardivi di un worker annullato o sostituito: scartati
        if worker is not self._task_worker or self._task is None:
            return
        self._task.update(fields)
        if fields.get("state") in ("done", "failed"):
            self._task["ended"] = config.now()
        self.model_task_changed.emit()

    def pull_model(self, name: str) -> str | None:
        """Avvia lo scaricamento di `name`. None se parte, altrimenti il motivo."""
        if self._closed:
            return _("OllaDesk si sta chiudendo")
        if not name:
            return _("nome del modello mancante")
        if self._task_running():
            return _("un'altra operazione sui modelli è già in corso")
        w = PullWorker(self.settings["host"], name, self)
        w.progress.connect(lambda d, w=w: self._on_pull_progress(w, d))
        w.done.connect(lambda w=w: self._on_task_done(w))
        w.failed.connect(lambda err, w=w: self._task_update(w, state="failed", error=err))
        self._start_task("pull", name, w)
        return None

    def _on_pull_progress(self, w, data: dict) -> None:
        total, done = data.get("total"), data.get("completed")
        pct = int(done * 100 / total) if total and done is not None else None
        self._task_update(w, pct=pct, status=str(data.get("status", "")))

    def delete_model(self, name: str) -> str | None:
        """Elimina `name` dal disco. None se parte, altrimenti il motivo."""
        if self._closed:
            return _("OllaDesk si sta chiudendo")
        if self._task_running():
            return _("un'altra operazione sui modelli è già in corso")
        if name not in self.model_names():
            return _("modello non installato")
        if self.busy():
            return _("il PC sta rispondendo: riprova alla fine")
        w = PostWorker(self.settings["host"], "/api/delete", {"model": name}, self, "DELETE")
        w.ready.connect(lambda _d, w=w: self._on_task_done(w))
        w.failed.connect(lambda err, w=w: self._task_update(w, state="failed", error=err))
        self._start_task("delete", name, w)
        return None

    def _on_task_done(self, w) -> None:
        self._task_update(w, state="done", pct=100)
        self.refresh_models()

    def cancel_model_task(self) -> bool:
        """Annulla uno scaricamento in corso (l'eliminazione non si annulla)."""
        w = self._task_worker
        if not self._task_running() or self._task["op"] != "pull" or w is None:
            return False
        w.stop()
        self._task.update(state="failed", error=_("scaricamento annullato"), ended=config.now())
        self._task_worker = None   # i segnali tardivi del worker fermo si scartano
        self._workers.retire(w)
        self.model_task_changed.emit()
        return True

    # ---------------------------------------------------------- conversazioni

    def _persist(self, chat: dict) -> None:
        """Salva la conversazione (file dedicato + indice)."""
        if not config.save_chat(chat):
            self.notice.emit(
                chat["id"],
                # senza percorso: gli avvisi arrivano anche al telefono abbinato,
                # che non deve vedere nome utente e cartelle del PC
                _('⚠ Impossibile salvare la conversazione su disco: spazio '
                  'esaurito o permessi mancanti?'),
            )
        # tiene allineato l'elenco in memoria (ordine e titolo nella sidebar)
        entry = {"id": chat["id"], "title": chat.get("title", _("Conversazione")),
                 "model": chat.get("model", ""), "updated": chat.get("updated", 0),
                 "archived": chat.get("archived") is True}
        self._chats = [e for e in self._chats if e["id"] != chat["id"]] + [entry]
        self.chats_changed.emit()

    def rename_chat(self, chat_id: str, title: str) -> None:
        cached = self._cache.get(chat_id)
        if cached is not None:
            # la copia in memoria va rinominata: il prossimo salvataggio
            # riscriverebbe il titolo vecchio
            cached["title"] = title.strip()
            self._persist(cached)
        else:
            config.rename_chat(chat_id, title.strip())
        self._chats = config.load_chats()
        self.chats_changed.emit()

    def set_archived(self, chat_id: str, archived: bool) -> None:
        """Archivia (fuori dall'elenco principale) o ripristina una conversazione."""
        cached = self._cache.get(chat_id)
        if cached is not None:
            # come per rename_chat: il prossimo salvataggio della copia in
            # memoria riscriverebbe lo stato vecchio
            if archived:
                cached["archived"] = True
            else:
                cached.pop("archived", None)
            self._persist(cached)
        else:
            config.set_chat_archived(chat_id, archived)
        self._chats = config.load_chats()
        self.chats_changed.emit()

    def archive_idle(self, days: int, now: float | None = None) -> int:
        """Archivia le conversazioni senza messaggi da `days` giorni; quante."""
        if days <= 0:
            return 0
        limit = (config.now() if now is None else now) - days * 86400
        idle = [c["id"] for c in self._chats
                if not c.get("archived") and c.get("updated", 0) < limit
                and c["id"] != self._active_id]
        for chat_id in idle:
            self.set_archived(chat_id, True)
        return len(idle)

    def set_chat_tools(self, chat_id: str, level) -> None:
        """Livello dei tool di una conversazione esistente (o True/False per
        lettura/spento), salvato subito: vale anche per i messaggi che
        arrivano dal telefono."""
        chat = self.chat(chat_id)
        level = tools.normalize_level(level)
        if chat is None or chat_tools_level(chat) == level:
            return
        _set_tools_level(chat, level)
        self._persist(chat)

    # ------------------------------------------------- conferma delle modifiche

    def pending_change(self) -> dict | None:
        """Modifica che aspetta la conferma (`Request.view()`), None se nessuna."""
        req = self._gate.pending() if self._gate is not None else None
        return req.view() if req is not None and not req.event.is_set() else None

    def resolve_change(self, req_id: int, decision: str) -> bool:
        """Scelta dell'utente (write_tools.APPLY, REJECT, ALLOW_REST) sulla
        richiesta `req_id`. False se non è più in attesa."""
        if self._gate is None or not self._gate.resolve(req_id, decision):
            return False
        self._change_closed(req_id, decision)
        return True

    def _on_change_asked(self, req) -> None:
        # arriva in coda dal thread dei tool: la richiesta può essere già
        # chiusa (Stop) o di una risposta precedente
        gate = self._gate
        if (gate is None or gate.pending() is not req or req.event.is_set()
                or self._open_round is None):
            return
        calls = self._open_round["calls"]
        if 0 <= self._tool_index < len(calls):
            calls[self._tool_index]["waiting"] = True
            self.tool_activity.emit(self._active_id,
                                    self._tool_event("waiting", self._tool_index))
        self.change_requested.emit(self._active_id, req.view())

    def _change_closed(self, req_id: int, decision: str) -> None:
        if self._open_round is not None:
            for c in self._open_round["calls"]:
                c.pop("waiting", None)
        self.change_resolved.emit(self._active_id, {"id": req_id, "decision": decision})

    def _close_gate(self) -> None:
        """Chiude la conferma della risposta: una richiesta aperta vale come
        rifiuto e il thread dei tool riparte."""
        gate, self._gate = self._gate, None
        if gate is None:
            return
        req = gate.close()
        if req is not None and self._active_id is not None:
            self._change_closed(req.id, "")

    # --------------------------------------------- annullare una risposta

    def _answer_journal(self, chat_id: str, msg) -> object | None:
        undo_id = msg.get("undo") if isinstance(msg, dict) and msg.get("role") == "assistant" \
            else None
        if not isinstance(undo_id, str) or not _SAFE_ID.match(undo_id):
            return None
        return write_tools.WriteJournal.load(_undo_dir(chat_id, undo_id))

    def has_undo(self, chat_id: str, msg: dict) -> bool:
        """La risposta ha un registro per annullare (una chat importata porta
        il campo "undo" ma non le copie)."""
        undo_id = msg.get("undo")
        try:
            return isinstance(undo_id, str) and \
                (_undo_dir(chat_id, undo_id) / "journal.json").is_file()
        except OSError:
            return False

    def _undo_journal(self, chat_id: str, index: int) -> tuple[dict, dict, object] | None:
        chat = self.chat(chat_id)
        if chat is None or not (0 <= index < len(chat["messages"])):
            return None
        msg = chat["messages"][index]
        journal = self._answer_journal(chat_id, msg)
        return (chat, msg, journal) if journal is not None else None

    @staticmethod
    def _undo_roots(chat: dict) -> list[Path]:
        """Le voci condivise dalla chat: si annulla solo lì dentro (il
        registro è un file che si tratta come non attendibile)."""
        return [s.path for s in file_tools.shared_from_chat(chat)]

    def _later_overlap(self, chat: dict, index: int, journal) -> bool:
        """Una risposta successiva, ancora annullabile, ha toccato gli stessi
        percorsi: annullare prima questa rimetterebbe i file in uno stato
        sbagliato (ognuna ripristina il suo «prima»)."""
        mine = set(journal._touched())
        for later in chat["messages"][index + 1:]:
            other = self._answer_journal(chat["id"], later)
            if other is not None and mine & set(other._touched()):
                return True
        return False

    def undo_status(self, chat_id: str, index: int) -> dict | None:
        """Si possono annullare le modifiche della risposta `index`? None se
        no; altrimenti {"count": operazioni, "changed": [file cambiati dopo
        la risposta, che annullando tornerebbero indietro], "later": True se
        prima va annullata una risposta successiva sugli stessi file}."""
        found = self._undo_journal(chat_id, index)
        if found is None:
            return None
        chat, _msg, journal = found
        return {"count": len(journal.ops),
                "changed": journal.changed_since(self._undo_roots(chat)),
                "later": self._later_overlap(chat, index, journal)}

    def undo_answer(self, chat_id: str, index: int) -> dict | None:
        """Annulla le modifiche ai file della risposta `index`. None se non si
        può (nulla da annullare, il PC sta rispondendo, prima va annullata
        una risposta successiva); altrimenti {"failed": file non
        ripristinati, "in_trash": file rimasti nel cestino del sistema}. Le
        chiamate annullate restano nella risposta, segnate come annullate."""
        if self.busy():
            return None
        found = self._undo_journal(chat_id, index)
        if found is None:
            return None
        chat, msg, journal = found
        if self._later_overlap(chat, index, journal):
            return None
        failed = journal.undo(self._undo_roots(chat))
        for rnd in msg.get("tool_rounds") or []:
            for c in rnd.get("calls") or []:
                if c.get("name") in tools.WRITE_TOOL_NAMES and not c.get("error"):
                    c["undone"] = True
        msg.pop("undo", None)
        self._persist(chat)
        self.answer_undone.emit(chat_id, index)
        return {"failed": failed, "in_trash": list(journal.in_trash)}

    def delete_chat(self, chat_id: str) -> None:
        if chat_id == self._active_id:
            self.stop()
        if _SAFE_ID.match(chat_id or ""):
            shutil.rmtree(config.config_dir() / "undo" / chat_id, ignore_errors=True)
        self._cache.pop(chat_id, None)
        # le chat in memoria proteggono i loro allegati anche se il loro
        # salvataggio su disco è fallito
        config.delete_chat(chat_id, list(self._cache.values()))
        self._chats = [c for c in self._chats if c["id"] != chat_id]
        self.chat_deleted.emit(chat_id)
        self.chats_changed.emit()

    # ------------------------------------------------------------------ invio

    def send_refusal(self, model: str) -> str | None:
        """Motivo per cui un invio con `model` non può partire, o None.

        Non copre «occupato», che i chiamanti spiegano a modo loro.
        """
        t = self._task
        if t and t["state"] == "running" and t["op"] == "delete" and t["name"] == model:
            return _('il modello «{model}» è in eliminazione').format(model=model)
        return None

    def send(self, chat_id: str | None, text: str, model: str, *,
             attachments=(), web: bool = False, think: bool = True,
             tools: bool | str | None = None) -> str | None:
        """Aggiunge un messaggio utente e avvia la risposta.

        `chat_id` è una conversazione esistente o un id nuovo (`new_chat_id`):
        la conversazione nasce al primo messaggio salvato. Restituisce l'id,
        oppure None se un'altra elaborazione è già in corso.

        `tools` è il livello della conversazione ("off", "read", "write",
        "full"; True/False valgono lettura/spento; spento di default): lo
        salva nella chat, None usa quello già salvato (così fa il telefono,
        che non ha il menu). I tool partono solo se il modello li supporta
        (`tools_supported`).
        """
        if self.busy() or self._closed or self.send_refusal(model):
            return None
        chat_id = chat_id or self.new_chat_id()

        # validazione rapida: il contenuto dei file viene letto alla generazione.
        # Gli avvisi partono dopo busy_changed(True): la companion azzera lì il
        # suo backlog, e un telefono che apre la chat nuova dopo l'invio non li
        # vedrebbe
        meta, warnings = [], []
        for a in attachments:
            p = Path(a["path"])
            if not p.exists():
                warnings.append(_('file non leggibile: {name}').format(name=a['name']))
                continue
            meta.append({"path": str(p), "name": a["name"], "kind": a["kind"]})
        if web and not web_search.make_query(text):
            warnings.append(_("Ricerca web saltata: scrivi una domanda insieme agli allegati."))
            web = False
        # ricerca affiancata: con i tool attivi (scelta di questo invio o
        # quella salvata) e un modello che li supporta, il modello cerca da
        # solo con il tool web_search invece della ricerca prima della risposta
        saved = self.chat(chat_id)
        if tools is not None:
            tools = _normalize_level(tools)   # il parametro copre il modulo tools
        level = tools if tools is not None else (chat_tools_level(saved) if saved else "off")
        tools_on = level != "off"
        web_tool = web and tools_on and self.tools_supported(model)

        self._pending_user = {
            "chat_id": chat_id,
            "display": text,
            "attachments_meta": meta,
            "attachments": [a["name"] for a in meta],
            "image_paths": [a["path"] for a in meta if a["kind"] == "image"],
            "web": web,
            "model": model,
            "think": think,
            "tools": tools,
            "web_tool": web_tool,
        }
        self._active_id = chat_id
        pre_search = web and not web_tool
        self._phase = "search" if pre_search else "chat"
        self.busy_changed.emit(True)
        if warnings:
            self.notice.emit(chat_id, "⚠ " + "\n⚠ ".join(warnings))
        if pre_search:
            # il messaggio va subito in chat: la ricerca può durare secondi
            # (prima la bolla compariva solo a ricerca finita)
            self._search_msg = self._commit_user_message(start=False)
            self._start_web_search()
        else:
            self._commit_user_message()
        return chat_id

    # ------------------------------------------------------------- ricerca web

    def _start_web_search(self) -> None:
        self._search_stopped = False
        self.search_started.emit(self._active_id)
        w = web_search.WebSearchWorker(
            web_search.make_query(self._search_msg[1]["display"]),
            parent=self,
            **self._search_settings(),
        )
        w.ready.connect(self._on_web_results)
        w.failed.connect(self._on_web_failed)
        w.notice.connect(self._on_web_notice)
        w.finished.connect(w.deleteLater)
        w.finished.connect(self._workers.clear_ref(self, "_search_worker", w))
        self._search_worker = w
        self._workers.track(w)
        w.start()

    def _search_settings(self) -> dict:
        """Provider, riserva, chiave e numero di risultati della ricerca web."""
        s = self.settings
        provider = s.get("web_provider", "duckduckgo")
        fallback = s.get("web_fallback", "")
        # la chiave serve solo a ollama.com: leggerla dal portachiavi
        # (KWallet, nel thread della UI) costa fino a ~200 ms per niente
        api_key = ""
        if "ollama" in (provider, fallback):
            api_key = secrets_store.load_api_key() or s.get("web_api_key", "")
        return {
            "n_results": int(s.get("web_results", 5)),
            "provider": provider,
            "fallback": fallback,
            "api_key": api_key,
            "searxng_url": s.get("web_searxng_url", ""),
        }

    def _on_web_results(self, block: str, _query: str) -> None:
        if self._search_stopped or self.sender() is not self._search_worker:
            return
        self.search_finished.emit(self._active_id)
        pending, self._search_msg = self._search_msg, None
        if pending is not None:
            # i risultati si agganciano al messaggio già in chat, prima della
            # generazione (che li allega al contesto dell'ultimo turno)
            chat, msg, think = pending
            msg["web_block"] = block
            self._persist(chat)
            self._phase = "chat"
            self._start_generation(chat, think)

    def _on_web_notice(self, text: str) -> None:
        if self._search_stopped or self.sender() is not self._search_worker:
            return
        self.notice.emit(self._active_id, "🌐 " + text)

    def _on_web_failed(self, err: str) -> None:
        if self._search_stopped or self.sender() is not self._search_worker:
            return
        self.search_finished.emit(self._active_id)
        self.notice.emit(
            self._active_id,
            _('⚠ Ricerca web non riuscita ({err}).\nProcedo senza i risultati web.').format(err=err),
        )
        pending, self._search_msg = self._search_msg, None
        if pending is not None:
            chat, _msg, think = pending
            self._phase = "chat"
            self._start_generation(chat, think)

    # ------------------------------------------------------------------ commit

    def _commit_user_message(self, start: bool = True) -> tuple[dict, dict, bool] | None:
        """Aggiunge il messaggio dell'utente alla conversazione e lo salva.

        Con start=False la generazione non parte: la avvia la fine della
        ricerca web. Restituisce (conversazione, messaggio, think).
        """
        p, self._pending_user = self._pending_user, None
        if p is None:
            return None
        chat_id = p["chat_id"]
        model = p["model"]
        chat = self.chat(chat_id)
        if chat is None:
            title = p["display"][:48] + ("…" if len(p["display"]) > 48 else "")
            chat = {
                "id": chat_id,
                "title": title or _("Allegati"),
                "model": model,
                "updated": config.now(),
                "messages": [],
            }
            self._cache[chat_id] = chat

        ts = config.now()
        msg = {
            "role": "user",
            "display": p["display"],
            "ts": ts,
            "attachments": p["attachments"],
            "attachments_meta": p["attachments_meta"],
            "web": p["web"],
        }
        if p.get("web_block"):
            msg["web_block"] = p["web_block"]
        if p["image_paths"]:
            msg["image_paths"] = p["image_paths"]
        chat["messages"].append(msg)
        chat["model"] = model
        chat["updated"] = ts
        chat.pop("archived", None)   # una chat ripresa torna nell'elenco
        if p["tools"] is not None:
            _set_tools_level(chat, p["tools"])
        self._tools_level = chat_tools_level(chat)
        self._use_tools = self._tools_level != "off"
        self._web_tool = bool(p.get("web_tool"))
        self.user_message_added.emit(chat_id, msg)
        self._persist(chat)
        if start:
            self._start_generation(chat, p["think"])
        return chat, msg, p["think"]

    def _build_messages(self, chat: dict) -> tuple[list[dict], list[str]]:
        """Messaggi per Ollama: prompt di sistema, cronologia e allegati
        dell'ultimo turno, letti ora dal disco. Restituisce anche gli avvisi."""
        s = self.settings
        messages: list[dict] = []
        warnings: list[str] = []
        system = s["system_prompt"]
        # con file o cartelle condivisi il modello deve sapere che non può
        # modificarli: altrimenti a «scrivi nel file» risponde di averlo fatto
        if file_tools.shared_from_chat(chat):
            # con il livello di scrittura (e un modello che usa i tool) il
            # modello sa che può modificare, e che ogni modifica va confermata
            writable = (self._use_tools and self._tools_level in tools.WRITE_LEVELS
                        and self.tools_supported(chat.get("model") or ""))
            note = (file_tools.READ_ONLY_NOTE if not writable
                    else write_tools.FULL_NOTE if self._tools_level == "full"
                    else write_tools.WRITE_NOTE)
            system = (system + "\n\n" if system else "") + note
        if system:
            messages.append({"role": "system", "content": system})
        history = context.history_window(chat["messages"], s["history_limit"])
        last_idx = len(history) - 1
        # i risultati dei tool restano interi solo nell'ultima risposta
        last_answer = max((i for i, m in enumerate(history) if m["role"] == "assistant"),
                          default=-1)
        for i, m in enumerate(history):
            if m["role"] == "assistant":
                messages.extend(_assistant_messages(m, full_results=i == last_answer))
                continue
            # il contenuto completo di allegati/ricerca web viaggia solo con
            # l'ultimo turno utente: i precedenti lasciano un segnaposto
            full = i == last_idx
            content, w = context.build_api_content(m, include_full=full)
            warnings.extend(w)
            entry = {"role": "user", "content": content}
            if full and m.get("image_paths"):
                imgs = []
                # le conversioni (gif, webp…) avvengono qui, nel thread
                # dell'interfaccia: un tetto di tempo per l'intero messaggio
                deadline = time.monotonic() + context.MAX_IMAGE_SECONDS
                for path in m["image_paths"]:
                    b64, note = context.image_to_b64(path, deadline)
                    if note:
                        warnings.append(note)
                    if b64:
                        imgs.append(b64)
                if imgs:
                    entry["images"] = imgs
            messages.append(entry)
        return messages, warnings

    def _start_generation(self, chat: dict, think: bool) -> None:
        chat_id = chat["id"]

        try:
            messages, warnings = self._build_messages(chat)
        except Exception as e:  # noqa: BLE001 — il motore non deve restare «occupato»
            # gli allegati si leggono qui, dal disco: un errore imprevisto
            # lasciava la fase a «chat» e rifiutava ogni invio successivo
            self.notice.emit(chat_id, "⚠ " + _('impossibile preparare la richiesta: {error}').format(
                error=f"{type(e).__name__}: {e}"))
            self._set_idle()
            return

        try:
            payload = self._payload_for(chat, messages, warnings, think)
        except Exception as e:  # noqa: BLE001 — come sopra: mai «occupato» senza richiesta
            # parametri del modello o impostazioni con valori inattesi
            self.notice.emit(chat_id, "⚠ " + _('impossibile preparare la richiesta: {error}').format(
                error=f"{type(e).__name__}: {e}"))
            self._set_idle()
            return

        self._pending_stream = ""
        self._pending_think = ""
        self._held = ""
        self._tail = ""
        self._chat_stopped = False
        self._payload = payload
        self._rounds = []
        self._open_round = None
        self._stream_mark = self._think_mark = 0
        self.generation_started.emit(chat_id)
        self._launch_chat()

    def _payload_for(self, chat: dict, messages: list[dict], warnings: list[str],
                     think: bool) -> dict:
        """Richiesta per /api/chat: opzioni del modello, avvisi e tool del turno."""
        chat_id = chat["id"]
        model = chat["model"]
        s = self.settings
        from .widgets.model_params import options_for_model

        options = options_for_model(model)
        note = context.context_overflow_note(messages, options.get("num_ctx"))
        if note:
            warnings.append(note)
        if warnings:
            self.notice.emit(chat_id, "⚠ " + "\n⚠ ".join(warnings))

        payload = {
            "model": model,
            "messages": messages,
            "stream": bool(s["stream"]),
            "options": options,
        }
        # con il thinking attivo il campo si omette (vale il predefinito del
        # server); solo quando l'utente lo disattiva si invia "think": false
        if not think:
            payload["think"] = False
        self._think_off_sent = not think
        self._turn_tools = self._tools_for(chat, web=self._web_tool)
        self._max_rounds = tools.MAX_ROUNDS
        if self._use_tools and len(self._turn_tools) and self.tools_supported(model):
            payload["tools"] = self._turn_tools.schemas()
            if self._gate is not None:
                self._max_rounds = tools.MAX_WRITE_ROUNDS
            if self._turn_tools.get("web_search") is not None:
                # con la ricerca il modello deve sapere che giorno è
                _add_system_note(messages, tools.today_note(datetime.date.today()))
        return payload

    def _launch_chat(self) -> None:
        """Avvia (o riprende, dopo un giro di tool) la richiesta del turno."""
        w = ChatWorker(self.settings["host"], self._payload, self)
        w.chunk.connect(self._on_chunk)
        w.think_chunk.connect(self._on_think_chunk)
        w.done.connect(self._on_done)
        w.failed.connect(self._on_failed)
        w.finished.connect(w.deleteLater)
        w.finished.connect(self._workers.clear_ref(self, "_worker", w))
        self._worker = w
        self._workers.track(w)
        w.start()

    # ------------------------------------------------------------------ stream

    def _on_chunk(self, chunk: str) -> None:
        if self._chat_stopped or self.sender() is not self._worker:
            return
        # da una chiamata scritta come testo in poi non si mostra nulla. Un
        # marcatore può arrivare spezzato tra due frammenti: la fine di un
        # frammento che potrebbe iniziarne uno aspetta il frammento dopo
        if self._held:
            self._held += chunk
            return
        text = self._tail + chunk
        self._tail = ""
        at = tools.leaked_call_at(text)
        if at >= 0:
            self._held = text[at:]
            text = text[:at]
        else:
            keep = tools.marker_prefix_len(text)
            if keep:
                text, self._tail = text[:-keep], text[-keep:]
        self._emit_text(text)

    def _emit_text(self, text: str) -> None:
        if text:
            self._pending_stream += text
            self.text_chunk.emit(self._active_id, text)

    def _release_held(self) -> bool:
        """Fine della risposta: il testo trattenuto torna visibile se non era
        una chiamata (una risposta che cita «<tool_call>»). True se era una
        chiamata, cioè se è stato tolto."""
        tail, self._tail = self._tail, ""
        self._emit_text(tail)
        held, self._held = self._held, ""
        if held and not tools.looks_like_call(held):
            self._emit_text(held)
            return False
        return bool(held)

    def _on_think_chunk(self, chunk: str) -> None:
        if self._chat_stopped or self.sender() is not self._worker:
            return
        self._pending_think += chunk
        self.think_chunk.emit(self._active_id, chunk)

    def _on_done(self, done: dict) -> None:
        if self._chat_stopped or self.sender() is not self._worker:
            return
        # chiamate ai tool solo se la richiesta li offriva: un modello che le
        # inventa senza tool ha comunque finito di rispondere
        calls = tools.parse_calls(done.get("tool_calls")) if "tools" in self._payload else []
        held = self._release_held()
        if calls:
            self._run_tools(calls)
            return
        if held:
            self.notice.emit(self._active_id, "⚠ " + _(
                'Il modello ha provato a chiamare uno strumento non disponibile invece di '
                'rispondere: la chiamata è stata tolta dalla risposta.'))
        self._finalize("done", format_stats(done))

    # ---------------------------------------------------------------- tool

    def _run_tools(self, calls: list[dict]) -> None:
        """Il modello ha chiesto dei tool: si eseguono, poi si riprende."""
        text = self._pending_stream[self._stream_mark:]
        thinking = self._pending_think[self._think_mark:]
        # la risposta del modello con le chiamate entra nella richiesta del
        # turno; il giro diventa «completo» solo con tutti i risultati
        echo = []
        for c in calls:
            entry = {"function": {"name": c["name"], "arguments": c["arguments"]}}
            if "id" in c:
                entry["id"] = c["id"]
            echo.append(entry)
        msg = {"role": "assistant", "content": text, "tool_calls": echo}
        if thinking:
            msg["thinking"] = thinking
        self._payload["messages"].append(msg)
        self._open_round = {
            "content": text,
            "calls": [{**c, "result": "", "error": False} for c in calls],
        }
        # le letture già riuscite nei giri precedenti non si ripetono
        seen: dict[str, int] = {}
        for n, rnd in enumerate(self._rounds, 1):
            for c in rnd["calls"]:
                if c.get("error"):
                    continue
                tool = self._turn_tools.get(c.get("name", ""))
                if tool is not None and not tool.read_only:
                    seen.clear()   # dopo una modifica le letture di prima non valgono più
                else:
                    seen.setdefault(tools.call_key(c), n)
        w = tools.ToolWorker(self._turn_tools, calls, self,
                             seen=seen, round_no=len(self._rounds) + 1)
        w.call_started.connect(self._on_tool_started)
        w.call_done.connect(self._on_tool_done)
        w.all_done.connect(self._on_tools_finished)
        w.finished.connect(w.deleteLater)
        w.finished.connect(self._workers.clear_ref(self, "_tool_worker", w))
        self._tool_worker = w
        self._workers.track(w)
        w.start()

    def _tool_event(self, state: str, index: int, **extra) -> dict:
        call = self._open_round["calls"][index]
        return {"state": state, "round": len(self._rounds) + 1, "index": index,
                "name": call["name"], "arguments": call["arguments"], **extra}

    def _on_tool_started(self, index: int) -> None:
        if self._chat_stopped or self.sender() is not self._tool_worker:
            return
        self._tool_index = index
        self.tool_activity.emit(self._active_id, self._tool_event("started", index))

    def _on_tool_done(self, index: int, result: str, error: bool) -> None:
        if self._chat_stopped or self.sender() is not self._tool_worker:
            return
        call = self._open_round["calls"][index]
        call.pop("waiting", None)
        call["result"], call["error"], call["done"] = result, error, True
        self.tool_activity.emit(self._active_id,
                                self._tool_event("done", index, result=result, error=error))

    def _on_tools_finished(self) -> None:
        if self._chat_stopped or self.sender() is not self._tool_worker:
            return
        done_round, self._open_round = self._open_round, None
        for c in done_round["calls"]:
            c.pop("done", None)   # serve solo alla vista del giro in corso
            c.pop("waiting", None)
        self._rounds.append(done_round)
        entries = []
        for c in done_round["calls"]:
            entry = {"role": "tool", "tool_name": c["name"], "content": c["result"]}
            if "id" in c:
                entry["tool_call_id"] = c["id"]
            entries.append(entry)
        # quanti giri restano, solo nella richiesta: il risultato salvato
        # (e mostrato) resta quello del tool
        if entries:
            note = tools.round_note(len(self._rounds), self._max_rounds)
            entries[-1] = {**entries[-1], "content": entries[-1]["content"] + "\n\n" + note}
        self._payload["messages"].extend(entries)
        self._stream_mark = len(self._pending_stream)
        self._think_mark = len(self._pending_think)
        for c in done_round["calls"]:
            if c["name"] == "web_search" and not c.get("error"):
                self._page_urls.update(web_search.page_url_key(src["url"])
                                       for src in web_search.web_sources(c["result"]))
        if ("tools" in self._payload and len(self._rounds) < self._max_rounds
                and tools.failed_web_streak(self._rounds) >= web_search.MAX_SEARCH_FAILURES
                and any(t["function"]["name"] in WEB_TOOLS for t in self._payload["tools"])):
            # la ricerca non risponde: i tool web escono dalla richiesta, così
            # il modello non spreca i giri restanti a riprovarla
            left = [t for t in self._payload["tools"] if t["function"]["name"] not in WEB_TOOLS]
            if left:
                self._payload["tools"] = left
            else:
                del self._payload["tools"]
            self._payload["messages"].append(
                {"role": "user", "content": tools.WEB_UNAVAILABLE_REQUEST})
            self.notice.emit(self._active_id, "⚠ " + _(
                'La ricerca web non risponde ({count} tentativi falliti di fila): il modello '
                'continua senza.').format(count=web_search.MAX_SEARCH_FAILURES))
        if len(self._rounds) >= self._max_rounds and "tools" in self._payload:
            # senza tool il modello deve rispondere con quello che ha: glielo
            # si chiede in chiaro, o un modello piccolo ritenta una chiamata
            del self._payload["tools"]
            self._payload["messages"].append(
                {"role": "user", "content": tools.FINAL_ANSWER_REQUEST})
            self.notice.emit(self._active_id, "⚠ " + _(
                'Raggiunto il limite di {count} giri di strumenti: il modello '
                'risponde con quello che ha raccolto.').format(count=self._max_rounds))
        self._launch_chat()

    def _save_assistant(self, stats: str | None) -> None:
        """Salva il testo prodotto nel turno (anche parziale), se c'è.

        Con i tool salva anche i giri completi; un giro interrotto da uno
        stop non entra: senza tutti i risultati, alla richiesta successiva
        Ollama riceverebbe chiamate senza risposta.
        """
        text, thinking = self._pending_stream, self._pending_think
        rounds = self._rounds
        self._pending_stream = ""
        self._pending_think = ""
        self._rounds = []
        self._open_round = None
        chat = self.chat(self._active_id)
        # con dei giri completi la risposta si salva anche senza testo: le
        # chiamate (e le loro fonti) restano visibili riaprendo la chat
        # con delle modifiche ai file si salva sempre: il pulsante «Annulla»
        # vive nella risposta
        journal, self._journal = self._journal, write_tools.WriteJournal()
        # dopo, il thread dei tool non scrive più: una scrittura approvata
        # un attimo prima di Stop o della chiusura finisce nel registro
        journal.close()
        if (text or (rounds and stats is not None) or journal) and chat is not None:
            msg = {"role": "assistant", "content": text, "ts": config.now()}
            if thinking:
                msg["thinking"] = thinking
            if stats:
                msg["stats"] = stats
            if rounds:
                msg["tool_rounds"] = rounds
            if journal:
                undo_id = uuid.uuid4().hex
                try:
                    journal.save(_undo_dir(chat["id"], undo_id))
                    msg["undo"] = undo_id
                except OSError as e:
                    journal.discard()
                    self.notice.emit(chat["id"], "⚠ " + _(
                        'Le modifiche ai file non si potranno annullare: {error}').format(
                        error=e.strerror or e))
            chat["messages"].append(msg)
            chat["updated"] = config.now()
            self._persist(chat)
        else:
            journal.discard()

    def _finalize(self, outcome: str, stats: str | None) -> None:
        """Chiude la risposta, salva il testo prodotto e torna idle."""
        self.generation_finished.emit(self._active_id, outcome, stats or "", "")
        self._save_assistant(stats)
        self._set_idle()

    def _on_failed(self, err: str) -> None:
        if self._chat_stopped or self.sender() is not self._worker:
            return
        self._release_held()
        self.generation_finished.emit(self._active_id, "failed", "", err)
        # se il modello aveva già prodotto testo, conservalo nella conversazione
        self._save_assistant(None)
        self._set_idle()

    def stop(self) -> None:
        if self._phase == "search":
            # i segnali del worker annullato verranno scartati da _search_stopped
            self._search_stopped = True
            if self._search_worker is not None:
                self._search_worker.stop()
                self._workers.retire(self._search_worker)
                self._search_worker = None
            # il messaggio dell'utente è già in chat e salvato: resta, senza
            # risposta, come quando si ferma una generazione prima del testo
            self._pending_user = None
            self._search_msg = None
            self.search_finished.emit(self._active_id)
            self._set_idle()
            return
        if self._worker is not None:
            self._chat_stopped = True
            self._worker.stop()   # chiude la connessione: il worker termina subito
            self._workers.retire(self._worker)
            self._worker = None
        # una modifica in attesa vale come rifiutata: il thread dei tool riparte
        self._close_gate()
        if self._tool_worker is not None:
            # la chiamata in corso finisce da sola; il suo risultato si scarta
            self._chat_stopped = True
            self._tool_worker.stop()
            self._workers.retire(self._tool_worker)
            self._tool_worker = None
        if self._phase is None:
            return
        # i pochi caratteri tenuti per un possibile marcatore sono testo
        tail, self._tail = self._tail, ""
        self._emit_text(tail)
        # una scrittura approvata un attimo prima di Stop: si aspetta che
        # finisca (tiene il lucchetto) e finisce nel registro; se non è
        # ancora partita, il registro chiuso la ferma
        with self._journal.lock:
            changed = bool(self._journal)
            if not changed:
                self._journal.closed = True
        if self._pending_stream or changed:
            # conserva il testo già prodotto e le modifiche già fatte ai file
            # (la risposta porta il pulsante per annullarle)
            self._finalize("stopped", None)
        else:
            # scelta coerente con «niente contenuto, niente messaggio»: una
            # generazione interrotta durante il solo ragionamento non lascia
            # messaggio, quindi anche il pensiero va scartato
            self._pending_think = ""
            self.generation_finished.emit(self._active_id, "stopped", "", "")
            self._set_idle()

    def _set_idle(self) -> None:
        self._close_gate()
        self._tool_index = -1
        self._max_rounds = tools.MAX_ROUNDS
        self._active_id = None
        self._phase = None
        self._search_msg = None
        self._think_off_sent = False
        self._use_tools = False
        self._tools_level = "off"
        self._web_tool = False
        self._payload = None
        self._rounds = []
        self._open_round = None
        self._held = ""
        self._tail = ""
        self.busy_changed.emit(False)

    # ---------------------------------------------------------------- chiusura

    def shutdown(self) -> list:
        """Blocca nuovi invii e restituisce i worker da fermare e attendere."""
        self._closed = True
        self._chat_stopped = True
        self._search_stopped = True
        # chiudere l'app con una modifica in attesa vale come rifiuto: il
        # thread dei tool non resta fermo e si può attendere
        self._close_gate()
        return self._workers.all()

    def save_on_exit(self) -> None:
        """Dopo aver fermato i worker: una risposta che ha già cambiato dei
        file si salva, con il suo «Annulla», come dopo uno Stop."""
        if self._active_id is not None and self._journal:
            self._save_assistant(None)
        else:
            self._journal.close()
            self._journal.discard()


# id delle chat e degli annullamenti: diventano nomi di cartelle
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _undo_dir(chat_id: str, undo_id: str) -> Path:
    """Cartella delle copie per annullare una risposta."""
    if not (_SAFE_ID.match(chat_id) and _SAFE_ID.match(undo_id)):
        raise OSError("invalid id")
    return config.config_dir() / "undo" / chat_id / undo_id


def _assistant_messages(m: dict, full_results: bool) -> list[dict]:
    """Una risposta salvata, come messaggi per Ollama.

    Senza tool è un solo messaggio. Con i tool ogni giro diventa la risposta
    con le chiamate più un messaggio `tool` per risultato, poi il testo
    finale. `content` è il testo di tutti i giri di seguito (come lo vede
    l'utente): il finale è quello che resta dopo i testi dei giri. Con
    `full_results=False` i risultati si accorciano. I file delle chat si
    possono modificare a mano: un giro malformato si salta.
    """
    content = m.get("content", "")
    if not isinstance(content, str):
        content = ""
    rounds = m.get("tool_rounds")
    if not isinstance(rounds, list) or not rounds:
        return [{"role": "assistant", "content": content}]
    out: list[dict] = []
    used = 0
    for r in rounds:
        calls = r.get("calls") if isinstance(r, dict) else None
        if not isinstance(calls, list):
            continue
        calls = [c for c in calls if isinstance(c, dict) and isinstance(c.get("name"), str)]
        if not calls:
            continue
        text = r.get("content") if isinstance(r.get("content"), str) else ""
        used += len(text)
        echo, results = [], []
        for c in calls:
            args = c.get("arguments") if isinstance(c.get("arguments"), dict) else {}
            entry = {"function": {"name": c["name"], "arguments": args}}
            result = c.get("result") if isinstance(c.get("result"), str) else ""
            if not full_results:
                result = tools.shorten(result, tools.OLD_RESULT_CHARS)
            if c.get("undone"):
                # il modello deve sapere che il file non è più così
                result += "\n[The user later undid this change: it is no longer applied.]"
            tool_msg = {"role": "tool", "tool_name": c["name"], "content": result}
            if isinstance(c.get("id"), str):
                entry["id"] = tool_msg["tool_call_id"] = c["id"]
            echo.append(entry)
            results.append(tool_msg)
        out.append({"role": "assistant", "content": text, "tool_calls": echo})
        out.extend(results)
    out.append({"role": "assistant", "content": content[used:] if out else content})
    return out


# tool che escono dalla richiesta quando la ricerca web non risponde
WEB_TOOLS = ("web_search", "fetch_page")


def _add_system_note(messages: list[dict], note: str) -> None:
    """Aggiunge `note` al prompt di sistema (uno solo: alcuni template di
    Ollama leggono solo il primo), creandolo se manca."""
    if messages and messages[0].get("role") == "system":
        messages[0] = {**messages[0], "content": messages[0]["content"] + "\n\n" + note}
    else:
        messages.insert(0, {"role": "system", "content": note})


def _normalize_level(value) -> str:
    return tools.normalize_level(value)


def chat_tools_level(chat: dict) -> str:
    """Livello dei tool salvato nella conversazione ("off" se manca)."""
    return tools.normalize_level(chat.get("tools"))


def _set_tools_level(chat: dict, level) -> None:
    """Il campo "tools" c'è solo se acceso; la sola lettura resta True, come
    nella 0.4, così le chat che non scrivono restano leggibili da lì."""
    value = tools.stored_level(level)
    if value is None:
        chat.pop("tools", None)
    else:
        chat["tools"] = value

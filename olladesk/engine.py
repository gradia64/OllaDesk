"""Motore di chat: stato delle conversazioni, generazione e persistenza.

QObject senza widget: la finestra (e, dalla 0.3, il server companion) ne
sono client. Ogni segnale porta il `chat_id`: un client mostra solo gli
eventi della conversazione che sta guardando.

Regola dei thread: i metodi si chiamano solo dal thread Qt principale.
Una sola elaborazione alla volta, globale (ricerca web + generazione).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import config, context, secrets_store, web_search
from .ollama_client import ApiWorker, ChatWorker, format_stats
from .workers import WorkerRegistry


class ChatEngine(QObject):
    # --- conversazioni
    chats_changed = Signal()                  # indice cambiato (titoli, ordine, eliminazioni)
    chat_deleted = Signal(str)                # chat_id eliminata (dal PC o dal telefono)
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
    # --- server Ollama e modelli
    status_changed = Signal(bool, str)        # online, versione
    models_loading = Signal(bool)             # elenco modelli in caricamento
    models_changed = Signal(list)             # nomi da mostrare ([] = nessun modello)

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
        self._pending_stream = ""
        self._pending_think = ""              # ragionamento ricevuto nel turno corrente
        self._chat_stopped = False            # scarta i segnali del worker dopo uno stop
        self._search_stopped = False
        self._closed = False
        self._status_worker: ApiWorker | None = None
        self._models_worker: ApiWorker | None = None
        self._models: list[dict] = []
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
            }
            for m in data.get("models", [])
        ]
        self._models.sort(key=lambda m: m["name"].lower())
        self.models_changed.emit(self.model_names())

    def _on_models_fail(self, _err: str) -> None:
        self.models_loading.emit(False)
        self.models_changed.emit([])

    # ---------------------------------------------------------- conversazioni

    def _persist(self, chat: dict) -> None:
        """Salva la conversazione (file dedicato + indice)."""
        if not config.save_chat(chat):
            self.notice.emit(
                chat["id"],
                "⚠ Impossibile salvare la conversazione su disco "
                f"({config.chats_dir()}): spazio esaurito o permessi mancanti?",
            )
        # tiene allineato l'elenco in memoria (ordine e titolo nella sidebar)
        entry = {"id": chat["id"], "title": chat.get("title", "Conversazione"),
                 "model": chat.get("model", ""), "updated": chat.get("updated", 0)}
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

    def delete_chat(self, chat_id: str) -> None:
        if chat_id == self._active_id:
            self.stop()
        config.delete_chat(chat_id)
        self._cache.pop(chat_id, None)
        self._chats = [c for c in self._chats if c["id"] != chat_id]
        self.chat_deleted.emit(chat_id)
        self.chats_changed.emit()

    # ------------------------------------------------------------------ invio

    def send(self, chat_id: str | None, text: str, model: str, *,
             attachments=(), web: bool = False, think: bool = True) -> str | None:
        """Aggiunge un messaggio utente e avvia la risposta.

        `chat_id` è una conversazione esistente o un id nuovo (`new_chat_id`):
        la conversazione nasce al primo messaggio salvato. Restituisce l'id,
        oppure None se un'altra elaborazione è già in corso.
        """
        if self.busy() or self._closed:
            return None
        chat_id = chat_id or self.new_chat_id()

        # validazione rapida: il contenuto dei file viene letto alla generazione
        meta, warnings = [], []
        for a in attachments:
            p = Path(a["path"])
            if not p.exists():
                warnings.append(f"file non leggibile: {a['name']}")
                continue
            meta.append({"path": str(p), "name": a["name"], "kind": a["kind"]})
        if warnings:
            self.notice.emit(chat_id, "⚠ " + "\n⚠ ".join(warnings))

        if web and not web_search.make_query(text):
            self.notice.emit(
                chat_id, "⚠ Ricerca web saltata: scrivi una domanda insieme agli allegati."
            )
            web = False

        self._pending_user = {
            "chat_id": chat_id,
            "display": text,
            "attachments_meta": meta,
            "attachments": [a["name"] for a in meta],
            "image_paths": [a["path"] for a in meta if a["kind"] == "image"],
            "web": web,
            "model": model,
            "think": think,
        }
        self._active_id = chat_id
        if web:
            self._phase = "search"
            self.busy_changed.emit(True)
            self._start_web_search()
        else:
            self._phase = "chat"
            self.busy_changed.emit(True)
            self._commit_user_message()
        return chat_id

    # ------------------------------------------------------------- ricerca web

    def _start_web_search(self) -> None:
        self._search_stopped = False
        self.search_started.emit(self._active_id)
        s = self.settings
        w = web_search.WebSearchWorker(
            web_search.make_query(self._pending_user["display"]),
            int(s.get("web_results", 5)),
            provider=s.get("web_provider", "duckduckgo"),
            api_key=secrets_store.load_api_key() or s.get("web_api_key", ""),
            searxng_url=s.get("web_searxng_url", ""),
            parent=self,
        )
        w.ready.connect(self._on_web_results)
        w.failed.connect(self._on_web_failed)
        w.finished.connect(w.deleteLater)
        w.finished.connect(self._workers.clear_ref(self, "_search_worker", w))
        self._search_worker = w
        self._workers.track(w)
        w.start()

    def _on_web_results(self, block: str, _query: str) -> None:
        if self._search_stopped or self.sender() is not self._search_worker:
            return
        self.search_finished.emit(self._active_id)
        if self._pending_user is not None:
            self._pending_user["web_block"] = block
            self._phase = "chat"
            self._commit_user_message()

    def _on_web_failed(self, err: str) -> None:
        if self._search_stopped or self.sender() is not self._search_worker:
            return
        self.search_finished.emit(self._active_id)
        self.notice.emit(
            self._active_id,
            f"⚠ Ricerca web non riuscita ({err}).\nProcedo senza i risultati web.",
        )
        if self._pending_user is not None:
            self._phase = "chat"
            self._commit_user_message()

    # ------------------------------------------------------------------ commit

    def _commit_user_message(self) -> None:
        p, self._pending_user = self._pending_user, None
        if p is None:
            return
        chat_id = p["chat_id"]
        model = p["model"]
        chat = self.chat(chat_id)
        if chat is None:
            title = p["display"][:48] + ("…" if len(p["display"]) > 48 else "")
            chat = {
                "id": chat_id,
                "title": title or "Allegati",
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
        self.user_message_added.emit(chat_id, msg)
        self._persist(chat)
        self._start_generation(chat, p["think"])

    def _start_generation(self, chat: dict, think: bool) -> None:
        chat_id = chat["id"]
        model = chat["model"]
        s = self.settings

        messages: list[dict] = []
        warnings: list[str] = []
        if s["system_prompt"]:
            messages.append({"role": "system", "content": s["system_prompt"]})
        history = context.history_window(chat["messages"], s["history_limit"])
        last_idx = len(history) - 1
        for i, m in enumerate(history):
            if m["role"] == "assistant":
                messages.append({"role": "assistant", "content": m.get("content", "")})
                continue
            # il contenuto completo di allegati/ricerca web viaggia solo con
            # l'ultimo turno utente: i precedenti lasciano un segnaposto
            full = i == last_idx
            content, w = context.build_api_content(m, include_full=full)
            warnings.extend(w)
            entry = {"role": "user", "content": content}
            if full and m.get("image_paths"):
                imgs = []
                for path in m["image_paths"]:
                    b64, note = context.image_to_b64(path)
                    if note:
                        warnings.append(note)
                    if b64:
                        imgs.append(b64)
                if imgs:
                    entry["images"] = imgs
            messages.append(entry)

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

        self._pending_stream = ""
        self._pending_think = ""
        self._chat_stopped = False
        self.generation_started.emit(chat_id)

        w = ChatWorker(s["host"], payload, self)
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
        self._pending_stream += chunk
        self.text_chunk.emit(self._active_id, chunk)

    def _on_think_chunk(self, chunk: str) -> None:
        if self._chat_stopped or self.sender() is not self._worker:
            return
        self._pending_think += chunk
        self.think_chunk.emit(self._active_id, chunk)

    def _on_done(self, done: dict) -> None:
        if self._chat_stopped or self.sender() is not self._worker:
            return
        self._finalize("done", format_stats(done))

    def _save_assistant(self, stats: str | None) -> None:
        """Salva il testo prodotto nel turno (anche parziale), se c'è."""
        text, thinking = self._pending_stream, self._pending_think
        self._pending_stream = ""
        self._pending_think = ""
        chat = self.chat(self._active_id)
        if text and chat is not None:
            msg = {"role": "assistant", "content": text, "ts": config.now()}
            if thinking:
                msg["thinking"] = thinking
            if stats:
                msg["stats"] = stats
            chat["messages"].append(msg)
            chat["updated"] = config.now()
            self._persist(chat)

    def _finalize(self, outcome: str, stats: str | None) -> None:
        """Chiude la risposta, salva il testo prodotto e torna idle."""
        self.generation_finished.emit(self._active_id, outcome, stats or "", "")
        self._save_assistant(stats)
        self._set_idle()

    def _on_failed(self, err: str) -> None:
        if self._chat_stopped or self.sender() is not self._worker:
            return
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
            self._pending_user = None
            self.search_finished.emit(self._active_id)
            self._set_idle()
            return
        if self._worker is not None:
            self._chat_stopped = True
            self._worker.stop()   # chiude la connessione: il worker termina subito
            self._workers.retire(self._worker)
            self._worker = None
        if self._phase is None:
            return
        if self._pending_stream:
            self._finalize("stopped", None)   # conserva il testo già prodotto
        else:
            # scelta coerente con «niente contenuto, niente messaggio»: una
            # generazione interrotta durante il solo ragionamento non lascia
            # messaggio, quindi anche il pensiero va scartato
            self._pending_think = ""
            self.generation_finished.emit(self._active_id, "stopped", "", "")
            self._set_idle()

    def _set_idle(self) -> None:
        self._active_id = None
        self._phase = None
        self.busy_changed.emit(False)

    # ---------------------------------------------------------------- chiusura

    def shutdown(self) -> list:
        """Blocca nuovi invii e restituisce i worker da fermare e attendere."""
        self._closed = True
        self._chat_stopped = True
        self._search_stopped = True
        return self._workers.all()

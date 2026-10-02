"""Finestra principale: sidebar + area chat + barra modelli/stato.

Gestisce l'invio con allegati (testo/PDF inlinati, immagini in base64) e la
ricerca web opzionale (i risultati vengono allegati al contesto prima della
richiesta a Ollama).
"""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__, app_update, config, context, secrets_store, server_share, theme, web_search
from .ollama_client import ApiWorker, ChatWorker, format_stats, shutdown_workers
from .widgets.chat_area import ChatArea
from .widgets.model_manager import ModelManagerDialog
from .widgets.settings_dialog import SettingsDialog
from .widgets.sidebar import ChatSidebar


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OllaDesk")
        self.resize(1120, 740)
        self.setMinimumSize(860, 560)

        self.settings = config.load_settings()
        self.chats: list[dict] = config.load_chats()
        self.current_chat: dict | None = None
        self._worker: ChatWorker | None = None
        self._search_worker: web_search.WebSearchWorker | None = None
        self._status_worker: ApiWorker | None = None
        self._models_worker: ApiWorker | None = None
        self._app_update_worker: app_update.AppUpdateCheckWorker | None = None
        self._app_update: dict | None = None   # release più recente trovata
        self._models: list[dict] = []
        self._online: bool | None = None
        self._preferred_model: str | None = None
        self._pending_stream: str = ""
        self._pending_think: str = ""   # ragionamento ricevuto nel turno corrente
        self._pending_user: dict | None = None
        self._chat_stopped = False     # scarta i segnali del worker dopo uno stop
        self._search_stopped = False
        self._running_workers: list = []
        self._zombie_workers: list = []   # worker in arresto, non bloccano la UI
        self._version = "?"
        self._resolved_theme = theme.resolve_theme(self.settings["theme"])
        self.tray: QSystemTrayIcon | None = None
        self._really_quit = False      # True solo da «Esci» nel menu della tray
        self._tray_hint_shown = False
        self._share_note_shown = False   # la nota «external» vale una volta per sessione
        self._share = server_share.SharedOllamaServer(self)
        self._share.state_changed.connect(self._on_share_state)

        self._build_ui()
        self._connect_signals()
        self._shortcuts()

        # il tema è già applicato da olladesk.app prima di creare la finestra
        self.chat_area.set_send_on_enter(self.settings["send_on_enter"])
        self.chat_area.set_thinking(self.settings["thinking"])

        # segue il cambio tema scuro/chiaro del desktop (modalità "Sistema")
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_scheme_changed)
        except Exception:
            pass

        # avvio: pagina predefinita di benvenuto; le conversazioni salvate si
        # aprono solo se l'utente le sceglie dalla colonna di sinistra
        self.sidebar.set_chats(self.chats, None)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(30_000)
        self._status_timer.timeout.connect(self._check_server)
        self._status_timer.start()
        QTimer.singleShot(150, self._check_server)
        QTimer.singleShot(150, self._refresh_models)
        # dopo l'avvio, per non rallentarlo: al massimo una volta al giorno
        QTimer.singleShot(3000, self._maybe_check_app_update)
        # tray e condivisione API: attivati dopo il primo disegno
        self._sync_tray()
        QTimer.singleShot(800, self._sync_share)

    # -------------------------------------------------------------------- UI

    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = ChatSidebar(central)
        root.addWidget(self.sidebar)

        self.main_pane = QWidget(central)
        pane_lay = QVBoxLayout(self.main_pane)
        pane_lay.setContentsMargins(0, 0, 0, 0)
        pane_lay.setSpacing(0)

        # barra superiore
        top = QFrame(self.main_pane)
        top.setObjectName("topBar")
        top.setFixedHeight(52)
        top_lay = QHBoxLayout(top)
        top_lay.setContentsMargins(10, 6, 14, 6)
        top_lay.setSpacing(8)

        self.burger = QToolButton(top)
        self.burger.setText("☰")
        self.burger.setToolTip("Mostra/nascondi la barra laterale (Ctrl+B)")
        top_lay.addWidget(self.burger)

        self.model_combo = QComboBox(top)
        self.model_combo.setMinimumWidth(260)
        self.model_combo.setToolTip("Modello da usare per la conversazione")
        top_lay.addWidget(self.model_combo, 0)

        self.reload_models_btn = QToolButton(top)
        self.reload_models_btn.setText("↻")
        self.reload_models_btn.setToolTip("Ricarica l'elenco dei modelli installati")
        top_lay.addWidget(self.reload_models_btn)

        top_lay.addStretch(1)
        # indicatore della condivisione API in rete (visibile solo se attiva)
        self.share_btn = QToolButton(top)
        self.share_btn.setText("🔗")
        self.share_btn.hide()
        top_lay.addWidget(self.share_btn)
        # compare solo quando GitHub ha una versione più recente di OllaDesk
        self.app_update_btn = QToolButton(top)
        self.app_update_btn.setObjectName("appUpdateBtn")
        self.app_update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.app_update_btn.hide()
        top_lay.addWidget(self.app_update_btn)
        self.status_dot = QLabel("●", top)
        self.status_dot.setProperty("status", "offline")
        self.status_label = QLabel("connessione…", top)
        self.status_label.setObjectName("statusLabel")
        top_lay.addWidget(self.status_dot)
        top_lay.addWidget(self.status_label)

        pane_lay.addWidget(top)
        pane_lay.addWidget(self._sep(top))

        self.chat_area = ChatArea(
            self._resolved_theme, self.settings["show_timestamps"], self.main_pane
        )
        pane_lay.addWidget(self.chat_area, 1)

        root.addWidget(self.main_pane, 1)
        self.setCentralWidget(central)

    @staticmethod
    def _sep(parent: QWidget) -> QFrame:
        line = QFrame(parent)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background: rgba(128,128,128,0.25); max-height: 1px; border: none;")
        return line

    def _connect_signals(self) -> None:
        self.sidebar.newChatRequested.connect(self._new_chat)
        self.sidebar.chatSelected.connect(self._open_chat)
        self.sidebar.chatRenamed.connect(self._rename_chat)
        self.sidebar.chatDeleted.connect(self._delete_chat)
        self.sidebar.modelsRequested.connect(self._open_models)
        self.sidebar.settingsRequested.connect(self.open_settings)

        self.chat_area.sendRequested.connect(self._on_send)
        self.chat_area.stopRequested.connect(self._on_stop)
        self.chat_area.thinkingToggled.connect(self._on_thinking_toggled)

        self.burger.clicked.connect(self._toggle_sidebar)
        self.reload_models_btn.clicked.connect(self._refresh_models)
        self.app_update_btn.clicked.connect(self._show_app_update)
        self.share_btn.clicked.connect(self._copy_share_urls)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)

    def _shortcuts(self) -> None:
        for seq, fn in (
            ("Ctrl+N", self._new_chat),
            ("Ctrl+B", self._toggle_sidebar),
            ("Ctrl+,", self.open_settings),
            ("Ctrl+M", self._open_models),
        ):
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(fn)

    # ------------------------------------------------------------------- tema

    def _track_worker(self, w) -> None:
        """Tiene registro dei worker attivi per chiuderli tutti alla chiusura."""
        self._running_workers.append(w)
        w.finished.connect(lambda: self._untrack_worker(w))

    def _clear_ref(self, attr: str, w):
        """Slot per `finished`: azzera `attr` solo se punta ancora a `w`.

        Un worker annullato può terminare DOPO che ne è partito uno nuovo:
        un azzeramento incondizionato scollegherebbe quello nuovo.
        """
        def clear() -> None:
            if getattr(self, attr, None) is w:
                setattr(self, attr, None)
        return clear

    def _untrack_worker(self, w) -> None:
        try:
            self._running_workers.remove(w)
        except ValueError:
            pass

    def _retire_worker(self, w) -> None:
        """Sgancia un worker in arresto: la UI non attende più la sua morte.

        Il thread può restare bloccato su un socket finché scatta il timeout
        di rete: gli signal emessi in ritardo vengono scartati e la UI è
        subito libera di fare nuove richieste.
        """
        if w is None:
            return
        self._untrack_worker(w)
        self._zombie_workers.append(w)
        w.finished.connect(lambda: self._forget_zombie(w))

    def _forget_zombie(self, w) -> None:
        try:
            self._zombie_workers.remove(w)
        except ValueError:
            pass

    def _on_scheme_changed(self, *_a) -> None:
        if self.settings["theme"] == "system":
            self._apply_theme_now()

    def _apply_theme_now(self) -> None:
        self._resolved_theme = theme.apply_theme(
            QGuiApplication.instance(), self.settings["theme"], self.settings["font_size"]
        )
        # aggiorna tema/orario/icone dell'area chat PRIMA di ricreare i messaggi
        self.chat_area.refresh_theme(self._resolved_theme, self.settings["show_timestamps"])
        self._rerender_messages()

    def _rerender_messages(self) -> None:
        # durante la generazione la bolla aperta non è ancora in current_chat:
        # ricrearla la distruggerebbe (refresh_theme ha già aggiornato i colori)
        if self.current_chat and not self._busy():
            self.chat_area.clear_messages()
            for m in self.current_chat["messages"]:
                self.chat_area.add_message(
                    m["role"], m.get("display", m.get("content", "")), m.get("ts"),
                    m.get("attachments"), bool(m.get("web")), m.get("stats"),
                    thinking=m.get("thinking", ""),
                )

    # ------------------------------------------------------------- connessione

    def _check_server(self) -> None:
        if self._status_worker is not None:
            return
        self._status_worker = ApiWorker(self.settings["host"], "/api/version", self)
        self._status_worker.ready.connect(self._on_server_ok)
        self._status_worker.failed.connect(self._on_server_fail)
        self._status_worker.finished.connect(self._status_worker.deleteLater)
        self._status_worker.finished.connect(self._clear_ref("_status_worker", self._status_worker))
        self._track_worker(self._status_worker)
        self._status_worker.start()

    def _on_server_ok(self, data: object) -> None:
        version = (data or {}).get("version", "?") if isinstance(data, dict) else "?"
        self._version = version
        was_online = self._online
        self._online = True
        self.status_dot.setProperty("status", "online")
        self.status_label.setText(f"Ollama {html.escape(version)}")
        self._repolish(self.status_dot)
        if not was_online:
            self._refresh_models()

    def _on_server_fail(self, _err: str) -> None:
        self._online = False
        self.status_dot.setProperty("status", "offline")
        self.status_label.setText("offline")
        self._repolish(self.status_dot)
        if not self._models:
            self._set_models([])

    @staticmethod
    def _repolish(w: QWidget) -> None:
        w.style().unpolish(w)
        w.style().polish(w)

    # ----------------------------------------------------------------- modelli

    def model_names(self) -> list[str]:
        return [m["name"] for m in self._models]

    def _refresh_models(self) -> None:
        if self._models_worker is not None:
            return
        self.reload_models_btn.setEnabled(False)
        self._models_worker = ApiWorker(self.settings["host"], "/api/tags", self)
        self._models_worker.ready.connect(self._on_models)
        self._models_worker.failed.connect(self._on_models_fail)
        self._models_worker.finished.connect(self._models_worker.deleteLater)
        self._models_worker.finished.connect(self._clear_ref("_models_worker", self._models_worker))
        self._track_worker(self._models_worker)
        self._models_worker.start()

    def _on_models(self, data: object) -> None:
        self.reload_models_btn.setEnabled(True)
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
        self._set_models(self.model_names())

    def _on_models_fail(self, _err: str) -> None:
        self.reload_models_btn.setEnabled(True)
        self._set_models([])

    def _set_models(self, names: list[str]) -> None:
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if not names:
            self.model_combo.addItem("— nessun modello —")
            self.model_combo.setEnabled(False)
        else:
            self.model_combo.setEnabled(True)
            for m in self._models:
                det = m.get("details", {})
                label = m["name"]
                extra = " · ".join(x for x in (det.get("parameter_size", ""), det.get("quantization_level", "")) if x)
                self.model_combo.addItem(label, m["name"])
                if extra:
                    self.model_combo.setItemData(
                        self.model_combo.count() - 1, f"{label}\n{extra}", Qt.ItemDataRole.ToolTipRole
                    )
            preferred = self._preferred_model
            if preferred:
                idx = self.model_combo.findData(preferred)
                if idx >= 0:
                    self.model_combo.setCurrentIndex(idx)
            self._preferred_model = self.model_combo.currentData()
        self.model_combo.blockSignals(False)

    def _on_model_changed(self, _idx: int) -> None:
        name = self.model_combo.currentData()
        if name:
            self._preferred_model = name

    def current_model(self) -> str | None:
        return self.model_combo.currentData() if self.model_combo.isEnabled() else None

    def _open_models(self) -> None:
        if self._busy():
            self.chat_area.add_system_note(
                "Attendi la fine della risposta (o premi ■ per interrompere) prima di gestire i modelli."
            )
            return
        dlg = ModelManagerDialog(self.settings["host"], self, self.current_model())
        dlg.exec()
        changed = dlg.changed
        # distrutto a ogni chiusura (prima restava figlio della finestra per
        # sempre); i worker ancora bloccati sono già stati parcheggiati
        dlg.deleteLater()
        if changed:
            self._refresh_models()

    # -------------------------------------------------------------- conversazioni

    def _persist_chat(self) -> None:
        """Salva la conversazione corrente (file dedicato + indice)."""
        c = self.current_chat
        if c is None:
            return
        if not config.save_chat(c):
            self.chat_area.add_system_note(
                "⚠ Impossibile salvare la conversazione su disco "
                f"({config.chats_dir()}): spazio esaurito o permessi mancanti?"
            )
        # tiene allineato l'elenco in memoria (ordine e titolo nella sidebar)
        entry = {"id": c["id"], "title": c.get("title", "Conversazione"),
                 "model": c.get("model", ""), "updated": c.get("updated", 0)}
        self.chats = [e for e in self.chats if e["id"] != c["id"]] + [entry]

    def _new_chat(self) -> None:
        if self._busy():
            return
        self.current_chat = None
        self.chat_area.clear_messages()
        self.sidebar.set_chats(self.chats, None)
        self.chat_area.focus_input()

    def _open_chat(self, chat_id: str) -> None:
        if self._busy():
            return
        chat = config.load_chat(chat_id)
        if chat is None:
            # elencata ma il file è mancante: ripulisci l'indice
            config.delete_chat(chat_id)
            self.chats = [c for c in self.chats if c["id"] != chat_id]
            if self.current_chat and self.current_chat["id"] == chat_id:
                self.current_chat = None
                self.chat_area.clear_messages()
            self.sidebar.set_chats(self.chats, self.current_chat["id"] if self.current_chat else None)
            self.chat_area.add_system_note("⚠ Conversazione non leggibile dal disco: rimossa dall'elenco.")
            return
        self.current_chat = chat
        self.chat_area.clear_messages()
        for m in chat["messages"]:
            self.chat_area.add_message(
                m["role"], m.get("display", m.get("content", "")), m.get("ts"),
                m.get("attachments"), bool(m.get("web")), m.get("stats"),
                thinking=m.get("thinking", ""),
            )
        # ripristina il modello con cui era nata la conversazione
        model = chat.get("model")
        if model and self.model_combo.isEnabled():
            idx = self.model_combo.findData(model)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
        self.sidebar.set_chats(self.chats, chat_id)
        self.chat_area.focus_input()

    def _rename_chat(self, chat_id: str, title: str) -> None:
        if self.current_chat and self.current_chat["id"] == chat_id:
            self.current_chat["title"] = title.strip()
            self._persist_chat()
        else:
            config.rename_chat(chat_id, title.strip())
        self.chats = config.load_chats()
        self.sidebar.set_chats(self.chats, chat_id)

    def _delete_chat(self, chat_id: str) -> None:
        config.delete_chat(chat_id)
        self.chats = [c for c in self.chats if c["id"] != chat_id]
        if self.current_chat and self.current_chat["id"] == chat_id:
            self.current_chat = None
            self.chat_area.clear_messages()
        self.sidebar.set_chats(self.chats, None)

    def _toggle_sidebar(self) -> None:
        self.sidebar.setVisible(not self.sidebar.isVisible())

    # ------------------------------------------------------------- invio/stream

    def _busy(self) -> bool:
        for w in (self._worker, self._search_worker):
            if w is not None and w.isRunning():
                return True
        return False

    def _on_send(self, text: str) -> None:
        if self._busy():
            return
        model = self.current_model()
        if not model:
            self.chat_area.add_system_note(
                "Nessun modello disponibile.\n"
                "Scaricalo dalla sezione «Modelli» (Ctrl+M) o controlla che Ollama sia avviato."
            )
            return

        atts = self.chat_area.attachments()
        web_on = self.chat_area.web_search_active()

        # validazione rapida: il contenuto dei file viene letto alla generazione
        meta, warnings = [], []
        for a in atts:
            p = Path(a["path"])
            if not p.exists():
                warnings.append(f"file non leggibile: {a['name']}")
                continue
            meta.append({"path": str(p), "name": a["name"], "kind": a["kind"]})
        if warnings:
            self.chat_area.add_system_note("⚠ " + "\n⚠ ".join(warnings))

        self._pending_user = {
            "display": text,
            "attachments_meta": meta,
            "attachments": [a["name"] for a in meta],
            "image_paths": [a["path"] for a in meta if a["kind"] == "image"],
            "web": web_on,
            "model": model,
        }
        self.chat_area.clear_attachments()

        if web_on and not web_search.make_query(text):
            self.chat_area.add_system_note(
                "⚠ Ricerca web saltata: scrivi una domanda insieme agli allegati."
            )
            web_on = self._pending_user["web"] = False
        if web_on:
            self._start_web_search()
        else:
            self._commit_user_message()

    # ----------------------------------------------------------- ricerca web

    def _start_web_search(self) -> None:
        self._search_stopped = False
        self.chat_area.set_streaming(True)
        self.sidebar.set_busy(True)
        placeholder = self.chat_area.begin_stream(config.now())
        placeholder.show_status("🌐 Ricerca web in corso…")

        self._search_worker = web_search.WebSearchWorker(
            web_search.make_query(self._pending_user["display"]),
            int(self.settings.get("web_results", 5)),
            provider=self.settings.get("web_provider", "duckduckgo"),
            api_key=secrets_store.load_api_key() or self.settings.get("web_api_key", ""),
            searxng_url=self.settings.get("web_searxng_url", ""),
            parent=self,
        )
        self._search_worker.ready.connect(self._on_web_results)
        self._search_worker.failed.connect(self._on_web_failed)
        self._search_worker.finished.connect(self._search_worker.deleteLater)
        self._search_worker.finished.connect(self._clear_ref("_search_worker", self._search_worker))
        self._track_worker(self._search_worker)
        self._search_worker.start()

    def _on_web_results(self, block: str, _query: str) -> None:
        if self._search_stopped or self.sender() is not self._search_worker:
            return
        self.chat_area.end_stream(discard_empty=True)
        if self._pending_user is not None:
            self._pending_user["web_block"] = block
            self._commit_user_message()

    def _on_web_failed(self, err: str) -> None:
        if self._search_stopped or self.sender() is not self._search_worker:
            return
        self.chat_area.end_stream(discard_empty=True)
        self.chat_area.add_system_note(
            f"⚠ Ricerca web non riuscita ({err}).\nProcedo senza i risultati web."
        )
        if self._pending_user is not None:
            self._commit_user_message()

    # ------------------------------------------------------------------ commit

    def _commit_user_message(self) -> None:
        p, self._pending_user = self._pending_user, None
        if p is None:
            return
        model = p["model"]
        if self.current_chat is None:
            title = p["display"][:48] + ("…" if len(p["display"]) > 48 else "")
            self.current_chat = {
                "id": config.new_chat_id(),
                "title": title or "Allegati",
                "model": model,
                "updated": config.now(),
                "messages": [],
            }

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
        self.current_chat["messages"].append(msg)
        self.current_chat["model"] = model
        self.current_chat["updated"] = ts
        self.chat_area.add_message(
            "user", p["display"], ts, p["attachments"] or None, p["web"]
        )
        self.chat_area.clear_input()
        self._persist_chat()
        self.sidebar.set_chats(self.chats, self.current_chat["id"])
        self._start_generation()

    def _start_generation(self) -> None:
        model = self.current_chat["model"]
        s = self.settings

        messages: list[dict] = []
        warnings: list[str] = []
        if s["system_prompt"]:
            messages.append({"role": "system", "content": s["system_prompt"]})
        history = context.history_window(self.current_chat["messages"], s["history_limit"])
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
                for p in m["image_paths"]:
                    b64, note = context.image_to_b64(p)
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
            self.chat_area.add_system_note("⚠ " + "\n⚠ ".join(warnings))

        payload = {
            "model": model,
            "messages": messages,
            "stream": bool(s["stream"]),
            "options": options,
        }
        # con il thinking attivo il campo si omette (vale il predefinito del
        # server); solo quando l'utente lo disattiva si invia "think": false
        if not s.get("thinking", True):
            payload["think"] = False

        self._pending_stream = ""
        self._pending_think = ""
        self._chat_stopped = False
        self.chat_area.begin_stream(config.now())

        self._worker = ChatWorker(self.settings["host"], payload, self)
        self._worker.chunk.connect(self._on_chunk)
        self._worker.think_chunk.connect(self._on_think_chunk)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.finished.connect(self._clear_ref("_worker", self._worker))
        self._track_worker(self._worker)
        self._worker.start()

        self.chat_area.set_streaming(True)
        self.sidebar.set_busy(True)

    def _on_chunk(self, chunk: str) -> None:
        if self._chat_stopped or self.sender() is not self._worker:
            return
        self._pending_stream += chunk
        self.chat_area.stream_text(chunk)

    def _on_think_chunk(self, chunk: str) -> None:
        if self._chat_stopped or self.sender() is not self._worker:
            return
        self._pending_think += chunk
        self.chat_area.stream_thinking(chunk)

    def _on_done(self, done: dict) -> None:
        if self._chat_stopped or self.sender() is not self._worker:
            return
        self._finalize_assistant(format_stats(done))

    def _finalize_assistant(self, stats: str | None) -> None:
        """Chiude la bolla, salva il testo prodotto e torna idle."""
        self.chat_area.end_stream(stats)
        text = self._pending_stream
        thinking = self._pending_think
        self._pending_stream = ""
        self._pending_think = ""
        if text and self.current_chat is not None:
            msg = {"role": "assistant", "content": text, "ts": config.now()}
            if thinking:
                msg["thinking"] = thinking
            if stats:
                msg["stats"] = stats
            self.current_chat["messages"].append(msg)
            self.current_chat["updated"] = config.now()
            self._persist_chat()
            self.sidebar.set_chats(self.chats, self.current_chat["id"])
        self._set_idle()

    def _on_failed(self, err: str) -> None:
        if self._chat_stopped or self.sender() is not self._worker:
            return
        self.chat_area.end_stream(discard_empty=True)
        partial = self._pending_stream
        partial_think = self._pending_think
        self._pending_stream = ""
        self._pending_think = ""
        # se il modello aveva già prodotto testo, conservalo nella conversazione
        if partial and self.current_chat is not None:
            msg = {"role": "assistant", "content": partial, "ts": config.now()}
            if partial_think:
                msg["thinking"] = partial_think
            self.current_chat["messages"].append(msg)
            self.current_chat["updated"] = config.now()
            self._persist_chat()
            self.sidebar.set_chats(self.chats, self.current_chat["id"])
        extra = ""
        if "think" in err.lower():
            extra = (
                "\n\nSuggerimento: il modello potrebbe non accettare il campo «think»: "
                "riattiva il pulsante 🧠 nell'input."
            )
        self.chat_area.add_system_note(
            err + extra
            + "\n\nSuggerimento: avvia Ollama con `ollama serve` e verifica l'URL "
            "nelle impostazioni (Ctrl+,)."
        )
        self._set_idle()

    def _on_stop(self) -> None:
        if self._search_worker is not None and self._search_worker.isRunning():
            # ripristino immediato della UI: i segnali del worker annullato
            # verranno scartati da _search_stopped
            self._search_stopped = True
            self._search_worker.stop()
            self._retire_worker(self._search_worker)
            self._search_worker = None
            self.chat_area.end_stream(discard_empty=True)
            self._pending_user = None
            self._set_idle()
            return
        if self._worker is not None:
            self._chat_stopped = True
            self._worker.stop()   # chiude la connessione: il worker termina subito
            self._retire_worker(self._worker)
            self._worker = None
        if self._pending_stream and self.current_chat is not None:
            self._finalize_assistant(None)   # conserva il testo già prodotto
        else:
            # scelta coerente con «niente contenuto, niente messaggio»: una
            # generazione interrotta durante il solo ragionamento non lascia
            # messaggio, quindi anche il pensiero va scartato
            self._pending_think = ""
            self.chat_area.end_stream(discard_empty=True)
            self._set_idle()

    def _set_idle(self) -> None:
        self.chat_area.set_streaming(False)
        self.sidebar.set_busy(False)
        self.chat_area.focus_input()

    # ------------------------------------------------------- thinking on/off

    def _on_thinking_toggled(self, on: bool) -> None:
        self.settings["thinking"] = on
        config.save_settings(self.settings)

    # --------------------------------------------------------------- tray

    def _sync_tray(self) -> None:
        """Crea o rimuove l'icona della tray in base alle impostazioni."""
        if self.settings.get("tray_icon", True) and QSystemTrayIcon.isSystemTrayAvailable():
            if self.tray is None:
                self._build_tray()
        elif self.tray is not None:
            self.tray.hide()
            self.tray.deleteLater()
            self.tray = None

    def _build_tray(self) -> None:
        icon_path = Path(__file__).resolve().parent / "assets" / "olladesk.svg"
        self.tray = QSystemTrayIcon(QIcon(str(icon_path)), self)
        self.tray.setToolTip("OllaDesk")
        menu = QMenu(self)
        act_toggle = QAction("Mostra / nascondi", menu)
        act_toggle.triggered.connect(self._toggle_from_tray)
        menu.addAction(act_toggle)
        menu.addSeparator()
        act_quit = QAction("Esci", menu)
        act_quit.triggered.connect(self._quit_from_tray)
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.MiddleClick,
        ):
            self._toggle_from_tray()

    def _toggle_from_tray(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
            return
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self) -> None:
        self._really_quit = True
        self.close()

    # ------------------------------------------------- condivisione API rete

    def _sync_share(self) -> None:
        """Avvia o arresta il server Ollama condiviso secondo le impostazioni."""
        if self.settings.get("share_api"):
            self._share.start(self.settings["share_bind"], int(self.settings["share_port"]))
        else:
            self._share.stop()

    def _on_share_state(self, state: str, detail: str) -> None:
        port = int(self.settings.get("share_port", 11434))
        if state == "running":
            if self.settings.get("share_bind") == "127.0.0.1":
                tip = f"API Ollama attiva\n{detail}"
            else:
                urls = server_share.lan_urls(port)
                tip = (
                    "API Ollama condivisa in rete\n"
                    "Da smartphone/tablet usa uno di questi indirizzi:\n  "
                    + "\n  ".join(urls)
                    + f"\n(da questo PC: http://localhost:{port})"
                    if urls else f"API Ollama condivisa\n{detail}"
                )
            self.share_btn.setToolTip(tip)
            self.share_btn.show()
        elif state == "external":
            # il dettaglio del manager dice già se l'istanza esterna è davvero
            # raggiungibile dalla LAN o solo in locale (bind 127.0.0.1)
            self.share_btn.setToolTip(f"Condivisione API (istanza esterna)\n{detail}")
            self.share_btn.show()
            # nota in chat solo alla PRIMA segnalazione della sessione: con un
            # Ollama di sistema sempre attivo sarebbe rumore a ogni avvio
            if not self._share_note_shown:
                self._share_note_shown = True
                self.chat_area.add_system_note(f"⚠ Condivisione API: {detail}")
        elif state == "starting":
            self.share_btn.setToolTip(f"Condivisione API: {detail}")
            self.share_btn.show()
        elif state == "error":
            self.share_btn.setToolTip(f"Condivisione API: errore\n{detail}")
            self.share_btn.show()
            self.chat_area.add_system_note(f"⚠ Condivisione API non riuscita:\n{detail}")
        else:   # off
            self.share_btn.hide()

    def _copy_share_urls(self) -> None:
        # copia solo indirizzi davvero raggiungibili dagli altri dispositivi:
        # il processo nostro in ascolto sulle interfacce di rete, oppure
        # un'istanza esterna verificata dalla sonda LAN (es. Ollama di
        # sistema già configurato su 0.0.0.0)
        if not self._share.lan_shared:
            self.chat_area.add_system_note(
                "⚠ Nessun indirizzo di rete valido da copiare:\n"
                "chi serve la porta non è raggiungibile dalla rete (processo "
                "assente, bind solo locale o istanza esterna chiusa in locale): "
                "vedi il tooltip di 🔗."
            )
            return
        urls = server_share.lan_urls(int(self.settings.get("share_port", 11434)))
        if not urls:
            return
        QGuiApplication.clipboard().setText("\n".join(urls))
        self.chat_area.add_system_note(
            "📋 Indirizzi dell'API copiati negli appunti:\n" + "\n".join(urls)
        )

    # ------------------------------------------------ aggiornamenti OllaDesk

    def _maybe_check_app_update(self) -> None:
        if self._app_update_worker is not None or not app_update.due_for_check(self.settings):
            return
        w = app_update.AppUpdateCheckWorker(self)
        w.ready.connect(self._on_app_release)
        w.failed.connect(lambda _e: None)   # controllo silenzioso: si riprova al prossimo avvio
        w.finished.connect(w.deleteLater)
        w.finished.connect(self._clear_ref("_app_update_worker", w))
        self._app_update_worker = w
        self._track_worker(w)
        w.start()

    def _on_app_release(self, release: object) -> None:
        self.settings["app_update_last_check"] = config.now()
        config.save_settings(self.settings)
        self._set_app_update(app_update.newer_release(release if isinstance(release, dict) else None))

    def _set_app_update(self, release: dict | None) -> None:
        """Mostra (o nasconde) l'avviso di nuova versione nella barra superiore."""
        if release and release["version"] == self.settings.get("app_update_skip"):
            release = None
        self._app_update = release
        if release:
            self.app_update_btn.setText(f"⬆ OllaDesk {release['version']}")
            self.app_update_btn.setToolTip(
                f"È disponibile OllaDesk {release['version']} (in uso: {__version__})"
            )
        self.app_update_btn.setVisible(bool(release))

    def _show_app_update(self) -> None:
        release = self._app_update
        if not release:
            return
        method = app_update.install_method()
        box = QMessageBox(self)
        box.setWindowTitle("Aggiornamento di OllaDesk")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(
            f"È disponibile OllaDesk {release['version']} (in uso: {__version__})."
        )
        box.setInformativeText(app_update.update_hint(method, release["version"]))
        open_btn = box.addButton("Apri la pagina di download", QMessageBox.ButtonRole.AcceptRole)
        skip_btn = box.addButton("Salta questa versione", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Più tardi", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        box.deleteLater()
        if clicked is open_btn:
            url = release["url"] if method != "arch" else app_update.download_page(method)
            QDesktopServices.openUrl(QUrl(url))
        elif clicked is skip_btn:
            self.settings["app_update_skip"] = release["version"]
            config.save_settings(self.settings)
            self._set_app_update(None)

    # ---------------------------------------------------------------- impostazioni

    def open_settings(self) -> None:
        if self._busy():
            self.chat_area.add_system_note(
                "Attendi la fine della risposta (o premi ■ per interrompere) prima di aprire le impostazioni."
            )
            return
        dlg = SettingsDialog(
            self.settings, self.model_names, self.settings["theme"], self._version, self
        )
        dlg.applied.connect(self._apply_settings)
        # «Verifica ora» nelle impostazioni: l'esito aggiorna anche l'avviso
        dlg.appReleaseChecked.connect(self._on_app_release)
        dlg.exec()
        dlg.deleteLater()

    def _apply_settings(self, s: dict) -> None:
        self.settings = dict(s)
        if not s.get("app_update_check", True):
            self._set_app_update(None)   # controllo disattivato: via l'avviso
        self._apply_theme_now()
        self.chat_area.set_send_on_enter(s["send_on_enter"])
        self.chat_area.set_thinking(s.get("thinking", True))
        # ricontatta il server e ricarica i modelli
        self._online = None
        self._check_server()
        self._refresh_models()
        # tray e condivisione possono essere cambiate nelle impostazioni
        self._sync_tray()
        self._sync_share()

    # -------------------------------------------------------------------- chiusura

    def closeEvent(self, ev) -> None:  # noqa: N802 (API Qt)
        # con l'icona nella tray la chiusura riduce la finestra: l'app resta
        # attiva (stream e condivisione API inclusi); si esce davvero solo
        # con «Esci» dal menu della tray (o senza tray configurata)
        if (
            self.tray is not None
            and self.settings.get("close_to_tray", True)
            and not self._really_quit
        ):
            ev.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.showMessage(
                    "OllaDesk",
                    "Resta attivo nella tray: clic sull'icona per mostrare o "
                    "nascondere la finestra, «Esci» dal menu per chiudere.",
                    QSystemTrayIcon.MessageIcon.Information,
                    4000,
                )
            return
        if self.tray is not None:
            self.tray.hide()   # via l'icona subito: niente residui nel pannello        # arresta l'eventuale server condiviso avviato da noi: al prossimo
        # avvio `_sync_share` lo riporta su se l'opzione è ancora attiva
        self._share.stop()
        # ferma e attende (con limite) TUTTI i worker: un QThread distrutto
        # mentre è in esecuzione fa abortire il processo. Gli "zombie" bloccati
        # su un socket muoiono al loro timeout di rete.
        # un'unica scadenza per tutti: prima 1,5 s per OGNI worker in fila
        shutdown_workers(list(self._running_workers) + list(self._zombie_workers))
        super().closeEvent(ev)
        # UNICO punto di uscita dell'app (quitOnLastWindowClosed è sempre
        # False, vedi app.py): «Esci» dalla tray, SIGTERM/SIGINT, logout di
        # sessione e X senza tray passano tutti di qui. Il quit è differito di
        # un ciclo perché chiamato prima che il ciclo eventi parta non ha
        # effetto (il gestore SIGTERM può chiudere prima di exec())
        QTimer.singleShot(0, QApplication.quit)

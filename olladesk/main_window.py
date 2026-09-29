"""Finestra principale: sidebar + area chat + barra modelli/stato.

Gestisce l'invio con allegati (testo/PDF inlinati, immagini in base64) e la
ricerca web opzionale (i risultati vengono allegati al contesto prima della
richiesta a Ollama).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import config, context, theme, web_search
from .ollama_client import ApiWorker, ChatWorker, format_stats
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
        self._models: list[dict] = []
        self._online: bool | None = None
        self._preferred_model: str | None = None
        self._pending_stream: str = ""
        self._pending_user: dict | None = None
        self._version = "?"
        self._resolved_theme = theme.resolve_theme(self.settings["theme"])

        self._build_ui()
        self._connect_signals()
        self._shortcuts()

        theme.apply_theme(QGuiApplication.instance(), self.settings["theme"], self.settings["font_size"])
        self.chat_area.set_send_on_enter(self.settings["send_on_enter"])

        # segue il cambio tema scuro/chiaro del desktop (modalità "Sistema")
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_scheme_changed)
        except Exception:
            pass

        # avvio: seleziona l'ultima conversazione, poi contatta il server
        if self.chats:
            self.current_chat = sorted(self.chats, key=lambda c: c.get("updated", 0))[-1]
            self._open_chat(self.current_chat["id"])
        else:
            self.sidebar.set_chats([], None)
        self.sidebar.set_chats(self.chats, self.current_chat["id"] if self.current_chat else None)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(30_000)
        self._status_timer.timeout.connect(self._check_server)
        self._status_timer.start()
        QTimer.singleShot(150, self._check_server)
        QTimer.singleShot(150, self._refresh_models)

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

        self.burger.clicked.connect(self._toggle_sidebar)
        self.reload_models_btn.clicked.connect(self._refresh_models)
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

    def _on_scheme_changed(self, *_a) -> None:
        if self.settings["theme"] == "system":
            self._apply_theme_now()

    def _apply_theme_now(self) -> None:
        self._resolved_theme = theme.apply_theme(
            QGuiApplication.instance(), self.settings["theme"], self.settings["font_size"]
        )
        self._rerender_messages()

    def _rerender_messages(self) -> None:
        show_ts = self.settings["show_timestamps"]
        if self.current_chat:
            self.chat_area.clear_messages()
            for m in self.current_chat["messages"]:
                self.chat_area.add_message(
                    m["role"], m.get("display", m["content"]), m.get("ts"),
                    m.get("attachments"), bool(m.get("web")),
                )
        else:
            self.chat_area.refresh_theme(self._resolved_theme, show_ts)

    # ------------------------------------------------------------- connessione

    def _check_server(self) -> None:
        if self._status_worker is not None:
            return
        self._status_worker = ApiWorker(self.settings["host"], "/api/version", self)
        self._status_worker.ready.connect(self._on_server_ok)
        self._status_worker.failed.connect(self._on_server_fail)
        self._status_worker.finished.connect(self._status_worker.deleteLater)
        self._status_worker.finished.connect(lambda: setattr(self, "_status_worker", None))
        self._status_worker.start()

    def _on_server_ok(self, data: object) -> None:
        version = (data or {}).get("version", "?") if isinstance(data, dict) else "?"
        self._version = version
        was_online = self._online
        self._online = True
        self.status_dot.setProperty("status", "online")
        self.status_label.setText(f"Ollama {version}")
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
        self._models_worker.finished.connect(lambda: setattr(self, "_models_worker", None))
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
            return
        dlg = ModelManagerDialog(self.settings["host"], self.current_model(), self)
        dlg.exec()
        if dlg.changed:
            self._refresh_models()

    # -------------------------------------------------------------- conversazioni

    def _save_chats(self) -> None:
        config.save_chats(self.chats)

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
        chat = next((c for c in self.chats if c["id"] == chat_id), None)
        if chat is None:
            return
        self.current_chat = chat
        self.chat_area.clear_messages()
        for m in chat["messages"]:
            self.chat_area.add_message(
                m["role"], m.get("display", m["content"]), m.get("ts"),
                m.get("attachments"), bool(m.get("web")),
            )
        self.sidebar.set_chats(self.chats, chat_id)
        self.chat_area.focus_input()

    def _rename_chat(self, chat_id: str, title: str) -> None:
        chat = next((c for c in self.chats if c["id"] == chat_id), None)
        if chat:
            chat["title"] = title.strip()
            chat["updated"] = config.now()
            self._save_chats()
            self.sidebar.set_chats(self.chats, chat_id)

    def _delete_chat(self, chat_id: str) -> None:
        self.chats = [c for c in self.chats if c["id"] != chat_id]
        self._save_chats()
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
        full_text, image_atts, warnings = context.build_user_content(text, atts)
        if warnings:
            self.chat_area.add_system_note("⚠ " + "\n⚠ ".join(warnings))

        self._pending_user = {
            "display": text,
            "full": full_text,
            "image_paths": [a["path"] for a in image_atts],
            "web": web_on,
            "attachments": [a["name"] for a in atts],
            "model": model,
        }
        self.chat_area.clear_attachments()

        if web_on:
            self._start_web_search()
        else:
            self._commit_user_message()

    # ----------------------------------------------------------- ricerca web

    def _start_web_search(self) -> None:
        self.chat_area.set_streaming(True)
        self.sidebar.set_busy(True)
        placeholder = self.chat_area.begin_stream(config.now())
        placeholder.show_status("🌐 Ricerca web in corso…")

        self._search_worker = web_search.WebSearchWorker(
            self._pending_user["display"],
            int(self.settings.get("web_results", 5)),
            provider=self.settings.get("web_provider", "duckduckgo"),
            api_key=self.settings.get("web_api_key", ""),
            searxng_url=self.settings.get("web_searxng_url", ""),
            parent=self,
        )
        self._search_worker.ready.connect(self._on_web_results)
        self._search_worker.failed.connect(self._on_web_failed)
        self._search_worker.finished.connect(self._search_worker.deleteLater)
        self._search_worker.finished.connect(lambda: setattr(self, "_search_worker", None))
        self._search_worker.start()

    def _on_web_results(self, block: str, _query: str) -> None:
        self.chat_area.end_stream(discard_empty=True)
        if self._pending_user is not None:
            self._pending_user["full"] = (
                f"{self._pending_user['full']}\n\n---\n{block}\n---"
            )
            self._commit_user_message()

    def _on_web_failed(self, err: str) -> None:
        self.chat_area.end_stream(discard_empty=True)
        worker = self._search_worker
        if worker is not None and worker.stopped:
            # annullata dall'utente
            self._pending_user = None
            self._set_idle()
            return
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
            self.current_chat = {
                "id": config.new_chat_id(),
                "title": p["display"][:48] + ("…" if len(p["display"]) > 48 else ""),
                "model": model,
                "updated": config.now(),
                "messages": [],
            }
            self.chats.append(self.current_chat)

        ts = config.now()
        msg = {
            "role": "user",
            "content": p["full"],
            "display": p["display"],
            "ts": ts,
            "attachments": p["attachments"],
            "web": p["web"],
        }
        if p["image_paths"]:
            msg["image_paths"] = p["image_paths"]
        self.current_chat["messages"].append(msg)
        self.current_chat["model"] = model
        self.current_chat["updated"] = ts
        self.chat_area.add_message(
            "user", p["display"], ts, p["attachments"] or None, p["web"]
        )
        self.chat_area.clear_input()
        self._save_chats()
        self.sidebar.set_chats(self.chats, self.current_chat["id"])
        self._start_generation()

    def _start_generation(self) -> None:
        model = self.current_chat["model"]
        s = self.settings

        messages: list[dict] = []
        if s["system_prompt"]:
            messages.append({"role": "system", "content": s["system_prompt"]})
        limit = max(0, int(s["history_limit"]))
        history = self.current_chat["messages"][-limit * 2:] if limit else []
        last_idx = len(history) - 1
        for i, m in enumerate(history):
            entry = {"role": m["role"], "content": m["content"]}
            # le immagini viaggiano solo con l'ultimo messaggio utente (context saving)
            if i == last_idx and m["role"] == "user" and m.get("image_paths"):
                imgs = [context.image_to_b64(p) for p in m["image_paths"]]
                imgs = [b for b in imgs if b]
                if imgs:
                    entry["images"] = imgs
            messages.append(entry)

        from .widgets.model_params import options_for_model

        payload = {
            "model": model,
            "messages": messages,
            "stream": bool(s["stream"]),
            "options": options_for_model(model),
        }

        self._pending_stream = ""
        self.chat_area.begin_stream(config.now())

        self._worker = ChatWorker(self.settings["host"], payload, self)
        self._worker.chunk.connect(self._on_chunk)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.finished.connect(lambda: setattr(self, "_worker", None))
        self._worker.start()

        self.chat_area.set_streaming(True)
        self.sidebar.set_busy(True)

    def _on_chunk(self, chunk: str) -> None:
        self._pending_stream += chunk
        self.chat_area.stream_text(chunk)

    def _on_done(self, done: dict) -> None:
        if self.current_chat is None:
            return
        self.chat_area.end_stream(format_stats(done))
        text = self._pending_stream
        self._pending_stream = ""
        if text:
            self.current_chat["messages"].append(
                {"role": "assistant", "content": text, "ts": config.now()}
            )
            self.current_chat["updated"] = config.now()
            self._save_chats()
            self.sidebar.set_chats(self.chats, self.current_chat["id"])
        self._set_idle()

    def _on_failed(self, err: str) -> None:
        self.chat_area.end_stream(discard_empty=True)
        partial = self._pending_stream
        self._pending_stream = ""
        # se il modello aveva già prodotto testo, conservalo nella conversazione
        if partial and self.current_chat is not None:
            self.current_chat["messages"].append(
                {"role": "assistant", "content": partial, "ts": config.now()}
            )
            self.current_chat["updated"] = config.now()
            self._save_chats()
            self.sidebar.set_chats(self.chats, self.current_chat["id"])
        self.chat_area.add_system_note(
            err + "\n\nSuggerimento: avvia Ollama con `ollama serve` e verifica l'URL nelle impostazioni (Ctrl+,)."
        )
        self._set_idle()

    def _on_stop(self) -> None:
        if self._search_worker is not None and self._search_worker.isRunning():
            self._search_worker.stop()
            return
        if self._worker is not None:
            self._worker.stop()
        # il worker chiuderà lo stream; forziamo comunque la conclusione
        if self._pending_stream:
            self._on_done({"done": True})
        else:
            self.chat_area.end_stream(discard_empty=True)
            self._set_idle()

    def _set_idle(self) -> None:
        self.chat_area.set_streaming(False)
        self.sidebar.set_busy(False)
        self.chat_area.focus_input()

    # ---------------------------------------------------------------- impostazioni

    def open_settings(self) -> None:
        dlg = SettingsDialog(
            self.settings, self.model_names, self.settings["theme"], self._version, self
        )
        dlg.applied.connect(self._apply_settings)
        dlg.exec()

    def _apply_settings(self, s: dict) -> None:
        self.settings = dict(s)
        self._apply_theme_now()
        self.chat_area.set_send_on_enter(s["send_on_enter"])
        # ricontatta il server e ricarica i modelli
        self._online = None
        self._check_server()
        self._refresh_models()

    # -------------------------------------------------------------------- chiusura

    def closeEvent(self, ev) -> None:  # noqa: N802 (API Qt)
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(2000)
        super().closeEvent(ev)

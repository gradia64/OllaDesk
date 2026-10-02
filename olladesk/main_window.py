"""Finestra principale: sidebar + area chat + barra modelli/stato.

Conversazioni, invio e generazione sono del `ChatEngine` (engine.py): la
finestra raccoglie l'input (testo, allegati, ricerca web, modello) e mostra
gli eventi del motore che riguardano la conversazione visualizzata.
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

from . import __version__, app_update, companion, config, server_share, theme
from .engine import ChatEngine
from .ollama_client import shutdown_workers
from .widgets.chat_area import ChatArea
from .widgets.model_manager import ModelManagerDialog
from .widgets.pairing_dialog import PairingDialog
from .widgets.settings_dialog import SettingsDialog
from .widgets.sidebar import ChatSidebar
from .workers import WorkerRegistry


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OllaDesk")
        self.resize(1120, 740)
        self.setMinimumSize(860, 560)

        self.settings = config.load_settings()
        self.engine = ChatEngine(self.settings, self)
        # conversazione visualizzata: None = pagina di benvenuto; un id non
        # ancora salvato è una conversazione nuova (nasce al primo messaggio)
        self._view_id: str | None = None
        self._app_update_worker: app_update.AppUpdateCheckWorker | None = None
        self._app_update: dict | None = None   # release più recente trovata
        self._preferred_model: str | None = None
        self._workers = WorkerRegistry()
        # l'invio partito da questa finestra svuota l'input al commit; uno
        # partito dal telefono no (la bozza sul PC resta)
        self._clear_input_for: str | None = None
        self._resolved_theme = theme.resolve_theme(self.settings["theme"])
        self.tray: QSystemTrayIcon | None = None
        self._really_quit = False      # True solo da «Esci» nel menu della tray
        self._tray_hint_shown = False
        self._share_note_shown = False   # la nota «external» vale una volta per sessione
        self._share = server_share.SharedOllamaServer(self)
        self._share.state_changed.connect(self._on_share_state)
        self._companion = companion.CompanionServer(self.engine, self)
        self._companion.state_changed.connect(self._on_companion_state)

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
        self.sidebar.set_chats(self.engine.chats(), None)

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
        QTimer.singleShot(800, self._sync_companion)

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
        # companion web attiva: clic per abbinare un telefono
        self.companion_btn = QToolButton(top)
        self.companion_btn.setText("📱")
        self.companion_btn.hide()
        top_lay.addWidget(self.companion_btn)
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
        self.companion_btn.clicked.connect(self._open_pairing)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)

        e = self.engine
        e.chats_changed.connect(self._on_chats_changed)
        e.user_message_added.connect(self._on_user_message)
        e.busy_changed.connect(self._on_busy_changed)
        e.search_started.connect(self._on_search_started)
        e.search_finished.connect(self._on_search_finished)
        e.generation_started.connect(self._on_generation_started)
        e.text_chunk.connect(self._on_text_chunk)
        e.think_chunk.connect(self._on_think_chunk)
        e.generation_finished.connect(self._on_generation_finished)
        e.notice.connect(self._on_notice)
        e.status_changed.connect(self._on_status_changed)
        e.models_loading.connect(self._on_models_loading)
        e.models_changed.connect(self._set_models)

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
        # aggiorna tema/orario/icone dell'area chat PRIMA di ricreare i messaggi
        self.chat_area.refresh_theme(self._resolved_theme, self.settings["show_timestamps"])
        self._rerender_messages()

    def _rerender_messages(self) -> None:
        # la risposta in corso non è ancora in current_chat: dopo i messaggi
        # salvati si ricrea la sua bolla dal testo già ricevuto
        chat = self.current_chat
        if chat:
            self._render_chat(chat)
            if self._viewing_active():
                self._show_partial()

    # ------------------------------------------------------------- connessione

    @property
    def _online(self) -> bool | None:
        return self.engine.online()

    @property
    def _version(self) -> str:
        return self.engine.version()

    def _check_server(self) -> None:
        self.engine.check_server()

    def _on_status_changed(self, online: bool, version: str) -> None:
        if online:
            self.status_dot.setProperty("status", "online")
            self.status_label.setText(f"Ollama {html.escape(version)}")
        else:
            self.status_dot.setProperty("status", "offline")
            self.status_label.setText("offline")
        self._repolish(self.status_dot)

    @staticmethod
    def _repolish(w: QWidget) -> None:
        w.style().unpolish(w)
        w.style().polish(w)

    # ----------------------------------------------------------------- modelli

    def model_names(self) -> list[str]:
        return self.engine.model_names()

    def _refresh_models(self) -> None:
        self.engine.refresh_models()

    def _on_models_loading(self, loading: bool) -> None:
        self.reload_models_btn.setEnabled(not loading)

    def _set_models(self, names: list[str]) -> None:
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if not names:
            self.model_combo.addItem("— nessun modello —")
            self.model_combo.setEnabled(False)
        else:
            self.model_combo.setEnabled(True)
            for m in self.engine.models():
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

    @property
    def current_chat(self) -> dict | None:
        """Conversazione visualizzata (None se nuova o pagina di benvenuto)."""
        return self.engine.chat(self._view_id)

    def _viewing(self, chat_id: str) -> bool:
        return chat_id == "" or chat_id == self._view_id

    def _render_chat(self, chat: dict) -> None:
        self.chat_area.clear_messages()
        for m in chat["messages"]:
            self.chat_area.add_message(
                m["role"], m.get("display", m.get("content", "")), m.get("ts"),
                m.get("attachments"), bool(m.get("web")), m.get("stats"),
                thinking=m.get("thinking", ""),
            )

    # durante una generazione si può navigare: la risposta continua in
    # background e si ritrova riaprendo la sua conversazione

    def _new_chat(self) -> None:
        self._view_id = None
        self.chat_area.clear_messages()
        self.sidebar.set_chats(self.engine.chats(), None)
        self._sync_busy_ui()
        self.chat_area.focus_input()

    def _open_chat(self, chat_id: str) -> None:
        chat = self.engine.chat(chat_id)
        if chat is None:
            # elencata ma il file è mancante: ripulisci l'indice
            self.engine.delete_chat(chat_id)
            if self._view_id == chat_id:
                self._view_id = None
                self.chat_area.clear_messages()
            self.sidebar.set_chats(self.engine.chats(), self._view_id)
            self.chat_area.add_system_note("⚠ Conversazione non leggibile dal disco: rimossa dall'elenco.")
            return
        self._view_id = chat_id
        self._render_chat(chat)
        if self._viewing_active():
            self._show_partial()
        self._sync_busy_ui()
        # ripristina il modello con cui era nata la conversazione
        model = chat.get("model")
        if model and self.model_combo.isEnabled():
            idx = self.model_combo.findData(model)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
        self.sidebar.set_chats(self.engine.chats(), chat_id)
        self.chat_area.focus_input()

    def _rename_chat(self, chat_id: str, title: str) -> None:
        self.engine.rename_chat(chat_id, title)
        self.sidebar.set_chats(self.engine.chats(), chat_id)

    def _delete_chat(self, chat_id: str) -> None:
        self.engine.delete_chat(chat_id)
        if self._view_id == chat_id:
            self._view_id = None
            self.chat_area.clear_messages()
        self.sidebar.set_chats(self.engine.chats(), None)

    def _toggle_sidebar(self) -> None:
        self.sidebar.setVisible(not self.sidebar.isVisible())

    # ------------------------------------------------------------- invio/stream

    def _busy(self) -> bool:
        return self.engine.busy()

    def _viewing_active(self) -> bool:
        """La conversazione visualizzata è quella in elaborazione."""
        return (self.engine.busy() and self._view_id is not None
                and self.engine.active_chat_id() == self._view_id)

    def _sync_busy_ui(self) -> None:
        # ■ solo sulla conversazione che sta rispondendo: dalle altre il
        # pulsante resta ➤ e l'invio spiega perché non parte
        self.chat_area.set_streaming(self._viewing_active())

    def _show_partial(self) -> None:
        """Bolla della risposta in corso, ricostruita dal testo già ricevuto."""
        if self.engine.phase() == "search":
            self.chat_area.begin_stream(config.now()).show_status("🌐 Ricerca web in corso…")
            return
        self.chat_area.begin_stream(config.now())
        text, think = self.engine.partial()
        if think:
            self.chat_area.stream_thinking(think)
        if text:
            self.chat_area.stream_text(text)

    def _on_send(self, text: str) -> None:
        if self._busy():
            if not self._viewing_active():
                self.chat_area.add_system_note(
                    "Il PC sta già rispondendo in un'altra conversazione (forse dal "
                    "telefono): attendi la fine oppure aprila e premi ■."
                )
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
        self.chat_area.clear_attachments()
        if self._view_id is None:
            self._view_id = self.engine.new_chat_id()
        self._clear_input_for = self._view_id
        self.engine.send(
            self._view_id, text, model, attachments=atts, web=web_on,
            think=bool(self.settings.get("thinking", True)),
        )

    def _on_stop(self) -> None:
        self.engine.stop()

    # ------------------------------------------------- eventi del motore

    def _on_chats_changed(self) -> None:
        self.sidebar.set_chats(self.engine.chats(), self._view_id)

    def _on_user_message(self, chat_id: str, msg: dict) -> None:
        if not self._viewing(chat_id):
            return
        self.chat_area.add_message(
            "user", msg["display"], msg["ts"], msg["attachments"] or None, msg["web"]
        )
        if chat_id == self._clear_input_for:
            self._clear_input_for = None
            self.chat_area.clear_input()

    def _on_busy_changed(self, busy: bool) -> None:
        self._sync_busy_ui()
        if not busy:
            self._clear_input_for = None
            self.chat_area.focus_input()

    def _on_search_started(self, chat_id: str) -> None:
        if self._viewing(chat_id):
            placeholder = self.chat_area.begin_stream(config.now())
            placeholder.show_status("🌐 Ricerca web in corso…")

    def _on_search_finished(self, chat_id: str) -> None:
        if self._viewing(chat_id):
            self.chat_area.end_stream(discard_empty=True)

    def _on_generation_started(self, chat_id: str) -> None:
        if self._viewing(chat_id):
            self.chat_area.begin_stream(config.now())

    def _on_text_chunk(self, chat_id: str, chunk: str) -> None:
        if self._viewing(chat_id):
            self.chat_area.stream_text(chunk)

    def _on_think_chunk(self, chat_id: str, chunk: str) -> None:
        if self._viewing(chat_id):
            self.chat_area.stream_thinking(chunk)

    def _on_generation_finished(self, chat_id: str, outcome: str, stats: str, err: str) -> None:
        if not self._viewing(chat_id):
            return
        if outcome == "done":
            self.chat_area.end_stream(stats or None)
            return
        # interrotta o fallita: la bolla resta solo se ha ricevuto testo
        self.chat_area.end_stream(discard_empty=True)
        if outcome != "failed":
            return
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

    def _on_notice(self, chat_id: str, text: str) -> None:
        if self._viewing(chat_id):
            self.chat_area.add_system_note(text)

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
            # nota in chat solo se c'è qualcosa da sistemare (istanza non
            # raggiungibile dalla rete), e una sola volta per sessione: se è
            # già tutto in ascolto sulle interfacce il tooltip di 🔗 basta,
            # una nota a ogni avvio è solo rumore
            if not self._share.lan_shared and not self._share_note_shown:
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

    # ------------------------------------------------------ companion web

    def _sync_companion(self) -> None:
        """Avvia o arresta il server companion secondo le impostazioni."""
        if self.settings.get("companion"):
            self._companion.start(int(self.settings.get("companion_port", 8765)))
        else:
            self._companion.stop()

    def _on_companion_state(self, state: str, detail: str) -> None:
        if state == "running":
            urls = self._companion.urls()
            where = "\n  ".join(urls) if urls else "(nessun indirizzo di rete trovato)"
            self.companion_btn.setToolTip(
                f"Companion web attiva, sul telefono apri:\n  {where}\n"
                "Clic per abbinare un dispositivo"
            )
            self.companion_btn.show()
        elif state == "error":
            self.companion_btn.hide()
            self.chat_area.add_system_note(
                f"⚠ Companion web non avviata: {detail}.\n"
                "Scegli un'altra porta nelle impostazioni (Ctrl+,)."
            )
        else:
            self.companion_btn.hide()

    def _open_pairing(self) -> None:
        if self._companion.state() != "running":
            return
        dlg = PairingDialog(self._companion, self)
        dlg.exec()
        dlg.deleteLater()

    # ------------------------------------------------ aggiornamenti OllaDesk

    def _maybe_check_app_update(self) -> None:
        if self._app_update_worker is not None or not app_update.due_for_check(self.settings):
            return
        w = app_update.AppUpdateCheckWorker(self)
        w.ready.connect(self._on_app_release)
        w.failed.connect(lambda _e: None)   # controllo silenzioso: si riprova al prossimo avvio
        w.finished.connect(w.deleteLater)
        w.finished.connect(self._workers.clear_ref(self, "_app_update_worker", w))
        self._app_update_worker = w
        self._workers.track(w)
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
        self.engine.set_settings(self.settings)
        if not s.get("app_update_check", True):
            self._set_app_update(None)   # controllo disattivato: via l'avviso
        self._apply_theme_now()
        self.chat_area.set_send_on_enter(s["send_on_enter"])
        self.chat_area.set_thinking(s.get("thinking", True))
        # ricontatta il server e ricarica i modelli
        self.engine.reset_status()
        self._check_server()
        self._refresh_models()
        # tray e condivisione possono essere cambiate nelle impostazioni
        self._sync_tray()
        self._sync_share()
        self._sync_companion()

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
            self.tray.hide()   # via l'icona subito: niente residui nel pannello
        # arresta l'eventuale server condiviso avviato da noi: al prossimo
        # avvio `_sync_share` lo riporta su se l'opzione è ancora attiva
        self._share.stop()
        self._companion.stop()
        # ferma e attende (con limite) TUTTI i worker: un QThread distrutto
        # mentre è in esecuzione fa abortire il processo. Gli "zombie" bloccati
        # su un socket muoiono al loro timeout di rete.
        # un'unica scadenza per tutti: prima 1,5 s per OGNI worker in fila
        shutdown_workers(self._workers.all() + self.engine.shutdown())
        super().closeEvent(ev)
        # UNICO punto di uscita dell'app (quitOnLastWindowClosed è sempre
        # False, vedi app.py): «Esci» dalla tray, SIGTERM/SIGINT, logout di
        # sessione e X senza tray passano tutti di qui. Il quit è differito di
        # un ciclo perché chiamato prima che il ciclo eventi parta non ha
        # effetto (il gestore SIGTERM può chiudere prima di exec())
        QTimer.singleShot(0, QApplication.quit)

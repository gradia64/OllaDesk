"""Area chat: elenco messaggi, schermata di benvenuto e campo di input.

L'input occupa tutta la larghezza dell'area messaggi (anche con la sidebar
chiusa). Nell'input ci sono i tasti per allegare file come contesto e per
attivare la ricerca web; gli allegati compaiono come chip sopra l'input.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import config, context, theme
from .. import __version__
from .message import MessageWidget, SystemNoteWidget

ASSISTANT_MAX_W = 860
USER_MAX_W = 620
# larghezza massima della colonna messaggi: centrata quando la finestra è
# più larga, così nascondere la sidebar non stira il contenuto (→ ROADMAP #7)
COLUMN_MAX_W = 900

FILE_FILTER = (
    "Documenti e immagini (*.txt *.md *.py *.json *.csv *.log *.yaml *.yml "
    "*.pdf *.png *.jpg *.jpeg *.webp *.gif);;Tutti i file (*)"
)


class ChatInput(QPlainTextEdit):
    sendPressed = Signal()
    pathsDropped = Signal(list)   # percorsi trascinati o incollati (file/immagini)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("inputEdit")
        self.setPlaceholderText("Scrivi un messaggio…")
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.send_on_enter = True
        self.setAcceptDrops(True)
        self.textChanged.connect(self._auto_height)
        self._auto_height()

    def set_send_on_enter(self, enabled: bool) -> None:
        self.send_on_enter = enabled

    def keyPressEvent(self, ev) -> None:  # noqa: N802 (API Qt)
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.sendPressed.emit()
                return
            if not (ev.modifiers() & Qt.KeyboardModifier.ShiftModifier) and self.send_on_enter:
                self.sendPressed.emit()
                return
        super().keyPressEvent(ev)

    def _auto_height(self) -> None:
        doc_h = self.document().documentLayout().documentSize().height()
        fm = self.fontMetrics()
        h = max(2, min(8, int(doc_h))) * fm.lineSpacing() + 22
        self.setFixedHeight(min(190, max(44, h)))

    # trascina file sull'input e incolla immagini/URL dai mimetype
    def dragEnterEvent(self, ev) -> None:  # noqa: N802 (API Qt)
        if ev.mimeData().hasUrls() or ev.mimeData().hasImage():
            ev.acceptProposedAction()
        else:
            super().dragEnterEvent(ev)

    def dragMoveEvent(self, ev) -> None:  # noqa: N802 (API Qt)
        if ev.mimeData().hasUrls() or ev.mimeData().hasImage():
            ev.acceptProposedAction()
        else:
            super().dragMoveEvent(ev)

    def dropEvent(self, ev) -> None:  # noqa: N802 (API Qt)
        if ev.mimeData().hasUrls():
            self.pathsDropped.emit([u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()])
            ev.acceptProposedAction()
        elif ev.mimeData().hasImage():
            self._paste_image(ev.mimeData().imageData())
            ev.acceptProposedAction()
        else:
            super().dropEvent(ev)

    def insertFromMimeData(self, mime) -> None:  # noqa: N802 (API Qt)
        """Intercolla incolla Ctrl+V: allega file/immagini invece del testo."""
        if mime.hasUrls():
            local = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
            if local:
                self.pathsDropped.emit(local)
                return
        if mime.hasImage():
            self._paste_image(mime.imageData())
            return
        super().insertFromMimeData(mime)

    def _paste_image(self, image) -> None:
        from PySide6.QtGui import QImage

        if not isinstance(image, QImage) or image.isNull():
            return
        # cartella privata (0700) e nome univoco: in /tmp il file sarebbe
        # leggibile da altri utenti, sparirebbe al riavvio (la chat però lo
        # riferisce) e due incolla nello stesso secondo si sovrascrivevano
        path = str(config.attachments_dir() / f"incollato-{uuid.uuid4().hex[:12]}.png")
        if image.save(path, "PNG"):
            self.pathsDropped.emit([path])


class ChatArea(QWidget):
    sendRequested = Signal(str)
    stopRequested = Signal()
    thinkingToggled = Signal(bool)

    def __init__(self, theme_name: str = "dark", show_ts: bool = False, parent=None):
        super().__init__(parent)
        self.theme_name = theme_name
        self.show_ts = show_ts
        self._stream_widget: MessageWidget | None = None
        self._attachments: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- stack: benvenuto / messaggi --------------------------------
        self.stack = QStackedWidget(self)

        self.welcome = self._build_welcome()
        self.stack.addWidget(self.welcome)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.msgs_host = QWidget()
        self.msgs = QVBoxLayout(self.msgs_host)
        self.msgs.setContentsMargins(20, 12, 20, 12)
        self.msgs.setSpacing(12)
        self.msgs.addStretch(1)
        self.scroll.setWidget(self.msgs_host)
        self.stack.addWidget(self.scroll)
        root.addWidget(self.stack, 1)

        bar = self.scroll.verticalScrollBar()
        bar.rangeChanged.connect(self._on_range_changed)
        bar.valueChanged.connect(self._on_value_changed)
        self._stick_bottom = True
        # il margine della colonna centrata segue la larghezza REALE del
        # viewport (il resize di ChatArea arriva prima della disposizione
        # della pagina dello stack: senza questo filtro il margine ritarda)
        self.scroll.viewport().installEventFilter(self)

        # -- riga chip degli allegati ------------------------------------
        self.attach_row = QWidget(self)
        attach_lay = QHBoxLayout(self.attach_row)
        attach_lay.setContentsMargins(20, 6, 20, 0)
        attach_lay.setSpacing(6)
        self.attach_lay = attach_lay
        self.attach_row.hide()
        root.addWidget(self.attach_row)

        # -- input -------------------------------------------------------
        input_row = QHBoxLayout()
        input_row.setContentsMargins(20, 4, 20, 10)
        self._input_row = input_row   # margine allineato alla colonna centrata
        self.input_frame = QFrame(self)
        self.input_frame.setObjectName("inputFrame")
        self.input_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        fl = QHBoxLayout(self.input_frame)
        fl.setContentsMargins(8, 8, 8, 8)
        fl.setSpacing(6)

        self.clip_btn = QToolButton(self.input_frame)
        self.clip_btn.setText("📎")
        self.clip_btn.setToolTip("Allega file come contesto (testo, codice, PDF, immagini)")
        self.clip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clip_btn.clicked.connect(self._pick_files)
        fl.addWidget(self.clip_btn)

        self.web_btn = QToolButton(self.input_frame)
        self.web_btn.setCheckable(True)
        self.web_btn.setIconSize(QSize(18, 18))
        self.web_btn.setToolTip(
            "Ricerca web: allega i risultati di una ricerca online al prossimo messaggio"
        )
        self.web_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.web_btn.toggled.connect(self._on_web_toggled)
        self._update_web_icon()
        fl.addWidget(self.web_btn)

        # thinking: attivo (= comportamento predefinito del modello) o forzato
        # a off inviando "think": false a /api/chat (modelli qwen3, deepseek-r1…)
        self.think_btn = QToolButton(self.input_frame)
        self.think_btn.setCheckable(True)
        self.think_btn.setIconSize(QSize(18, 18))
        self.think_btn.setToolTip(
            "Ragionamento (thinking): attivo (icona colorata), i modelli che sanno "
            "farlo ragionano prima di rispondere e il pensiero è visibile in chat;\n"
            "spento (icona grigia), il ragionamento viene disattivato inviando "
            "\"think\": false alla richiesta.\n"
            "Nota: gpt-oss non accetta la disattivazione (Ollama accetta per "
            "esso solo i livelli low/medium/high)."
        )
        self.think_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.think_btn.toggled.connect(self._on_think_toggled)
        self._update_think_icon()
        fl.addWidget(self.think_btn)

        self.input = ChatInput(self.input_frame)
        self.input.sendPressed.connect(self._emit_send)
        self.input.pathsDropped.connect(self._handle_dropped_paths)
        fl.addWidget(self.input, 1)

        self.send_btn = QPushButton("➤", self.input_frame)
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setFixedSize(28, 28)
        self.send_btn.setToolTip("Invia messaggio")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.clicked.connect(self._on_button)
        fl.addWidget(self.send_btn, 0, Qt.AlignmentFlag.AlignBottom)
        input_row.addWidget(self.input_frame, 1)
        root.addLayout(input_row)

        self.hint = QLabel(self)
        self.hint.setObjectName("metaLabel")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(self.hint)
        self._update_hint()
        root.addSpacing(6)

        self._stream_timer = QTimer(self)
        self._stream_timer.setSingleShot(True)
        self._stream_timer.setInterval(70)
        self._stream_timer.timeout.connect(self._flush_stream)
        self._stream_buffer = ""

        # stesso throttling per lo stream del ragionamento
        self._think_timer = QTimer(self)
        self._think_timer.setSingleShot(True)
        self._think_timer.setInterval(70)
        self._think_timer.timeout.connect(self._flush_thinking)
        self._think_buffer = ""

        # trascinamento file su tutta l'area chat (i messaggi o l'input)
        self.setAcceptDrops(True)

    @staticmethod
    def _side_margin(viewport_w: int) -> int:
        """Margine laterale che centra la colonna messaggi (min 20 px)."""
        return max(20, (viewport_w - COLUMN_MAX_W) // 2)

    def _update_column(self) -> None:
        """Centra la colonna messaggi e allinea input e chip allargati.

        Con finestre strette il margine resta 20 px: come prima. Nascondere
        la sidebar non cambia più la larghezza del blocco di conversazione.
        """
        vw = self.scroll.viewport().width()
        side = self._side_margin(vw)
        self.msgs.setContentsMargins(side, 12, side, 12)
        self.attach_lay.setContentsMargins(side, 6, side, 0)
        self._input_row.setContentsMargins(side, 4, side, 10)

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.scroll.viewport() and ev.type() == QEvent.Type.Resize:
            self._update_column()
        return super().eventFilter(obj, ev)

    def dragEnterEvent(self, ev) -> None:  # noqa: N802 (API Qt)
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:  # noqa: N802 (API Qt)
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if paths:
            self._handle_dropped_paths(paths)
            ev.acceptProposedAction()

    def _handle_dropped_paths(self, paths: list) -> None:
        rejected = self.add_attachment_paths(paths)
        if rejected:
            self.add_system_note(
                "⚠ File ignorati (tipo non supportato o non trovati): " + ", ".join(rejected)
            )

    # ------------------------------------------------------------ benvenuto

    def _build_welcome(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addStretch(2)
        title = QLabel("OllaDesk", w)
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        title.setStyleSheet("font-size: 26px; font-weight: 700;")
        sub = QLabel(f"Chatta in locale con i tuoi modelli Ollama · v{__version__}", w)
        sub.setObjectName("metaLabel")
        sub.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(title)
        lay.addSpacing(6)
        lay.addWidget(sub)
        lay.addSpacing(22)

        for prompt in (
            "Spiegami in modo semplice come funziona un LLM",
            "Scrivi uno script Python che ordina un elenco di file",
            "Riassumi le caratteristiche principali di Debian",
        ):
            btn = QPushButton("✦  " + prompt, w)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(36)
            btn.setMaximumWidth(560)
            btn.clicked.connect(lambda _=False, p=prompt: self._use_suggestion(p))
            lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(3)
        return w

    def _use_suggestion(self, text: str) -> None:
        self.input.setPlainText(text)
        self.input.setFocus()
        cursor = self.input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.input.setTextCursor(cursor)

    # ------------------------------------------------------------- messaggi

    def _wrap_row(self, widget: QWidget, role: str) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        if role == "user":
            lay.addStretch(1)
            lay.addWidget(widget, 0, Qt.AlignmentFlag.AlignTop)
        else:
            # factor 1: la bolla dell'assistente si estende fino al massimo consentito
            lay.addWidget(widget, 1, Qt.AlignmentFlag.AlignTop)
            lay.addStretch(0)
        return row

    def _apply_widths(self, w: MessageWidget) -> None:
        vw = self.scroll.viewport().width()
        side = self._side_margin(vw)
        row_w = max(280, vw - 2 * side)
        if w.role == "user":
            w.setMaximumWidth(min(USER_MAX_W, max(280, int(row_w * 0.8))))
        else:
            w.setMaximumWidth(min(ASSISTANT_MAX_W, max(280, row_w)))

    def add_message(
        self,
        role: str,
        text: str = "",
        ts: float | None = None,
        attachments: list[str] | None = None,
        web: bool = False,
        stats: str | None = None,
        thinking: str = "",
    ) -> MessageWidget:
        w = MessageWidget(
            role, text, ts, self.show_ts, self.theme_name, attachments, web, self,
            thinking=thinking,
        )
        if stats:
            w.set_stats(stats)
        self._apply_widths(w)
        self.msgs.addWidget(self._wrap_row(w, role), 0, Qt.AlignmentFlag.AlignTop)
        self.stack.setCurrentWidget(self.scroll)
        QTimer.singleShot(0, self._scroll_to_bottom)
        return w

    def add_system_note(self, text: str) -> None:
        w = SystemNoteWidget(text, self)
        w.setMaximumWidth(ASSISTANT_MAX_W)
        self.msgs.addWidget(self._wrap_row(w, "assistant"), 0, Qt.AlignmentFlag.AlignTop)
        self.stack.setCurrentWidget(self.scroll)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def begin_stream(self, ts: float | None = None) -> MessageWidget:
        self._stream_buffer = ""  # evita di trascinare testo della risposta precedente
        self._think_buffer = ""
        w = self.add_message("assistant", "", ts)
        w.start_animation()
        self._stream_widget = w
        return w

    def stream_text(self, chunk: str) -> None:
        self._stream_buffer += chunk
        # throttle: NON riavviare il timer se è già attivo, altrimenti con
        # stream rapidi il flush slitta di continuo e il testo appare solo alla fine
        if not self._stream_timer.isActive():
            # ogni flush riconverte TUTTO il Markdown: con risposte lunghe si
            # diradano gli aggiornamenti (70 ms + 1 ms ogni 100 caratteri, max 400 ms)
            n = len(self._stream_buffer)
            self._stream_timer.setInterval(min(400, 70 + n // 100))
            self._stream_timer.start()

    def stream_thinking(self, chunk: str) -> None:
        self._think_buffer += chunk
        if not self._think_timer.isActive():
            n = len(self._think_buffer)
            self._think_timer.setInterval(min(400, 70 + n // 100))
            self._think_timer.start()

    def _flush_stream(self) -> None:
        if self._stream_widget is not None and self._stream_buffer:
            self._stream_widget.append_stream(self._stream_buffer)
            QTimer.singleShot(0, self._scroll_to_bottom)

    def _flush_thinking(self) -> None:
        if self._stream_widget is not None and self._think_buffer:
            self._stream_widget.set_thinking_stream(self._think_buffer)
            QTimer.singleShot(0, self._scroll_to_bottom)

    def end_stream(self, stats: str | None = None, discard_empty: bool = False) -> None:
        self._flush_stream()
        self._flush_thinking()
        w, self._stream_widget = self._stream_widget, None
        if w is None:
            return
        if discard_empty and not w.raw:
            # nessun contenuto ricevuto: elimina la bolla vuota (con la sua riga)
            row = w.parentWidget()
            row.deleteLater()
            return
        w.finish(stats, self.show_ts)

    def clear_messages(self) -> None:
        self._stream_widget = None
        while self.msgs.count() > 1:  # l'indice 0 è lo stretch
            item = self.msgs.takeAt(self.msgs.count() - 1)
            if item.widget():
                item.widget().deleteLater()
        self.stack.setCurrentWidget(self.welcome)

    @staticmethod
    def _row_widget(row: QWidget | None) -> QWidget | None:
        """Il widget di una riga della chat.

        Nelle righe utente il primo item del layout è lo stretch: il messaggio
        va cercato tra gli item, non preso con itemAt(0).
        """
        lay = row.layout() if row is not None else None
        if lay is None:
            return None
        for j in range(lay.count()):
            w = lay.itemAt(j).widget()
            if w is not None:
                return w
        return None

    def refresh_theme(self, theme_name: str, show_ts: bool) -> None:
        self.theme_name = theme_name
        self.show_ts = show_ts
        self._update_web_icon()
        self._update_think_icon()
        for i in range(self.msgs.count()):
            row = self.msgs.itemAt(i).widget()
            if row is None:
                continue
            w = self._row_widget(row)
            if isinstance(w, MessageWidget):
                w.refresh_theme(theme_name, show_ts)
            elif isinstance(w, SystemNoteWidget):
                w.setProperty("bubble", "error")
        for i in range(self.msgs.count()):
            row = self.msgs.itemAt(i).widget()
            w = self._row_widget(row) if row is not None else None
            if isinstance(w, MessageWidget):
                self._apply_widths(w)

    def resizeEvent(self, ev) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(ev)
        self._update_column()
        for i in range(self.msgs.count()):
            row = self.msgs.itemAt(i).widget()
            w = self._row_widget(row) if row is not None else None
            if isinstance(w, MessageWidget):
                self._apply_widths(w)

    # -------------------------------------------------------------- scrolling

    def _on_range_changed(self, _mn: int, mx: int) -> None:
        if self._stick_bottom:
            self.scroll.verticalScrollBar().setValue(mx)

    def _on_value_changed(self, value: int) -> None:
        bar = self.scroll.verticalScrollBar()
        self._stick_bottom = value >= bar.maximum() - 80

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ------------------------------------------------------------- allegati

    def _pick_files(self) -> None:
        paths, _f = QFileDialog.getOpenFileNames(self, "Allega file", "", FILE_FILTER)
        if paths:
            rejected = self.add_attachment_paths(paths)
            if rejected:
                self.add_system_note(
                    "⚠ Tipi di file non supportati: " + ", ".join(rejected)
                )

    def add_attachment_paths(self, paths: list[str]) -> list[str]:
        """Aggiunge allegati; restituisce i nomi dei file rifiutati."""
        rejected: list[str] = []
        known = {a["path"] for a in self._attachments}
        for s in paths:
            p = Path(s).expanduser()
            kind = context.classify(p)
            if str(p) in known:
                continue   # già allegato: ignora senza avvisare
            if kind == "unknown" or not p.exists():
                rejected.append(p.name)
                continue
            self._attachments.append(
                {"path": str(p), "name": p.name, "kind": kind}
            )
        self._refresh_attach_row()
        return rejected

    def _refresh_attach_row(self) -> None:
        while self.attach_lay.count():
            item = self.attach_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for idx, att in enumerate(self._attachments):
            icon = "🖼" if att["kind"] == "image" else "📄"
            chip = QWidget(self.attach_row)
            lay = QHBoxLayout(chip)
            lay.setContentsMargins(8, 2, 4, 2)
            lay.setSpacing(4)
            lab = QLabel(f"{icon} {att['name']}", chip)
            lab.setObjectName("chip")
            lay.addWidget(lab)
            rm = QToolButton(chip)
            rm.setText("✕")
            rm.setToolTip("Rimuovi allegato")
            rm.clicked.connect(lambda _=False, i=idx: self.remove_attachment(i))
            lay.addWidget(rm)
            self.attach_lay.addWidget(chip)
        self.attach_lay.addStretch(1)
        self.attach_row.setVisible(bool(self._attachments))

    def remove_attachment(self, idx: int) -> None:
        if 0 <= idx < len(self._attachments):
            del self._attachments[idx]
            self._refresh_attach_row()

    def attachments(self) -> list[dict]:
        return list(self._attachments)

    def clear_attachments(self) -> None:
        self._attachments = []
        self._refresh_attach_row()

    # ----------------------------------------------------------- ricerca web

    def _on_web_toggled(self, _checked: bool) -> None:
        self._update_web_icon()

    def _update_web_icon(self) -> None:
        """Globo grigio da spento, blu da acceso (nessun sfondo colorato)."""
        t = theme.palette_for(self.theme_name)
        color = theme.WEB_ACTIVE_COLOR if self.web_btn.isChecked() else t["dim"]
        self.web_btn.setIcon(theme.globe_icon(color))

    def web_search_active(self) -> bool:
        return self.web_btn.isChecked()

    def set_web_search(self, on: bool) -> None:
        self.web_btn.setChecked(on)

    # ------------------------------------------------------------- thinking

    def _on_think_toggled(self, checked: bool) -> None:
        self._update_think_icon()
        self.thinkingToggled.emit(checked)

    def _update_think_icon(self) -> None:
        """Cervello grigio da spento, blu da acceso (come il globo del web)."""
        t = theme.palette_for(self.theme_name)
        color = theme.WEB_ACTIVE_COLOR if self.think_btn.isChecked() else t["dim"]
        self.think_btn.setIcon(theme.brain_icon(color))

    def set_thinking(self, on: bool) -> None:
        # senza blocco dei segnali, il setChecked all'avvio (o al cambio
        # tema/impostazioni) emetterebbe toggled e riscriverebbe settings.json
        self.think_btn.blockSignals(True)
        self.think_btn.setChecked(on)
        self.think_btn.blockSignals(False)
        # con i segnali bloccati _on_think_toggled non scatta: l'icona va
        # allineata qui, altrimenti all'avvio resta grigia col thinking attivo
        self._update_think_icon()

    def thinking_active(self) -> bool:
        return self.think_btn.isChecked()

    # ----------------------------------------------------------------- input

    def set_streaming(self, busy: bool) -> None:
        if busy:
            self.send_btn.setText("■")
            self.send_btn.setToolTip("Interrompi")
        else:
            self.send_btn.setText("➤")
            self.send_btn.setToolTip("Invia messaggio")

    def set_send_on_enter(self, enabled: bool) -> None:
        self.input.set_send_on_enter(enabled)
        self._update_hint()

    def _update_hint(self) -> None:
        if self.input.send_on_enter:
            self.hint.setText("Invio: invia · Shift+Invio: a capo")
        else:
            self.hint.setText("Ctrl+Invio: invia · Invio: a capo")

    def input_text(self) -> str:
        return self.input.toPlainText().strip()

    def set_input_text(self, text: str) -> None:
        self.input.setPlainText(text)
        self.input.setFocus()

    def clear_input(self) -> None:
        self.input.clear()
        self.input.setFocus()

    def focus_input(self) -> None:
        self.input.setFocus()

    def _emit_send(self) -> None:
        text = self.input_text()
        # si può inviare anche un messaggio fatto di soli allegati
        if text or self._attachments:
            self.sendRequested.emit(text)

    def _on_button(self) -> None:
        if self._stream_widget is not None:
            self.stopRequested.emit()
        else:
            self._emit_send()

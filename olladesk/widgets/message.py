"""Bolle di messaggio in stile ChatGPT (utente a destra, assistente a sinistra)."""
from __future__ import annotations

import html
import time

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
)

from ..md import md_to_html
from .. import theme

_ANIM_FRAMES = ["·", "··", "···"]


class MessageWidget(QFrame):
    def __init__(
        self,
        role: str,
        text: str = "",
        ts: float | None = None,
        show_ts: bool = False,
        theme_name: str = "dark",
        attachments: list[str] | None = None,
        web: bool = False,
        parent=None,
        thinking: str = "",
    ):
        super().__init__(parent)
        self.role = role
        self.raw = ""
        self.ts = ts
        self.theme_name = theme_name
        self._anim_idx = 0
        # le statistiche vivono qui, non nel testo della meta: rileggerle
        # dall'etichetta accumulerebbe l'orario a ogni refresh del tema
        self._stats: str | None = None
        self._show_ts = show_ts
        # ragionamento del modello (message.thinking): vuoto se assente
        self._thinking_raw = thinking or ""
        self._think_expanded = False
        self._think_streaming = False
        self._think_color = theme.palette_for(theme_name)["dim"]

        self.setProperty("bubble", role)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        inner = QVBoxLayout(self)
        inner.setContentsMargins(14, 10, 14, 8)
        inner.setSpacing(4)

        # -- blocco «Pensiero» richiudibile (sopra al contenuto) ---------
        self.think_btn = QToolButton(self)
        self.think_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.think_btn.setStyleSheet(
            "QToolButton { border: none; background: transparent; "
            f"color: {self._think_color}; padding: 0; }}"
        )
        self.think_btn.clicked.connect(self._on_think_toggle)
        self.think_btn.hide()
        self.think_label = QLabel(self)
        self.think_label.setTextFormat(Qt.TextFormat.PlainText)
        self.think_label.setWordWrap(True)
        self.think_label.setStyleSheet(
            f"color: {self._think_color}; font-style: italic;"
        )
        self.think_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.think_label.hide()
        inner.addWidget(self.think_btn, 0, Qt.AlignmentFlag.AlignLeft)
        inner.addWidget(self.think_label)
        if self._thinking_raw:
            # riapertura di una conversazione salvata: blocco già richiuso
            self.think_btn.show()
            self._sync_think_widgets()

        chips = []
        if role == "user" and web:
            chips.append("🌐 Ricerca web")
        for name in attachments or []:
            chips.append(f"📎 {name}")
        if chips:
            chips_row = QHBoxLayout()
            chips_row.setContentsMargins(0, 0, 0, 0)
            chips_row.setSpacing(4)
            for c in chips:
                lab = QLabel(html.escape(c), self)
                lab.setObjectName("chip")
                chips_row.addWidget(lab)
            chips_row.addStretch(1)
            inner.addLayout(chips_row)

        self.label = QLabel(self)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setWordWrap(True)
        self.label.setOpenExternalLinks(True)
        self.label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.label.linkActivated.connect(lambda _u: None)
        # menu contestuale dedicato (tasto destro): «Copia messaggio» sempre
        # disponibile, senza dover prima selezionare il testo
        self.label.installEventFilter(self)
        inner.addWidget(self.label)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.addStretch(1)
        self.meta = QLabel(self)
        self.meta.setObjectName("metaLabel")
        meta_row.addWidget(self.meta)
        # pulsante di copia su TUTTI i messaggi, non solo dell'assistente
        self.copy_btn = QToolButton(self)
        self.copy_btn.setText("⧉")
        self.copy_btn.setToolTip("Copia messaggio")
        self.copy_btn.clicked.connect(self._copy)
        meta_row.addWidget(self.copy_btn)
        inner.addLayout(meta_row)

        self._update_meta(show_ts, stats=None)
        self.set_text(text)

    # ------------------------------------------------------------------ API

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.label and ev.type() == QEvent.Type.ContextMenu:
            menu = QMenu(self)
            act_copy = menu.addAction("📋  Copia messaggio")
            menu.addSeparator()
            act_sel = menu.addAction("Seleziona tutto")
            chosen = menu.exec(ev.globalPos())
            if chosen is act_copy:
                self._copy()
            elif chosen is act_sel:
                self.label.selectAll()
            return True   # sostituisce il menu standard di QLabel
        return super().eventFilter(obj, ev)

    def set_text(self, raw: str) -> None:
        self.raw = raw
        if raw:
            self.stop_animation()
            # primo contenuto dopo il ragionamento: richiudi il blocco
            # (riapribile dal pulsante), come fanno le chat più note
            if self._think_streaming:
                self._think_streaming = False
                self._auto_collapse_think()
        self.copy_btn.setVisible(bool(raw))
        bg, fg, inline = theme.code_colors(self.theme_name)
        if raw:
            self.label.setText(md_to_html(raw, bg, fg, inline))
        elif self.role == "assistant":
            self.label.setText(self._anim_frames_text())
        else:
            self.label.setText("")

    def append_stream(self, full_text: str) -> None:
        self.set_text(full_text)

    # ------------------------------------------------------------ pensiero

    def set_thinking_stream(self, full_text: str) -> None:
        """Imposta il ragionamento accumulato nella bolla in streaming.

        Come append_stream per il testo, riceve il buffer COMPLETO dal chiamante
        (che accumula i frammenti): sostituisce, non concatena — altrimenti ogni
        flush riappenderebbe tutto il buffer duplicandolo.
        """
        self._thinking_raw = full_text
        if not self.raw:
            # finché non arriva contenuto lo stream resta visibile ed espanso
            self._think_streaming = True
            self._think_expanded = True
        self.think_btn.show()
        self._sync_think_widgets()

    def _sync_think_widgets(self) -> None:
        self.think_btn.setText(self._think_header())
        self.think_label.setText(self._thinking_raw)
        self.think_label.setVisible(self._think_expanded)

    def _think_header(self) -> str:
        arrow = "▾" if self._think_expanded else "▸"
        label = "sta pensando…" if self._think_streaming else "Pensiero"
        return f"💭 {label} {arrow}"

    def _auto_collapse_think(self) -> None:
        self._think_expanded = False
        self._sync_think_widgets()

    def _on_think_toggle(self) -> None:
        if not self._thinking_raw:
            return
        self._think_expanded = not self._think_expanded
        self._sync_think_widgets()

    def finish(self, stats: str | None = None, show_ts: bool = False) -> None:
        self.stop_animation()
        if self._think_streaming:
            # interrotto durante il ragionamento senza contenuto: richiudi
            self._think_streaming = False
            self._auto_collapse_think()
        if not self.raw:
            self.label.setText("(nessuna risposta)")
        self._stats = stats
        self._update_meta(show_ts, stats)

    def set_stats(self, stats: str | None) -> None:
        """Statistiche della risposta, mostrate nella meta insieme all'orario."""
        self._stats = stats
        self._update_meta(self._show_ts, stats)

    def refresh_theme(self, theme_name: str, show_ts: bool) -> None:
        self.theme_name = theme_name
        self._think_color = theme.palette_for(theme_name)["dim"]
        self.think_btn.setStyleSheet(
            "QToolButton { border: none; background: transparent; "
            f"color: {self._think_color}; padding: 0; }}"
        )
        self.think_label.setStyleSheet(
            f"color: {self._think_color}; font-style: italic;"
        )
        self._update_meta(show_ts, self._stats)
        if self.raw:
            bg, fg, inline = theme.code_colors(theme_name)
            self.label.setText(md_to_html(self.raw, bg, fg, inline))

    # -------------------------------------------------------------- interni

    def _update_meta(self, show_ts: bool, stats: str | None) -> None:
        bits = []
        if show_ts and self.ts:
            bits.append(time.strftime("%H:%M", time.localtime(self.ts)))
        if stats:
            bits.append(stats)
        self.meta.setText("  ·  ".join(bits))

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.raw)

    def _anim_frames_text(self) -> str:
        frame = _ANIM_FRAMES[self._anim_idx % len(_ANIM_FRAMES)]
        return f'<font color="#9b9b9b">{frame}</font>'

    def show_status(self, text: str) -> None:
        """Testo di stato transitorio (es. «Ricerca web in corso…»)."""
        self.stop_animation()
        self.label.setText(f'<font color="#9b9b9b">{html.escape(text)}</font>')

    def start_animation(self) -> None:
        if getattr(self, "_anim_timer", None) is None:
            self._anim_timer = QTimer(self)
            self._anim_timer.setInterval(350)
            self._anim_timer.timeout.connect(self._tick_anim)
        self._anim_timer.start()

    def stop_animation(self) -> None:
        timer = getattr(self, "_anim_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()

    def _tick_anim(self) -> None:
        self._anim_idx += 1
        if not self.raw:
            self.label.setText(self._anim_frames_text())


class SystemNoteWidget(QFrame):
    """Nota informativa centrata (es. nessun modello disponibile)."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setProperty("bubble", "error")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        lab = QLabel(text, self)
        lab.setProperty("error", True)
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(lab)

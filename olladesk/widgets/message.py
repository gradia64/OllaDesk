"""Bolle di messaggio in stile ChatGPT (utente a destra, assistente a sinistra)."""
from __future__ import annotations

import html
import time

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QTextOption
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
)

from ..md import COPY_LINK, code_blocks, md_to_html
from .. import icons, theme
from ..i18n import _
from ..tools import arguments_summary, result_preview

_ANIM_FRAMES = ["·", "··", "···"]
TOOL_PREVIEW_LINES = 6       # righe del risultato di un tool mostrate nella bolla
TOOL_PREVIEW_CHARS = 500
DIFF_MAX_HEIGHT = 300        # altezza massima del diff nella scheda di conferma
DIFF_MEASURE_WIDTH = 600
# righe aggiunte e tolte del diff: (tema scuro, tema chiaro), leggibili su entrambi
DIFF_ADD_COLORS = ("#4ade80", "#15803d")
DIFF_DEL_COLORS = ("#f87171", "#b91c1c")


def change_title(view: dict) -> str:
    """Titolo della richiesta di conferma (`Request.view()`)."""
    label = view.get("label", "")
    kind = view.get("kind")
    if kind == "move":
        target = view.get("target", "")
        if target.rpartition("/")[0] == label.rpartition("/")[0]:
            return _("Il modello vuole rinominare {path} in {name}").format(
                path=label, name=target.rpartition("/")[2])
        return _("Il modello vuole spostare {path} in {target}").format(path=label, target=target)
    if kind == "delete":
        return _("Il modello vuole spostare nel cestino {path}").format(path=label)
    if kind == "mkdir":
        return _("Il modello vuole creare la cartella {path}").format(path=label)
    if kind == "create":
        return _("Il modello vuole creare {path}").format(path=label)
    if kind == "replace":
        return _("Il modello vuole riscrivere {path}").format(path=label)
    return _("Il modello vuole modificare {path}").format(path=label)


def diff_html(diff: str, dim: str, dark: bool = True) -> str:
    """Diff unificato in HTML: righe aggiunte verdi, tolte rosse."""
    add = DIFF_ADD_COLORS[0 if dark else 1]
    rem = DIFF_DEL_COLORS[0 if dark else 1]
    rows = []
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            color = dim
        elif line.startswith("+"):
            color = add
        elif line.startswith("-"):
            color = rem
        elif line.startswith("@@"):
            color = dim
        else:
            color = ""
        text = html.escape(line) or "&nbsp;"
        rows.append(f'<span style="color:{color}">{text}</span>' if color else text)
    return ('<pre style="margin:0; font-family: monospace; white-space: pre-wrap;">'
            + "<br>".join(rows) + "</pre>")


class ChangeCard(QFrame):
    """Richiesta di conferma di una modifica ai file, dentro la bolla.

    Mostra il diff e tre scelte; `decided` porta l'id della richiesta e la
    scelta (write_tools.APPLY, REJECT, ALLOW_REST). Dopo un clic i pulsanti
    si disattivano: la scheda sparisce quando il motore chiude la richiesta.
    """

    decided = Signal(int, str)

    def __init__(self, view: dict, theme_name: str, parent=None):
        super().__init__(parent)
        self.view = dict(view)
        t = theme.palette_for(theme_name)
        self.setObjectName("changeCard")
        self.setStyleSheet(
            f"QFrame#changeCard {{ border: 1px solid {theme.TOOLS_WRITE_COLOR}; "
            "border-radius: 8px; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        title = change_title(view)
        if view.get("kind") in ("create", "replace", "edit"):
            title += "  " + _("(+{added} −{removed})").format(
                added=view.get("added", 0), removed=view.get("removed", 0))
        self.title = QLabel(title, self)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-weight: 600;")
        lay.addWidget(self.title)
        self.diff = QTextEdit(self)
        self.diff.setReadOnly(True)
        # a capo anche dentro le parole: una riga lunga si legge tutta
        self.diff.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.diff.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.diff.setHtml(diff_html(view.get("diff", ""), t["dim"],
                                    theme.palette_for(theme_name) is theme.palette_for("dark")))
        # alto quanto il diff, fino al tetto (poi si scorre); con l'a capo
        # l'altezza dipende dalla larghezza: si ricalcola quando cambia
        self.diff.document().documentLayout().documentSizeChanged.connect(self._fit_diff)
        self.diff.document().setTextWidth(DIFF_MEASURE_WIDTH)
        self._fit_diff()
        self.diff.setVisible(bool(view.get("diff")))
        lay.addWidget(self.diff)
        # una modifica lunga si vede solo in parte: chi approva deve saperlo
        if view.get("kind") == "delete":
            cut = _("Elenco incompleto: finisce nel cestino anche ciò che qui non vedi.")
        else:
            cut = _("Modifica troppo lunga: qui ne vedi solo una parte. Se non "
                    "sai cosa cambia nel resto del file, rifiutala.")
        self.cut_note = self._note(lay, cut, view.get("truncated"))
        self.long_note = self._note(lay, _(
            "Righe molto lunghe: delle righe cambiate vedi il pezzo attorno alla modifica "
            "(⟪…⟫ segna le parti uguali omesse). Leggi tutto prima di applicare."),
            view.get("long_lines"))
        hidden = int(view.get("hidden") or 0)
        self.hidden_note = self._note(lay, _(
            "Contiene {n} caratteri invisibili o di controllo, mostrati come ⟦U+…⟧: possono "
            "far sembrare il testo diverso da com'è. Se non sai perché ci sono, "
            "rifiuta.").format(n=hidden), hidden > 0)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.apply_btn = QPushButton(_("Applica"), self)
        self.apply_btn.setObjectName("primaryBtn")
        self.reject_btn = QPushButton(_("Rifiuta"), self)
        self.rest_btn = QPushButton(_("Consenti per il resto della risposta"), self)
        self.rest_btn.setToolTip(_("Applica questa modifica e le prossime di questa risposta "
                                   "senza chiedere di nuovo."))
        for btn, decision in ((self.apply_btn, "apply"), (self.reject_btn, "reject"),
                              (self.rest_btn, "allow_rest")):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, d=decision: self._decide(d))
            row.addWidget(btn)
        if view.get("kind") == "delete":
            self.apply_btn.setText(_("Sposta nel cestino"))
        # spostare e cestinare chiedono sempre, una per una
        self.rest_btn.setVisible(view.get("allow_rest", True) is not False)
        row.addStretch(1)
        lay.addLayout(row)

    def _fit_diff(self, *_args) -> None:
        doc_h = int(self.diff.document().size().height())
        self.diff.setFixedHeight(min(DIFF_MAX_HEIGHT, doc_h + 2 * self.diff.frameWidth() + 2))

    def _note(self, lay, text: str, show) -> QLabel:
        """Avviso in ambra sotto il diff: chi approva deve saperlo."""
        note = QLabel(text, self)
        note.setTextFormat(Qt.TextFormat.PlainText)
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {theme.TOOLS_WRITE_COLOR}; font-weight: 600;")
        note.setVisible(bool(show))
        lay.addWidget(note)
        return note

    def _decide(self, decision: str) -> None:
        for btn in (self.apply_btn, self.reject_btn, self.rest_btn):
            btn.setEnabled(False)
        self.decided.emit(int(self.view.get("id", 0)), decision)



class MessageWidget(QFrame):
    undoRequested = Signal()   # «Annulla le modifiche» della risposta

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
        sources: list[dict] | None = None,
        tool_calls: list[dict] | None = None,
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

        # -- chiamate dei tool, richiudibili (tra pensiero e contenuto) --
        self._tool_calls: list[dict] = []
        self._tools_expanded = False
        self.tools_btn = QToolButton(self)
        self.tools_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tools_btn.clicked.connect(self._on_tools_toggle)
        self.tools_btn.hide()
        self.tools_label = QLabel(self)
        self.tools_label.setTextFormat(Qt.TextFormat.PlainText)
        self.tools_label.setWordWrap(True)
        self.tools_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.tools_label.hide()
        self._style_tools()
        inner.addWidget(self.tools_btn, 0, Qt.AlignmentFlag.AlignLeft)
        inner.addWidget(self.tools_label)
        # richiesta di conferma di una modifica (solo nella risposta in corso)
        self._inner = inner
        self.change_card: ChangeCard | None = None
        if tool_calls:
            self.set_tool_calls(tool_calls)

        chips = []
        if role == "user" and web:
            chips.append(_("🌐 Ricerca web"))
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
        # i link li apre _on_link: «Copia» dei blocchi di codice resta qui,
        # nel browser vanno solo http/https
        self.label.setOpenExternalLinks(False)
        self.label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.label.linkActivated.connect(self._on_link)
        self._copied_block: int | None = None   # blocco appena copiato («Copiato ✓»)
        # menu contestuale dedicato (tasto destro): «Copia messaggio» sempre
        # disponibile, senza dover prima selezionare il testo
        self.label.installEventFilter(self)
        inner.addWidget(self.label)

        # -- fonti della ricerca web, richiudibili (sotto al contenuto) ----
        self._sources: list[dict] = []
        self._sources_expanded = False
        self.sources_btn = QToolButton(self)
        self.sources_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sources_btn.setStyleSheet(
            "QToolButton { border: none; background: transparent; "
            f"color: {self._think_color}; padding: 0; }}"
        )
        self.sources_btn.clicked.connect(self._on_sources_toggle)
        self.sources_btn.hide()
        self.sources_label = QLabel(self)
        self.sources_label.setTextFormat(Qt.TextFormat.RichText)
        self.sources_label.setWordWrap(True)
        self.sources_label.setOpenExternalLinks(True)
        self.sources_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.sources_label.hide()
        inner.addWidget(self.sources_btn, 0, Qt.AlignmentFlag.AlignLeft)
        inner.addWidget(self.sources_label)
        if sources:
            self.set_sources(sources)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        # annulla le modifiche ai file fatte da questa risposta (0.5, W4)
        self.undo_btn = QToolButton(self)
        self.undo_btn.setText(_("↶ Annulla le modifiche"))
        self.undo_btn.setToolTip(_("Rimette i file com'erano prima di questa risposta"))
        self.undo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.undo_btn.setStyleSheet(
            "QToolButton { border: none; background: transparent; "
            f"color: {theme.TOOLS_WRITE_COLOR}; padding: 0; }}"
        )
        self.undo_btn.clicked.connect(self.undoRequested)
        self.undo_btn.hide()
        meta_row.addWidget(self.undo_btn)
        meta_row.addStretch(1)
        self.meta = QLabel(self)
        self.meta.setObjectName("metaLabel")
        meta_row.addWidget(self.meta)
        # pulsante di copia su TUTTI i messaggi, non solo dell'assistente
        self.copy_btn = QToolButton(self)
        self.copy_btn.setText("⧉")
        icons.set_icon(self.copy_btn, "copy", theme.palette_for(theme_name)["dim"], 14, fallback="⧉")
        self.copy_btn.setToolTip(_("Copia messaggio"))
        self.copy_btn.clicked.connect(self._copy)
        # visibile al passaggio del mouse: trasparente invece che nascosto,
        # così la bolla non cambia altezza
        self._copy_fx = QGraphicsOpacityEffect(self.copy_btn)
        self._copy_fx.setOpacity(0.0)
        self.copy_btn.setGraphicsEffect(self._copy_fx)
        meta_row.addWidget(self.copy_btn)
        inner.addLayout(meta_row)

        self._update_meta(show_ts, stats=None)
        self.set_text(text)

    # ------------------------------------------------------------------ API

    def enterEvent(self, ev) -> None:  # noqa: N802 (API Qt)
        self._copy_fx.setOpacity(1.0)
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:  # noqa: N802 (API Qt)
        self._copy_fx.setOpacity(0.0)
        super().leaveEvent(ev)

    def _render(self) -> str:
        bg, fg, inline = theme.code_colors(self.theme_name)
        return md_to_html(self.raw, bg, fg, inline,
                          copy_labels=(_("codice"), _("Copia"), _("Copiato ✓")),
                          copied=self._copied_block)

    def _on_link(self, url: str) -> None:
        if url.startswith(COPY_LINK):
            try:
                idx = int(url[len(COPY_LINK):])
                code = code_blocks(self.raw)[idx]
            except (ValueError, IndexError):
                return
            QGuiApplication.clipboard().setText(code)
            self._copied_block = idx
            self.label.setText(self._render())
            QTimer.singleShot(1500, self._clear_copied)
        elif url.startswith(("http://", "https://")):
            QDesktopServices.openUrl(QUrl(url))

    def _clear_copied(self) -> None:
        if self._copied_block is not None:
            self._copied_block = None
            if self.raw:
                self.label.setText(self._render())

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.label and ev.type() == QEvent.Type.ContextMenu:
            menu = QMenu(self)
            act_copy = menu.addAction(_("📋  Copia messaggio"))
            menu.addSeparator()
            act_sel = menu.addAction(_("Seleziona tutto"))
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
        if raw:
            self.label.setText(self._render())
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
        label = _("sta pensando…") if self._think_streaming else _("Pensiero")
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
            self.label.setText(_("(nessuna risposta)"))
        self._stats = stats
        self._update_meta(show_ts, stats)

    # ------------------------------------------------------------ strumenti

    def set_tool_calls(self, calls: list[dict]) -> None:
        """Chiamate dei tool della risposta (`tools.calls_view`), richiuse.

        Durante la risposta il chiamante passa ogni volta l'elenco completo:
        l'intestazione mostra il tool in corso, il blocco resta come
        l'utente l'ha lasciato (aperto o chiuso).
        """
        self._tool_calls = list(calls or [])
        if not self._tool_calls:
            self.tools_btn.hide()
            self.tools_label.hide()
            return
        self.tools_btn.show()
        self._sync_tools_widgets()

    def _sync_tools_widgets(self) -> None:
        arrow = "▾" if self._tools_expanded else "▸"
        waiting = [c for c in self._tool_calls if c.get("state") == "waiting"]
        running = [c for c in self._tool_calls if c.get("state") == "running"]
        if waiting:
            head = _('{name}: attende la tua conferma').format(name=waiting[0]["name"])
        elif running:
            head = _('{name} in corso…').format(name=running[0]["name"])
        else:
            head = _('Strumenti usati ({n})').format(n=len(self._tool_calls))
        self.tools_btn.setText(f"🔧 {head} {arrow}")
        if self._tools_expanded:
            self.tools_label.setText(self._tools_text())
        self.tools_label.setVisible(self._tools_expanded)

    def _tools_text(self) -> str:
        blocks = []
        outcomes = {"rejected": _("rifiutata"), "undone": _("annullata")}
        for c in self._tool_calls:
            outcome = c.get("outcome")
            mark = "…" if c.get("state") in ("running", "waiting") else \
                "↶" if outcome == "undone" else ("✗" if c.get("error") else "✓")
            head = f"{mark} {c['name']}({arguments_summary(c.get('arguments') or {})})"
            if outcome in outcomes:
                head += f" — {outcomes[outcome]}"
            lines = [head]
            result = result_preview(c["name"], c.get("result") or "")
            if result:
                preview = result[:TOOL_PREVIEW_CHARS].splitlines()[:TOOL_PREVIEW_LINES]
                more = len(result) > TOOL_PREVIEW_CHARS or \
                    len(result.splitlines()) > TOOL_PREVIEW_LINES
                lines += ["    " + ln for ln in preview]
                if more:
                    lines.append("    …")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def set_undo_available(self, on: bool) -> None:
        self.undo_btn.setVisible(on)

    def show_change(self, view: dict) -> ChangeCard:
        """Mostra la richiesta di conferma (sostituisce quella di prima)."""
        self.clear_change()
        self.stop_animation()
        if not self.raw:
            self.label.setText("")   # niente puntini sotto la scheda: la risposta aspetta
        card = ChangeCard(view, self.theme_name, self)
        # sotto al blocco dei tool, sopra al testo
        self._inner.insertWidget(self._inner.indexOf(self.tools_label) + 1, card)
        self.change_card = card
        return card

    def clear_change(self, req_id: int | None = None) -> None:
        """Toglie la richiesta (solo quella con `req_id`, se dato)."""
        card = self.change_card
        if card is None or (req_id is not None and card.view.get("id") != req_id):
            return
        self.change_card = None
        card.hide()
        card.deleteLater()
        if not self.raw and self.role == "assistant":
            self.start_animation()

    def _on_tools_toggle(self) -> None:
        self._tools_expanded = not self._tools_expanded
        self._sync_tools_widgets()

    def _style_tools(self) -> None:
        self.tools_btn.setStyleSheet(
            "QToolButton { border: none; background: transparent; "
            f"color: {self._think_color}; padding: 0; }}"
        )
        self.tools_label.setStyleSheet(
            f"color: {self._think_color}; font-family: monospace;"
        )

    def set_sources(self, sources: list[dict]) -> None:
        """Fonti della ricerca web (web_search.web_sources), richiuse."""
        self._sources = list(sources or [])
        self._sources_expanded = False
        if not self._sources:
            self.sources_btn.hide()
            self.sources_label.hide()
            return
        # titoli e indirizzi arrivano dai motori di ricerca: sempre escapati,
        # e web_sources lascia passare solo http/https
        rows = [
            f'[{s["n"]}] <a href="{html.escape(s["url"], quote=True)}">'
            f'{html.escape(s["title"])}</a> '
            f'<span style="color: {self._think_color}">— {html.escape(s["host"])}</span>'
            for s in self._sources
        ]
        self.sources_label.setText("<br>".join(rows))
        self.sources_label.setToolTip(_("Si aprono nel browser"))
        self.sources_btn.show()
        self._sync_sources_widgets()

    def _sync_sources_widgets(self) -> None:
        arrow = "▾" if self._sources_expanded else "▸"
        self.sources_btn.setText(_('{arrow} 🌐 Fonti ({n})').format(arrow=arrow, n=len(self._sources)))
        self.sources_label.setVisible(self._sources_expanded)

    def _on_sources_toggle(self) -> None:
        self._sources_expanded = not self._sources_expanded
        self._sync_sources_widgets()

    def set_stats(self, stats: str | None) -> None:
        """Statistiche della risposta, mostrate nella meta insieme all'orario."""
        self._stats = stats
        self._update_meta(self._show_ts, stats)

    def refresh_theme(self, theme_name: str, show_ts: bool) -> None:
        self.theme_name = theme_name
        self._think_color = theme.palette_for(theme_name)["dim"]
        icons.set_icon(self.copy_btn, "copy", self._think_color, 14, fallback="⧉")
        self.think_btn.setStyleSheet(
            "QToolButton { border: none; background: transparent; "
            f"color: {self._think_color}; padding: 0; }}"
        )
        self.think_label.setStyleSheet(
            f"color: {self._think_color}; font-style: italic;"
        )
        self.sources_btn.setStyleSheet(
            "QToolButton { border: none; background: transparent; "
            f"color: {self._think_color}; padding: 0; }}"
        )
        self._style_tools()
        if self._sources:
            expanded = self._sources_expanded
            self.set_sources(self._sources)   # riscrive il colore del dominio
            self._sources_expanded = expanded
            self._sync_sources_widgets()
        self._update_meta(show_ts, self._stats)
        if self.raw:
            self.label.setText(self._render())

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
    """Nota informativa centrata (es. nessun modello disponibile).

    Il pulsante ✕ la rimuove dalla chat: è un avviso transitorio, non parte
    della conversazione (e non viene nemmeno salvato su disco).
    """

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setProperty("bubble", "error")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 10, 8)
        lay.setSpacing(8)
        self.label = QLabel(text, self)
        # i testi delle note contengono nomi di file: mai interpretati come HTML
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setProperty("error", True)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.label, 1)
        self.close_btn = QToolButton(self)
        self.close_btn.setText("✕")
        self.close_btn.setToolTip(_("Nascondi questa nota"))
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setStyleSheet(
            "QToolButton { border: none; background: transparent; padding: 0; }"
        )
        self.close_btn.clicked.connect(self._dismiss)
        lay.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)

    def _dismiss(self) -> None:
        # la nota vive dentro la riga creata da ChatArea._wrap_row: eliminando
        # la riga sparisce anche il buco che lascerebbe nella chat. Se per
        # qualche motivo non è in una riga, si elimina da sola (mai il parent,
        # che potrebbe essere l'area chat stessa)
        row = self.parentWidget()
        lay = row.layout() if row is not None else None
        if lay is not None and lay.indexOf(self) >= 0:
            row.deleteLater()
        else:
            self.deleteLater()

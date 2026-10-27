"""Barra laterale in stile ChatGPT: elenco conversazioni + azioni."""
from __future__ import annotations

import datetime
import unicodedata

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, icons, theme
from ..i18n import N_, _


# spazio orizzontale di una voce oltre al testo: margini (6 + 6) e padding
# (8 + 8) di QListWidget::item nel QSS di theme.py, più un po' di respiro
ITEM_CHROME_W = 34

# gruppi dell'elenco, dal più recente: (chiave, etichetta da tradurre)
DATE_GROUPS = (
    ("today", N_("Oggi")),
    ("yesterday", N_("Ieri")),
    ("week", N_("Ultimi 7 giorni")),
    ("month", N_("Ultimi 30 giorni")),
    ("older", N_("Più vecchie")),
)


def date_group(ts: float, now: datetime.datetime | None = None) -> str:
    """Gruppo di una conversazione dall'ultima modifica (giorni del calendario
    locale, non intervalli di 24 ore)."""
    now = now or datetime.datetime.now()
    try:
        day = datetime.datetime.fromtimestamp(float(ts or 0)).date()
    except (OverflowError, OSError, ValueError):
        return "older"
    days = (now.date() - day).days
    if days <= 0:
        return "today"   # anche date nel futuro (orologio spostato)
    if days == 1:
        return "yesterday"
    if days < 7:
        return "week"
    if days < 30:
        return "month"
    return "older"


def _fold(text: str) -> str:
    """Testo per la ricerca: senza maiuscole né accenti («Città» trova «citta»)."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


class ChatSidebar(QWidget):
    newChatRequested = Signal()
    chatSelected = Signal(str)      # id conversazione
    chatRenamed = Signal(str, str)  # id, nuovo titolo
    chatDeleted = Signal(str)       # id
    chatArchived = Signal(str, bool)  # id, True = archivia / False = ripristina
    modelsRequested = Signal()
    companionRequested = Signal()
    settingsRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(248)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("OllaDesk", self)
        title.setStyleSheet("font-weight: 700; font-size: 14px;")
        header.addWidget(title)
        header.addStretch(1)
        version = QLabel(f"v{__version__}", self)
        version.setObjectName("metaLabel")
        version.setToolTip(_('Versione di OllaDesk: {version}').format(version=__version__))
        header.addWidget(version)
        lay.addLayout(header)

        # «Nuova chat» e, accanto, l'archivio: entrambi decidono cosa mostra
        # l'elenco qui sotto, quindi stanno in cima e insieme
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        self.new_btn = QPushButton(_("Nuova chat"), self)
        self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_btn.setToolTip(_("Nuova conversazione (Ctrl+N)"))
        self.new_btn.clicked.connect(self.newChatRequested.emit)
        top_row.addWidget(self.new_btn, 1)
        # passa dall'elenco principale all'archivio e ritorno; nascosto finché
        # non c'è nulla di archiviato
        self.archive_btn = QPushButton(self)
        self.archive_btn.setObjectName("archiveBtn")
        self.archive_btn.setCheckable(True)
        self.archive_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.archive_btn.clicked.connect(self._toggle_archive)
        # compatto: lo spazio della riga resta a «Nuova chat»
        self.archive_btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.archive_btn.setMaximumWidth(64)
        top_row.addWidget(self.archive_btn)
        lay.addLayout(top_row)
        # stessa altezza di «Nuova chat»: l'emoji alzerebbe il pulsante
        self.archive_btn.setFixedHeight(self.new_btn.sizeHint().height())

        # ricerca nei titoli: vale per l'elenco mostrato (conversazioni o archivio)
        self.search = QLineEdit(self)
        self.search.setObjectName("chatSearch")
        self.search.setPlaceholderText(_("Cerca conversazioni"))
        self.search.setToolTip(_("Filtra le conversazioni per titolo (Ctrl+K)"))
        self.search.setClearButtonEnabled(True)
        self._search_action = QAction(self.search)
        self.search.addAction(self._search_action, QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(lambda _t: self.set_chats(self._chats, self._current_id))
        QShortcut(QKeySequence("Escape"), self.search, self.search.clear,
                  context=Qt.ShortcutContext.WidgetShortcut)
        lay.addWidget(self.search)

        # intestazione della vista «Archivio»: senza, l'elenco sembra quello solito
        self.archive_label = QLabel(_("🗄  Archivio: conversazioni archiviate"), self)
        self.archive_label.setObjectName("metaLabel")
        self.archive_label.setWordWrap(True)
        self.archive_label.hide()
        lay.addWidget(self.archive_label)

        self.list = QListWidget(self)
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        # i titoli si accorciano alla larghezza della colonna (_elide): mai
        # una barra orizzontale, nemmeno per un attimo durante un resize
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.viewport().installEventFilter(self)
        self.list.itemClicked.connect(self._on_clicked)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_menu)
        lay.addWidget(self.list, 1)

        self.models_btn = QPushButton(_("Modelli"), self)
        self.models_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.models_btn.setToolTip(_("Scarica o rimuovi modelli Ollama"))
        self.models_btn.clicked.connect(self.modelsRequested.emit)
        lay.addWidget(self.models_btn)

        self.companion_btn = QPushButton(_("Companion"), self)
        self.companion_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.companion_btn.setToolTip(_("OllaDesk dal telefono: attivazione, abbinamento, dispositivi (Ctrl+D)"))
        self.companion_btn.clicked.connect(self.companionRequested.emit)
        lay.addWidget(self.companion_btn)

        self.settings_btn = QPushButton(_("Impostazioni"), self)
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.clicked.connect(self.settingsRequested.emit)
        lay.addWidget(self.settings_btn)

        self._chats: list[dict] = []
        self._show_archived = False   # vista «Archivio» al posto dell'elenco
        self._current_id: str | None = None
        self._n_archived = 0
        self._palette = theme.palette_for("dark")
        self.apply_icons("dark")

    def apply_icons(self, theme_name: str) -> None:
        """Icone dei pulsanti nei colori del tema (anche al cambio di tema)."""
        self._palette = theme.palette_for(theme_name)
        fg = self._palette["text"]
        for btn, name in ((self.new_btn, "plus"), (self.models_btn, "boxes"),
                          (self.companion_btn, "smartphone"), (self.settings_btn, "settings")):
            icons.set_icon(btn, name, fg, 16)
            # QPushButton non ha uno spazio tra icona e testo: lo dà uno spazio
            # in testa all'etichetta, solo se l'icona c'è
            label = btn.text().strip()
            btn.setText(("  " if not btn.icon().isNull() else "") + label)
        search_icon = icons.icon("search", self._palette["dim"], 14)
        if search_icon is not None:
            self._search_action.setIcon(search_icon)
        self._sync_archive_btn(self._n_archived)
        if self._chats:
            self.set_chats(self._chats, self._current_id)   # colore delle intestazioni

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    # ------------------------------------------------------------------ API

    def set_chats(self, chats: list[dict], current_id: str | None = None) -> None:
        self._chats = sorted(chats, key=lambda c: c.get("updated", 0), reverse=True)
        self._current_id = current_id
        archived = [c for c in self._chats if c.get("archived")]
        if self._show_archived and not archived:
            self._show_archived = False   # archivio svuotato: torna all'elenco
        shown = archived if self._show_archived else [
            c for c in self._chats if not c.get("archived")]
        query = _fold(self.search.text().strip())
        if query:
            shown = [c for c in shown if query in _fold(c.get("title") or "")]
        self._sync_archive_btn(len(archived))
        self.list.blockSignals(True)
        self.list.clear()
        if query and not shown:
            self._add_header(_("Nessun risultato"))
        now = datetime.datetime.now()
        groups: dict[str, list[dict]] = {}
        for c in shown:
            groups.setdefault(date_group(c.get("updated", 0), now), []).append(c)
        for key, label in DATE_GROUPS:
            if key not in groups:
                continue
            self._add_header(_(label))
            for c in groups[key]:
                title = c.get("title") or _("Conversazione")
                item = QListWidgetItem(self._elide(title))
                item.setData(Qt.ItemDataRole.UserRole, c["id"])
                item.setToolTip(title)
                self.list.addItem(item)
                if c["id"] == current_id:
                    self.list.setCurrentItem(item)
        self.list.blockSignals(False)

    def _add_header(self, text: str) -> None:
        """Intestazione di gruppo: non selezionabile, piccola e grigia."""
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        font = item.font()
        font.setPointSizeF(max(7.0, self.font().pointSizeF() - 1))
        font.setBold(True)
        item.setFont(font)
        item.setForeground(QColor(self._palette["dim"]))
        self.list.addItem(item)

    def chat_ids(self) -> list[str]:
        """Id delle conversazioni mostrate, nell'ordine dell'elenco."""
        return [d for i in range(self.list.count())
                if (d := self.list.item(i).data(Qt.ItemDataRole.UserRole))]

    # -------------------------------------------------------------- interni

    def showing_archive(self) -> bool:
        return self._show_archived

    def _sync_archive_btn(self, n_archived: int) -> None:
        self._n_archived = n_archived
        fg = self._palette["text"]
        if self._show_archived:
            icons.set_icon(self.archive_btn, "arrow-left", fg, 16, fallback="←")
            self.archive_btn.setToolTip(_("Torna all'elenco delle conversazioni"))
        else:
            if icons.icon("archive", fg, 16) is None:
                self.archive_btn.setText(f"🗄 {n_archived}")
            else:
                icons.set_icon(self.archive_btn, "archive", fg, 16)
                self.archive_btn.setText(f" {n_archived}")
            self.archive_btn.setToolTip(
                _('Archivio: {n_archived} conversazioni archiviate, fuori dall\'elenco ma non '
                  'eliminate').format(n_archived=n_archived))
        self.archive_btn.setChecked(self._show_archived)
        self.archive_btn.setVisible(self._show_archived or n_archived > 0)
        self.archive_label.setVisible(self._show_archived)

    def _toggle_archive(self) -> None:
        self._show_archived = not self._show_archived
        self.set_chats(self._chats, self._current_id)

    def _elide(self, text: str) -> str:
        """Titolo accorciato con «…» alla larghezza in pixel della colonna.

        Un limite in caratteri non bastava: con lettere larghe (o in un'altra
        lingua) la voce superava la colonna e compariva la barra orizzontale.
        """
        avail = self.list.viewport().width() - ITEM_CHROME_W
        return self.list.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, max(40, avail))

    def _reelide(self) -> None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) is None:
                continue   # intestazione di gruppo
            item.setText(self._elide(item.toolTip()))   # il tooltip ha il titolo intero

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.list.viewport() and ev.type() == QEvent.Type.Resize:
            self._reelide()
        return super().eventFilter(obj, ev)

    def _on_clicked(self, item: QListWidgetItem) -> None:
        chat_id = item.data(Qt.ItemDataRole.UserRole)
        if chat_id:
            self.chatSelected.emit(chat_id)

    def _on_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None or not item.data(Qt.ItemDataRole.UserRole):
            return
        chat_id = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        act_rename = menu.addAction(_("✏  Rinomina"))
        if self._show_archived:
            act_archive = menu.addAction(_("↩  Ripristina"))
        else:
            act_archive = menu.addAction(_("🗄  Archivia"))
        act_del = menu.addAction(_("🗑  Elimina"))
        chosen = menu.exec(self.list.mapToGlobal(pos))
        if chosen is act_archive:
            self.chatArchived.emit(chat_id, not self._show_archived)
        elif chosen is act_rename:
            from PySide6.QtWidgets import QInputDialog

            current = item.toolTip()   # titolo completo (il testo è abbreviato)
            name, ok = QInputDialog.getText(self, _("Rinomina conversazione"), _("Titolo:"), text=current)
            if ok and name.strip():
                self.chatRenamed.emit(chat_id, name.strip())
        elif chosen is act_del:
            from PySide6.QtWidgets import QMessageBox

            ret = QMessageBox.question(
                self,
                _("Elimina conversazione"),
                _('Eliminare definitivamente «{title}»?').format(title=item.toolTip()),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret == QMessageBox.StandardButton.Yes:
                self.chatDeleted.emit(chat_id)

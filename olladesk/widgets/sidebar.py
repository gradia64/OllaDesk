"""Barra laterale in stile ChatGPT: elenco conversazioni + azioni."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..i18n import _


# spazio orizzontale di una voce oltre al testo: margini (6 + 6) e padding
# (8 + 8) di QListWidget::item nel QSS di theme.py, più un po' di respiro
ITEM_CHROME_W = 34


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

        self.new_btn = QPushButton(_("＋  Nuova chat"), self)
        self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_btn.clicked.connect(self.newChatRequested.emit)
        lay.addWidget(self.new_btn)

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

        # passa dall'elenco principale all'archivio e ritorno; nascosto finché
        # non c'è nulla di archiviato
        self.archive_btn = QPushButton(self)
        self.archive_btn.setObjectName("archiveBtn")
        self.archive_btn.setFlat(True)
        self.archive_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.archive_btn.clicked.connect(self._toggle_archive)
        lay.addWidget(self.archive_btn)

        self.models_btn = QPushButton(_("🧩  Modelli"), self)
        self.models_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.models_btn.setToolTip(_("Scarica o rimuovi modelli Ollama"))
        self.models_btn.clicked.connect(self.modelsRequested.emit)
        lay.addWidget(self.models_btn)

        self.companion_btn = QPushButton(_("📱  Companion"), self)
        self.companion_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.companion_btn.setToolTip(_("OllaDesk dal telefono: attivazione, abbinamento, dispositivi (Ctrl+D)"))
        self.companion_btn.clicked.connect(self.companionRequested.emit)
        lay.addWidget(self.companion_btn)

        self.settings_btn = QPushButton(_("⚙  Impostazioni"), self)
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.clicked.connect(self.settingsRequested.emit)
        lay.addWidget(self.settings_btn)

        self._chats: list[dict] = []
        self._show_archived = False   # vista «Archivio» al posto dell'elenco
        self._current_id: str | None = None
        self._sync_archive_btn(0)

    # ------------------------------------------------------------------ API

    def set_chats(self, chats: list[dict], current_id: str | None = None) -> None:
        self._chats = sorted(chats, key=lambda c: c.get("updated", 0), reverse=True)
        self._current_id = current_id
        archived = [c for c in self._chats if c.get("archived")]
        if self._show_archived and not archived:
            self._show_archived = False   # archivio svuotato: torna all'elenco
        shown = archived if self._show_archived else [
            c for c in self._chats if not c.get("archived")]
        self._sync_archive_btn(len(archived))
        self.list.blockSignals(True)
        self.list.clear()
        for c in shown:
            title = c.get("title") or _("Conversazione")
            item = QListWidgetItem(self._elide(title))
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            item.setToolTip(title)
            self.list.addItem(item)
            if c["id"] == current_id:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)

    # -------------------------------------------------------------- interni

    def showing_archive(self) -> bool:
        return self._show_archived

    def _sync_archive_btn(self, n_archived: int) -> None:
        if self._show_archived:
            self.archive_btn.setText(_("←  Conversazioni"))
            self.archive_btn.setToolTip(_("Torna all'elenco delle conversazioni"))
        else:
            self.archive_btn.setText(_('🗄  Archivio ({n_archived})').format(n_archived=n_archived))
            self.archive_btn.setToolTip(_("Conversazioni archiviate: fuori dall'elenco, non eliminate"))
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
            item.setText(self._elide(item.toolTip()))   # il tooltip ha il titolo intero

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.list.viewport() and ev.type() == QEvent.Type.Resize:
            self._reelide()
        return super().eventFilter(obj, ev)

    def _on_clicked(self, item: QListWidgetItem) -> None:
        self.chatSelected.emit(item.data(Qt.ItemDataRole.UserRole))

    def _on_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
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

"""Barra laterale in stile ChatGPT: elenco conversazioni + azioni."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
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


class ChatSidebar(QWidget):
    newChatRequested = Signal()
    chatSelected = Signal(str)      # id conversazione
    chatRenamed = Signal(str, str)  # id, nuovo titolo
    chatDeleted = Signal(str)       # id
    modelsRequested = Signal()
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
        lay.addLayout(header)

        self.new_btn = QPushButton("＋  Nuova chat", self)
        self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_btn.clicked.connect(self.newChatRequested.emit)
        lay.addWidget(self.new_btn)

        self.list = QListWidget(self)
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.itemClicked.connect(self._on_clicked)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_menu)
        lay.addWidget(self.list, 1)

        self.models_btn = QPushButton("🧩  Modelli", self)
        self.models_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.models_btn.setToolTip("Scarica o rimuovi modelli Ollama")
        self.models_btn.clicked.connect(self.modelsRequested.emit)
        lay.addWidget(self.models_btn)

        self.settings_btn = QPushButton("⚙  Impostazioni", self)
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.clicked.connect(self.settingsRequested.emit)
        lay.addWidget(self.settings_btn)

        self._chats: list[dict] = []

    # ------------------------------------------------------------------ API

    def set_chats(self, chats: list[dict], current_id: str | None = None) -> None:
        self._chats = sorted(chats, key=lambda c: c.get("updated", 0), reverse=True)
        self.list.blockSignals(True)
        self.list.clear()
        for c in self._chats:
            title = c.get("title") or "Conversazione"
            item = QListWidgetItem(self._elide(title))
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            item.setToolTip(title)
            self.list.addItem(item)
            if c["id"] == current_id:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)

    def select_chat(self, chat_id: str | None) -> None:
        self.list.blockSignals(True)
        if chat_id is None:
            self.list.clearSelection()
        else:
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.ItemDataRole.UserRole) == chat_id:
                    self.list.setCurrentRow(i)
                    break
        self.list.blockSignals(False)

    def set_busy(self, busy: bool) -> None:
        self.list.setEnabled(not busy)
        self.new_btn.setEnabled(not busy)

    # -------------------------------------------------------------- interni

    @staticmethod
    def _elide(text: str, n: int = 30) -> str:
        return text if len(text) <= n else text[: n - 1] + "…"

    def _on_clicked(self, item: QListWidgetItem) -> None:
        self.chatSelected.emit(item.data(Qt.ItemDataRole.UserRole))

    def _on_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        chat_id = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        act_rename = menu.addAction("✏  Rinomina")
        act_del = menu.addAction("🗑  Elimina")
        chosen = menu.exec(self.list.mapToGlobal(pos))
        if chosen is act_rename:
            from PySide6.QtWidgets import QInputDialog

            current = item.text()
            name, ok = QInputDialog.getText(self, "Rinomina conversazione", "Titolo:", text=current)
            if ok and name.strip():
                self.chatRenamed.emit(chat_id, name.strip())
        elif chosen is act_del:
            self.chatDeleted.emit(chat_id)

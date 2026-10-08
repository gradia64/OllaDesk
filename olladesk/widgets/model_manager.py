"""Gestione modelli Ollama: elenco installati, scaricamento (pull) ed eliminazione."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCompleter,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..ollama_client import ApiWorker, shutdown_workers, visible_models

SUGGESTED_MODELS = [
    "llama3.2:3b", "llama3.1:8b", "qwen3:8b", "qwen2.5-coder:7b",
    "gemma3:4b", "mistral:7b", "deepseek-r1:8b", "phi4-mini",
    "llava:7b", "granite3.3:2b", "nomic-embed-text",
]


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class ModelManagerDialog(QDialog):
    """Elenco dei modelli installati con pull ed eliminazione.

    Scaricamento ed eliminazione passano dal motore, come quelli del
    telefono: una sola operazione alla volta, visibile da entrambi, e
    niente eliminazione mentre il PC risponde. Chiudere il dialogo non
    annulla uno scaricamento: continua e riappare riaprendolo.

    `changed` è True se qualcosa è stato scaricato/eliminato mentre il
    dialogo era aperto.
    """

    def __init__(self, engine, parent=None, current_model: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Gestione modelli — OllaDesk")
        self.setMinimumSize(680, 540)
        self.engine = engine
        self.host = engine.settings["host"]
        self.current_model = current_model   # modello selezionato nella chat
        self.changed = False

        self._list_worker: ApiWorker | None = None
        self._task_seen: tuple | None = None   # (op, name, state) già mostrato

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        # --- scaricamento ----------------------------------------------
        head = QHBoxLayout()
        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText("Nome modello da scaricare, es. llama3.2:3b")
        self.name_edit.setCompleter(QCompleter(SUGGESTED_MODELS, self))
        self.name_edit.returnPressed.connect(self._start_pull)
        head.addWidget(self.name_edit, 1)
        self.pull_btn = QPushButton("⬇  Scarica", self)
        self.pull_btn.setObjectName("primaryBtn")
        self.pull_btn.clicked.connect(self._start_pull)
        head.addWidget(self.pull_btn)
        lay.addLayout(head)

        hint = QLabel(
            'Biblioteca dei modelli: <a href="https://ollama.com/library">ollama.com/library</a> '
            "(l'esatto nome del modello va incollato qui sopra)",
            self,
        )
        hint.setObjectName("metaLabel")
        hint.setOpenExternalLinks(True)
        lay.addWidget(hint)

        self.progress = QProgressBar(self)
        self.progress.hide()
        lay.addWidget(self.progress)
        self.progress_label = QLabel("", self)
        self.progress_label.setObjectName("metaLabel")
        self.progress_label.hide()
        self.progress_label.setWordWrap(True)
        lay.addWidget(self.progress_label)
        self.cancel_btn = QPushButton("✕  Annulla scaricamento", self)
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self.engine.cancel_model_task)
        lay.addWidget(self.cancel_btn)

        # --- elenco modelli --------------------------------------------
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Modello", "Dimensione", "Parametri", "Quantizzazione"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        lay.addWidget(self.tree, 1)

        btns = QHBoxLayout()
        self.refresh_btn = QPushButton("↻  Aggiorna", self)
        self.refresh_btn.clicked.connect(self.refresh_models)
        btns.addWidget(self.refresh_btn)
        self.delete_btn = QPushButton("🗑  Elimina", self)
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._delete_selected)
        btns.addWidget(self.delete_btn)
        btns.addStretch(1)
        self.close_btn = QPushButton("Chiudi", self)
        self.close_btn.clicked.connect(self.reject)
        btns.addWidget(self.close_btn)
        lay.addLayout(btns)

        engine.model_task_changed.connect(self._on_task)
        engine.busy_changed.connect(self._update_buttons)
        self._on_task()   # operazione già in corso (anche dal telefono)
        self.refresh_models()

    # ------------------------------------------------------------- elenco

    def refresh_models(self) -> None:
        if self._list_worker is not None:
            return
        self.refresh_btn.setEnabled(False)
        self._list_worker = ApiWorker(self.host, "/api/tags", self)
        self._list_worker.ready.connect(self._on_models)
        self._list_worker.failed.connect(self._on_models_failed)
        self._list_worker.finished.connect(self._list_worker.deleteLater)
        self._list_worker.finished.connect(lambda: setattr(self, "_list_worker", None))
        self._list_worker.start()

    def _on_models(self, data: object) -> None:
        self.refresh_btn.setEnabled(True)
        self.tree.clear()
        models = visible_models(data.get("models") if isinstance(data, dict) else [])
        bold = QFont()
        bold.setBold(True)
        for m in models:
            det = m.get("details", {})
            name = m.get("name") or m.get("model") or "?"
            item = QTreeWidgetItem(
                [
                    name,
                    human_size(m.get("size", 0)),
                    det.get("parameter_size", "—"),
                    det.get("quantization_level", "—"),
                ]
            )
            # nome pulito nel UserRole: il testo della colonna può avere il
            # contrassegno «●» senza corrompere eliminazione e download
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            if self.current_model and name == self.current_model:
                item.setText(0, f"● {name}")
                item.setFont(0, bold)
                item.setToolTip(0, "Modello in uso nella conversazione corrente")
            self.tree.addTopLevelItem(item)
        self._update_buttons()

    def _on_models_failed(self, err: str) -> None:
        self.refresh_btn.setEnabled(True)
        self.tree.clear()
        item = QTreeWidgetItem([f"⚠ {err}", "", "", ""])
        item.setDisabled(True)
        self.tree.addTopLevelItem(item)
        self._update_buttons()

    def _selected_model(self) -> str | None:
        item = self.tree.currentItem()
        if not item or not item.text(1):
            return None
        return item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)

    def _update_buttons(self, *_a) -> None:
        running = self._task_running()
        self.pull_btn.setEnabled(not running)
        self.name_edit.setEnabled(not running)
        self.delete_btn.setEnabled(
            self._selected_model() is not None and not running and not self.engine.busy()
        )

    # ------------------------------------------- scaricamento ed eliminazione

    def _task_running(self) -> bool:
        t = self.engine.model_task()
        return bool(t and t["state"] == "running")

    def _start_pull(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.information(self, "Scarica modello", "Inserisci il nome del modello (es. llama3.2:3b).")
            return
        err = self.engine.pull_model(name)
        if err:
            QMessageBox.information(self, "Scarica modello", f"Scaricamento non avviato: {err}.")

    def _delete_selected(self) -> None:
        model = self._selected_model()
        if not model:
            return
        ret = QMessageBox.question(
            self,
            "Elimina modello",
            f"Eliminare definitivamente «{model}» dal disco?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        err = self.engine.delete_model(model)
        if err:
            QMessageBox.warning(self, "Elimina modello", f"Eliminazione non avviata: {err}.")

    def _on_task(self) -> None:
        """Stato dell'operazione del motore, avviata da qui o dal telefono."""
        t = self.engine.model_task()
        key = (t["op"], t["name"], t["state"]) if t else None
        running = bool(t and t["state"] == "running")
        self.progress.setVisible(running)
        self.cancel_btn.setVisible(running and t["op"] == "pull")
        if t is None or (not running and self._task_seen is None):
            # nessuna operazione, o una finita prima dell'apertura
            self.progress_label.hide()
        elif running:
            self.progress_label.show()
            if t["op"] == "delete":
                self.progress.setRange(0, 0)
                self.progress_label.setText(f"Eliminazione di {t['name']}…")
            elif t["pct"] is None:
                self.progress.setRange(0, 0)
                self.progress_label.setText(f"{t['name']}: {t['status'] or 'avvio scaricamento'}…")
            else:
                self.progress.setRange(0, 100)
                self.progress.setValue(t["pct"])
                status = t["status"].split(" ")[0] if t["status"] else "scaricamento"
                self.progress_label.setText(f"{t['name']}: {status}… {t['pct']}%")
        elif key != self._task_seen:
            self.progress_label.show()
            if t["state"] == "done":
                self.changed = True
                done = "Scaricamento completato" if t["op"] == "pull" else f"«{t['name']}» eliminato"
                self.progress_label.setText(f"✓ {done}")
                self.refresh_models()
            else:
                self.progress_label.setText(f"⚠ {t['error']}")
        self._task_seen = key
        self._update_buttons()

    def done(self, r: int) -> None:
        # chiusura per qualunque via (Chiudi, Esc, X): nessun worker deve
        # sopravvivere legato al dialogo, che il chiamante poi distrugge.
        # L'operazione sui modelli appartiene al motore e continua
        for sig, slot in ((self.engine.model_task_changed, self._on_task),
                          (self.engine.busy_changed, self._update_buttons)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        shutdown_workers([self._list_worker])
        super().done(r)

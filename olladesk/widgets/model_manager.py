"""Gestione modelli Ollama: elenco installati, scaricamento (pull) ed eliminazione."""
from __future__ import annotations

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

from ..ollama_client import ApiWorker, PostWorker, PullWorker, shutdown_workers

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

    `changed` è True se qualcosa è stato scaricato/eliminato: il chiamante
    può ricaricare l'elenco dei modelli alla chiusura.
    """

    def __init__(self, host: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gestione modelli — OllaDesk")
        self.setMinimumSize(680, 540)
        self.host = host
        self.changed = False

        self._pull_worker: PullWorker | None = None
        self._cancelled_pulls: list[PullWorker] = []   # annullati, in chiusura
        self._list_worker: ApiWorker | None = None
        self._delete_worker: PostWorker | None = None

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
        self.cancel_btn.clicked.connect(self._cancel_pull)
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
        models = data.get("models", []) if isinstance(data, dict) else []
        for m in models:
            det = m.get("details", {})
            item = QTreeWidgetItem(
                [
                    m.get("name") or m.get("model") or "?",
                    human_size(m.get("size", 0)),
                    det.get("parameter_size", "—"),
                    det.get("quantization_level", "—"),
                ]
            )
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
        return item.text(0) if item and item.text(1) else None

    def _update_buttons(self) -> None:
        self.delete_btn.setEnabled(self._selected_model() is not None and self._pull_worker is None)

    # ------------------------------------------------------------ pull

    def _start_pull(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.information(self, "Scarica modello", "Inserisci il nome del modello (es. llama3.2:3b).")
            return
        if self._pull_worker is not None:
            return
        self._pull_running_ui(True)
        self.progress.setRange(0, 0)
        self.progress_label.setText(f"Avvio scaricamento di {name}…")
        self._pull_worker = PullWorker(self.host, name, self)
        self._pull_worker.progress.connect(self._on_pull_progress)
        self._pull_worker.done.connect(self._on_pull_done)
        self._pull_worker.failed.connect(self._on_pull_failed)
        self._pull_worker.finished.connect(self._pull_worker.deleteLater)
        w = self._pull_worker
        w.finished.connect(lambda: self._pull_worker is w and setattr(self, "_pull_worker", None))
        w.start()

    def _pull_running_ui(self, running: bool) -> None:
        self.progress.setVisible(running)
        self.progress_label.setVisible(running)
        self.cancel_btn.setVisible(running)
        self.pull_btn.setEnabled(not running)
        self.name_edit.setEnabled(not running)
        self.delete_btn.setEnabled(not running and self._selected_model() is not None)

    def _on_pull_progress(self, data: dict) -> None:
        if self.sender() is not self._pull_worker:
            return
        status = data.get("status", "")
        total = data.get("total")
        completed = data.get("completed")
        if total and completed is not None:
            pct = int(completed * 100 / total)
            if self.progress.maximum() != 100:
                self.progress.setRange(0, 100)
            self.progress.setValue(pct)
            self.progress_label.setText(
                f"{status.split(' ')[0]}… {human_size(completed)} / {human_size(total)} ({pct}%)"
            )
        else:
            self.progress.setRange(0, 0)
            self.progress_label.setText(status or "…")

    def _on_pull_done(self) -> None:
        if self.sender() is not self._pull_worker:
            return   # segnale tardivo di un worker annullato
        self.changed = True
        self._pull_running_ui(False)
        self.progress_label.show()
        self.progress_label.setText("✓ Scaricamento completato")
        self.refresh_models()

    def _on_pull_failed(self, err: str) -> None:
        if self.sender() is not self._pull_worker:
            return   # l'annullamento ha già aggiornato la UI
        self._pull_running_ui(False)
        self.progress_label.show()
        self.progress_label.setText(f"⚠ {err}")

    def _cancel_pull(self) -> None:
        """Annulla lo scaricamento e ripristina SUBITO la UI.

        Il worker stoppato non emette più segnali (la connessione chiusa fa
        uscire il thread in silenzio): se non ripristiniamo qui, la barra di
        avanzamento resta visibile e «Scarica» disabilitato.
        """
        w = self._pull_worker
        if w is None:
            return
        w.stop()
        # sganciato subito: si può avviare un nuovo scaricamento senza
        # aspettare che il thread annullato termini
        self._pull_worker = None
        self._cancelled_pulls.append(w)
        w.finished.connect(lambda: self._cancelled_pulls.remove(w))
        self._pull_running_ui(False)
        self.progress_label.show()
        self.progress_label.setText("⚠ Scaricamento annullato")

    # --------------------------------------------------------- eliminazione

    def _delete_selected(self) -> None:
        model = self._selected_model()
        if not model or self._delete_worker is not None:
            return
        ret = QMessageBox.question(
            self,
            "Elimina modello",
            f"Eliminare definitivamente «{model}» dal disco?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.delete_btn.setEnabled(False)
        # l'API Ollama richiede DELETE /api/delete con chiave "model"
        self._delete_worker = PostWorker(self.host, "/api/delete", {"model": model}, self, "DELETE")
        self._delete_worker.ready.connect(lambda _d: self._on_delete_done(model))
        self._delete_worker.failed.connect(self._on_delete_failed)
        self._delete_worker.finished.connect(self._delete_worker.deleteLater)
        self._delete_worker.finished.connect(lambda: setattr(self, "_delete_worker", None))
        self._delete_worker.start()

    def _on_delete_done(self, model: str) -> None:
        self.changed = True
        self.delete_btn.setEnabled(True)
        self.refresh_models()

    def _on_delete_failed(self, err: str) -> None:
        self.delete_btn.setEnabled(True)
        QMessageBox.warning(self, "Elimina modello", f"Eliminazione non riuscita:\n{err}")

    def reject(self) -> None:
        if self._pull_worker is not None and self._pull_worker.isRunning():
            ret = QMessageBox.question(
                self,
                "Scaricamento in corso",
                "Annullare lo scaricamento in corso e chiudere?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
        super().reject()

    def done(self, r: int) -> None:
        # chiusura per qualunque via (Chiudi, Esc, X): nessun worker deve
        # sopravvivere legato al dialogo, che il chiamante poi distrugge
        shutdown_workers(
            [self._list_worker, self._pull_worker, self._delete_worker, *self._cancelled_pulls]
        )
        super().done(r)

"""Scheda parametri dei modelli Ollama: definizioni, editor e persistenza.

Ogni parametro ha una casella di "personalizzazione": solo i parametri attivati
vengono inviati a Ollama nell'opzione ``options`` della richiesta; gli altri
restano ai valori predefiniti del modello/server.
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import config

# ------------------------------------------------------------ definizioni

# type: "int" | "float" | "strlist" | "combo"
PARAM_DEFS: list[dict[str, Any]] = [
    dict(key="temperature", label="Temperatura", type="float",
         lo=0.0, hi=2.0, step=0.05, default=0.8,
         desc="Controlla la creatività: valori bassi → risposte più deterministiche, alti → più creative."),
    dict(key="top_k", label="Top K", type="int",
         lo=1, hi=200, step=1, default=40,
         desc="Campiona solo tra i K token più probabili."),
    dict(key="top_p", label="Top P", type="float",
         lo=0.0, hi=1.0, step=0.01, default=0.9,
         desc="Campionamento nucleo: considera i token finché la probabilità cumulata raggiunge P."),
    dict(key="min_p", label="Min P", type="float",
         lo=0.0, hi=1.0, step=0.01, default=0.0,
         desc="Scarta i token con probabilità inferiore a min_p × probabilità del token migliore."),
    dict(key="num_ctx", label="Finestra di contesto (num_ctx)", type="int",
         lo=128, hi=262144, step=128, default=2048,
         desc="Numero massimo di token di contesto (prompt + risposta) tenuti in memoria."),
    dict(key="num_predict", label="Token massimi (num_predict)", type="int",
         lo=-2, hi=32768, step=1, default=-1, default_display="-1 (illimitato)",
         desc="Numero massimo di token generati. -1 = illimitato, -2 = riempi il contesto."),
    dict(key="repeat_penalty", label="Penalità di ripetizione", type="float",
         lo=0.0, hi=2.0, step=0.05, default=1.1,
         desc=">1 scoraggia le ripetizioni, <1 le incoraggia."),
    dict(key="repeat_last_n", label="Finestra penalità (repeat_last_n)", type="int",
         lo=-1, hi=4096, step=1, default=64,
         desc="Token recenti considerati per la penalità (0 = disattivata, -1 = tutto il contesto)."),
    dict(key="seed", label="Seed", type="int",
         lo=0, hi=2147483647, step=1, default=0,
         desc="0 = casuale a ogni richiesta; un valore fisso rende l'output riproducibile."),
    dict(key="stop", label="Sequenze di stop", type="strlist", default=[],
         desc="Una sequenza per riga: la generazione si interrompe quando ne compare una."),
    dict(key="mirostat", label="Mirostat", type="combo",
         choices=[(0, "Disattivato"), (1, "Mirostat"), (2, "Mirostat 2.0")], default=0,
         desc="Campionamento adattivo che mantiene stabile la sorpresa (perplessità)."),
    dict(key="mirostat_tau", label="Mirostat Tau", type="float",
         lo=0.0, hi=10.0, step=0.1, default=5.0,
         desc="Livello di coerenza target: più alto = più coerente e meno vario."),
    dict(key="mirostat_eta", label="Mirostat Eta", type="float",
         lo=0.0, hi=1.0, step=0.01, default=0.1,
         desc="Velocità con cui l'algoritmo si adatta alle statistiche del testo generato."),
    dict(key="num_gpu", label="Layer su GPU (num_gpu)", type="int",
         lo=0, hi=999, step=1, default=0, default_display="auto",
         desc="Layer del modello scaricati sulla GPU (0 = nessuno; non attivato = automatico)."),
    dict(key="num_thread", label="Thread CPU (num_thread)", type="int",
         lo=1, hi=256, step=1, default=4, default_display="auto",
         desc="Numero di thread di calcolo (non attivato = automatico)."),
    dict(key="presence_penalty", label="Penalità di presenza", type="float",
         lo=-2.0, hi=2.0, step=0.05, default=0.0,
         desc="Penalizza i token già comparsi, spingendo verso nuovi argomenti."),
    dict(key="frequency_penalty", label="Penalità di frequenza", type="float",
         lo=-2.0, hi=2.0, step=0.05, default=0.0,
         desc="Penalizza i token in base a quante volte sono già comparsi."),
]


def options_for_model(model: str) -> dict:
    """Opzioni attive salvate per il modello (da passare a /api/chat)."""
    profile = config.load_model_params().get(model, {})
    opts: dict[str, Any] = {}
    for d in PARAM_DEFS:
        state = profile.get(d["key"])
        if isinstance(state, dict) and state.get("enabled"):
            val = state.get("value", d["default"])
            if d["type"] == "strlist":
                val = [s for s in (val or []) if str(s).strip()]
                if not val:
                    continue
            opts[d["key"]] = val
    return opts


# ------------------------------------------------------------------ riga

class ParamRow(QFrame):
    """Riga di un parametro: casella di personalizzazione + valore."""

    changed = Signal()

    def __init__(self, definition: dict, parent=None):
        super().__init__(parent)
        self.defn = definition
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(10)

        self.enable = QCheckBox(self)
        self.enable.setToolTip("Attiva per inviare questo parametro a Ollama al posto del predefinito")
        self.enable.toggled.connect(self._on_toggle)
        lay.addWidget(self.enable)

        label = QLabel(definition["label"], self)
        label.setToolTip(f"{definition['desc']}\n\nPredefinito: {definition.get('default_display', definition['default'])}")
        lay.addWidget(label, 1)

        self.value_widget = self._build_value()
        lay.addWidget(self.value_widget, 0)

        default = definition.get("default_display", definition["default"])
        self.default_label = QLabel(f"predefinito: {default}", self)
        self.default_label.setObjectName("metaLabel")
        lay.addWidget(self.default_label, 0)

        self.set_enabled(False)

    # costruzione del controllo di valore in base al tipo
    def _build_value(self) -> QWidget:
        d = self.defn
        if d["type"] == "int":
            w = QSpinBox(self)
            w.setRange(d["lo"], d["hi"])
            w.setSingleStep(d["step"])
            w.setValue(int(d["default"]))
            w.valueChanged.connect(lambda _v: self.changed.emit())
        elif d["type"] == "float":
            w = QDoubleSpinBox(self)
            w.setRange(d["lo"], d["hi"])
            w.setSingleStep(d["step"])
            w.setDecimals(2)
            w.setValue(float(d["default"]))
            w.valueChanged.connect(lambda _v: self.changed.emit())
        elif d["type"] == "combo":
            w = QComboBox(self)
            for value, text in d["choices"]:
                w.addItem(text, value)
            w.setCurrentIndex(0)
            w.currentIndexChanged.connect(lambda _i: self.changed.emit())
        else:  # strlist
            w = QPlainTextEdit(self)
            w.setPlaceholderText("una sequenza per riga…")
            w.setFixedHeight(56)
            w.textChanged.connect(self.changed.emit)
        w.setEnabled(False)
        w.setFocusPolicy(Qt.FocusPolicy.StrongFocus if d["type"] != "strlist"
                         else Qt.FocusPolicy.ClickFocus)
        return w

    # ------------------------------------------------------------------ API

    def set_enabled(self, on: bool) -> None:
        self.enable.blockSignals(True)
        self.enable.setChecked(on)
        self.enable.blockSignals(False)
        self.value_widget.setEnabled(on)
        self.default_label.setVisible(not on)

    def set_value(self, value: Any) -> None:
        d = self.defn
        w = self.value_widget
        if d["type"] == "int":
            w.setValue(int(value))
        elif d["type"] == "float":
            w.setValue(float(value))
        elif d["type"] == "combo":
            idx = w.findData(value)
            if idx >= 0:
                w.setCurrentIndex(idx)
        else:
            w.setPlainText("\n".join(str(s) for s in (value or [])))

    def state(self) -> dict:
        d = self.defn
        w = self.value_widget
        if d["type"] == "int":
            value = w.value()
        elif d["type"] == "float":
            value = round(w.value(), 4)
        elif d["type"] == "combo":
            value = w.currentData()
        else:
            value = [ln.strip() for ln in w.toPlainText().splitlines() if ln.strip()]
        return {"enabled": self.enable.isChecked(), "value": value}

    def load_state(self, state: dict) -> None:
        value = state.get("value", self.defn["default"])
        self.set_value(value)
        self.set_enabled(bool(state.get("enabled")))

    def reset(self) -> None:
        self.set_value(self.defn["default"])
        self.set_enabled(False)

    def _on_toggle(self, on: bool) -> None:
        self.value_widget.setEnabled(on)
        self.default_label.setVisible(not on)
        self.changed.emit()


# ------------------------------------------------------------------- tab

class ModelParamsTab(QWidget):
    """Editor dei parametri per modello, con salvataggio e ripristino."""

    paramsSaved = Signal(str)   # nome modello

    def __init__(self, models_provider: Callable[[], list[str]], parent=None):
        super().__init__(parent)
        self._models_provider = models_provider
        self._dirty = False
        self._last_model: str | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 8, 4, 4)
        lay.setSpacing(8)

        info = QLabel(
            "Solo i parametri spuntati vengono inviati al modello; gli altri restano "
            "ai valori predefiniti di Ollama. Ogni modello ha un profilo separato.",
            self,
        )
        info.setObjectName("metaLabel")
        info.setWordWrap(True)
        lay.addWidget(info)

        head = QHBoxLayout()
        head.addWidget(QLabel("Modello:", self))
        self.model_combo = QComboBox(self)
        self.model_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        head.addWidget(self.model_combo, 1)
        self.reload_btn = QPushButton("↻ Aggiorna", self)
        self.reload_btn.setToolTip("Rilegge l'elenco dei modelli installati")
        self.reload_btn.clicked.connect(self.reload_models)
        head.addWidget(self.reload_btn)
        lay.addLayout(head)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        host_lay = QVBoxLayout(host)
        host_lay.setContentsMargins(0, 4, 8, 4)
        host_lay.setSpacing(2)

        self.rows: list[ParamRow] = []
        for d in PARAM_DEFS:
            row = ParamRow(d, host)
            row.changed.connect(self._mark_dirty)
            self.rows.append(row)
            host_lay.addWidget(row)
        host_lay.addStretch(1)
        scroll.setWidget(host)
        lay.addWidget(scroll, 1)

        btns = QHBoxLayout()
        self.reset_btn = QPushButton("↺  Ripristina predefiniti", self)
        self.reset_btn.setToolTip("Annulla tutte le personalizzazioni del modello corrente")
        self.reset_btn.clicked.connect(self.reset_defaults)
        btns.addWidget(self.reset_btn)
        btns.addStretch(1)
        self.status_label = QLabel("", self)
        self.status_label.setObjectName("metaLabel")
        btns.addWidget(self.status_label)
        self.save_btn = QPushButton("💾  Salva parametri", self)
        self.save_btn.setObjectName("primaryBtn")
        self.save_btn.clicked.connect(self.save_profile)
        btns.addWidget(self.save_btn)
        lay.addLayout(btns)

        self._dirty = False
        self.reload_models()

    # ------------------------------------------------------------------ API

    def reload_models(self) -> None:
        names = list(self._models_provider())
        current = self.current_model()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if not names:
            self.model_combo.addItem("— nessun modello disponibile —")
            self.model_combo.model().item(0).setEnabled(False)
        for n in names:
            self.model_combo.addItem(n, n)
        if current:
            idx = self.model_combo.findData(current)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
        self.model_combo.blockSignals(False)
        if names:
            self._load_current()
            self._last_model = self.current_model()

    def current_model(self) -> str | None:
        return self.model_combo.currentData() if self.model_combo.count() else None

    def is_dirty(self) -> bool:
        return self._dirty

    def save_profile(self) -> None:
        model = self.current_model()
        if not model:
            return
        params = config.load_model_params()
        params[model] = {d["key"]: row.state() for d, row in zip(PARAM_DEFS, self.rows)}
        config.save_model_params(params)
        self._dirty = False
        self.status_label.setText(f"✓ Parametri salvati per {model}")
        self.paramsSaved.emit(model)

    def reset_defaults(self) -> None:
        for row in self.rows:
            row.reset()
        self._dirty = True
        self.status_label.setText("Predefiniti ripristinati — premi «Salva parametri» per confermare")

    def maybe_discard(self) -> bool:
        """Se ci sono modifiche non salvate chiede cosa fare. True = si può chiudere."""
        if not self._dirty:
            return True
        ret = QMessageBox.question(
            self,
            "Modifiche non salvate",
            "I parametri del modello corrente sono stati modificati ma non salvati.\n"
            "Salvare le modifiche?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if ret == QMessageBox.StandardButton.Save:
            self.save_profile()
            return True
        return ret == QMessageBox.StandardButton.Discard

    # -------------------------------------------------------------- interni

    def _load_current(self) -> None:
        model = self.current_model()
        profile = config.load_model_params().get(model, {}) if model else {}
        for d, row in zip(PARAM_DEFS, self.rows):
            state = profile.get(d["key"])
            if isinstance(state, dict):
                row.load_state(state)
            else:
                row.reset()
        self._dirty = False
        self.status_label.setText(
            f"Profilo salvato caricato per {model}" if profile and model else ""
        )

    def _on_model_changed(self, _idx: int) -> None:
        if self._dirty:
            if not self.maybe_discard():
                # torna al modello precedente
                idx = self.model_combo.findData(self._last_model)
                if idx >= 0:
                    self.model_combo.blockSignals(True)
                    self.model_combo.setCurrentIndex(idx)
                    self.model_combo.blockSignals(False)
                return
        self._last_model = self.current_model()
        self._load_current()

    def _mark_dirty(self) -> None:
        self._dirty = True
        self.status_label.setText("")

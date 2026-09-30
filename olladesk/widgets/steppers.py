"""Campi numerici con pulsanti −/+ tondi ai lati del valore (stile stepper).

Sostituisce le frecce impilate di QSpinBox/QDoubleSpinBox — piccole, strette
nel bordo arrotondato del tema e scomode da premere — con due pulsanti larghi
ai lati del campo. La tastiera funziona sul campo interno; la rotella cambia
il valore solo quando il campo ha il focus (altrimenti scorre la pagina che
lo contiene, vedi _WheelOnFocusMixin). Tenere premuto un pulsante ripete il passo.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QWidget,
)

BTN_SIZE = 26   # lato dei pulsanti tondi (il raggio nel QSS è metà)

DEC_TIP = "Diminuisci il valore (tieni premuto per scorrere)"
INC_TIP = "Aumenta il valore (tieni premuto per scorrere)"


class _WheelOnFocusMixin:
    """La rotella cambia il valore solo se il campo ha il focus.

    Altrimenti l'evento viene ignorato e Qt lo propaga al genitore: l'area
    scorrevole che contiene il campo scorre, invece di modificare per
    sbaglio temperatura, num_ctx o dimensione del carattere.
    """

    def wheelEvent(self, ev) -> None:  # noqa: N802 (API Qt)
        if not self.hasFocus():
            ev.ignore()
            return
        super().wheelEvent(ev)


class _SpinBox(_WheelOnFocusMixin, QSpinBox):
    pass


class _DoubleSpinBox(_WheelOnFocusMixin, QDoubleSpinBox):
    pass


class _NumberStepper(QWidget):
    """[−] [campo] [+] con API ridotta compatibile con QSpinBox."""

    # ridechiarato nelle sottoclassi con il tipo giusto (int/float)
    valueChanged = Signal(float)

    def __init__(self, inner: QSpinBox | QDoubleSpinBox, parent=None):
        super().__init__(parent)
        self.spin = inner
        inner.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        inner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        inner.setMinimumWidth(84)
        inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        inner.valueChanged.connect(self.valueChanged)
        inner.valueChanged.connect(self._update_buttons)
        # il campo prende il focus al clic e con Tab; non con la rotella
        inner.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # solo il contenitore rinuncia al focus: il metodo ridefinito più sotto
        # inoltrerebbe NoFocus al campo, rendendolo non modificabile
        super().setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.dec_btn = QPushButton("−", self)
        self.inc_btn = QPushButton("+", self)
        for btn, tip in ((self.dec_btn, DEC_TIP), (self.inc_btn, INC_TIP)):
            btn.setObjectName("stepBtn")
            btn.setFixedSize(BTN_SIZE, BTN_SIZE)
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            # tenere premuto ripete il passo (lento all'inizio, poi rapido)
            btn.setAutoRepeat(True)
            btn.setAutoRepeatDelay(400)
            btn.setAutoRepeatInterval(60)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self.dec_btn)
        lay.addWidget(self.spin, 1)
        lay.addWidget(self.inc_btn)

        self.dec_btn.clicked.connect(lambda: self._nudge(-1))
        self.inc_btn.clicked.connect(lambda: self._nudge(+1))
        self._update_buttons()

    def _update_buttons(self, *_a) -> None:
        """−/+ disattivati agli estremi dell'intervallo."""
        v = self.spin.value()
        self.dec_btn.setEnabled(v > self.spin.minimum())
        self.inc_btn.setEnabled(v < self.spin.maximum())

    def changeEvent(self, ev) -> None:  # noqa: N802 (API Qt)
        super().changeEvent(ev)
        # riabilitando il contenitore Qt riattiva anche i pulsanti: riapplica
        # gli estremi dell'intervallo
        if ev.type() == QEvent.Type.EnabledChange and self.isEnabled():
            self._update_buttons()

    def _nudge(self, direction: int) -> None:
        # setValue borna da sola dentro l'intervallo [min, max]
        self.spin.setValue(self.spin.value() + direction * self.spin.singleStep())

    # ------------------------------------------------------------ API campo

    def value(self):
        return self.spin.value()

    def setValue(self, v) -> None:  # noqa: N802 (API Qt)
        self.spin.setValue(v)

    def setRange(self, lo, hi) -> None:  # noqa: N802 (API Qt)
        self.spin.setRange(lo, hi)
        self._update_buttons()

    def setSingleStep(self, step) -> None:  # noqa: N802 (API Qt)
        self.spin.setSingleStep(step)

    def setSuffix(self, suffix: str) -> None:  # noqa: N802 (API Qt)
        self.spin.setSuffix(suffix)

    # il focus va al campo, non al contenitore vuoto
    def setFocusPolicy(self, policy: Qt.FocusPolicy) -> None:  # noqa: N802
        self.spin.setFocusPolicy(policy)

    # il contenitore è coperto dai figli: il tooltip vale anche per loro
    def setToolTip(self, tip: str) -> None:  # noqa: N802 (API Qt)
        super().setToolTip(tip)
        self.spin.setToolTip(tip)


class IntStepper(_NumberStepper):
    valueChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(_SpinBox(), parent)


class FloatStepper(_NumberStepper):
    valueChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(_DoubleSpinBox(), parent)

    def setDecimals(self, n: int) -> None:  # noqa: N802 (API Qt)
        self.spin.setDecimals(n)

"""Finestra della companion web (voce «Companion» della barra laterale).

Raccoglie tutto quello che serve per usare OllaDesk dal telefono:
attivazione e porta del servizio, stato e indirizzi, abbinamento con il
codice di 6 cifre (e il QR code, se è installato `python3-qrcode`) e
revoca dei dispositivi. Il QR è disegnato con QPainter dalla matrice di
`get_matrix()`, senza Pillow né QtSvg.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..companion import MAX_DEVICES
from .steppers import IntStepper


def qr_matrix(text: str) -> list[list[bool]] | None:
    """Matrice del QR code (bordo incluso); None se manca python3-qrcode."""
    try:
        import qrcode
    except ImportError:
        return None
    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(text)
    qr.make(fit=True)
    return [[bool(c) for c in row] for row in qr.get_matrix()]


class QrWidget(QWidget):
    """Disegna una matrice QR: moduli neri su fondo bianco, anche col tema scuro."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._matrix: list[list[bool]] = []
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_matrix(self, matrix: list[list[bool]] | None) -> None:
        self._matrix = matrix or []
        self.setVisible(bool(self._matrix))
        if self._matrix:
            self.setFixedSize(self.sizeHint())   # moduli quadrati, senza resti
        self.updateGeometry()
        self.update()

    def has_matrix(self) -> bool:
        return bool(self._matrix)

    def sizeHint(self) -> QSize:  # noqa: N802 (API Qt)
        n = len(self._matrix)
        side = max(n * max(1, 220 // n), 0) if n else 0
        return QSize(side, side)

    def paintEvent(self, _ev) -> None:  # noqa: N802 (API Qt)
        n = len(self._matrix)
        if not n:
            return
        cell = max(1, min(self.width(), self.height()) // n)
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("white"))
        black = QColor("black")
        for y, row in enumerate(self._matrix):
            for x, on in enumerate(row):
                if on:
                    p.fillRect(x * cell, y * cell, cell, cell, black)
        p.end()


class CompanionDialog(QDialog):
    """Servizio, abbinamento e dispositivi della companion web.

    `apply(enabled, port)` è fornita dalla finestra principale: salva le
    impostazioni e avvia o ferma il server. Il dialogo segue lo stato del
    server tramite `state_changed`.
    """

    def __init__(self, server, settings: dict, apply=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Companion web")
        self.setMinimumWidth(460)
        self._server = server
        self._apply = apply
        self._code = ""
        self._url = ""

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        # ------------------------------------------------------------ servizio
        svc = QGroupBox("Servizio", self)
        svc_lay = QVBoxLayout(svc)
        row = QHBoxLayout()
        self.enable_chk = QCheckBox("Attiva la companion web nella rete locale", svc)
        self.enable_chk.setChecked(bool(settings.get("companion", False)))
        self.enable_chk.toggled.connect(self._on_toggled)
        row.addWidget(self.enable_chk, 1)
        row.addWidget(QLabel("Porta:", svc))
        self.port_spin = IntStepper(svc)
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(int(settings.get("companion_port", 8765)))
        self.port_spin.valueChanged.connect(self._on_port_edited)
        row.addWidget(self.port_spin)
        self.port_btn = QPushButton("Applica", svc)
        self.port_btn.setToolTip("Riavvia la companion sulla nuova porta")
        self.port_btn.clicked.connect(self._apply_port)
        self.port_btn.hide()
        row.addWidget(self.port_btn)
        svc_lay.addLayout(row)

        self.state_label = QLabel(svc)
        self.state_label.setWordWrap(True)
        svc_lay.addWidget(self.state_label)

        urls_row = QHBoxLayout()
        self.urls_label = QLabel(svc)
        self.urls_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.urls_label.setWordWrap(True)
        urls_row.addWidget(self.urls_label, 1)
        self.copy_btn = QPushButton("Copia indirizzo", svc)
        self.copy_btn.clicked.connect(self._copy_url)
        urls_row.addWidget(self.copy_btn, 0, Qt.AlignmentFlag.AlignTop)
        svc_lay.addLayout(urls_row)
        lay.addWidget(svc)

        # ---------------------------------------------------------- abbinamento
        self.pair_box = QGroupBox("Abbina un dispositivo", self)
        pair_lay = QVBoxLayout(self.pair_box)
        self.qr = QrWidget(self.pair_box)
        pair_lay.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignHCenter)

        self.code_label = QLabel(self.pair_box)
        self.code_label.setObjectName("pairingCode")
        self.code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        font = self.code_label.font()
        font.setPointSize(font.pointSize() * 2 + 4)
        font.setBold(True)
        self.code_label.setFont(font)
        pair_lay.addWidget(self.code_label)

        self.status_label = QLabel(self.pair_box)
        self.status_label.setObjectName("metaLabel")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        pair_lay.addWidget(self.status_label)

        self.new_code_btn = QPushButton("Nuovo codice", self.pair_box)
        self.new_code_btn.clicked.connect(self._new_code)
        pair_lay.addWidget(self.new_code_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.pair_box)

        # ---------------------------------------------------------- dispositivi
        dev = QGroupBox("Dispositivi abbinati", self)
        dev_lay = QHBoxLayout(dev)
        self.devices_label = QLabel(dev)
        self.devices_label.setWordWrap(True)
        dev_lay.addWidget(self.devices_label, 1)
        self.revoke_btn = QPushButton("Revoca dispositivi", dev)
        self.revoke_btn.clicked.connect(self._revoke)
        dev_lay.addWidget(self.revoke_btn)
        lay.addWidget(dev)

        self.note = QLabel(self)
        self.note.setObjectName("metaLabel")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close_btn = QPushButton("Chiudi", self)
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        lay.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        server.paired.connect(self._on_paired)
        server.state_changed.connect(self._on_state)

        self._update_devices()
        self._on_state(server.state(), "")

    # ------------------------------------------------------------- servizio

    def _on_toggled(self, on: bool) -> None:
        if self._apply is not None:
            self._apply(on, self.port_spin.value())
        self.port_btn.hide()

    def _on_port_edited(self, _value: int) -> None:
        # con il servizio acceso la porta nuova vale solo con «Applica»
        self.port_btn.setVisible(self.enable_chk.isChecked()
                                 and self.port_spin.value() != self._server.port)

    def _apply_port(self) -> None:
        self.port_btn.hide()
        if self._apply is not None:
            self._apply(self.enable_chk.isChecked(), self.port_spin.value())

    def _copy_url(self) -> None:
        if self._url:
            QGuiApplication.clipboard().setText(self._url)
            self.copy_btn.setText("Copiato ✓")
            QTimer.singleShot(1500, lambda: self.copy_btn.setText("Copia indirizzo"))

    def _on_state(self, state: str, detail: str) -> None:
        running = state == "running"
        if running:
            self.state_label.setText(f"● Attiva sulla porta {self._server.port}")
            urls = self._server.urls()
            self._url = urls[0] if urls else ""
            if urls:
                self.urls_label.setText(
                    "Sul telefono o sul tablet, collegato alla stessa rete, apri:\n  "
                    + "\n  ".join(urls))
            else:
                self.urls_label.setText(
                    "⚠ Nessun indirizzo di rete trovato: il PC è collegato alla rete "
                    "locale (Wi-Fi o cavo)?")
        elif state == "error":
            self.state_label.setText(f"⚠ Non avviata: {detail}. Scegli un'altra porta.")
            self._url = ""
            self.urls_label.setText("")
        else:
            self.state_label.setText("○ Spenta: il telefono non può collegarsi.")
            self._url = ""
            self.urls_label.setText("")
        self.urls_label.setVisible(bool(self.urls_label.text()))
        self.copy_btn.setVisible(bool(self._url))
        port = self._server.port if running else self.port_spin.value()
        self.note.setText(
            "Il codice vale 2 minuti, una sola volta e solo finché questa finestra "
            "è aperta. Il collegamento è HTTP in chiaro: usalo solo sulla rete di "
            "casa (Wi-Fi protetta), non aprire la porta sul router. Se il telefono "
            f"non raggiunge la pagina, apri la porta {port}/tcp nel firewall del PC "
            "per la sola rete locale."
        )
        self.pair_box.setEnabled(running)
        if running:
            if not self._timer.isActive():
                self._new_code()
        else:
            self._expire("Attiva la companion per abbinare un telefono.")
            self.code_label.setText("— — —")
        self._fit()

    def _fit(self) -> None:
        # QR e indirizzi compaiono e spariscono a finestra aperta: la
        # dimensione segue il contenuto, senza sovrapposizioni
        self.layout().activate()
        self.resize(self.width(), self.sizeHint().height())

    # ------------------------------------------------------------------ codice

    def _new_code(self) -> None:
        if self._server.state() != "running":
            return
        self._code, _ttl = self._server.auth.new_code()
        self.code_label.setText(f"{self._code[:3]} {self._code[3:]}")
        self.code_label.setEnabled(True)
        self.qr.set_matrix(qr_matrix(f"{self._url}/#pair={self._code}") if self._url else None)
        self.new_code_btn.hide()
        self._timer.start()
        self._tick()

    def _tick(self) -> None:
        left = int(self._server.auth.code_remaining())
        if left <= 0:
            self._expire("Codice scaduto o già usato: generane uno nuovo.")
            return
        hint = "Scrivi il codice nella pagina"
        if self.qr.has_matrix():
            hint = "Inquadra il QR code o scrivi il codice nella pagina"
        self.status_label.setText(f"{hint} · valido ancora {left // 60}:{left % 60:02d}")

    def _expire(self, text: str) -> None:
        self._timer.stop()
        self._server.auth.cancel_code()
        self.code_label.setEnabled(False)
        self.qr.set_matrix(None)
        self.status_label.setText(text)
        self.new_code_btn.setVisible(self._server.state() == "running")

    def _on_paired(self) -> None:
        self._expire("✓ Dispositivo abbinato.")
        self.code_label.setText("✓")
        self._update_devices()
        self._fit()

    # -------------------------------------------------------------- dispositivi

    def _update_devices(self) -> None:
        n = self._server.auth.device_count()
        self.devices_label.setText(
            f"Dispositivi abbinati: {n}. Se ne ricordano al massimo {MAX_DEVICES}: "
            "oltre, i più vecchi vengono scollegati.")
        self.revoke_btn.setEnabled(n > 0)

    def _revoke(self) -> None:
        answer = QMessageBox.question(
            self, "Revoca dispositivi",
            "Scollegare tutti i dispositivi abbinati? Per riusare la companion "
            "dovranno abbinarsi di nuovo con un codice.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._server.auth.revoke_all()
        self._update_devices()
        self._expire("Dispositivi scollegati.")

    def done(self, r: int) -> None:
        # il codice vale solo finché il dialogo è aperto
        self._timer.stop()
        self._server.auth.cancel_code()
        for sig, slot in ((self._server.paired, self._on_paired),
                          (self._server.state_changed, self._on_state)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        super().done(r)

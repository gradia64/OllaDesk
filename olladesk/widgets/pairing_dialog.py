"""Dialogo di abbinamento della companion web (pulsante 📱 della barra).

Mostra gli indirizzi della pagina, il codice di 6 cifre con il tempo
rimasto e, se è installato `python3-qrcode`, un QR code con indirizzo e
codice. Il QR è disegnato con QPainter dalla matrice di `get_matrix()`,
senza Pillow né QtSvg.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..companion import MAX_DEVICES


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


class PairingDialog(QDialog):
    def __init__(self, server, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Abbina un dispositivo")
        self.setMinimumWidth(420)
        self._server = server
        self._code = ""

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        urls = server.urls()
        self._url = urls[0] if urls else ""
        if urls:
            intro = ("Sul telefono o sul tablet, collegato alla stessa rete, apri:\n  "
                     + "\n  ".join(urls))
        else:
            intro = ("⚠ Nessun indirizzo di rete trovato: il PC è collegato alla rete "
                     "locale (Wi-Fi o cavo)?")
        self.urls_label = QLabel(intro, self)
        self.urls_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.urls_label.setWordWrap(True)
        lay.addWidget(self.urls_label)

        self.qr = QrWidget(self)
        lay.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignHCenter)

        self.code_label = QLabel(self)
        self.code_label.setObjectName("pairingCode")
        self.code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        font = self.code_label.font()
        font.setPointSize(font.pointSize() * 2 + 4)
        font.setBold(True)
        self.code_label.setFont(font)
        lay.addWidget(self.code_label)

        self.status_label = QLabel(self)
        self.status_label.setObjectName("metaLabel")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        lay.addWidget(self.status_label)

        self.new_code_btn = QPushButton("Nuovo codice", self)
        self.new_code_btn.clicked.connect(self._new_code)
        lay.addWidget(self.new_code_btn, 0, Qt.AlignmentFlag.AlignHCenter)

        note = QLabel(
            "Il codice vale 2 minuti e una sola volta. Il collegamento è HTTP in "
            "chiaro: usalo solo sulla rete di casa (Wi-Fi protetta). Se il telefono "
            "non raggiunge la pagina, apri la porta "
            f"{server.port}/tcp nel firewall del PC per la sola rete locale. "
            f"Si ricordano al massimo {MAX_DEVICES} dispositivi: oltre, i più "
            "vecchi vengono scollegati.",
            self,
        )
        note.setObjectName("metaLabel")
        note.setWordWrap(True)
        lay.addWidget(note)

        bottom = QHBoxLayout()
        self.devices_label = QLabel(self)
        bottom.addWidget(self.devices_label, 1)
        self.revoke_btn = QPushButton("Revoca dispositivi", self)
        self.revoke_btn.clicked.connect(self._revoke)
        bottom.addWidget(self.revoke_btn)
        close_btn = QPushButton("Chiudi", self)
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        lay.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        server.paired.connect(self._on_paired)

        self._update_devices()
        self._new_code()

    # ------------------------------------------------------------------ codice

    def _new_code(self) -> None:
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
        self.new_code_btn.show()

    def _on_paired(self) -> None:
        self._expire("✓ Dispositivo abbinato.")
        self.code_label.setText("✓")
        self._update_devices()

    # -------------------------------------------------------------- dispositivi

    def _update_devices(self) -> None:
        n = self._server.auth.device_count()
        self.devices_label.setText(f"Dispositivi abbinati: {n}")
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
        try:
            self._server.paired.disconnect(self._on_paired)
        except (RuntimeError, TypeError):
            pass
        super().done(r)

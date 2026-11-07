"""Controlli dentro il Flatpak installato, eseguiti con il suo Python
(packaging/flatpak/smoke-test.sh, con flatpak run --command=python3).

Il codice dell'app è in /app/share/olladesk, come lo trova il lanciatore.
"""
import io
import os
import sys

sys.path.insert(0, "/app/share/olladesk")

import PySide6  # noqa: E402
from PySide6.QtGui import QIcon, QImageReader  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

# PySide6 deve venire dalla base app (/app), mai da una wheel di pip: porterebbe
# un secondo Qt accanto a quello del runtime
assert PySide6.__file__.startswith("/app/"), PySide6.__file__

app = QApplication(sys.argv)

from olladesk import __version__, app_update, portal  # noqa: E402
from olladesk import app as olla  # noqa: E402
from olladesk.main_window import MainWindow  # noqa: E402,F401

expected = os.environ.get("OLLADESK_EXPECTED_VERSION")
assert not expected or __version__ == expected, (__version__, expected)
assert portal.in_flatpak(), "/.flatpak-info assente: non siamo nella sandbox"
assert app_update.install_method() == "flatpak", app_update.install_method()
assert not QIcon(str(olla.ICON)).pixmap(64, 64).isNull(), "icona SVG non caricata"
formats = {bytes(f).decode() for f in QImageReader.supportedImageFormats()}
assert {"svg", "png", "jpeg", "webp"} <= formats, formats

import jeepney  # noqa: E402,F401  portale FileTransfer (trascinamento)
import keyring.backends.SecretService  # noqa: E402,F401  portachiavi via Secret Service
import qrcode  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402

qr = qrcode.QRCode()
qr.add_data("http://192.0.2.1:8799/#pair=123456")
qr.make()
assert qr.get_matrix()
w = PdfWriter()
w.add_blank_page(100, 100)
buf = io.BytesIO()
w.write(buf)
assert len(PdfReader(io.BytesIO(buf.getvalue())).pages) == 1

import ssl  # noqa: E402,F401  ricerca web e GitHub via HTTPS
print(f"Flatpak di OllaDesk {__version__}: controlli OK")

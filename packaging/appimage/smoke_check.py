"""Controlli dentro l'AppImage, eseguiti con il suo Python (smoke-test.sh).

APPIMAGE e APPDIR vengono dall'ambiente, come li imposterebbe il runtime.
"""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.environ["APPDIR"], "usr", "share", "olladesk"))

from PySide6.QtGui import QIcon, QImageReader  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from olladesk import __version__, app_update  # noqa: E402
from olladesk import app as olla  # noqa: E402
from olladesk.main_window import MainWindow  # noqa: E402,F401

assert app_update.install_method() == "appimage", app_update.install_method()
assert not QIcon(str(olla.ICON)).pixmap(64, 64).isNull(), "icona SVG non caricata"
formats = {bytes(f).decode() for f in QImageReader.supportedImageFormats()}
assert {"svg", "png", "jpeg", "webp"} <= formats, formats

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
print(f"AppImage di OllaDesk {__version__}: controlli OK")

"""Icone dell'interfaccia: SVG di Lucide (assets/icons, licenza ISC) tinte
con i colori del tema.

Gli SVG usano `stroke="currentColor"`: il colore si sostituisce nel testo e
l'immagine si disegna alla dimensione richiesta, il doppio per gli schermi
ad alta densità. Serve il plugin SVG delle immagini di Qt (qt6-svg-plugins,
dipendenza del pacchetto): se manca, `icon()` restituisce None e
`set_icon()` lascia al pulsante il simbolo di testo di ripiego.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
from PySide6.QtGui import QIcon, QImageReader, QPixmap

ICON_DIR = Path(__file__).resolve().parent / "assets" / "icons"
# doppia risoluzione: nitide anche con il ridimensionamento dello schermo al 200%
_DPR = 2


@lru_cache(maxsize=256)
def _svg(name: str) -> str | None:
    try:
        return (ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")
    except OSError:
        return None


@lru_cache(maxsize=512)
def icon(name: str, color: str, size: int = 18, stroke: float = 2.0) -> QIcon | None:
    """Icona `name` (nome Lucide) di colore `color`, `size` pixel logici.

    `stroke` è lo spessore del tratto nella griglia 24×24 di Lucide (2 di
    default; più sottile per le icone piccole e chiare).
    """
    svg = _svg(name)
    if svg is None:
        return None
    svg = svg.replace("currentColor", color)
    if stroke != 2.0:
        svg = svg.replace('stroke-width="2"', f'stroke-width="{stroke:g}"')
    data = QByteArray(svg.encode("utf-8"))
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buf, b"svg")
    reader.setScaledSize(QSize(size * _DPR, size * _DPR))
    img = reader.read()
    buf.close()
    if img.isNull():
        return None   # plugin SVG assente
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(_DPR)
    return QIcon(pm)


def set_icon(button, name: str, color: str, size: int = 18, fallback: str = "",
             stroke: float = 2.0) -> None:
    """Icona su un pulsante; senza plugin SVG resta il testo `fallback`.

    Con l'icona, un pulsante senza etichetta (`fallback` è solo un simbolo)
    perde il testo; uno con etichetta la tiene accanto all'icona.
    """
    ic = icon(name, color, size, stroke)
    if ic is None:
        button.setIcon(QIcon())
        if fallback:
            button.setText(fallback)
        return
    button.setIcon(ic)
    button.setIconSize(QSize(size, size))
    if fallback:
        button.setText("")   # pulsante di solo simbolo: basta l'icona


def available() -> bool:
    """True se le icone si possono disegnare (plugin SVG presente)."""
    return icon("plus", "#000000", 16) is not None

"""Temi chiaro/scuro/sistema in stile ChatGPT (Fusion + palette + QSS)."""
from __future__ import annotations

import subprocess

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QTransform,
)
from PySide6.QtWidgets import QApplication

WEB_ACTIVE_COLOR = "#3b82f6"  # blu della ricerca web attiva


def resolve_theme(name: str) -> str:
    """Risolve "system" nel tema effettivo (scuro/chiaro) in uso sul desktop.

    Su KDE Plasma usa Qt colorScheme (via xdg-desktop-portal); in mancanza
    interroga kreadconfig su kdeglobals.
    """
    name = (name or "dark").lower()
    if name in ("dark", "light"):
        return name
    try:
        cs = QGuiApplication.styleHints().colorScheme()
        if cs == Qt.ColorScheme.Dark:
            return "dark"
        if cs == Qt.ColorScheme.Light:
            return "light"
    except Exception:
        pass
    for cmd in ("kreadconfig6", "kreadconfig5"):
        try:
            out = subprocess.run(
                [cmd, "--file", "kdeglobals", "--group", "General",
                 "--key", "ColorScheme"],
                capture_output=True, text=True, timeout=3,
            ).stdout.strip()
            if out:
                return "dark" if "dark" in out.lower() else "light"
        except Exception:
            continue
    return "dark"

COLORS: dict[str, dict[str, str]] = {
    "dark": {
        "window": "#212121",
        "sidebar": "#171717",
        "hover": "#2f2f2f",
        "surface": "#2b2b2b",
        "border": "#3a3a3a",
        "text": "#ececec",
        "dim": "#9b9b9b",
        "input_bg": "#2f2f2f",
        "bubble_user": "#2f2f2f",
        "bubble_user_border": "#3a3a3a",
        "accent": "#0d9373",
        "accent_hover": "#0fa983",
        "danger": "#ef4444",
        "error_bg": "rgba(239,68,68,0.10)",
        "scroll": "#3d3d3d",
        "scroll_hover": "#4d4d4d",
        "code_bg": "#1a1d21",
        "code_fg": "#e8eaed",
        "code_inline": "#79b8ff",
        "online": "#22c55e",
        "offline": "#ef4444",
    },
    "light": {
        "window": "#ffffff",
        "sidebar": "#f9f9f9",
        "hover": "#ececec",
        "surface": "#f4f4f4",
        "border": "#d9d9d9",
        "text": "#0d0d0d",
        "dim": "#6e6e6e",
        "input_bg": "#ffffff",
        "bubble_user": "#f1f1f1",
        "bubble_user_border": "#e2e2e2",
        "accent": "#0d9373",
        "accent_hover": "#0a8064",
        "danger": "#dc2626",
        "error_bg": "rgba(220,38,38,0.08)",
        "scroll": "#cfcfcf",
        "scroll_hover": "#bdbdbd",
        "code_bg": "#f6f8fa",
        "code_fg": "#24292f",
        "code_inline": "#a31515",
        "online": "#16a34a",
        "offline": "#dc2626",
    },
}


def globe_icon(color: str, size: int = 18) -> QIcon:
    """Globo disegnato con QPainter: grigio da spento, blu quando attivo.

    Usato al posto dell'emoji 🌐 (che ha colori propri non controllabili).
    """
    dpr = 2
    s = size * dpr
    pm = QPixmap(s, s)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.5 * dpr)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    r = s / 2 - 2 * dpr
    c = QPointF(s / 2, s / 2)
    p.drawEllipse(c, r, r)                    # contorno
    p.drawEllipse(c, r * 0.45, r)             # meridiano
    p.drawLine(QPointF(c.x() - r, c.y()), QPointF(c.x() + r, c.y()))          # equatore
    p.drawLine(QPointF(c.x() - r * 0.90, c.y() - r * 0.5), QPointF(c.x() + r * 0.90, c.y() - r * 0.5))
    p.drawLine(QPointF(c.x() - r * 0.90, c.y() + r * 0.5), QPointF(c.x() + r * 0.90, c.y() + r * 0.5))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return QIcon(pm)


def brain_icon(color: str, size: int = 18) -> QIcon:
    """Cervello del toggle thinking: grigio da spento, blu quando attivo.

    Disegnato con QPainter nello stesso stile a linee del globo (stesso tratto
    e stesso ingombro), al posto dell'emoji 🧠: l'emoji ha colori propri, la
    sua sagoma tinta a 18 px non si leggeva e senza font emoji diventava un
    riquadro «tofu». Vista dall'alto: due emisferi speculari, la scissura
    centrale e tre solchi per lato, in una griglia 24×24 scalata all'icona.
    """
    dpr = 2
    s = size * dpr
    pm = QPixmap(s, s)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    k = s / 24
    p.scale(k, k)
    pen = QPen(QColor(color), 1.5 * dpr / k)   # tratto uguale al globo
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)

    half = QPainterPath(QPointF(12, 4))         # emisfero sinistro
    half.cubicTo(9, 2.6, 5.6, 3.4, 5.2, 6.4)
    half.cubicTo(2.6, 7.2, 2.2, 10.8, 3.6, 12.2)
    half.cubicTo(2.2, 14.2, 3.2, 17.4, 5.8, 17.6)
    half.cubicTo(6.4, 20.6, 10.4, 21.2, 12, 19.4)
    sulci = QPainterPath(QPointF(5.2, 6.4))     # solchi
    sulci.cubicTo(6.4, 6.4, 7.6, 7.4, 7.8, 8.8)
    sulci.moveTo(3.6, 12.2)
    sulci.cubicTo(5, 11.2, 7, 11.6, 8, 12.8)
    sulci.moveTo(5.8, 17.6)
    sulci.cubicTo(5.8, 16, 7, 15, 8.6, 15.2)
    mirror = QTransform(-1, 0, 0, 1, 24, 0)     # emisfero destro
    for path in (half, sulci):
        p.drawPath(path)
        p.drawPath(mirror.map(path))
    p.drawLine(QPointF(12, 4), QPointF(12, 19.4))   # scissura
    p.end()
    pm.setDevicePixelRatio(dpr)
    return QIcon(pm)


def palette_for(name: str) -> dict[str, str]:
    return COLORS.get(name, COLORS["dark"])


def code_colors(name: str) -> tuple[str, str, str]:
    t = palette_for(name)
    return t["code_bg"], t["code_fg"], t["code_inline"]


def _build_palette(t: dict[str, str]) -> QPalette:
    p = QPalette()
    p.setColor(QPalette.Window, QColor(t["window"]))
    p.setColor(QPalette.WindowText, QColor(t["text"]))
    p.setColor(QPalette.Base, QColor(t["window"]))
    p.setColor(QPalette.AlternateBase, QColor(t["surface"]))
    p.setColor(QPalette.Text, QColor(t["text"]))
    p.setColor(QPalette.Button, QColor(t["surface"]))
    p.setColor(QPalette.ButtonText, QColor(t["text"]))
    p.setColor(QPalette.ToolTipBase, QColor(t["surface"]))
    p.setColor(QPalette.ToolTipText, QColor(t["text"]))
    p.setColor(QPalette.Highlight, QColor(t["accent"]))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.Link, QColor(t["code_inline"]))
    p.setColor(QPalette.PlaceholderText, QColor(t["dim"]))
    return p


def _scrollbars(t: dict[str, str]) -> str:
    h = """
    QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
    QScrollBar::handle:horizontal { background: %(scroll)s; border-radius: 4px; min-width: 30px; }
    QScrollBar::handle:horizontal:hover { background: %(scroll_hover)s; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
    """ % t
    v = """
    QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
    QScrollBar::handle:vertical { background: %(scroll)s; border-radius: 4px; min-height: 30px; }
    QScrollBar::handle:vertical:hover { background: %(scroll_hover)s; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
    """ % t
    return h + v


def build_qss(theme_name: str) -> str:
    t = palette_for(theme_name)
    return """
    QMainWindow, QDialog {{ background: {window}; }}
    QLabel {{ background: transparent; color: {text}; }}

    /* ---- sidebar ---- */
    QFrame#sidebar {{ background: {sidebar}; border: none; }}
    QFrame#sidebar QLabel {{ background: transparent; }}
    QListWidget {{ background: transparent; border: none; color: {text}; outline: 0; }}
    QListWidget::item {{ color: {text}; padding: 7px 8px; border-radius: 8px; margin: 1px 6px; }}
    QListWidget::item:hover {{ background: {hover}; }}
    QListWidget::item:selected {{ background: {hover}; color: {text}; }}

    /* ---- pulsanti ---- */
    QPushButton {{
        background: {surface}; color: {text};
        border: 1px solid {border}; border-radius: 8px; padding: 6px 14px;
    }}
    QPushButton:hover {{ background: {hover}; }}
    QPushButton:disabled {{ color: {dim}; border-color: {border}; }}
    QPushButton#primaryBtn {{ background: {accent}; color: #ffffff; border: none; font-weight: 600; }}
    QPushButton#primaryBtn:hover {{ background: {accent_hover}; }}
    QPushButton#primaryBtn:disabled {{ background: {border}; color: {dim}; }}
    QPushButton#sendBtn {{ background: {accent}; color: #ffffff; border: none; border-radius: 14px; padding: 0; }}
    QPushButton#sendBtn:hover {{ background: {accent_hover}; }}
    QPushButton#sendBtn:disabled {{ background: {border}; }}
    /* pulsanti tondi −/+ dei campi numerici (steppers.py) */
    QPushButton#stepBtn {{
        background: {surface}; color: {text};
        border: 1px solid {border}; border-radius: 13px;
        padding: 0; font-weight: 700;
    }}
    QPushButton#stepBtn:hover {{ background: {hover}; border-color: {dim}; }}
    QPushButton#stepBtn:pressed {{ background: {accent}; border-color: {accent}; color: #ffffff; }}
    QPushButton#stepBtn:disabled {{ background: transparent; color: {dim}; border-color: {border}; }}
    QToolButton {{ background: transparent; border: none; border-radius: 6px; padding: 4px; color: {dim}; }}
    QToolButton:hover {{ background: {hover}; color: {text}; }}
    /* avviso «nuova versione di OllaDesk» nella barra superiore */
    QToolButton#appUpdateBtn {{
        color: {accent}; border: 1px solid {accent};
        border-radius: 10px; padding: 2px 10px; font-weight: 600;
    }}
    QToolButton#appUpdateBtn:hover {{ background: {accent}; color: #ffffff; }}

    /* ---- bolle messaggi ---- */
    QFrame[bubble="user"] {{
        background: {bubble_user}; border: 1px solid {bubble_user_border};
        border-radius: 16px;
    }}
    QFrame[bubble="assistant"] {{ background: transparent; border: none; }}
    QFrame[bubble="error"] {{ background: {error_bg}; border: 1px solid {danger}; border-radius: 12px; }}
    QLabel[error="true"] {{ color: {danger}; }}
    QLabel#metaLabel {{ color: {dim}; }}
    QLabel#chip {{
        color: {text}; background: rgba(128,128,128,0.28);
        border-radius: 9px; padding: 2px 8px;
    }}

    /* ---- barra superiore ---- */
    QFrame#topBar {{ background: {window}; border: none; }}
    QLabel#statusLabel {{ color: {dim}; }}
    QLabel[status="online"] {{ color: {online}; font-size: 13px; }}
    QLabel[status="offline"] {{ color: {offline}; font-size: 13px; }}

    /* ---- input ---- */
    QFrame#inputFrame {{
        background: {input_bg}; border: 1px solid {border}; border-radius: 22px;
    }}
    QPlainTextEdit#inputEdit {{
        background: transparent; border: none; color: {text};
        selection-background-color: {accent}; selection-color: #ffffff;
    }}

    /* ---- controlli ---- */
    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
        background: {surface}; color: {text};
        border: 1px solid {border}; border-radius: 8px; padding: 5px 8px;
    }}
    QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {{ border-color: {dim}; }}
    QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{ border-color: {accent}; }}
    QComboBox QAbstractItemView {{
        background: {surface}; color: {text};
        selection-background-color: {hover}; selection-color: {text};
        border: 1px solid {border};
    }}
    QComboBox::drop-down {{ border: none; width: 20px; }}

    QCheckBox {{ color: {text}; spacing: 8px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px; border: 1px solid {border};
        border-radius: 4px; background: {surface};
    }}
    QCheckBox::indicator:hover {{ border-color: {dim}; }}
    QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; }}

    QTabWidget::pane {{ border: 1px solid {border}; border-radius: 8px; }}
    QTabBar::tab {{
        background: transparent; color: {dim};
        padding: 8px 18px; border: none; border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:selected {{ color: {text}; border-bottom: 2px solid {accent}; }}
    QTabBar::tab:hover {{ color: {text}; }}

    /* ---- others ---- */
    QMenu {{ background: {surface}; color: {text}; border: 1px solid {border}; border-radius: 8px; padding: 4px; }}
    QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {hover}; }}
    QMenu::separator {{ height: 1px; background: {border}; margin: 4px 8px; }}

    QToolTip {{ background: {surface}; color: {text}; border: 1px solid {border}; padding: 4px; }}

    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}

    QSplitter::handle {{ background: {border}; width: 1px; }}
    """ .format(**t) + _scrollbars(t)


def apply_theme(app: QApplication, theme_name: str, font_size: int) -> str:
    """Applica il tema (risolvendo "system") e restituisce il nome effettivo."""
    resolved = resolve_theme(theme_name)
    t = palette_for(resolved)
    app.setStyle("Fusion")
    app.setPalette(_build_palette(t))
    f = app.font()
    f.setPointSize(int(font_size))
    app.setFont(f)
    app.setStyleSheet(build_qss(resolved))
    return resolved

"""Traduzioni dell'interfaccia: italiano (testi sorgente), inglese, francese,
tedesco e spagnolo.

I cataloghi sono file gettext ``olladesk/locale/<lingua>.po`` letti
direttamente all'avvio: niente .mo da compilare, quindi deb, AppImage, AUR e
Flatpak li usano così come sono. La lingua si sceglie nelle impostazioni
(«language»); con "auto" segue quella del sistema e, se OllaDesk non la
conosce, usa l'inglese. ``OLLADESK_LANG`` la impone (test, screenshot).

Uso: ``_("testo")`` traduce subito; ``N_("testo")`` segna soltanto un testo
da tradurre più avanti (costanti di modulo, chiavi di dizionari).
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

SOURCE_LANGUAGE = "it"
FALLBACK_LANGUAGE = "en"
# nome di ogni lingua nella lingua stessa, per il menu delle impostazioni
LANGUAGES = {
    "it": "Italiano",
    "en": "English",
    "fr": "Français",
    "de": "Deutsch",
    "es": "Español",
}
LOCALE_DIR = Path(__file__).resolve().parent / "locale"

_catalog: dict[str, str] = {}
_language: str | None = None


def system_language() -> str:
    """Codice a due lettere della lingua del sistema ("" se non impostata)."""
    # stesso ordine di gettext: LANGUAGE può elencare più lingue (it:en)
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        for item in value.split(":"):
            code = item.split(".")[0].split("@")[0].split("_")[0].lower()
            if code and code not in ("c", "posix"):
                return code
    return ""


def resolve(setting: str | None) -> str:
    """Lingua effettiva per l'impostazione data ("auto" o un codice)."""
    forced = os.environ.get("OLLADESK_LANG", "").strip().lower()
    if forced in LANGUAGES:
        return forced
    if setting in LANGUAGES:
        return setting
    code = system_language()
    return code if code in LANGUAGES else FALLBACK_LANGUAGE


def _unquote(token: str) -> str:
    # le stringhe dei .po usano gli stessi escape del C: ast li decodifica
    # senza eseguire nulla
    return ast.literal_eval(token)


def parse_po(text: str) -> dict[str, str]:
    """msgid → msgstr di un file .po (salta voci fuzzy e non tradotte)."""
    out: dict[str, str] = {}
    msgid: list[str] | None = None
    msgstr: list[str] | None = None
    fuzzy = False
    current: list[str] | None = None

    def commit() -> None:
        if msgid is not None and msgstr is not None and not fuzzy:
            key, value = "".join(msgid), "".join(msgstr)
            if key and value:
                out[key] = value

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.startswith("#,") and "fuzzy" in line:
                # il flag precede la voce: chiude la precedente
                commit()
                msgid = msgstr = current = None
                fuzzy = True
            continue
        if line.startswith("msgid "):
            if msgstr is not None:
                commit()
                fuzzy = False
            msgid, msgstr = [_unquote(line[6:])], None
            current = msgid
        elif line.startswith("msgstr "):
            msgstr = [_unquote(line[7:])]
            current = msgstr
        elif line.startswith('"') and current is not None:
            current.append(_unquote(line))
    commit()
    return out


def setup(setting: str | None = None) -> str:
    """Carica il catalogo della lingua scelta; restituisce il suo codice.

    Senza argomento legge l'impostazione salvata. L'interfaccia non si
    ritraduce da sola: un cambio di lingua vale dal prossimo avvio.
    """
    global _catalog, _language
    if setting is None:
        from . import config   # import tardivo: config non dipende da i18n

        setting = config.load_settings().get("language", "auto")
    lang = resolve(setting)
    catalog: dict[str, str] = {}
    if lang != SOURCE_LANGUAGE:
        try:
            catalog = parse_po((LOCALE_DIR / f"{lang}.po").read_text(encoding="utf-8"))
        except (OSError, ValueError, SyntaxError):
            catalog = {}   # catalogo mancante o rotto: testi in italiano
    _catalog, _language = catalog, lang
    return lang


def language() -> str:
    if _language is None:
        setup()
    return _language or SOURCE_LANGUAGE


def _(text: str) -> str:
    if _language is None:
        setup()
    return _catalog.get(text, text)


def N_(text: str) -> str:
    """Segna un testo da tradurre senza tradurlo (lo farà `_` all'uso)."""
    return text


def catalog() -> dict[str, str]:
    """Il catalogo della lingua attiva (per la companion web)."""
    language()
    return dict(_catalog)


def install_qt_translator(app) -> None:
    """Traduzioni di Qt per i suoi testi (pulsanti Sì/No, Annulla…)."""
    lang = language()
    try:
        from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    except ImportError:
        return
    QLocale.setDefault(QLocale(lang))
    tr = QTranslator(app)
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if tr.load(f"qtbase_{lang}", path):
        app.installTranslator(tr)
        app._olladesk_qt_translator = tr   # tiene vivo il traduttore

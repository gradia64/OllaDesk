"""Finestre dell'importazione dei dati da un'altra installazione (F4)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from olladesk import config, migrate
from olladesk.i18n import _


def should_offer(settings: dict) -> bool:
    """Proposta al primo avvio: solo con un profilo ancora vuoto, una volta."""
    return not settings.get("profile_import_offered") and migrate.profile_is_empty()


def _mark_offered() -> None:
    s = config.load_settings()
    s["profile_import_offered"] = True
    # diretto: dopo un'importazione dalle Impostazioni save_settings è bloccato
    config._write_json(config.config_dir() / "settings.json", {**config.DEFAULT_SETTINGS, **s})


def choose_and_import(parent: QWidget | None) -> migrate.ImportResult | None:
    """Fa scegliere la cartella e importa. None se annullato o non riuscito
    (in quel caso l'utente è già stato avvisato)."""
    start = Path.home() / ".config" / config.APP_NAME
    chosen = QFileDialog.getExistingDirectory(
        parent,
        _("Cartella dei dati di OllaDesk (~/.config/olladesk)"),
        str(start),
    )
    if not chosen:
        return None
    result = migrate.import_profile(Path(chosen))
    if result is None:
        QMessageBox.warning(
            parent, _("Importa dati"),
            _("In «{path}» non ci sono dati di OllaDesk. Scegli la cartella "
              "olladesk dentro ~/.config (Ctrl+H mostra le cartelle "
              "nascoste).").format(path=chosen),
        )
        return None
    return result


def summary(result: migrate.ImportResult) -> str:
    lines = [_("Conversazioni importate: {count}").format(count=result.chats)]
    if result.skipped_chats:
        lines.append(_("Già presenti, lasciate com'erano: {count}").format(
            count=result.skipped_chats))
    if result.attachments:
        lines.append(_("Immagini allegate copiate: {count}").format(count=result.attachments))
    if "settings.json" in result.files:
        lines.append(_("Impostazioni importate."))
        # dove andranno chat e ricerche: una cartella preparata da altri può
        # indicare un server che non è il tuo
        if result.host:
            lines.append(_("Server Ollama: {host}").format(host=result.host))
        if result.search:
            lines.append(_("Ricerca web: {service}").format(service=result.search))
    if "model_params.json" in result.files:
        lines.append(_("Parametri dei modelli importati."))
    lines.append(_("La companion web resta spenta e i telefoni non vengono importati: "
                   "riabbinali dalla finestra «Companion» (Ctrl+D)."))
    return "\n".join(lines)


def offer_at_startup(settings: dict) -> bool:
    """Al primo avvio in Flatpak propone l'importazione. True se ha importato."""
    if not should_offer(settings):
        return False
    box = QMessageBox(QMessageBox.Icon.Question, _("Benvenuto in OllaDesk"), _(
        "Hai già usato OllaDesk installato in un altro modo (.deb, AUR, "
        "AppImage)? Nel Flatpak i dati stanno in una cartella separata: puoi "
        "importare conversazioni, impostazioni e parametri dei modelli.\n\n"
        "Nella finestra che si apre scegli la cartella olladesk dentro "
        "~/.config (Ctrl+H mostra le cartelle nascoste)."))
    imp = box.addButton(_("Importa dati…"), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(_("Inizia da zero"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(imp)
    box.exec()
    imported = None
    if box.clickedButton() is imp:
        imported = choose_and_import(None)
    # le impostazioni importate sostituiscono quelle di prima: il segno va
    # scritto dopo, altrimenti la proposta tornerebbe al prossimo avvio
    _mark_offered()
    if imported is None:
        return False
    QMessageBox.information(None, _("Importa dati"), summary(imported))
    return True


def import_from_settings(parent: QWidget) -> bool:
    """Pulsante delle Impostazioni. True se ha importato: l'app va chiusa
    subito, perché la finestra ha in memoria le impostazioni di prima e il
    primo salvataggio le riscriverebbe."""
    ok = QMessageBox.question(
        parent, _("Importa dati"),
        _("Le conversazioni della cartella scelta si aggiungono a quelle "
          "presenti; impostazioni e parametri dei modelli sostituiscono quelli "
          "attuali. Importa solo una cartella tua: le impostazioni decidono a "
          "quale server vanno le chat.\n\nAlla fine OllaDesk si chiude: "
          "riaprilo per vedere i dati importati. Continuare?"),
    )
    if ok != QMessageBox.StandardButton.Yes:
        return False
    result = choose_and_import(parent)
    if result is None:
        return False
    # prima di qualsiasi ciclo di eventi: nessun salvataggio in coda deve
    # riscrivere le impostazioni appena importate
    config.freeze_settings()
    _mark_offered()
    QMessageBox.information(
        parent, _("Importa dati"),
        summary(result) + "\n\n" + _("OllaDesk ora si chiude: riaprilo."),
    )
    return True

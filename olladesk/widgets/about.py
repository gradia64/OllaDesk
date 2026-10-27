"""Contenuto delle pagine «Info» e «Aiuto» delle impostazioni.

Qui ci sono solo dati e testi (tradotti con i cataloghi di i18n); le pagine
le costruisce SettingsDialog. I collegamenti sono costanti del progetto: le
etichette che li mostrano aprono solo indirizzi https di GitHub.
"""
from __future__ import annotations

import os
import platform
import sys

from .. import __version__, app_update
from ..i18n import N_, _

REPO_URL = "https://github.com/gradia64/OllaDesk"
MAINTAINER = "gradia <gradia@disroot.org>"
LICENSE_NAME = "GPL-3.0-or-later"
# chiave primaria che certifica le due sottochiavi di firma (tag e allegati):
# la stessa impronta del README, sezione «Verifica delle firme»
RELEASE_KEY = "ed25519  5B16 6C1B 4AD7 428C 74A0  7D5B A337 0987 A057 6694"

# (etichetta, indirizzo)
LINKS = (
    (N_("Codice sorgente"), REPO_URL),
    (N_("Note di rilascio"), f"{REPO_URL}/releases"),
    (N_("Segnala un problema"), f"{REPO_URL}/issues"),
    (N_("Segnala una vulnerabilità (privata)"), f"{REPO_URL}/security/advisories/new"),
    (N_("Licenza"), f"{REPO_URL}/blob/main/LICENSE"),
)


def install_label(method: str) -> str:
    return {
        "deb": _("pacchetto .deb"), "arch": _("pacchetto AUR"), "appimage": "AppImage",
        "flatpak": "Flatpak", "source": _("sorgenti (git)"), "pip": _("pacchetto Python"),
    }.get(method, method)


def _os_name() -> str:
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system()


def component_versions() -> str:
    """«Python 3.13.5 · Qt 6.11.2 · PySide6 6.11.2»."""
    try:
        import PySide6
        from PySide6.QtCore import qVersion
        qt, pyside = qVersion(), PySide6.__version__
    except ImportError:   # pragma: no cover - l'app non parte senza PySide6
        qt = pyside = "?"
    return f"Python {platform.python_version()} · Qt {qt} · PySide6 {pyside}"


def system_info(ollama_version: str = "") -> str:
    """Testo da incollare in una segnalazione: versioni e ambiente, niente
    dati personali (né percorsi, né indirizzi, né impostazioni)."""
    from .. import i18n
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "?")
    session = os.environ.get("XDG_SESSION_TYPE", "?")
    lines = [
        f"OllaDesk {__version__} ({app_update.install_method()})",
        component_versions(),
        f"Ollama {ollama_version or '?'}",
        f"{_os_name()} · {platform.machine()} · {desktop} ({session})",
        f"lingua: {i18n.language()} · {sys.platform}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------- aiuto

# (titolo, testo): testo semplice, le righe che iniziano con «•» sono elenchi
HELP_SECTIONS = (
    (N_("Per iniziare"), N_(
        "OllaDesk parla con Ollama, che deve essere in esecuzione su questo computer "
        "o in rete (Impostazioni → Generale → «Server Ollama»).\n"
        "• Scegli il modello in alto e scrivi il messaggio: Invio lo invia, Shift+Invio va a capo.\n"
        "• Per scaricare o rimuovere modelli: «Modelli» nella barra laterale (Ctrl+M).\n"
        "• L'indicatore in alto a destra mostra lo stato di Ollama; un clic apre versione, "
        "aggiornamento, modelli e indirizzo del server.")),
    (N_("Conversazioni"), N_(
        "• «＋» in alto o Ctrl+N: nuova conversazione.\n"
        "• L'elenco è diviso per data; il campo di ricerca (Ctrl+K) filtra i titoli.\n"
        "• Tasto destro su una conversazione: rinomina, archivia o elimina.\n"
        "• L'archivio (accanto a «Nuova chat») contiene le conversazioni archiviate: "
        "non sono eliminate e si ripristinano con il tasto destro.")),
    (N_("Allegati e cartelle"), N_(
        "Con la graffetta nell'input, trascinando o incollando:\n"
        "• file di testo e codice, PDF, documenti Word, Excel e OpenDocument: il modello ne riceve il testo;\n"
        "• immagini, per i modelli che leggono le immagini (badge con l'occhio accanto al modello);\n"
        "• una cartella intera, con i file di testo, codice, PDF e documenti che contiene.\n"
        "I file si leggono al momento dell'invio; un documento molto grande viene allegato in parte, con un avviso.")),
    (N_("Ricerca web"), N_(
        "Il globo nell'input allega al prossimo messaggio i risultati di una ricerca online. "
        "Il servizio (DuckDuckGo, Ollama Cloud o una tua istanza SearXNG) si sceglie in "
        "Impostazioni → Ricerca web; le fonti compaiono sotto la risposta.")),
    (N_("Ragionamento"), N_(
        "Il cervello nell'input attiva o spegne il ragionamento dei modelli che lo supportano "
        "(badge con il cervello accanto al modello). Il pensiero compare sopra la risposta, "
        "in un blocco che si apre e si chiude.")),
    (N_("Risposte e codice"), N_(
        "• Ogni blocco di codice ha il nome del linguaggio e «Copia».\n"
        "• Il pulsante di copia del messaggio compare passando con il mouse.\n"
        "• Il pulsante di invio diventa un quadrato durante la risposta: premilo per interromperla.")),
    (N_("Companion web: OllaDesk dal telefono"), N_(
        "«Companion» nella barra laterale (Ctrl+D) avvia una pagina web per il telefono, sulla "
        "stessa rete. Il telefono si abbina con un codice di 6 cifre o con il QR code; le "
        "conversazioni sono le stesse del PC.")),
    (N_("Modelli e parametri"), N_(
        "In Impostazioni → Parametri modelli ogni modello ha i suoi parametri (temperatura, "
        "finestra di contesto…): si inviano solo quelli spuntati. In Impostazioni → Generale "
        "si scrivono il prompt di sistema e quanti messaggi precedenti inviare.")),
    (N_("Se qualcosa non va"), N_(
        "• «offline» in alto: controlla che Ollama sia avviato (ollama serve) e l'indirizzo del server.\n"
        "• Il modello dimentica l'inizio della conversazione: aumenta «num_ctx» nei parametri del "
        "modello o riduci allegati e messaggi inviati.\n"
        "• Per una segnalazione: Impostazioni → Info → «Copia informazioni di sistema».")),
)

# (tasti, descrizione): i nomi dei tasti con «Invio» si traducono
SHORTCUTS = (
    ("Ctrl+N", N_("Nuova conversazione")),
    ("Ctrl+K", N_("Cerca tra le conversazioni")),
    ("Ctrl+B", N_("Mostra o nasconde la barra laterale")),
    ("Ctrl+M", N_("Gestione dei modelli")),
    ("Ctrl+D", N_("Companion web")),
    ("Ctrl+,", N_("Impostazioni")),
    ("F1", N_("Questo aiuto")),
    (N_("Ctrl+Invio"), N_("Invia sempre, anche con «Invio rapido» spento")),
    (N_("Shift+Invio"), N_("A capo nel messaggio")),
)

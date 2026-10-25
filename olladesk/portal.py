"""File trascinati o incollati dentro la sandbox Flatpak.

Il file manager (Dolphin, Nautilus…) gira fuori dalla sandbox: nel
text/uri-list mette i percorsi dell'host, che dentro la sandbox non
esistono. Se è compatibile con il portale FileTransfer aggiunge anche una
chiave in «application/vnd.portal.filetransfer»; chi riceve la scambia con
RetrieveFiles e ottiene percorsi del portale dei documenti
(/run/user/<uid>/doc/…) leggibili senza permessi sul filesystem. GTK lo fa
da sé, il Qt del runtime KDE no: lo fa questo modulo.

I portali sono raggiungibili da ogni app Flatpak, senza --talk-name.
"""
from __future__ import annotations

import os

FILETRANSFER_MIME = "application/vnd.portal.filetransfer"
# il portale risponde in pochi millisecondi; il tempo massimo evita che un
# bus bloccato congeli l'interfaccia (la chiamata avviene nel drop)
PORTAL_TIMEOUT = 3.0


def in_flatpak() -> bool:
    return os.path.exists("/.flatpak-info")


def retrieve_files(key: str) -> list[str] | None:
    """Percorsi leggibili per la chiave del portale, o None se non riesce.

    Usa jeepney (puro Python, già dipendenza di keyring): il PySide6 di
    alcune distribuzioni non include QtDBus.
    """
    key = key.strip("\x00 \r\n")
    if not key:
        return None
    try:
        from jeepney import DBusAddress, MessageType, new_method_call
        from jeepney.io.blocking import open_dbus_connection
    except ImportError:
        return None
    addr = DBusAddress("/org/freedesktop/portal/documents",
                       bus_name="org.freedesktop.portal.Documents",
                       interface="org.freedesktop.portal.FileTransfer")
    try:
        with open_dbus_connection(bus="SESSION") as conn:
            reply = conn.send_and_get_reply(
                new_method_call(addr, "RetrieveFiles", "sa{sv}", (key, {})),
                timeout=PORTAL_TIMEOUT,
            )
    except Exception:   # bus assente, portale mancante, chiave scaduta, tempo scaduto
        return None
    if reply.header.message_type == MessageType.error:
        return None   # chiave sconosciuta o già usata (vale una volta sola)
    body = reply.body
    if not body or not isinstance(body[0], list):
        return None
    return [p for p in body[0] if isinstance(p, str) and p]

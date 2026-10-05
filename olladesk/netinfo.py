"""Indirizzi di rete locale di questo PC, per i dispositivi della companion web."""
from __future__ import annotations

from PySide6.QtNetwork import QNetworkInterface

# interfacce virtuali inutili per un telefono: bridge/container (Docker,
# libvirt). WireGuard/Tailscale restano: servono per l'accesso da fuori casa.
_INTERFACE_NOISE = ("docker", "br-", "veth", "virbr", "tap", "lo")


def _is_shareable_ip(text: str) -> bool:
    """True per un IPv4 di rete utile ai client mobili (no loopback/link-local/IPv6)."""
    if ":" in text or "." not in text:
        return False   # IPv6 o stringa non valida
    parts = text.split(".")
    if len(parts) != 4:
        return False
    try:
        values = [int(p) for p in parts]
    except ValueError:
        return False
    if any(v < 0 or v > 255 for v in values):
        return False
    first, second = values[0], values[1]
    if first == 0 or first == 127:          # 0.0.0.0, loopback
        return False
    if first == 169 and second == 254:      # link-local
        return False
    return True


def lan_urls(port: int) -> list[str]:
    """Indirizzi http://<ip-lan>:<port> raggiungibili da altri dispositivi.

    Esclude loopback, link-local (169.254.x.x), IPv6 e le interfacce virtuali
    (docker*, br-*, veth*, virbr*): per il cellulare conta l'IPv4 della LAN.
    """
    out: list[str] = []
    for iface in QNetworkInterface.allInterfaces():
        name = iface.name() or ""
        if name.startswith(_INTERFACE_NOISE):
            continue
        flags = iface.flags()
        if not (flags & QNetworkInterface.InterfaceFlag.IsUp) or not (
            flags & QNetworkInterface.InterfaceFlag.IsRunning
        ):
            continue
        for entry in iface.addressEntries():
            text = entry.ip().toString()
            if _is_shareable_ip(text):
                url = f"http://{text}:{port}"
                if url not in out:
                    out.append(url)
    return out

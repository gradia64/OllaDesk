"""Chiave API nel portachiavi di sistema (KWallet su Plasma, via keyring).

Se il modulo `keyring` non è installato o non c'è un backend funzionante, si
degrada senza errori: la chiave resta in settings.json (protettto con chmod
600 da config.py) e la UI continua a offrire il campo password.
"""
from __future__ import annotations

_SERVICE = "olladesk"
_KEY_NAME = "ollama-cloud-api-key"


def _keyring():
    try:
        import keyring
        kr = keyring.get_keyring()
        # il backend "fail" risponde sempre con errori: equivale a niente
        if kr is None or kr.__class__.__module__.endswith("fail"):
            return None
        return kr
    except Exception:
        return None


def available() -> bool:
    return _keyring() is not None


def save_api_key(key: str) -> bool:
    """Salva (o rimuove, con stringa vuota) la chiave nel portachiavi."""
    kr = _keyring()
    if kr is None:
        return False
    try:
        if key:
            kr.set_password(_SERVICE, _KEY_NAME, key)
        else:
            try:
                kr.delete_password(_SERVICE, _KEY_NAME)
            except Exception:
                pass
        return True
    except Exception:
        return False


def load_api_key() -> str:
    kr = _keyring()
    if kr is None:
        return ""
    try:
        return kr.get_password(_SERVICE, _KEY_NAME) or ""
    except Exception:
        return ""

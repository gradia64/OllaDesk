"""Client HTTP per l'API di Ollama (solo libreria standard) + worker Qt.

Tutte le richieste di lunga durata (streaming, elenco modelli) girano in
QThread separati e comunicano con la UI tramite segnali.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from PySide6.QtCore import QThread, Signal


class OllamaError(Exception):
    """Errore di comunicazione con il server Ollama."""


def friendly_error(e: Exception, host: str = "") -> str:
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            body = ""
        if "model" in body.lower() and "not found" in body.lower():
            return "Modello non trovato sul server: scaricalo con `ollama pull <modello>`."
        return f"Errore HTTP {e.code} dal server Ollama: {body or e.reason}"
    if isinstance(e, urllib.error.URLError):
        reason = getattr(e, "reason", e)
        if isinstance(reason, ConnectionRefusedError):
            return f"Connessione rifiutata: Ollama non è in esecuzione su {host or 'localhost'}? Avvialo con `ollama serve`."
        if isinstance(reason, TimeoutError):
            return "Timeout di connessione con il server Ollama."
        return f"Impossibile raggiungere il server Ollama ({host}): {reason}"
    return str(e)


def _open(host: str, path: str, payload: dict | None = None, timeout: float = 10):
    url = host.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    return urllib.request.urlopen(req, timeout=timeout)


def server_version(host: str) -> str:
    try:
        with _open(host, "/api/version", timeout=4) as resp:
            return json.loads(resp.read().decode("utf-8")).get("version", "?")
    except Exception as e:  # pragma: no cover - errore di rete gestito dal chiamante
        raise OllamaError(friendly_error(e, host)) from e


def list_models(host: str) -> list[dict]:
    """Restituisce l'elenco dei modelli installati (GET /api/tags)."""
    try:
        with _open(host, "/api/tags", timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise OllamaError(friendly_error(e, host)) from e
    models = []
    for m in data.get("models", []):
        det = m.get("details", {})
        models.append(
            {
                "name": m.get("name") or m.get("model") or "?",
                "size": m.get("size", 0),
                "parameter_size": det.get("parameter_size", ""),
                "quantization": det.get("quantization_level", ""),
                "family": det.get("family", ""),
            }
        )
    models.sort(key=lambda m: m["name"].lower())
    return models


# --------------------------------------------------------------------- worker

class ApiWorker(QThread):
    """Richiesta GET generica (/api/tags, /api/version) in background."""

    ready = Signal(object)   # dict JSON
    failed = Signal(str)

    def __init__(self, host: str, path: str, parent=None):
        super().__init__(parent)
        self._host = host
        self._path = path

    def run(self) -> None:
        try:
            with _open(self._host, self._path, timeout=8) as resp:
                self.ready.emit(json.loads(resp.read().decode("utf-8")))
        except Exception as e:
            self.failed.emit(friendly_error(e, self._host))


class ChatWorker(QThread):
    """POST /api/chat con streaming NDJSON (o risposta singola)."""

    chunk = Signal(str)      # frammento di testo dell'assistente
    done = Signal(dict)      # payload finale (statistiche)
    failed = Signal(str)

    def __init__(self, host: str, payload: dict, parent=None):
        super().__init__(parent)
        self._host = host
        self._payload = payload
        self._resp = None
        self._stopped = False

    def stop(self) -> None:
        """Interrompe la generazione e chiude la connessione."""
        self._stopped = True
        if self._resp is not None:
            try:
                self._resp.close()
            except Exception:
                pass

    @property
    def stopped(self) -> bool:
        return self._stopped

    def run(self) -> None:
        try:
            self._resp = _open(self._host, "/api/chat", self._payload, timeout=600)
        except Exception as e:
            if not self._stopped:
                self.failed.emit(friendly_error(e, self._host))
            return

        try:
            if not self._payload.get("stream", True):
                data = json.loads(self._resp.read().decode("utf-8"))
                if data.get("error"):
                    self.failed.emit(str(data["error"]))
                    return
                content = (data.get("message") or {}).get("content", "")
                if content:
                    self.chunk.emit(content)
                self.done.emit(data if data.get("done") else {"done": True})
                return

            for raw in self._resp:
                if self._stopped:
                    return
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw.decode("utf-8"))
                except ValueError:
                    continue
                if data.get("error"):
                    self.failed.emit(str(data["error"]))
                    return
                if data.get("done"):
                    self.done.emit(data)
                    return
                content = (data.get("message") or {}).get("content", "")
                if content:
                    self.chunk.emit(content)
            # stream chiuso senza "done": consideriamo comunque concluso
            self.done.emit({"done": True})
        except Exception as e:
            if not self._stopped:
                self.failed.emit(friendly_error(e, self._host))


class PostWorker(QThread):
    """POST JSON generico (es. /api/delete per rimuovere un modello)."""

    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, host: str, path: str, payload: dict | None = None, parent=None):
        super().__init__(parent)
        self._host = host
        self._path = path
        self._payload = payload

    def run(self) -> None:
        try:
            with _open(self._host, self._path, self._payload, timeout=60) as resp:
                body = resp.read().decode("utf-8", "replace").strip()
            self.ready.emit(json.loads(body) if body else None)
        except Exception as e:
            self.failed.emit(friendly_error(e, self._host))


class PullWorker(QThread):
    """Scarica un modello con POST /api/pull (NDJSON di avanzamento)."""

    progress = Signal(dict)
    done = Signal()
    failed = Signal(str)

    def __init__(self, host: str, model: str, parent=None):
        super().__init__(parent)
        self._host = host
        self._model = model
        self._resp = None
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True
        if self._resp is not None:
            try:
                self._resp.close()
            except Exception:
                pass

    @property
    def stopped(self) -> bool:
        return self._stopped

    def run(self) -> None:
        try:
            self._resp = _open(
                self._host, "/api/pull",
                {"model": self._model, "stream": True}, timeout=600,
            )
        except Exception as e:
            if not self._stopped:
                self.failed.emit(friendly_error(e, self._host))
            return
        try:
            for raw in self._resp:
                if self._stopped:
                    return
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw.decode("utf-8"))
                except ValueError:
                    continue
                if data.get("error"):
                    self.failed.emit(str(data["error"]))
                    return
                if data.get("status") == "success":
                    self.done.emit()
                    return
                self.progress.emit(data)
            self.done.emit()
        except Exception as e:
            if not self._stopped:
                self.failed.emit(friendly_error(e, self._host))


def format_stats(done: dict) -> str | None:
    """Riepilogo leggibile dal payload finale di /api/chat."""
    if not isinstance(done, dict):
        return None
    ev = done.get("eval_count")
    if not ev:
        return None
    parts = [f"{ev} token"]
    ed = done.get("eval_duration")
    if ed:
        parts.append(f"{ev / (ed / 1e9):.1f} tok/s")
    td = done.get("total_duration")
    if td:
        parts.append(f"{td / 1e9:.1f} s")
    return " · ".join(parts)

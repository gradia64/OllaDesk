"""Client HTTP per l'API di Ollama (solo libreria standard) + worker Qt.

Tutte le richieste di lunga durata (streaming, elenco modelli) girano in
QThread separati e comunicano con la UI tramite segnali.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from PySide6.QtCore import QThread, Signal


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


# --------------------------------------------------------------------- worker

class ApiWorker(QThread):
    """Richiesta GET generica (/api/tags, /api/version) in background."""

    ready = Signal(object)   # dict JSON
    failed = Signal(str)

    def __init__(self, host: str, path: str, parent=None):
        super().__init__(parent)
        self._host = host
        self._path = path
        self._resp = None

    def stop(self) -> None:
        resp, self._resp = self._resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    def run(self) -> None:
        try:
            self._resp = _open(self._host, self._path, timeout=8)
            data = json.loads(self._resp.read().decode("utf-8"))
            self.ready.emit(data)
        except Exception as e:
            self.failed.emit(friendly_error(e, self._host))
        finally:
            self._close_resp()

    def _close_resp(self) -> None:
        resp, self._resp = self._resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass


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
        self._close_resp()

    @property
    def stopped(self) -> bool:
        return self._stopped

    def run(self) -> None:
        try:
            self._resp = _open(self._host, "/api/chat", self._payload, timeout=600)
            if self._stopped:
                # chiudere subito: altrimenti Ollama continua a generare per
                # una richiesta già annullata e accoda quella successiva
                self._close_resp()
                return
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
            if not self._stopped:
                self.done.emit({"done": True})
        except Exception as e:
            if not self._stopped:
                self.failed.emit(friendly_error(e, self._host))
        finally:
            self._close_resp()

    def _close_resp(self) -> None:
        resp, self._resp = self._resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass


class PostWorker(QThread):
    """Richiesta JSON generica (POST per /api/pull, DELETE per /api/delete…)."""

    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, host: str, path: str, payload: dict | None = None, parent=None,
                 method: str = "POST"):
        super().__init__(parent)
        self._host = host
        self._path = path
        self._payload = payload
        self._method = method

    def run(self) -> None:
        try:
            url = self._host.rstrip("/") + self._path
            data = json.dumps(self._payload).encode("utf-8") if self._payload is not None else None
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json"},
                method=self._method,
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
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
        self._close_resp()

    @property
    def stopped(self) -> bool:
        return self._stopped

    def run(self) -> None:
        try:
            self._resp = _open(
                self._host, "/api/pull",
                {"model": self._model, "stream": True}, timeout=600,
            )
            if self._stopped:
                self._close_resp()   # interrompe il pull anche lato server
                return
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
            # stream chiuso senza "success": scaricamento non concluso
            if not self._stopped:
                self.failed.emit("scaricamento interrotto: il server ha chiuso la connessione")
        except Exception as e:
            if not self._stopped:
                self.failed.emit(friendly_error(e, self._host))
        finally:
            self._close_resp()

    def _close_resp(self) -> None:
        resp, self._resp = self._resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass


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

"""Companion web: OllaDesk dal telefono o dal tablet nella rete locale.

Un `ThreadingHTTPServer` (libreria standard) gira in un thread proprio e
serve una pagina mobile e una piccola API JSON. Regola dei thread: il
thread HTTP non tocca mai lo stato delle conversazioni. Le richieste
passano al thread Qt principale con un segnale in coda (`_Bridge`) e la
risposta torna su una coda thread-safe.

Abbinamento: il desktop mostra un codice di 6 cifre, monouso e valido 2
minuti. Il telefono lo scambia con un token di sessione in un cookie
`HttpOnly; SameSite=Strict`, mai nell'URL. Su disco resta solo l'hash dei
token (companion.json); «Revoca dispositivi» li cancella tutti.

Limite noto: solo HTTP in LAN, quindi chi è sulla stessa Wi-Fi può
intercettare il cookie.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import ipaddress
import json
import os
import queue
import re
import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal

from . import __version__, config, context, netinfo, web_search
from .md import md_to_html

CODE_TTL = 120            # secondi di validità del codice di abbinamento
MAX_ATTEMPTS = 5          # tentativi errati che invalidano il codice
MAX_DEVICES = 20          # token conservati (i più vecchi escono)
COOKIE = "olladesk_session"
COOKIE_MAX_AGE = 365 * 24 * 3600
MAX_BODY = 512 * 1024     # un messaggio di MAX_TEXT caratteri anche se tutto escapato
BRIDGE_TIMEOUT = 5.0

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/icon-maskable-512.png": ("icon-maskable-512.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    # i browser la chiedono comunque: la PNG va bene a tutti
    "/favicon.ico": ("icon-192.png", "image/png"),
}

_CHAT_ID = config.CHAT_ID_PATTERN
_CHAT_URL_RE = re.compile(rf"^/api/chats/({_CHAT_ID})$")
_EVENTS_URL_RE = re.compile(rf"^/api/chats/({_CHAT_ID})/events$")

# colori del Markdown reso: il foglio di stile della pagina li ridefinisce
# per il tema chiaro/scuro (le regole CSS battono gli attributi HTML)
_MD_COLORS = ("#1a1d21", "#e8eaed", "#79b8ff")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- abbinamento

class PairingAuth:
    """Codice di abbinamento e token dei dispositivi. Thread-safe."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._code: str | None = None
        self._expires = 0.0
        self._failures = 0
        devices = config.load_companion().get("devices")
        self._devices: list[dict] = [
            d for d in (devices or [])
            if isinstance(d, dict) and isinstance(d.get("hash"), str)
        ]

    def _save(self) -> None:
        config.save_companion({"devices": self._devices})

    def new_code(self) -> tuple[str, float]:
        """Nuovo codice (sostituisce il precedente): (codice, secondi di validità)."""
        with self._lock:
            self._code = f"{secrets.randbelow(10**6):06d}"
            self._expires = self._clock() + CODE_TTL
            self._failures = 0
            return self._code, float(CODE_TTL)

    def cancel_code(self) -> None:
        with self._lock:
            self._code = None

    def code_remaining(self) -> float:
        """Secondi di validità rimasti del codice attivo (0 se nessuno)."""
        with self._lock:
            if self._code is None:
                return 0.0
            return max(0.0, self._expires - self._clock())

    def pair(self, code: str) -> str | None:
        """Scambia il codice con un token nuovo; None se rifiutato."""
        with self._lock:
            if self._code is None or self._clock() >= self._expires:
                self._code = None
                return None
            if not hmac.compare_digest(str(code).encode(), self._code.encode()):
                self._failures += 1
                if self._failures >= MAX_ATTEMPTS:
                    self._code = None   # troppi errori: serve un codice nuovo
                return None
            self._code = None           # monouso
            token = secrets.token_urlsafe(32)
            self._devices.append({"hash": _hash(token), "paired": time.time()})
            self._devices = self._devices[-MAX_DEVICES:]
            self._save()
            return token

    def check(self, token: str | None) -> bool:
        if not token:
            return False
        h = _hash(token)
        with self._lock:
            return any(hmac.compare_digest(h, d["hash"]) for d in self._devices)

    def device_count(self) -> int:
        with self._lock:
            return len(self._devices)

    def revoke_all(self) -> None:
        """Scollega tutti i dispositivi e annulla il codice in corso."""
        with self._lock:
            self._devices = []
            self._code = None
            self._save()


# ------------------------------------------------------------ allegati

MAX_UPLOAD = context.MAX_IMAGE_BYTES   # 20 MB per file, come per le immagini dal PC
MAX_ATTACHMENTS = 10                   # allegati per messaggio
MAX_PENDING = 30                       # caricati e non ancora inviati
UPLOAD_TTL = 3600                      # secondi prima che un caricamento orfano sparisca
MAX_NAME_BYTES = 120                   # nome salvato: id + "_" + nome resta sotto i 255 byte
_UNSAFE_NAME_RE = re.compile(r"[^\w.\- ()]+")


class UploadRefused(Exception):
    """Caricamento non accettato: (stato HTTP, messaggio per il telefono)."""


def _cut_bytes(text: str, limit: int) -> str:
    """Taglia `text` a `limit` byte UTF-8 senza spezzare un carattere."""
    return text.encode("utf-8")[:limit].decode("utf-8", "ignore")


def safe_filename(name: str) -> str:
    """Nome di file ripulito: niente percorsi, solo caratteri innocui.

    Il limite dei filesystem è in byte, non in caratteri: un nome lungo in
    cinese o con molti accenti va tagliato in byte. Il punto iniziale resta
    (`.env`, `.gitignore` sono file di testo riconosciuti): il nome salvato
    ha comunque davanti l'id, quindi non diventa un file nascosto.
    """
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = _UNSAFE_NAME_RE.sub("_", base).strip(" ").rstrip(".")
    if base.strip(".") == "":
        base = "file"
    stem, dot, ext = base.rpartition(".")
    if dot and stem and len(ext) <= 10:
        return _cut_bytes(stem, MAX_NAME_BYTES - len(ext.encode()) - 1) + "." + ext
    return _cut_bytes(base, MAX_NAME_BYTES)


class UploadStore:
    """File caricati dal telefono e non ancora inviati. Thread-safe.

    Il telefono conosce solo l'id: all'invio il server lo traduce nel file
    che ha salvato lui, nella cartella privata degli allegati. Mai un
    percorso scelto dal client.
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}

    @staticmethod
    def folder() -> Path:
        d = config.attachments_dir() / "companion"
        d.mkdir(mode=0o700, exist_ok=True)
        return d

    def pending(self) -> int:
        with self._lock:
            return len(self._items)

    def add(self, name: str, kind: str, rfile, length: int) -> dict:
        """Salva `length` byte letti da `rfile`.

        OSError se il telefono interrompe l'invio (o la companion si spegne
        nel frattempo); UploadRefused se il PC non accetta o non riesce a
        salvare il file.
        """
        self.purge()
        upload_id = secrets.token_hex(8)
        path = self.folder() / f"{upload_id}_{name}"
        # registrato PRIMA di scrivere: uno spegnimento a metà lo vede e lo
        # cancella, e il limite dei caricamenti in attesa non si supera
        with self._lock:
            if len(self._items) >= MAX_PENDING:
                raise UploadRefused(429, "troppi allegati in attesa: invia o riprova più tardi")
            self._items[upload_id] = {"path": str(path), "name": name, "kind": kind,
                                      "t": self._clock(), "partial": True}
        try:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except OSError as e:
                raise UploadRefused(500, f"impossibile salvare l'allegato sul PC ({e.strerror or e})") from e
            with os.fdopen(fd, "wb") as fh:
                left = length
                while left > 0:
                    chunk = rfile.read(min(65536, left))
                    if not chunk:
                        raise OSError("caricamento interrotto")
                    try:
                        fh.write(chunk)
                    except OSError as e:   # disco pieno, permessi
                        raise UploadRefused(500, f"impossibile salvare l'allegato sul PC ({e.strerror or e})") from e
                    left -= len(chunk)
            with self._lock:
                item = self._items.get(upload_id)
                if item is None:   # companion spenta durante il caricamento
                    raise OSError("companion spenta durante il caricamento")
                item.pop("partial", None)
        except BaseException:
            with self._lock:
                self._items.pop(upload_id, None)
            path.unlink(missing_ok=True)
            raise
        return {"id": upload_id, "name": name, "kind": kind}

    def take(self, ids: list[str]) -> list[dict] | None:
        """Allegati per un invio (monouso); None se un id è sconosciuto,
        scaduto, ancora in caricamento o ripetuto."""
        with self._lock:
            if len(set(ids)) != len(ids) or any(
                i not in self._items or self._items[i].get("partial") for i in ids
            ):
                return None
            return [
                {k: v for k, v in self._items.pop(i).items() if k != "t"} | {"id": i}
                for i in ids
            ]

    def give_back(self, metas: list[dict]) -> None:
        """Invio rifiutato (es. occupato): gli allegati restano disponibili."""
        with self._lock:
            for m in metas:
                self._items[m["id"]] = {k: v for k, v in m.items() if k != "id"} | {"t": self._clock()}

    def purge(self, max_age: float = UPLOAD_TTL) -> None:
        """Cancella i caricamenti mai inviati più vecchi di `max_age` secondi."""
        now = self._clock()
        with self._lock:
            old = [i for i, m in self._items.items() if now - m["t"] >= max_age]
            gone = [self._items.pop(i) for i in old]
        for m in gone:
            Path(m["path"]).unlink(missing_ok=True)

    def clear(self) -> None:
        self.purge(max_age=-1)


# ------------------------------------------------------------------- ponte

class _Bridge(QObject):
    """Esegue funzioni nel thread Qt principale per conto del thread HTTP."""

    _job = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._job.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def _run(self, job) -> None:
        fn, args, out = job
        try:
            out.put((True, fn(*args)))
        except Exception as e:   # l'errore torna al thread HTTP
            out.put((False, e))

    def call(self, fn, *args, timeout: float | None = None):
        """Dal thread HTTP: esegue `fn(*args)` nel thread principale e attende."""
        out: queue.Queue = queue.Queue(maxsize=1)
        self._job.emit((fn, args, out))
        # queue.Empty se il thread principale è bloccato
        ok, value = out.get(timeout=timeout or BRIDGE_TIMEOUT)
        if not ok:
            raise value
        return value


# ------------------------------------------------------- dati per il browser

MAX_TEXT = 64_000          # caratteri massimi di un messaggio dal telefono
ANSWER_INTERVAL = 0.35     # secondi tra due rese Markdown durante lo streaming
PING_INTERVAL = 15.0       # commento SSE che tiene viva la connessione


def chat_index(engine, hub) -> dict:
    """Thread principale: elenco delle conversazioni, dalla più recente.

    `seq` è l'ultimo evento già compreso: lo stream globale riparte da lì.
    `active` è la conversazione che sta rispondendo, se c'è.
    """
    items = [
        {"id": c["id"], "title": c.get("title", ""), "model": c.get("model", ""),
         "updated": c.get("updated", 0)}
        for c in engine.chats()
    ]
    items.sort(key=lambda c: c["updated"], reverse=True)
    return {"chats": items, "seq": hub.seq(),
            "active": engine.active_chat_id() if engine.busy() else None}


def chat_snapshot(engine, hub, chat_id: str) -> dict | None:
    """Thread principale: copia della conversazione, da rendere altrove.

    `seq` è l'ultimo evento già compreso nella copia: lo stream SSE riparte
    da lì. `state` dice se questa conversazione è in elaborazione.
    """
    c = engine.chat(chat_id)
    active = engine.active_chat_id() == chat_id
    if c is None and not active:
        return None
    snap = copy.deepcopy(c) if c is not None else {"id": chat_id, "messages": []}
    snap["seq"] = hub.seq()
    snap["state"] = engine.phase() if active else None
    return snap


def _task_info(t: dict | None) -> dict | None:
    """Stato di scarica/elimina per il telefono (solo i campi mostrati)."""
    if not t:
        return None
    return {k: t.get(k) for k in ("op", "name", "state", "pct", "status", "error", "ended")}


def models_info(engine) -> dict:
    """Thread principale: modelli installati e stato del server."""
    return {
        "models": [
            {"name": m["name"],
             "size": m.get("size") or 0,
             "details": {k: m.get("details", {}).get(k, "")
                         for k in ("parameter_size", "quantization_level")}}
            for m in engine.models()
        ],
        "task": _task_info(engine.model_task()),
        "online": bool(engine.online()),
        "version": engine.version(),
        "busy": engine.busy(),
        "thinking": bool(engine.settings.get("thinking", True)),
    }


def web_send(engine, hub, chat_id: str | None, text: str, model: str, think: bool,
             attachments: list[dict] = (), web: bool = False):
    """Thread principale: invio dal telefono.

    Restituisce (200, chat_id, seq) oppure (stato HTTP, messaggio, None).
    `seq` precede gli eventi di questo invio: lo stream SSE li riceve tutti.
    """
    busy = (409, "occupato: il PC sta già rispondendo, riprova alla fine", None)
    if engine.busy():
        return busy
    if model not in engine.model_names():
        return 400, f"modello non disponibile: {model}", None
    refusal = engine.send_refusal(model)
    if refusal:
        return 409, refusal, None
    # una conversazione eliminata sul PC non va ricreata in silenzio con il
    # vecchio id (le chat nuove arrivano con chat_id nullo)
    if chat_id is not None and chat_id not in {c["id"] for c in engine.chats()}:
        return 404, "conversazione inesistente: è stata eliminata sul PC?", None
    seq = hub.seq()
    cid = engine.send(chat_id or engine.new_chat_id(), text, model, think=think,
                      attachments=list(attachments), web=web)
    if cid is None:
        return busy
    return 200, cid, seq


MAX_TITLE = 200
# nome di un modello Ollama: «llama3.2:3b», «hf.co/utente/repo:Q4_K_M»…
_MODEL_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")


def web_model_op(engine, op: str, name: str = "") -> tuple[int, str | None]:
    """Thread principale: scarica, elimina o annulla dal telefono.

    Restituisce (stato HTTP, messaggio d'errore o None).
    """
    if op == "cancel":
        return (200, None) if engine.cancel_model_task() else (409, "nessuno scaricamento in corso")
    err = engine.pull_model(name) if op == "pull" else engine.delete_model(name)
    if err is None:
        return 200, None
    return (404 if err == "modello non installato" else 409), err


def web_rename(engine, chat_id: str, title: str) -> bool:
    """Thread principale: rinomina dal telefono; False se la chat non esiste."""
    if chat_id not in {c["id"] for c in engine.chats()}:
        return False
    engine.rename_chat(chat_id, title)
    return True


def web_delete(engine, chat_id: str) -> bool:
    """Thread principale: elimina dal telefono (ferma la sua risposta, se c'è)."""
    if chat_id not in {c["id"] for c in engine.chats()}:
        return False
    engine.delete_chat(chat_id)
    return True


def web_stop(engine, chat_id: str) -> bool:
    """Thread principale: il telefono ferma solo la conversazione che guarda."""
    if engine.busy() and engine.active_chat_id() == chat_id:
        engine.stop()
        return True
    return False


def render_message(m: dict, sources: list[dict] | None = None) -> dict | None:
    """Messaggio per il browser, con il Markdown già reso.

    Escono solo i campi mostrati: niente percorsi degli allegati, niente
    risultati web completi né immagini. Di una ricerca web escono solo le
    fonti (titolo, link http/https, dominio) sotto la risposta.
    """
    role = m.get("role")
    if role not in ("user", "assistant"):
        return None
    text = m.get("display", m.get("content", "")) if role == "user" else m.get("content", "")
    out = {
        "role": role,
        "html": md_to_html(text, *_MD_COLORS),
        "text": text,           # sorgente Markdown, per il pulsante «copia»
        "ts": m.get("ts", 0),
    }
    if role == "user":
        out["attachments"] = [str(a) for a in m.get("attachments") or []]
        out["web"] = bool(m.get("web"))
    else:
        if m.get("thinking"):
            out["thinking_html"] = md_to_html(m["thinking"], *_MD_COLORS)
        if m.get("stats"):
            out["stats"] = m["stats"]
        if sources:
            out["sources"] = sources
    return out


def render_chat(chat: dict) -> dict:
    """Thread HTTP: conversazione per il browser."""
    raw = chat.get("messages", [])
    msgs = [r for r in (render_message(m, web_search.answer_sources(raw, i))
                        for i, m in enumerate(raw)) if r is not None]
    out = {
        "id": chat.get("id"), "title": chat.get("title", ""),
        "model": chat.get("model", ""), "updated": chat.get("updated", 0),
        "messages": msgs,
    }
    for k in ("seq", "state"):
        if k in chat:
            out[k] = chat[k]
    return out


# --------------------------------------------------------------- eventi

class EventHub(QObject):
    """Raccoglie gli eventi del motore (thread principale) per gli stream SSE.

    Ogni evento ha un numero progressivo. Il backlog conserva gli eventi
    dell'elaborazione in corso (o dell'ultima): un telefono che si collega
    a metà risposta ricostruisce il testo già generato.
    """

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._seq = 0
        self._backlog: list[tuple] = []
        self._subs: set[queue.Queue] = set()
        self._engine = engine
        self._busy = engine.busy()
        self._active = engine.active_chat_id() or ""
        e = engine
        e.busy_changed.connect(self._on_busy)
        e.user_message_added.connect(
            lambda cid, msg: self._push(cid, "user", copy.deepcopy(msg)))
        e.search_started.connect(lambda cid: self._push(cid, "search", {}))
        e.search_finished.connect(lambda cid: self._push(cid, "search_done", {}))
        e.generation_started.connect(lambda cid: self._push(cid, "start", {}))
        e.text_chunk.connect(lambda cid, t: self._push(cid, "text", t))
        e.think_chunk.connect(lambda cid, t: self._push(cid, "think", t))
        e.generation_finished.connect(self._on_finished)
        e.notice.connect(lambda cid, text: self._push(cid, "notice", {"text": text}))
        e.chats_changed.connect(lambda: self._push("", "chats", {}))

    def _on_finished(self, chat_id: str, outcome: str, stats: str, err: str) -> None:
        # arriva prima del salvataggio della risposta: l'ultimo messaggio è
        # ancora quello dell'utente, con i risultati della ricerca web
        chat = self._engine.chat(chat_id)
        msgs = chat["messages"] if chat else []
        self._push(chat_id, "done", {
            "outcome": outcome, "stats": stats, "error": err, "ts": config.now(),
            "sources": web_search.answer_sources(msgs, len(msgs)),
        })

    def _on_busy(self, busy: bool) -> None:
        # con busy=True il motore ha già fissato la conversazione attiva
        active = (self._engine.active_chat_id() or "") if busy else ""
        with self._lock:
            self._busy = busy
            self._active = active
            if busy:
                self._backlog = []   # nuova elaborazione: il backlog riparte
        self._push("", "busy", {"busy": busy, "chat_id": active})

    def _push(self, chat_id: str, kind: str, data) -> None:
        with self._lock:
            self._seq += 1
            ev = (self._seq, chat_id, kind, data)
            self._backlog.append(ev)
            for q in self._subs:
                q.put(ev)

    def seq(self) -> int:
        with self._lock:
            return self._seq

    def subscribe(self) -> tuple[queue.Queue, list[tuple], dict]:
        """Coda degli eventi futuri, backlog attuale e stato occupato."""
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.add(q)
            return q, list(self._backlog), {"busy": self._busy, "chat_id": self._active}

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def close_all(self) -> None:
        """Chiude tutti gli stream (arresto del server)."""
        with self._lock:
            for q in self._subs:
                q.put(None)
            self._subs = set()


class _StreamState:
    """Risposta in costruzione vista da uno stream SSE."""

    def __init__(self) -> None:
        self.active = False
        self.text = ""
        self.think = ""
        self.dirty = False
        self.last_sent = 0.0


def host_allowed(host_header: str | None) -> bool:
    """Difesa dal DNS rebinding: accetta solo IP, localhost e nomi locali.

    Una pagina ostile che fa risolvere il proprio dominio verso il PC manda
    il suo nome nell'intestazione Host e viene respinta.
    """
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith("["):              # [ipv6]:porta
        host = host[1:host.find("]")] if "]" in host else ""
    elif host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    # solo i nomi di questa macchina (anche in mDNS), non qualunque *.local
    names = {n.lower() for n in (socket.gethostname(), socket.getfqdn()) if n}
    local = {"localhost"} | names | {n.split(".")[0] + ".local" for n in names}
    return host in local


# --------------------------------------------------------------- handler HTTP

class _Handler(BaseHTTPRequestHandler):
    server_version = "OllaDesk"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    # timeout del socket: un client lento non tiene occupato un thread per
    # sempre (lettura della richiesta, connessioni keep-alive inattive)
    timeout = 10
    ctx: "CompanionServer"   # impostato dalla sottoclasse creata in start()

    def log_message(self, *_a) -> None:
        pass   # niente log su stderr a ogni richiesta

    # ------------------------------------------------------------ risposte

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data, extra: dict | None = None) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", extra)

    def _error(self, status: int, msg: str) -> None:
        self._json(status, {"error": msg})

    def _token(self) -> str | None:
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                return value
        return None

    def _authed(self) -> bool:
        if self.ctx.auth.check(self._token()):
            return True
        self._error(401, "dispositivo non abbinato")
        return False

    def _main(self, fn, *args):
        """Chiamata nel thread principale: (False, None) con l'errore già inviato."""
        try:
            return True, self.ctx.bridge.call(fn, *args)
        except queue.Empty:
            self._error(503, "OllaDesk non risponde")
        except Exception:
            self._error(500, "errore interno di OllaDesk")
        return False, None

    # -------------------------------------------------------------- metodi

    def _precheck(self) -> bool:
        if not host_allowed(self.headers.get("Host")):
            self.close_connection = True   # eventuale corpo non letto
            self._error(421, "host non ammesso")
            return False
        return True

    def do_HEAD(self) -> None:  # noqa: N802 (API http.server)
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        if not self._precheck():
            return
        path = self.path.split("?", 1)[0]
        if path in STATIC:
            name, ctype = STATIC[path]
            try:
                body = (WEB_DIR / name).read_bytes()
            except OSError:
                self._error(500, "file della pagina mancante")
                return
            self._send(200, body, ctype)
            return
        if path == "/api/session":
            if self._authed():
                self._json(200, {"ok": True, "version": __version__})
            return
        if path == "/api/chats":
            if not self._authed():
                return
            ok, data = self._main(chat_index, self.ctx.engine, self.ctx.hub)
            if ok:
                self._json(200, data)
            return
        if path == "/api/events":
            if self._authed():
                self._stream(None)   # solo eventi generali: elenco e occupato
            return
        if path == "/api/models":
            if not self._authed():
                return
            ok, info = self._main(models_info, self.ctx.engine)
            if ok:
                self._json(200, info)
            return
        m = _EVENTS_URL_RE.match(path)
        if m:
            if self._authed():
                self._stream(m.group(1))
            return
        m = _CHAT_URL_RE.match(path)
        if m:
            if not self._authed():
                return
            ok, chat = self._main(chat_snapshot, self.ctx.engine, self.ctx.hub, m.group(1))
            if not ok:
                return
            if chat is None:
                self._error(404, "conversazione inesistente")
                return
            self._json(200, render_chat(chat))
            return
        self._error(404, "risorsa inesistente")

    def do_POST(self) -> None:  # noqa: N802
        if not self._precheck():
            return
        path = self.path.split("?", 1)[0]
        if path == "/api/upload":
            self._upload()
            return
        # solo JSON: un modulo HTML di un altro sito non può inviarlo, e una
        # fetch da un'altra origine richiede un preflight CORS che qui non c'è
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            # il corpo resta non letto: la connessione non va riusata
            self.close_connection = True
            self._error(415, "serve Content-Type: application/json")
            return
        # corpo chunked o senza lunghezza: resterebbe non letto
        if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length") is None:
            self.close_connection = True
            self._error(411, "serve Content-Length")
            return
        try:
            length = int(self.headers["Content-Length"])
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self.close_connection = True
            self._error(413, "richiesta troppo grande")
            return
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._error(400, "JSON non valido")
            return
        if not isinstance(data, dict):
            self._error(400, "JSON non valido")
            return

        if path == "/api/pair":
            token = self.ctx.auth.pair(str(data.get("code", "")).strip())
            if token is None:
                self._error(403, "codice errato o scaduto: generane uno nuovo sul PC")
                return
            self.ctx.paired.emit()
            cookie = (f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; "
                      f"Max-Age={COOKIE_MAX_AGE}")
            self._json(200, {"ok": True}, {"Set-Cookie": cookie})
            return
        if path == "/api/send":
            if not self._authed():
                return
            text, model = data.get("text"), data.get("model")
            chat_id = data.get("chat_id")
            # booleani veri: bool("false") varrebbe True, e con la ricerca web
            # la domanda andrebbe a un servizio esterno che l'utente non ha chiesto
            think, web = data.get("think", True), data.get("web", False)
            if not (isinstance(think, bool) and isinstance(web, bool)):
                self._error(400, "«think» e «web» devono essere true o false")
                return
            ids = data.get("attachments") or []
            if not (isinstance(ids, list) and len(ids) <= MAX_ATTACHMENTS
                    and all(isinstance(i, str) for i in ids)):
                self._error(400, f"al massimo {MAX_ATTACHMENTS} allegati per messaggio")
                return
            if len(set(ids)) != len(ids):
                self._error(400, "lo stesso allegato compare più volte")
                return
            # come sul PC: si può inviare anche un messaggio fatto di soli allegati
            if not isinstance(text, str) or len(text) > MAX_TEXT or not (text.strip() or ids):
                self._error(400, f"messaggio vuoto o più lungo di {MAX_TEXT} caratteri")
                return
            if not isinstance(model, str) or not model:
                self._error(400, "modello mancante")
                return
            if chat_id is not None and not (
                isinstance(chat_id, str) and re.fullmatch(_CHAT_ID, chat_id)
            ):
                self._error(400, "conversazione non valida")
                return
            atts = self.ctx.uploads.take(ids)
            if atts is None:
                self._error(400, "allegato sconosciuto o scaduto: caricalo di nuovo")
                return
            ok, res = self._main(web_send, self.ctx.engine, self.ctx.hub, chat_id,
                                 text.strip(), model, think, atts, web)
            if not ok or res[0] != 200:
                self.ctx.uploads.give_back(atts)
            if not ok:
                return
            status, value, seq = res
            if status != 200:
                self._error(status, value)
                return
            self._json(200, {"chat_id": value, "after": seq})
            return
        if path == "/api/stop":
            if not self._authed():
                return
            chat_id = data.get("chat_id")
            if not (isinstance(chat_id, str) and re.fullmatch(_CHAT_ID, chat_id)):
                self._error(400, "conversazione non valida")
                return
            ok, stopped = self._main(web_stop, self.ctx.engine, chat_id)
            if ok:
                self._json(200, {"stopped": stopped})
            return
        if path in ("/api/rename", "/api/delete"):
            if not self._authed():
                return
            chat_id = data.get("chat_id")
            if not (isinstance(chat_id, str) and re.fullmatch(_CHAT_ID, chat_id)):
                self._error(400, "conversazione non valida")
                return
            if path == "/api/rename":
                title = data.get("title")
                if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE:
                    self._error(400, f"titolo vuoto o più lungo di {MAX_TITLE} caratteri")
                    return
                ok, found = self._main(web_rename, self.ctx.engine, chat_id, title.strip())
            else:
                ok, found = self._main(web_delete, self.ctx.engine, chat_id)
            if not ok:
                return
            if not found:
                self._error(404, "conversazione inesistente")
                return
            self._json(200, {"ok": True})
            return
        if path in ("/api/models/pull", "/api/models/delete", "/api/models/cancel"):
            if not self._authed():
                return
            op = path.rsplit("/", 1)[1]
            name = data.get("name", "")
            if op != "cancel" and not (isinstance(name, str) and _MODEL_NAME_RE.fullmatch(name)):
                self._error(400, "nome del modello non valido")
                return
            ok, res = self._main(web_model_op, self.ctx.engine, op, name)
            if not ok:
                return
            status, err = res
            if err:
                self._error(status, err)
            else:
                self._json(200, {"ok": True})
            return
        self._error(404, "risorsa inesistente")

    # ---------------------------------------------------------- caricamenti

    def _upload(self) -> None:
        """POST /api/upload?name=<nome>: corpo binario, risposta {id, name, kind}.

        Content-Type application/octet-stream: non è un tipo «semplice» dei
        moduli HTML, quindi da un'altra origine serve un preflight che qui non
        c'è. Ogni rifiuto chiude la connessione: il corpo resta non letto.
        """
        self.close_connection = True
        if not self._authed():
            return
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/octet-stream":
            self._error(415, "serve Content-Type: application/octet-stream")
            return
        if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length") is None:
            self._error(411, "serve Content-Length")
            return
        try:
            length = int(self.headers["Content-Length"])
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_UPLOAD:
            self._error(413, f"file vuoto o più grande di {MAX_UPLOAD // (1024 * 1024)} MB")
            return
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        name = safe_filename(query.get("name", ["file"])[0])
        kind = context.classify(Path(name))
        if kind == "unknown":
            self._error(415, f"tipo di file non supportato: {name} (immagini, testo, PDF)")
            return
        try:
            meta = self.ctx.uploads.add(name, kind, self.rfile, length)
        except UploadRefused as e:
            status, message = e.args
            self._error(status, message)
            return
        except OSError:
            return   # telefono disconnesso a metà: niente da rispondere
        self.close_connection = False   # corpo letto per intero
        self._json(200, meta)

    # ---------------------------------------------------------- stream SSE

    def _sse(self, kind: str, data, seq: int | None = None) -> None:
        head = f"id: {seq}\n" if seq is not None else ""
        payload = json.dumps(data, ensure_ascii=False)
        self.wfile.write(f"{head}event: {kind}\ndata: {payload}\n\n".encode("utf-8"))

    def _flush_answer(self, st: _StreamState) -> None:
        """Rende il Markdown della risposta in costruzione (testo completo)."""
        data = {"html": md_to_html(st.text, *_MD_COLORS), "text": st.text}
        if st.think:
            data["thinking_html"] = md_to_html(st.think, *_MD_COLORS)
        self._sse("answer", data)
        st.dirty = False
        st.last_sent = time.monotonic()

    def _handle_event(self, ev: tuple, chat_id: str, after: int, st: _StreamState,
                      notices_after: int | None = None) -> None:
        seq, cid, kind, data = ev
        if cid not in ("", chat_id):
            return
        new = seq > after   # il client non l'ha ancora visto
        if kind == "text" or kind == "think":
            if st.active:
                if kind == "text":
                    st.text += data
                else:
                    st.think += data
                st.dirty = True
            return
        if kind == "start":
            st.active, st.text, st.think, st.dirty = True, "", "", False
        elif kind == "done":
            if new and st.active and st.dirty:
                self._flush_answer(st)
            st.active, st.text, st.think, st.dirty = False, "", "", False
        if not new:
            if kind == "notice" and notices_after is not None and seq > notices_after:
                # avviso del proprio invio, che la copia della chat non contiene:
                # senza id, per non riportare indietro il Last-Event-ID
                self._sse(kind, data)
            return
        if kind == "user":
            data = render_message(data)
        self._sse(kind, data, seq)

    def _stream(self, chat_id: str | None) -> None:
        """GET /api/chats/<id>/events: eventi della conversazione in SSE.

        Con `chat_id` None (GET /api/events) passano solo gli eventi
        generali: elenco cambiato e stato occupato.

        `after` (query o Last-Event-ID) è l'ultimo evento già noto al
        client: quelli successivi del backlog vengono rispediti.
        `notices_after` (solo al primo collegamento, senza Last-Event-ID)
        rispedisce anche gli avvisi successivi, che la copia della chat non
        contiene: il telefono che crea una chat la apre dopo l'invio, con
        `after` già oltre gli avvisi di quell'invio. Il testo
        arriva come HTML già reso, al massimo ogni ANSWER_INTERVAL secondi.
        """
        if self.command == "HEAD":
            # HEAD passa da do_GET: qui aprirebbe uno stream senza fine
            self._error(405, "lo stream eventi richiede GET")
            return
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        after = 0
        for raw in (query.get("after", ["0"])[0], self.headers.get("Last-Event-ID") or "0"):
            try:
                after = max(after, int(raw))
            except ValueError:
                pass
        notices_after = None
        if not self.headers.get("Last-Event-ID"):
            try:
                notices_after = int(query["notices_after"][0])
            except (KeyError, ValueError):
                pass
        hub = self.ctx.hub
        q, backlog, busy = hub.subscribe()
        self.close_connection = True
        st = _StreamState()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"retry: 3000\n\n")
            self._sse("busy", busy)
            for ev in backlog:
                self._handle_event(ev, chat_id, after, st, notices_after)
            if st.active and st.dirty:
                self._flush_answer(st)   # testo generato prima del collegamento
            self.wfile.flush()
            while True:
                wait = PING_INTERVAL
                if st.dirty:
                    wait = max(0.0, ANSWER_INTERVAL - (time.monotonic() - st.last_sent))
                try:
                    ev = q.get(timeout=wait)
                except queue.Empty:
                    if st.dirty:
                        self._flush_answer(st)
                    else:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if ev is None:      # server in arresto
                    break
                self._handle_event(ev, chat_id, 0, st)
                if st.dirty and time.monotonic() - st.last_sent >= ANSWER_INTERVAL:
                    self._flush_answer(st)
                self.wfile.flush()
        except OSError:
            pass   # telefono disconnesso
        finally:
            hub.unsubscribe(q)


# ------------------------------------------------------------------ server

class CompanionServer(QObject):
    """Server companion: avvio/arresto dal thread principale."""

    state_changed = Signal(str, str)   # "off" | "running" | "error", dettaglio
    paired = Signal()                  # un dispositivo si è appena abbinato

    def __init__(self, engine, parent=None, auth: PairingAuth | None = None):
        super().__init__(parent)
        self.engine = engine
        self.auth = auth or PairingAuth()
        self.bridge = _Bridge(self)
        self.hub = EventHub(engine, self)
        self.uploads = UploadStore()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._state = "off"
        self.port = 0

    def state(self) -> str:
        return self._state

    def _set_state(self, state: str, detail: str) -> None:
        self._state = state
        self.state_changed.emit(state, detail)

    def start(self, port: int, bind: str = "0.0.0.0") -> None:
        """Avvia (o riavvia su un'altra porta) il server."""
        if self._httpd is not None and self.port == port:
            return
        self.stop()
        handler = type("CompanionHandler", (_Handler,), {"ctx": self})
        try:
            httpd = ThreadingHTTPServer((bind, port), handler)
        except OSError as e:
            self._set_state("error", f"porta {port} non disponibile ({e.strerror or e})")
            return
        httpd.daemon_threads = True
        self._httpd = httpd
        self.port = httpd.server_address[1]
        self._thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.2},
            name="olladesk-companion", daemon=True,
        )
        self._thread.start()
        self._set_state("running", f"in ascolto sulla porta {self.port}")

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            self.hub.close_all()    # chiude gli stream SSE aperti
            self.uploads.clear()    # caricamenti mai inviati
            httpd.shutdown()        # attende l'uscita da serve_forever
            httpd.server_close()
            self._thread = None
            self.auth.cancel_code()
        if self._state != "off":
            self._set_state("off", "")

    def urls(self) -> list[str]:
        return netinfo.lan_urls(self.port) if self._httpd is not None else []

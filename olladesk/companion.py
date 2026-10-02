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
import queue
import re
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal

from . import __version__, config
from .md import md_to_html

CODE_TTL = 120            # secondi di validità del codice di abbinamento
MAX_ATTEMPTS = 5          # tentativi errati che invalidano il codice
MAX_DEVICES = 20          # token conservati (i più vecchi escono)
COOKIE = "olladesk_session"
COOKIE_MAX_AGE = 365 * 24 * 3600
MAX_BODY = 4096
BRIDGE_TIMEOUT = 5.0

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}

_CHAT_URL_RE = re.compile(r"^/api/chats/([A-Za-z0-9_-]{1,64})$")

# colori del Markdown reso: il foglio di stile della pagina li ridefinisce
# per il tema chiaro/scuro (le regole CSS battono gli attributi HTML)
_MD_COLORS = ("#1a1d21", "#e8eaed", "#79b8ff")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- abbinamento

class PairingAuth:
    """Codice di abbinamento e token dei dispositivi. Thread-safe."""

    def __init__(self, path: Path | None = None, clock=time.monotonic):
        self._path = path or (config.config_dir() / "companion.json")
        self._clock = clock
        self._lock = threading.Lock()
        self._code: str | None = None
        self._expires = 0.0
        self._failures = 0
        data = config._read_json(self._path, {})
        devices = data.get("devices") if isinstance(data, dict) else None
        self._devices: list[dict] = [
            d for d in (devices or [])
            if isinstance(d, dict) and isinstance(d.get("hash"), str)
        ]

    def _save(self) -> None:
        config._write_json(self._path, {"devices": self._devices})

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

def chat_index(engine) -> list[dict]:
    """Thread principale: elenco delle conversazioni, dalla più recente."""
    items = [
        {"id": c["id"], "title": c.get("title", ""), "model": c.get("model", ""),
         "updated": c.get("updated", 0)}
        for c in engine.chats()
    ]
    items.sort(key=lambda c: c["updated"], reverse=True)
    return items


def chat_snapshot(engine, chat_id: str) -> dict | None:
    """Thread principale: copia della conversazione, da rendere altrove."""
    c = engine.chat(chat_id)
    return copy.deepcopy(c) if c is not None else None


def render_chat(chat: dict) -> dict:
    """Thread HTTP: conversazione per il browser, con il Markdown già reso.

    Escono solo i campi mostrati: niente percorsi degli allegati, niente
    risultati web completi né immagini.
    """
    msgs = []
    for m in chat.get("messages", []):
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        text = m.get("display", m.get("content", "")) if role == "user" else m.get("content", "")
        out = {
            "role": role,
            "html": md_to_html(text, *_MD_COLORS),
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
        msgs.append(out)
    return {
        "id": chat.get("id"), "title": chat.get("title", ""),
        "model": chat.get("model", ""), "updated": chat.get("updated", 0),
        "messages": msgs,
    }


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
    local = {"localhost", socket.gethostname().lower()}
    return host in local or host.endswith(".local")


# --------------------------------------------------------------- handler HTTP

class _Handler(BaseHTTPRequestHandler):
    server_version = "OllaDesk"
    sys_version = ""
    protocol_version = "HTTP/1.1"
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
            ok, items = self._main(chat_index, self.ctx.engine)
            if ok:
                self._json(200, {"chats": items})
            return
        m = _CHAT_URL_RE.match(path)
        if m:
            if not self._authed():
                return
            ok, chat = self._main(chat_snapshot, self.ctx.engine, m.group(1))
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
        # solo JSON: un modulo HTML di un altro sito non può inviarlo, e una
        # fetch da un'altra origine richiede un preflight CORS che qui non c'è
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            # il corpo resta non letto: la connessione non va riusata
            self.close_connection = True
            self._error(415, "serve Content-Type: application/json")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
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
        self._error(404, "risorsa inesistente")


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
            httpd.shutdown()        # attende l'uscita da serve_forever
            httpd.server_close()
            self._thread = None
            self.auth.cancel_code()
        if self._state != "off":
            self._set_state("off", "")

    def urls(self) -> list[str]:
        from .server_share import lan_urls

        return lan_urls(self.port) if self._httpd is not None else []

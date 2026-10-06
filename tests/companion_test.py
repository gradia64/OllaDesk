"""Test della companion web: server HTTP su una porta casuale, senza rete.

Copre pagine statiche e intestazioni di sicurezza, abbinamento (codice
errato, scaduto, già usato, limite ai tentativi), cookie, revoca, lettura
delle chat con il Markdown reso dal server, difesa dal DNS rebinding,
risposta 503 col thread principale bloccato, dialogo di abbinamento con
QR code e pulsante 📱 della finestra.

Uso:  python3 tests/companion_test.py   (nessuna rete, nessun Ollama)
"""
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time
import types

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="olladesk_companion_config_")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from olladesk import companion, config
from olladesk.engine import ChatEngine

import traceback  # noqa: E402

slot_errors = []


def _hook(*exc):
    # eccezioni negli slot Qt (e nel test stesso): registrate e stampate
    slot_errors.append(exc)
    traceback.print_exception(*exc)


sys.excepthook = _hook

app = QApplication([])


def wait_until(pred, ms=5000):
    deadline = time.monotonic() + ms / 1000
    while not pred() and time.monotonic() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(10, loop.quit)
        loop.exec()
    return pred()


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


clock = Clock()

# conversazione con Markdown, pensiero, allegati e un tentativo di iniezione
CHAT = {
    "id": "abc123", "title": "Prova <b>", "model": "finto", "updated": 50,
    "messages": [
        {"role": "user", "display": "ciao <script>alert(1)</script>", "ts": 10,
         "attachments": ["nota.txt"], "web": True, "web_block": "SEGRETO-WEB",
         "attachments_meta": [{"path": "/home/segreto/nota.txt", "name": "nota.txt", "kind": "text"}],
         "image_paths": ["/home/segreto/foto.png"]},
        {"role": "assistant", "content": "**grassetto**\n```py\nx = '<i>'\n```", "ts": 11,
         "thinking": "ci *penso*", "stats": "12 tok/s"},
    ],
}
assert config.save_chat(CHAT)
config.save_chat({"id": "vecchia", "title": "Vecchia", "model": "m", "updated": 5, "messages": []})

engine = ChatEngine(config.load_settings())
auth = companion.PairingAuth(clock=clock)
srv = companion.CompanionServer(engine, auth=auth)
states = []
srv.state_changed.connect(lambda s, d: states.append((s, d)))
srv.start(0, "127.0.0.1")
assert srv.state() == "running" and srv.port, states
PORT = srv.port


def request(method, path, body=None, headers=None, cookie=None, spin=True):
    """Richiesta in un thread separato mentre il thread principale gira."""
    h = {"Host": f"127.0.0.1:{PORT}"}
    if body is not None:
        h["Content-Type"] = "application/json"
        body = json.dumps(body).encode()
    if cookie:
        h["Cookie"] = f"{companion.COOKIE}={cookie}"
    h.update(headers or {})
    out = {}

    def run():
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        out["status"], out["headers"], out["body"] = r.status, dict(r.getheaders()), r.read()
        c.close()

    t = threading.Thread(target=run)
    t.start()
    if spin:
        assert wait_until(lambda: not t.is_alive(), 15000), f"{method} {path} senza risposta"
    else:
        t.join(15)
    return out["status"], out["headers"], out["body"]


def jbody(b):
    return json.loads(b.decode())


# ------------------------------------------------- 1. pagine e intestazioni

for path, ctype in (("/", "text/html"), ("/app.js", "text/javascript"), ("/app.css", "text/css")):
    st, hd, body = request("GET", path)
    assert st == 200 and hd["Content-Type"].startswith(ctype), (path, st)
    assert "script-src 'self'" in hd["Content-Security-Policy"]
    assert hd["X-Frame-Options"] == "DENY" and hd["X-Content-Type-Options"] == "nosniff"
    for ref in (b'src="http', b'href="http', b"url(http", b'fetch("http', b"@import"):
        assert ref not in body, f"{path}: risorsa esterna ({ref!r})"
# PWA: manifest con icone locali, tutte servite come PNG vere
st, hd, body = request("GET", "/manifest.webmanifest")
assert st == 200 and hd["Content-Type"] == "application/manifest+json"
manifest = json.loads(body)
assert manifest["display"] == "standalone" and manifest["start_url"] == "/"
assert any(i.get("purpose") == "maskable" for i in manifest["icons"])
for icon in [i["src"] for i in manifest["icons"]] + ["apple-touch-icon.png", "favicon.ico"]:
    st, hd, body = request("GET", "/" + icon)
    assert st == 200 and hd["Content-Type"] == "image/png", icon
    assert body.startswith(b"\x89PNG\r\n\x1a\n"), f"{icon}: non è una PNG"
st, _h, body = request("GET", "/")
for ref in (b'href="manifest.webmanifest"', b'href="apple-touch-icon.png"', b'name="theme-color"'):
    assert ref in body, ref
assert request("GET", "/nulla")[0] == 404
assert request("GET", "/../companion.py")[0] == 404
print("1. pagine statiche, CSP, PWA e nessuna risorsa esterna OK")

# -------------------------------------------------------- 2. DNS rebinding

assert request("GET", "/", headers={"Host": f"evil.example:{PORT}"})[0] == 421
assert request("GET", "/api/session", headers={"Host": "attacker.com"})[0] == 421
HOSTNAME = socket.gethostname().split(".")[0]
assert request("GET", "/", headers={"Host": f"{HOSTNAME}.local:{PORT}"})[0] == 200
assert request("GET", "/", headers={"Host": f"{HOSTNAME}:{PORT}"})[0] == 200
# un nome mDNS qualunque non basta: solo quelli di questa macchina
assert request("GET", "/", headers={"Host": f"altro-pc-{HOSTNAME}.local:{PORT}"})[0] == 421
assert request("GET", "/", headers={"Host": f"[::1]:{PORT}"})[0] == 200
assert request("GET", "/", headers={"Host": f"localhost:{PORT}"})[0] == 200
print("2. Host ammessi solo IP, localhost e nomi di questa macchina OK")

# ------------------------------------------------- 3. senza abbinamento

for path in ("/api/session", "/api/chats", "/api/chats/abc123"):
    assert request("GET", path)[0] == 401, path
assert request("GET", "/api/chats", cookie="inventato")[0] == 401
assert request("POST", "/api/pair", {"code": "000000"})[0] == 403   # nessun codice attivo
print("3. API chiuse senza cookie valido OK")

# ---------------------------------------------------------- 4. abbinamento

code, ttl = auth.new_code()
assert len(code) == 6 and code.isdigit() and ttl == 120
wrong = f"{(int(code) + 1) % 10**6:06d}"
assert request("POST", "/api/pair", {"code": wrong})[0] == 403
st, _h, _b = request("POST", "/api/pair", headers={"Content-Type": "text/plain"},
                     body=None)
assert st == 415
# errore con il corpo non letto: la connessione keep-alive va chiusa,
# altrimenti il corpo sarebbe letto come la richiesta successiva
raw_out = {}


def raw():
    s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
    s.sendall(b"POST /api/pair HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: text/plain\r\n"
              b"Content-Length: 30\r\n\r\nGET /api/session HTTP/1.1\r\n\r\n")
    data = b""
    while chunk := s.recv(4096):
        data += chunk
    raw_out["data"] = data
    s.close()


t = threading.Thread(target=raw)
t.start()
assert wait_until(lambda: not t.is_alive()), "la connessione non è stata chiusa"
assert raw_out["data"].count(b"HTTP/1.1 ") == 1 and b" 415 " in raw_out["data"]


def raw_request(payload: bytes) -> bytes:
    """Richiesta grezza in un thread; restituisce tutto fino alla chiusura."""
    out = {}

    def run():
        s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
        s.sendall(payload)
        data = b""
        while chunk := s.recv(4096):
            data += chunk
        out["data"] = data
        s.close()

    th = threading.Thread(target=run)
    th.start()
    assert wait_until(lambda: not th.is_alive()), "connessione non chiusa"
    return out["data"]


# corpo senza lunghezza (chunked o assente): 411 e connessione chiusa
for extra in (b"Transfer-Encoding: chunked\r\n", b""):
    data = raw_request(b"POST /api/pair HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                       b"Content-Type: application/json\r\n" + extra + b"\r\n"
                       b"0\r\n\r\nGET /api/session HTTP/1.1\r\n\r\n")
    assert data.count(b"HTTP/1.1 ") == 1 and b" 411 " in data, data[:80]

st, hd, body = request("POST", "/api/pair", {"code": code})
assert st == 200, body
cookie = hd["Set-Cookie"]
assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
token = cookie.split(";")[0].split("=", 1)[1]
assert len(token) >= 40 and token.encode() not in body
assert request("POST", "/api/pair", {"code": code})[0] == 403, "il codice deve essere monouso"
stored = open(config.config_dir() / "companion.json").read()
assert token not in stored and companion._hash(token) in stored
assert auth.device_count() == 1
print("4. abbinamento, cookie HttpOnly/SameSite e codice monouso OK")

# ----------------------------------------------------- 5. scadenza e tentativi

code, _ = auth.new_code()
clock.t += 121
assert request("POST", "/api/pair", {"code": code})[0] == 403, "codice scaduto accettato"

code, _ = auth.new_code()
for _ in range(companion.MAX_ATTEMPTS):
    assert request("POST", "/api/pair", {"code": wrong if wrong != code else "999999"})[0] == 403
assert request("POST", "/api/pair", {"code": code})[0] == 403, "limite ai tentativi ignorato"
assert auth.code_remaining() == 0
print("5. codice scaduto e limite ai tentativi OK")

# ------------------------------------------------------- 6. lettura delle chat

st, _h, body = request("GET", "/api/session", cookie=token)
assert st == 200 and jbody(body)["ok"]
st, _h, body = request("GET", "/api/chats", cookie=token)
assert st == 200
assert [c["id"] for c in jbody(body)["chats"]] == ["abc123", "vecchia"]
st, _h, body = request("GET", "/api/chats/abc123", cookie=token)
assert st == 200
chat = jbody(body)
user, bot = chat["messages"]
assert "<script>" not in user["html"] and "&lt;script&gt;" in user["html"]
assert user["attachments"] == ["nota.txt"] and user["web"] is True
assert "<b>grassetto</b>" in bot["html"] and "&lt;i&gt;" in bot["html"]
assert "<i>penso</i>" in bot["thinking_html"] and bot["stats"] == "12 tok/s"
for secret in (b"/home/segreto", b"SEGRETO-WEB", b"image_paths", b"attachments_meta"):
    assert secret not in body, f"dato interno esposto: {secret!r}"
assert request("GET", "/api/chats/inesistente", cookie=token)[0] == 404
assert request("GET", "/api/chats/a%2F..%2Fb", cookie=token)[0] == 404
print("6. elenco e chat con Markdown reso dal server, senza dati interni OK")

# --------------------------------------------- 7. thread principale bloccato

companion.BRIDGE_TIMEOUT = 0.3
st, _h, _b = request("GET", "/api/chats", cookie=token, spin=False)
assert st == 503, st
companion.BRIDGE_TIMEOUT = 5.0
wait_until(lambda: False, 100)   # smaltisce il lavoro rimasto in coda
print("7. 503 con il thread principale bloccato OK")

# ------------------------------------------------------------- 8. revoca

auth.revoke_all()
assert request("GET", "/api/chats", cookie=token)[0] == 401
assert auth.device_count() == 0
# il file è stato riscritto: un nuovo PairingAuth non ritrova il token
assert not companion.PairingAuth().check(token)
print("8. revoca dei dispositivi OK")

# ----------------------------------------------------- 9. porta occupata, stop

busy = socket.socket()
busy.bind(("127.0.0.1", 0))
busy.listen(1)
other = companion.CompanionServer(engine, auth=auth)
other_states = []
other.state_changed.connect(lambda s, d: other_states.append((s, d)))
other.start(busy.getsockname()[1], "127.0.0.1")
assert other.state() == "error" and "non disponibile" in other_states[-1][1], other_states
busy.close()

srv.stop()
assert srv.state() == "off"
try:
    socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
    raise AssertionError("il server risponde ancora dopo lo stop")
except OSError:
    pass
print("9. porta occupata ed arresto OK")

# --------------------------------- 10. finestra della companion: abbinamento e QR

fake_qr = types.ModuleType("qrcode")
fake_qr.constants = types.SimpleNamespace(ERROR_CORRECT_M=0)


class FakeQR:
    def __init__(self, **_kw):
        self.data = ""

    def add_data(self, d):
        self.data = d

    def make(self, fit=True):
        pass

    def get_matrix(self):
        return [[(x + y) % 2 == 0 for x in range(25)] for y in range(25)]


fake_qr.QRCode = FakeQR
sys.modules["qrcode"] = fake_qr

from olladesk.widgets.companion_dialog import CompanionDialog, qr_matrix  # noqa: E402

# servizio spento: niente codice, abbinamento disattivato
srv.urls = lambda: ["http://192.168.1.50:%d" % srv.port]
off = CompanionDialog(srv, {"companion": False})
assert srv.state() == "off" and not off.pair_box.isEnabled()
assert auth.code_remaining() == 0 and "Spenta" in off.state_label.text()
off.reject()

srv.start(0, "127.0.0.1")
dlg = CompanionDialog(srv, {"companion": True, "companion_port": srv.port})
assert dlg.pair_box.isEnabled() and f"porta {srv.port}" in dlg.state_label.text()
assert "192.168.1.50" in dlg.urls_label.text() and dlg.copy_btn.isVisibleTo(dlg)
code_text = dlg.code_label.text().replace(" ", "")
assert len(code_text) == 6 and auth.code_remaining() > 0
assert dlg.qr.isVisibleTo(dlg) and len(dlg.qr._matrix) == 25
assert dlg.qr.sizeHint().width() >= 200
img = dlg.qr.grab().toImage()
assert img.pixelColor(0, 0).name() == "#000000" and img.pixelColor(img.width() - 1, 0).name() == "#000000"
assert "valido ancora" in dlg.status_label.text() and "QR" in dlg.status_label.text()
PORT = srv.port
st, _h, _b = request("POST", "/api/pair", {"code": code_text})
assert st == 200
assert wait_until(lambda: "abbinato" in dlg.status_label.text())
assert "abbinati: 1." in dlg.devices_label.text() and not dlg.new_code_btn.isHidden()
assert not dlg.qr.isVisibleTo(dlg), "il QR deve sparire dopo l'abbinamento"
dlg._new_code()
dlg.reject()
assert auth.code_remaining() == 0, "il codice deve scadere alla chiusura del dialogo"
del sys.modules["qrcode"]
try:
    import qrcode  # noqa: F401
except ImportError:
    assert qr_matrix("x") is None, "senza python3-qrcode niente QR"
srv.stop()
print("10. finestra della companion: abbinamento con QR code OK")

# --------------------------------------------------- 11. finestra principale

s = config.load_settings()
probe = socket.socket()
probe.bind(("127.0.0.1", 0))
free_port = probe.getsockname()[1]
probe.close()
s.update(companion=True, companion_port=free_port, app_update_check=False,
         host="http://127.0.0.1:9", tray_icon=False)
config.save_settings(s)

from olladesk.main_window import MainWindow  # noqa: E402


def auth_code_active(server) -> bool:
    return server.auth.code_remaining() > 0


win = MainWindow()
win._sync_companion()
assert win._companion.state() == "running" and not win.companion_btn.isHidden()
win.settings["companion"] = False
win._sync_companion()
assert win._companion.state() == "off" and win.companion_btn.isHidden()

# la finestra della companion (barra laterale, Ctrl+D) accende, cambia porta
# e spegne il servizio, e salva la scelta nelle impostazioni
assert win.sidebar.companion_btn.text().endswith("Companion")
from olladesk.widgets.companion_dialog import CompanionDialog  # noqa: E402

box = CompanionDialog(win._companion, win.settings, win._apply_companion)
box.enable_chk.setChecked(True)
assert win._companion.state() == "running" and win._companion.port == free_port
assert config.load_settings()["companion"] is True
assert box.pair_box.isEnabled() and auth_code_active(win._companion)
probe = socket.socket()
probe.bind(("127.0.0.1", 0))
other_port = probe.getsockname()[1]
probe.close()
box.port_spin.setValue(other_port)
assert box.port_btn.isVisibleTo(box)
box._apply_port()
assert win._companion.state() == "running" and win._companion.port == other_port
assert config.load_settings()["companion_port"] == other_port
# porta occupata: errore mostrato nella finestra
busy_sock = socket.socket()
busy_sock.bind(("0.0.0.0", 0))
busy_sock.listen(1)
box.port_spin.setValue(busy_sock.getsockname()[1])
box._apply_port()
assert win._companion.state() == "error" and "Non avviata" in box.state_label.text()
assert not box.pair_box.isEnabled()
busy_sock.close()
box.enable_chk.setChecked(False)
assert win._companion.state() == "off" and config.load_settings()["companion"] is False
box.reject()
# le impostazioni non hanno più la sezione della companion
from olladesk.widgets.settings_dialog import SettingsDialog  # noqa: E402

sd = SettingsDialog(win.settings, lambda: [], "dark", "?", win)
assert not hasattr(sd, "companion_chk")
# i valori della companion passano intatti da un salvataggio delle impostazioni
assert sd.collect_settings()["companion"] is False
sd.deleteLater()
win._really_quit = True
win.close()
wait_until(lambda: False, 100)

assert not slot_errors, f"eccezioni negli slot: {slot_errors}"
print("COMPANION OK")

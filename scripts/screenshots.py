#!/usr/bin/env python3
"""Screenshot per i metadati AppStream e il README: docs/screenshots/*.png.

Uso:  python3 scripts/screenshots.py

Tutto è isolato: configurazione in una cartella temporanea con chat di
esempio, un finto Ollama locale con nomi di modelli realistici, finestre
disegnate offscreen (niente finestre sullo schermo né icona nella tray).
La companion ascolta solo su 127.0.0.1 e su una porta libera, mai sulla
8765 dell'app installata. La vista del telefono si cattura con Firefox
headless pilotato via Marionette, in un profilo temporaneo.
"""
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="olladesk_shots_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "screenshots")
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea, QToolButton  # noqa: E402

from olladesk import config, web_search  # noqa: E402

GB = 1024 ** 3
MODELS = [
    ("qwen3:8b", 5.2 * GB, "8.2B", "Q4_K_M"),
    ("gemma3:12b", 8.1 * GB, "12.2B", "Q4_K_M"),
    ("deepseek-r1:14b", 9.0 * GB, "14.8B", "Q4_K_M"),
    ("llama3.2:3b", 2.0 * GB, "3.2B", "Q4_K_M"),
    ("nomic-embed-text:latest", 0.27 * GB, "137M", "F16"),
]


# ------------------------------------------------------------ finto Ollama

class _Ollama(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_GET(self):  # noqa: N802 (API http.server)
        if self.path == "/api/version":
            body = {"version": "0.40.2"}
        elif self.path == "/api/tags":
            body = {"models": [
                {"name": n, "model": n, "size": int(s), "modified_at": "2026-10-01T10:00:00Z",
                 "details": {"parameter_size": p, "quantization_level": q}}
                for n, s, p, q in MODELS]}
        else:
            self.send_error(404)
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------- chat di esempio

CODE_ANSWER = """Ecco uno script che legge la data di scatto dai metadati EXIF e \
rinomina le foto nel formato `AAAA-MM-GG_HHMMSS.jpg`:

```python
from datetime import datetime
from pathlib import Path

from PIL import Image

EXIF_DATA_SCATTO = 36867


def data_scatto(foto: Path) -> datetime | None:
    with Image.open(foto) as img:
        valore = img.getexif().get_ifd(0x8769).get(EXIF_DATA_SCATTO)
    return datetime.strptime(valore, "%Y:%m:%d %H:%M:%S") if valore else None


for foto in sorted(Path("Foto").glob("*.jpg")):
    quando = data_scatto(foto)
    if quando:
        foto.rename(foto.with_name(quando.strftime("%Y-%m-%d_%H%M%S") + ".jpg"))
```

**Come funziona**

1. `getexif()` legge i metadati, e il tag `36867` è la data di scatto originale.
2. Le foto senza EXIF (screenshot, immagini scaricate) restano con il loro nome.

Serve Pillow: `pip install pillow`."""

WEB_ANSWER = """Le novità principali di **Python 3.14** sono:

- **Template string (PEP 750)**: il prefisso `t"..."` crea un oggetto \
`Template`, utile per l'escape sicuro di HTML e SQL [1].
- **Annotazioni valutate in ritardo (PEP 649)**: i riferimenti in avanti \
funzionano senza virgolette [1].
- **Interprete senza GIL**: la build *free-threaded* è supportata \
ufficialmente [2].
- **Modulo `concurrent.interpreters`** (PEP 734): più interpreti nella \
libreria standard [3]."""

WEB_RESULTS = [
    ("What's new in Python 3.14 — Python documentation",
     "https://docs.python.org/3/whatsnew/3.14.html",
     "This article explains the new features in Python 3.14, compared to 3.13."),
    ("Python 3.14 release",
     "https://www.python.org/downloads/release/python-3140/",
     "Python 3.14 is the newest major release of the Python programming language."),
    ("PEP 734 – Multiple Interpreters in the Stdlib",
     "https://peps.python.org/pep-0734/",
     "This PEP proposes to add a new module, concurrent.interpreters."),
]


def write_chats() -> tuple[str, str]:
    t = time.time()
    chats = [
        ("Traduci l'email al fornitore", "gemma3:12b", 9 * 86400),
        ("Spiegami i decoratori in Python", "qwen3:8b", 6 * 86400),
        ("Riassunto del contratto d'affitto", "gemma3:12b", 3 * 86400),
        ("Ricetta della pizza in teglia", "llama3.2:3b", 2 * 86400),
    ]
    for title, model, age in chats:
        config.save_chat({"id": config.new_chat_id(), "title": title, "model": model,
                          "updated": t - age, "messages": [
                              {"role": "user", "display": title, "ts": t - age},
                              {"role": "assistant", "content": "…", "ts": t - age + 20}]})
    web_id = config.new_chat_id()
    q = "Quali sono le novità di Python 3.14?"
    config.save_chat({"id": web_id, "title": "Novità di Python 3.14", "model": "qwen3:8b",
                      "updated": t - 3600, "messages": [
                          {"role": "user", "display": q, "ts": t - 3700, "web": True,
                           "web_block": web_search.format_results(q, WEB_RESULTS)},
                          {"role": "assistant", "content": WEB_ANSWER, "ts": t - 3600,
                           "thinking": "L'utente chiede le novità di Python 3.14…",
                           "stats": "312 token · 41.7 tok/s · 7.5 s"}]})
    code_id = config.new_chat_id()
    q = "Scrivi uno script Python che rinomina le foto di una cartella con la data di scatto"
    config.save_chat({"id": code_id, "title": "Rinominare le foto per data", "model": "qwen3:8b",
                      "updated": t - 60, "messages": [
                          {"role": "user", "display": q, "ts": t - 120},
                          {"role": "assistant", "content": CODE_ANSWER, "ts": t - 60,
                           "thinking": "Serve leggere il tag EXIF DateTimeOriginal…",
                           "stats": "486 token · 43.2 tok/s · 11.2 s"}]})
    return code_id, web_id


# -------------------------------------------------------- Firefox headless

class Marionette:
    """Client minimo del protocollo Marionette (lunghezza:json su TCP)."""

    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=30)
        self.n = 0
        self._read()   # saluto del server

    def _read(self):
        buf = b""
        while b":" not in buf:
            buf += self.sock.recv(1)
        size, rest = buf.split(b":", 1)
        while len(rest) < int(size):
            rest += self.sock.recv(int(size) - len(rest))
        return json.loads(rest)

    def cmd(self, name: str, params: dict | None = None):
        self.n += 1
        msg = json.dumps([0, self.n, name, params or {}]).encode()
        self.sock.sendall(str(len(msg)).encode() + b":" + msg)
        _t, _id, err, result = self._read()
        if err:
            raise RuntimeError(f"{name}: {err}")
        return result


def phone_shot(url: str, token: str, cookie: str, path: str) -> None:
    profile = tempfile.mkdtemp(prefix="olladesk_ff_")
    port = free_port()
    with open(os.path.join(profile, "user.js"), "w") as f:
        f.write(f'user_pref("marionette.port", {port});\n'
                'user_pref("layout.css.devPixelsPerPx", "2.0");\n'
                'user_pref("ui.systemUsesDarkTheme", 1);\n'
                'user_pref("browser.shell.checkDefaultBrowser", false);\n')
    ff = subprocess.Popen(["firefox", "--headless", "--marionette", "--no-remote",
                           "--profile", profile],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                m = Marionette(port)
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("Marionette non risponde")
        m.cmd("WebDriver:NewSession", {"capabilities": {}})
        # Firefox non scende sotto i 500 px CSS di larghezza: lo zoom CSS
        # sotto riporta la pagina ai 390 px di un telefono
        m.cmd("WebDriver:SetWindowRect", {"width": 500, "height": 1082})
        # il cookie si aggiunge da una pagina dello stesso host, ma non da
        # quella della companion: senza cookie mostrerebbe l'abbinamento, e
        # ricaricarla cambiando solo il frammento non la riavvierebbe
        m.cmd("WebDriver:Navigate", {"url": url.split("#")[0] + "api/session"})
        m.cmd("WebDriver:AddCookie", {"cookie": {"name": cookie, "value": token,
                                                 "path": "/", "httpOnly": True}})
        m.cmd("WebDriver:Navigate", {"url": url})
        # la vista si riempie via fetch dopo il caricamento
        ready = m.cmd("WebDriver:ExecuteAsyncScript", {"script": """
            const done = arguments[0];
            const t0 = Date.now();
            (function poll() {
              const item = document.querySelector('#view-chat:not([hidden]) #messages > *');
              if (item || Date.now() - t0 > 8000) done(!!item); else setTimeout(poll, 100);
            })();"""})
        if not ready.get("value"):
            state = m.cmd("WebDriver:ExecuteScript", {"script":
                "return location.href + ' | ' + document.body.innerText.slice(0, 300)"})
            print("cookie:", m.cmd("WebDriver:GetCookies"), file=sys.stderr)
            print("session:", m.cmd("WebDriver:ExecuteAsyncScript", {"script":
                "fetch('/api/session').then(r => r.text().then(t => arguments[0](r.status + ' ' + t)))"}),
                file=sys.stderr)
            raise RuntimeError("la conversazione non compare nella companion: " + str(state.get("value")))
        # fonti aperte, domanda in alto, niente barra di scorrimento
        size = m.cmd("WebDriver:ExecuteScript", {"script": """
            // la CSP della companion blocca i <style> aggiunti, non lo stile in linea
            const html = document.documentElement;
            html.style.zoom = innerWidth / 390;
            for (const el of document.querySelectorAll('*')) el.style.scrollbarWidth = 'none';
            for (const d of document.querySelectorAll('#messages details.sources')) d.open = true;
            for (const el of document.querySelectorAll('*')) el.scrollTop = 0;
            return Math.round(html.clientWidth / html.style.zoom) + 'x'
                + Math.round(html.clientHeight / html.style.zoom);"""})
        print("telefono:", size.get("value"), "px CSS", file=sys.stderr)
        time.sleep(0.5)
        png = m.cmd("WebDriver:TakeScreenshot", {"full": False})["value"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(png))
        m.cmd("WebDriver:DeleteSession")
    finally:
        ff.terminate()
        ff.wait(10)
        shutil.rmtree(profile, ignore_errors=True)


# ------------------------------------------------------------------- main

def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Ollama)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    s = config.load_settings()
    s.update(host=f"http://127.0.0.1:{srv.server_address[1]}", theme="dark",
             tray_icon=False, close_to_tray=False, companion=False,
             app_update_check=False)
    config.save_settings(s)
    code_id, web_id = write_chats()

    from olladesk import theme
    from olladesk.main_window import MainWindow
    from olladesk.widgets.model_manager import ModelManagerDialog

    app = QApplication(sys.argv)
    app.setApplicationName("OllaDesk")
    theme.apply_theme(app, "dark", s["font_size"])

    def wait(ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def save(widget, name):
        widget.grab().save(os.path.join(OUT, name))
        print("scritto", os.path.join("docs", "screenshots", name))

    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    wait(1500)
    win._open_chat(code_id)
    wait(500)
    for area in win.chat_area.findChildren(QScrollArea):
        area.verticalScrollBar().setValue(0)   # la domanda in alto
    wait(200)
    save(win, "chat.png")

    win.settings["theme"] = "light"
    win._apply_theme_now()
    win._open_chat(web_id)
    wait(300)
    for b in win.findChildren(QToolButton):
        if "Fonti" in b.text() and b.text().startswith("▸"):
            b.click()   # apre l'elenco delle fonti
    wait(500)
    save(win, "web-search.png")

    win.settings["theme"] = "dark"
    win._apply_theme_now()
    dlg = ModelManagerDialog(win.engine, win, "qwen3:8b")
    dlg.resize(760, 460)
    dlg.show()
    wait(1200)
    save(dlg, "models.png")
    dlg.close()

    # companion: solo 127.0.0.1, porta libera (mai la 8765 dell'app installata)
    from olladesk import companion
    port = free_port()
    win._companion.start(port, "127.0.0.1")
    wait(500)
    code, _ttl = win._companion.auth.new_code()
    token = win._companion.auth.pair(code)
    result = {}

    def shoot():
        try:
            phone_shot(f"http://127.0.0.1:{port}/#/c/{web_id}", token,
                       companion.COOKIE, os.path.join(OUT, "phone.png"))
            result["ok"] = True
        except Exception as e:   # noqa: BLE001 (riportato sotto)
            result["err"] = e

    t = threading.Thread(target=shoot)
    t.start()
    while t.is_alive():   # il server della companion risponde dal ciclo Qt
        wait(100)
    win._companion.stop()
    if "err" in result:
        print("telefono:", result["err"], file=sys.stderr)
        return 1
    print("scritto docs/screenshots/phone.png")
    win._really_quit = True
    win.close()
    srv.shutdown()
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(os.environ["XDG_CONFIG_HOME"], ignore_errors=True)
    sys.exit(rc)

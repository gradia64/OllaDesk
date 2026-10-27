#!/usr/bin/env python3
"""Screenshot per i metadati AppStream e il README: docs/screenshots/*.png.

Uso:  python3 scripts/screenshots.py            # inglese → docs/screenshots/
      python3 scripts/screenshots.py --lang it  # → docs/screenshots/it/

Flathub vuole gli screenshot in inglese: è la lingua predefinita. Con
--lang l'interfaccia (OLLADESK_LANG) e le chat di esempio cambiano lingua.

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

LANG = sys.argv[sys.argv.index("--lang") + 1] if "--lang" in sys.argv else "en"
if LANG not in ("en", "it"):
    sys.exit(f"lingua non supportata: {LANG} (en, it)")

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="olladesk_shots_")
os.environ["OLLADESK_LANG"] = LANG   # prima di importare olladesk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_REL = os.path.join("docs", "screenshots", *([] if LANG == "en" else [LANG]))
OUT = os.path.join(ROOT, OUT_REL)
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QToolButton  # noqa: E402

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


    def do_POST(self):  # noqa: N802 (API http.server)
        # /api/show: capacità del modello (badge accanto al selettore)
        length = int(self.headers.get("Content-Length") or 0)
        name = json.loads(self.rfile.read(length) or b"{}").get("model", "")
        if self.path != "/api/show":
            self.send_error(404)
            return
        caps = ["completion"]
        if name.startswith(("qwen3", "deepseek-r1")):
            caps += ["thinking", "tools"]
        if name.startswith("gemma3"):
            caps.append("vision")
        data = json.dumps({"capabilities": caps}).encode()
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

TEXTS = {"it": {}, "en": {}}

TEXTS["it"]["code_answer"] = """Ecco uno script che legge la data di scatto dai metadati EXIF e \
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

TEXTS["it"]["web_answer"] = """Le novità principali di **Python 3.14** sono:

- **Template string (PEP 750)**: il prefisso `t"..."` crea un oggetto \
`Template`, utile per l'escape sicuro di HTML e SQL [1].
- **Annotazioni valutate in ritardo (PEP 649)**: i riferimenti in avanti \
funzionano senza virgolette [1].
- **Interprete senza GIL**: la build *free-threaded* è supportata \
ufficialmente [2].
- **Modulo `concurrent.interpreters`** (PEP 734): più interpreti nella \
libreria standard [3]."""

TEXTS["it"].update(
    chats=["Traduci l'email al fornitore", "Spiegami i decoratori in Python",
           "Riassunto del contratto d'affitto", "Ricetta della pizza in teglia"],
    web_title="Novità di Python 3.14",
    web_question="Quali sono le novità di Python 3.14?",
    web_thinking="L'utente chiede le novità di Python 3.14…",
    code_title="Rinominare le foto per data",
    code_question="Scrivi uno script Python che rinomina le foto di una cartella "
                  "con la data di scatto",
    code_thinking="Serve leggere il tag EXIF DateTimeOriginal…",
)

TEXTS["en"]["code_answer"] = """Here is a script that reads the capture date from the EXIF \
metadata and renames the photos as `YYYY-MM-DD_HHMMSS.jpg`:

```python
from datetime import datetime
from pathlib import Path

from PIL import Image

EXIF_DATE_TAKEN = 36867


def date_taken(photo: Path) -> datetime | None:
    with Image.open(photo) as img:
        value = img.getexif().get_ifd(0x8769).get(EXIF_DATE_TAKEN)
    return datetime.strptime(value, "%Y:%m:%d %H:%M:%S") if value else None


for photo in sorted(Path("Photos").glob("*.jpg")):
    taken = date_taken(photo)
    if taken:
        photo.rename(photo.with_name(taken.strftime("%Y-%m-%d_%H%M%S") + ".jpg"))
```

**How it works**

1. `getexif()` reads the metadata, and tag `36867` is the original capture date.
2. Photos without EXIF (screenshots, downloaded images) keep their name.

It needs Pillow: `pip install pillow`."""

TEXTS["en"]["web_answer"] = """The main new features of **Python 3.14** are:

- **Template strings (PEP 750)**: the `t"..."` prefix creates a `Template` \
object, useful for safely escaping HTML and SQL [1].
- **Deferred evaluation of annotations (PEP 649)**: forward references \
work without quotes [1].
- **Interpreter without the GIL**: the *free-threaded* build is officially \
supported [2].
- **The `concurrent.interpreters` module** (PEP 734): multiple interpreters \
in the standard library [3]."""

TEXTS["en"].update(
    chats=["Translate the email to the supplier", "Explain Python decorators",
           "Summary of the rental agreement", "Sheet pan pizza recipe"],
    web_title="What's new in Python 3.14",
    web_question="What's new in Python 3.14?",
    web_thinking="The user is asking about the new features of Python 3.14…",
    code_title="Rename photos by date",
    code_question="Write a Python script that renames the photos in a folder "
                  "by the date they were taken",
    code_thinking="I need to read the EXIF DateTimeOriginal tag…",
)
TX = TEXTS[LANG]

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
    chats = zip(TX["chats"], ["gemma3:12b", "qwen3:8b", "gemma3:12b", "llama3.2:3b"],
                [9 * 86400, 6 * 86400, 3 * 86400, 2 * 86400])
    for title, model, age in chats:
        config.save_chat({"id": config.new_chat_id(), "title": title, "model": model,
                          "updated": t - age, "messages": [
                              {"role": "user", "display": title, "ts": t - age},
                              {"role": "assistant", "content": "…", "ts": t - age + 20}]})
    web_id = config.new_chat_id()
    q = TX["web_question"]
    config.save_chat({"id": web_id, "title": TX["web_title"], "model": "qwen3:8b",
                      "updated": t - 3600, "messages": [
                          {"role": "user", "display": q, "ts": t - 3700, "web": True,
                           "web_block": web_search.format_results(q, WEB_RESULTS)},
                          {"role": "assistant", "content": TX["web_answer"], "ts": t - 3600,
                           "thinking": TX["web_thinking"],
                           "stats": "312 token · 41.7 tok/s · 7.5 s"}]})
    code_id = config.new_chat_id()
    q = TX["code_question"]
    config.save_chat({"id": code_id, "title": TX["code_title"], "model": "qwen3:8b",
                      "updated": t - 60, "messages": [
                          {"role": "user", "display": q, "ts": t - 120},
                          {"role": "assistant", "content": TX["code_answer"], "ts": t - 60,
                           "thinking": TX["code_thinking"],
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
    ff = None
    try:
        # dentro il try: se Firefox manca o non parte, il profilo va via lo stesso
        port = free_port()
        with open(os.path.join(profile, "user.js"), "w") as f:
            f.write(f'user_pref("marionette.port", {port});\n'
                    'user_pref("layout.css.devPixelsPerPx", "2.0");\n'
                    'user_pref("ui.systemUsesDarkTheme", 1);\n'
                    'user_pref("browser.shell.checkDefaultBrowser", false);\n')
        ff = subprocess.Popen(["firefox", "--headless", "--marionette", "--no-remote",
                               "--profile", profile],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        if ff is not None:
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
             app_update_check=False, ollama_update_check=False)
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
        print("scritto", os.path.join(OUT_REL, name))

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
        if "🌐" in b.text() and b.text().startswith("▸"):   # «Fonti», in ogni lingua
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

    # impostazioni: pagina «Generale», stessa dimensione della galleria dei README
    from olladesk.widgets.settings_dialog import SettingsDialog
    # indirizzo e cartella predefiniti al posto di quelli temporanei della prova
    sdlg = SettingsDialog({**win.settings, "host": "http://localhost:11434"},
                          win.model_names, "dark", "0.40.2", win)
    real_dir = str(config.config_dir())
    for label in sdlg.findChildren(QLabel):
        if real_dir in label.text():
            label.setText(label.text().replace(real_dir, "~/.config/olladesk"))
    sdlg.resize(820, 620)
    sdlg.show()
    wait(500)
    save(sdlg, "settings.png")
    sdlg.done(0)

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
    print("scritto", os.path.join(OUT_REL, "phone.png"))
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

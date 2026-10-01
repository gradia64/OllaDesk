"""Controllo aggiornamenti di OllaDesk con un finto GitHub locale.

Verifica: avviso nella barra superiore quando la release è più recente,
«Salta questa versione», nessun avviso se si è aggiornati, e che «Salva»
nelle impostazioni conservi data dell'ultimo controllo e versione saltata.

Uso:  python3 tests/offline_app_update.py   (nessuna rete, nessun Ollama)
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> int:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="olladesk_appupd_config_")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from olladesk import __version__, app_update, config
    from olladesk.main_window import MainWindow
    from olladesk.widgets.settings_dialog import SettingsDialog

    state = {"tag": "v99.0.0", "hits": 0}

    class FakeGitHub(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_GET(self):  # noqa: N802
            if self.path.startswith("/api/"):   # finto Ollama: offline
                self.send_response(503)
                self.end_headers()
                return
            state["hits"] += 1
            body = json.dumps({
                "tag_name": state["tag"], "draft": False, "prerelease": False,
                "html_url": f"https://example.invalid/releases/{state['tag']}",
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    app_update.RELEASES_API = base + "/repos/x/releases/latest"

    s = config.load_settings()
    s["host"] = base   # niente dipendenza da un Ollama reale
    config.save_settings(s)

    def wait_ms(ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    wait_ms(4000)   # il controllo parte 3 s dopo l'avvio

    # 1. versione più recente → avviso
    assert state["hits"] == 1, state
    assert win.app_update_btn.isVisible(), "avviso non mostrato"
    assert "99.0.0" in win.app_update_btn.text()
    assert config.load_settings()["app_update_last_check"] > 0
    print("1. nuova versione: avviso nella barra superiore OK")

    # 2. un secondo avvio entro 24 h non ricontrolla
    win._maybe_check_app_update()
    wait_ms(500)
    assert state["hits"] == 1, "ricontrollato prima delle 24 ore"
    print("2. al massimo un controllo al giorno OK")

    # 3. «Salta questa versione»
    def click_skip():
        box = next(w for w in app.topLevelWidgets() if isinstance(w, QMessageBox) and w.isVisible())
        skip = next(b for b in box.buttons()
                    if box.buttonRole(b) == QMessageBox.ButtonRole.DestructiveRole)
        skip.click()
    QTimer.singleShot(300, click_skip)
    win._show_app_update()
    assert not win.app_update_btn.isVisible()
    assert config.load_settings()["app_update_skip"] == "99.0.0"
    win._set_app_update(app_update.newer_release({"version": "99.0.0", "url": "u"}))
    assert not win.app_update_btn.isVisible(), "la versione saltata ricompare"
    print("3. «Salta questa versione» OK")

    # 4. impostazioni: «Verifica ora» con versione uguale, poi Salva
    state["tag"] = "v" + __version__
    dlg = SettingsDialog(win.settings, win.model_names, "dark", "?", win)
    dlg.applied.connect(win._apply_settings)
    dlg.appReleaseChecked.connect(win._on_app_release)
    dlg.show()
    dlg._check_app_update()
    wait_ms(1500)
    assert "aggiornato" in dlg.app_update_status.text(), dlg.app_update_status.text()
    dlg._on_save()
    saved = config.load_settings()
    assert saved["app_update_skip"] == "99.0.0", "Salva ha perso la versione saltata"
    assert saved["app_update_last_check"] > 0, "Salva ha azzerato la data del controllo"
    print("4. «Verifica ora» e Salva conservano lo stato OK")

    # 5. controllo disattivato → nessun controllo automatico
    win.settings["app_update_check"] = False
    win.settings["app_update_last_check"] = 0
    hits = state["hits"]
    win._maybe_check_app_update()
    wait_ms(500)
    assert state["hits"] == hits
    print("5. controllo automatico disattivabile OK")

    print("APP UPDATE OK")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())

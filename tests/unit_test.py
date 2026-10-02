"""Unit test delle funzioni pure di OllaDesk (senza rete, senza event loop).

Eseguibili con pytest oppure direttamente: python3 tests/unit_test.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from olladesk.context import build_api_content, classify, extract_text
from olladesk.md import md_to_html
from olladesk.updater import is_local_host, is_newer, parse_version, update_command_stdin


# ------------------------------------------------------------------- md.py

def test_md_bold_italic_heading():
    html = md_to_html("# Titolo\n**grassetto** e *corsivo*")
    assert "<h1>Titolo</h1>" in html
    assert "<b>grassetto</b>" in html
    assert "<i>corsivo</i>" in html


def test_md_code_block_escaped():
    html = md_to_html("Testo\n```python\nprint('<b>&</b>')\n```\nfine")
    assert "print(&#x27;&lt;b&gt;&amp;&lt;/b&gt;&#x27;)" in html
    assert "bgcolor" in html


def test_md_streaming_unclosed_fence():
    html = md_to_html("Ecco:\n```py\nx = 1")
    assert html.count("bgcolor") == 1


def test_md_inline_backtick_no_stray_fence():
    html = md_to_html("usa ``` nel mezzo del testo e basta")
    assert "<table" not in html                       # nessun blocco spurio
    assert not html.rstrip().endswith("```")          # niente fence aggiunta in coda


def test_md_list_no_br_between_items():
    html = md_to_html("- uno\n- due\n- tre")
    assert "<li>uno</li><br>" not in html
    assert html.count("<li>") == 3


def test_md_links_only_http():
    html = md_to_html("[x](https://example.com) e [y](javascript:alert(1))")
    assert 'href="https://example.com"' in html
    assert 'href="javascript' not in html


def test_md_table():
    html = md_to_html("| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<table" in html
    assert "<b>a</b>" in html and "<b>b</b>" in html
    assert "<td>1</td>" in html and "<td>2</td>" in html


def test_md_blockquote():
    html = md_to_html("prima\n> una citazione\n> seconda riga\ndopo")
    assert "<blockquote>" in html and "una citazione" in html
    assert html.startswith("prima") and html.endswith("dopo")


def test_md_nested_list():
    html = md_to_html("- a\n  - a1\n- b")
    assert "<li>a<ul><li>a1</li></ul></li>" in html
    assert "<li>b</li>" in html


def test_md_under_bold_and_intraword_safe():
    html = md_to_html("__grande__ e foo__bar__baz e var_name")
    assert "<b>grande</b>" in html
    assert "foo__bar__baz" in html          # underscore interni alla parola: intatti
    assert "<i>name</i>" not in html


def test_md_heading_escaped():
    # le intestazioni devono subire l'escape HTML come tutto il resto
    html = md_to_html("# a < b e [x](file:///etc/passwd)")
    assert "&lt; b" in html
    assert 'href="file://' not in html
    assert "<h1>" in html


def test_update_command_stdin():
    cmd = update_command_stdin()
    if cmd is not None:   # pkexec può mancare nell'ambiente di test
        assert cmd[1:] == ["sh", "-s"]


# -------------------------------------------------------------- context.py

def test_context_dotfiles_recognized():
    d = Path(tempfile.mkdtemp())
    assert classify(d / ".gitignore") == "text"
    assert classify(d / ".env") == "text"
    assert classify(d / "Dockerfile") == "text"
    assert classify(d / "Makefile") == "text"
    (d / ".env").write_text("SEGRETO=1\n", encoding="utf-8")
    text, note = extract_text(d / ".env")
    assert "SEGRETO=1" in text and note is None


def test_context_bounded_read():
    d = Path(tempfile.mkdtemp())
    big = d / "big.log"
    big.write_text("x" * 500_000, encoding="utf-8")
    text, note = extract_text(big)
    assert note is None
    assert len(text) <= 120_000 + 60                  # troncato, senza leggere tutto


def test_context_build_api_content():
    d = Path(tempfile.mkdtemp())
    (d / "note.txt").write_text("CIAO", encoding="utf-8")
    msg = {"display": "domanda",
           "attachments_meta": [{"path": str(d / "note.txt"), "name": "note.txt", "kind": "text"}]}
    full, warnings = build_api_content(msg, include_full=True)
    assert "CIAO" in full and "dati non attendibili" in full
    assert not warnings
    short, _w = build_api_content(msg, include_full=False)
    assert "CIAO" not in short and "note.txt" in short
    msg2 = {"display": "domanda", "web_block": "Risultati della ricerca web"}
    full2, _w2 = build_api_content(msg2, include_full=True)
    assert "Risultati della ricerca web" in full2
    short2, _w3 = build_api_content(msg2, include_full=False)
    assert "Risultati della ricerca web" not in short2


# --------------------------------------------------------------- updater.py

def test_version_parsing():
    assert parse_version("v0.34.4") == (0, 34, 4)
    assert is_newer("0.35.0", "0.34.4")
    assert not is_newer("0.34.4", "0.34.4")
    assert not is_newer("0.9", "0.34.4")     # confronto numerico, non lessicografico


def test_is_local_host():
    assert is_local_host("http://localhost:11434")
    assert is_local_host("127.0.0.1:11434")
    assert is_local_host("http://[::1]:11434")
    assert not is_local_host("http://192.168.1.20:11434")
    assert not is_local_host("https://ollama.example.org")


# ---------------------------------------------------------------- config.py

def test_settings_roundtrip_and_permissions():
    from olladesk import config
    old = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
    try:
        s = config.load_settings()
        assert s["host"] == config.DEFAULT_SETTINGS["host"]
        s["web_provider"] = "searxng"
        config.save_settings(s)
        path = config.config_dir() / "settings.json"
        assert json.loads(path.read_text(encoding="utf-8"))["web_provider"] == "searxng"
        assert not (os.stat(path).st_mode & 0o077)    # 0600: solo il proprietario
        assert not (os.stat(config.config_dir()).st_mode & 0o077)  # 0700
    finally:
        if old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = old


def test_chat_split_and_legacy_migration():
    from olladesk import config
    old = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
    try:
        base = config.config_dir()
        # simula il vecchio archivio monolitico
        legacy = [{"id": "abc", "title": "Vecchia", "model": "m", "updated": 5,
                   "messages": [{"role": "user", "content": "ciao"}]}]
        (base / "chats.json").write_text(json.dumps(legacy), encoding="utf-8")

        index = config.load_chats()
        assert [c["id"] for c in index] == ["abc"]          # migrata nell'indice
        full = config.load_chat("abc")
        assert full["messages"][0]["content"] == "ciao"
        assert (base / "chats.json.bak").exists()           # backup del legacy

        full["messages"].append({"role": "assistant", "content": "ehi"})
        config.save_chat(full)
        assert config.load_chat("abc")["messages"][0]["content"] == "ciao"
        assert len(config.load_chats()) == 1

        config.rename_chat("abc", "Nuovo titolo")
        assert config.load_chats()[0]["title"] == "Nuovo titolo"
        config.delete_chat("abc")
        assert config.load_chat("abc") is None and config.load_chats() == []
    finally:
        if old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = old


def _isolated_config():
    """Context manager: XDG_CONFIG_HOME in una cartella temporanea nuova."""
    import contextlib

    @contextlib.contextmanager
    def cm():
        old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
        try:
            yield
        finally:
            if old is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = old
    return cm()


def test_rename_survives_next_save():
    # regressione: rinominare una chat non aperta aggiornava solo l'indice
    from olladesk import config
    with _isolated_config():
        chat = {"id": "r1", "title": "Vecchio", "model": "m", "updated": 1, "messages": []}
        assert config.save_chat(chat)
        assert config.rename_chat("r1", "Nuovo")
        reopened = config.load_chat("r1")
        assert reopened["title"] == "Nuovo"
        reopened["messages"].append({"role": "user", "display": "x"})
        config.save_chat(reopened)
        assert config.load_chats()[0]["title"] == "Nuovo"


def test_index_rebuilt_when_corrupt_or_missing():
    from olladesk import config
    with _isolated_config():
        for i in range(3):
            config.save_chat({"id": f"c{i}", "title": f"T{i}", "updated": i, "messages": []})
        idx = config.chats_dir() / "index.json"
        idx.write_text("{ non json", encoding="utf-8")
        assert [c["id"] for c in config.load_chats()] == ["c2", "c1", "c0"]
        idx.unlink()
        assert len(config.load_chats()) == 3
        # un salvataggio con indice corrotto non deve rendere orfane le altre
        idx.write_text("garbage", encoding="utf-8")
        config.save_chat({"id": "c3", "title": "T3", "updated": 9, "messages": []})
        assert len(config.load_chats()) == 4


def test_write_failure_reported():
    from olladesk import config
    with _isolated_config():
        d = config.chats_dir()
        os.chmod(d, 0o500)   # sola lettura
        try:
            if os.access(d, os.W_OK):
                return       # eseguito come root: il test non è significativo
            assert config.save_chat({"id": "w1", "messages": []}) is False
        finally:
            os.chmod(d, 0o700)


def test_history_window_always_includes_current_turn():
    from olladesk.context import history_window
    msgs = [{"role": "user", "n": i} for i in range(5)]
    assert history_window(msgs, 0) == msgs[-1:]
    assert history_window(msgs, 2) == msgs[-3:]
    assert history_window(msgs, 100) == msgs
    assert history_window([], 3) == []


def test_context_overflow_note():
    from olladesk.context import context_overflow_note
    small = [{"role": "user", "content": "ciao"}]
    big = [{"role": "user", "content": "x" * 40_000}]
    assert context_overflow_note(small, None) is None
    assert context_overflow_note(big, None)             # 4096 predefinito
    assert context_overflow_note(big, 32768) is None


def test_image_to_b64_limits_and_conversion():
    from olladesk import context
    d = Path(tempfile.mkdtemp())
    png = d / "p.png"
    png.write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"))
    b64, note = context.image_to_b64(png)
    assert b64 and note is None
    missing, note = context.image_to_b64(d / "manca.png")
    assert missing is None and note
    bad = d / "rotta.gif"
    bad.write_bytes(b"non un'immagine")
    b64, note = context.image_to_b64(bad)
    assert b64 is None and "non supportato" in note


def test_web_query_is_short_first_line():
    from olladesk.web_search import MAX_QUERY_CHARS, make_query
    assert make_query("\n  prima   riga \nsegreto\n") == "prima riga"
    long_q = make_query("parola " * 100)
    assert len(long_q) <= MAX_QUERY_CHARS and not long_q.endswith(" ")
    assert make_query("") == ""


def test_md_link_url_not_formatted():
    html = md_to_html("[**doc**](https://x.com/a_b/_c_)")
    assert 'href="https://x.com/a_b/_c_"' in html
    assert "<b>doc</b>" in html


def test_md_rules_and_ordered_start():
    assert md_to_html("* * *") == "<hr>"
    assert md_to_html("- - -") == "<hr>"
    assert '<ol start="3">' in md_to_html("3. tre\n4. quattro")
    assert "<ol>" in md_to_html("1. uno\n2. due")


def test_md_list_kind_switch_balanced():
    html = md_to_html("- a\n- b\n1. uno\n2. due")
    assert html == "<ul><li>a</li><li>b</li></ul><ol><li>uno</li><li>due</li></ol>"


def test_md_table_needs_matching_separator():
    assert "<table" not in md_to_html("titolo | x\n---")
    assert "<table" in md_to_html("| a | b |\n|---|---|\n| 1 | 2 |")


# ------------------------------------------------------------ app_update.py

def test_app_update_parse_release():
    from olladesk.app_update import parse_release
    rel = parse_release({"tag_name": "v0.2.3", "html_url": "https://x/r/v0.2.3"})
    assert rel == {"version": "0.2.3", "url": "https://x/r/v0.2.3"}
    assert parse_release({"tag_name": "v0.3.0", "prerelease": True}) is None
    assert parse_release({"tag_name": "v0.3.0", "draft": True}) is None
    assert parse_release({"tag_name": ""}) is None
    assert parse_release(["non", "un", "dict"]) is None


def test_app_update_newer_release():
    from olladesk.app_update import newer_release
    rel = {"version": "0.2.3", "url": "u"}
    assert newer_release(rel, "0.2.2") == rel
    assert newer_release(rel, "0.2.3") is None
    assert newer_release(rel, "0.10.0") is None       # confronto numerico, non testuale
    assert newer_release(None, "0.2.2") is None


def test_app_update_due_for_check():
    from olladesk.app_update import CHECK_INTERVAL, due_for_check
    now = 1_000_000.0
    assert due_for_check({}, now)                                       # mai controllato
    assert not due_for_check({"app_update_check": False}, now)
    assert not due_for_check({"app_update_last_check": now - 60}, now)
    assert due_for_check({"app_update_last_check": now - CHECK_INTERVAL}, now)
    assert due_for_check({"app_update_last_check": now + 3600}, now)    # orologio indietro
    assert due_for_check({"app_update_last_check": "rotto"}, now)


def test_app_update_install_method_and_hint():
    from olladesk import app_update
    d = Path(tempfile.mkdtemp())
    pkg = d / "olladesk"
    pkg.mkdir()
    assert app_update.install_method(pkg) == "pip"
    (d / ".git").mkdir()
    assert app_update.install_method(pkg) == "source"
    for m in ("deb", "arch"):
        (pkg / app_update.MARKER_NAME).write_text(m + "\n", encoding="utf-8")
        assert app_update.install_method(pkg) == m
    (pkg / app_update.MARKER_NAME).write_text("sconosciuto", encoding="utf-8")
    assert app_update.install_method(pkg) == "source"
    assert "olladesk_0.2.3_all.deb" in app_update.update_hint("deb", "0.2.3")
    assert "AUR" in app_update.update_hint("arch", "0.2.3")
    assert app_update.download_page("arch") == app_update.AUR_PAGE
    assert app_update.download_page("deb") == app_update.RELEASES_PAGE


def test_settings_keep_app_update_keys():
    # le chiavi nuove devono sopravvivere al filtro di load_settings
    from olladesk import config
    with _isolated_config():
        s = config.load_settings()
        assert s["app_update_check"] is True and s["app_update_skip"] == ""
        s.update(app_update_last_check=123.0, app_update_skip="0.2.3")
        config.save_settings(s)
        s2 = config.load_settings()
        assert s2["app_update_last_check"] == 123.0 and s2["app_update_skip"] == "0.2.3"


def test_settings_new_024_keys():
    # 0.2.4: thinking, tray e condivisione API hanno predefiniti sensati
    from olladesk import config
    with _isolated_config():
        s = config.load_settings()
        assert s["thinking"] is True
        assert s["tray_icon"] is True and s["close_to_tray"] is True
        assert s["share_api"] is False
        assert s["share_bind"] == "0.0.0.0" and s["share_port"] == 11434
        # e sopravvivono al roundtrip su disco
        s.update(thinking=False, share_api=True, share_port=11435)
        config.save_settings(s)
        s2 = config.load_settings()
        assert s2["thinking"] is False
        assert s2["share_api"] is True and s2["share_port"] == 11435


def test_chat_column_side_margin():
    # colonna messaggi centrata: sotto soglia margine minimo, sopra si centra
    from olladesk.widgets.chat_area import COLUMN_MAX_W, ChatArea
    assert ChatArea._side_margin(300) == 20
    assert ChatArea._side_margin(COLUMN_MAX_W + 40) == 20
    assert ChatArea._side_margin(COLUMN_MAX_W + 340) == 170


def test_share_lan_url_filter():
    # il filtro degli indirizzi di rete parte dalla stringa: loopback,
    # link-local e IPv6 non sono utili al client su smartphone
    from olladesk.server_share import _is_shareable_ip
    assert _is_shareable_ip("192.168.1.20")
    assert _is_shareable_ip("10.0.0.5")
    assert not _is_shareable_ip("127.0.0.1")
    assert not _is_shareable_ip("0.0.0.0")
    assert not _is_shareable_ip("169.254.3.4")
    assert not _is_shareable_ip("::1")
    assert not _is_shareable_ip("fe80::1")


def test_chat_worker_emits_thinking():
    # il ChatWorker deve distinguere thinking da content, sia in risposta
    # singola che in streaming NDJSON
    import json as _json
    from unittest.mock import patch

    from olladesk import ollama_client
    from olladesk.ollama_client import ChatWorker

    single = _json.dumps({
        "message": {"role": "assistant", "thinking": "ragiono…", "content": "risposta"},
        "done": True,
    }).encode("utf-8")

    class FakeSingle:
        def read(self):
            return single

        def close(self):
            pass

    w = ChatWorker("http://localhost:11434", {"stream": False})
    got: list[tuple[str, str]] = []
    w.think_chunk.connect(lambda t: got.append(("think", t)))
    w.chunk.connect(lambda t: got.append(("chunk", t)))
    w.done.connect(lambda d: got.append(("done", str(d.get("done")))))
    with patch.object(ollama_client, "_open", return_value=FakeSingle()):
        w.run()
    assert ("think", "ragiono…") in got
    assert ("chunk", "risposta") in got
    assert any(k == "done" for k, _ in got)

    lines = [
        _json.dumps({"message": {"thinking": "penso "}}),
        _json.dumps({"message": {"content": "ciao"}}),
        _json.dumps({"done": True, "eval_count": 5}),
    ]

    class FakeStream:
        def close(self):
            pass

        def __iter__(self):
            return iter(("\n".join(lines)).encode("utf-8").splitlines())

    w2 = ChatWorker("http://localhost:11434", {"stream": True})
    got2: list[tuple[str, str]] = []
    w2.think_chunk.connect(lambda t: got2.append(("think", t)))
    w2.chunk.connect(lambda t: got2.append(("chunk", t)))
    w2.done.connect(lambda d: got2.append(("done", str(d.get("done")))))
    with patch.object(ollama_client, "_open", return_value=FakeStream()):
        w2.run()
    assert ("think", "penso ") in got2
    assert ("chunk", "ciao") in got2
    assert any(k == "done" for k, _ in got2)


def test_is_local_host_debian_hostname():
    import socket
    assert is_local_host("http://127.0.1.1:11434")
    assert is_local_host(f"http://{socket.gethostname()}:11434")


def test_clear_ref_keeps_newer_worker():
    # regressione: il `finished` di un worker annullato azzerava il nuovo
    from olladesk.main_window import MainWindow

    class Host:
        _worker = None

    h = Host()
    old, new = object(), object()
    clear_old = MainWindow._clear_ref(h, "_worker", old)
    h._worker = new
    clear_old()
    assert h._worker is new
    MainWindow._clear_ref(h, "_worker", new)()
    assert h._worker is None


# --------------------------------------------------------- secrets_store.py

def test_secrets_store_stub_and_fallback():
    from olladesk import secrets_store

    class FakeKR:
        def __init__(self):
            self.store = {}

        def set_password(self, svc, name, val):
            self.store[(svc, name)] = val

        def get_password(self, svc, name):
            return self.store.get((svc, name))

        def delete_password(self, svc, name):
            self.store.pop((svc, name), None)

    fake = FakeKR()
    orig = secrets_store._keyring
    secrets_store._keyring = lambda: fake
    try:
        assert secrets_store.available()
        assert secrets_store.save_api_key("SK-123")
        assert secrets_store.load_api_key() == "SK-123"
        assert secrets_store.save_api_key("")      # rimozione
        assert secrets_store.load_api_key() == ""
    finally:
        secrets_store._keyring = orig
    # senza keyring: fallback silenzioso (la chiave resta nel file, chmod 600)
    secrets_store._keyring = lambda: None
    try:
        assert not secrets_store.available()
        assert secrets_store.load_api_key() == ""
        assert secrets_store.save_api_key("x") is False
    finally:
        secrets_store._keyring = orig


def _make_window():
    """MainWindow offscreen con QApplication condivisa (per i test GUI).

    Richiede l'ambiente già isolato (_isolated_config): nessun timer viene
    eseguito perché non c'è event loop.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from olladesk.main_window import MainWindow

    return MainWindow()


def _thinking_texts(win) -> list[str]:
    from olladesk.widgets.message import MessageWidget

    out: list[str] = []
    for i in range(win.chat_area.msgs.count()):
        row = win.chat_area.msgs.itemAt(i).widget()
        if row is None:
            continue
        lay = row.layout()
        for j in range(lay.count()):
            it = lay.itemAt(j).widget()
            if isinstance(it, MessageWidget):
                out.append(it._thinking_raw)
    return out


def test_share_state_updates_indicator():
    # B1: lo stato del server condiviso deve riflettersi sull'indicatore 🔗
    with _isolated_config():
        win = _make_window()
        try:
            assert not win.share_btn.isVisibleTo(win)
            # emit diretto: verifica anche il collegamento state_changed → UI
            win._share.state_changed.emit("running", "192.168.1.10")
            assert win.share_btn.isVisibleTo(win)
            assert win.share_btn.toolTip()
            win._share.state_changed.emit("off", "")
            assert not win.share_btn.isVisibleTo(win)
            win._share.state_changed.emit("error", "collaudo")
            assert win.share_btn.isVisibleTo(win)
            assert "collaudo" in win.share_btn.toolTip()
        finally:
            win._really_quit = True
            win.close()


def test_reopen_and_rerender_keep_thinking():
    # B4: il «Pensiero» salvato ricompare riaprendo la conversazione e
    # anche dopo un re-render generale (cambio tema)
    from olladesk import config

    with _isolated_config():
        chat = {
            "id": "th1", "title": "T", "model": "m", "updated": 1,
            "messages": [
                {"role": "user", "content": "domanda", "display": "domanda"},
                {"role": "assistant", "content": "risposta",
                 "thinking": "ho riflettuto molto"},
            ],
        }
        assert config.save_chat(chat)
        win = _make_window()
        try:
            win._open_chat("th1")
            assert "ho riflettuto molto" in _thinking_texts(win)
            win._rerender_messages()
            assert "ho riflettuto molto" in _thinking_texts(win)
        finally:
            win._really_quit = True
            win.close()


def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_share_spawn_starts_process():
    # regressione: QProcess.setChildProcessModifier non esiste in PySide6 e
    # _spawn sollevava AttributeError lasciando lo stato «starting» per sempre.
    # Gira in un processo a parte: l'event loop necessario alla sonda non deve
    # ricevere anche gli eventi in sospeso delle finestre create dagli altri
    # test della suite (già chiuse).
    import subprocess

    child = """
import os, sys, stat, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, {root!r})
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
from olladesk import server_share

import socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()

bindir = tempfile.mkdtemp()
fake = os.path.join(bindir, "ollama")
with open(fake, "w") as fh:
    fh.write("#!/bin/sh\\nexec sleep 60\\n")
os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")

srv = server_share.SharedOllamaServer()
srv.start("127.0.0.1", port)
loop = QEventLoop()
QTimer.singleShot(2500, loop.quit)
loop.exec()
assert srv._proc is not None, "il processo non è mai stato avviato"
assert srv.state() in ("starting", "running"), srv.state()
srv.stop()
assert srv._proc is None
print("SPAWN CHILD OK")
""".format(root=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    r = subprocess.run(
        [sys.executable, "-c", child],
        capture_output=True, text=True, timeout=60,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert "SPAWN CHILD OK" in r.stdout, f"figlio fallito:\n{r.stdout}\n{r.stderr}"
    assert r.returncode == 0, f"figlio uscito con {r.returncode}:\n{r.stderr}"


def test_share_probe_replaced_while_in_flight():
    # regressione: _start_probe usciva se una sonda era in volo, quindi una
    # configurazione cambiata a caldo non veniva mai sondata
    with _isolated_config():
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])
        from olladesk import server_share

        srv = server_share.SharedOllamaServer()
        srv._cfg = ("127.0.0.1", _free_port())
        srv._set_state("starting", "")
        srv._start_probe("http://127.0.0.1:1/api/version", "external-local")
        first = srv._probe
        assert first is not None
        srv._start_probe("http://127.0.0.1:2/api/version", "external-local")
        assert srv._probe is not None and srv._probe is not first
        # la sonda sostituita, finendo, non azzera il riferimento alla nuova
        first.wait(2500)
        assert srv._probe is not None
        srv.stop()


def test_thinking_stream_no_duplication():
    # B1: _flush_thinking passa il buffer INTERO accumulato: il widget deve
    # sostituire, non concatenare, altrimenti il pensiero si ripete
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from olladesk.widgets.message import MessageWidget

    w = MessageWidget("assistant", "", None, False, "dark", None, False, None)
    for chunk in ("Uno ", "Uno due ", "Uno due tre "):   # come i flush successivi
        w.set_thinking_stream(chunk)
    assert w._thinking_raw == "Uno due tre ", w._thinking_raw


def test_quit_from_hidden_tray_exits_app():
    # B2: finestra già ridotta nella tray → «Esci» deve far tornare app.exec().
    # Gira in un processo a parte perché serve un vero ciclo eventi.
    import subprocess

    child = """
import os, sys, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["OLLADEK_CHILD"] = "1"
sys.path.insert(0, {root!r})
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
app = QApplication([])
from olladesk.main_window import MainWindow

win = MainWindow()
win.show()
win.tray = QSystemTrayIcon()   # finta: offscreen non ha una tray di sistema
win.close()                    # X → ridotta nella tray (finestra nascosta)
assert win.isHidden(), "la finestra doveva ridursi nella tray"

def _timeout():
    print("QUIT TIMEOUT: app.exec() non è mai tornato", flush=True)
    os._exit(1)

QTimer.singleShot(8000, _timeout)
win._quit_from_tray()          # «Esci» dal menu della tray
code = app.exec()
assert code == 0, code
# flush prima di os._exit, che altrimenti scarterebbe il buffer dello stdout
print("QUIT OK: app.exec() è tornato", flush=True)
os._exit(0)
""".format(root=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    r = subprocess.run(
        [sys.executable, "-c", child],
        capture_output=True, text=True, timeout=60,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert "QUIT OK" in r.stdout, f"figlio fallito:\n{r.stdout}\n{r.stderr}"
    assert r.returncode == 0, f"figlio uscito con {r.returncode}:\n{r.stderr}"


def test_share_lan_shared_flag():
    # il flag decide quando copiare gli indirizzi: nostra istanza con bind di
    # rete, o istanza esterna raggiungibile dalla sonda LAN
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from olladesk import server_share

    srv = server_share.SharedOllamaServer()
    srv._cfg = ("0.0.0.0", 11434)
    srv._generation = 1

    srv._set_state("starting", "")
    srv._on_probe(True, "external-lan", 1)
    assert srv.lan_shared is True            # istanza di sistema raggiungibile

    srv._set_state("starting", "")
    srv._on_probe(False, "external-lan", 1)
    assert srv.lan_shared is False           # bind 127.0.0.1 del servizio

    srv._set_state("starting", "")
    srv._on_probe(True, "spawn-check", 1)
    assert srv.lan_shared is True            # nostro processo, bind di rete

    srv._cfg = ("127.0.0.1", 11434)
    srv._set_state("starting", "")
    srv._on_probe(True, "spawn-check", 1)
    assert srv.lan_shared is False           # nostro processo, solo locale

    srv._on_probe(True, "external-lan", 1)   # riattiva, poi stop azzera
    srv.stop()
    assert srv.lan_shared is False


def test_share_copy_respects_lan_shared():
    # regressione I4: con l'Ollama di sistema già raggiungibile in rete il
    # clic su 🔗 deve COPIARE gli indirizzi, non rifiutare
    with _isolated_config():
        win = _make_window()
        try:
            from PySide6.QtGui import QGuiApplication

            from olladesk import server_share

            QGuiApplication.clipboard().setText("")
            win._share.lan_shared = False
            win._copy_share_urls()
            assert QGuiApplication.clipboard().text() == ""   # rifiutato

            win._share.lan_shared = True
            win._copy_share_urls()
            urls = server_share.lan_urls(int(win.settings.get("share_port", 11434)))
            text = QGuiApplication.clipboard().text()
            if urls:   # senza interfacce di rete non c'è nulla da copiare
                assert text == "\n".join(urls), text
        finally:
            win._really_quit = True
            win.close()


def test_share_external_note_only_when_actionable():
    # la nota «external» in chat non deve comparire quando è tutto a posto
    # (istanza già in ascolto sulle interfacce): resta solo per il caso da
    # sistemare, e una volta per sessione
    with _isolated_config():
        win = _make_window()
        try:
            from olladesk.widgets.message import SystemNoteWidget

            def note_count() -> int:
                n = 0
                for i in range(win.chat_area.msgs.count()):
                    row = win.chat_area.msgs.itemAt(i).widget()
                    lay = row.layout() if row is not None else None
                    if lay is not None and any(
                        isinstance(lay.itemAt(j).widget(), SystemNoteWidget)
                        for j in range(lay.count())
                    ):
                        n += 1
                return n

            win._share.lan_shared = True   # Ollama di sistema già in rete
            win._on_share_state("external", "già attivo e in ascolto su tutte le interfacce")
            assert note_count() == 0, "nota inutile nello stato buono"

            win._share.lan_shared = False  # bind 127.0.0.1: da sistemare
            win._on_share_state("external", "risponde solo su questo PC")
            assert note_count() == 1
            win._on_share_state("external", "risponde solo su questo PC")
            assert note_count() == 1   # una sola volta per sessione
        finally:
            win._really_quit = True
            win.close()


def test_system_note_dismiss():
    # il pulsante ✕ delle note di sistema le rimuove dalla chat. Gira in un
    # processo a parte: il processEvents che smalta la deleteLater non deve
    # ricevere anche i timer in scadenza delle finestre degli altri test
    import subprocess

    child = """
import os, sys, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, {root!r})
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
from olladesk.main_window import MainWindow
from olladesk.widgets.message import SystemNoteWidget

win = MainWindow()
win.chat_area.add_system_note("avviso di prova")
win.chat_area.add_system_note("secondo avviso")

def notes():
    out = []
    for i in range(win.chat_area.msgs.count()):
        row = win.chat_area.msgs.itemAt(i).widget()
        lay = row.layout() if row is not None else None
        for j in range(lay.count() if lay is not None else 0):
            w = lay.itemAt(j).widget()
            if isinstance(w, SystemNoteWidget):
                out.append(w)
    return out

assert len(notes()) == 2
notes()[0].close_btn.click()
# la deleteLater serve un vero ciclo eventi: processEvents da solo può non
# smaltire i DeferredDelete postati fuori dal ciclo
loop = QEventLoop()
QTimer.singleShot(120, loop.quit)
loop.exec()
remaining = notes()
assert len(remaining) == 1, remaining
assert remaining[0].label.text() == "secondo avviso"
print("NOTE DISMISS OK", flush=True)
""".format(root=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    r = subprocess.run(
        [sys.executable, "-c", child],
        capture_output=True, text=True, timeout=60,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert "NOTE DISMISS OK" in r.stdout, f"figlio fallito:\n{r.stdout}\n{r.stderr}"
    assert r.returncode == 0, f"figlio uscito con {r.returncode}:\n{r.stderr}"


def test_brain_icon_tinted_and_sized():
    # il toggle thinking usa un'icona tinta (grigia/blu) come il globo: il
    # glifo deve riempire il riquadro e il fallback deve comunque produrre
    # un'icona valida
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from olladesk import theme

    for color in ("#9b9b9b", theme.WEB_ACTIVE_COLOR):
        img = theme.brain_icon(color).pixmap(18, 18).toImage()
        opaque = sum(
            1 for x in range(18) for y in range(18)
            if img.pixelColor(x, y).alpha() > 8
        )
        assert opaque > 18 * 18 * 0.2, (color, opaque)   # glifo presente
    fb = theme._brain_fallback_icon("#9b9b9b", 18).pixmap(18, 18).toImage()
    assert any(
        fb.pixelColor(x, y).alpha() > 8 for x in range(18) for y in range(18)
    )


def main() -> int:
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ✓ {name}")
            except AssertionError as e:
                failed += 1
                print(f"  ✗ {name}: {e}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"  ✗ {name}: ERRORE {e.__class__.__name__}: {e}")
    print("UNIT TEST OK" if not failed else f"UNIT TEST FAILED ({failed})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

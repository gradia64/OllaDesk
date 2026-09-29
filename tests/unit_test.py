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

"""Unit test delle funzioni pure di OllaDesk (senza rete, senza event loop).

Eseguibili con pytest oppure direttamente: python3 tests/unit_test.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from olladesk.context import build_user_content, classify, extract_text
from olladesk.md import md_to_html
from olladesk.updater import is_local_host, is_newer, parse_version


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


def test_context_build_user_content():
    d = Path(tempfile.mkdtemp())
    (d / "note.txt").write_text("CIAO", encoding="utf-8")
    full, images, warnings = build_user_content(
        "domanda",
        [{"path": str(d / "note.txt"), "name": "note.txt", "kind": "text"}],
    )
    assert "CIAO" in full and "dati non attendibili" in full
    assert not images and not warnings


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

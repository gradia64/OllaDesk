"""Configurazione di pytest: raccoglie solo tests/unit_test.py.

Gli altri script hanno il loro runner e si eseguono con python3, come in CI:
- release_tools_test.py: le sue funzioni prendono un portachiavi temporaneo
  condiviso (tmp, keys) che non è una fixture di pytest; raccolte da pytest
  davano 10 errori;
- engine_test.py, companion_test.py, companion_send_test.py, sync_test.py,
  companion_models_test.py:
  girano al livello del modulo (finto Ollama, server su porta casuale, ciclo
  eventi Qt) e importati da pytest partirebbero alla raccolta.
I gui_*.py e gli altri script non seguono i nomi test_*/_test.
"""
import os
import tempfile

# i test confrontano i testi in italiano, la lingua sorgente dell'interfaccia
os.environ["OLLADESK_LANG"] = "it"
# mai la configurazione vera di chi lancia i test: cartella temporanea per
# tutta la sessione, e config.py rifiuta ~/.config se qualcosa sfugge
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="olladesk_pytest_config_")
os.environ["OLLADESK_TEST"] = "1"

collect_ignore = [
    "release_tools_test.py",
    "engine_test.py",
    "companion_test.py",
    "companion_send_test.py",
    "sync_test.py",
    "companion_models_test.py",
]

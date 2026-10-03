"""Configurazione di pytest: raccoglie solo tests/unit_test.py.

tests/release_tools_test.py ha il suo runner (python3 tests/release_tools_test.py,
come in CI): le sue funzioni prendono un portachiavi temporaneo condiviso
(tmp, keys) che non è una fixture di pytest, e raccolte da pytest davano
10 errori. I gui_*.py e gli altri script non seguono i nomi test_*/_test.
"""
collect_ignore = ["release_tools_test.py"]

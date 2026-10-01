#!/bin/sh
# Launcher OllaDesk (/usr/bin/olladesk): esegue lo script di avvio installato
# in /usr/share/olladesk (vedi launcher.py per il perché non si usa -m)
exec /usr/bin/python3 /usr/share/olladesk/launcher.py "$@"

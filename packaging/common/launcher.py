"""Avvio di OllaDesk installato da pacchetto (.deb o Arch).

Installato in /usr/share/olladesk/launcher.py ed eseguito da /usr/bin/olladesk.

NON `python3 -m olladesk`: con -m Python mette la cartella corrente in
sys.path[0], davanti a tutto, e un json.py (o un checkout di olladesk/) nella
cartella di avvio verrebbe importato al posto dei moduli veri. Eseguendo uno
script, sys.path[0] è la cartella dello script: /usr/share/olladesk.
"""
import sys

from olladesk.app import main

sys.exit(main())

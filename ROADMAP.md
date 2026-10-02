# Roadmap 0.2.5

Piano della versione 0.2.5. Architettura di riferimento: app PySide6,
client puro dell'API Ollama, streaming NDJSON in `ChatWorker`.

## Funzionalità approvate

Nessuna funzionalità nuova: la 0.2.5 raccoglie le rifiniture emerse dal
collaudo quotidiano della 0.2.4 (telefono collegato alla API condivisa
incluso).

| # | Voce | Origine | Stato |
|---|------|---------|-------|
| 1 | 🔗 copia gli indirizzi anche quando la porta è servita da un'Ollama esterno già raggiungibile in rete (`lan_shared` dalle sonde) | collaudo 0.2.4 | ☑ |
| 2 | Niente nota «⚠ Condivisione API» all'avvio quando l'istanza esterna è già in ascolto sulle interfacce (resta solo nel tooltip; nota solo se c'è da sistemare, una volta per sessione) | collaudo 0.2.4 | ☑ |
| 3 | Icona dell'app a piena dimensione nella tray (viewBox ritagliato sui bordi del disegno) | collaudo 0.2.4 | ☑ |
| 4 | Pulsante ✕ per chiudere le note di sistema in chat | collaudo 0.2.4 | ☑ |
| 5 | Icona thinking come il globo della ricerca web: tinta grigia da spento/blu da attivo, senza cornice, stessa dimensione ottica (`theme.brain_icon` con ritaglio del glifo e fallback disegnato) | collaudo 0.2.4 | ☑ |

## Residui tecnici dalla 0.2.4

| # | Voce | Note | Stato |
|---|------|------|-------|
| T1 | Primo rilascio con le action aggiornate (Node 24) | `checkout` v7.0.1, `setup-python` v7.0.0, `upload-artifact` v7.0.1, `download-artifact` v8.0.1, runner fissati su `ubuntu-24.04`. `test`, `dist` e `arch` sono già verificati con un avvio manuale; il job `publish` (release GitHub + AUR) gira solo sui tag: va controllato al tag `v0.2.5` | ☐ |
| T2 | Test e2e della condivisione API | Collaudo manuale completato il 2026-10-02 (telefono collegato a `http://192.168.1.83:11434`; firewalld sistemato con regola rich limitata alla LAN). Resta da valutare un test automatico con bind `127.0.0.1` su una porta libera | ☐ |

## Promemoria per il rilascio

- Il bump di versione si fa al momento del rilascio (fonte unica:
  `olladesk/__init__.py`); aggiornare anche l'esempio `gpg --verify` nel
  README.
- Prima del tag: `tests/unit_test.py` e i collaudi reali
  `tests/gui_smoke.py` e `tests/gui_features.py`.

---

## Archivio — 0.2.4 (rilasciata)

Funzionalità rilasciate: versione nella sidebar, modello in uso nella
«Gestione modelli», condivisione dell'API Ollama in rete (stile LM Studio),
icona nella tray, disattivazione del thinking (`think: false`), blocco
«Pensiero» in streaming, colonna chat centrata (900 px).

Decisioni di progetto ancora valide:

- **Condivisione API — scopo (b)**: OllaDesk gestisce lui stesso
  `ollama serve` (`QProcess` con `OLLAMA_HOST=<bind>:<porta>`). Una sonda
  rileva un'istanza già attiva e una seconda sonda dalla LAN verifica se è
  davvero raggiungibile. C'è un avviso esplicito per l'assenza di
  autenticazione, con il consiglio di usare VPN/SSH per l'accesso da fuori
  casa. Il processo figlio muore con l'app (`setpriv --pdeathsig TERM`).
  Scartati: il solo HOW-TO e il proxy HTTP interno.
- **Tray**: con `quitOnLastWindowClosed` disattivato, l'uscita passa solo
  dal `QApplication.quit()` esplicito nel `closeEvent`.
- **Thinking**: con 🧠 attivo il campo si omette (vale il predefinito del
  server). Il ragionamento è salvato nella conversazione ma non viene
  rimandato al modello nella cronologia. Una generazione interrotta durante
  il solo ragionamento non lascia messaggio, quindi anche il pensiero viene
  scartato («niente contenuto, niente messaggio»).
- **Colonna centrata**: larghezza massima fissa, a piena larghezza nelle
  finestre strette.

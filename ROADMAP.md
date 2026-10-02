# Roadmap 0.2.4

Piano delle funzionalità per la versione 0.2.4, con la valutazione di
fattibilità fatta sull'architettura attuale (app PySide6, client puro
dell'API Ollama, streaming NDJSON in `ChatWorker`).

## Funzionalità approvate

| # | Funzionalità | Fattibilità | Stato |
|---|--------------|-------------|-------|
| 1 | Versione corrente nell'intestazione della sidebar | banale | ☑ |
| 2 | Contrassegno del modello in uso nel «Gestione modelli» | facile | ☑ |
| 3 | Server di rete per l'API Ollama (stile LM Studio) — **scopo (b) confermato**: OllaDesk avvia/arresta `ollama serve` con bind e porta configurabili, indicatore di stato, avviso di sicurezza, URL per smartphone/tablet | media | ☑ |
| 4 | Riduzione a icona nella tray e ripristino (`QSystemTrayIcon`) | facile | ☑ |
| 5 | Tool per disattivare il thinking (`"think": false` in `/api/chat`) | facile | ☑ |
| 6 | Mostrare cosa sta facendo il modello in chat: blocco «Pensiero» in streaming (oggi i token di thinking vengono scartati) + stato | media | ☑ |
| 7 | Colonna chat centrata a larghezza massima fissa (~900 px): nascondendo la sidebar il contenuto non si stira e la bolla utente resta allineata al bordo della colonna, non della finestra | media | ☑ |

Rilasciate nella 0.2.4; i test offline (unit, app update, QThread, percorsi
d'errore GUI) e i collaudi reali (gui_smoke, gui_features con thinking e2e)
passano. Copertura e2e: `tests/gui_smoke.py`
verifica la chat base con Ollama reale; `tests/gui_features.py` include il
thinking (sezione 8, se è installato un modello che lo supporta). La
condivisione API non ha test e2e automatici: va provata a mano (toggle nelle
impostazioni, indicatore 🔗, collegamento da telefono con l'Ollama di
sistema attivo).

## Decisioni di progetto

- **#3 — scopo (b)**: OllaDesk gestisce lui stesso il processo `ollama serve`
  (`QProcess` con `OLLAMA_HOST=<bind>:<porta>`), con probe della porta per
  rilevare un'istanza già attiva, avviso esplicito perché l'API Ollama non
  prevede autenticazione (chi può raggiungere la porta può usare i modelli)
  e suggerimento di VPN/SSH tunnel per l'accesso da fuori casa. Scarti:
  il solo HOW-TO senza gestione di processo (minimo) e il proxy HTTP interno
  (troppo peso di manutenzione: Ollama fa già tutto).
  *Raffinato in revisione*: se sulla porta risponde già un Ollama di sistema,
  una seconda sonda dalla LAN verifica se è davvero condiviso (bind
  127.0.0.1 → avviso con la soluzione consigliata `systemctl edit ollama`);
  la seconda istanza avviata da OllaDesk è presentata come ripiego (usa i
  modelli dell'utente, VRAM separata); il figlio muore con l'app
  (`setpriv --pdeathsig TERM` — `setChildProcessModifier` non esiste in
  PySide6 — più gestori SIGTERM/SIGINT e timer di risveglio perché scattino
  subito).
- **#7 — colonna centrata** stile ChatGPT: la colonna messaggi ha larghezza
  massima fissa e resta centrata quando la sidebar si nasconde; fallback a
  larghezza piena nelle finestre strette.
- **#5/#6 accoppiati**: il pulsante 🧠 disattiva il thinking inviando
  `think: false` (solo quando disattivato: attivato si omette il campo e
  vale il comportamento predefinito del server); il blocco «Pensiero»
  mostra lo stream di `message.thinking`, si richiude all'arrivo del
  contenuto e viene salvato nella conversazione.
- Il bump di versione resta al momento del rilascio (fonte unica:
  `olladesk/__init__.py`).

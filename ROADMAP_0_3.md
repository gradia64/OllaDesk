# Roadmap 0.3

Piano delle funzionalità per la versione 0.3: la **companion web**, che permette di usare OllaDesk da smartphone e tablet nella rete locale. Il telefono vede e continua le **stesse conversazioni** del desktop, con la stessa cronologia, gli stessi parametri e lo stesso prompt di sistema. La fattibilità è valutata sull'architettura della 0.2.4: app PySide6, client puro dell'API Ollama, generazione oggi gestita da `MainWindow`.

## Funzionalità approvate

| # | Funzionalità | Fattibilità | Stato |
|---|---|---|---|
| 0 | Motore di chat separato dalla finestra (`ChatEngine`, QObject senza widget): stato delle conversazioni, generazione, worker e persistenza. Finestra e server web ne diventano client | alta (refactoring) | ☑ |
| 1 | Server companion in LAN (`ThreadingHTTPServer` in un thread), spento di default, attivabile dalle impostazioni | media | ☑ |
| 2 | Abbinamento del dispositivo: codice di 6 cifre monouso valido 2 minuti, scambiato con un token in cookie; QR code (URL + codice) se è installato `python3-qrcode` | media | ☑ |
| 3 | Lettura delle chat dal telefono: elenco e apertura, Markdown reso dal server con `md.py` | facile | ☑ |
| 4 | Invio dal telefono: streaming via Server-Sent Events, nuova chat, scelta del modello, pulsante 🧠 | media | ☑ |
| 5 | Sincronizzazione dal vivo: un messaggio scritto dal telefono compare nella finestra desktop, e viceversa | media | ☑ |
| 6 | Pagina mobile unica (HTML + JavaScript senza build) con manifest PWA per l'installazione sulla schermata home | facile | ☑ |
| 7 | Rimozione della condivisione dell'API (#3 della 0.2.4) | facile | ☑ |
| 8 | Ricerca web dal telefono: interruttore 🌐 nella barra di scrittura | facile | ☑ |
| 9 | Rinomina ed elimina chat dal telefono, con conferma per l'eliminazione | facile | ☑ |
| 10 | Allegati dal telefono: foto dalla fotocamera o dalla galleria, file di testo e PDF | media | ☑ |

## Ordine di lavoro

1. **Fase 0 (#0):** da sola, prima di qualsiasi funzione web. Si chiude solo con l'intera suite attuale verde (unit, `repro_qthread_crash`, `gui_smoke`, `gui_features`), senza cambiamenti visibili per l'utente.
2. **Fase 1 (#1, #2, #3):** server, abbinamento e sola lettura. Verifica tutta l'infrastruttura senza toccare la generazione.
3. **Fase 2 (#4):** invio e streaming.
4. **Fase 3 (#5):** sincronizzazione dal vivo.
5. **Fase 4 (#6, #7):** PWA e rimozione della condivisione API.
6. **Fase 5 (#8, #9, #10):** il telefono non si limita a scrivere. #9 e #10 subito; #8 dopo il merge di `main` (0.2.6), che cambia proprio la ricerca web (messaggio in chat prima della ricerca, provider di riserva). Merge fatto il 2026-10-07: le correzioni della 0.2.6 che in `main` vivono in `MainWindow` (suggerimento «think» solo con `think: false`, messaggio in chat prima della ricerca, portachiavi solo per Ollama, provider di riserva con la sua nota) sono riscritte in `ChatEngine`, con i test in `tests/engine_test.py`.

## Decisioni di progetto

**Accesso da altri dispositivi solo tramite la companion autenticata.** La condivisione dell'API (#3 della 0.2.4) viene rimossa: spawn di `ollama serve`, sonde e indicatore 🔗. L'API di Ollama non ha autenticazione, e tenerla aperta annullerebbe il vantaggio principale della companion, cioè poter lasciare Ollama in ascolto solo su `127.0.0.1`. Chi vuole comunque usare client di terze parti configura il servizio di sistema (`systemctl edit ollama` con `OLLAMA_HOST=0.0.0.0`) sotto la propria responsabilità; il README documenta la procedura. Scartata: mantenere entrambe le vie.

**#0 — regola dei thread.** Il thread HTTP non tocca mai lo stato. Inoltra le richieste al `ChatEngine` nel thread Qt principale con segnali in coda, e riceve le risposte tramite una coda thread-safe. Solo il motore legge e scrive le conversazioni.

**#1 — server.** Libreria standard (`http.server`), nessuna dipendenza nuova. Ascolta sulla LAN solo quando l'opzione è attiva. Niente risorse esterne (CDN): la pagina deve funzionare anche senza internet.

**#2 — abbinamento.**
- Il codice di 6 cifre è il meccanismo di base, sempre disponibile. Il QR code è un'aggiunta che compare solo se `python3-qrcode` è installato (`Recommends` nel `.deb`, `optdepends` su AUR). Il QR viene disegnato con `QPainter` dalla matrice di `get_matrix()`, senza Pillow né QtSvg.
- Il token di sessione viaggia in un cookie `HttpOnly; SameSite=Strict`, mai nell'URL. Un pulsante «Revoca dispositivi» lo rigenera e scollega tutti i dispositivi.
- Limite ai tentativi falliti sul codice di abbinamento.
- **Limite noto, da documentare:** solo HTTP in LAN, quindi chi è sulla stessa Wi-Fi può intercettare il cookie. Accettabile su una rete domestica WPA2/3. Un HTTPS con certificato autofirmato è rinviato a una versione successiva.

**#3 — Markdown reso dal server.** Il browser riceve HTML prodotto da `md.py`, che ha già escape e test contro le iniezioni. Non si scrive un secondo renderer in JavaScript. Durante lo streaming il testo arriva grezzo e la resa Markdown si applica a intervalli e alla fine.

**#4 — concorrenza e perimetro.** Una sola generazione alla volta, globale: con 6 GB di VRAM due generazioni parallele non hanno senso. Una richiesta del telefono mentre il desktop sta generando riceve «occupato», e la pagina lo mostra. Il pulsante 🧠 è incluso (un booleano nella richiesta); il blocco «Pensiero» compare richiuso.

**#10 — allegati dal telefono.** Il telefono non indica mai un percorso: carica il file con `POST /api/upload` (corpo binario, `Content-Type` del file, mai uno dei tipi «semplici» dei moduli HTML) e riceve un id. All'invio passa gli id; il server li traduce nei file che ha salvato lui, nella cartella privata degli allegati. Limiti: 20 MB per file (come `MAX_IMAGE_BYTES`), solo i tipi che `context.classify` riconosce (immagini, testo, PDF), nome ripulito. Le foto vengono ridotte sul telefono (lato lungo 1600 px, JPEG) prima del caricamento. I file caricati e mai inviati si cancellano dopo un'ora o all'arresto del server.

**Fuori perimetro nella 0.3:** impostazioni dal telefono (riguardano il PC). Rinviati alla 0.3.1: copia di un messaggio, gestione dei modelli dal telefono.

## Test

- `ChatEngine` testato senza widget Qt: generazione con un finto server Ollama, persistenza, stop.
- Server testato con `urllib` su una porta casuale: endpoint, streaming SSE, risposta «occupato».
- Autenticazione con casi negativi: codice errato, scaduto o già usato, cookie mancante, token revocato, limite ai tentativi.
- Sincronizzazione: un messaggio inviato via HTTP produce l'aggiornamento della finestra (test offscreen).
- Prima della fase 4: verifica che la rimozione della #3 non lasci impostazioni orfane (`share_api`, `share_bind`, `share_port` ignorate o migrate).

Il bump di versione resta al momento del rilascio (fonte unica: `olladesk/__init__.py`).

## Rinviato alla 0.3.1

| Funzionalità | Note |
|---|---|
| Copia di un messaggio dal telefono (⧉) | su HTTP l'API Clipboard non è disponibile: serve il ripiego con selezione del testo |
| Gestione dei modelli dal telefono (scarica ed elimina) | fattibile con i worker esistenti; scaricare GB dal telefono ha poco senso, priorità bassa |
| Pulizia degli allegati all'eliminazione di una chat | i file in `attachments/` (foto dal telefono in `companion/`, immagini incollate sul PC) restano anche dopo l'eliminazione della chat; da rimuovere quando nessun'altra chat li usa |

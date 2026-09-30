# OllaDesk

**OllaDesk** è un'applicazione desktop nativa per Linux per interagire con e
gestire i modelli LLM locali tramite [Ollama](https://ollama.com), realizzata
con **Qt / PySide6**. Leggera, in stile ChatGPT, pensata per Debian SID con
KDE Plasma.

> *A native Linux desktop application for interacting with and managing local
> LLMs through Ollama, designed with Qt/PySide6.*

Licenza: **GPL-3.0** · Python 3.10+ · PySide6 6.6+

## Funzionalità

- **Chat in stile ChatGPT**: bolle utente/assistente, risposta in *streaming*
  token per token, pulsante per interrompere la generazione, statistiche
  (token, tok/s, durata), rendering Markdown (blocchi di codice, elenchi,
  grassetti, link…), pulsante di copia per ogni risposta.
- **Cronologia conversazioni** nella barra laterale: nuova chat, selezione,
  rinomina (tasto destro) ed eliminazione; persistenza su disco.
- **Allegati come contesto** (📎 nell'input): file di testo e codice (inlinati
  nel messaggio), PDF (richiede il pacchetto opzionale `pypdf`) e immagini
  (inviate in base64 ai modelli visione: llava, llama3.2-vision, gemma3…).
  Gli allegati compaiono come chip sopra l'input e nella bolla del messaggio.
- **Ricerca web** (🌐 nell'input, grigia da spenta e blu da attiva): prima di
  rispondere, l'app cerca sul web e allega i risultati al contesto; la risposta
  cita le fonti. **Provider a scelta** nelle impostazioni:
  - *DuckDuckGo* — senza chiave API (può essere temporaneamente bloccato dalla
    sua protezione anti-bot: in quel caso l'app lo segnala chiaramente);
  - *Ollama Cloud* — endpoint ufficiale `ollama.com/api/web_search`, richiede
    la chiave API dell'account ollama.com;
  - *SearXNG* — la tua istanza personale (es. `http://localhost:8888`), via
    API JSON: ideale in locale. L'istanza deve avere l'output JSON abilitato —
    in `settings.yml` di SearXNG:

    ```yaml
    search:
      formats:
        - html
        - json
    ```

  Come query viene usata solo la prima riga del messaggio (massimo 200
  caratteri), non l'intero testo. Il numero di risultati è configurabile. I modelli non chiamano il tool da
  soli: i risultati vengono iniettati nel prompt dalla GUI (verificato e2e).
- **Gestione modelli** (pulsante «Modelli» nella sidebar, Ctrl+M): elenco dei
  modelli installati con dimensione/parametri/quantizzazione, **scaricamento**
  di nuovi modelli con barra di avanzamento e annullamento, ed **eliminazione**.
- **Aggiornamenti Ollama** (Impostazioni → Interfaccia): confronto tra la
  versione del server e l'ultima release su GitHub, con **aggiornamento in
  un clic** tramite lo script ufficiale (eseguito con `pkexec`, quindi con la
  password di amministratore di KDE).
- **Impostazioni → scheda «Interfaccia»**: URL del server Ollama, tema
  scuro/chiaro/**sistema** (segue KDE Plasma, anche a caldo quando lo cambi),
  dimensione del carattere, streaming on/off, invio con Invio, orario nei
  messaggi, quantità di contesto inviata al modello, prompt di sistema
  predefinito, risultati ricerca web.
- **Impostazioni → scheda «Parametri modelli»**: profilo di parametri separato
  per ogni modello (temperatura, top_k, top_p, min_p, num_ctx, num_predict,
  penalità di ripetizione/presenza/frequenza, seed, sequenze di stop, mirostat,
  num_gpu, num_thread…). Solo i parametri spuntati vengono inviati a Ollama:
  gli altri restano ai valori predefiniti del server. Ogni profilo può essere
  **salvato** e **ripristinato ai predefiniti** con un clic.
- Stato della connessione sempre visibile (verde/rosso) con ricontrollo
  automatico ogni 30 s.

## Requisiti

- Python 3.10+ con PySide6
- Un server Ollama in esecuzione (`ollama serve`) con almeno un modello

## Installazione su Debian

### Dal pacchetto .deb (consigliata)

Scarica `olladesk_<versione>_all.deb` dall'ultima
[release su GitHub](https://github.com/gradia64/OllaDesk/releases/latest)
e installalo: apt risolve da solo le dipendenze (Python 3.10+ e i moduli
PySide6 di Qt core/gui/widgets):

```bash
sudo apt install ./olladesk_0.2.1_all.deb
```

Il pacchetto installa il launcher `/usr/bin/olladesk`, la voce «OllaDesk»
nel menu applicazioni con la relativa icona, la manpage e il changelog;
la disinstallazione è `sudo apt remove olladesk`.

Per costruire il pacchetto dai sorgenti (serve `dpkg-deb`, presente su
qualunque Debian):

```bash
scripts/build-deb.sh        # produce dist/olladesk_<versione>_all.deb
```

### Dai sorgenti

Con i pacchetti di Debian:

```bash
sudo apt install python3-pyside6
```

oppure via pip (ambiente virtuale):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

poi avvia dalla cartella del progetto:

```bash
python3 main.py
# oppure
python3 -m olladesk
```

### Come pacchetto Python (pipx/pip)

```bash
pipx install .            # oppure: pip install --user .
olladesk                  # entry point creato da pyproject.toml
```

Nel `.desktop` puoi allora usare `Exec=olladesk` al posto del percorso assoluto.

### Integrazione con KDE Plasma (menu applicazioni)

```bash
cp olladesk.desktop ~/.local/share/applications/
install -Dm644 olladesk/assets/olladesk.svg ~/.local/share/icons/hicolor/scalable/apps/olladesk.svg
update-desktop-database ~/.local/share/applications
```

Il `.desktop` usa `Exec=olladesk` (l'entry point installato con `pipx`/`pip`)
e `Icon=olladesk` (l'icona copiata qui sopra nel tema `hicolor`). Se avvii
l'app dai sorgenti senza installarla, sostituisci `Exec=` con
`python3 /percorso/di/OllaDesk/main.py`.

## Scorciatoie

| Scorciatoia | Azione                          |
|-------------|---------------------------------|
| `Ctrl+N`    | Nuova conversazione             |
| `Ctrl+B`    | Mostra/nascondi la sidebar      |
| `Ctrl+M`    | Gestione modelli (download/rimozione) |
| `Ctrl+,`    | Apri le impostazioni            |
| `Invio`     | Invia il messaggio (attivabile) |
| `Ctrl+Invio` | Invia il messaggio (sempre)    |
| `Shift+Invio` | A capo                        |

## Parametri dei modelli

Nella scheda «Parametri modelli» ogni riga ha una casella di
personalizzazione: **solo i parametri spuntati** vengono inclusi nell'opzione
`options` della richiesta a `/api/chat`; gli altri restano ai valori di
default di Ollama, quindi «Ripristina predefiniti» equivale a non inviare più
nessuna personalizzazione.

I profili sono salvati per-modello in `~/.config/olladesk/model_params.json`
e vengono applicati automaticamente al modello selezionato nella chat.

## File di configurazione

Tutto è salvato in `$XDG_CONFIG_HOME/olladesk` (di default
`~/.config/olladesk`). Se esiste una configurazione del vecchio nome
(`~/.config/ollama-gui`), viene migrata automaticamente al primo avvio:

| Percorso            | Contenuto                          |
|---------------------|------------------------------------|
| `settings.json`     | Impostazioni della GUI (la chiave API viaggia nel portachiavi di KDE, se disponibile) |
| `model_params.json` | Profili di parametri per modello   |
| `chats/index.json`  | Indice delle conversazioni (solo metadati) |
| `chats/<id>.json`   | Una conversazione per file: salvare un messaggio non riscrive l'archivio |
| `attachments/`      | Immagini incollate dagli appunti (cartella privata, permessi 700) |

Il vecchio `chats.json` monolitico viene migrato automaticamente al primo
avvio (lasciato come `chats.json.bak`). Gli allegati sono referenziati per
percorso e il loro testo viene allegato al contesto solo nel turno in cui
vengono inviati.

## Test

Smoke test offscreen (avvia la GUI, chatta davvero con Ollama, salva/ripristina
i parametri e salva screenshot in `/tmp/olladesk_shots`):

```bash
python3 tests/gui_smoke.py      # funzionalità base
python3 tests/gui_features.py   # allegati, ricerca web, modelli, aggiornamenti
python3 tests/gui_errors.py     # percorsi di errore (server offline)
python3 tests/unit_test.py      # unit test delle funzioni pure (senza rete)
```

Gli script `gui_*.py` sono collaudi end-to-end (richiedono Ollama e, per la
ricerca web, la rete) e non vengono raccolti da `pytest`, che esegue solo
`tests/unit_test.py`. La configurazione di prova vive in una cartella
temporanea nuova a ogni esecuzione.

Per leggere i PDF come allegati:

```bash
pip install pypdf    # oppure: sudo apt install python3-pypdf
```

## Struttura del progetto

```
main.py                     punto di ingresso
olladesk/
  app.py                    avvio dell'applicazione
  config.py                 persistenza (settings, parametri, chat) + migrazione
  theme.py                  temi scuro/chiaro/sistema (Fusion + QSS)
  md.py                     Markdown → HTML (subset Qt rich text)
  context.py                allegati: testo/PDF/immagini come contesto
  web_search.py             ricerca web (DuckDuckGo/Ollama Cloud/SearXNG) + worker
  updater.py                controllo release GitHub + comando di aggiornamento
  ollama_client.py          client API Ollama (stdlib) + worker QThread
  main_window.py            finestra principale e orchestrazione streaming
  widgets/
    chat_area.py            area messaggi + input (allegati, ricerca web)
    message.py              bolle dei messaggi
    sidebar.py              elenco conversazioni
    model_params.py         definizioni ed editor dei parametri
    steppers.py             campi numerici con pulsanti tondi −/+
    model_manager.py        dialog scaricamento/eliminazione modelli
    settings_dialog.py      finestra impostazioni (2 schede + aggiornamenti)
tests/gui_smoke.py          collaudo automatico offscreen (base)
tests/gui_features.py       collaudo automatico offscreen (funzionalità extra)
tests/gui_errors.py         collaudo percorsi di errore (offline)
tests/unit_test.py          unit test delle funzioni pure
olladesk.desktop            voce per il menu applicazioni di KDE
```

## Licenza

Questo progetto è distribuito sotto licenza **GNU General Public License
v3.0** — vedi il file [LICENSE](LICENSE).

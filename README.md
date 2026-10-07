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
  grassetti, link…), pulsante di copia per ogni risposta. La colonna dei
  messaggi ha larghezza massima fissa e resta centrata anche quando la
  sidebar è nascosta (Ctrl+B): il contenuto non si stira a tutta finestra.
- **Cronologia conversazioni** nella barra laterale (con la versione di
  OllaDesk nell'intestazione): nuova chat, selezione, rinomina (tasto destro)
  ed eliminazione; persistenza su disco.
- **Allegati come contesto** (📎 nell'input): file di testo e codice (inlinati
  nel messaggio), PDF (richiede il pacchetto opzionale `pypdf`) e immagini
  (inviate in base64 ai modelli visione: llava, llama3.2-vision, gemma3…).
  Gli allegati compaiono come chip sopra l'input e nella bolla del messaggio.
- **Ragionamento (thinking)**: con i modelli che lo supportano (qwen3,
  deepseek-r1…) il pulsante 🧠 dell'input abilita o disattiva il ragionamento
  e il pensiero del modello compare nella bolla in un blocco «💭 Pensiero»
  in streaming, richiudibile all'arrivo della risposta e salvato nella
  conversazione. Finché il modello pensa non si vedono solo i puntini: la
  bolla mostra «sta pensando…» e poi il testo del ragionamento.
- **Companion web** (voce «📱 Companion» della barra laterale, `Ctrl+D`;
  spenta di default): una
  pagina per smartphone e tablet nella rete locale, con le stesse
  conversazioni del PC. Dal telefono si leggono le chat, se ne apre una
  nuova o si continua una esistente: si sceglie il modello, si accende o
  spegne il ragionamento (🧠) e la ricerca web (🌐, con il provider scelto
  sul PC; a ogni apertura della pagina riparte spenta e, finché è accesa,
  la barra di scrittura lo ricorda) e la risposta arriva in streaming, con il
  pulsante ■ per interromperla. Il PC genera una risposta alla volta: se
  sta già rispondendo, il telefono lo segnala e attende. PC e telefono
  restano sincronizzati dal vivo: un messaggio scritto da una parte compare
  sull'altra, l'elenco delle chat sul telefono si aggiorna da solo e un
  pallino indica la conversazione che sta rispondendo. Durante una risposta
  il PC resta libero di aprire altre conversazioni: la risposta continua in
  background e si ritrova riaprendo la sua chat.
  - **Dal telefono:** allegati con 📎 (foto dalla fotocamera o dalla
    galleria, file di testo e PDF, fino a 20 MB ciascuno): le foto vengono
    ridotte sul telefono prima del caricamento e il PC le passa ai modelli
    che leggono le immagini. Dal menu «⋯» di una chat si rinomina o si
    elimina la conversazione, anche dal PC. Restano solo sul PC le
    impostazioni e la gestione dei modelli.
  - **Finestra «Companion»:** raccoglie tutto in un posto: attivazione e
    porta, stato e indirizzo (con «Copia indirizzo»), abbinamento e
    dispositivi. Quando è attiva, anche il pulsante 📱 della barra superiore
    apre la stessa finestra.
  - **Abbinamento:** nella finestra compaiono l'indirizzo, un codice di 6
    cifre valido 2 minuti e una sola volta e, se
    è installato `python3-qrcode` (`python-qrcode` su Arch), un QR code da
    inquadrare. Il telefono riceve un cookie di sessione; «Revoca
    dispositivi» li scollega tutti (se ne ricordano al massimo 20).
  - **Sulla schermata Home:** dal menu del browser «Aggiungi a schermata
    Home» la pagina diventa un'icona con il logo di OllaDesk. Su iPhone e
    iPad si apre a schermo intero, senza barra degli indirizzi, ma ha
    cookie separati da Safari: l'abbinamento va ripetuto la prima volta che
    la si apre dall'icona. Su Android Chrome crea un collegamento (l'app
    installabile vera richiede HTTPS, rinviato a una versione successiva).
  - **Firewall:** il PC deve accettare la porta (predefinita 8765) **dalla
    sola rete locale**, es. firewalld: `sudo firewall-cmd --permanent
    --add-rich-rule='rule family=ipv4 source address=192.168.1.0/24 port
    port=8765 protocol=tcp accept'` e `sudo firewall-cmd --reload`; analogo
    con `ufw allow from 192.168.1.0/24 to any port 8765 proto tcp`.
    Sostituisci `192.168.1.0/24` con la sottorete della tua LAN (la vedi con
    `ip -4 addr`).
  - **Limite noto:** il collegamento è HTTP in chiaro, quindi chi è sulla
    stessa Wi-Fi può intercettare il cookie. Va bene su una rete domestica
    protetta (WPA2/WPA3); non aprire la porta sul router. Nessuna risorsa
    esterna: la pagina funziona anche senza internet.
- **Client Ollama di terze parti** (app per smartphone che parlano con
  l'API di Ollama): la condivisione dell'API delle versioni 0.2.x è stata
  rimossa nella 0.3, perché l'API di Ollama non ha autenticazione e la
  companion web sì. Chi vuole comunque esporre Ollama in rete lo configura
  nel servizio di sistema, **sotto la propria responsabilità**:
  `sudo systemctl edit ollama`, poi nella sezione `[Service]` la riga
  `Environment="OLLAMA_HOST=0.0.0.0"` e `sudo systemctl restart ollama`
  (con la stessa regola del firewall per la porta 11434). Chi raggiunge la
  porta può usare i modelli e la GPU: per l'accesso da fuori casa meglio
  una VPN (es. Tailscale) o un tunnel SSH.
- **Icona nella tray**: la finestra si riduce nell'area di notifica e si
  ripristina con un clic sull'icona (menu: Mostra/nascondi, Esci);
  facoltativamente la chiusura (X) riduce nella tray invece di uscire,
  così la companion web e le generazioni in corso restano attive.
- **Gestione modelli** (pulsante «Modelli» nella sidebar, Ctrl+M): elenco dei
  modelli installati con dimensione/parametri/quantizzazione, **scaricamento**
  di nuovi modelli con barra di avanzamento e annullamento, ed **eliminazione**.
  Il modello selezionato per la conversazione è contrassegnato con «●».
- **Ricerca web** (🌐 nell'input, grigia da spenta e blu da attiva): prima di
  rispondere, l'app cerca sul web e allega i risultati al contesto. Sotto la
  risposta il blocco richiudibile **«🌐 Fonti»** elenca i risultati usati, con
  la stessa numerazione [1], [2]… che vede il modello (anche sul telefono e
  nelle conversazioni salvate in precedenza). **Provider a scelta** nelle
  impostazioni:
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

    Se l'istanza risponde senza risultati perché i suoi motori non hanno
    risposto (tipico a connessioni fredde), l'app riprova una volta e poi
    elenca i motori in errore: di solito aiuta alzare
    `outgoing.request_timeout` o cambiare motori nel `settings.yml`. Se
    duckduckgo o brave vengono bloccati (CAPTCHA, «too many requests»),
    disattivali e attiva bing o startpage:

    ```yaml
    outgoing:
      request_timeout: 6.0
    engines:
      - name: bing
        disabled: false
      - name: startpage
        inactive: false
        disabled: false
      - name: duckduckgo
        disabled: true
    ```

  Un **provider di riserva** facoltativo (predefinito: nessuno; mai lo
  stesso del principale) viene
  provato quando il principale fallisce o non trova nulla, e una nota in
  chat dice quale ha risposto. Il messaggio compare in chat subito, mentre
  la ricerca è in corso.
  Come query viene usata solo la prima riga del messaggio (massimo 200
  caratteri), non l'intero testo. Il numero di risultati è configurabile. I modelli non chiamano il tool da
  soli: i risultati vengono iniettati nel prompt dalla GUI (verificato e2e).
- **Aggiornamenti Ollama** (Impostazioni → Interfaccia): confronto tra la
  versione del server e l'ultima release su GitHub, con **aggiornamento in
  un clic** tramite lo script ufficiale (eseguito con `pkexec`, quindi con la
  password di amministratore di KDE).
- **Aggiornamenti di OllaDesk**: all'avvio, al massimo una volta al giorno,
  l'app controlla su GitHub se è uscita una nuova versione e, in quel caso,
  mostra un avviso nella barra superiore con le istruzioni adatte a come è
  stata installata (.deb, AUR, pip o sorgenti). Solo un avviso: nessun
  download né installazione automatica. Si può saltare una versione, verificare
  a mano o disattivare il controllo in Impostazioni → Interfaccia.
- **Impostazioni → scheda «Interfaccia»**: URL del server Ollama, tema
  scuro/chiaro/**sistema** (segue KDE Plasma, anche a caldo quando lo cambi),
  dimensione del carattere, streaming on/off, invio con Invio, orario nei
  messaggi, icona nella tray, quantità di contesto
  inviata al modello, prompt di sistema predefinito, risultati ricerca web.
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

## Installazione su Arch Linux (AUR)

Il pacchetto [`olladesk`](https://aur.archlinux.org/packages/olladesk) è su
AUR. Con un helper:

```bash
yay -S olladesk
```

oppure a mano:

```bash
git clone https://aur.archlinux.org/olladesk.git && cd olladesk
curl -LO https://github.com/gradia64/OllaDesk/releases/latest/download/olladesk-release-key.asc
gpg --import olladesk-release-key.asc   # impronta: vedi «Verifica delle firme»
makepkg -si
```

Dalla 0.2.5 il PKGBUILD clona da GitHub il tag della versione e makepkg ne
verifica la firma GPG (`?signed` e `validpgpkeys` nel PKGBUILD): se la chiave
di release non è nel tuo portachiavi, o è una copia precedente al 03/10/2026
senza le sottochiavi nuove, la build si ferma. Importala (o reimportala) dal
file `olladesk-release-key.asc` allegato a ogni release
(`gpg --import olladesk-release-key.asc`) dopo averne controllato l'impronta.
Ogni release include anche il pacchetto già costruito
(`olladesk-<versione>-1-any.pkg.tar.zst`, installabile con `sudo pacman -U`).

## Installazione su Debian

### Dal pacchetto .deb (consigliata)

Scarica `olladesk_<versione>_all.deb` dall'ultima
[release su GitHub](https://github.com/gradia64/OllaDesk/releases/latest)
e installalo: apt risolve da solo le dipendenze (Python 3.10+ e i moduli
PySide6 di Qt core/gui/widgets/network):

```bash
sudo apt install ./olladesk_*_all.deb
```

Il pacchetto installa il launcher `/usr/bin/olladesk`, la voce «OllaDesk»
nel menu applicazioni con la relativa icona, la manpage e il changelog;
la disinstallazione è `sudo apt remove olladesk`.

### Verifica delle firme

Dalla 0.2.3 ogni file della release ha una firma GPG staccata (`.sig`) fatta
con la chiave di release del progetto, pubblicata come
`olladesk-release-key.asc` nella release e in `packaging/` nel repository.
Dalla 0.2.5 firmano due sottochiavi della stessa chiave, con ruoli separati:
una i tag git (solo il maintainer), l'altra gli allegati (la CI). L'impronta
da controllare resta quella della chiave primaria:

```
gradia (OllaDesk release signing) <gradia@disroot.org>
ed25519  5B16 6C1B 4AD7 428C 74A0  7D5B A337 0987 A057 6694
```

Dopo l'import controlla che l'impronta mostrata da gpg coincida, poi:

```bash
gpg --import olladesk-release-key.asc
gpg --verify olladesk_0.3.0_all.deb.sig olladesk_0.3.0_all.deb
# oppure tutto in una volta:
gpg --verify SHA256SUMS.sig SHA256SUMS && sha256sum -c --ignore-missing SHA256SUMS
```

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
| `Ctrl+D`    | Companion web (telefono e tablet) |
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
python3 tests/repro_qthread_crash.py  # chiusura dialoghi con worker bloccati (senza rete)
python3 tests/release_tools_test.py   # script di rilascio: firma dei tag e degli allegati (senza rete)
```

Gli script `gui_*.py` sono collaudi end-to-end (richiedono Ollama e, per la
ricerca web, la rete). `pytest` raccoglie solo `tests/unit_test.py`;
gli altri script senza rete (`release_tools_test.py`, `engine_test.py`, i
test della companion e `sync_test.py`) hanno il loro runner e si eseguono
con `python3` (`tests/conftest.py`). La configurazione di prova vive in una
cartella temporanea nuova a ogni esecuzione.

```bash
python3 tests/offline_app_update.py   # controllo aggiornamenti con finto GitHub (senza rete)
python3 tests/engine_test.py          # motore di chat con finto Ollama (senza rete)
python3 tests/companion_test.py       # companion web: server, abbinamento, lettura (senza rete)
python3 tests/companion_send_test.py  # companion web: invio e streaming SSE (senza rete)
python3 tests/sync_test.py            # sincronizzazione telefono-PC dal vivo (senza rete)
```

La CI (`.github/workflows/release.yml`) esegue unit test e test offline a ogni
push e pull request; sui tag `v*` costruisce tarball, `.deb` e pacchetto Arch,
li firma e li pubblica (release GitHub e AUR). Dettagli per chi pubblica:
[packaging/README.md](packaging/README.md).

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
  updater.py                aggiornamenti di Ollama (release GitHub + pkexec)
  app_update.py             aggiornamenti di OllaDesk (solo controllo e avviso)
  ollama_client.py          client API Ollama (stdlib) + worker QThread
  netinfo.py                indirizzi di rete locale (per la companion web)
  engine.py                 motore di chat: conversazioni, generazione, salvataggio
  workers.py                registro dei worker QThread attivi e in arresto
  companion.py              companion web: server HTTP in LAN e abbinamento
  web/                      pagina mobile della companion (HTML, CSS, JS, manifest e icone)
  main_window.py            finestra principale, client del motore di chat
  widgets/
    chat_area.py            area messaggi + input (allegati, ricerca web)
    message.py              bolle dei messaggi
    sidebar.py              elenco conversazioni
    model_params.py         definizioni ed editor dei parametri
    steppers.py             campi numerici con pulsanti tondi −/+
    model_manager.py        dialog scaricamento/eliminazione modelli
    settings_dialog.py      finestra impostazioni (2 schede + aggiornamenti)
    companion_dialog.py     finestra della companion: servizio, abbinamento, dispositivi
tests/gui_smoke.py          collaudo automatico offscreen (base)
tests/gui_features.py       collaudo automatico offscreen (funzionalità extra)
tests/gui_errors.py         collaudo percorsi di errore (offline)
tests/unit_test.py          unit test delle funzioni pure
tests/repro_qthread_crash.py regressione: dialoghi distrutti con worker bloccati
tests/offline_app_update.py controllo aggiornamenti di OllaDesk (finto GitHub)
tests/engine_test.py        motore di chat (finto Ollama)
tests/companion_test.py     companion web (server, abbinamento, lettura)
tests/companion_send_test.py companion web (invio, streaming SSE, stop)
tests/sync_test.py          sincronizzazione telefono-PC dal vivo
tests/fake_ollama.py        finto server Ollama per i test offline
tests/companion_client.py   client HTTP/SSE per i test della companion
tests/release_tools_test.py script di rilascio (chiavi e repository temporanei)
olladesk.desktop            voce per il menu applicazioni di KDE
packaging/                  file comuni .deb/Arch, modello PKGBUILD, chiave di release
scripts/                    build (.deb, sorgenti, Arch), firma, pubblicazione AUR, icone web
```

## Licenza

Questo progetto è distribuito sotto licenza **GNU General Public License
v3.0** — vedi il file [LICENSE](LICENSE).

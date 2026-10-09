# `olladesk/updater.py`

**Scopo del modulo:**
Controlla e installa gli aggiornamenti di Ollama (il server, non OllaDesk: per quello c'è `app_update.py`).

- **Controllo versione:** recupera l'ultima release pubblicata su GitHub (`api.github.com`). Il confronto con la versione del server locale lo fa chi chiama, con `is_newer`.
- **Aggiornamento:** scarica in memoria lo script ufficiale `install.sh`. Il modulo **non lo esegue**: chi chiama (`widgets/settings_dialog.py`) lo passa via stdin al comando restituito da `update_command_stdin()`, cioè `pkexec sh -s`. La password di amministratore viene chiesta da Polkit.
- Nessun file temporaneo viene letto da root: uno script in `/tmp` resterebbe di proprietà dell'utente mentre viene eseguito come root.

## Costanti

| Nome | Valore | Uso |
|---|---|---|
| `RELEASES_URL` | `https://api.github.com/repos/ollama/ollama/releases/latest` | ultima release di Ollama |
| `INSTALL_SCRIPT_URL` | `https://ollama.com/install.sh` | script ufficiale di installazione |
| `INSTALL_CMD` | `curl -fsSL https://ollama.com/install.sh \| sh` | comando mostrato all'utente per l'aggiornamento manuale, quando `pkexec` manca o il download fallisce |
| `_LOCAL_HOSTS` | `localhost`, `127.0.0.1`, `::1`, `0.0.0.0`, `localhost.localdomain` | nomi considerati locali |

## Funzioni

### `parse_version(v: str) -> tuple[int, ...]`
Estrae i gruppi di cifre dalla stringa e ne tiene **al massimo 4**, come interi. Per esempio `"v0.12.3-rc1"` diventa `(0, 12, 3, 1)`. Con una stringa vuota, `None` o senza cifre restituisce `(0,)`.

### `is_newer(latest: str, current: str) -> bool`
`True` se `parse_version(latest) > parse_version(current)`. Il confronto è tra tuple, quindi elemento per elemento.

### `is_local_host(host: str) -> bool`
`True` se l'URL del server Ollama punta a questa macchina. Il pannello impostazioni lo usa per disattivare «Aggiorna ora» con un server remoto, perché il pulsante aggiornerebbe l'host sbagliato.
- Accetta anche un indirizzo senza schema (`127.0.0.1:11434`): in quel caso aggiunge `http://` prima di analizzarlo.
- È locale se il nome host, in minuscolo, è in `_LOCAL_HOSTS` o inizia con `127.`. Il secondo caso copre Debian, che associa il nome macchina a `127.0.1.1`.
- Altrimenti è locale se coincide con `socket.gethostname()` o `socket.getfqdn()`.
- Un URL non valido (`ValueError`) restituisce `False`.

### `pkexec_available() -> bool`
`True` se `pkexec` è nel `PATH`.

### `update_command_stdin() -> list[str] | None`
Restituisce `[<percorso assoluto di pkexec>, "sh", "-s"]`: lo script arriva dallo stdin di `sh`. Se `pkexec` non è installato restituisce `None`.

## Classi

Entrambe sono `QThread`: la rete viene interrogata in `run()`, fuori dal thread della UI, e il risultato arriva tramite segnali.

### `UpdateCheckWorker(QThread)`
Scarica `RELEASES_URL` con timeout di 10 s e intestazioni `User-Agent: olladesk` e `Accept: application/vnd.github+json`, poi legge `tag_name`.

| Segnale | Argomento | Quando |
|---|---|---|
| `ready` | `str` | versione più recente, senza la `v`/`V` iniziale. È una stringa vuota se la risposta non contiene `tag_name`. |
| `failed` | `str` | qualsiasi eccezione (rete, JSON…), con il messaggio `impossibile controllare gli aggiornamenti: <errore>` |

### `UpdateDownloadWorker(QThread)`
Scarica `INSTALL_SCRIPT_URL` in memoria con timeout di 30 s e `User-Agent: olladesk`. Non scrive niente su disco.

| Segnale | Argomento | Quando |
|---|---|---|
| `ready` | `bytes` | contenuto dello script |
| `failed` | `str` | eccezione durante il download (messaggio dell'eccezione), oppure contenuto che non inizia con `#!` (`il contenuto scaricato non sembra uno script di installazione`) |

Il controllo su `#!` è una verifica minima per scartare pagine di errore HTML o risposte vuote. Non è una verifica di integrità.

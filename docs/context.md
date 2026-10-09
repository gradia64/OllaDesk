# `olladesk/context.py`

**Scopo del modulo:**
Prepara gli allegati (file di testo/codice, PDF e immagini) e la cronologia da inviare a Ollama.

- **Immagini:** vengono codificate in base64 e inviate nel campo `"images"` del messaggio. Servono i modelli visione di Ollama (llava, llama3.2-vision, gemma3…).
- **Testo/codice:** il contenuto viene inserito nel testo del messaggio come blocco allegato.
- **PDF:** il testo viene estratto solo se è installato il pacchetto opzionale `pypdf`.

Il modulo stima anche l'occupazione della finestra di contesto e sceglie quali messaggi della cronologia inviare.

## Costanti

| Nome | Valore / contenuto | Uso |
|---|---|---|
| `IMAGE_EXTS` | `.png .jpg .jpeg .webp .gif .bmp` | estensioni classificate come immagine |
| `PDF_EXTS` | `.pdf` | estensioni classificate come PDF |
| `TEXT_EXTS` | ~60 estensioni di testo e codice (`.txt`, `.md`, `.py`, `.json`, `.yaml`, `.sh`, `.c`, `.rs`, `.go`, `.sql`, `.diff`, `.desktop`, `.cmake`…) | estensioni classificate come testo |
| `_NO_SUFFIX_FILES` | `.gitignore`, `.env`, `.bashrc`, `.vimrc`, `.tmux.conf`, `dockerfile`, `makefile`, `license`, `readme`… | file riconosciuti come testo dal nome completo (senza distinguere maiuscole e minuscole) |
| `MAX_TEXT_CHARS` | `120_000` | caratteri massimi letti da un file di testo |
| `MAX_IMAGE_BYTES` | 20 MB | dimensione massima di un'immagine allegata |
| `_NATIVE_IMAGE_EXTS` | `.png .jpg .jpeg` | formati inviati così come sono; gli altri vengono convertiti in PNG |

## Funzioni

### `classify(path: Path) -> str`
Restituisce `"text"`, `"image"`, `"pdf"` oppure `"unknown"`.
1. Controlla prima il nome completo del file, in minuscolo, contro `_NO_SUFFIX_FILES`. Così `Makefile` e `LICENSE` risultano `"text"`.
2. Poi controlla l'estensione, in minuscolo, contro `IMAGE_EXTS`, `PDF_EXTS` e `TEXT_EXTS`, in quest'ordine.

### `_to_png(path: Path) -> bytes | None`
Converte un'immagine in PNG con `QImage` di PySide6 e restituisce i byte del PNG. Restituisce `None` in tre casi:
- PySide6 non è importabile;
- Qt non riesce a leggere l'immagine;
- il salvataggio fallisce.

Accetta solo i formati che Qt sa leggere, non qualsiasi formato.

### `image_to_b64(path) -> tuple[str | None, str | None]`
Restituisce `(base64, None)` se va a buon fine, oppure `(None, nota)` se fallisce.
- Se il file supera `MAX_IMAGE_BYTES`, la nota indica la dimensione in MB e l'immagine non viene inviata.
- Se la lettura fallisce (`OSError`), la nota riporta il messaggio di sistema.
- I formati diversi da PNG e JPEG vengono convertiti con `_to_png`. Se la conversione fallisce, la nota dice che il formato non è supportato.

Accetta sia `str` sia `Path`.

### `estimate_tokens(text: str) -> int`
Stima grossolana: `len(text) // 4`, cioè circa 4 caratteri per token. Basta per decidere se mostrare un avviso.

### `context_overflow_note(messages: list[dict], num_ctx: int | None) -> str | None`
Somma le stime di `content` di tutti i messaggi e la confronta con la finestra di contesto.
- **Finestra usata:** il valore di `num_ctx`, cioè la dimensione della finestra configurata per il modello. Se `num_ctx` manca o vale 0, si usa 4096, il predefinito delle versioni recenti di Ollama.
- **Soglia:** l'avviso scatta oltre il 90% della finestra.
- **Messaggio:** spiega che Ollama scarterà l'inizio del prompt. Suggerisce di aumentare «num_ctx» in Impostazioni → Parametri modelli oppure di ridurre allegati e cronologia.

Il controllo serve perché Ollama tronca silenziosamente dall'inizio: si perdono il prompt di sistema e i primi messaggi senza alcun errore.

### `_pdf_text(path: Path) -> tuple[str, str | None]`
Estrae il testo delle prime **50 pagine** con `pypdf.PdfReader`. Le pagine senza testo contano come stringa vuota. In caso di problemi restituisce `("", nota)`:
- se `pypdf` non è installato, la nota suggerisce `pip install pypdf`;
- per qualsiasi eccezione di lettura, la nota riporta l'eccezione.

### `extract_text(path: Path) -> tuple[str, str | None]`
- I PDF, riconosciuti con `classify`, passano a `_pdf_text`.
- Tutti gli altri file vengono letti come testo UTF-8 con `errors="replace"`, compresi quelli `"unknown"`.
- Vengono letti al massimo `MAX_TEXT_CHARS + 1` caratteri, così un file enorme non finisce in memoria.
- Oltre `MAX_TEXT_CHARS` il testo viene tagliato e chiuso dal marcatore `…[troncato a 120000 caratteri]`. Il troncamento non produce una nota.
- Se la lettura fallisce (`OSError`), restituisce `("", nota)`.

### `history_window(messages: list[dict], limit: int) -> list[dict]`
Restituisce gli ultimi `limit` messaggi **precedenti** più il messaggio corrente, cioè l'ultimo della lista. `limit` conta messaggi, non turni.
- Con `limit = 0`, o con un valore negativo trattato come 0, viene inviato comunque l'ultimo messaggio.
- Con una lista vuota restituisce `[]`.

### `build_api_content(msg: dict, include_full: bool) -> tuple[str, list[str]]`
Costruisce il testo del messaggio utente da inviare a Ollama e restituisce `(contenuto, avvisi)`. La base è `msg["display"]`; se manca, si usa `msg["content"]`.

**Con `include_full=True`**, usato solo per l'ultimo turno:
- Gli allegati vengono letti da disco al momento. Le voci di `attachments_meta` che non sono dizionari o non hanno `path` vengono ignorate.
- Il tipo di allegato è `att["kind"]`; se manca, viene calcolato con `classify`.
- Le immagini vengono saltate, perché viaggiano nel campo `"images"`.
- Per gli altri allegati viene chiamata `extract_text`, e la sua nota, se c'è, finisce negli avvisi.
- Se il testo estratto non è vuoto, viene aggiunto in un blocco delimitato da `---` con l'intestazione `Allegato: <nome> (dati non attendibili)`.
- Se il testo è vuoto e non c'è una nota, viene aggiunto l'avviso `nessun testo estratto da <nome>`.
- Se è presente `msg["web_block"]`, viene aggiunto anch'esso tra delimitatori `---`.

**Con `include_full=False`**, usato per i turni precedenti, vengono aggiunti solo segnaposto:
- `[allegati inviati in un turno precedente: …]` con i nomi degli allegati;
- `[una ricerca web era stata allegata in un turno precedente]` se c'era un blocco web.

Così il contesto non cresce a ogni turno e il file della chat resta leggero.

Le parti vengono unite con `"\n"`.

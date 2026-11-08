# Changelog

Le modifiche di OllaDesk che si notano da fuori, versione per versione.
Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/) e le
versioni il [Semantic Versioning](https://semver.org/).

Le note dettagliate di ogni rilascio sono anche in `packaging/release-notes/`. Il dettaglio delle versioni precedenti alla 0.2.3 è disponibile nei tag e nelle release di GitHub.

## [Non rilasciato]

### Correzioni

- **AppImage su X11**: su distribuzioni recenti (Arch, Fedora 45) OllaDesk si chiudeva all'avvio con un errore di segmentazione nelle sessioni X11. L'AppImage portava con sé una libxkbcommon-x11 di Ubuntu 22.04, che non va d'accordo con la libxkbcommon più recente del sistema; ora la usa dal sistema, insieme alle estensioni di libxcb. Su Wayland il problema non c'era.

## [0.3.8] — 2026-11-07

### Novità

- **Scelta del modello all'avvio**: il selettore mostra «Seleziona il modello» invece di scegliere da solo il primo modello dell'elenco. Se invii un messaggio senza averne scelto uno, compare un avviso e il testo resta nel campo; riaprire una conversazione salvata ripristina il modello con cui era nata.

### Correzioni

- **Limite di richieste di GitHub**: i controlli degli aggiornamenti di OllaDesk e di Ollama riconoscono il limite di richieste anonime di GitHub (60 all'ora per indirizzo IP, condiviso con altri programmi o con chi è dietro lo stesso NAT) e dicono a che ora riprovare, invece di un errore generico. Il dialogo di aggiornamento di Ollama non mostra più l'eccezione grezza.

### Note tecniche

- **Sicurezza delle dipendenze**: pypdf passa da 6.19.0 a 6.21.4 nell'AppImage e nel manifest Flatpak, e chiude tutti gli advisory segnalati sulla versione precedente.


## [0.3.7] — 2026-10-28

### Novità

- **Interfaccia rinnovata**: icone a linea (Lucide) al posto di emoji e simboli, che seguono il colore del tema. Nella barra superiore «＋» apre una nuova conversazione anche con la barra laterale chiusa; l'archivio è un pulsante compatto in cima alla barra laterale.
- **Barra laterale**: conversazioni raggruppate per data (Oggi, Ieri, Ultimi 7 giorni, Ultimi 30 giorni, Più vecchie) e un campo di ricerca sui titoli, che ignora maiuscole e accenti e vale anche nell'archivio. Ctrl+K lo apre, Esc lo svuota.
- **Blocchi di codice**: ogni blocco ha una riga con il linguaggio e un pulsante «Copia» che copia solo quel blocco. Il pulsante di copia del messaggio compare al passaggio del mouse.
- **Capacità del modello**: accanto al selettore, dei badge mostrano se il modello scelto accetta immagini, ragiona o usa strumenti.
- **Aggiornamento di Ollama dall'indicatore di stato**: con un server su questo computer, OllaDesk controlla all'avvio (al massimo una volta al giorno) se c'è una versione più recente di Ollama e lo segnala con un badge; un clic apre aggiornamento, modelli e indirizzo del server. Il controllo si può disattivare.
- **Impostazioni a pagine**: un menu a sinistra con Generale, Aspetto, Ricerca web, Parametri modelli, Aggiornamenti, più due pagine nuove:
  - **Aiuto** (anche con F1): le funzioni principali in breve e la tabella delle scorciatoie;
  - **Info**: versione, tipo di installazione, componenti, licenza, impronta della chiave di firma delle release, cartella di configurazione e link utili, con «Copia informazioni di sistema» per le segnalazioni (senza dati personali).
- **Esempi della schermata iniziale**: 24 in 6 categorie, ne compaiono 4 a caso a ogni nuova chat.

### Correzioni

- **settings.json illeggibile**: se il file delle impostazioni era rotto o vuoto, OllaDesk ripartiva dai predefiniti e al primo salvataggio lo sovrascriveva. Ora ne conserva una copia (`settings.json.corrupt-<data>`) e lo dice in chat, con il percorso.
- **Documenti allegati**: nelle tabelle dei `.doc` le righe non finivano più tutte su una sola; nei `.ods` numeri, date e ore arrivano dai valori salvati e non dal testo visualizzato, che dipende dalla lingua; nei `.xlsx` una cella senza riferimento dopo la colonna 256 non sovrascrive più la colonna A; un'ora a zero è 00:00:00.

### Note tecniche

- **Pacchetti**: il plugin SVG di Qt diventa necessario (`qt6-svg-plugins` nel `.deb`, `qt6-svg` su AUR); la licenza ISC di Lucide è inclusa.
- **Test**: la suite non scrive più nella configurazione reale di chi la esegue (cartella temporanea per tutta l'esecuzione, e `config_dir()` rifiuta `~/.config` durante i test). Gli utenti dell'app non ne erano toccati.


## [0.3.6] — 2026-10-25

### Novità

- **Documenti Word, Excel e OpenDocument come contesto**: oltre a testo, PDF e immagini si possono allegare `.docx`, `.doc`, `.xlsx`, `.xls`, `.odt` e `.ods` (anche `.docm` e `.xlsm`), dal PC, dal telefono e dentro una cartella allegata. Il modello riceve il testo; i fogli di calcolo arrivano riga per riga, con le celle separate da tabulazioni, le date in formato ISO e il risultato salvato delle formule. Non serve nessun pacchetto in più. Il formato si riconosce dal contenuto, quindi un `.docx` salvato con il nome `.doc` si legge lo stesso. Un documento molto grande o lento da leggere viene allegato in parte, con un avviso.

### Correzioni

- **Indirizzo di Ollama dopo l'aggiornamento da una 0.2.x**: con la condivisione dell'API attiva, il campo «Server Ollama» poteva restare sulla porta della vecchia istanza di condivisione (di solito 11435), che dalla 0.3 non esiste più: OllaDesk risultava offline finché non lo si correggeva a mano. Ora la migrazione lo riporta a `http://localhost:11434` e lo segnala nella nota in chat. Vale per tutte le forme di indirizzo locale (`localhost`, `127.0.0.1`, `[::1]`, `localhost.localdomain`, anche senza `http://`) e anche quando si copiano i dati della vecchia cartella `ollama-gui`.
- **Trascinare un collegamento web sull'input**: veniva accettato senza fare nulla. Ora il collegamento si inserisce come testo.

### Note tecniche

- **Lettore dei documenti** (`olladesk/office.py`): scritto con la sola libreria standard (zip e XML per i formati moderni, un piccolo lettore OLE2 per `.doc` e `.xls`). Prima del rilascio due revisioni esterne hanno provato file costruiti apposta per bloccarlo: le correzioni limitano tempo, memoria e profondità di lettura, e nessun errore di un file arriva più a bloccare l'invio. Se la lettura degli allegati fallisce in modo imprevisto, il messaggio resta in chat con un avviso e OllaDesk torna pronto per un nuovo invio.
- **Flatpak in preparazione**: il manifest è in `packaging/flatpak/`, con permessi minimi (nessun accesso al filesystem: allegati e cartelle passano dai portali). Il bundle non è ancora tra gli allegati della release né su Flathub. Per i file trascinati dal file manager dentro la sandbox, OllaDesk usa il portale FileTransfer, che il Qt del runtime KDE non gestisce da sé.
- **Test**: nuovi test sui documenti (file di LibreOffice e file costruiti per bloccare il lettore), sulla migrazione dell'indirizzo, sul portale e sul motore che torna pronto dopo un allegato illeggibile.


## [0.3.5] — 2026-10-19

### Novità

- **Interfaccia in cinque lingue**: oltre all'italiano, OllaDesk parla inglese, francese, tedesco e spagnolo, compresa la companion web sul telefono. Segue la lingua del sistema (per le lingue non ancora tradotte usa l'inglese) oppure si sceglie in Impostazioni → Interfaccia → «Lingua»; la scelta vale dal riavvio. Sono tradotti anche i pulsanti standard di Qt (Sì, No, Annulla…), la voce di menu e i metadati AppStream.

### Correzioni

- **Scorrimento durante la risposta**: mentre il modello scriveva non si poteva risalire con la rotella o la barra a rileggere la conversazione, perché ogni aggiornamento riportava la vista in fondo. Ora la vista resta dove la lasci e torna a seguire la risposta quando si scende di nuovo in fondo. Lo stesso vale nella companion web.
- **Cartella come contesto**: i moduli JavaScript `.mjs` e `.cjs` venivano esclusi come «tipo non supportato». Ora sono inclusi, insieme ad altri tipi comuni (`.vue`, `.svelte`, `.swift`, `.dart`, `.cs`, `.proto`, `.tf`, `.nix`, `.lock`…) e a file come `COPYING`, `CHANGELOG` e `PKGBUILD`. L'avviso nomina i file rimasti fuori perché di tipo non supportato.
- **Titoli lunghi nella barra laterale**: si accorciavano a un numero fisso di caratteri, così con lettere larghe la voce usciva dalla colonna e compariva una barra di scorrimento orizzontale. Ora si accorciano con «…» alla larghezza reale della colonna.

### Note tecniche

- **Traduzioni**: cataloghi gettext in `olladesk/locale/*.po`, letti all'avvio senza compilazione, quindi uguali per `.deb`, AUR e AppImage. `scripts/i18n.py update` li allinea ai sorgenti e `scripts/i18n.py check`, nella CI, controlla che siano completi e con gli stessi segnaposto. La variabile `OLLADESK_LANG` impone una lingua (test e screenshot).
- **Screenshot di AppStream in inglese**, come chiede Flathub: `scripts/screenshots.py --lang` sceglie la lingua (inglese di default).
- **README tradotti** in inglese, francese, tedesco e spagnolo, con un controllo nella CI che le cinque versioni restino allineate.
- **Test**: nuovi test per le traduzioni (cataloghi completi, segnaposto, scelta della lingua, testi della companion), per lo scorrimento durante lo stream, per i titoli nella barra laterale e per i nuovi tipi di file nelle cartelle.


## [0.3.4] — 2026-10-12

### Novità

- **AppImage per qualunque distribuzione**: ogni release include `OllaDesk-<versione>-x86_64.AppImage`, un solo file con dentro Python, Qt e le dipendenze facoltative (PDF, portachiavi, QR code), da scaricare, verificare con la firma e avviare senza installare nulla. Serve un sistema x86_64 con glibc 2.34 o più recente (Debian 12, Ubuntu 22.04, Fedora 35 o successive). Con [Gear Lever](https://flathub.org/apps/it.mijorus.gearlever) o [AppImageLauncher](https://github.com/TheAssassin/AppImageLauncher) compare nel menu con la sua icona; il README spiega anche come integrarla a mano. Chat e impostazioni sono nella stessa cartella del `.deb` e del pacchetto AUR. Quando esce una versione nuova, OllaDesk lo segnala e indica il file da scaricare.
- **Cartella come contesto** (📎 → «Cartella…», oppure trascinandola nell'input): un solo allegato «📁 nome/» con i file di testo, codice e PDF che contiene, ciascuno con il percorso relativo. Restano fuori file e cartelle nascosti, dipendenze e cache (`node_modules`, `__pycache__`, `venv`…), ciò che esclude il `.gitignore` in cima alla cartella, i probabili file di credenziali (`*.env`, `credentials.*`, `secrets.*`, `*.pem`, `id_rsa*`…, che si possono comunque allegare uno per uno) e le immagini. Ci sono limiti di file, caratteri e tempo, e un avviso riassume cosa è rimasto fuori. La cartella si legge al momento dell'invio: nella conversazione resta solo il percorso.
- **Archivio delle conversazioni** (tasto destro → «Archivia»): la chat esce dall'elenco senza essere eliminata e si ritrova con «🗄 Archivio (N)» in fondo alla barra laterale, da cui si apre o si ripristina. Un nuovo messaggio la riporta nell'elenco. Facoltativa l'archiviazione automatica all'avvio delle chat ferme da un certo numero di giorni (Impostazioni → Interfaccia, spenta di default). Anche dal telefono, dal menu «⋯» della chat; le archiviate stanno in un riquadro chiuso in fondo all'elenco.

### Cambiamenti

- **Nuovo ID dell'app: `io.github.gradia64.OllaDesk`**. La voce di menu e l'icona si chiamano ora `io.github.gradia64.OllaDesk.desktop` e `io.github.gradia64.OllaDesk.svg` (prima `olladesk.desktop` e `olladesk.svg`), lo stesso nome usato dall'AppImage e dai metadati AppStream. Con il `.deb` e il pacchetto AUR i file vecchi vengono sostituiti dall'aggiornamento; se OllaDesk era fissato nella barra delle applicazioni o tra i preferiti, va fissato di nuovo. Se avevi copiato a mano `olladesk.desktop` e `olladesk.svg` in `~/.local/share`, cancellali per non avere OllaDesk due volte nel menu.
- **Finestra collegata alla voce di menu**: `StartupWMClass` è l'ID dell'app, che è anche la classe della finestra su X11 e l'app_id su Wayland. La barra delle applicazioni mostra l'icona di OllaDesk anche fuori da Plasma.
- **Menu**: OllaDesk compare in Accessori e Sviluppo invece che in Internet.
- **Metadati AppStream** (`io.github.gradia64.OllaDesk.metainfo.xml`), in inglese con traduzione italiana, con screenshot e note delle versioni: servono ai centri software e all'AppImage.

### Correzioni

- **Allegati che bloccavano l'interfaccia**: una pipe o un altro file speciale scelto come allegato teneva ferma la finestra in attesa di dati. Ora i file speciali vengono ignorati, anche dentro una cartella.

### Note tecniche

- **AppImage riproducibile**: stesso commit, stesso SHA-256, in locale e nella CI, che lo scrive nel riepilogo del job. Tutti i download della build sono fissati per SHA-256 e le dipendenze Python installate con `--require-hashes`. `scripts/build-appimage.sh` la costruisce, `packaging/appimage/smoke-test.sh` la prova con il suo Python (test offline, avvio senza schermo e su X11 con `xvfb`); la CI la firma e la allega alla release come gli altri file.
- **Test**: nuovi test per la cartella come contesto (filtri, `.gitignore` confrontato con git, credenziali, limiti), per l'archivio sul PC e sul telefono, per il riconoscimento dell'AppImage negli aggiornamenti e per i blocchi di ricerca web della 0.3.0.
- **Documentazione**: sezione AppImage nel README e in `packaging/README.md`; `scripts/screenshots.py` rigenera gli screenshot di AppStream e `scripts/metainfo-releases.py` le note delle versioni nei metadati.


## [0.3.3] — 2026-10-09

### Correzioni dalla revisione della 0.3.2

- **Allegati dei collegamenti simbolici al sicuro**: se una conversazione citava un collegamento simbolico e un'altra il file a cui punta, eliminando la seconda il file veniva cancellato e il collegamento restava rotto. Ora un collegamento citato protegge anche il file a cui punta.
- **Conversazioni modificate a mano**: un messaggio non valido (non un oggetto, o senza ruolo) impediva di aprire la conversazione sul PC e sul telefono. Ora quei messaggi vengono scartati alla lettura e il resto della conversazione si apre normalmente.
- **Allegati delle conversazioni non salvate**: se il salvataggio di una conversazione era fallito, i suoi allegati potevano essere cancellati eliminando un'altra conversazione. Ora sono protetti anche quelli delle conversazioni rimaste solo in memoria.
- **Avvisi sul telefono non più ripetuti**: su una conversazione appena creata dal telefono, se la connessione cadeva durante la prima risposta, alla riconnessione un avviso come «Ricerca web saltata» compariva una seconda volta. Ora compare una volta sola, e cambiando vista prima che la chat si apra non si ripresenta più tardi.
- **Gestore modelli aggiornato**: se un'operazione finiva mentre l'elenco dei modelli si stava già aggiornando, restava l'elenco di prima fino a «Aggiorna». Ora l'aggiornamento si ripete appena finisce quello in corso.

### Rifiniture

- Allegati: una voce non valida tra gli allegati di un turno precedente, in una conversazione modificata a mano, non blocca più l'invio del messaggio successivo.

### Note tecniche

- **Test**: nuovi test per il collegamento simbolico citato altrove, per le conversazioni non salvate, per i messaggi non validi, per l'id dell'avviso rimandato e la riconnessione con `Last-Event-ID`, per l'elenco dei modelli aggiornato con una richiesta in corso e per gli allegati non validi nei turni precedenti. Il finto Ollama può trattenere una risposta di `/api/tags`.
- **Documentazione**: nuova cartella `docs/` con la descrizione dei moduli `context.py` e `updater.py`.


## [0.3.2] — 2026-10-08

### Correzioni dalla revisione della 0.3.1

- **Allegati condivisi al sicuro**: eliminando una conversazione, un allegato usato anche da un'altra poteva essere cancellato se quell'altra non si leggeva (file danneggiato o modificato a mano). Ora si controllano tutti i file delle conversazioni e, se uno non si legge, non si cancella nessun allegato. I collegamenti simbolici nella cartella degli allegati non vengono mai seguiti.
- **Avvisi sul telefono anche su una chat nuova**: «Ricerca web saltata» e «file non leggibile» non comparivano quando la conversazione nasceva dal telefono. La correzione della 0.3.1 non bastava, perché la pagina apriva la chat dopo gli avvisi; ora li riceve.
- **Modelli, PC e telefono insieme**: il gestore modelli del PC e la vista 🧩 del telefono condividono la stessa operazione. Se ne fa una alla volta, si vede e si annulla da entrambi, e anche dal PC non si elimina un modello mentre il PC risponde. Un invio con un modello in eliminazione viene rifiutato con una spiegazione. Chiudendo il gestore modelli lo scaricamento non si interrompe più: continua e si rivede riaprendolo.
- **Fonti della ricerca web**: titoli, estratti e domanda vengono scritti su una riga sola nel blocco dei risultati, così un testo non può aggiungere una fonte inventata.

### Rifiniture

- Telefono: il riquadro per copiare un messaggio a mano si chiude cambiando vista; dopo uno scaricamento o un'eliminazione l'elenco dei modelli si aggiorna subito.
- Conversazioni modificate a mano: identificativi non validi e blocchi di risultati di tipo inatteso vengono ignorati senza errori.

### Note tecniche

- **Test**: nuovi test per la pulizia degli allegati (chat corrotta, id diverso dal nome, fuori dall'indice, collegamento simbolico), per gli id delle conversazioni, per gli avvisi sul percorso reale della pagina del telefono, per l'invio durante un'eliminazione e per il gestore modelli coordinato con il telefono. Il finto Ollama può tenere in attesa un'eliminazione.


## [0.3.1] — 2026-10-08

### Dal telefono: copia e modelli

- **Copia di un messaggio** (⧉): un pulsante sotto ogni messaggio copia il testo Markdown originale. Su HTTP in LAN la Clipboard API del browser non è disponibile, quindi si prova prima il metodo classico e, se anche quello fallisce, compare un riquadro con il testo già selezionato da copiare con il menu del telefono.
- **Gestione dei modelli** (🧩): dall'elenco delle chat si scaricano nuovi modelli, con percentuale di avanzamento e «Annulla», e si eliminano quelli installati. Lo scaricamento avviene sul PC. Si fa una sola operazione alla volta e non si può eliminare un modello mentre il PC sta rispondendo.

### Fonti della ricerca web

- **Fonti sotto la risposta**: un blocco richiudibile «🌐 Fonti (n)» elenca i link usati dalla ricerca, con la stessa numerazione [n] vista dal modello, sul PC e sul telefono. Si ricavano dai risultati già salvati, quindi compaiono anche nelle chat vecchie; solo link http/https.

### Correzioni

- **Telefono bloccato su ■ dopo lo stop di una ricerca web**: il pulsante torna a ➤ anche quando lo stop arriva durante la ricerca.
- **Avvisi persi su una chat nuova dal telefono** («Ricerca web saltata», «file non leggibile»): ora arrivano anche quando la chat nasce dal telefono.
- **Provider di riserva uguale al principale**: non si può più scegliere come riserva lo stesso provider della ricerca web; un valore già salvato così torna a «Nessuno».
- **Modelli doppi e voci `llamacpp:` di Ollama 0.40.0**: dopo la migrazione dei modelli al nuovo motore Ollama elenca lo stesso nome due volte più una voce interna; OllaDesk le nasconde nel menu, sul telefono e nel gestore modelli, senza toccare nulla su disco ([ollama/ollama#18830](https://github.com/ollama/ollama/issues/18830)).
- **Allegati orfani**: eliminando una conversazione si cancellano anche gli allegati (foto dal telefono, immagini incollate) che nessun'altra conversazione usa; prima restavano su disco.
- **Pacchetti**: la descrizione del pacchetto Arch è allineata a quella del `.deb` (companion web).

### Note tecniche

- **Test**: nuovo `companion_models_test` (scarica, errore, annulla, elimina, casi rifiutati) con un finto Ollama che ora gestisce `/api/pull` e `/api/delete`; test per la pulizia degli allegati e per il campo `text` dei messaggi della companion.


## [0.3.0] — 2026-10-07

### Companion web: OllaDesk dal telefono

- **Le stesse conversazioni su smartphone e tablet**: OllaDesk può servire una pagina web nella rete locale (voce «📱 Companion» della barra laterale, `Ctrl+D`; spenta di default). Dal telefono si leggono le chat del PC, se ne apre una nuova o si continua una esistente con la stessa cronologia, gli stessi parametri e lo stesso prompt di sistema. Si sceglie il modello, si accendono o spengono ragionamento (🧠) e ricerca web (🌐), e la risposta arriva in streaming con il pulsante ■ per interromperla.
- **Sincronizzazione dal vivo**: un messaggio scritto da una parte compare sull'altra mentre la risposta viene generata; l'elenco delle chat sul telefono si aggiorna da solo e un pallino indica la conversazione che sta rispondendo. Il PC genera una risposta alla volta: se è occupato, il telefono lo segnala e attende.
- **Allegati e foto dal telefono** (📎): foto dalla fotocamera o dalla galleria, file di testo e PDF, fino a 20 MB ciascuno. Le foto vengono ridotte sul telefono prima del caricamento e il PC le passa ai modelli che leggono le immagini.
- **Rinomina ed elimina** una conversazione dal menu «⋯» della chat, anche mentre è aperta sul PC.
- **Abbinamento con codice o QR**: la finestra Companion mostra l'indirizzo, un codice di 6 cifre valido 2 minuti e una sola volta e, se è installato `python3-qrcode` (`python-qrcode` su Arch), un QR code da inquadrare. Il telefono riceve un cookie di sessione; «Revoca dispositivi» li scollega tutti.
- **Icona sulla schermata Home**: dal menu del browser «Aggiungi a schermata Home» la pagina diventa un'icona con il logo di OllaDesk (a schermo intero su iPhone e iPad). Nessuna risorsa esterna: la pagina funziona anche senza internet.
- **Finestra «Companion»**: attivazione e porta, stato, indirizzo con «Copia indirizzo», abbinamento e dispositivi in un unico posto, fuori dalle impostazioni.

### Sicurezza della companion

- **Solo dispositivi abbinati**: ogni richiesta richiede il cookie di sessione (`HttpOnly`, `SameSite=Strict`), salvato sul PC solo come impronta SHA-256; il codice di abbinamento accetta al massimo 5 tentativi. Il server rifiuta gli host sconosciuti (protezione dal DNS rebinding), accetta le richieste che modificano dati solo in JSON (o come file binario, per gli allegati), che un modulo HTML di un altro sito non può inviare, e invia una Content Security Policy che non ammette risorse esterne.
- **Limite noto**: il collegamento è HTTP in chiaro, adatto a una rete domestica protetta (WPA2/WPA3). Non aprire la porta sul router; il README spiega come limitarla alla rete locale con firewalld o ufw.
- **Segnalazioni private**: `SECURITY.md` spiega come segnalare una vulnerabilità tramite la segnalazione privata di GitHub.

### Cambiamenti

- **Rimossa la condivisione dell'API Ollama** (Impostazioni → Interfaccia, pulsante 🔗): l'API di Ollama non ha autenticazione, la companion sì. Chi la usava riceve una nota una sola volta all'avvio. Per continuare a usare app di terze parti che parlano con l'API di Ollama, il README spiega come esporre il servizio di sistema con `OLLAMA_HOST`, sotto la propria responsabilità.
- **Risposte in background**: durante una risposta il PC resta libero di aprire altre conversazioni; la risposta continua e si ritrova riaprendo la sua chat.

### Note tecniche

- **Motore di chat separato dalla finestra** (`ChatEngine`): conversazioni, generazione, ricerca web e salvataggio stanno in un oggetto senza widget, di cui la finestra e il server web sono client. Il server HTTP non tocca mai lo stato: inoltra le richieste al thread principale di Qt.
- **Correzioni della 0.2.6 riportate nel motore**: messaggio in chat prima della ricerca web, provider di riserva, portachiavi letto solo per Ollama Cloud, suggerimento sul ragionamento solo con `"think": false`.
- **Test**: nuovi test senza rete per il motore (finto server Ollama), per la companion (abbinamento, lettura, invio, streaming SSE, allegati, rinomina ed eliminazione) e per la sincronizzazione telefono-PC, eseguiti dalla CI a ogni push.


## [0.2.6] — 2026-10-05

### Ricerca web

- **Il messaggio compare subito**: con la ricerca web attiva il messaggio restava nel campo di testo finché la ricerca non finiva (2–3 s con SearXNG), come se l'invio fosse lento. Ora va in chat all'istante e la ricerca parte subito dopo; i risultati vengono allegati al messaggio prima della risposta.
- **Provider di riserva**: in Impostazioni → Interfaccia si può scegliere un secondo provider da provare se il principale fallisce (es. DuckDuckGo bloccato dalla sua protezione anti-bot) o non trova nulla; una nota in chat dice quale ha risposto. Predefinito: nessuno, così la domanda non va mai a un servizio non scelto.
- **SearXNG più tollerante**: quando l'istanza risponde senza risultati perché i suoi motori non hanno risposto (tipico alla prima ricerca, a connessioni fredde), l'app riprova una volta; se non basta, il messaggio elenca i motori in errore e il motivo (es. «brave: timeout, duckduckgo: CAPTCHA»).
- **Niente attese per il portachiavi**: la chiave API viene letta da KWallet solo se si usa Ollama Cloud, non più a ogni ricerca con DuckDuckGo o SearXNG.

### Correzioni

- **Interruzione della ricerca web senza blocchi**: premendo ■ mentre la ricerca web aspettava la risposta del provider, la finestra poteva restare bloccata fino al timeout di rete (misurati quasi 10 s). Ora l'interruzione è immediata, come già per chat, gestione modelli e controllo aggiornamenti dalla 0.2.5.
- **Suggerimento sul ragionamento solo quando serve**: il consiglio «riattiva il pulsante 🧠» compariva in qualunque errore che contenesse la parola «think», anche con il ragionamento attivo. Ora compare solo se la richiesta aveva davvero disattivato il ragionamento (`"think": false`).

### Release firmate e pacchetti

- **Tag «Verified» su GitHub**: la chiave di release è caricata sull'account del maintainer senza la sottochiave della CI, così i tag firmati dal maintainer risultano «Verified» mentre la CI non può produrne. I comandi, anche per proroghe e rotazioni, sono in `packaging/README.md`.
- **Descrizione della release dal repository**: le note di ogni versione stanno in `packaging/release-notes/` e diventano la descrizione della release così come sono; la CI si ferma se mancano.
- **Pacchetto .deb riproducibile**: la dimensione installata dichiarata si calcola dal contenuto e non più dai blocchi del disco, che cambiavano con il filesystem. Lo stesso commit produce ora lo stesso `.deb` in locale e nella CI.
- **SHA256SUMS in ordine fisso**: l'elenco dei checksum non dipende più dalla lingua del sistema su cui si firma.

### Note tecniche

- **Test**: `pytest` raccoglie solo i test unitari (prima dava 10 errori sui test degli script di rilascio, che hanno il loro runner); i test degli script di rilascio verificano il motivo di ogni rifiuto e, in CI, non si saltano più se mancano gpg, git o dpkg-deb; nuovi test di regressione per lo stop della ricerca web, il suggerimento sul ragionamento, l'ordine messaggio/ricerca, il nuovo tentativo di SearXNG e il provider di riserva.
- **CI**: PySide6 fissato a `>=6.6,<7`, come in `pyproject.toml`.


## [0.2.5] — 2026-10-03

### Miglioramenti

- **Condivisione in rete con un Ollama già attivo**: se la porta è servita da un Ollama avviato altrove (es. il servizio di sistema configurato su `0.0.0.0`) e raggiungibile dalla rete, il pulsante 🔗 ora copia gli indirizzi anche in quel caso. La nota «⚠ Condivisione API» compare solo se c'è qualcosa da sistemare (istanza raggiungibile solo da questo PC), una volta per sessione; quando è tutto a posto basta il tooltip di 🔗. Se non c'è nessun indirizzo di rete da copiare, una nota spiega il motivo invece di non fare nulla.
- **Note di sistema chiudibili**: ogni nota in chat (ricerca web non riuscita, avvisi della condivisione…) ha un pulsante ✕ per toglierla. Le note restano transitorie e non vengono salvate nella conversazione.
- **Icona thinking come il globo della ricerca web**: il pulsante 🧠 è ora un cervello disegnato nello stesso stile del globo, grigio da spento e blu da attivo, senza cornice e della stessa dimensione. Mostra lo stato corretto già all'avvio e non dipende più dal font emoji.
- **Icona dell'app più grande nella tray**: tolti i margini interni dell'SVG, l'icona riempie lo spazio come le altre (vale anche per la finestra e il menu applicazioni).
- **README**: se dal telefono la porta condivisa non risponde, la causa più comune è il firewall; esempi di regola limitata alla rete locale per firewalld e ufw.

### Correzioni

- **Interruzione della risposta senza blocchi**: premendo ■ mentre lo stream di Ollama era in pausa la finestra poteva restare bloccata per diversi secondi (misurati 9,8 s). Ora l'interruzione è immediata; lo stesso vale per la gestione modelli e i controlli degli aggiornamenti.
- **Condivisione**: dopo un errore del processo condiviso o un cambio di porta, 🔗 non copia più indirizzi di un server non attivo.

### Release firmate e AUR

- **Firma con due sottochiavi a ruoli separati**: i tag git sono firmati dal maintainer con una sottochiave che non lascia la sua macchina; gli allegati della release (`.sig`, `SHA256SUMS.sig`) con un'altra, la sola presente nella CI. La chiave primaria non è più nei secret di GitHub. L'impronta da controllare non cambia: `5B16 6C1B 4AD7 428C 74A0  7D5B A337 0987 A057 6694`.
- **AUR dal tag firmato**: il PKGBUILD clona il tag `v0.2.5` da GitHub e `makepkg` ne verifica la firma (`?signed` + `validpgpkeys`), invece di scaricare il tarball della release.
- ⚠ **Reimporta la chiave di release**: le due sottochiavi sono nuove, quindi una copia della chiave importata prima di questa release non basta a verificare il tag né gli allegati. Importa `olladesk-release-key.asc` allegato qui (o da keyserver.ubuntu.com) e controlla l'impronta. Le firme delle release precedenti restano valide.

### Note tecniche

- **Pacchetto .deb in xz**: compressione dichiarata esplicitamente; prima il `.deb` costruito dalla CI (Ubuntu) era in zstd e quello costruito su Debian in xz.
- **Pipeline di rilascio**: nuovo job `verify` sulla firma del tag; i secret sono solo nel job di pubblicazione, dentro un environment protetto che richiede l'approvazione del maintainer. Action di GitHub aggiornate a Node 24 e runner fissati su Ubuntu 24.04.
- **README**: tra le dipendenze del `.deb` compare anche il modulo Qt network.


## [0.2.4] — 2026-10-02

### Funzionalità

- **Condivisione dell'API Ollama in rete (stile LM Studio)**: dalle impostazioni OllaDesk può avviare e arrestare `ollama serve` con interfaccia e porta configurabili, così smartphone e tablet sulla stessa rete possono usare i modelli puntando un client Ollama a `http://<IP-del-PC>:11434`. L'indirizzo esatto compare nel pulsante 🔗 della barra superiore (un clic lo copia negli appunti); una sonda rileva un'istanza già attiva (es. servizio systemd) e verifica se è raggiungibile davvero dalla rete; il processo avviato da OllaDesk muore con l'app, e l'API — che non prevede autenticazione — viene aperta solo dopo un avviso esplicito (per l'accesso da fuori casa: VPN o tunnel SSH).
- **Ragionamento (thinking) visibile in chat**: con i modelli che lo supportano (qwen3, deepseek-r1…) il ragionamento scorre in un blocco «💭 Pensiero» dentro la bolla, richiudibile all'arrivo della risposta, salvato nella conversazione e ripristinato alla riapertura. Il pulsante 🧠 dell'input disattiva il ragionamento inviando `"think": false` (nota: gpt-oss accetta solo i livelli low/medium/high).
- **Icona nell'area di notifica (tray)**: clic per mostrare/nascondere la finestra, menu con «Esci»; facoltativamente la chiusura (X) riduce nella tray invece di uscire, così generazioni in corso e condivisione API restano attive. L'uscita è unica e pulita anche con SIGTERM, Ctrl+C e logout di Plasma.
- **Colonna chat centrata**: larghezza massima fissa (~900 px) come nelle chat più note — nascondendo la sidebar (Ctrl+B) il contenuto non si stira a tutta finestra e il prompt resta allineato alla colonna.
- **Versione nell'intestazione della sidebar** e **modello in uso contrassegnato** con «●» nella finestra «Gestione modelli».

### Note tecniche

- **Icona thinking**: tinta grigia da spento e blu da attivo, stessa dimensione del globo della ricerca web.
- **Dipendenza .deb**: aggiunta `python3-pyside6.qtnetwork` (richiesta dalla condivisione in rete).
- **Manpage**: aggiornata con thinking, tray e condivisione in rete.


## [0.2.3] — 2026-10-01

### Aggiornamenti di OllaDesk

- **Avviso di nuova versione**: all'avvio (al massimo una volta al giorno, disattivabile) OllaDesk legge l'ultima release da api.github.com e, se più recente, mostra un avviso nella barra superiore con le istruzioni adatte a come l'app è installata (.deb, AUR, pip o sorgenti). Si può saltare una versione o verificare a mano in Impostazioni → Interfaccia. Nessun download né installazione automatica: solo un avviso.

### Release firmate, .deb e AUR

- **Artefatti firmati**: ogni file della release ha una firma GPG staccata (chiave ed25519 dedicata, pubblicata come `olladesk-release-key.asc` nella release e in `packaging/` nel repository) più `SHA256SUMS` firmato. Verifica con `gpg --verify` (istruzioni nel README).
- **Pacchetto Arch e AUR**: nuovo PKGBUILD pubblicato su AUR; `makepkg` verifica la firma del tarball (`validpgpkeys`) e la CI esegue `check()`, namcap, installazione e avvio di prova in un container Arch.
- **Tarball sorgente riproducibile**: costruito con `git archive` dall'ultimo commit — checksum stabili nel tempo, al posto dell'archivio automatico di GitHub.
- **Pipeline di rilascio unica**: `release.yml` esegue i test a ogni push e pull request; sui tag `v*` produce tarball, `.deb` e pacchetto Arch, li firma e li pubblica su GitHub e AUR. La build parte solo se il tag coincide con la versione del codice.

### Correzioni

- **Orario e statistiche nei messaggi**: l'orario resta accanto alle statistiche (token, tok/s) sia a fine risposta sia alla riapertura delle conversazioni, e non si accumula più a ogni cambio tema.
- **Larghezza delle bolle utente**: ricalcolata al ridimensionamento della finestra (prima restavano larghe come alla creazione e venivano ritagliate in finestre strette).
- **Cambio tema durante la generazione**: con tema «Sistema» un passaggio scuro/chiaro del desktop non distrugge più la bolla della risposta in streaming.
- **Impostazioni**: «Salva» non chiude più la finestra mentre un aggiornamento di Ollama è in corso (l'aggiornamento verrebbe interrotto).
- **Ricerca web**: sanitizzazione completa degli URL dei risultati di DuckDuckGo, con confronto sull'hostname (fix del code scanning di GitHub).
- **Varie**: escape HTML delle versioni mostrate dal server Ollama; voce `.desktop` con una sola categoria principale (niente duplicazioni nel menu applicazioni).


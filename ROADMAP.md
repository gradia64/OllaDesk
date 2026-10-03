# Roadmap 0.2.6

Piano della versione 0.2.6. Architettura di riferimento: app PySide6,
client puro dell'API Ollama, streaming NDJSON in `ChatWorker`.

## Voci approvate

| # | Voce | Origine | Stato |
|---|------|---------|-------|
| 1 | Chiave di rilascio su GitHub nella documentazione: in `packaging/README.md` (configurazione e «Rotazione e revoca») i comandi per caricare sull'account la chiave pubblica **senza la sottochiave della CI** (`gpg --export --export-filter drop-subkey="fpr = <CI>"`, poi `gh gpg-key add`), così i tag risultano «Verified» ma una CI compromessa non può produrre tag o commit «Verified». A ogni rotazione o proroga la chiave va cancellata e ricaricata su GitHub (`gh gpg-key delete`). Fatto a mano il 2026-10-03 per OllaDesk e KlamAV-Py: `v0.2.5` e `v0.1.14` risultano «Verified» | revisione 0.2.5 | ☑ |
| 2 | **Stop della ricerca web senza blocchi**: `WebSearchWorker.stop()` chiude la risposta dal thread principale (`web_search.py:298`); va usato `abort_response`, come per gli altri worker, con un test di regressione gemello di quello di `ChatWorker` (riprodotto: 9,66 s di UI bloccata premendo ■ durante la ricerca) | revisione 0.2.5 | ☑ |
| 3 | **Note di rilascio dal repository**: il workflow crea la release con `--generate-notes` (`release.yml:187`), quindi la descrizione è solo il link «Full Changelog». Le note di ogni versione in un file del repository, passate con `--notes-file` (la descrizione della 0.2.5 è stata riscritta a mano) | revisione 0.2.5 | ☑ |
| 4 | **`.deb` riproducibile**: `Installed-Size` calcolato sui file invece che con `du -sk`, che conta i blocchi delle directory e cambia con il filesystem (ricostruito dal tag: 328 su tmpfs, 396 nella CI; contenuto identico) | revisione 0.2.5 | ☑ |
| 5 | **pytest**: `tests/release_tools_test.py` viene raccolto e dà 10 errori (le funzioni prendono `tmp, keys`); escluderlo (es. `testpaths` in `pyproject.toml`) e allineare il README (oggi: 57 passati, 10 errori) | revisione 0.2.5 | ☑ |
| 6 | **Test degli script di rilascio più rigorosi**: verificare il messaggio d'errore e non solo il codice d'uscita; senza gpg/git uscire con errore in CI invece di «SKIP» con successo | revisione 0.2.5 | ☐ |
| 7 | **Rifiniture**: controllo della versione vuota in `prepare-aur.sh`; commento superato in `build-source.sh` (il PKGBUILD clona il tag); refuso `OLLADEK_CHILD` in `unit_test.py`; ordine di `SHA256SUMS` indipendente dal locale (`LC_ALL=C`); PySide6 fissato in CI (`>=6.6,<7`); commento «stesso layout del .deb» nel PKGBUILD (il pacchetto Arch contiene i `.pyc`, per prassi Arch) | revisione 0.2.5 | ☐ |
| 8 | **Falso positivo del suggerimento su `think`**: `if "think" in err.lower()` scatta per qualunque errore che contenga «thinking» | revisione 0.2.5 | ☐ |

Le voci 2–8 vengono dalle revisioni esterne della 0.2.5 (DeepSeek 4.1
Flash, GLM 5.3 Flash), verificate sul codice e sulla release pubblicata e
approvate il 2026-10-03.

Rilievi valutati e non accolti come difetti: `arch` non eseguibile con
l'avvio manuale (scelta: costruisce dal tag firmato, documentato in
`packaging/README.md`); `.pyc` nel pacchetto Arch (prassi della
distribuzione). Suggerimenti di processo da valutare a parte: pinning delle
action obbligatorio nelle impostazioni del repository, protezione di
`main`, `concurrency` nel workflow.

## Residui tecnici

| # | Voce | Note | Stato |
|---|------|------|-------|
| T2 | Test e2e della condivisione API | Collaudo manuale completato il 2026-10-02 (telefono collegato a `http://192.168.1.83:11434`; firewalld sistemato con regola rich limitata alla LAN). Resta da valutare un test automatico con bind `127.0.0.1` su una porta libera | ☐ |

## Promemoria per il rilascio

- Il bump di versione si fa al momento del rilascio (fonte unica:
  `olladesk/__init__.py`); aggiornare anche l'esempio `gpg --verify` nel
  README.
- Prima del tag: `tests/unit_test.py`, `tests/release_tools_test.py` e i
  collaudi reali `tests/gui_smoke.py` e `tests/gui_features.py`.
- Il tag va firmato con la sottochiave dei tag (`git tag -s vX.Y.Z`, poi
  `scripts/verify-tag.sh vX.Y.Z` prima del push) e `publish` va approvato
  nell'environment `release`: vedi `packaging/README.md`, «Rilascio».
- Note di rilascio in `packaging/release-notes/X.Y.Z.md` prima del tag:
  diventano la descrizione della release (`scripts/release-notes.sh`).

---

## Archivio — 0.2.5 (rilasciata il 2026-10-03)

Rifiniture dal collaudo della 0.2.4: copia 🔗 con istanza esterna
raggiungibile, nota all'avvio solo se serve, icona più grande nella tray,
✕ sulle note di sistema, icona thinking disegnata come il globo. Stop dei
worker senza blocchi della UI (chat, modelli, aggiornamenti), `.deb` in xz.
Residuo T1 chiuso: action Node 24 e runner fissati verificati al primo
`publish`.

Decisioni di progetto ancora valide:

- **Firma di rilascio a due sottochiavi** (modello di KlamAV-Py): i tag li
  firma il maintainer con la sottochiave dei tag, che non lascia la sua
  macchina; gli allegati la CI con la sua sottochiave, l'unica nei secret
  dell'environment `release`. La primaria `5B16…6694` resta `[SC]` (con
  `change-usage` a `[C]` gpg rifiuterebbe le firme fino alla 0.2.4) ma non
  firma più nulla. AUR costruisce dal tag firmato (`?signed`).

## Archivio — 0.2.4

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

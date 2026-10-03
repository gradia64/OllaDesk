# Packaging e release di OllaDesk

Guida per chi pubblica le release. Gli utenti trovano installazione e verifica
delle firme nel [README principale](../README.md).

## Modello di fiducia

Una chiave di rilascio, tre parti con ruoli separati (impronte in
`scripts/release-keys.sh`, unico punto in cui sono scritte):

| Parte | Impronta | Dove sta | Firma |
|-------|----------|----------|-------|
| Primaria `[SC]` | `5B16 6C1B 4AD7 428C 74A0  7D5B A337 0987 A057 6694` | solo sulla macchina del maintainer | niente di nuovo: certifica le sottochiavi. Ha firmato gli allegati fino alla 0.2.4. È l'impronta di `validpgpkeys` e del README |
| Sottochiave dei tag `[S]` | vedi `TAG_SIGNING_SUBKEYS` | solo sulla macchina del maintainer, **mai nella CI** | i tag `v*` (dalla 0.2.5) |
| Sottochiave della CI `[S]` | vedi `CI_SIGNING_SUBKEY` | secret dell'environment `release` | gli allegati della release (`.sig`, `SHA256SUMS.sig`) |

La primaria resta `[SC]` invece di diventare solo `[C]`: togliendole la
capacità di firma, gpg rifiuterebbe le firme che ha già fatto (gli allegati
della 0.2.3 e 0.2.4, e il tarball da cui AUR ricostruisce la 0.2.4). Non
firma più nulla, e non lascia la macchina del maintainer.

Cosa garantisce cosa:

- **La firma del tag** dice che il codice di quella versione l'ha approvato
  il maintainer. È quella che verifica makepkg (`?signed` nel PKGBUILD): il
  pacchetto AUR si costruisce dal tag, non dagli allegati. Il job `verify`
  accetta solo la sottochiave dei tag: un tag firmato con la sottochiave
  della CI o con la primaria, valido per gpg, qui viene rifiutato.
- **La firma degli allegati** dice che quei file li ha prodotti la CI di
  questo repository da quel tag. Non dice nulla di più del tag.
- **Se la CI o i secret vengono compromessi**: si revoca solo la
  sottochiave della CI. Le firme dei tag passati restano valide, la
  primaria e `validpgpkeys` non cambiano.

Limiti da sapere:

- makepkg accetta una firma di **qualunque** chiave della primaria in
  `validpgpkeys`. Chi avesse la sottochiave della CI potrebbe firmare un
  tag che makepkg accetta, anche se `verify` lo rifiuta: per questo la
  sottochiave sta solo nell'environment protetto, e va revocata subito al
  primo sospetto.
- La chiave SSH di AUR nella CI dà accesso a tutto l'account AUR
  (`olladesk` e `klamav-py`): chi la ottiene può pubblicare un PKGBUILD a
  sua scelta. L'approvazione dell'environment è l'ultimo controllo prima
  di AUR.

La separazione non rende innocua una CI compromessa: la rende rimediabile
senza danni allo storico.

## Cosa produce una release

Al push di un tag `vX.Y.Z` il workflow [`release.yml`](../.github/workflows/release.yml)
esegue, in ordine:

| Job       | Cosa fa |
|-----------|---------|
| `test`    | unit test, test offline (worker, controllo aggiornamenti), test degli script di rilascio |
| `verify`  | il tag è annotato e firmato dalla sottochiave dei tag (`scripts/verify-tag.sh`); tag = `__version__`; note di rilascio presenti (`scripts/release-notes.sh`) |
| `dist`    | tarball sorgente, `.deb`, lintian. Niente firme: qui non ci sono secret |
| `arch`    | in un container `archlinux:base-devel`: PKGBUILD che clona il tag da GitHub e ne **verifica la firma**, `check()`, namcap, installazione e avvio di prova |
| `publish` | **attende la tua approvazione** (environment `release`), poi: firma di tutti gli artefatti con la sottochiave della CI, release GitHub con la descrizione da `packaging/release-notes/X.Y.Z.md`, push del PKGBUILD su AUR |

Solo `publish` vede i secret. Avviato a mano (`workflow_dispatch`) il
workflow esegue `test` e `dist`: `verify`, `arch` e `publish` servono un tag.

File allegati alla release:

```
olladesk-X.Y.Z.tar.gz             sorgenti del tag (git archive)
olladesk_X.Y.Z_all.deb            pacchetto Debian
olladesk-X.Y.Z-1-any.pkg.tar.zst  pacchetto Arch (lo stesso che AUR costruisce)
*.sig                             firma GPG staccata di ciascun file
SHA256SUMS, SHA256SUMS.sig        checksum di tutto, firmati
olladesk-release-key.asc          chiave pubblica di release
```

## Configurazione (una volta sola)

### 1. Le due sottochiavi

Sulla macchina del maintainer, dove sta la primaria (gpg chiede la sua
passphrase):

```bash
gpg --quick-add-key 5B166C1B4AD7428C74A07D5BA3370987A0576694 ed25519 sign 3y   # tag
gpg --quick-add-key 5B166C1B4AD7428C74A07D5BA3370987A0576694 ed25519 sign 2y   # CI
gpg --list-keys --with-subkey-fingerprints 5B166C1B4AD7428C74A07D5BA3370987A0576694
```

Scrivi le due impronte in `scripts/release-keys.sh` (`TAG_SIGNING_SUBKEYS`
e `CI_SIGNING_SUBKEY`). `tests/release_tools_test.py` controlla che
corrispondano alla chiave pubblica del repository.

### 2. Chiave pubblica aggiornata

Chi ha la versione vecchia della chiave non conosce le nuove sottochiavi e
non riesce a verificare né i tag né gli allegati: va ripubblicata ovunque.

```bash
gpg --armor --export 5B166C1B4AD7428C74A07D5BA3370987A0576694 > packaging/olladesk-release-key.asc
gpg --keyserver hkps://keys.openpgp.org --send-keys 5B166C1B4AD7428C74A07D5BA3370987A0576694
gpg --keyserver hkps://keyserver.ubuntu.com --send-keys 5B166C1B4AD7428C74A07D5BA3370987A0576694
```

keys.openpgp.org pubblica l'uid (nome ed email) solo dopo la conferma
dell'indirizzo: senza, `gpg --recv-keys` da lì risponde «chiave non
trovata» anche se il server ha la chiave. Per chiedere la conferma:
`gpg --export 5B166C1B4AD7428C74A07D5BA3370987A0576694 | curl -T - https://keys.openpgp.org`
e segui il link stampato.

Ricarica anche la copia sull'account GitHub (sezione 6).

### 3. Environment `release` su GitHub

Settings → Environments → New environment, nome `release`:

- **Required reviewers**: il maintainer. Ogni rilascio si ferma prima di
  `publish` finché non approvi.
- **Deployment branches and tags**: «Selected branches and tags», regola
  sul tag `v*`. Un job che usa l'environment da un branch non parte.
- **Environment secrets** (sostituisci `<CI>` con l'impronta della
  sottochiave della CI):
  ```bash
  gpg --armor --export-secret-subkeys '<CI>!' \
      | gh secret set GPG_PRIVATE_KEY --env release -R gradia64/OllaDesk
  gh secret set GPG_PASSPHRASE --env release -R gradia64/OllaDesk
  gh secret set AUR_SSH_PRIVATE_KEY --env release -R gradia64/OllaDesk < ~/.ssh/aur_olladesk_ci
  ```
  Il `!` esporta solo la sottochiave della CI: né la primaria né la
  sottochiave dei tag.

Poi togli i secret a livello di repository: fino alla 0.2.4
`GPG_PRIVATE_KEY` conteneva la **primaria**.

```bash
gh secret delete GPG_PRIVATE_KEY -R gradia64/OllaDesk
gh secret delete GPG_PASSPHRASE -R gradia64/OllaDesk
gh secret delete AUR_SSH_PRIVATE_KEY -R gradia64/OllaDesk
gh secret list -R gradia64/OllaDesk              # vuoto
gh secret list --env release -R gradia64/OllaDesk
```

La primaria è stata in un secret di GitHub: se vuoi escludere anche il
rischio che sia stata letta in passato, cambia la sua passphrase (non
protegge la copia già esportata) o valuta una chiave nuova. Il modello qui
sopra limita i danni futuri, non quelli passati.

### 4. git firma con la sottochiave dei tag

Con più chiavi di firma nel portachiavi, gpg sceglie da sé la più recente,
cioè quella della CI, se la chiave è indicata senza `!`. Nel repository va
fissata quella dei tag (sostituisci `<TAG>`):

```bash
git config --local user.signingkey '<TAG>!'
```

Se non ti serve firmare gli allegati in locale, puoi togliere dal
portachiavi la parte privata della sottochiave della CI dopo averla
caricata nell'environment: resta solo su GitHub, e per sostituirla basta
crearne una nuova con la primaria.

Fai un backup offline del portachiavi: se la primaria va persa, le release
future andranno firmate con una chiave nuova e gli utenti dovranno fidarsi
di nuovo.

### 5. Account AUR

1. Registrati su <https://aur.archlinux.org> e aggiungi al profilo una chiave
   SSH **dedicata** (non la tua personale):
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/aur_olladesk_ci -C "olladesk CI"
   ```
2. Caricala nell'environment (sezione 3).

`publish-aur.sh` rifiuta di pubblicare se il tag non è clonabile senza
autenticazione (repository privato o tag non pubblicato). Senza il secret
il passo di pubblicazione viene saltato con un avviso e il resto della
release procede.

### 6. Chiave sull'account GitHub (tag «Verified»)

GitHub mostra un tag firmato come «Verified» solo se la chiave pubblica è
fra le GPG key dell'account e il suo uid (`gradia@disroot.org`) è un
indirizzo verificato. Si carica una copia **senza la sottochiave della
CI**: i tag del maintainer risultano «Verified», mentre una CI compromessa
non può produrre tag o commit «Verified» (con la sua sottochiave la firma
resta «Unverified» su GitHub e `scripts/verify-tag.sh` la rifiuta).

```bash
gpg --armor --export \
    --export-filter drop-subkey="fpr = <CI>" \
    5B166C1B4AD7428C74A07D5BA3370987A0576694 > /tmp/olladesk-github.asc
gpg --show-keys --with-subkey-fingerprints /tmp/olladesk-github.asc   # niente <CI>
gh auth refresh -h github.com -s admin:gpg_key     # una volta: permesso sulle chiavi GPG
gh gpg-key add /tmp/olladesk-github.asc --title "OllaDesk release signing (senza CI)"
```

GitHub non aggiorna una chiave già caricata e non ne accetta due con la
stessa primaria: dopo una proroga delle scadenze o una rotazione della
sottochiave dei tag va cancellata e ricaricata.

```bash
gh gpg-key list                         # l'ID della chiave è A3370987A0576694
gh gpg-key delete A3370987A0576694
gh gpg-key add /tmp/olladesk-github.asc --title "OllaDesk release signing (senza CI)"
```

Tra la cancellazione e il nuovo caricamento i tag risultano per qualche
istante «Unverified». Controllo:

```bash
gh api repos/gradia64/OllaDesk/git/tags/$(git rev-parse vX.Y.Z) --jq .verification.reason   # valid
```

## Rilascio

1. Scrivi le note in `packaging/release-notes/X.Y.Z.md`, nello stile delle
   release precedenti (sezioni `## …`, voci in grassetto, link «Full
   Changelog» in fondo): diventano la descrizione della release così come
   sono, quindi rileggile.
   ```bash
   scripts/release-notes.sh X.Y.Z
   ```
   Porta `__version__` in `olladesk/__init__.py` alla nuova versione,
   committa e fai il push di `main` con la CI verde (`tests/release_tools_test.py`
   fallisce se la versione del codice non ha le sue note).
2. Tag firmato con la sottochiave dei tag (sezione 4), verifica e push:
   ```bash
   git tag -s vX.Y.Z -m "OllaDesk X.Y.Z"
   scripts/verify-tag.sh vX.Y.Z
   git push origin vX.Y.Z
   ```
   `scripts/verify-tag.sh` in locale, prima del push, dice subito se il tag
   è stato firmato con la chiave sbagliata.
3. Segui il workflow (`gh run watch`) e approva `publish` quando `dist` e
   `arch` sono verdi. Se `publish` fallisce dopo l'upload si può
   rilanciare: upload con `--clobber`, push AUR idempotente. Una release
   già esistente (creata a mano o da un rilancio) non viene ricreata: si
   caricano solo i file, e la descrizione resta quella che ha.

## Rotazione e revoca

**Compromissione della CI o dei secret** (o solo il sospetto):

```bash
gpg --edit-key 5B166C1B4AD7428C74A07D5BA3370987A0576694
#   selezionare la sottochiave della CI (key N), poi: revkey, save
gpg --quick-add-key 5B166C1B4AD7428C74A07D5BA3370987A0576694 ed25519 sign 2y
```

Aggiorna `CI_SIGNING_SUBKEY` in `scripts/release-keys.sh`, ripubblica la
chiave pubblica (sezione 2), ricarica `GPG_PRIVATE_KEY` nell'environment e
sostituisci la chiave SSH della CI nel profilo AUR. La sottochiave dei tag
non si tocca, e nemmeno la copia sull'account GitHub (sezione 6), che non
contiene la sottochiave della CI.

**Scadenze**: prorogale prima che scadano (la primaria il 30/09/2029, le
sottochiavi 3 e 2 anni dopo la creazione), poi ripubblica la chiave pubblica:

```bash
gpg --quick-set-expire 5B166C1B4AD7428C74A07D5BA3370987A0576694 3y
gpg --quick-set-expire 5B166C1B4AD7428C74A07D5BA3370987A0576694 2y <sottochiave>
```

Se hai prorogato la primaria o la sottochiave dei tag, ricarica anche la
copia sull'account GitHub (sezione 6): altrimenti, dopo la vecchia
scadenza, i tag nuovi risultano «Unverified».

**Rotazione della sottochiave dei tag**: aggiungi la nuova a
`TAG_SIGNING_SUBKEYS` (separate da spazi) prima di firmare con essa, e togli
la vecchia solo quando non serve più verificare tag nuovi con quella.
Revocarla invaliderebbe la verifica di tutti i tag che ha firmato. Ricarica
la copia sull'account GitHub (sezione 6) con la nuova sottochiave.

## Script

Tutti gli script funzionano anche in locale, con gli stessi risultati della CI.

| Script | Uso |
|--------|-----|
| `scripts/release-keys.sh` | impronte e ruoli delle chiavi (letto dagli altri script) |
| `scripts/verify-tag.sh <tag>` | il tag è firmato dalla sottochiave dei tag |
| `scripts/release-notes.sh <versione>` | note di rilascio della versione (`packaging/release-notes/`), con i controlli del job `verify` |
| `scripts/build-source.sh [commit]` | `dist/olladesk-X.Y.Z.tar.gz` da `git archive` (riproducibile) |
| `scripts/build-deb.sh` | `dist/olladesk_X.Y.Z_all.deb` (riproducibile) |
| `scripts/sign-release.sh [dir]` | firme `.sig` + `SHA256SUMS` con la sottochiave della CI; verifica con la sola chiave pubblica del repository |
| `scripts/prepare-aur.sh` | `build/aur/PKGBUILD` da `packaging/arch/PKGBUILD.in` |
| `scripts/build-arch.sh` | build dal tag firmato e controlli del pacchetto Arch (come root in un container Arch) |
| `scripts/publish-aur.sh` | push di PKGBUILD e `.SRCINFO` su AUR |

Esempio in locale (su Debian, con Docker per la parte Arch; il tag deve
essere già pubblicato):

```bash
scripts/build-source.sh && scripts/build-deb.sh
scripts/sign-release.sh           # serve la sottochiave della CI nel portachiavi
scripts/prepare-aur.sh
docker run --rm -v "$PWD":/src -w /src archlinux:base-devel scripts/build-arch.sh
```

`build-source.sh` usa il commit (default `HEAD`), non la cartella di lavoro:
le modifiche non committate non finiscono nel tarball.

## File comuni

`packaging/common/` contiene launcher (`launcher.py`, `olladesk.sh`) e manpage
(`olladesk.1.in`) usati sia dal `.deb` sia dal PKGBUILD, così i due pacchetti
installano lo stesso layout. Il file `olladesk/_packaging` (`deb` o `arch`),
scritto al momento del packaging, dice al controllo aggiornamenti dell'app
quali istruzioni mostrare.

# Packaging e release di OllaDesk

Guida per chi pubblica le release. Gli utenti trovano installazione e verifica
delle firme nel [README principale](../README.md).

## Cosa produce una release

Al push di un tag `vX.Y.Z` il workflow [`release.yml`](../.github/workflows/release.yml)
esegue, in ordine:

| Job       | Cosa fa |
|-----------|---------|
| `test`    | unit test + test offline (worker, controllo aggiornamenti) |
| `dist`    | verifica tag = `__version__`, tarball sorgente, `.deb`, lintian, firma GPG |
| `arch`    | in un container `archlinux:base-devel`: PKGBUILD, build con **verifica della firma** del tarball, `check()`, namcap, installazione e avvio di prova |
| `publish` | firma di tutti gli artefatti, upload sulla release GitHub, push del PKGBUILD su AUR |

File allegati alla release:

```
olladesk-X.Y.Z.tar.gz             sorgenti (usati dal PKGBUILD AUR)
olladesk_X.Y.Z_all.deb            pacchetto Debian
olladesk-X.Y.Z-1-any.pkg.tar.zst  pacchetto Arch
*.sig                             firma GPG staccata di ciascun file
SHA256SUMS, SHA256SUMS.sig        checksum di tutto, firmati
olladesk-release-key.asc          chiave pubblica di release
```

## Preparazione (una volta sola)

### 1. Chiave GPG di release

Chiave in uso (generata il 30/09/2026, scade il 30/09/2029):

```
gradia (OllaDesk release signing) <gradia@disroot.org>
ed25519  5B16 6C1B 4AD7 428C 74A0  7D5B A337 0987 A057 6694
```

La parte pubblica è in `packaging/olladesk-release-key.asc`. La privata sta
nel portachiavi principale di chi pubblica (`~/.gnupg`). Per caricarla nei
secret della CI, **solo la chiave primaria di firma** (il `!` esclude la
sottochiave di cifratura, che alla CI non serve), senza file intermedi:

```bash
gpg --armor --export-secret-keys '5B166C1B4AD7428C74A07D5BA3370987A0576694!' \
    | gh secret set GPG_PRIVATE_KEY
gh secret set GPG_PASSPHRASE     # chiede la passphrase della chiave
```

gpg chiede la passphrase per l'esportazione. Per firmare in locale basta
`scripts/sign-release.sh` (usa il portachiavi predefinito).

Per una chiave nuova in un portachiavi dedicato c'è invece:

```bash
scripts/gen-release-key.sh "gradia <gradia@disroot.org>"
```

Lo script crea un portachiavi dedicato (`~/.local/share/olladesk-release-gnupg`),
una chiave ed25519 di sola firma valida 3 anni (gpg chiede la passphrase), e
scrive:

- `packaging/olladesk-release-key.asc`: chiave **pubblica**, da committare;
- una copia esportata della chiave **privata**, da caricare come secret e poi
  cancellare (lo script stampa i comandi esatti).

```bash
gh secret set GPG_PRIVATE_KEY < ~/.local/share/olladesk-release-gnupg/olladesk-release-private.asc
gh secret set GPG_PASSPHRASE
shred -u ~/.local/share/olladesk-release-gnupg/olladesk-release-private.asc
```

Fai un backup offline del portachiavi: se la chiave va persa, le release future
andranno firmate con una chiave nuova e gli utenti dovranno fidarsi di nuovo.
Per prorogare la scadenza (prima del 30/09/2029): `gpg --quick-set-expire
5B166C1B4AD7428C74A07D5BA3370987A0576694 3y`, poi riesporta e ricommitta la
chiave pubblica (`gpg --armor --export <impronta> > packaging/olladesk-release-key.asc`)
e aggiorna il secret.

Senza `GPG_PRIVATE_KEY` il job `dist` fallisce: una release non firmata non
viene pubblicata.

### 2. Account AUR

1. Registrati su <https://aur.archlinux.org> e aggiungi al profilo una chiave
   SSH **dedicata** (non la tua personale):
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/aur_olladesk -C "olladesk CI"
   ```
2. Carica la privata come secret:
   ```bash
   gh secret set AUR_SSH_PRIVATE_KEY < ~/.ssh/aur_olladesk
   ```

Il primo push crea il pacchetto `olladesk` su AUR (il nome era libero al
30/09/2026). `publish-aur.sh` rifiuta di pubblicare se tarball e firma della
release non sono scaricabili senza autenticazione (repository privato o
release non ancora pubblicata). Senza il secret il passo di pubblicazione viene saltato con un
avviso e il resto della release procede.

## Rilascio

1. Porta `__version__` in `olladesk/__init__.py` alla nuova versione e committa.
2. `git tag vX.Y.Z && git push origin main vX.Y.Z`
3. Controlla il workflow: se `publish` fallisce dopo l'upload, si può rilanciare
   (upload con `--clobber`, push AUR idempotente).

## Script

Tutti gli script funzionano anche in locale, con gli stessi risultati della CI.

| Script | Uso |
|--------|-----|
| `scripts/build-source.sh [commit]` | `dist/olladesk-X.Y.Z.tar.gz` da `git archive` (riproducibile) |
| `scripts/build-deb.sh` | `dist/olladesk_X.Y.Z_all.deb` (riproducibile) |
| `scripts/sign-release.sh [dir]` | firme `.sig` + `SHA256SUMS`; verifica con la sola chiave pubblica del repository |
| `scripts/prepare-aur.sh` | `build/aur/PKGBUILD` da `packaging/arch/PKGBUILD.in` |
| `scripts/build-arch.sh` | build e controlli del pacchetto Arch (come root in un container Arch) |
| `scripts/publish-aur.sh` | push di PKGBUILD e `.SRCINFO` su AUR |
| `scripts/gen-release-key.sh` | generazione della chiave di release (una volta) |

Esempio completo in locale (su Debian, con Docker per la parte Arch):

```bash
scripts/build-source.sh && scripts/build-deb.sh
scripts/sign-release.sh
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

# Flatpak (in preparazione, non su Flathub)

Bundle Flatpak di OllaDesk, da distribuire insieme alle release. Per ora **non**
va consegnato a Flathub. Primo build riuscito il 2026-10-25 (Plasma 6, Wayland):
l'app parte, PySide6 viene dalla base app, Ollama risponde dalla sandbox e il
trascinamento da Dolphin allega file e cartelle. Restano i punti di «Cosa verificare»
non ancora segnati ✅.

## File

- `io.github.gradia64.OllaDesk.yaml` — manifest flatpak-builder (permessi commentati uno a uno).
- `requirements.txt` — dipendenze terze hash-pinnate: stesse versioni di
  `packaging/appimage/requirements.txt` **senza** `PySide6_Essentials`/`shiboken6`,
  ma con gli hash delle wheel per Python 3.13. Gli stessi URL e hash sono le
  `sources` del modulo `python3-deps` nel manifest.
- `fontconfig-local.conf` — installato come `/app/etc/fonts/local.conf`: esclude i
  font WOFF/WOFF2 dell'host (vedi «Font WOFF dell'host»).

## Scelte di base

- **Runtime**: `org.kde.Platform // 6.11` (+ `org.kde.Sdk`): Qt 6.11.2 e Python 3.13,
  ma **niente PySide6** (verificato: `import PySide6` fallisce nel runtime).
- **PySide6**: dalla base app `io.qt.PySide.BaseApp // 6.11` di Flathub, costruita da
  pyside-setup v6.11.2 (la stessa versione dell'AppImage) sul Qt del runtime. La base
  porta anche QtWebEngine e numpy: `cleanup-commands: /app/cleanup-BaseApp.sh` con
  `BASEAPP_REMOVE_WEBENGINE=1` e `BASEAPP_DISABLE_NUMPY=1` li toglie. Mai una wheel
  PySide6 da pip: porterebbe un secondo Qt.
- **Dipendenze**: modulo `python3-deps`, wheel come `sources` con sha256 (il build
  di flatpak-builder non ha rete), installate con `pip --no-index --require-hashes`.
- **Dati**: `XDG_CONFIG_HOME` in sandbox → `~/.var/app/io.github.gradia64.OllaDesk/config`.
  L'app ci scrive sotto `.../config/olladesk` (settings, chat, allegati, companion).
  La migrazione da `ollama-gui` e dalle altre installazioni **non** attraversa
  (percorsi diversi): primo avvio Flatpak = profilo pulito.

## Come costruire

Serve `flatpak-builder` (`sudo apt install flatpak-builder`). Runtime, SDK e base
app (`io.qt.PySide.BaseApp//6.11`) arrivano da Flathub con `--install-deps-from`:

```sh
flatpak-builder --user --install-deps-from=flathub --force-clean \
  --repo=build/flatpak-repo build/flatpak \
  packaging/flatpak/io.github.gradia64.OllaDesk.yaml
flatpak build-bundle build/flatpak-repo OllaDesk.flatpak io.github.gradia64.OllaDesk
```

## Dipendenze Python

Le versioni seguono il lock dell'AppImage; le wheel sono quelle per CPython 3.13
(il Python del runtime). Quando cambia `packaging/appimage/requirements.txt`, o il
runtime passa a un altro Python, vanno rigenerati **insieme** `requirements.txt` e le
`sources` del modulo `python3-deps`:

```sh
grep -oE '^[A-Za-z0-9_.-]+==[^ ]+' packaging/appimage/requirements.txt \
  | grep -viE 'pyside6|shiboken6' > /tmp/fp-pins.txt
pip3 download --no-deps --only-binary=:all: --dest /tmp/fp-wheels \
  --python-version 3.13 --implementation cp --abi cp313 --abi abi3 --abi none \
  --platform manylinux_2_34_x86_64 --platform manylinux_2_28_x86_64 \
  --platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 --platform any \
  -r /tmp/fp-pins.txt
# per ogni wheel: URL e sha256 da https://pypi.org/pypi/<nome>/<versione>/json
```

Prova senza rete, nell'SDK, prima del build (con il nuovo `requirements.txt`
copiato in `/tmp/fp-wheels`):

```sh
flatpak run --user --unshare=network --filesystem=/tmp/fp-wheels --cwd=/tmp/fp-wheels \
  --command=pip3 org.kde.Sdk//6.11 install --no-index --find-links=. \
  --require-hashes --no-deps --prefix=/tmp/fp-prefix -r requirements.txt
```

## Font WOFF dell'host

Senza `fontconfig-local.conf` l'app va in segfault all'avvio. I font WOFF/WOFF2
dell'host (`/usr/share/fonts/woff`, da `fonts-roboto-slab` e `fonts-font-awesome`
su Debian) arrivano nella sandbox da `/run/host/fonts` **senza set di caratteri**.
Il Qt del runtime (6.11.2, `QFontEngineMultiFontConfig::shouldLoadFontEngineForCharacter`)
non controlla l'esito di `FcPatternGetCharSet` e legge un puntatore non
inizializzato. Succede al primo carattere che il font principale non ha: con Noto
Sans, «➤» del pulsante Invia (Roboto Slab è il secondo font di ripiego). Sono font
per il web, inutili per un'interfaccia desktop, quindi si escludono. Per verificare
che un host abbia font senza set di caratteri:

```sh
flatpak run --command=sh io.github.gradia64.OllaDesk -c \
  'fc-list -f "%{file}|%{charset}\n" | awk -F"|" "\$2==\"\""'
```

## Cosa verificare PRIMA di consegnare a Flathub (la policy è restrittiva)

1. ✅ **Rete / Ollama** (verificato al primo build: 7 modelli da `localhost:11434`;
   la companion verso il telefono è ancora da provare) — `--share=network` (non è il predefinito: senza, niente rete)
   rende raggiungibile `localhost:11434` dell'host + la companion in entrata.
   Test reale sulla box: avvia il bundle e controlla che Ollama risponda e che la
   companion veda la LAN. Se Flathub contestasse l'accesso al loopback, alternativa:
   Ollama su `OLLAMA_HOST=0.0.0.0` + app sull'IP LAN, oppure Ollama dentro lo stesso Flatpak.
2. ✅ **File** (verificato al primo build: file e cartella trascinati da Dolphin fuori
   dalle cartelle XDG vengono allegati) — nessun `--filesystem`: allegati e cartelle passano dal portale
   FileChooser, che dà percorsi `/run/user/<uid>/doc/…` già leggibili. Il
   **trascinamento** dal file manager passa dal portale FileTransfer: il Qt del runtime
   KDE non lo gestisce (la chiave `application/vnd.portal.filetransfer` c'è solo in GTK3
   e SDL3), quindi lo fa l'app in `olladesk/portal.py` con `RetrieveFiles`. Da
   sorgenti che non usano il portale l'app avvisa di allegare con 📎. Da verificare
   al primo avvio trascinando un file e una cartella da Dolphin. Config e cache sono quelle della sandbox (`~/.var/app/<app-id>/`):
   `--filesystem=xdg-config/…` o `xdg-cache` aprirebbero le cartelle dell'**host**.
3. **Keyring** — `--talk-name=org.freedesktop.secrets` per KWallet (keyring usa
   D-Bus, non il portale). Senza, l'app degrada già senza errori (chiave in `settings.json`).
4. **Tray e avvisi** — `--talk-name=org.kde.StatusNotifierWatcher` (vale anche su
   GNOME con AppIndicator), `--own-name=org.kde.StatusNotifierItem-2-1` (il nome con
   cui Qt registra l'icona, pid 2 nella sandbox: verificare se serve davvero) e
   `--talk-name=org.freedesktop.Notifications` per gli avvisi della tray.
5. **X11** — `--socket=fallback-x11` + `--share=ipc`, non `--socket=x11`: su Wayland
   il server X resterebbe esposto. I portali (tema chiaro/scuro compreso) sono sempre
   raggiungibili, senza `--talk-name`.
6. **`install_method == "flatpak"`** — `olladesk/app_update.py` va esteso con la
   branca `flatpak` (rileva via `FLATPAK_ID` o `/.flatpak/<app-id>`) e l'hint
   `flatpak update io.github.gradia64.OllaDesk`. Il marcatore `_packaging=flatpak`
   scritto nel manifest deve combaciare. Oggi un marcatore `flatpak` ricade su
   `"pip"` (istruzioni di aggiornamento sbagliate). Verificare anche che
   `app._desktop_file_installed()` trovi il `.desktop` nella sandbox, altrimenti
   `setDesktopFileName` non viene chiamato e su Wayland app_id e icona sono sbagliati.
7. ✅ **PySide6 della base app** (verificato: 6.11.2 da `/app/lib/python3.13/site-packages`) — `io.qt.PySide.BaseApp // 6.11` è PySide 6.11.2,
   allineato al pin dell'AppImage. Quando si passa a un altro runtime, `base-version`
   e `runtime-version` vanno cambiati insieme. Dopo la pulizia restano in `/app/lib`
   le librerie LLVM/clang della base (servono solo a generare i binding): possibile
   risparmio di spazio da valutare dopo il primo build.

## Note

- **Permessi ereditati dalla base app:** oltre ai nostri `finish-args`, il bundle
  riceve quelli di `io.qt.PySide.BaseApp`: `--filesystem=xdg-config/kdeglobals:ro` e
  `--talk-name` per `com.canonical.AppMenu.Registrar`, `org.kde.kconfig.notify` e
  `org.kde.KGlobalSettings` (tema e colori di KDE). Controllarli con
  `flatpak info --show-permissions io.github.gradia64.OllaDesk`.
- **Spazio:** dopo la pulizia restano in `/app/lib` circa 150 MB di librerie LLVM e
  clang della base (`libLLVM.so.21.1`, `libclang-cpp.so.21.1`), che servono solo a
  generare i binding.

- Formato YAML per i commenti; Flathub usa spesso JSON (senza commenti). Equivalenti.
- `sources` punta a `../../` (checkout locale). In CI/Flathub: `type: git` (tag) o
  `type: archive` (tarball con hash).
- Mai `--talk-name=org.freedesktop.Flatpak` (`flatpak-spawn --host` esce dalla
  sandbox): l'aggiornamento di Ollama via pkexec in Flatpak non c'è, l'app mostra
  il comando da eseguire a mano.
- In Flatpak si installa in `/app` (`/usr` è il runtime, in sola lettura) e `/app`
  parte vuota: le cartelle, `bin` compresa, vanno create.
- Licenza: app GPL-3.0, PySide6 (Qt for Python) LGPLv3 → ammessa su Flathub;
  rispettare le note di licenza.

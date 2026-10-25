<p align="center">
  <a href="README.md"><img src="https://img.shields.io/badge/Lang-Italiano-green?style=for-the-badge" alt="Italiano"></a>
  <a href="README.en.md"><img src="https://img.shields.io/badge/Lang-English-blue?style=for-the-badge" alt="English"></a>
  <a href="README.fr.md"><img src="https://img.shields.io/badge/Lang-Français-blue?style=for-the-badge" alt="Français"></a>
  <a href="README.es.md"><img src="https://img.shields.io/badge/Lang-Español-orange?style=for-the-badge" alt="Español"></a>
</p>

# OllaDesk

**OllaDesk** ist eine native Linux-Desktop-Anwendung zur Interaktion mit und Verwaltung lokaler LLMs über [Ollama](https://ollama.com), erstellt mit **Qt / PySide6**. Leichtgewichtig, im ChatGPT-Stil, für Debian SID mit KDE Plasma konzipiert.

> *A native Linux desktop application for interacting with and managing local
> LLMs through Ollama, designed with Qt/PySide6.*

Lizenz: **GPL-3.0** · Python 3.10+ · PySide6 6.6+

<p align="center">
  <img src="docs/screenshots/chat.png" alt="OllaDesk — chat" width="45%" />
  <img src="docs/screenshots/web-search.png" alt="Websuche" width="45%" />
</p>
<p align="center">
  <img src="docs/screenshots/models.png" alt="Modellverwaltung" width="46%" />
  <img src="docs/screenshots/settings.png" alt="Einstellungen" width="44%" />
</p>
<p align="center">
  <img src="docs/screenshots/phone.png" alt="Web-Companion auf dem Telefon" width="30%" />
</p>

## Funktionen

- **Chat im ChatGPT-Stil**: Nutzer-/Assistenten-Bubbles, *Streaming*-Antwort Token für Token, Schaltfläche zum Abbrechen der Generierung, Statistiken (Token, Tok/s, Dauer), Markdown-Rendering (Codeblöcke, Listen, Fett, Links…), Kopier-Schaltfläche für jede Antwort. Die Nachrichtenspalte hat eine feste maximale Breite und bleibt auch bei ausgeblendeter Seitenleiste (Strg+B) zentriert: der Inhalt streckt sich nicht über das ganze Fenster. Während das Modell antwortet, bleibt die Ansicht dort, wo man sie lässt (mit dem Rad nach oben scrollen) und folgt der Antwort wieder, wenn man nach unten scrollt; dasselbe gilt im Web-Companion.
- **Mehrsprachige Oberfläche**: Italienisch, Englisch, Französisch, Deutsch und Spanisch, auch im Web-Companion. Sie folgt der Systemsprache (noch nicht übersetzte Sprachen verwenden Englisch) oder wird unter Einstellungen → Oberfläche → „Sprache“ gewählt; die Wahl gilt nach einem Neustart. Die Kataloge liegen in `olladesk/locale/*.po` und werden mit `python3 scripts/i18n.py update` aktualisiert.
- **Gesprächsverlauf** in der Seitenleiste (mit der OllaDesk-Version in der Kopfzeile): neue Chat, Auswahl, Umbenennen (Rechtsklick) und Löschen; Persistenz auf der Festplatte. Lange Titel in der Seitenleiste werden mit «…» auf die tatsächliche Spaltenbreite gekürzt.
- **Archiv der Gespräche** (Rechtsklick → «Archivieren»): der Chat verschwindet aus der Liste, ohne gelöscht zu werden, und findet sich über «🗄 Archiv (N)» am Ende der Seitenleiste wieder, von wo aus er geöffnet oder wiederhergestellt wird. Schreibt man eine neue Nachricht in einen archivierten Chat, erscheint er wieder in der Liste. Optionale automatische Archivierung beim Start von Chats ohne Nachrichten seit einer bestimmten Anzahl von Tagen (Einstellungen → Oberfläche, standardmäßig aus).
- **Anhänge als Kontext** (📎 im Eingabefeld): Text- und Codedateien (im Nachrichtentext eingebettet), PDF (benötigt das optionale Paket `pypdf`), Word-, Excel- und OpenDocument-Dokumente (`.docx`, `.doc`, `.xlsx`, `.xls`, `.odt`, `.ods`: der Text, Tabellen Zeile für Zeile, ohne zusätzliche Abhängigkeiten) und Bilder (als Base64 an Vision-Modelle gesendet: llava, llama3.2-vision, gemma3…). Die Anhänge erscheinen als Chips über dem Eingabefeld und in der Nachrichtenblase.
- **Ordner als Kontext** (📎 → «Ordner…», oder hineinziehen in das Eingabefeld): ein einziger Anhang «📁 name/» mit den Text-, Code-, PDF- und Dokumentdateien, die er enthält, jeweils mit relativem Pfad. JavaScript-Module (`.mjs`, `.cjs`) und andere gängige Typen (`.vue`, `.svelte`, `.swift`, `.dart`, `.cs`, `.proto`, `.tf`, `.nix`, `.lock`…) sowie Dateien wie `COPYING`, `CHANGELOG` und `PKGBUILD` sind nun ebenfalls enthalten; Dateien, die wegen nicht unterstütztem Typ ausgeschlossen bleiben, sind im Hinweis aufgelistet. Ausgeschlossen bleiben versteckte Dateien und Ordner, Abhängigkeiten und Caches (`node_modules`, `__pycache__`, `venv`, `build`, `dist`…), was die `.gitignore` im Stamm des Ordners ausschließt (mit `*`, `**` und Schrägstrichen wie git, aber ohne Negationen und ohne interne `.gitignore`), wahrscheinliche Zugangsdatendateien (`*.env`, `credentials.*`, `secrets.*`, `token.*`, `*.pem`, `*.key`, `id_rsa*`…, die man dennoch einzeln anhängen kann), Bilder, spezielle Dateien (Pipes, Sockets) und symbolische Links, die aus dem Ordner herausführen. Grenzen: 200 Dateien, 10 PDF, 30.000 Zeichen pro Datei und 200.000 insgesamt, 5000 geprüfte Einträge (Dateien und Ordner), 2 Sekunden zwischen Besuch und Lesen (zuerst Textdateien, dann PDF). Ein Hinweis fasst zusammen, was ausgeschlossen blieb, und das Modell erhält dieselbe Liste. Der Ordner wird beim Senden gelesen: in der Unterhaltung bleibt nur der Pfad.
- **Reasoning (thinking)**: bei Modellen, die es unterstützen (qwen3, deepseek-r1…), aktiviert/deaktiviert die Schaltfläche 🧠 im Eingabefeld das Reasoning, und das Denken des Modells erscheint in der Blase in einem Block «💭 Gedanke» im Streaming, der beim Eintreffen der Antwort einklappbar und in der Unterhaltung gespeichert wird. Solange das Modell denkt, zeigt die Blase nicht nur Punkte: sie zeigt «denkt nach…» und dann den Reasoning-Text.
- **Web-Companion** (Eintrag «📱 Companion» in der Seitenleiste, `Strg+D`; standardmäßig aus): eine Seite für Smartphone und Tablet im lokalen Netzwerk, mit denselben Gesprächen wie der PC. Vom Telefon aus liest man die Chats, öffnet einen neuen oder setzt einen bestehenden fort: Modellwahl, Reasoning ein/aus (🧠) und Websuche ein/aus (🌐, mit dem auf dem PC gewählten Provider; bei jedem Öffnen der Seite startet sie aus und, solange sie an ist, erinnert die Schreibzeile daran) und die Antwort kommt im Streaming, mit der Schaltfläche ■ zum Abbrechen. Der PC generiert jeweils eine Antwort: antwortet er bereits, meldet das Telefon und wartet. PC und Telefon bleiben live synchron: eine von einer Seite geschriebene Nachricht erscheint auf der anderen, die Chatliste auf dem Telefon aktualisiert sich selbst und ein Punkt markiert das gerade antwortende Gespräch. Während einer Antwort bleibt der PC frei, andere Gespräche zu öffnen: die Antwort läuft im Hintergrund weiter und findet sich wieder, indem man ihren Chat erneut öffnet.
  - **Vom Telefon:** Anhänge mit 📎 (Fotos von Kamera oder Galerie, Textdateien und PDF, bis zu 20 MB jeweils): Fotos werden auf dem Telefon vor dem Upload verkleinert und der PC übergibt sie an die Modelle, die Bilder lesen. Über das Menü «⋯» eines Chats benennt man ihn um, archiviert (oder stellt wieder her) oder löscht die Unterhaltung, auch vom PC aus; Archivierte stehen in einem geschlossenen Bereich unten in der Liste; die Schaltfläche ⧉ unter jeder Nachricht kopiert deren Text. Mit 🧩 lädt man Modelle herunter (mit Fortschritt und «Abbrechen») und löscht sie: der Download erfolgt auf dem PC. Der Rest der Einstellungen bleibt nur auf dem PC.
  - **Fenster «Companion»:** versammelt alles an einem Ort: Aktivierung und Port, Status und Adresse (mit «Adresse kopieren»), Abgleich und Geräte. Wenn aktiv, öffnet auch die Schaltfläche 📱 in der oberen Leiste dasselbe Fenster.
  - **Abgleich:** im Fenster erscheinen die Adresse, ein 6-stelliger Code, der 2 Minuten gültig ist und nur einmal verwendet werden kann, und, falls `python3-qrcode` (`python-qrcode` unter Arch) installiert ist, ein QR-Code zum Einscannen. Das Telefon erhält ein Session-Cookie; «Geräte widerrufen» trennt alle (es werden höchstens 20 gemerkt).
  - **Auf dem Home-Bildschirm:** über das Browsermenü «Zum Home-Bildschirm hinzufügen» wird die Seite zu einem Icon mit dem OllaDesk-Logo. Auf iPhone und iPad öffnet sie sich bildschirmfüllend ohne Adressleiste, hat aber eigene Cookies getrennt von Safari: der Abgleich muss beim ersten Öffnen über das Icon wiederholt werden. Chrome auf Android erstellt eine Verknüpfung (die echte installierbare App erfordert HTTPS, verschoben auf eine spätere Version).
  - **Firewall:** der PC muss den Port (Standard 8765) **nur aus dem lokalen Netzwerk** akzeptieren, z. B. firewalld: `sudo firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=192.168.1.0/24 port port=8765 protocol=tcp accept'` und `sudo firewall-cmd --reload`; analog mit `ufw allow from 192.168.1.0/24 to any port 8765 proto tcp`. Ersetze `192.168.1.0/24` durch das Subnetz deines LAN (siehst du mit `ip -4 addr`).
  - **Bekannte Einschränkung:** die Verbindung ist unverschlüsseltes HTTP, daher kann jeder im selben WLAN das Cookie abfangen. In einem geschützten Heimnetz (WPA2/WPA3) ist das in Ordnung; öffne den Port nicht im Router. Keine externen Ressourcen: die Seite funktioniert auch ohne Internet.
- **Client für Ollama von Drittanbietern** (Smartphone-Apps, die mit der Ollama-API sprechen): die Freigabe der API der Versionen 0.2.x wurde in 0.3 entfernt, weil die Ollama-API keine Authentifizierung hat und der Web-Companion schon. Beim Update von einer 0.2.x mit aktiver Freigabe kehrt das Feld „Ollama-Server“, das auf deren Port zeigte, seit 0.3.6 von selbst zu `http://localhost:11434` zurück. Wer Ollama dennoch im Netzwerk exponieren will, konfiguriert es im Systemdienst, **auf eigene Verantwortung**: `sudo systemctl edit ollama`, dann im Abschnitt `[Service]` die Zeile `Environment="OLLAMA_HOST=0.0.0.0"` und `sudo systemctl restart ollama` (mit derselben Firewall-Regel für Port 11434). Wer den Port erreicht, kann die Modelle und die GPU nutzen: für Zugriff von außerhalb des Hauses besser eine VPN (z. B. Tailscale) oder einen SSH-Tunnel.
- **Tray-Icon**: das Fenster reduziert sich in den Benachrichtigungsbereich und wird mit einem Klick auf das Icon wiederhergestellt (Menü: Anzeigen/ausblenden, Beenden); optional reduziert das Schließen (X) in die Tray statt zu beenden, so dass Web-Companion und laufende Generierungen aktiv bleiben.
- **Modellverwaltung** (Schaltfläche «Modelle» in der Seitenleiste, Strg+M): Liste der installierten Modelle mit Größe/Parametern/Quantisierung, **Download** neuer Modelle mit Fortschrittsbalken und Abbruch sowie **Löschen**. Das für das Gespräch ausgewählte Modell ist mit «●» markiert. PC und Telefon teilen sich dieselbe Operation: jeweils eine zur Zeit, sichtbar und abbrechbar von beiden, ein Modell wird nicht gelöscht, während der PC antwortet, und nicht benutzt, während es gelöscht wird. Schließt man das Fenster, läuft der Download weiter; beim erneuten Öffnen sieht man den Fortschritt wieder. Mit Ollama 0.40.0, nach der Konvertierung der Modelle auf die neue Engine, meldet die Liste von Ollama denselben Namen zweimal plus einen internen Eintrag `llamacpp:<sha>` ([ollama/ollama#18830](https://github.com/ollama/ollama/issues/18830)): OllaDesk zeigt jedes Modell nur einmal und blendet den internen Eintrag aus (auf der Festplatte ändert sich nichts; `ollama list` zeigt sie weiterhin).
- **Websuche** (🌐 im Eingabefeld, grau wenn aus und blau wenn an): vor der Antwort sucht die App im Web und hängt die Ergebnisse an den Kontext. Unter der Antwort listet der einklappbare Block **«🌐 Quellen»** die genutzten Ergebnisse, mit derselben Nummerierung [1], [2]… die das Modell sieht (auch auf dem Telefon und in zuvor gespeicherten Gesprächen). **Wählbarer Provider** in den Einstellungen:
  - *DuckDuckGo* — ohne API-Schlüssel (kann vorübergehend durch seinen Anti-Bot-Schutz blockiert werden: in dem Fall meldet die App das deutlich);
  - *Ollama Cloud* — offizieller Endpoint `ollama.com/api/web_search`, erfordert den API-Schlüssel des ollama.com-Kontos;
  - *SearXNG* — deine eigene Instanz (z. B. `http://localhost:8888`), über die JSON-API: ideal lokal. Die Instanz muss JSON-Ausgabe aktiviert haben — in der `settings.yml` von SearXNG:

    ```yaml
    search:
      formats:
        - html
        - json
    ```

    Wenn die Instanz ohne Ergebnisse antwortet, weil ihre Engines nicht geantwortet haben (typisch bei kalten Verbindungen), versucht die App es einmal erneut und listet dann die fehlerhaften Engines: meist hilft es, `outgoing.request_timeout` zu erhöhen oder die Engines in der `settings.yml` zu wechseln. Werden duckduckgo oder brave blockiert (CAPTCHA, «too many requests»), deaktiviere sie und aktiviere bing oder startpage:

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

  Ein **optionaler Reserve-Provider** (Standard: keiner; niemals derselbe wie der Haupt-Provider) wird versucht, wenn der Haupt-Provider fehlschlägt oder nichts findet, und eine Notiz in der Chat sagt, welcher geantwortet hat. Die Nachricht erscheint sofort in der Chat, während die Suche läuft.
  Als Abfrage wird nur die erste Zeile der Nachricht verwendet (maximal 200 Zeichen), nicht der gesamte Text. Die Anzahl der Ergebnisse ist konfigurierbar. Die Modelle rufen das Tool nicht selbst auf: die Ergebnisse werden von der GUI in den Prompt injiziert (e2e verifiziert).
- **Ollama-Aktualisierungen** (Einstellungen → Oberfläche): Vergleich der Serverversion mit dem letzten Release auf GitHub, mit **Aktualisierung per Klick** über das offizielle Skript (ausgeführt mit `pkexec`, also mit dem Administratorpasswort von KDE).
- **OllaDesk-Aktualisierungen**: beim Start, höchstens einmal am Tag, prüft die App auf GitHub, ob eine neue Version erschienen ist, und zeigt in dem Fall einen Hinweis in der oberen Leiste mit Anweisungen, die zur Installationsart passen (.deb, AUR, AppImage, pip oder Quellen). Nur ein Hinweis: kein automatischer Download und keine Installation. Eine Version kann übersprungen, manuell geprüft oder die Prüfung in Einstellungen → Oberfläche deaktiviert werden.
- **Einstellungen → Reiter «Oberfläche»**: URL des Ollama-Servers, dunkles/helles/**System**-Thema (folgt KDE Plasma, auch live beim Wechsel), Schriftgröße, Streaming an/aus, Senden mit Enter, Uhrzeit in Nachrichten, Tray-Icon, Menge des Kontexts, der an das Modell gesendet wird, automatische Archivierung inaktiver Chats, Standard-Systemprompt, Ergebnisse der Websuche.
- **Einstellungen → Reiter «Modellparameter»**: separates Parameterprofil für jedes Modell (Temperatur, top_k, top_p, min_p, num_ctx, num_predict, Wiederholungs-/Präsenz-/Frequenz-Strafe, Seed, Stop-Sequenzen, mirostat, num_gpu, num_thread…). Nur die angehakten Parameter werden an Ollama gesendet: die anderen bleiben bei den Server-Standards. Jedes Profil kann **gespeichert** und mit einem Klick **auf die Standards zurückgesetzt** werden.
- Verbindungsstatus immer sichtbar (grün/rot) mit automatischer Neuprüfung alle 30 s.

## Voraussetzungen

- Python 3.10+ mit PySide6 (die AppImage enthält sie bereits)
- Ein laufender Ollama-Server (`ollama serve`) mit mindestens einem Modell

## Installation auf Arch Linux (AUR)

Das Paket [`olladesk`](https://aur.archlinux.org/packages/olladesk) ist auf AUR. Mit einem Helper:

```bash
yay -S olladesk
```

oder von Hand:

```bash
git clone https://aur.archlinux.org/olladesk.git && cd olladesk
curl -LO https://github.com/gradia64/OllaDesk/releases/latest/download/olladesk-release-key.asc
gpg --import olladesk-release-key.asc   # Fingerprint: siehe «Verifizierung der Signaturen»
makepkg -si
```

Ab 0.2.5 klont das PKGBUILD den Tag der Version von GitHub und makepkg prüft seine GPG-Signatur (`?signed` und `validpgpkeys` im PKGBUILD): fehlt die Release-Schlüssel in deinem Schlüsselbund oder ist es eine Kopie vor dem 03.10.2026 ohne die neuen Unterschlüssel, stoppt der Build. Importiere sie (oder reimportiere) aus der Datei `olladesk-release-key.asc`, die jeder Release beiliegt (`gpg --import olladesk-release-key.asc`), nachdem du den Fingerprint geprüft hast. Jede Release enthält auch das bereits gebaute Paket (`olladesk-<version>-1-any.pkg.tar.zst`, installierbar mit `sudo pacman -U`).

## AppImage (beliebige Distribution)

Ab 0.3.4 enthält jede Release `OllaDesk-<version>-x86_64.AppImage`: eine einzige Datei mit Python, Qt und den optionalen Abhängigkeiten (PDF, Schlüsselbund, QR-Code) darin, zum Herunterladen und Starten ohne Installation.

```bash
chmod +x OllaDesk-*-x86_64.AppImage
./OllaDesk-*-x86_64.AppImage
```

Vor dem Start prüfe die Signatur, wie bei den anderen Dateien (Abschnitt «Verifizierung der Signaturen»):

```bash
gpg --verify OllaDesk-0.3.4-x86_64.AppImage.sig OllaDesk-0.3.4-x86_64.AppImage
```

**Voraussetzungen.** 64-Bit-x86_64-System mit glibc 2.34 oder neuer: Debian 12, Ubuntu 22.04, Fedora 35, openSUSE Tumbleweed oder neuer (das ist das Minimum von PySide6). Vom System nutzt die AppImage nur Bibliotheken, die jeder Desktop schon hat: OpenGL/EGL, xkbcommon, fontconfig, D-Bus, GLib, Kerberos (`libgssapi_krb5`) und X11 oder Wayland. Zum Einhängen braucht sie FUSE (`fusermount3`, Paket `fuse3`); wo das fehlt, z. B. in einem Container, startet sie mit `./OllaDesk-*.AppImage --appimage-extract-and-run`. Ollama ist nicht enthalten: separat installieren.

**Menü und Icon.** So gestartet erscheint OllaDesk nicht im Menü und hat in der Anwendungsleiste ein generisches Icon. Zur Integration nutze [Gear Lever](https://flathub.org/apps/it.mijorus.gearlever) oder [AppImageLauncher](https://github.com/TheAssassin/AppImageLauncher): sie kopieren die AppImage in einen festen Ordner und installieren Menüeintrag und Icon, das unter Plasma auch in der Leiste erscheint. Von Hand, aus dem Ordner, in dem du die AppImage aufbewahrst:

```bash
./OllaDesk-*-x86_64.AppImage --appimage-extract 'usr/share/*'   # nur usr/share, etwa 1 MB
sed "s|^Exec=.*|Exec=$PWD/$(ls OllaDesk-*-x86_64.AppImage)|" \
    squashfs-root/usr/share/applications/io.github.gradia64.OllaDesk.desktop \
    > ~/.local/share/applications/io.github.gradia64.OllaDesk.desktop
install -Dm644 squashfs-root/usr/share/icons/hicolor/scalable/apps/io.github.gradia64.OllaDesk.svg \
    ~/.local/share/icons/hicolor/scalable/apps/io.github.gradia64.OllaDesk.svg
rm -r squashfs-root
```

**Aktualisierungen.** Die AppImage aktualisiert sich nicht selbst: wenn eine neue Version erscheint, meldet OllaDesk das (Einstellungen → Oberfläche) und nennt die zu ladende Datei. Man ersetzt einfach die alte `.AppImage` durch die neue (mit Gear Lever oder AppImageLauncher, indem man die neue Datei öffnet). Bei der manuellen Integration steht der Dateiname im `.desktop`: nach der Aktualisierung die obigen Befehle wiederholen.

**Zu wissen.** Chats, Einstellungen und abgeglichene Geräte liegen in `~/.config/olladesk`, demselben Ordner wie beim `.deb` und beim AUR-Paket: beim Wechsel zur AppImage findest du alles wieder, aber öffne nicht zwei OllaDesk gleichzeitig. Der API-Schlüssel läuft über den Schlüsselbund via Secret Service (KWallet unter Plasma, GNOME Keyring anderswo). Nur x86_64: keine Builds für ARM.

## Installation auf Debian

### Aus dem .deb-Paket (empfohlen)

Lade `olladesk_<version>_all.deb` aus der letzten [Release auf GitHub](https://github.com/gradia64/OllaDesk/releases/latest) herunter und installiere es: apt löst die Abhängigkeiten von selbst auf (Python 3.10+ und die PySide6-Module von Qt core/gui/widgets/network):

```bash
sudo apt install ./olladesk_*_all.deb
```

Das Paket installiert den Launcher `/usr/bin/olladesk`, den Eintrag «OllaDesk» im Anwendungsmenü mit dem zugehörigen Icon, die Manpage und das Changelog; Deinstallation mit `sudo apt remove olladesk`.

### Verifizierung der Signaturen

Ab 0.2.3 hat jede Datei der Release eine abgetrennte GPG-Signatur (`.sig`), erstellt mit dem Release-Schlüssel des Projekts, veröffentlicht als `olladesk-release-key.asc` in der Release und in `packaging/` im Repository. Ab 0.2.5 signieren zwei Unterschlüssel derselben Schlüssels mit getrennten Rollen: einer die Git-Tags (nur der Maintainer), der andere die Anhänge (die CI). Der zu prüfende Fingerprint bleibt der des Hauptschlüssels:

```
gradia (OllaDesk release signing) <gradia@disroot.org>
ed25519  5B16 6C1B 4AD7 428C 74A0  7D5B A337 0987 A057 6694
```

Nach dem Import prüfe, dass der von gpg angezeigte Fingerprint übereinstimmt, dann:

```bash
gpg --import olladesk-release-key.asc
gpg --verify olladesk_0.3.4_all.deb.sig olladesk_0.3.4_all.deb
# oder alles auf einmal:
gpg --verify SHA256SUMS.sig SHA256SUMS && sha256sum -c --ignore-missing SHA256SUMS
```

Um das Paket aus den Quellen zu bauen (braucht `dpkg-deb`, vorhanden auf jedem Debian):

```bash
scripts/build-deb.sh        # erzeugt dist/olladesk_<version>_all.deb
```

### Aus den Quellen

Mit den Debian-Paketen:

```bash
sudo apt install python3-pyside6
```

oder via pip (virtuelle Umgebung):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

dann aus dem Projektordner starten:

```bash
python3 main.py
# oder
python3 -m olladesk
```

### Als Python-Paket (pipx/pip)

```bash
pipx install .            # oder: pip install --user .
olladesk                  # von pyproject.toml erzeugter Entry Point
```

Im `.desktop` kann man dann `Exec=olladesk` statt des absoluten Pfads verwenden.

### Integration mit KDE Plasma (Anwendungsmenü)

```bash
cp io.github.gradia64.OllaDesk.desktop ~/.local/share/applications/
install -Dm644 olladesk/assets/olladesk.svg \
    ~/.local/share/icons/hicolor/scalable/apps/io.github.gradia64.OllaDesk.svg
update-desktop-database ~/.local/share/applications
```

Das `.desktop` verwendet `Exec=olladesk` (der mit `pipx`/`pip` installierte Entry Point) und `Icon=io.github.gradia64.OllaDesk` (das oben in das `hicolor`-Thema kopierte Icon). Startet man die App aus den Quellen ohne Installation, ersetze `Exec=` durch `python3 /pfad/zu/OllaDesk/main.py`.

Bis 0.3.3 hießen die Dateien `olladesk.desktop` und `olladesk.svg`: hatte man sie von Hand kopiert, lösche sie, um OllaDesk nicht zweimal im Menü zu haben.

## Tastenkürzel

| Tastenkürzel | Aktion |
|--------------|--------|
| `Ctrl+N` | Neues Gespräch |
| `Ctrl+B` | Seitenleiste anzeigen/ausblenden |
| `Ctrl+M` | Modellverwaltung (Download/Entfernung) |
| `Ctrl+D` | Web-Companion (Telefon und Tablet) |
| `Ctrl+,` | Einstellungen öffnen |
| `Enter` | Nachricht senden (aktivierbar) |
| `Ctrl+Enter` | Nachricht senden (immer) |
| `Shift+Enter` | Zeilenumbruch |

## Modellparameter

Im Reiter «Modellparameter» hat jede Zeile ein Anpassungskästchen: **nur die angehakten Parameter** werden in die Option `options` der Anfrage an `/api/chat` aufgenommen; die anderen bleiben bei den Ollama-Standards, sodass «Auf Standards zurücksetzen» gleichbedeutend damit ist, keine Anpassung mehr zu senden.

Die Profile werden pro Modell in `~/.config/olladesk/model_params.json` gespeichert und automatisch auf das im Chat ausgewählte Modell angewendet.

## Konfigurationsdateien

Alles wird in `$XDG_CONFIG_HOME/olladesk` (standardmäßig `~/.config/olladesk`) gespeichert. Existiert eine Konfiguration des alten Namens (`~/.config/ollama-gui`), wird sie beim ersten Start automatisch migriert:

| Pfad | Inhalt |
|------|--------|
| `settings.json` | Einstellungen der GUI (der API-Schlüssel wandert, falls verfügbar, in den KDE-Schlüsselbund) |
| `model_params.json` | Parameterprofile pro Modell |
| `chats/index.json` | Index der Gespräche (nur Metadaten) |
| `chats/<id>.json` | Ein Gespräch pro Datei: das Speichern einer Nachricht schreibt das Archiv nicht neu |
| `attachments/` | Aus der Zwischenablage eingefügte Bilder (privater Ordner, Berechtigungen 700) |

Die alte monolithische `chats.json` wird beim ersten Start automatisch migriert (als `chats.json.bak` belassen). Anhänge werden per Pfad referenziert und ihr Text wird nur im Zug, in dem sie gesendet werden, an den Kontext angehängt.

## Tests

Offscreen-Smoke-Test (startet die GUI, chattet wirklich mit Ollama, speichert/wiederherstellt Parameter und speichert Screenshots in `/tmp/olladesk_shots`):

```bash
python3 tests/gui_smoke.py      # Grundfunktionen
python3 tests/gui_features.py   # Anhänge, Websuche, Modelle, Aktualisierungen
python3 tests/gui_errors.py     # Fehlerpfade (Server offline)
python3 tests/unit_test.py      # Unit-Tests der reinen Funktionen (ohne Netz)
python3 tests/repro_qthread_crash.py  # Schließen von Dialogen mit blockierten Workern (ohne Netz)
python3 tests/release_tools_test.py   # Release-Skripte: Signatur von Tags und Anhängen (ohne Netz)
```

Die Skripte `gui_*.py` sind End-to-End-Prüfungen (benötigen Ollama und für die Websuche das Netz). `pytest` sammelt nur `tests/unit_test.py`; die anderen netzfreien Skripte (`release_tools_test.py`, `engine_test.py`, die Companion-Tests und `sync_test.py`) haben ihren eigenen Runner und werden mit `python3` ausgeführt (`tests/conftest.py`). Die Testkonfiguration lebt bei jedem Lauf in einem neuen temporären Ordner.

```bash
python3 tests/offline_app_update.py   # Aktualisierungsprüfung mit gefälschtem GitHub (ohne Netz)
python3 tests/engine_test.py          # Chat-Engine mit gefälschtem Ollama (ohne Netz)
python3 tests/companion_test.py       # Web-Companion: Server, Abgleich, Lesen (ohne Netz)
python3 tests/companion_send_test.py  # Web-Companion: Senden und SSE-Streaming (ohne Netz)
python3 tests/sync_test.py            # Live-Synchronisation Telefon-PC (ohne Netz)
python3 tests/companion_models_test.py  # Web-Companion: Modelle herunterladen und löschen (ohne Netz)
```

Die CI (`.github/workflows/release.yml`) führt Unit-Tests und Offline-Tests bei jedem Push und Pull Request aus; bei Tags `v*` baut sie Tarball, `.deb`, AppImage und Arch-Paket, signiert sie und veröffentlicht sie (GitHub-Release und AUR). Die AppImage wird mit `packaging/appimage/smoke-test.sh` getestet, das die Offline-Tests mit ihrem Python ausführt. Details für Veröffentlichende: [packaging/README.md](packaging/README.md).

Zum Lesen von PDFs als Anhänge:

```bash
pip install pypdf    # oder: sudo apt install python3-pypdf
```

## Projektstruktur

```
main.py                     Einstiegspunkt
olladesk/
  app.py                    Start der Anwendung
  config.py                 Persistenz (Einstellungen, Parameter, Chat) + Migration
  theme.py                  dunkle/helle/System-Themen (Fusion + QSS)
  md.py                     Markdown → HTML (Subset von Qt Rich Text)
  i18n.py                   Übersetzungen der Oberfläche (Kataloge in locale/*.po)
  context.py                Anhänge: Text/PDF/Bilder als Kontext
  office.py                 Text aus .docx/.doc/.xlsx/.xls/.odt/.ods
  web_search.py             Websuche (DuckDuckGo/Ollama Cloud/SearXNG) + Worker
  updater.py                Ollama-Aktualisierungen (GitHub-Release + pkexec)
  app_update.py             OllaDesk-Aktualisierungen (nur Prüfung und Hinweis)
  ollama_client.py          Ollama-API-Client (stdlib) + Worker QThread
  netinfo.py                lokale Netzwerkadressen (für den Web-Companion)
  engine.py                 Chat-Engine: Gespräche, Generierung, Speicherung
  workers.py                Register der aktiven und zu beendenden QThread-Worker
  companion.py              Web-Companion: HTTP-Server im LAN und Abgleich
  web/                      mobile Seite des Companions (HTML, CSS, JS, Manifest und Icons)
  main_window.py            Hauptfenster, Client der Chat-Engine
  widgets/
    chat_area.py            Nachrichtenbereich + Eingabe (Anhänge, Websuche)
    message.py              Nachrichten-Bubbles
    sidebar.py              Gesprächsliste
    model_params.py         Definitionen und Editor der Parameter
    steppers.py             numerische Felder mit runden −/+-Schaltflächen
    model_manager.py        Dialog zum Herunterladen/Löschen von Modellen
    settings_dialog.py      Einstellungsfenster (2 Reiter + Aktualisierungen)
    companion_dialog.py     Companion-Fenster: Dienst, Abgleich, Geräte
tests/gui_smoke.py          automatischer Offscreen-Collaudo (Basis)
tests/gui_features.py       automatischer Offscreen-Collaudo (zusätzliche Funktionen)
tests/gui_errors.py         Collaudo der Fehlerpfade (offline)
tests/unit_test.py          Unit-Tests der reinen Funktionen
tests/repro_qthread_crash.py Regression: zerstörte Dialoge mit blockierten Workern
tests/offline_app_update.py Aktualisierungsprüfung von OllaDesk (gefälschtes GitHub)
tests/engine_test.py        Chat-Engine (gefälschtes Ollama)
tests/companion_test.py     Web-Companion (Server, Abgleich, Lesen)
tests/companion_send_test.py Web-Companion (Senden, SSE-Streaming, Stop)
tests/sync_test.py          Live-Synchronisation Telefon-PC
tests/companion_models_test.py Web-Companion (Modelle herunterladen und löschen)
tests/fake_ollama.py        gefälschter Ollama-Server für Offline-Tests
tests/companion_client.py   HTTP/SSE-Client für die Companion-Tests
tests/release_tools_test.py Release-Skripte (Schlüssel und temporäre Repositorys)
io.github.gradia64.OllaDesk.desktop  Eintrag für das Anwendungsmenü (App-ID)
io.github.gradia64.OllaDesk.metainfo.xml  AppStream-Metadaten (Softwarezentren)
packaging/                  gemeinsame Dateien für .deb/Arch, PKGBUILD-Vorlage, AppImage, Release-Schlüssel
scripts/                    Build (.deb, AppImage, Quellen, Arch), Signatur, AUR-Veröffentlichung,
                            Web-Icons, Screenshots (docs/screenshots/, für AppStream),
                            i18n (Aktualisierung/Prüfung der gettext-Kataloge)
```

## Lizenz

Dieses Projekt wird unter der **GNU General Public License v3.0** veröffentlicht — siehe die Datei [LICENSE](LICENSE).

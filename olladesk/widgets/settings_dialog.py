"""Finestra impostazioni con due schede: «Interfaccia» e «Parametri modelli».

La scheda interfaccia include anche «Aggiornamenti OllaDesk» (solo controllo
e avviso, vedi app_update.py) e la sezione «Aggiornamenti Ollama»:
controllo dell'ultima release su GitHub e aggiornamento tramite lo script
ufficiale, scaricato in memoria e passato a sh via stdin (pkexec chiede la
password di amministratore).
"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, app_update, config, secrets_store
from .. import i18n
from ..i18n import _
from ..ollama_client import shutdown_workers
from .model_params import ModelParamsTab
from .ollama_update import OllamaUpdateBox
from .steppers import IntStepper


class SettingsDialog(QDialog):
    """Emette `applied(settings)` quando l'utente conferma con «Salva»."""

    applied = Signal(dict)
    # esito di «Verifica ora» per OllaDesk: dict {"version", "url"} o None
    appReleaseChecked = Signal(object)

    def __init__(
        self,
        settings: dict,
        models_provider,
        theme_name: str,
        version: str = "?",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(_("Impostazioni — OllaDesk"))
        # la scroll area della scheda interfaccia rende sicure anche le
        # dimensioni piccole: le schermate scorrono invece di sovrapporsi
        self.setMinimumSize(720, 480)
        self._theme_name = theme_name
        self._installed_version = version
        self._app_check_worker: app_update.AppUpdateCheckWorker | None = None
        # impostazioni non modificabili qui (es. data dell'ultimo controllo
        # aggiornamenti): «Salva» deve conservarle, non riportarle ai default
        self._base_settings = dict(settings)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)

        self.tabs = QTabWidget(self)
        lay.addWidget(self.tabs, 1)

        self.tabs.addTab(self._build_gui_tab(settings), _("🖥  Interfaccia"))
        self.params_tab = ModelParamsTab(models_provider, self)
        self.tabs.addTab(self.params_tab, _("⚙  Parametri modelli"))

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton(_("Annulla"), self)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        save = QPushButton(_("💾  Salva"), self)
        save.setObjectName("primaryBtn")
        save.clicked.connect(self._on_save)
        btns.addWidget(save)
        lay.addLayout(btns)

    # ---------------------------------------------------- scheda interfaccia

    def _build_gui_tab(self, s: dict) -> QWidget:
        # tutto il contenuto viaggia in una scroll area: se la finestra è più
        # piccola dei contenuti compare la barra di scorrimento invece delle
        # sovrapposizioni
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        w = QWidget()
        scroll.setWidget(w)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._gui_form = form

        self.host_edit = QLineEdit(s.get("host", ""), w)
        self.host_edit.setPlaceholderText("http://localhost:11434")
        self.host_edit.setToolTip(_("URL del server Ollama (API locale)"))
        form.addRow(_("Server Ollama:"), self.host_edit)

        self.theme_combo = QComboBox(w)
        self.theme_combo.addItem(_("🖥  Sistema (come KDE Plasma)"), "system")
        self.theme_combo.addItem(_("🌙  Scuro"), "dark")
        self.theme_combo.addItem(_("☀  Chiaro"), "light")
        idx = self.theme_combo.findData(s.get("theme", "dark"))
        self.theme_combo.setCurrentIndex(max(0, idx))
        form.addRow(_("Tema:"), self.theme_combo)

        self.lang_combo = QComboBox(w)
        self.lang_combo.addItem(_("Automatica (lingua del sistema)"), "auto")
        for code, name in i18n.LANGUAGES.items():
            self.lang_combo.addItem(name, code)
        self._initial_language = s.get("language", "auto")
        idx = self.lang_combo.findData(self._initial_language)
        self.lang_combo.setCurrentIndex(max(0, idx))
        self.lang_combo.setToolTip(_("La nuova lingua vale dal prossimo avvio di OllaDesk"))
        form.addRow(_("Lingua:"), self.lang_combo)

        self.font_spin = IntStepper(w)
        self.font_spin.setRange(8, 16)
        self.font_spin.setValue(int(s.get("font_size", 10)))
        self.font_spin.setSuffix(" pt")
        form.addRow(_("Dimensione carattere:"), self.font_spin)

        self.stream_chk = QCheckBox(_("Mostra la risposta token per token"), w)
        self.stream_chk.setChecked(bool(s.get("stream", True)))
        form.addRow(_("Streaming:"), self.stream_chk)

        self.enter_chk = QCheckBox(_("Invia il messaggio con il tasto Invio"), w)
        self.enter_chk.setChecked(bool(s.get("send_on_enter", True)))
        form.addRow(_("Invio rapido:"), self.enter_chk)

        self.ts_chk = QCheckBox(_("Mostra l'orario accanto ai messaggi"), w)
        self.ts_chk.setChecked(bool(s.get("show_timestamps", False)))
        form.addRow(_("Orario:"), self.ts_chk)

        self.tray_chk = QCheckBox(
            _("Mostra l'icona di OllaDesk nell'area di notifica (tray)"), w
        )
        self.tray_chk.setChecked(bool(s.get("tray_icon", True)))
        self.tray_chk.setToolTip(
            _("L'icona nella tray permette di mostrare/nascondere la finestra con un clic.")
        )
        form.addRow(_("Tray:"), self.tray_chk)

        self.close_tray_chk = QCheckBox(
            _("La chiusura (X) riduce nella tray invece di uscire"), w
        )
        self.close_tray_chk.setChecked(bool(s.get("close_to_tray", True)))
        self.close_tray_chk.setToolTip(
            _("Per uscire davvero: icona nella tray → tasto destro → «Esci».\n"
            "Con la finestra nascosta la companion web resta attiva.")
        )
        form.addRow("", self.close_tray_chk)
        # ha senso solo con l'icona in tray: il collegamento va fatto DOPO la
        # creazione di entrambe le caselle
        self.tray_chk.toggled.connect(self.close_tray_chk.setEnabled)
        self.close_tray_chk.setEnabled(self.tray_chk.isChecked())

        self.hist_spin = IntStepper(w)
        self.hist_spin.setRange(0, 200)
        self.hist_spin.setValue(int(s.get("history_limit", 20)))
        self.hist_spin.setSuffix(_(" messaggi"))
        self.hist_spin.setToolTip(_("Quanti messaggi precedenti vengono inviati al modello come contesto"))
        form.addRow(_("Contesto inviato al modello:"), self.hist_spin)

        self.archive_spin = IntStepper(w)
        self.archive_spin.setRange(0, 3650)
        try:
            self.archive_spin.setValue(int(s.get("archive_after_days") or 0))
        except (TypeError, ValueError):
            self.archive_spin.setValue(0)
        self.archive_spin.setSuffix(_(" giorni"))
        self.archive_spin.setSpecialValueText(_("mai"))
        self.archive_spin.setToolTip(
            _("Le conversazioni senza messaggi da questo numero di giorni vanno\n"
            "nell'archivio (in fondo alla colonna di sinistra), all'avvio.\n"
            "Non vengono eliminate. «mai»: si archivia solo a mano (tasto destro).")
        )
        form.addRow(_("Archivia le chat inattive dopo:"), self.archive_spin)

        self.web_spin = IntStepper(w)
        self.web_spin.setRange(3, 10)
        self.web_spin.setValue(int(s.get("web_results", 5)))
        self.web_spin.setSuffix(_(" risultati"))
        self.web_spin.setToolTip(_("Numero di risultati web allegati quando la ricerca web è attiva (🌐)"))
        form.addRow(_("Risultati ricerca web:"), self.web_spin)

        self.web_provider_combo = QComboBox(w)
        self.web_provider_combo.addItem(_("DuckDuckGo (senza chiave API)"), "duckduckgo")
        self.web_provider_combo.addItem(_("Ollama Cloud — ollama.com (chiave API)"), "ollama")
        self.web_provider_combo.addItem(_("SearXNG (istanza personale)"), "searxng")
        idx = self.web_provider_combo.findData(s.get("web_provider", "duckduckgo"))
        self.web_provider_combo.setCurrentIndex(max(0, idx))
        self.web_provider_combo.currentIndexChanged.connect(
            lambda _i: (self._sync_fallback_choices(), self._update_provider_fields())
        )
        form.addRow(_("Provider ricerca web:"), self.web_provider_combo)

        self.web_fallback_combo = QComboBox(w)
        self.web_fallback_combo.addItem(_("Nessuno"), "")
        self.web_fallback_combo.addItem("DuckDuckGo", "duckduckgo")
        self.web_fallback_combo.addItem(_("Ollama Cloud — ollama.com"), "ollama")
        self.web_fallback_combo.addItem("SearXNG", "searxng")
        idx = self.web_fallback_combo.findData(s.get("web_fallback", ""))
        self.web_fallback_combo.setCurrentIndex(max(0, idx))
        self.web_fallback_combo.setToolTip(
            _("Provider da provare se il principale fallisce (blocco anti-bot, nessun\n"
            "risultato, rete): una nota in chat dice quale ha risposto. «Nessuno»\n"
            "non manda mai la domanda a un servizio diverso da quello scelto.")
        )
        self.web_fallback_combo.currentIndexChanged.connect(
            lambda _i: self._update_provider_fields()
        )
        self._sync_fallback_choices()
        form.addRow(_("Provider di riserva:"), self.web_fallback_combo)

        self.web_key_edit = QLineEdit("", w)
        self.web_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._keyring_ok = secrets_store.available()
        self._file_key = s.get("web_api_key", "")
        if self._keyring_ok:
            # migrazione: una chiave salvata nel file passa al portachiavi
            if self._file_key and not secrets_store.load_api_key():
                secrets_store.save_api_key(self._file_key)
            stored = bool(secrets_store.load_api_key())
            self.web_key_edit.setPlaceholderText(
                _("lascia vuoto per mantenere quella nel portachiavi di KDE")
                + (_(" (una chiave è già salvata)") if stored else _(" (nessuna chiave salvata)"))
            )
            self.web_key_edit.setToolTip(
                _("Chiave creabile su ollama.com → Impostazioni → API Keys.\n"
                "Viene salvata nel portachiavi di sistema (KWallet), mai nei file di configurazione.")
            )
        else:
            self.web_key_edit.setText(self._file_key)
            self.web_key_edit.setPlaceholderText(_("chiave API dell'account ollama.com"))
            self.web_key_edit.setToolTip(
                _("Chiave creabile su ollama.com → Impostazioni → API Keys.\n"
                "Portachiavi non disponibile: la chiave resta in settings.json "
                "(file protetto con permessi 600). Installa `keyring` per salvarla in KWallet.")
            )
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.addWidget(self.web_key_edit, 1)
        self.clear_key_btn = QPushButton(_("Rimuovi"), w)
        self.clear_key_btn.setToolTip(_("Elimina la chiave API salvata"))
        self.clear_key_btn.clicked.connect(self._clear_api_key)
        key_row.addWidget(self.clear_key_btn)
        self._key_row = QWidget(w)
        self._key_row.setLayout(key_row)
        self._clear_key = False
        form.addRow(_("Chiave API:"), self._key_row)

        self.searxng_edit = QLineEdit(s.get("web_searxng_url", ""), w)
        self.searxng_edit.setPlaceholderText("http://localhost:8888")
        self.searxng_edit.setToolTip(
            _("URL base della tua istanza SearXNG (deve accettare format=json)\n"
            "es. `searxng-run` in Docker: docker run -p 8888:8080 searxng/searxng")
        )
        form.addRow(_("SearXNG — URL:"), self.searxng_edit)

        self.sys_prompt = QPlainTextEdit(s.get("system_prompt", ""), w)
        self.sys_prompt.setPlaceholderText(_("(vuoto = nessun prompt di sistema)"))
        self.sys_prompt.setFixedHeight(64)
        self.sys_prompt.setToolTip(
            _("Prompt di sistema inviato a Ollama con ogni richiesta (vale per tutte le conversazioni)")
        )
        form.addRow(_("Prompt di sistema:"), self.sys_prompt)

        lay.addLayout(form)


        info = QLabel(
            _('Impostazioni, parametri e conversazioni sono salvati in:\n{path}').format(path=config.config_dir()),
            w,
        )
        info.setObjectName("metaLabel")
        info.setWordWrap(True)
        lay.addWidget(info)
        lay.addStretch(1)

        self._update_provider_fields()
        lay.addWidget(self._build_app_update_group(w, s))
        self.ollama_update = OllamaUpdateBox(lambda: self.host_edit.text().strip(),
                                             self._installed_version, w)
        self.ollama_update_chk = QCheckBox(
            _("Controlla automaticamente all'avvio (al massimo una volta al giorno)"), self.ollama_update
        )
        self.ollama_update_chk.setChecked(bool(s.get("ollama_update_check", True)))
        self.ollama_update_chk.setToolTip(
            _("Contatta solo api.github.com per leggere l'ultima versione di Ollama: se è più "
              "recente, l'indicatore in alto a destra lo segnala. Nessuna installazione automatica.")
        )
        self.ollama_update.layout().insertWidget(0, self.ollama_update_chk)
        lay.addWidget(self.ollama_update)
        return page

    def _sync_fallback_choices(self) -> None:
        """Il principale non può fare da riserva: la ricerca lo ignorerebbe
        e si resterebbe senza riserva senza saperlo (es. SearXNG con riserva
        SearXNG). La sua voce si disattiva e, se era scelta, torna «Nessuno»."""
        combo = getattr(self, "web_fallback_combo", None)
        if combo is None:
            return
        provider = self.web_provider_combo.currentData()
        model = combo.model()
        for i in range(combo.count()):
            item = model.item(i)
            if item is not None:
                item.setEnabled(combo.itemData(i) != provider)
        if combo.currentData() == provider:
            # a segnali bloccati: chi chiama aggiorna poi i campi visibili
            # (durante la costruzione del dialogo non esistono ancora)
            combo.blockSignals(True)
            combo.setCurrentIndex(combo.findData(""))
            combo.blockSignals(False)

    def _update_provider_fields(self) -> None:
        """Mostra i campi del provider selezionato (chiave per Ollama, URL per SearXNG)."""
        used = {self.web_provider_combo.currentData()}
        fallback = getattr(self, "web_fallback_combo", None)
        if fallback is not None:
            used.add(fallback.currentData())
        form = getattr(self, "_gui_form", None)
        if form is None:
            return
        # campi del principale E della riserva (es. SearXNG con riserva Ollama)
        form.setRowVisible(self._key_row, "ollama" in used)
        form.setRowVisible(self.searxng_edit, "searxng" in used)


    def _fallback_value(self) -> str:
        fallback = self.web_fallback_combo.currentData() or ""
        return "" if fallback == self.web_provider_combo.currentData() else fallback

    def _clear_api_key(self) -> None:
        """Segna la chiave per la rimozione (effettiva con «Salva»)."""
        self._clear_key = True
        self.web_key_edit.clear()
        self.web_key_edit.setPlaceholderText(_("la chiave verrà rimossa al salvataggio"))

    # ---------------------------------------------- aggiornamenti OllaDesk

    def _build_app_update_group(self, parent: QWidget, s: dict) -> QGroupBox:
        box = QGroupBox(_("Aggiornamenti OllaDesk"), parent)
        lay = QVBoxLayout(box)
        lay.setSpacing(8)

        self._install_method = app_update.install_method()
        method_label = {
            "deb": _("pacchetto .deb"), "arch": _("pacchetto AUR"), "appimage": "AppImage",
            "flatpak": "Flatpak",
            "source": _("sorgenti (git)"), "pip": _("pacchetto Python"),
        }[self._install_method]
        row = QHBoxLayout()
        row.addWidget(QLabel(_('Versione in uso: <b>{version}</b> ({method_label})').format(version=__version__, method_label=method_label), box))
        row.addStretch(1)
        self.app_check_btn = QPushButton(_("🔍  Verifica ora"), box)
        self.app_check_btn.clicked.connect(self._check_app_update)
        row.addWidget(self.app_check_btn)
        lay.addLayout(row)

        self.app_update_chk = QCheckBox(
            _("Controlla automaticamente all'avvio (al massimo una volta al giorno)"), box
        )
        self.app_update_chk.setChecked(bool(s.get("app_update_check", True)))
        self.app_update_chk.setToolTip(
            _("Contatta solo api.github.com per leggere il numero dell'ultima release.\n"
            "Nessun download né installazione automatica: solo un avviso.")
        )
        lay.addWidget(self.app_update_chk)

        self.app_update_status = QLabel("", box)
        self.app_update_status.setObjectName("metaLabel")
        self.app_update_status.setWordWrap(True)
        self.app_update_status.setTextFormat(Qt.TextFormat.RichText)
        self.app_update_status.setOpenExternalLinks(True)
        lay.addWidget(self.app_update_status)
        return box

    def _check_app_update(self) -> None:
        if self._app_check_worker is not None:
            return
        self.app_check_btn.setEnabled(False)
        self.app_update_status.setText(_("Controllo l'ultima release su GitHub…"))
        w = app_update.AppUpdateCheckWorker(self)
        w.ready.connect(self._on_app_release)
        w.failed.connect(self._on_app_release_failed)
        w.finished.connect(w.deleteLater)
        w.finished.connect(self._clear_ref("_app_check_worker", w))
        self._app_check_worker = w
        w.start()

    def _on_app_release(self, release: object) -> None:
        self.app_check_btn.setEnabled(True)
        release = release if isinstance(release, dict) else None
        self._base_settings["app_update_last_check"] = config.now()
        self.appReleaseChecked.emit(release)
        newer = app_update.newer_release(release)
        if release is None:
            # GitHub risponde 404 sia senza release sia con repository privato
            self.app_update_status.setText(
                _("Nessuna release pubblica trovata su GitHub "
                "(repository privato o senza release).")
            )
        elif newer is None:
            self.app_update_status.setText(_('✓ OllaDesk è aggiornato (ultima release: {version}).').format(version=release['version']))
        else:
            url = html.escape(app_update.download_page(self._install_method)
                              if self._install_method == "arch" else newer["url"], quote=True)
            hint = html.escape(app_update.update_hint(self._install_method, newer["version"]))
            self.app_update_status.setText(
                _('✦ Disponibile OllaDesk <b>{version}</b>. {hint}<br><a '
                  'href="{url}">Apri la pagina di download</a>').format(version=html.escape(newer['version']), hint=hint, url=url)
            )

    def _on_app_release_failed(self, err: str) -> None:
        self.app_check_btn.setEnabled(True)
        self.app_update_status.setText(_('⚠ Verifica non riuscita: {err}').format(err=html.escape(err)))

    # ------------------------------------------------ aggiornamenti Ollama

    # ------------------------------------------------------------------ azioni

    def collect_settings(self) -> dict:
        return {
            **self._base_settings,
            "app_update_check": self.app_update_chk.isChecked(),
            "ollama_update_check": self.ollama_update_chk.isChecked(),
            "host": self._normalized_host(),
            "theme": self.theme_combo.currentData() or "dark",
            "language": self.lang_combo.currentData() or "auto",
            "font_size": self.font_spin.value(),
            "stream": self.stream_chk.isChecked(),
            "send_on_enter": self.enter_chk.isChecked(),
            "show_timestamps": self.ts_chk.isChecked(),
            "tray_icon": self.tray_chk.isChecked(),
            "close_to_tray": self.close_tray_chk.isChecked(),
            "history_limit": self.hist_spin.value(),
            "archive_after_days": self.archive_spin.value(),
            "web_results": self.web_spin.value(),
            "web_provider": self.web_provider_combo.currentData() or "duckduckgo",
            "web_fallback": self._fallback_value(),
            # con il portachiavi la chiave NON finisce mai nel file di config
            "web_api_key": self._file_api_key(),
            "web_searxng_url": self.searxng_edit.text().strip() or "http://localhost:8888",
            "system_prompt": self.sys_prompt.toPlainText().strip(),
        }

    def _file_api_key(self) -> str:
        if self._keyring_ok:
            # _file_key resta solo se il portachiavi ha rifiutato il salvataggio
            return self._file_key
        return "" if self._clear_key else self.web_key_edit.text().strip()

    def _normalized_host(self) -> str:
        """Completa l'URL con lo schema se manca (evita errori opachi)."""
        host = self.host_edit.text().strip() or config.DEFAULT_SETTINGS["host"]
        if not host.startswith(("http://", "https://")):
            host = "http://" + host
        return host

    def _clear_ref(self, attr: str, w):
        """Slot per `finished`: azzera `attr` solo se punta ancora a `w`."""
        def clear() -> None:
            if getattr(self, attr, None) is w:
                setattr(self, attr, None)
        return clear

    def _update_running(self) -> bool:
        return self.ollama_update.is_running()

    def _on_save(self) -> None:
        # come reject(): chiudere il dialogo ora distruggerebbe il QProcess
        # dell'aggiornamento di Ollama ancora in corsa
        if self._update_running():
            QMessageBox.warning(self, _("Aggiornamento in corso"), _("Attendi la fine dell'aggiornamento."))
            return
        # «Salva» salva TUTTO: anche i parametri modificati nella seconda scheda
        if self.params_tab.is_dirty():
            self.params_tab.save_profile()
        if self._keyring_ok:
            new_key = self.web_key_edit.text().strip()
            if new_key or self._clear_key:
                if secrets_store.save_api_key(new_key):
                    self._file_key = ""
                else:
                    # portachiavi non disponibile (es. KWallet chiuso): la
                    # chiave resta nel file protetto invece di andare persa
                    self._file_key = new_key
                    QMessageBox.warning(
                        self, _("Portachiavi non disponibile"),
                        _("Non è stato possibile aggiornare la chiave nel portachiavi di KDE.\n")
                        + (_("La chiave è stata salvata in settings.json (permessi 600).")
                           if new_key else _("La chiave precedente potrebbe essere ancora salvata.")),
                    )
            elif self._file_key and secrets_store.load_api_key():
                self._file_key = ""   # migrazione nel portachiavi già avvenuta
        settings = self.collect_settings()
        if not config.save_settings(settings):
            QMessageBox.warning(
                self, _("Impostazioni"),
                _('Impossibile scrivere le impostazioni in {path}.\nLe modifiche '
                  "valgono solo fino alla chiusura dell'app.").format(path=config.config_dir()),
            )
        if settings["language"] != self._initial_language:
            QMessageBox.information(
                self, _("Lingua"),
                _("La nuova lingua sarà usata al prossimo avvio di OllaDesk."),
            )
        self.applied.emit(settings)
        self.accept()

    def done(self, r: int) -> None:
        # il dialogo viene distrutto dal chiamante: nessun worker deve restarvi
        # legato (un QThread distrutto in esecuzione fa abortire il processo)
        shutdown_workers([*self.ollama_update.workers(), self._app_check_worker])
        super().done(r)

    # chiusura gestita: parametri non salvati o aggiornamento in corso
    def reject(self) -> None:
        if self._update_running():
            QMessageBox.warning(self, _("Aggiornamento in corso"), _("Attendi la fine dell'aggiornamento."))
            return
        if not self.params_tab.is_dirty() or self.params_tab.maybe_discard():
            super().reject()

    def closeEvent(self, ev) -> None:  # noqa: N802 (API Qt)
        if self._update_running():
            ev.ignore()
            return
        if not self.params_tab.is_dirty() or self.params_tab.maybe_discard():
            super().closeEvent(ev)
        else:
            ev.ignore()

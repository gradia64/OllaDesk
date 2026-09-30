"""Finestra impostazioni con due schede: «Interfaccia» e «Parametri modelli».

La scheda interfaccia include anche la sezione «Aggiornamenti Ollama»:
controllo dell'ultima release su GitHub e aggiornamento tramite lo script
ufficiale, scaricato in memoria e passato a sh via stdin (pkexec chiede la
password di amministratore).
"""
from __future__ import annotations

from PySide6.QtCore import QProcess, Qt, Signal
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

from .. import config, secrets_store, updater
from ..ollama_client import ApiWorker, shutdown_workers
from .model_params import ModelParamsTab
from .steppers import IntStepper


class SettingsDialog(QDialog):
    """Emette `applied(settings)` quando l'utente conferma con «Salva»."""

    applied = Signal(dict)

    def __init__(
        self,
        settings: dict,
        models_provider,
        theme_name: str,
        version: str = "?",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Impostazioni — OllaDesk")
        # la scroll area della scheda interfaccia rende sicure anche le
        # dimensioni piccole: le schermate scorrono invece di sovrapporsi
        self.setMinimumSize(720, 480)
        self._theme_name = theme_name
        self._installed_version = version
        self._latest_version: str | None = None
        self._check_worker: ApiWorker | None = None
        self._update_proc: QProcess | None = None
        self._download_worker = None
        self._release_worker = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)

        self.tabs = QTabWidget(self)
        lay.addWidget(self.tabs, 1)

        self.tabs.addTab(self._build_gui_tab(settings), "🖥  Interfaccia")
        self.params_tab = ModelParamsTab(models_provider, self)
        self.tabs.addTab(self.params_tab, "⚙  Parametri modelli")

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("Annulla", self)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        save = QPushButton("💾  Salva", self)
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
        self.host_edit.setToolTip("URL del server Ollama (API locale)")
        form.addRow("Server Ollama:", self.host_edit)

        self.theme_combo = QComboBox(w)
        self.theme_combo.addItem("🖥  Sistema (come KDE Plasma)", "system")
        self.theme_combo.addItem("🌙  Scuro", "dark")
        self.theme_combo.addItem("☀  Chiaro", "light")
        idx = self.theme_combo.findData(s.get("theme", "dark"))
        self.theme_combo.setCurrentIndex(max(0, idx))
        form.addRow("Tema:", self.theme_combo)

        self.font_spin = IntStepper(w)
        self.font_spin.setRange(8, 16)
        self.font_spin.setValue(int(s.get("font_size", 10)))
        self.font_spin.setSuffix(" pt")
        form.addRow("Dimensione carattere:", self.font_spin)

        self.stream_chk = QCheckBox("Mostra la risposta token per token", w)
        self.stream_chk.setChecked(bool(s.get("stream", True)))
        form.addRow("Streaming:", self.stream_chk)

        self.enter_chk = QCheckBox("Invia il messaggio con il tasto Invio", w)
        self.enter_chk.setChecked(bool(s.get("send_on_enter", True)))
        form.addRow("Invio rapido:", self.enter_chk)

        self.ts_chk = QCheckBox("Mostra l'orario accanto ai messaggi", w)
        self.ts_chk.setChecked(bool(s.get("show_timestamps", False)))
        form.addRow("Orario:", self.ts_chk)

        self.hist_spin = IntStepper(w)
        self.hist_spin.setRange(0, 200)
        self.hist_spin.setValue(int(s.get("history_limit", 20)))
        self.hist_spin.setSuffix(" messaggi")
        self.hist_spin.setToolTip("Quanti messaggi precedenti vengono inviati al modello come contesto")
        form.addRow("Contesto inviato al modello:", self.hist_spin)

        self.web_spin = IntStepper(w)
        self.web_spin.setRange(3, 10)
        self.web_spin.setValue(int(s.get("web_results", 5)))
        self.web_spin.setSuffix(" risultati")
        self.web_spin.setToolTip("Numero di risultati web allegati quando la ricerca web è attiva (🌐)")
        form.addRow("Risultati ricerca web:", self.web_spin)

        self.web_provider_combo = QComboBox(w)
        self.web_provider_combo.addItem("DuckDuckGo (senza chiave API)", "duckduckgo")
        self.web_provider_combo.addItem("Ollama Cloud — ollama.com (chiave API)", "ollama")
        self.web_provider_combo.addItem("SearXNG (istanza personale)", "searxng")
        idx = self.web_provider_combo.findData(s.get("web_provider", "duckduckgo"))
        self.web_provider_combo.setCurrentIndex(max(0, idx))
        self.web_provider_combo.currentIndexChanged.connect(
            lambda _i: self._update_provider_fields()
        )
        form.addRow("Provider ricerca web:", self.web_provider_combo)

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
                "lascia vuoto per mantenere quella nel portachiavi di KDE"
                + (" (una chiave è già salvata)" if stored else " (nessuna chiave salvata)")
            )
            self.web_key_edit.setToolTip(
                "Chiave creabile su ollama.com → Impostazioni → API Keys.\n"
                "Viene salvata nel portachiavi di sistema (KWallet), mai nei file di configurazione."
            )
        else:
            self.web_key_edit.setText(self._file_key)
            self.web_key_edit.setPlaceholderText("chiave API dell'account ollama.com")
            self.web_key_edit.setToolTip(
                "Chiave creabile su ollama.com → Impostazioni → API Keys.\n"
                "Portachiavi non disponibile: la chiave resta in settings.json "
                "(file protetto con permessi 600). Installa `keyring` per salvarla in KWallet."
            )
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.addWidget(self.web_key_edit, 1)
        self.clear_key_btn = QPushButton("Rimuovi", w)
        self.clear_key_btn.setToolTip("Elimina la chiave API salvata")
        self.clear_key_btn.clicked.connect(self._clear_api_key)
        key_row.addWidget(self.clear_key_btn)
        self._key_row = QWidget(w)
        self._key_row.setLayout(key_row)
        self._clear_key = False
        form.addRow("Chiave API:", self._key_row)

        self.searxng_edit = QLineEdit(s.get("web_searxng_url", ""), w)
        self.searxng_edit.setPlaceholderText("http://localhost:8888")
        self.searxng_edit.setToolTip(
            "URL base della tua istanza SearXNG (deve accettare format=json)\n"
            "es. `searxng-run` in Docker: docker run -p 8888:8080 searxng/searxng"
        )
        form.addRow("SearXNG — URL:", self.searxng_edit)

        self.sys_prompt = QPlainTextEdit(s.get("system_prompt", ""), w)
        self.sys_prompt.setPlaceholderText("(vuoto = nessun prompt di sistema)")
        self.sys_prompt.setFixedHeight(64)
        self.sys_prompt.setToolTip(
            "Prompt di sistema inviato a Ollama con ogni richiesta (vale per tutte le conversazioni)"
        )
        form.addRow("Prompt di sistema:", self.sys_prompt)

        lay.addLayout(form)

        info = QLabel(
            f"Impostazioni, parametri e conversazioni sono salvati in:\n{config.config_dir()}",
            w,
        )
        info.setObjectName("metaLabel")
        info.setWordWrap(True)
        lay.addWidget(info)
        lay.addStretch(1)

        self._update_provider_fields()
        lay.addWidget(self._build_update_group(w))
        return page

    def _update_provider_fields(self) -> None:
        """Mostra i campi del provider selezionato (chiave per Ollama, URL per SearXNG)."""
        provider = self.web_provider_combo.currentData()
        form = getattr(self, "_gui_form", None)
        if form is None:
            return
        form.setRowVisible(self._key_row, provider == "ollama")
        form.setRowVisible(self.searxng_edit, provider == "searxng")

    def _clear_api_key(self) -> None:
        """Segna la chiave per la rimozione (effettiva con «Salva»)."""
        self._clear_key = True
        self.web_key_edit.clear()
        self.web_key_edit.setPlaceholderText("la chiave verrà rimossa al salvataggio")

    # ------------------------------------------------ aggiornamenti Ollama

    def _build_update_group(self, parent: QWidget) -> QGroupBox:
        box = QGroupBox("Aggiornamenti Ollama", parent)
        lay = QVBoxLayout(box)
        lay.setSpacing(8)

        row1 = QHBoxLayout()
        self.ver_installed_label = QLabel(
            f"Versione installata: <b>{self._installed_version}</b>", box
        )
        row1.addWidget(self.ver_installed_label)
        row1.addStretch(1)
        self.check_btn = QPushButton("🔍  Verifica aggiornamenti", box)
        self.check_btn.clicked.connect(self._check_updates)
        row1.addWidget(self.check_btn)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        self.ver_latest_label = QLabel("Ultima versione disponibile: —", box)
        self.ver_latest_label.setObjectName("metaLabel")
        row2.addWidget(self.ver_latest_label, 1)
        self.update_btn = QPushButton("⬆  Aggiorna ora", box)
        self.update_btn.setObjectName("primaryBtn")
        self.update_btn.setEnabled(False)
        self.update_btn.setToolTip(
            "Esegue lo script ufficiale di installazione (richiede la password "
            "di amministratore tramite pkexec)"
        )
        self.update_btn.clicked.connect(self._run_update)
        row2.addWidget(self.update_btn)
        lay.addLayout(row2)

        self.update_status = QLabel("", box)
        self.update_status.setObjectName("metaLabel")
        self.update_status.setWordWrap(True)
        lay.addWidget(self.update_status)

        self.update_log = QPlainTextEdit(box)
        self.update_log.setReadOnly(True)
        self.update_log.setMaximumHeight(140)
        self.update_log.setPlaceholderText("…output dell'aggiornamento…")
        self.update_log.hide()
        lay.addWidget(self.update_log)

        if self._installed_version in ("", "?"):
            self.update_status.setText(
                "Versione attuale non nota: avvia Ollama o premi «Verifica aggiornamenti»."
            )
        return box

    def _clear_ref(self, attr: str, w):
        """Slot per `finished`: azzera `attr` solo se punta ancora a `w`."""
        def clear() -> None:
            if getattr(self, attr, None) is w:
                setattr(self, attr, None)
        return clear

    def _set_check_busy(self, busy: bool) -> None:
        self.check_btn.setEnabled(not busy)
        self.check_btn.setText("⏳ Verifica…" if busy else "🔍  Verifica aggiornamenti")

    def _check_updates(self) -> None:
        if self._check_worker is not None or self._update_proc is not None:
            return
        self._set_check_busy(True)
        self.update_status.setText("Ricontatto il server Ollama e GitHub…")
        self._check_pending = 2

        # ricontrolla anche la versione installata
        self._check_worker = ApiWorker(self.host_edit.text().strip(), "/api/version", self)
        self._check_worker.ready.connect(self._on_installed_version)
        self._check_worker.failed.connect(lambda _e: self._check_step())
        self._check_worker.finished.connect(self._check_worker.deleteLater)
        self._check_worker.finished.connect(self._clear_ref("_check_worker", self._check_worker))
        self._check_worker.start()

        self._release_worker = updater.UpdateCheckWorker(self)
        self._release_worker.ready.connect(self._on_latest_version)
        self._release_worker.failed.connect(self._on_latest_failed)
        self._release_worker.finished.connect(self._release_worker.deleteLater)
        self._release_worker.finished.connect(self._clear_ref("_release_worker", self._release_worker))
        self._release_worker.start()

    def _on_installed_version(self, data: object) -> None:
        v = (data or {}).get("version") if isinstance(data, dict) else None
        if v:
            self._installed_version = v
            self.ver_installed_label.setText(f"Versione installata: <b>{v}</b>")
        self._check_step()

    def _on_latest_version(self, tag: str) -> None:
        self._latest_version = tag
        self.ver_latest_label.setText(f"Ultima versione disponibile: <b>{tag}</b>")
        self._check_step()

    def _on_latest_failed(self, _err: str) -> None:
        self._latest_version = None
        self.ver_latest_label.setText("Ultima versione disponibile: —")
        self._check_step()

    def _check_step(self) -> None:
        self._check_pending = getattr(self, "_check_pending", 1) - 1
        if self._check_pending > 0:
            return
        self._set_check_busy(False)
        if self._latest_version and self._installed_version not in ("", "?"):
            if not updater.is_newer(self._latest_version, self._installed_version):
                self.update_status.setText("✓ Ollama è aggiornato.")
                self.update_btn.setEnabled(False)
            elif not updater.is_local_host(self.host_edit.text().strip()):
                # il pulsante aggiorna la macchina LOCALE: con un server remoto
                # sarebbe un aggiornamento sull'host sbagliato
                self.update_status.setText(
                    f"✦ Aggiornamento disponibile ({self._installed_version} → {self._latest_version}), "
                    "ma il server configurato non è su questa macchina: aggiorna Ollama sull'host remoto."
                )
                self.update_btn.setEnabled(False)
            else:
                self.update_status.setText(
                    f"✦ Aggiornamento disponibile: {self._installed_version} → {self._latest_version}. "
                    "Premi «Aggiorna ora» (serve la password di amministratore)."
                )
                self.update_btn.setEnabled(True)
        else:
            self.update_status.setText(
                "⚠ Verifica non riuscita: controlla la connessione a internet o il server Ollama."
            )

    def _run_update(self) -> None:
        if not updater.pkexec_available():
            self.update_status.setText(
                "⚠ `pkexec` non disponibile. Aggiorna manualmente da terminale:\n"
                f"    {updater.INSTALL_CMD}"
            )
            return
        self.update_btn.setEnabled(False)
        self.check_btn.setEnabled(False)
        self.update_status.setText("Scarico lo script ufficiale di installazione…")
        self._download_worker = updater.UpdateDownloadWorker(self)
        self._download_worker.ready.connect(self._on_update_script_ready)
        self._download_worker.failed.connect(self._on_update_script_failed)
        self._download_worker.finished.connect(self._download_worker.deleteLater)
        # senza azzeramento, _update_running() chiamerebbe isRunning() su un
        # oggetto già distrutto da deleteLater (RuntimeError alla chiusura)
        self._download_worker.finished.connect(
            self._clear_ref("_download_worker", self._download_worker)
        )
        self._download_worker.start()

    def _on_update_script_ready(self, script: bytes) -> None:
        # lo script resta in memoria e passa a `sh -s` via stdin
        size = len(script)
        cmd = updater.update_command_stdin()
        if cmd is None:
            self.update_status.setText("⚠ `pkexec` non disponibile sul sistema.")
            self.check_btn.setEnabled(True)
            return
        self.update_log.clear()
        self.update_log.show()
        self.update_status.setText(
            f"Script ufficiale scaricato ({size} byte): passa a sh via stdin, esecuzione "
            "con privilegi di amministratore — inserisci la password quando richiesto…"
        )
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_update_output)
        proc.finished.connect(self._on_update_finished)
        proc.errorOccurred.connect(self._on_update_error)
        self._update_proc = proc
        proc.start(cmd[0], cmd[1:])
        # scritto nel buffer di QProcess: viene consegnato all'avvio del
        # processo, senza bloccare la UI con waitForStarted
        proc.write(script)
        proc.closeWriteChannel()

    def _on_update_error(self, err) -> None:
        if err != QProcess.ProcessError.FailedToStart:
            return   # gli altri casi arrivano anche da finished
        self._update_proc = None
        self.update_status.setText(
            "⚠ Impossibile avviare `pkexec`. Aggiorna manualmente da terminale:\n"
            f"    {updater.INSTALL_CMD}"
        )
        self.check_btn.setEnabled(True)

    def _on_update_script_failed(self, err: str) -> None:
        self.update_status.setText(
            f"⚠ Impossibile scaricare lo script ({err}). Aggiorna manualmente da terminale:\n"
            f"    {updater.INSTALL_CMD}"
        )
        self.check_btn.setEnabled(True)

    def _on_update_output(self) -> None:
        proc = self._update_proc
        if proc is None:
            return
        text = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
        self.update_log.appendPlainText(text.rstrip())

    def _on_update_finished(self, code: int, _status) -> None:
        self._update_proc = None
        if code == 0:
            self.update_status.setText(
                "✓ Aggiornamento completato. Riavvia il server Ollama per usare la nuova versione."
            )
            self._check_updates()
        else:
            self.update_status.setText(
                f"⚠ Aggiornamento non riuscito (codice {code}). Vedi l'output qui sopra."
            )
            self.check_btn.setEnabled(True)

    # ------------------------------------------------------------------ azioni

    def collect_settings(self) -> dict:
        return {
            "host": self._normalized_host(),
            "theme": self.theme_combo.currentData() or "dark",
            "font_size": self.font_spin.value(),
            "stream": self.stream_chk.isChecked(),
            "send_on_enter": self.enter_chk.isChecked(),
            "show_timestamps": self.ts_chk.isChecked(),
            "history_limit": self.hist_spin.value(),
            "web_results": self.web_spin.value(),
            "web_provider": self.web_provider_combo.currentData() or "duckduckgo",
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

    def _update_running(self) -> bool:
        if self._update_proc is not None and self._update_proc.state() != QProcess.ProcessState.NotRunning:
            return True
        return self._download_worker is not None and self._download_worker.isRunning()

    def _on_save(self) -> None:
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
                        self, "Portachiavi non disponibile",
                        "Non è stato possibile aggiornare la chiave nel portachiavi di KDE.\n"
                        + ("La chiave è stata salvata in settings.json (permessi 600)."
                           if new_key else "La chiave precedente potrebbe essere ancora salvata."),
                    )
            elif self._file_key and secrets_store.load_api_key():
                self._file_key = ""   # migrazione nel portachiavi già avvenuta
        settings = self.collect_settings()
        if not config.save_settings(settings):
            QMessageBox.warning(
                self, "Impostazioni",
                f"Impossibile scrivere le impostazioni in {config.config_dir()}.\n"
                "Le modifiche valgono solo fino alla chiusura dell'app.",
            )
        self.applied.emit(settings)
        self.accept()

    def done(self, r: int) -> None:
        # il dialogo viene distrutto dal chiamante: nessun worker deve restarvi
        # legato (un QThread distrutto in esecuzione fa abortire il processo)
        shutdown_workers([self._check_worker, self._release_worker, self._download_worker])
        super().done(r)

    # chiusura gestita: parametri non salvati o aggiornamento in corso
    def reject(self) -> None:
        if self._update_running():
            QMessageBox.warning(self, "Aggiornamento in corso", "Attendi la fine dell'aggiornamento.")
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

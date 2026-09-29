"""Finestra impostazioni con due schede: «Interfaccia» e «Parametri modelli».

La scheda interfaccia include anche la sezione «Aggiornamenti Ollama»:
controllo dell'ultima release su GitHub e aggiornamento tramite lo script
ufficiale (eseguito con pkexec, quindi con password di amministratore).
"""
from __future__ import annotations

from PySide6.QtCore import QProcess, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config, updater
from ..ollama_client import ApiWorker
from .model_params import ModelParamsTab


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
        self.setMinimumSize(780, 600)
        self._theme_name = theme_name
        self._installed_version = version
        self._latest_version: str | None = None
        self._check_worker: ApiWorker | None = None
        self._update_proc: QProcess | None = None

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
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(12)

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

        self.font_spin = QSpinBox(w)
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

        self.hist_spin = QSpinBox(w)
        self.hist_spin.setRange(0, 200)
        self.hist_spin.setValue(int(s.get("history_limit", 20)))
        self.hist_spin.setSuffix(" messaggi")
        self.hist_spin.setToolTip("Quanti messaggi precedenti vengono inviati al modello come contesto")
        form.addRow("Contesto inviato al modello:", self.hist_spin)

        self.web_spin = QSpinBox(w)
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

        self.web_key_edit = QLineEdit(s.get("web_api_key", ""), w)
        self.web_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.web_key_edit.setPlaceholderText("chiave API dell'account ollama.com")
        self.web_key_edit.setToolTip(
            "Chiave creabile su ollama.com → Impostazioni → API Keys.\n"
            "Serve al provider «Ollama Cloud»; gli altri provider non la richiedono."
        )
        form.addRow("Chiave API:", self.web_key_edit)

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
        self.sys_prompt.setToolTip("Prompt di sistema applicato alle nuove conversazioni")
        form.addRow("Prompt di sistema:", self.sys_prompt)

        outer.addLayout(form)

        info = QLabel(
            f"Impostazioni, parametri e conversazioni sono salvati in:\n{config.config_dir()}",
            w,
        )
        info.setObjectName("metaLabel")
        info.setWordWrap(True)
        outer.addWidget(info)
        outer.addStretch(1)

        self._update_provider_fields()
        outer.addWidget(self._build_update_group(w))
        return w

    def _update_provider_fields(self) -> None:
        """Mostra i campi del provider selezionato (chiave per Ollama, URL per SearXNG)."""
        provider = self.web_provider_combo.currentData()
        form = getattr(self, "_gui_form", None)
        if form is None:
            return
        form.setRowVisible(self.web_key_edit, provider == "ollama")
        form.setRowVisible(self.searxng_edit, provider == "searxng")

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
        self._check_worker.finished.connect(lambda: setattr(self, "_check_worker", None))
        self._check_worker.start()

        self._release_worker = updater.UpdateCheckWorker(self)
        self._release_worker.ready.connect(self._on_latest_version)
        self._release_worker.failed.connect(self._on_latest_failed)
        self._release_worker.finished.connect(self._release_worker.deleteLater)
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
            if updater.is_newer(self._latest_version, self._installed_version):
                self.update_status.setText(
                    f"✦ Aggiornamento disponibile: {self._installed_version} → {self._latest_version}. "
                    "Premi «Aggiorna ora» (serve la password di amministratore)."
                )
                self.update_btn.setEnabled(True)
            else:
                self.update_status.setText("✓ Ollama è aggiornato.")
                self.update_btn.setEnabled(False)
        else:
            self.update_status.setText(
                "⚠ Verifica non riuscita: controlla la connessione a internet o il server Ollama."
            )

    def _run_update(self) -> None:
        cmd = updater.update_command()
        if not cmd:
            self.update_status.setText(
                "⚠ `pkexec` non disponibile. Aggiorna manualmente da terminale:\n"
                f"    {updater.INSTALL_CMD}"
            )
            return
        self.update_btn.setEnabled(False)
        self.check_btn.setEnabled(False)
        self.update_log.clear()
        self.update_log.show()
        self.update_status.setText("Aggiornamento in corso — inserisci la password quando richiesto…")
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_update_output)
        proc.finished.connect(self._on_update_finished)
        self._update_proc = proc
        proc.start(cmd[0], cmd[1:])

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
            "host": self.host_edit.text().strip() or config.DEFAULT_SETTINGS["host"],
            "theme": self.theme_combo.currentData() or "dark",
            "font_size": self.font_spin.value(),
            "stream": self.stream_chk.isChecked(),
            "send_on_enter": self.enter_chk.isChecked(),
            "show_timestamps": self.ts_chk.isChecked(),
            "history_limit": self.hist_spin.value(),
            "web_results": self.web_spin.value(),
            "web_provider": self.web_provider_combo.currentData() or "duckduckgo",
            "web_api_key": self.web_key_edit.text().strip(),
            "web_searxng_url": self.searxng_edit.text().strip() or "http://localhost:8888",
            "system_prompt": self.sys_prompt.toPlainText().strip(),
        }

    def _on_save(self) -> None:
        settings = self.collect_settings()
        config.save_settings(settings)
        self.applied.emit(settings)
        self.accept()

    # chiusura gestita: parametri non salvati o aggiornamento in corso
    def reject(self) -> None:
        if self._update_proc is not None and self._update_proc.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, "Aggiornamento in corso", "Attendi la fine dell'aggiornamento.")
            return
        if not self.params_tab.is_dirty() or self.params_tab.maybe_discard():
            super().reject()

    def closeEvent(self, ev) -> None:  # noqa: N802 (API Qt)
        if self._update_proc is not None and self._update_proc.state() != QProcess.ProcessState.NotRunning:
            ev.ignore()
            return
        if not self.params_tab.is_dirty() or self.params_tab.maybe_discard():
            super().closeEvent(ev)
        else:
            ev.ignore()


# QMessageBox serve a reject(); import in fondo per evitare cicli visivi
from PySide6.QtWidgets import QMessageBox  # noqa: E402

"""Aggiornamento di Ollama: verifica della versione e script ufficiale.

Il riquadro sta nelle impostazioni e, dentro `OllamaUpdateDialog`, si apre
anche dall'indicatore di stato nella barra superiore. Lo script ufficiale di
installazione viene scaricato in memoria e passato a `sh -s` via stdin con
pkexec (chiede la password di amministratore).
"""
from __future__ import annotations

import html

from PySide6.QtCore import QProcess, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .. import updater
from ..i18n import _
from ..ollama_client import ApiWorker, shutdown_workers


class OllamaUpdateBox(QGroupBox):
    """Versione installata e più recente, pulsante «Aggiorna ora», output.

    `host_getter` restituisce l'indirizzo del server: nelle impostazioni è
    quello ancora da salvare nel campo di testo.
    """

    latestFound = Signal(str)   # ultima versione rilasciata (senza «v»)

    def __init__(self, host_getter, installed_version: str = "?", parent=None):
        super().__init__(_("Aggiornamenti Ollama"), parent)
        self._host = host_getter
        self._installed_version = installed_version or "?"
        self._latest_version: str | None = None
        self._check_worker: ApiWorker | None = None
        self._update_proc: QProcess | None = None
        self._download_worker = None
        self._release_worker = None
        box = self
        lay = QVBoxLayout(box)
        lay.setSpacing(8)

        row1 = QHBoxLayout()
        self.ver_installed_label = QLabel(
            _('Versione installata: <b>{installed}</b>').format(installed=html.escape(self._installed_version)), box
        )
        row1.addWidget(self.ver_installed_label)
        row1.addStretch(1)
        self.check_btn = QPushButton(_("🔍  Verifica aggiornamenti"), box)
        self.check_btn.clicked.connect(self._check_updates)
        row1.addWidget(self.check_btn)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        self.ver_latest_label = QLabel(_("Ultima versione disponibile: —"), box)
        self.ver_latest_label.setObjectName("metaLabel")
        row2.addWidget(self.ver_latest_label, 1)
        self.update_btn = QPushButton(_("⬆  Aggiorna ora"), box)
        self.update_btn.setObjectName("primaryBtn")
        self.update_btn.setEnabled(False)
        self.update_btn.setToolTip(
            _("Esegue lo script ufficiale di installazione (richiede la password "
            "di amministratore tramite pkexec)")
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
        self.update_log.setPlaceholderText(_("…output dell'aggiornamento…"))
        self.update_log.hide()
        lay.addWidget(self.update_log)

        if self._installed_version in ("", "?"):
            self.update_status.setText(
                _("Versione attuale non nota: avvia Ollama o premi «Verifica aggiornamenti».")
            )

    def _clear_ref(self, attr: str, w):
        """Slot per `finished`: azzera `attr` solo se punta ancora a `w`."""
        def clear() -> None:
            if getattr(self, attr, None) is w:
                setattr(self, attr, None)
        return clear

    def _set_check_busy(self, busy: bool) -> None:
        self.check_btn.setEnabled(not busy)
        self.check_btn.setText(_("⏳ Verifica…") if busy else _("🔍  Verifica aggiornamenti"))

    def check_updates(self) -> None:
        """Ricontrolla la versione installata e l'ultima rilasciata."""
        self._check_updates()

    def _check_updates(self) -> None:
        if self._check_worker is not None or self._update_proc is not None:
            return
        self._set_check_busy(True)
        self.update_status.setText(_("Ricontatto il server Ollama e GitHub…"))
        self._check_pending = 2

        # ricontrolla anche la versione installata
        self._check_worker = ApiWorker(self._host(), "/api/version", self)
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
            self.ver_installed_label.setText(_('Versione installata: <b>{version}</b>').format(version=html.escape(v)))
        self._check_step()

    def _on_latest_version(self, tag: str) -> None:
        self._latest_version = tag
        self.latestFound.emit(tag)
        self.ver_latest_label.setText(_('Ultima versione disponibile: <b>{tag}</b>').format(tag=html.escape(tag)))
        self._check_step()

    def _on_latest_failed(self, _err: str) -> None:
        self._latest_version = None
        self.ver_latest_label.setText(_("Ultima versione disponibile: —"))
        self._check_step()

    def _check_step(self) -> None:
        self._check_pending = getattr(self, "_check_pending", 1) - 1
        if self._check_pending > 0:
            return
        self._set_check_busy(False)
        if self._latest_version and self._installed_version not in ("", "?"):
            if not updater.is_newer(self._latest_version, self._installed_version):
                self.update_status.setText(_("✓ Ollama è aggiornato."))
                self.update_btn.setEnabled(False)
            elif not updater.is_local_host(self._host()):
                # il pulsante aggiorna la macchina LOCALE: con un server remoto
                # sarebbe un aggiornamento sull'host sbagliato
                self.update_status.setText(
                    _('✦ Aggiornamento disponibile ({installed} → {latest}), '
                      'ma il server configurato non è su questa macchina: aggiorna Ollama '
                      "sull'host remoto.").format(installed=self._installed_version, latest=self._latest_version)
                )
                self.update_btn.setEnabled(False)
            else:
                self.update_status.setText(
                    _('✦ Aggiornamento disponibile: {installed} → {latest}. '
                      'Premi «Aggiorna ora» (serve la password di amministratore).').format(installed=self._installed_version, latest=self._latest_version)
                )
                self.update_btn.setEnabled(True)
        else:
            self.update_status.setText(
                _("⚠ Verifica non riuscita: controlla la connessione a internet o il server Ollama.")
            )

    def _run_update(self) -> None:
        if not updater.pkexec_available():
            self.update_status.setText(
                _('⚠ `pkexec` non disponibile. Aggiorna manualmente da terminale:\n    '
                  '{command}').format(command=updater.INSTALL_CMD)
            )
            return
        self.update_btn.setEnabled(False)
        self.check_btn.setEnabled(False)
        self.update_status.setText(_("Scarico lo script ufficiale di installazione…"))
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
            self.update_status.setText(_("⚠ `pkexec` non disponibile sul sistema."))
            self.check_btn.setEnabled(True)
            return
        self.update_log.clear()
        self.update_log.show()
        self.update_status.setText(
            _('Script ufficiale scaricato ({size} byte): passa a sh via stdin, '
              'esecuzione con privilegi di amministratore — inserisci la password '
              'quando richiesto…').format(size=size)
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
            _('⚠ Impossibile avviare `pkexec`. Aggiorna manualmente da terminale:\n  '
              '  {command}').format(command=updater.INSTALL_CMD)
        )
        self.check_btn.setEnabled(True)

    def _on_update_script_failed(self, err: str) -> None:
        self.update_status.setText(
            _('⚠ Impossibile scaricare lo script ({err}). Aggiorna manualmente da '
              'terminale:\n    {command}').format(err=err, command=updater.INSTALL_CMD)
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
                _("✓ Aggiornamento completato. Riavvia il server Ollama per usare la nuova versione.")
            )
            self._check_updates()
        else:
            self.update_status.setText(
                _("⚠ Aggiornamento non riuscito (codice {code}). Vedi l'output qui sopra.").format(code=code)
            )
            self.check_btn.setEnabled(True)

    def is_running(self) -> bool:
        if self._update_proc is not None and self._update_proc.state() != QProcess.ProcessState.NotRunning:
            return True
        return self._download_worker is not None and self._download_worker.isRunning()

    def workers(self) -> list:
        """Worker ancora vivi, da fermare prima di distruggere il riquadro."""
        return [self._check_worker, self._release_worker, self._download_worker]


class OllamaUpdateDialog(QDialog):
    """Il riquadro da solo, aperto dall'indicatore di stato di Ollama: la
    verifica parte subito."""

    def __init__(self, host: str, installed_version: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Aggiornamento di Ollama"))
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        self.box = OllamaUpdateBox(lambda: host, installed_version, self)
        lay.addWidget(self.box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.box.check_updates()

    def done(self, r: int) -> None:
        # come nelle impostazioni: nessun QThread deve restare legato al dialogo
        shutdown_workers(self.box.workers())
        super().done(r)

    def reject(self) -> None:
        if self.box.is_running():
            QMessageBox.warning(self, _("Aggiornamento in corso"), _("Attendi la fine dell'aggiornamento."))
            return
        super().reject()

    def closeEvent(self, ev) -> None:  # noqa: N802 (API Qt)
        if self.box.is_running():
            ev.ignore()
            return
        super().closeEvent(ev)

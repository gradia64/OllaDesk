"""Condivisione dell'API Ollama in rete (stile LM Studio).

Quando è attiva nelle impostazioni, OllaDesk avvia `ollama serve` come
sottoprocesso con ``OLLAMA_HOST=<bind>:<porta>``: smartphone e tablet sulla
stessa rete possono usare i modelli puntando un client Ollama a
``http://<ip-del-pc>:<porta>``.

L'API di Ollama NON prevede autenticazione: chi raggiunge la porta può usare
i modelli. Nelle impostazioni l'utente viene avvisato; per l'accesso da
fuori casa la scelta giusta resta una VPN (es. Tailscale) o un tunnel SSH.

Se sulla porta locale risponde già un Ollama avviato altrove (es. servizio
systemd), non viene avviato nessun processo: dopo la sonda su 127.0.0.1 una
seconda sonda verifica la raggiungibilità REALE dalla LAN (un servizio con
bind 127.0.0.1 risponde in locale ma non dal telefono) e lo stato «external»
riporta di conseguenza la situazione, senza avviare nulla. In quel caso la
via consigliata resta riavviare il servizio con OLLAMA_HOST=0.0.0.0; una
seconda istanza avviata da OllaDesk usa i modelli dell'UTENTE
(~/.ollama/models, non quelli del servizio) e carica in VRAM per conto suo.

Il processo avviato da OllaDesk muore con l'app: parte tramite
``setpriv --pdeathsig TERM`` (il kernel gli consegna SIGTERM alla morte del
padre, anche davanti a un kill -9) e i segnali SIGTERM/SIGINT dell'app sono
gestiti con chiusura pulita. QProcess.setChildProcessModifier non esiste in
PySide6 (il binding non viene generato: in C++ prende una std::function),
quindi setpriv è la via portabile su Linux.
"""
from __future__ import annotations

import shutil
import urllib.request

from PySide6.QtCore import (
    QObject,
    QProcess,
    QProcessEnvironment,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtNetwork import QNetworkInterface

# interfacce virtuali inutili per un telefono: bridge/container (Docker,
# libvirt). WireGuard/Tailscale restano: servono per l'accesso da fuori casa.
_INTERFACE_NOISE = ("docker", "br-", "veth", "virbr", "tap", "lo")


def _is_shareable_ip(text: str) -> bool:
    """True per un IPv4 di rete utile ai client mobili (no loopback/link-local/IPv6)."""
    if ":" in text or "." not in text:
        return False   # IPv6 o stringa non valida
    parts = text.split(".")
    if len(parts) != 4:
        return False
    try:
        values = [int(p) for p in parts]
    except ValueError:
        return False
    if any(v < 0 or v > 255 for v in values):
        return False
    first, second = values[0], values[1]
    if first == 0 or first == 127:          # 0.0.0.0, loopback
        return False
    if first == 169 and second == 254:      # link-local
        return False
    return True


def lan_urls(port: int) -> list[str]:
    """Indirizzi http://<ip-lan>:<port> raggiungibili da altri dispositivi.

    Esclude loopback, link-local (169.254.x.x), IPv6 e le interfacce virtuali
    (docker*, br-*, veth*, virbr*): per il cellulare conta l'IPv4 della LAN.
    """
    out: list[str] = []
    for iface in QNetworkInterface.allInterfaces():
        name = iface.name() or ""
        if name.startswith(_INTERFACE_NOISE):
            continue
        flags = iface.flags()
        if not (flags & QNetworkInterface.InterfaceFlag.IsUp) or not (
            flags & QNetworkInterface.InterfaceFlag.IsRunning
        ):
            continue
        for entry in iface.addressEntries():
            text = entry.ip().toString()
            if _is_shareable_ip(text):
                url = f"http://{text}:{port}"
                if url not in out:
                    out.append(url)
    return out


# le sonde devono bypassare i proxy di sistema: un http_proxy farebbe passare
# dal proxy anche la verifica su 127.0.0.1 o sull'IP LAN, falsandola
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class _ProbeWorker(QThread):
    """Tenta una volta le connessioni a http://…/api/version (True se una risponde)."""

    reachable = Signal(bool)

    def __init__(self, urls: list[str], parent=None):
        super().__init__(parent)
        self._urls = list(urls)

    def run(self) -> None:  # noqa: N802 (API Qt)
        ok = False
        for url in self._urls:
            try:
                with _NO_PROXY_OPENER.open(url, timeout=1.5) as resp:
                    if getattr(resp, "status", 200) == 200:
                        ok = True
                        break
            except Exception:
                continue
        self.reachable.emit(ok)


class SharedOllamaServer(QObject):
    """Avvia/arresta `ollama serve` condiviso e segnala i cambi di stato.

    Stati: "off" | "starting" | "running" (processo avviato da OllaDesk) |
    "external" (porta già servita da un'altra istanza: il dettaglio dice se
    è raggiungibile davvero dalla LAN o solo in locale) | "error".
    """

    state_changed = Signal(str, str)   # stato, dettaglio (tooltip o messaggio)

    POLL_MS = 700
    MAX_POLLS = 12   # ~8,4 s di attesa prima di dichiarare l'errore

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._probe: _ProbeWorker | None = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self.POLL_MS)
        self._poll_timer.timeout.connect(self._probe_once)
        self._polls_left = 0
        self._stderr_tail = ""
        self._state = "off"
        self._cfg: tuple[str, int] | None = None
        # le sonde di un ciclo precedente (start/stop ravvicinati) non devono
        # parlare per quella nuova
        self._generation = 0

    # ------------------------------------------------------------------ API

    def state(self) -> str:
        return self._state

    def start(self, bind: str, port: int) -> None:
        cfg = (bind, port)
        if self._state in ("running", "starting", "external") and self._cfg == cfg:
            return   # già attivo (o già servito da un'altra istanza) così
        if self._state in ("running", "starting"):
            self.stop()   # la configurazione è cambiata: riavvia
        self._generation += 1
        self._cfg = cfg
        self._stderr_tail = ""
        self._state = "starting"
        self.state_changed.emit(self._state, f"Avvio di Ollama su {bind}:{port}…")
        # prima una sola prova su 127.0.0.1: se risponde già qualcosa non si
        # avvia nulla (istanza già attiva, es. systemd); segue la verifica
        # dalla LAN perché il bind di quell'istanza non lo conosciamo
        self._start_probe([f"http://127.0.0.1:{port}/api/version"], "external-local")

    def stop(self) -> None:
        self._generation += 1   # le sonde in volo non devono più rispondere
        self._poll_timer.stop()
        proc, self._proc = self._proc, None
        if proc is not None:
            proc.terminate()
            if not proc.waitForFinished(3000):
                proc.kill()
        if self._state != "off":   # anche da "error": niente indicatori stantii
            self._set_state("off", "")

    # -------------------------------------------------------------- interni

    def _start_probe(self, urls: list[str], kind: str) -> None:
        # NON blocca se una sonda è già in volo: una start()/stop() ravvicinata
        # cambia _generation e la risposta vecchia viene scartata in _on_probe;
        # bloccare qui lascerebbe la nuova configurazione mai sondata
        gen = self._generation
        probe = _ProbeWorker(urls, self)
        probe.reachable.connect(lambda ok: self._on_probe(ok, kind, gen))
        probe.finished.connect(probe.deleteLater)
        probe.finished.connect(lambda p=probe: self._clear_probe(p))
        self._probe = probe
        probe.start()

    def _clear_probe(self, probe) -> None:
        # il `finished` di una sonda sostituita non deve azzerare la nuova
        if self._probe is probe:
            self._probe = None

    def _on_probe(self, reachable: bool, kind: str, gen: int) -> None:
        if gen != self._generation or self._state == "off":
            return   # risposta di un ciclo già sostituito, o stop() nel frattempo
        port = self._cfg[1]
        if kind == "external-local":
            if reachable:
                urls = lan_urls(port)
                if urls:
                    # tutti gli IP, non solo il primo: se il primo è una VPN
                    # (WireGuard/Tailscale) il risultato non deve dipendere da lei
                    self._start_probe([f"{u}/api/version" for u in urls], "external-lan")
                    return
                self._set_state(
                    "external",
                    "Ollama risponde già su questa porta, ma nessuna interfaccia "
                    "di rete LAN è attiva: dal telefono non sarà raggiungibile.",
                )
                return
            if not self._spawn():
                return
            self._polls_left = self.MAX_POLLS
            self._poll_timer.start()
        elif kind == "external-lan":
            if reachable:
                # la sonda dall'IP LAN della stessa macchina dimostra il bind,
                # non l'apertura del firewall: la dicitura resta prudente
                self._set_state(
                    "external",
                    f"Ollama è già attivo sulla porta {port} e in ascolto su tutte "
                    f"le interfacce ({', '.join(lan_urls(port))}): nessun nuovo "
                    "processo avviato. Se dal telefono non risponde, controlla il "
                    "firewall (es. ufw/firewalld) e la stessa rete Wi-Fi.",
                )
            else:
                self._set_state(
                    "external",
                    "Ollama di sistema risponde solo su questo PC (bind 127.0.0.1): "
                    "dalla rete non è raggiungibile e qui nulla è stato avviato. "
                    "Soluzione consigliata: `sudo systemctl edit ollama` con "
                    "`Environment=OLLAMA_HOST=0.0.0.0:11434` e riavvio del servizio; "
                    "in alternativa scegli un'altra porta perché OllaDesk ne avvii "
                    "una seconda (usa i modelli dell'utente, non quelli del servizio).",
                )
        else:   # "spawn-check": il processo avviato da noi ha preso la porta
            if reachable:
                self._poll_timer.stop()
                bind = self._cfg[0]
                if bind == "127.0.0.1":
                    self._set_state(
                        "running",
                        f"Ollama condiviso attivo sulla porta {self._cfg[1]}, ma in "
                        "ascolto solo su questo PC (bind 127.0.0.1): per gli altri "
                        "dispositivi serve «Tutte le interfacce» nelle impostazioni.",
                    )
                else:
                    urls = ", ".join(lan_urls(port)) or "(nessun indirizzo di rete rilevato)"
                    self._set_state("running", urls)

    def _probe_once(self) -> None:
        if self._state != "starting":
            self._poll_timer.stop()
            return
        if self._probe is not None:
            return   # la sonda precedente è ancora in volo: la prossima lancia
        self._polls_left -= 1
        if self._polls_left <= 0:
            self._poll_timer.stop()
            detail = self._stderr_tail.strip()[-300:]
            self._set_state(
                "error",
                f"Ollama non risponde su 127.0.0.1:{self._cfg[1]}"
                + (f" — {detail}" if detail else "."),
            )
            return
        self._start_probe([f"http://127.0.0.1:{self._cfg[1]}/api/version"], "spawn-check")

    def _spawn(self) -> bool:
        binary = shutil.which("ollama")
        if binary is None:
            self._set_state(
                "error",
                "Il comando `ollama` non è nel PATH: installalo, oppure avvia "
                "manualmente `ollama serve` con OLLAMA_HOST=<bind>:<porta>.",
            )
            return False
        proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("OLLAMA_HOST", f"{self._cfg[0]}:{self._cfg[1]}")
        # OLLAMA_MODELS resta quella dell'ambiente di OllaDesk: una seconda
        # istanza vede i modelli dell'UTENTE (~/.ollama/models), NON quelli
        # del servizio di sistema (/usr/share/ollama/.ollama/models)
        proc.setProcessEnvironment(env)
        # setpriv --pdeathsig: se OllaDesk muore (anche kill -9) il kernel
        # termina il figlio, che non resta orfano in ascolto sulla rete.
        # QProcess.setChildProcessModifier non esiste in PySide6, e chiamare
        # prctl via ctypes nel figlio dopo il fork è rischioso (thread attivi)
        setpriv = shutil.which("setpriv")
        if setpriv:
            proc.setProgram(setpriv)
            proc.setArguments(["--pdeathsig", "TERM", binary, "serve"])
        else:
            proc.setProgram(binary)
            proc.setArguments(["serve"])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._read_output)
        proc.errorOccurred.connect(self._on_proc_error)
        proc.finished.connect(self._on_proc_finished)
        self._proc = proc
        proc.start()
        return True

    def _read_output(self) -> None:
        proc = self._proc
        if proc is None:
            return
        text = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._stderr_tail = (self._stderr_tail + text)[-2000:]

    def _on_proc_error(self, err) -> None:
        if err != QProcess.ProcessError.FailedToStart:
            return   # gli altri casi arrivano anche da finished
        self._poll_timer.stop()
        self._proc = None
        self._set_state(
            "error", "Impossibile avviare `ollama serve` (binario non eseguibile?)."
        )

    def _on_proc_finished(self, code: int, _status) -> None:
        if self.sender() is not self._proc:
            return   # arresto volontario (stop): già gestito
        self._proc = None
        self._poll_timer.stop()
        if self._state in ("running", "starting"):
            detail = self._stderr_tail.strip()[-300:]
            self._set_state(
                "error",
                f"`ollama serve` condiviso si è chiuso (codice {code})"
                + (f": {detail}" if detail else "."),
            )

    def _set_state(self, state: str, detail: str) -> None:
        self._state = state
        self.state_changed.emit(state, detail)

"""Ricerca web per la chat: provider DuckDuckGo (senza chiavi API) e
provider «Ollama Cloud» (endpoint ufficiale ollama.com, richiede la chiave
API dell'account) + worker Qt per l'esecuzione in background.

I risultati (titolo, URL, snippet) vengono iniettati nel contesto del
messaggio: i modelli non chiamano il tool da soli, ricevono il testo.
"""
from __future__ import annotations

import html as html_mod
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from PySide6.QtCore import QThread, Signal

from .ollama_client import abort_response

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_TAG_RE = re.compile(r"<[^>]+>")

OLLAMA_SEARCH_URL = "https://ollama.com/api/web_search"

_BLOCK_MARKERS = ("anomaly", "challenge", "detected unusual traffic")


MAX_QUERY_CHARS = 200


class WebSearchError(Exception):
    pass


def make_query(text: str, max_chars: int = MAX_QUERY_CHARS) -> str:
    """Query di ricerca dal messaggio dell'utente.

    Il messaggio intero (magari lungo, con codice o dati personali) non va
    spedito al motore di ricerca: si usa la prima riga non vuota, troncata
    a una parola intera.
    """
    line = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    line = re.sub(r"\s+", " ", line)
    if len(line) <= max_chars:
        return line
    cut = line[:max_chars]
    return cut.rsplit(" ", 1)[0] if " " in cut else cut


def _clean(s: str) -> str:
    s = html_mod.unescape(_TAG_RE.sub("", s))
    return re.sub(r"\s+", " ", s).strip()


def _clean_url(u: str) -> str:
    u = html_mod.unescape(u or "")
    if u.startswith("//"):
        u = "https:" + u
    if "uddg=" in u:
        try:
            q = urllib.parse.urlparse(u).query
            real = urllib.parse.parse_qs(q).get("uddg", [None])[0]
            if real:
                return real
        except Exception:
            pass
    return u


def _fetch(url: str, data: dict | None = None, timeout: float = 12,
           conn_store: list | None = None) -> str:
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": _UA,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if conn_store is not None:
            conn_store.append(resp)   # permette allo stop() di interromperla
        return resp.read().decode("utf-8", "replace")


def _parse_lite(page: str) -> list[tuple[str, str, str]]:
    links = re.findall(
        r'<a[^>]+rel="nofollow"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S
    )
    snippets = re.findall(r'class="result-snippet"[^>]*>(.*?)</td>', page, re.S)
    out = []
    for i, (url, title) in enumerate(links):
        host = urllib.parse.urlparse(url).hostname or ""
        is_duckduckgo = host == "duckduckgo.com" or host.endswith(".duckduckgo.com")
        if is_duckduckgo and "uddg=" not in url:
            continue
        title = _clean(title)
        if not title:
            continue
        snip = _clean(snippets[i]) if i < len(snippets) else ""
        out.append((title, _clean_url(url), snip))
    return out


def _parse_full(page: str) -> list[tuple[str, str, str]]:
    links = re.findall(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S
    )
    snippets = re.findall(
        r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S
    )
    out = []
    for i, (url, title) in enumerate(links):
        title = _clean(title)
        if not title:
            continue
        snip = _clean(snippets[i]) if i < len(snippets) else ""
        out.append((title, _clean_url(url), snip))
    return out


def search(query: str, n_results: int = 5,
           conn_store: list | None = None) -> list[tuple[str, str, str]]:
    """Ricerca via DuckDuckGo; fino a n risultati (titolo, url, snippet)."""
    results: list[tuple[str, str, str]] = []
    blocked = False
    net_error: Exception | None = None
    try:
        page = _fetch("https://lite.duckduckgo.com/lite/", {"q": query}, conn_store=conn_store)
        results = _parse_lite(page)
        if not results and any(m in page for m in _BLOCK_MARKERS):
            blocked = True
    except Exception as e:
        net_error = e
        results = []
    if not results:
        try:
            page = _fetch("https://html.duckduckgo.com/html/", {"q": query}, conn_store=conn_store)
            results = _parse_full(page)
            if not results and any(m in page for m in _BLOCK_MARKERS):
                blocked = True
            net_error = None
        except Exception as e:
            net_error = net_error or e
            results = []
    if not results and net_error is not None and not blocked:
        reason = getattr(net_error, "reason", None) or net_error.__class__.__name__
        raise WebSearchError(f"DuckDuckGo non raggiungibile ({reason})")
    if not results and blocked:
        raise WebSearchError(
            "DuckDuckGo sta bloccando le richieste automatiche (protezione anti-bot): "
            "riprova tra qualche minuto oppure scegli un altro provider nelle impostazioni"
        )
    # dedup per URL e limite
    seen: set[str] = set()
    unique = []
    for r in results:
        if r[1] in seen:
            continue
        seen.add(r[1])
        unique.append(r)
        if len(unique) >= max(1, n_results):
            break
    return unique


def search_searxng(base_url: str, query: str, n_results: int = 5,
                   conn_store: list | None = None) -> list[tuple[str, str, str]]:
    """Ricerca via API JSON di un'istanza SearXNG personale."""
    url = base_url.rstrip("/") + "/search?" + urllib.parse.urlencode(
        {"q": query, "format": "json"}
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        resp = urllib.request.urlopen(req, timeout=12)
        if conn_store is not None:
            conn_store.append(resp)
        with resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except ValueError as e:
        raise WebSearchError(
            "l'istanza SearXNG non ha risposto in JSON: controlla l'URL e che «json» "
            "sia in search.formats"
        ) from e
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise WebSearchError(
                "l'istanza SearXNG ha disabilitato l'output JSON: aggiungi «json» a "
                "search.formats nel settings.yml dell'istanza"
            ) from e
        raise WebSearchError(f"errore HTTP {e.code} dall'istanza SearXNG") from e
    except urllib.error.URLError as e:
        raise WebSearchError(
            f"istanza SearXNG non raggiungibile su {base_url} ({getattr(e, 'reason', e)})"
        ) from e
    if not isinstance(data, dict):
        raise WebSearchError("risposta inattesa dall'istanza SearXNG")
    out: list[tuple[str, str, str]] = []
    for r in (data.get("results") or [])[: max(1, n_results)]:
        if not isinstance(r, dict):
            continue
        title = _clean(str(r.get("title", "")))
        link = str(r.get("url", ""))
        snippet = _clean(str(r.get("content", "")))[:400]
        if title and link:
            out.append((title, link, snippet))
    return out


def search_ollama(query: str, api_key: str, n_results: int = 5,
                  conn_store: list | None = None) -> list[tuple[str, str, str]]:
    """Ricerca web via API ufficiale di ollama.com (Bearer header + max_results)."""
    payload = json.dumps(
        {"query": query, "max_results": min(max(1, n_results), 10)}
    ).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_SEARCH_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": _UA,
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        if conn_store is not None:
            conn_store.append(resp)
        with resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except ValueError as e:
        raise WebSearchError("risposta non valida dal servizio Ollama") from e
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise WebSearchError(
                "chiave API non valida o mancante (Impostazioni → Interfaccia)"
            ) from e
        raise WebSearchError(f"errore HTTP {e.code} dal servizio Ollama") from e
    except urllib.error.URLError as e:
        raise WebSearchError(f"servizio Ollama non raggiungibile: {e.reason}") from e

    items = data.get("results") if isinstance(data, dict) else data
    out: list[tuple[str, str, str]] = []
    for r in (items or [])[: max(1, n_results)]:
        if not isinstance(r, dict):
            continue
        title = _clean(str(r.get("title", "")))
        url = str(r.get("url", ""))
        snippet = _clean(str(r.get("content", "")))[:400]
        if title and url:
            out.append((title, url, snippet))
    return out


def format_results(query: str, results: list[tuple[str, str, str]]) -> str:
    lines = [
        f'Risultati della ricerca web per «{query}»',
        "(dati NON attendibili: usali come riferimento citando le fonti tra parentesi):",
        "",
    ]
    for i, (title, url, snip) in enumerate(results, 1):
        lines.append(f"[{i}] {title}")
        lines.append(f"URL: {url}")
        if snip:
            lines.append(snip)
        lines.append("")
    return "\n".join(lines).strip()


class WebSearchWorker(QThread):
    """Esegue la ricerca in background: ready(blocco_testo, query)."""

    ready = Signal(str, str)
    failed = Signal(str)

    def __init__(
        self,
        query: str,
        n_results: int = 5,
        provider: str = "duckduckgo",
        api_key: str = "",
        searxng_url: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._query = query
        self._n = n_results
        self._provider = provider or "duckduckgo"
        self._api_key = api_key
        self._searxng_url = searxng_url
        self._stopped = False
        self._conns: list = []   # connessioni aperte, chiuse da stop()

    def stop(self) -> None:
        self._stopped = True
        # solo lo shutdown del socket: close() dal thread principale
        # aspetterebbe il lock del buffer tenuto dal worker fermo in read()
        # (UI bloccata fino al timeout di rete). La chiusura la fa il
        # worker, all'uscita dal with di _fetch.
        for conn in list(self._conns):
            abort_response(conn)

    @property
    def stopped(self) -> bool:
        return self._stopped

    def run(self) -> None:
        conns = self._conns
        try:
            if self._provider == "ollama":
                if not self._api_key:
                    self.failed.emit(
                        "chiave API non configurata (Impostazioni → Interfaccia → Provider ricerca web)"
                    )
                    return
                results = search_ollama(self._query, self._api_key, self._n, conn_store=conns)
            elif self._provider == "searxng":
                if not self._searxng_url.strip():
                    self.failed.emit(
                        "URL dell'istanza SearXNG non configurato (Impostazioni → Interfaccia)"
                    )
                    return
                results = search_searxng(self._searxng_url.strip(), self._query, self._n, conn_store=conns)
            else:
                results = search(self._query, self._n, conn_store=conns)
            if self._stopped:
                return
        except WebSearchError as e:
            if not self._stopped:
                self.failed.emit(str(e))
            return
        except Exception as e:
            if not self._stopped:
                self.failed.emit(f"rete non raggiungibile ({e.__class__.__name__})")
            return
        if self._stopped:
            return
        if not results:
            self.failed.emit("nessun risultato (il servizio di ricerca potrebbe non rispondere)")
            return
        self.ready.emit(format_results(self._query, results), self._query)

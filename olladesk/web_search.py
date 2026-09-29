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

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_TAG_RE = re.compile(r"<[^>]+>")

OLLAMA_SEARCH_URL = "https://ollama.com/api/web_search"

_BLOCK_MARKERS = ("anomaly", "challenge", "detected unusual traffic")


class WebSearchError(Exception):
    pass


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


def _fetch(url: str, data: dict | None = None, timeout: float = 12) -> str:
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
        return resp.read().decode("utf-8", "replace")


def _parse_lite(page: str) -> list[tuple[str, str, str]]:
    links = re.findall(
        r'<a[^>]+rel="nofollow"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S
    )
    snippets = re.findall(r'class="result-snippet"[^>]*>(.*?)</td>', page, re.S)
    out = []
    for i, (url, title) in enumerate(links):
        if "duckduckgo.com" in url and "uddg=" not in url:
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


def search(query: str, n_results: int = 5) -> list[tuple[str, str, str]]:
    """Ricerca via DuckDuckGo; fino a n risultati (titolo, url, snippet)."""
    results: list[tuple[str, str, str]] = []
    blocked = False
    try:
        page = _fetch("https://lite.duckduckgo.com/lite/", {"q": query})
        results = _parse_lite(page)
        if not results and any(m in page for m in _BLOCK_MARKERS):
            blocked = True
    except Exception:
        results = []
    if not results:
        try:
            page = _fetch("https://html.duckduckgo.com/html/", {"q": query})
            results = _parse_full(page)
            if not results and any(m in page for m in _BLOCK_MARKERS):
                blocked = True
        except Exception:
            results = []
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


def search_searxng(base_url: str, query: str, n_results: int = 5) -> list[tuple[str, str, str]]:
    """Ricerca via API JSON di un'istanza SearXNG personale."""
    url = base_url.rstrip("/") + "/search?" + urllib.parse.urlencode(
        {"q": query, "format": "json"}
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
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
    out: list[tuple[str, str, str]] = []
    for r in (data.get("results") or [])[: max(1, n_results)]:
        title = _clean(str(r.get("title", "")))
        link = str(r.get("url", ""))
        snippet = _clean(str(r.get("content", "")))[:400]
        if title and link:
            out.append((title, link, snippet))
    return out


def search_ollama(query: str, api_key: str, n_results: int = 5) -> list[tuple[str, str, str]]:
    """Ricerca web via API ufficiale di ollama.com (richiede chiave API)."""
    payload = json.dumps({"query": query, "key": api_key}).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_SEARCH_URL,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": _UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
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
        "(usa queste informazioni per rispondere, citando le fonti tra parentesi):",
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

    def stop(self) -> None:
        self._stopped = True

    @property
    def stopped(self) -> bool:
        return self._stopped

    def run(self) -> None:
        try:
            if self._provider == "ollama":
                if not self._api_key:
                    self.failed.emit(
                        "chiave API non configurata (Impostazioni → Interfaccia → Provider ricerca web)"
                    )
                    return
                results = search_ollama(self._query, self._api_key, self._n)
            elif self._provider == "searxng":
                if not self._searxng_url.strip():
                    self.failed.emit(
                        "URL dell'istanza SearXNG non configurato (Impostazioni → Interfaccia)"
                    )
                    return
                results = search_searxng(self._searxng_url.strip(), self._query, self._n)
            else:
                results = search(self._query, self._n)
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

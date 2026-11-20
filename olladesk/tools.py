"""Strumenti (tool) che il modello può chiamare durante la risposta.

Il ciclo vive in `engine.py`: con i tool attivi la richiesta a /api/chat
porta l'elenco `tools`; se il modello risponde con `message.tool_calls`, il
motore esegue le chiamate con `ToolWorker`, aggiunge i risultati come
messaggi `role: "tool"` e richiama il modello, fino alla risposta finale o
al tetto di `MAX_ROUNDS` giri.

Qui ci sono il registro dei tool, la normalizzazione delle chiamate di
Ollama e l'esecuzione in un thread. I tool veri (lettura di file, ricerca
web) arrivano con le voci successive del ciclo: il registro del motore parte
vuoto, e con un registro vuoto la richiesta resta quella di sempre.

I testi dei risultati e degli errori sono per il modello, non per l'utente:
sono in inglese, la lingua che i modelli seguono meglio, e non passano da
`_()`.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QThread, Signal

# giri di chiamate per una risposta: poi si richiama il modello senza tool,
# così deve rispondere (un modello piccolo può chiamare lo stesso tool
# all'infinito)
MAX_ROUNDS = 8
# tetto del risultato di una chiamata, nel turno corrente e nel file della chat
MAX_RESULT_CHARS = 16_000
# nei messaggi vecchi (prima dell'ultima risposta) i risultati si accorciano:
# non servono più per intero e riempirebbero il contesto a ogni turno
OLD_RESULT_CHARS = 1_000


@dataclass(frozen=True)
class Tool:
    """Uno strumento: nome, descrizione e parametri (JSON Schema) per il
    modello, e la funzione che lo esegue (nel thread di `ToolWorker`)."""

    name: str
    description: str
    run: Callable[[dict], str]
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    read_only: bool = True

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Tool disponibili, per nome. Vuoto = nessun tool nella richiesta."""

    def __init__(self, tools=()) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools:
            self.add(t)

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict]:
        return [self._tools[n].schema() for n in self.names()]

    def __len__(self) -> int:
        return len(self._tools)


def shorten(text: str, limit: int) -> str:
    """Accorcia `text` a `limit` caratteri, dicendo al modello quanto manca."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... truncated: {len(text) - limit} more characters]"


def parse_calls(raw) -> list[dict]:
    """Chiamate di `message.tool_calls` in forma uniforme.

    Ollama manda `{"function": {"name", "arguments"}}`, con gli argomenti
    come oggetto; per sicurezza si accettano anche argomenti in una stringa
    JSON. Le voci senza nome si scartano. Ogni chiamata diventa
    `{"name", "arguments": dict}`, più `"id"` se Ollama lo fornisce.
    """
    calls: list[dict] = []
    if not isinstance(raw, list):
        return calls
    for item in raw:
        if not isinstance(item, dict):
            continue
        fn = item.get("function") if isinstance(item.get("function"), dict) else item
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            continue
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except ValueError:
                args = {"_raw": args}
        if not isinstance(args, dict):
            args = {}
        call = {"name": name, "arguments": args}
        if isinstance(item.get("id"), str) and item["id"]:
            call["id"] = item["id"]
        calls.append(call)
    return calls


def run_call(registry: ToolRegistry, call: dict) -> tuple[str, bool]:
    """Esegue una chiamata: (risultato per il modello, errore?).

    Non solleva mai: un tool che fallisce, o un nome inventato dal modello,
    diventa un risultato d'errore che il modello legge e può correggere.
    """
    tool = registry.get(call.get("name", ""))
    if tool is None:
        available = ", ".join(registry.names()) or "none"
        return f"Error: unknown tool '{call.get('name')}'. Available tools: {available}.", True
    args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
    try:
        result = tool.run(args)
    except Exception as e:  # noqa: BLE001 — l'errore va al modello, non all'app
        return shorten(f"Error: {type(e).__name__}: {e}", MAX_RESULT_CHARS), True
    if not isinstance(result, str):
        result = json.dumps(result, ensure_ascii=False, default=str)
    return shorten(result, MAX_RESULT_CHARS), False


class ToolWorker(QThread):
    """Esegue in ordine le chiamate di un giro, fuori dal thread della UI."""

    call_started = Signal(int)           # indice della chiamata
    call_done = Signal(int, str, bool)   # indice, risultato, errore?
    all_done = Signal()                  # giro completo (non emesso dopo uno stop)

    def __init__(self, registry: ToolRegistry, calls: list[dict], parent=None):
        super().__init__(parent)
        self._registry = registry
        self._calls = list(calls)
        self._stopped = False

    def stop(self) -> None:
        """Nessuna nuova chiamata; quella in corso finisce da sola (i tool
        hanno tetti di tempo), e il motore ne scarta il risultato."""
        self._stopped = True

    def run(self) -> None:
        for i, call in enumerate(self._calls):
            if self._stopped:
                return
            self.call_started.emit(i)
            text, error = run_call(self._registry, call)
            if self._stopped:
                return
            self.call_done.emit(i, text, error)
        if not self._stopped:
            self.all_done.emit()

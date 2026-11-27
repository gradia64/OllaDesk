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
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QThread, Signal

from .i18n import N_, _

# giri di chiamate per una risposta: poi si richiama il modello senza tool,
# così deve rispondere (un modello piccolo può chiamare lo stesso tool
# all'infinito)
MAX_ROUNDS = 8
# con la scrittura servono più giri: leggere, modificare, rileggere, e un
# compito su due file con un modello piccolo arrivava al tetto di 8
MAX_WRITE_ROUNDS = 12
# chiamate eseguite al massimo in un giro: il tetto dei giri non ferma un
# modello che ne chiede cinquanta insieme
MAX_CALLS_PER_ROUND = 8
# argomenti di una chiamata (in JSON): oltre, la chiamata è un errore e
# l'argomento non si salva nella chat
MAX_ARGS_CHARS = 4_000
# i tool di scrittura portano il testo del file negli argomenti
MAX_WRITE_ARGS_CHARS = 200_000
LARGE_ARGS_TOOLS = frozenset({"write_file", "edit_file"})
# tool che cambiano i file (0.5): la vista ne mostra l'esito
WRITE_TOOL_NAMES = frozenset({"write_file", "edit_file", "make_dir", "move_file",
                              "delete_file"})
# tetto del risultato di una chiamata, nel turno corrente e nel file della chat
MAX_RESULT_CHARS = 16_000
# nei messaggi vecchi (prima dell'ultima risposta) i risultati si accorciano:
# non servono più per intero e riempirebbero il contesto a ogni turno
OLD_RESULT_CHARS = 1_000


# livelli di accesso dei tool di una conversazione (0.5, W1). Nel file della
# chat il campo "tools" manca (spento), vale True (sola lettura: lo leggono
# anche le versioni 0.4) o "write"/"full"
LEVELS = ("off", "read", "write", "full")
WRITE_LEVELS = ("write", "full")


def normalize_level(value) -> str:
    """Livello da un valore salvato o passato: True = lettura, il resto spento."""
    if value is True:
        return "read"
    return value if isinstance(value, str) and value in LEVELS else "off"


def stored_level(level: str):
    """Valore da salvare nel campo "tools" della chat (None = da togliere)."""
    level = normalize_level(level)
    return None if level == "off" else True if level == "read" else level


@dataclass(frozen=True)
class Tool:
    """Uno strumento: nome, descrizione e parametri (JSON Schema) per il
    modello, e la funzione che lo esegue (nel thread di `ToolWorker`)."""

    name: str
    description: str
    run: Callable[[dict], str]
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    read_only: bool = True
    # interrompe da un altro thread una chiamata in corso (connessioni di
    # rete); None = il tool finisce da solo entro i suoi tetti di tempo
    stop: Callable[[], None] | None = None

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolError(Exception):
    """Errore previsto di un tool (percorso fuori, file assente…): il modello
    legge il messaggio così com'è, senza il nome dell'eccezione."""


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
        size = len(json.dumps(args, ensure_ascii=False, default=str))
        if size > (MAX_WRITE_ARGS_CHARS if name in LARGE_ARGS_TOOLS else MAX_ARGS_CHARS):
            args = {"_too_long": size}
        call = {"name": name, "arguments": args}
        if isinstance(item.get("id"), str) and item["id"]:
            call["id"] = item["id"]
        calls.append(call)
        if len(calls) >= MAX_CALLS_PER_ROUND:
            break
    return calls


def round_note(done: int, limit: int = MAX_ROUNDS) -> str:
    """Riga per il modello dopo il giro `done` di `limit`: quanti giri restano.

    Senza, un modello piccolo non sa del tetto e continua a chiamare tool
    (otto ricerche quasi uguali) finché il motore non lo ferma.
    """
    left = limit - done
    if left <= 0:
        return (f"[tool round {done} of {limit}: no tool rounds left. "
                "Answer now with what you have.]")
    if left <= 2:
        return (f"[tool round {done} of {limit}: only {left} left. "
                "Answer with what you have unless another call is really needed.]")
    return f"[tool round {done} of {limit}]"


# nomi leggibili dei tool per gli avvisi all'utente; un tool senza etichetta
# (per esempio uno nuovo) si mostra col nome tecnico
TOOL_LABELS = {
    "web_search": N_("ricerca web"),
    "fetch_page": N_("lettura pagina"),
    "list_files": N_("elenco cartella"),
    "read_file": N_("lettura file"),
    "search_text": N_("ricerca nei file"),
    "write_file": N_("scrittura file"),
    "edit_file": N_("modifica file"),
    "make_dir": N_("creazione cartella"),
    "move_file": N_("spostamento file"),
    "delete_file": N_("eliminazione file"),
}


def tool_label(name: str) -> str:
    """Nome del tool per l'utente, tradotto; il nome tecnico se manca."""
    label = TOOL_LABELS.get(name)
    return _(label) if label else name


def tools_used(rounds) -> list[tuple[str, int]]:
    """Tool chiamati nei giri, con quante volte, nell'ordine del primo uso."""
    counts: dict[str, int] = {}
    for c in calls_view(rounds):
        counts[c["name"]] = counts.get(c["name"], 0) + 1
    return list(counts.items())


def limit_notice(rounds, limit: int) -> str:
    """Avviso per l'utente al tetto dei giri: quali strumenti ha usato il modello.

    Non è un errore (il modello risponde comunque con quello che ha
    raccolto), perciò ℹ e non ⚠.
    """
    used = [f"{tool_label(n)} ({k})" for n, k in tools_used(rounds)]
    if not used:
        return "ℹ " + _(
            'Raggiunto il limite di {count} giri di strumenti: il modello '
            'risponde con quello che ha raccolto.').format(count=limit)
    if len(used) == 1:
        return "ℹ " + _(
            'Il modello ha usato lo strumento {tools} fino al limite di {count} giri: '
            'risponde con quello che ha raccolto.').format(tools=used[0], count=limit)
    listed = _("{items} e {last}").format(items=", ".join(used[:-1]), last=used[-1])
    return "ℹ " + _(
        'Il modello ha usato gli strumenti {tools} fino al limite di {count} giri: '
        'risponde con quello che ha raccolto.').format(tools=listed, count=limit)


# chiamata scritta come testo invece che come tool_calls (formato nativo del
# modello che Ollama non ha riconosciuto, per esempio dopo il tetto dei giri,
# quando la richiesta non offre più tool): non va mostrata come risposta
LEAKED_CALL_MARKERS = ("<|tool_call_start|>", "<tool_call>", "<|python_tag|>",
                       "<function_call>", "[TOOL_CALLS]")

FINAL_ANSWER_REQUEST = (
    "The tool limit for this answer is reached: no tools are available any more. "
    "Write the final answer now, in the user's language, using only the tool results "
    "above. Do not call or mention tools."
)


WEB_UNAVAILABLE_REQUEST = (
    "Web search is unavailable right now: it failed twice in a row, so web_search and "
    "fetch_page were removed for the rest of this answer. Do not try to search again: "
    "answer with what you know or have found, and tell the user that the web search failed."
)

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")


def today_note(day) -> str:
    """Data di oggi per il modello, in inglese e senza dipendere dal locale.

    Con la ricerca web serve: un modello crede di vivere nell'anno del suo
    addestramento e cerca «2024 2025», trovando notizie vecchie.
    """
    return (f"Today's date is {_WEEKDAYS[day.weekday()]} {day.day} {_MONTHS[day.month - 1]} "
            f"{day.year}. Your training data may be older: for recent events, search with "
            "this date in mind and do not assume an earlier year.")


def failed_web_streak(rounds) -> int:
    """Ricerche web fallite di fila, alla fine dei giri completi."""
    streak = 0
    for rnd in rounds or []:
        for c in rnd.get("calls") or []:
            if c.get("name") != "web_search":
                continue
            result = str(c.get("result") or "")
            if not c.get("error"):
                streak = 0
            elif result.startswith(("Error: web search failed", "Error: web search is unavailable")):
                streak += 1
    return streak


def leaked_call_at(text: str) -> int:
    """Posizione della prima chiamata scritta come testo, -1 se non ce n'è."""
    found = [i for i in (text.find(m) for m in LEAKED_CALL_MARKERS) if i >= 0]
    return min(found) if found else -1


def marker_prefix_len(text: str) -> int:
    """Lunghezza della fine di `text` che è l'inizio di un marcatore (0 se
    nessuna): quei caratteri aspettano il frammento successivo."""
    for n in range(min(len(text), max(map(len, LEAKED_CALL_MARKERS)) - 1), 0, -1):
        tail = text[-n:]
        if any(m.startswith(tail) for m in LEAKED_CALL_MARKERS):
            return n
    return 0


_CALL_AFTER_MARKER = re.compile(r"\s*(?:[\[{(\"]|[A-Za-z_][\w.]*\s*\()")


def looks_like_call(held: str) -> bool:
    """True se dopo il marcatore c'è una chiamata (JSON, lista, nome(…)), non
    una frase che parla del marcatore."""
    for m in LEAKED_CALL_MARKERS:
        if held.startswith(m):
            return bool(_CALL_AFTER_MARKER.match(held[len(m):]))
    return True


def call_key(call: dict) -> str:
    """Chiave di una chiamata: stesso nome e stessi argomenti = stessa chiave."""
    args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
    return str(call.get("name")) + "\0" + json.dumps(args, sort_keys=True,
                                                     ensure_ascii=False, default=str)


def repeated_result(round_no: int) -> str:
    return (f"Same call as earlier in this answer (tool round {round_no}): the result "
            "is unchanged and already above. Do not repeat it: use that result or answer.")


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
    if "_too_long" in args:
        limit = MAX_WRITE_ARGS_CHARS if tool.name in LARGE_ARGS_TOOLS else MAX_ARGS_CHARS
        return (f"Error: the arguments are too long ({args['_too_long']} characters, at most "
                f"{limit}): call the tool again with short arguments."), True
    try:
        result = tool.run(args)
    except ToolError as e:
        return shorten(f"Error: {e}", MAX_RESULT_CHARS), True
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

    def __init__(self, registry: ToolRegistry, calls: list[dict], parent=None, *,
                 seen: dict[str, int] | None = None, round_no: int = 1):
        super().__init__(parent)
        self._registry = registry
        self._calls = list(calls)
        self._stopped = False
        # chiamate di sola lettura già riuscite nella risposta: chiave → giro.
        # Una ripetizione non si riesegue: il modello riceve un rimando
        self._seen = dict(seen or {})
        self._round = round_no

    def stop(self) -> None:
        """Nessuna nuova chiamata; quella in corso si interrompe se il tool
        ha un `stop`, altrimenti finisce da sola (i tool hanno tetti di
        tempo). Il motore ne scarta il risultato."""
        self._stopped = True
        for name in self._registry.names():
            tool = self._registry.get(name)
            if tool is not None and tool.stop is not None:
                try:
                    tool.stop()
                except Exception:  # noqa: BLE001 — lo stop non deve fallire
                    pass

    def run(self) -> None:
        for i, call in enumerate(self._calls):
            if self._stopped:
                return
            self.call_started.emit(i)
            key = call_key(call)
            tool = self._registry.get(call.get("name", ""))
            if tool is not None and tool.read_only and key in self._seen:
                text, error = repeated_result(self._seen[key]), False
            else:
                text, error = run_call(self._registry, call)
                if not error and tool is not None and tool.read_only:
                    self._seen.setdefault(key, self._round)
                elif not error and tool is not None:
                    # dopo una modifica le letture di prima non valgono più
                    self._seen.clear()
            if self._stopped:
                return
            self.call_done.emit(i, text, error)
        if not self._stopped:
            self.all_done.emit()


# --------------------------------------------------------- vista delle chiamate

def calls_view(rounds, open_round: dict | None = None) -> list[dict]:
    """Chiamate di una risposta, in ordine, per l'interfaccia e la companion.

    [{"name", "arguments", "result", "error", "state"}] con state "done"
    per i giri completi (`tool_rounds`) e, per il giro in corso, "running"
    finché non arriva il risultato ("waiting" mentre una modifica aspetta la
    conferma dell'utente). Le chiamate dei tool di scrittura concluse hanno
    anche "outcome": "applied", "rejected" (dall'utente), "failed" o
    "undone" (annullata dopo con il pulsante della risposta). Giri malformati (chat modificate a mano)
    si saltano.
    """
    out: list[dict] = []
    sources = [(r, "done") for r in (rounds or [])]
    if open_round:
        sources.append((open_round, "open"))
    for rnd, kind in sources:
        calls = rnd.get("calls") if isinstance(rnd, dict) else None
        if not isinstance(calls, list):
            continue
        for c in calls:
            if not isinstance(c, dict) or not isinstance(c.get("name"), str):
                continue
            if kind == "done" or c.get("done"):
                state = "done"
            else:
                state = "waiting" if c.get("waiting") else "running"
            args = c.get("arguments")
            item = {
                "name": c["name"],
                "arguments": args if isinstance(args, dict) else {},
                "result": str(c.get("result") or ""),
                "error": bool(c.get("error")),
                "state": state,
            }
            if c["name"] in WRITE_TOOL_NAMES and state == "done":
                item["outcome"] = change_outcome(c)
            out.append(item)
    return out


def change_outcome(call: dict) -> str:
    """Esito di una chiamata di scrittura conclusa."""
    if not call.get("error"):
        return "undone" if call.get("undone") else "applied"
    return "rejected" if "the user rejected" in str(call.get("result") or "") or \
        "already rejected" in str(call.get("result") or "") else "failed"


_WEB_TITLE_RE = re.compile(r"^\[\d+\] ")


def result_preview(name: str, result: str) -> str:
    """Testo del risultato da mostrare in anteprima (bolla, telefono).

    Di `web_search` l'intestazione è sempre la stessa: si mostrano i titoli
    dei risultati, uno per riga. Gli altri tool restano come sono.
    """
    result = (result or "").strip()
    if name == "web_search":
        titles = [ln for ln in result.splitlines() if _WEB_TITLE_RE.match(ln)]
        if titles:
            return "\n".join(titles)
    return result


def arguments_summary(args: dict, limit: int = 80) -> str:
    """Argomenti su una riga: `path=progetto/a.py, start_line=10`."""
    parts = []
    for k, v in (args or {}).items():
        text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        text = " ".join(str(text).split())
        if len(text) > limit:
            text = text[:limit - 1] + "…"
        parts.append(f"{k}={text}")
    return ", ".join(parts)

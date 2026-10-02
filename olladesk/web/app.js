// Companion web di OllaDesk: abbinamento, lettura e invio dei messaggi.
// Nessuna libreria esterna: la pagina funziona anche senza internet.
// L'HTML dei messaggi arriva già reso (ed escapato) dal server con md.py.
"use strict";

const $ = (id) => document.getElementById(id);
const VIEWS = ["loading", "pair", "list", "chat"];

let currentId = null;     // conversazione aperta (null = nuova)
let inChat = false;       // vista conversazione (o nuova) attiva
let stream = null;        // EventSource della conversazione aperta
let pending = null;       // risposta in costruzione: {box, body, think, thinkBody}
let busy = false;         // il PC sta elaborando (qualsiasi conversazione)
let activeHere = false;   // l'elaborazione riguarda la conversazione aperta
let haveModels = false;

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch (e) { /* archiviazione non disponibile */ }
  return null;
}

function show(view) {
  for (const v of VIEWS) $("view-" + v).hidden = v !== view;
  inChat = view === "chat";
  $("back").hidden = !inChat;
  $("new-chat").hidden = view !== "list";
  $("refresh").hidden = view !== "list" && view !== "chat";
  $("composer").hidden = !inChat;
  document.body.classList.toggle("composing", inChat);
  if (!inChat) {
    $("title").textContent = "OllaDesk";
    closeStream();
  }
}

function netError(msg) {
  $("net-error").textContent = msg || "";
  $("net-error").hidden = !msg;
}

class Unauthorized extends Error {}

async function api(path, options) {
  let res;
  try {
    res = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options });
  } catch (e) {
    throw new Error("PC non raggiungibile: OllaDesk è aperto e sulla stessa rete?");
  }
  if (res.status === 401) throw new Unauthorized();
  let data = {};
  try { data = await res.json(); } catch (e) { /* corpo vuoto */ }
  if (!res.ok) throw new Error(data.error || "errore " + res.status);
  return data;
}

function post(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const sameDay = d.toDateString() === new Date().toDateString();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return sameDay ? time : d.toLocaleDateString([], { day: "numeric", month: "short" }) + " " + time;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function scrollToEnd() {
  window.scrollTo(0, document.body.scrollHeight);
}

// ------------------------------------------------------------ abbinamento

async function pair(code) {
  $("pair-error").hidden = true;
  try {
    await post("/api/pair", { code });
    $("pair-code").value = "";
    route();
  } catch (e) {
    show("pair");
    $("pair-error").textContent = e.message;
    $("pair-error").hidden = false;
  }
}

$("pair-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  pair($("pair-code").value.trim());
});

// --------------------------------------------------------------- elenco

async function showList() {
  const data = await api("/api/chats");
  const ul = $("chat-list");
  ul.replaceChildren();
  for (const c of data.chats) {
    const a = el("a");
    a.href = "#/c/" + encodeURIComponent(c.id);
    a.append(el("span", "t", c.title || "Conversazione"));
    a.append(el("span", "m", [c.model, fmtTime(c.updated)].filter(Boolean).join(" · ")));
    const li = el("li");
    li.append(a);
    ul.append(li);
  }
  $("list-empty").hidden = data.chats.length > 0;
  show("list");
}

// ---------------------------------------------------------- messaggi

function setThinking(target, html) {
  // blocco «Pensiero» richiuso, come sul desktop (resta aperto se l'utente l'ha aperto)
  if (!html) return;
  if (!target.think) {
    target.think = el("details", "think");
    target.think.append(el("summary", "", "🧠 Pensiero"));
    target.thinkBody = el("div", "body");
    target.think.append(target.thinkBody);
    target.box.insertBefore(target.think, target.body);
  }
  target.thinkBody.innerHTML = html;
}

function renderMessage(m) {
  const box = el("div", "msg " + m.role);
  const chips = [];
  if (m.web) chips.push("🌐 Ricerca web");
  for (const a of m.attachments || []) chips.push("📎 " + a);
  if (chips.length) {
    const row = el("div", "chips");
    for (const c of chips) row.append(el("span", "chip", c));
    box.append(row);
  }
  const body = el("div", "body");
  body.innerHTML = m.html;
  box.append(body);
  setThinking({ box, body }, m.thinking_html);
  const meta = [fmtTime(m.ts), m.stats].filter(Boolean).join(" · ");
  if (meta) box.append(el("div", "meta", meta));
  return box;
}

function addNote(text, isError) {
  $("messages").append(el("div", "note" + (isError ? " error" : ""), text));
  scrollToEnd();
}

function startPending(status) {
  if (pending) return pending;
  const box = el("div", "msg assistant");
  const body = el("div", "body");
  body.append(el("span", "muted typing", status || ""));
  box.append(body);
  $("messages").append(box);
  pending = { box, body, think: null, thinkBody: null, hasText: false };
  scrollToEnd();
  return pending;
}

function finishPending(d) {
  const p = pending;
  pending = null;
  if (!p) return;
  if (!p.hasText && d.outcome !== "done") {
    p.box.remove();     // interrotta o fallita prima di ogni testo
  } else {
    const meta = [fmtTime(Date.now() / 1000), d.stats].filter(Boolean).join(" · ");
    p.box.append(el("div", "meta", meta));
  }
  if (d.outcome === "failed") {
    addNote((d.error || "errore") +
      "\nVerifica sul PC che Ollama sia avviato.", true);
  }
}

// ------------------------------------------------------------- stream SSE

function closeStream() {
  if (stream) stream.close();
  stream = null;
  pending = null;
  activeHere = false;
}

function openStream(id, after) {
  if (stream) stream.close();   // lo stato della vista (pending, activeHere) resta
  const es = new EventSource(
    "/api/chats/" + encodeURIComponent(id) + "/events?after=" + after);
  stream = es;
  const on = (kind, fn) => es.addEventListener(kind, (ev) => {
    if (stream === es) fn(JSON.parse(ev.data));
  });
  on("busy", (d) => { busy = d.busy; updateComposer(); });
  on("user", (m) => { $("messages").append(renderMessage(m)); scrollToEnd(); });
  on("search", () => {
    activeHere = true;
    startPending("🌐 Ricerca web in corso");
    updateComposer();
  });
  on("search_done", () => {
    if (pending && !pending.hasText) { pending.box.remove(); pending = null; }
  });
  on("start", () => { activeHere = true; startPending(); updateComposer(); });
  on("answer", (d) => {
    const p = startPending();
    p.body.innerHTML = d.html;
    p.hasText = !!d.html;
    setThinking(p, d.thinking_html);
    if (!p.hasText && d.thinking_html) {
      p.body.replaceChildren(el("span", "muted typing", "sta pensando"));
    }
    scrollToEnd();
  });
  on("done", (d) => { finishPending(d); activeHere = false; updateComposer(); });
  on("notice", (d) => addNote(d.text));
  es.onerror = () => {
    // 401 o server spento: EventSource non riprova da solo
    if (es.readyState === EventSource.CLOSED && stream === es) route();
  };
}

// ---------------------------------------------------------- conversazione

async function loadModels(preferred) {
  const info = await api("/api/models");
  const sel = $("model");
  sel.replaceChildren();
  for (const m of info.models) {
    const extra = [m.details.parameter_size, m.details.quantization_level].filter(Boolean).join(" · ");
    const opt = el("option", "", extra ? m.name + "  (" + extra + ")" : m.name);
    opt.value = m.name;
    sel.append(opt);
  }
  haveModels = info.models.length > 0;
  if (!haveModels) {
    const opt = el("option", "", info.online ? "— nessun modello —" : "— Ollama non raggiungibile —");
    sel.append(opt);
  }
  const names = info.models.map((m) => m.name);
  for (const want of [preferred, store("olladesk.model")]) {
    if (want && names.includes(want)) { sel.value = want; break; }
  }
  const think = store("olladesk.think");
  setThink(think === null ? info.thinking : think === "1");
  busy = info.busy;
  updateComposer();
}

function setThink(on) {
  $("think").setAttribute("aria-pressed", on ? "true" : "false");
  $("think").title = on ? "Ragionamento attivo" : "Ragionamento spento";
}

$("think").addEventListener("click", () => {
  const on = $("think").getAttribute("aria-pressed") !== "true";
  setThink(on);
  store("olladesk.think", on ? "1" : "0");
});

function updateComposer() {
  const btn = $("send");
  if (activeHere) {
    btn.textContent = "■";
    btn.setAttribute("aria-label", "Interrompi");
    btn.disabled = false;
  } else {
    btn.textContent = "➤";
    btn.setAttribute("aria-label", "Invia");
    btn.disabled = busy || !haveModels;
  }
  $("busy-note").hidden = !(busy && !activeHere);
}

async function showChat(id) {
  closeStream();
  const [chat] = await Promise.all([
    api("/api/chats/" + encodeURIComponent(id)),
    loadModels(null),
  ]);
  currentId = id;
  $("title").textContent = chat.title || "Conversazione";
  $("chat-model").textContent = chat.model || "";
  $("messages").replaceChildren(...chat.messages.map(renderMessage));
  if (chat.model && [...$("model").options].some((o) => o.value === chat.model)) {
    $("model").value = chat.model;
  }
  show("chat");
  if (chat.state) {
    activeHere = true;
    startPending(chat.state === "search" ? "🌐 Ricerca web in corso" : "");
  }
  updateComposer();
  openStream(id, chat.seq);
  scrollToEnd();
}

async function showNewChat() {
  closeStream();
  await loadModels(null);
  currentId = null;
  $("title").textContent = "Nuova conversazione";
  $("chat-model").textContent = "";
  $("messages").replaceChildren();
  show("chat");
  updateComposer();
  $("input").focus();
}

async function send() {
  if (activeHere) {
    if (currentId) {
      try { await post("/api/stop", { chat_id: currentId }); } catch (e) { addNote(e.message, true); }
    }
    return;
  }
  const text = $("input").value.trim();
  if (!text || $("send").disabled) return;
  const model = $("model").value;
  const think = $("think").getAttribute("aria-pressed") === "true";
  $("send").disabled = true;
  let res;
  try {
    res = await post("/api/send", { chat_id: currentId, text, model, think });
  } catch (e) {
    if (e instanceof Unauthorized) { route(); return; }
    addNote(e.message, true);
    updateComposer();
    return;
  }
  $("input").value = "";
  autosize();
  store("olladesk.model", model);
  if (currentId === null) {
    // conversazione nuova: la vista si apre sull'id assegnato dal PC
    location.hash = "#/c/" + encodeURIComponent(res.chat_id);
  } else {
    activeHere = true;   // gli eventi arrivano dallo stream già aperto
    updateComposer();
  }
}

function autosize() {
  const t = $("input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 160) + "px";
}

$("composer").addEventListener("submit", (ev) => { ev.preventDefault(); send(); });
$("input").addEventListener("input", autosize);
$("input").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); send(); }
});

// ----------------------------------------------------------------- router

async function route() {
  netError("");
  // QR code: indirizzo del PC + #pair=123456. Il codice sta nel frammento,
  // che il browser non invia al server, e sparisce subito dalla barra
  const qr = location.hash.match(/^#pair=(\d{6})$/);
  if (qr) {
    history.replaceState(null, "", location.pathname);
    await pair(qr[1]);
    return;
  }
  const m = location.hash.match(/^#\/c\/(.+)$/);
  try {
    if (m) await showChat(decodeURIComponent(m[1]));
    else if (location.hash === "#/new") await showNewChat();
    else await showList();
  } catch (e) {
    if (e instanceof Unauthorized) {
      show("pair");
      $("pair-code").focus();
    } else {
      netError(e.message);
      if (!$("view-loading").hidden) show("list");
    }
  }
}

$("back").addEventListener("click", () => { location.hash = ""; });
$("new-chat").addEventListener("click", () => { location.hash = "#/new"; });
$("refresh").addEventListener("click", route);
window.addEventListener("hashchange", route);
route();

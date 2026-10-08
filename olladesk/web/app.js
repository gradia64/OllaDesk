// Companion web di OllaDesk: abbinamento, lettura e invio dei messaggi.
// Nessuna libreria esterna: la pagina funziona anche senza internet.
// L'HTML dei messaggi arriva già reso (ed escapato) dal server con md.py.
"use strict";

const $ = (id) => document.getElementById(id);
const VIEWS = ["loading", "pair", "list", "models", "chat"];

let currentId = null;     // conversazione aperta (null = nuova)
let inChat = false;       // vista conversazione (o nuova) attiva
let stream = null;        // EventSource della conversazione aperta
let pending = null;       // risposta in costruzione: {box, body, think, thinkBody}
let busy = false;         // il PC sta elaborando (qualsiasi conversazione)
let activeHere = false;   // l'elaborazione riguarda la conversazione aperta
let haveModels = false;
let atts = [];            // allegati della barra di scrittura: {name, state, id, chip}
let sentNew = null;       // chat appena creata da qui: {id, after} della risposta all'invio
const MAX_SIDE = 1600;    // lato lungo delle foto caricate (px)

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
  $("back").hidden = !inChat && view !== "models";
  $("models-btn").hidden = view !== "list";
  $("new-chat").hidden = view !== "list";
  $("refresh").hidden = view !== "list" && view !== "chat";
  $("composer").hidden = !inChat;
  $("chat-menu").hidden = true;   // showChat lo mostra per le chat già salvate
  closeMenu();
  document.body.classList.toggle("composing", inChat);
  if (view !== "models") stopModelsPoll();
  if (!inChat) {
    $("title").textContent = view === "models" ? "Modelli" : "OllaDesk";
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
  if (!res.ok) {
    const err = new Error(data.error || "errore " + res.status);
    err.status = res.status;
    throw err;
  }
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

let listActive = null;     // conversazione che sta rispondendo (elenco)
let listTimer = null;

function renderList(data) {
  const ul = $("chat-list");
  ul.replaceChildren();
  for (const c of data.chats) {
    const a = el("a");
    a.href = "#/c/" + encodeURIComponent(c.id);
    a.dataset.id = c.id;
    const t = el("span", "t", c.title || "Conversazione");
    t.prepend(el("span", "badge", "● "));
    a.append(t);
    a.append(el("span", "m", [c.model, fmtTime(c.updated)].filter(Boolean).join(" · ")));
    const li = el("li");
    li.append(a);
    ul.append(li);
  }
  $("list-empty").hidden = data.chats.length > 0;
  listActive = data.active;
  markActive();
}

function markActive() {
  // pallino sulla conversazione che sta rispondendo (anche dal PC)
  for (const a of $("chat-list").querySelectorAll("a")) {
    a.classList.toggle("active", a.dataset.id === listActive);
  }
}

async function showList() {
  const data = await api("/api/chats");
  renderList(data);
  show("list");
  openListStream(data.seq);
}

function openListStream(after) {
  if (stream) stream.close();
  const es = new EventSource("/api/events?after=" + after);
  stream = es;
  es.addEventListener("busy", (ev) => {
    if (stream !== es) return;
    const d = JSON.parse(ev.data);
    listActive = d.busy ? d.chat_id : null;
    markActive();
  });
  es.addEventListener("chats", () => {
    if (stream !== es) return;
    // più modifiche ravvicinate (salvataggi durante una risposta): un solo ricaricamento
    clearTimeout(listTimer);
    listTimer = setTimeout(async () => {
      if (stream !== es) return;
      try { renderList(await api("/api/chats")); } catch (e) { /* riprova al prossimo evento */ }
    }, 300);
  });
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED && stream === es) route();
  };
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
  addSources(box, m.sources);
  addFoot(box, () => m.text, [fmtTime(m.ts), m.stats].filter(Boolean).join(" · "));
  return box;
}

// piè del messaggio: pulsante ⧉ a sinistra, ora e statistiche a destra
function addFoot(box, getText, meta) {
  const foot = el("div", "foot");
  const btn = el("button", "copy", "⧉");
  btn.type = "button";
  btn.title = "Copia il messaggio";
  btn.setAttribute("aria-label", "Copia il messaggio");
  btn.onclick = () => copyMessage(getText() || "", btn);
  foot.append(btn, el("div", "meta", meta));
  box.append(foot);
}

// Su HTTP in LAN navigator.clipboard non esiste (serve un contesto sicuro):
// si prova execCommand, e se anche quello fallisce si mostra il testo già
// selezionato, da copiare con il menu del telefono.
async function copyMessage(text, btn) {
  if (!text) return;
  let ok = false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      ok = true;
    }
  } catch (e) { /* ripiego sotto */ }
  if (!ok) ok = legacyCopy(text);
  if (ok) {
    btn.textContent = "✓";
    setTimeout(() => { btn.textContent = "⧉"; }, 1500);
  } else {
    showSelectable(text);
  }
}

function legacyCopy(text) {
  const ta = el("textarea");
  ta.value = text;
  ta.readOnly = true;
  ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;font-size:16px";
  document.body.append(ta);
  ta.select();
  ta.setSelectionRange(0, text.length);
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  ta.remove();
  return ok;
}

function showSelectable(text) {
  const old = document.getElementById("copybox");
  if (old) old.remove();
  const wrap = el("div", "copybox");
  wrap.id = "copybox";
  const card = el("div", "card");
  card.append(el("p", "", "Copia automatica non disponibile: tieni premuto sul testo selezionato e scegli «Copia»."));
  const ta = el("textarea");
  ta.value = text;
  ta.readOnly = true;
  ta.rows = 8;
  const close = el("button", "", "Chiudi");
  close.type = "button";
  close.onclick = () => wrap.remove();
  card.append(ta, close);
  wrap.append(card);
  document.body.append(wrap);
  ta.focus();
  ta.select();
  ta.setSelectionRange(0, text.length);
}

// fonti della ricerca web sotto la risposta: titoli e link arrivano dai
// motori di ricerca, quindi solo testo (mai innerHTML) e solo http/https
function addSources(box, sources) {
  const ok = (sources || []).filter((s) => /^https?:\/\//i.test(s.url));
  if (!ok.length) return;
  const d = el("details", "sources");
  d.append(el("summary", "", "🌐 Fonti (" + ok.length + ")"));
  const list = el("ol");
  for (const s of ok) {
    const li = el("li");
    li.value = s.n;
    const a = el("a", "", s.title);
    a.href = s.url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    li.append(a, el("span", "host", " — " + s.host));
    list.append(li);
  }
  d.append(list);
  box.append(d);
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
  pending = { box, body, think: null, thinkBody: null, hasText: false, raw: "" };
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
    addSources(p.box, d.sources);
    addFoot(p.box, () => p.raw,
      [fmtTime(d.ts || Date.now() / 1000), d.stats].filter(Boolean).join(" · "));
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

function openStream(id, after, noticesAfter) {
  if (stream) stream.close();   // lo stato della vista (pending, activeHere) resta
  let url = "/api/chats/" + encodeURIComponent(id) + "/events?after=" + after;
  if (noticesAfter != null) url += "&notices_after=" + noticesAfter;
  const es = new EventSource(url);
  stream = es;
  const on = (kind, fn) => es.addEventListener(kind, (ev) => {
    if (stream === es) fn(JSON.parse(ev.data));
  });
  on("busy", (d) => {
    busy = d.busy;
    if (!busy) {
      // elaborazione finita: di solito «done» è già arrivato, ma uno stop
      // durante la ricerca web non ha risposta e quindi niente «done»
      if (pending && !pending.hasText) { pending.box.remove(); pending = null; }
      activeHere = false;
    }
    updateComposer();
  });
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
    p.raw = d.text || "";
    p.hasText = !!d.html;
    setThinking(p, d.thinking_html);
    if (!p.hasText && d.thinking_html) {
      p.body.replaceChildren(el("span", "muted typing", "sta pensando"));
    }
    scrollToEnd();
  });
  on("done", (d) => { finishPending(d); activeHere = false; updateComposer(); });
  on("notice", (d) => addNote(d.text));
  let goneTimer = null;
  on("chats", () => {
    // la conversazione aperta è stata eliminata sul PC? (una nuova in
    // ricerca web non è ancora nell'elenco ma è quella attiva)
    clearTimeout(goneTimer);
    goneTimer = setTimeout(async () => {
      if (stream !== es || !currentId) return;
      try {
        const d = await api("/api/chats");
        const me = d.chats.find((c) => c.id === currentId);
        if (me) $("title").textContent = me.title || "Conversazione";   // rinominata
        else if (d.active !== currentId) chatGone();
      } catch (e) { /* riprova al prossimo evento */ }
    }, 300);
  });
  es.onerror = () => {
    // 401 o server spento: EventSource non riprova da solo
    if (es.readyState === EventSource.CLOSED && stream === es) route();
  };
}

// ----------------------------------------------------------------- modelli

let modelsTimer = null;
let taskSeen = null;      // operazione vista in corso da questa pagina: fine da mostrare

function stopModelsPoll() {
  clearTimeout(modelsTimer);
  modelsTimer = null;
}

function fmtSize(n) {
  if (!n) return "";
  const gb = n / 1e9;
  return gb >= 1 ? gb.toFixed(1) + " GB" : Math.round(n / 1e6) + " MB";
}

function renderModels(info) {
  const ul = $("model-list");
  ul.replaceChildren();
  const taskBusy = !!info.task && info.task.state === "running";
  for (const m of info.models) {
    const row = el("li", "model-row");
    const text = el("div", "model-text");
    text.append(el("span", "t", m.name));
    const extra = [fmtSize(m.size), m.details.parameter_size, m.details.quantization_level]
      .filter(Boolean).join(" · ");
    if (extra) text.append(el("span", "m", extra));
    const del = el("button", "model-del", "🗑");
    del.type = "button";
    del.setAttribute("aria-label", "Elimina " + m.name);
    del.disabled = taskBusy || info.busy;
    del.title = info.busy ? "Il PC sta rispondendo" : "Elimina dal disco del PC";
    del.addEventListener("click", () => deleteModel(m.name));
    row.append(text, del);
    ul.append(row);
  }
  $("models-empty").hidden = info.models.length > 0;
  $("pull-btn").disabled = taskBusy;
  renderTask(info.task);
}

function renderTask(t) {
  const box = $("task");
  if (t && t.state === "running") taskSeen = "running";
  // l'esito si mostra solo se l'operazione è stata seguita da questa pagina
  // o è finita da poco (un minuto)
  const fresh = t && t.state !== "running" && (taskSeen === "running" ||
    (t.ended && Date.now() / 1000 - t.ended < 60));
  if (!t || (t.state !== "running" && !fresh)) { box.hidden = true; return; }
  box.hidden = false;
  box.classList.toggle("failed", t.state === "failed");
  const verb = t.op === "pull" ? "Scaricamento" : "Eliminazione";
  const bar = $("task-bar");
  const cancel = $("task-cancel");
  if (t.state === "running") {
    $("task-text").textContent = verb + " di " + t.name + (t.pct != null ? " — " + t.pct + "%" : "…") +
      (t.pct == null && t.status ? " (" + t.status + ")" : "");
    bar.hidden = t.pct == null;
    if (t.pct != null) bar.value = t.pct;
    cancel.hidden = t.op !== "pull";
  } else {
    $("task-text").textContent = t.state === "done"
      ? "✓ " + verb + " di " + t.name + " completato"
      : "⚠ " + verb + " di " + t.name + " non riuscito: " + t.error;
    bar.hidden = true;
    cancel.hidden = true;
  }
}

let wasRunning = false;

async function refreshModels() {
  const info = await api("/api/models");
  renderModels(info);
  stopModelsPoll();
  const running = !!info.task && info.task.state === "running";
  if (running) {
    modelsTimer = setTimeout(pollModels, 1500);
  } else if (wasRunning) {
    // a operazione finita il PC sta ancora ricaricando l'elenco dei modelli:
    // un ultimo aggiornamento poco dopo
    modelsTimer = setTimeout(pollModels, 1200);
  }
  wasRunning = running;
}

async function pollModels() {
  try {
    await refreshModels();
  } catch (e) {
    if (e instanceof Unauthorized) { route(); return; }
    netError(e.message);
    modelsTimer = setTimeout(pollModels, 4000);   // il PC può tornare raggiungibile
  }
}

async function showModels() {
  taskSeen = null;
  wasRunning = false;
  show("models");
  await refreshModels();
}

async function modelAction(path, body) {
  netError("");
  try {
    await post(path, body);
  } catch (e) {
    if (e instanceof Unauthorized) { route(); return; }
    netError(e.message);
  }
  wasRunning = true;   // un'operazione veloce può finire prima del primo aggiornamento
  try { await refreshModels(); } catch (e) { netError(e.message); }
}

$("pull-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const name = $("pull-name").value.trim();
  if (!name) return;
  await modelAction("/api/models/pull", { name });
  $("pull-name").value = "";
});

function deleteModel(name) {
  if (!confirm("Eliminare definitivamente «" + name + "» dal disco del PC?")) return;
  modelAction("/api/models/delete", { name });
}

$("task-cancel").addEventListener("click", () => modelAction("/api/models/cancel", {}));

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

// ricerca web: come sul PC resta accesa fra un messaggio e l'altro, ma non
// viene ricordata: a ogni apertura della pagina riparte spenta, perché la
// domanda andrebbe a un servizio esterno
function setWeb(on) {
  $("web").setAttribute("aria-pressed", on ? "true" : "false");
  $("web").title = on ? "Ricerca web attiva" : "Ricerca web spenta";
  $("web-note").hidden = !on;
  document.body.classList.toggle("web-on", on);
}

function webOn() {
  return $("web").getAttribute("aria-pressed") === "true";
}

$("web").addEventListener("click", () => setWeb(!webOn()));

function updateComposer() {
  const btn = $("send");
  if (activeHere) {
    btn.textContent = "■";
    btn.setAttribute("aria-label", "Interrompi");
    btn.disabled = false;
  } else {
    btn.textContent = "➤";
    btn.setAttribute("aria-label", "Invia");
    btn.disabled = busy || !haveModels || atts.some((a) => a.state === "uploading");
  }
  $("busy-note").hidden = !(busy && !activeHere);
}

async function showChat(id) {
  closeStream();
  clearAtts();
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
  $("chat-menu").hidden = false;   // solo per le chat già salvate
  if (chat.state) {
    activeHere = true;
    startPending(chat.state === "search" ? "🌐 Ricerca web in corso" : "");
  }
  updateComposer();
  // chat appena creata da qui: gli avvisi dell'invio non sono nella copia
  const na = sentNew && sentNew.id === id ? sentNew.after : null;
  sentNew = null;
  openStream(id, chat.seq, na);
  scrollToEnd();
}

async function showNewChat() {
  closeStream();
  clearAtts();
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
  const ids = atts.filter((a) => a.state === "ready").map((a) => a.id);
  if ((!text && !ids.length) || $("send").disabled) return;
  const model = $("model").value;
  const think = $("think").getAttribute("aria-pressed") === "true";
  const web = webOn();
  $("send").disabled = true;
  let res;
  try {
    res = await post("/api/send", { chat_id: currentId, text, model, think, web, attachments: ids });
  } catch (e) {
    if (e instanceof Unauthorized) { route(); return; }
    if (e.status === 404) { chatGone(); return; }
    addNote(e.message, true);
    updateComposer();
    return;
  }
  clearAtts();   // il PC li ha presi in carico
  $("input").value = "";
  autosize();
  store("olladesk.model", model);
  if (currentId === null) {
    // conversazione nuova: la vista si apre sull'id assegnato dal PC
    sentNew = { id: res.chat_id, after: res.after };
    location.hash = "#/c/" + encodeURIComponent(res.chat_id);
  } else {
    activeHere = true;   // gli eventi arrivano dallo stream già aperto
    updateComposer();
  }
}

// ----------------------------------------------------------------- allegati

async function prepareFile(file) {
  // foto: lato lungo ridotto a MAX_SIDE e JPEG, così il caricamento è
  // leggero; anche i formati che il PC non conosce (es. HEIC) diventano JPEG
  const isImage = file.type.startsWith("image/") && file.type !== "image/gif";
  const known = /\.(png|jpe?g)$/i.test(file.name);
  if (isImage) {
    try {
      const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
      const scale = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
      if (scale < 1 || !known || file.size > 2 * 1024 * 1024) {
        const c = document.createElement("canvas");
        c.width = Math.round(bmp.width * scale);
        c.height = Math.round(bmp.height * scale);
        c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
        const blob = await new Promise((r) => c.toBlob(r, "image/jpeg", 0.85));
        if (blob) return { blob, name: file.name.replace(/\.[^.]*$/, "") + ".jpg" };
      }
    } catch (e) { /* non decodificabile qui: si carica com'è */ }
  }
  return { blob: file, name: file.name };
}

function renderAtts() {
  const box = $("atts");
  box.replaceChildren(...atts.map((a) => a.chip));
  box.hidden = atts.length === 0;
  updateComposer();
}

function clearAtts() {
  atts = [];
  renderAtts();
}

async function addFile(file) {
  const entry = { name: file.name, state: "uploading", id: null, chip: el("span", "chip") };
  const label = el("span", "", "📎 " + file.name + " …");
  const remove = el("button", "chip-x", "✕");
  remove.type = "button";
  remove.setAttribute("aria-label", "Togli " + file.name);
  remove.addEventListener("click", () => {
    atts = atts.filter((a) => a !== entry);
    renderAtts();
  });
  entry.chip.append(label, remove);
  atts.push(entry);
  renderAtts();
  try {
    const { blob, name } = await prepareFile(file);
    const meta = await api("/api/upload?name=" + encodeURIComponent(name), {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: blob,
    });
    entry.id = meta.id;
    entry.state = "ready";
    label.textContent = (meta.kind === "image" ? "🖼 " : "📎 ") + meta.name;
  } catch (e) {
    if (e instanceof Unauthorized) { route(); return; }
    entry.state = "error";
    label.textContent = "⚠ " + file.name;
    entry.chip.title = e.message;
    addNote(file.name + ": " + e.message, true);
  }
  renderAtts();
}

$("attach").addEventListener("click", () => $("file").click());
$("file").addEventListener("change", () => {
  for (const f of $("file").files) addFile(f);
  $("file").value = "";   // lo stesso file si può riscegliere
});

function autosize() {
  const t = $("input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 160) + "px";
}

async function chatGone() {
  // senza hashchange: route() mostra l'elenco, poi resta visibile l'avviso
  history.replaceState(null, "", location.pathname);
  await route();
  netError("La conversazione è stata eliminata sul PC.");
}

// i link delle risposte si aprono in una nuova scheda: la pagina resta qui
$("messages").addEventListener("click", (ev) => {
  const a = ev.target.closest(".body a[href]");
  if (!a) return;
  ev.preventDefault();
  window.open(a.href, "_blank", "noopener");
});

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
    else if (location.hash === "#/models") await showModels();
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

// ------------------------------------------------------ rinomina, elimina

function closeMenu() {
  $("menu").hidden = true;
  $("chat-menu").setAttribute("aria-expanded", "false");
}

$("chat-menu").addEventListener("click", (ev) => {
  ev.stopPropagation();
  const open = $("menu").hidden;
  $("menu").hidden = !open;
  $("chat-menu").setAttribute("aria-expanded", open ? "true" : "false");
});
document.addEventListener("click", (ev) => {
  if (!$("menu").hidden && !$("menu").contains(ev.target)) closeMenu();
});

$("m-rename").addEventListener("click", async () => {
  closeMenu();
  const title = prompt("Nuovo titolo della conversazione:", $("title").textContent);
  if (title === null || !title.trim() || !currentId) return;
  try {
    await post("/api/rename", { chat_id: currentId, title: title.trim() });
    $("title").textContent = title.trim();
  } catch (e) {
    if (e.status === 404) chatGone(); else addNote(e.message, true);
  }
});

$("m-delete").addEventListener("click", async () => {
  closeMenu();
  if (!currentId) return;
  const what = activeHere ? " La risposta in corso verrà interrotta." : "";
  if (!confirm("Eliminare questa conversazione anche dal PC?" + what)) return;
  try {
    await post("/api/delete", { chat_id: currentId });
  } catch (e) {
    if (e.status !== 404) { addNote(e.message, true); return; }
  }
  history.replaceState(null, "", location.pathname);
  route();
});

$("back").addEventListener("click", () => { location.hash = ""; });
$("models-btn").addEventListener("click", () => { location.hash = "#/models"; });
$("new-chat").addEventListener("click", () => { location.hash = "#/new"; });
$("refresh").addEventListener("click", route);
window.addEventListener("hashchange", route);
route();

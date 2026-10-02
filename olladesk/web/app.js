// Companion web di OllaDesk: abbinamento e lettura delle conversazioni.
// Nessuna libreria esterna: la pagina funziona anche senza internet.
// L'HTML dei messaggi arriva già reso (ed escapato) dal server con md.py.
"use strict";

const $ = (id) => document.getElementById(id);
const VIEWS = ["loading", "pair", "list", "chat"];

function show(view) {
  for (const v of VIEWS) $("view-" + v).hidden = v !== view;
  $("back").hidden = view !== "chat";
  $("refresh").hidden = view !== "list" && view !== "chat";
  if (view !== "chat") $("title").textContent = "OllaDesk";
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

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString([], { day: "numeric", month: "short" }) + " " +
      d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

// ------------------------------------------------------------ abbinamento

async function pair(code) {
  $("pair-error").hidden = true;
  try {
    await api("/api/pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
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
    const li = el("li");
    const a = el("a");
    a.href = "#/c/" + encodeURIComponent(c.id);
    a.append(el("span", "t", c.title || "Conversazione"));
    a.append(el("span", "m", [c.model, fmtTime(c.updated)].filter(Boolean).join(" · ")));
    li.append(a);
    ul.append(li);
  }
  $("list-empty").hidden = data.chats.length > 0;
  show("list");
}

// ---------------------------------------------------------- conversazione

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
  if (m.thinking_html) {
    const d = el("details", "think");   // richiuso, come sul desktop
    d.append(el("summary", "", "🧠 Pensiero"));
    const t = el("div", "body");
    t.innerHTML = m.thinking_html;
    d.append(t);
    box.append(d);
  }
  const body = el("div", "body");
  body.innerHTML = m.html;
  box.append(body);
  const meta = [fmtTime(m.ts), m.stats].filter(Boolean).join(" · ");
  if (meta) box.append(el("div", "meta", meta));
  return box;
}

async function showChat(id) {
  const chat = await api("/api/chats/" + encodeURIComponent(id));
  $("title").textContent = chat.title || "Conversazione";
  $("chat-model").textContent = chat.model || "";
  const box = $("messages");
  box.replaceChildren(...chat.messages.map(renderMessage));
  show("chat");
  window.scrollTo(0, document.body.scrollHeight);
}

// ----------------------------------------------------------------- router

async function route() {
  netError("");
  const m = location.hash.match(/^#\/c\/(.+)$/);
  try {
    if (m) await showChat(decodeURIComponent(m[1]));
    else await showList();
  } catch (e) {
    if (e instanceof Unauthorized) {
      show("pair");
      $("pair-code").focus();
    } else {
      netError(e.message);
      if ($("view-loading").hidden === false) show("list");
    }
  }
}

$("back").addEventListener("click", () => { location.hash = ""; });
$("refresh").addEventListener("click", route);
window.addEventListener("hashchange", route);

// QR code: http://<pc>:<porta>/#pair=123456. Il codice sta nel frammento,
// che il browser non invia al server, e sparisce subito dalla barra
const qr = location.hash.match(/^#pair=(\d{6})$/);
if (qr) {
  history.replaceState(null, "", location.pathname);
  pair(qr[1]);
} else {
  route();
}

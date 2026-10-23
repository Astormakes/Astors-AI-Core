// AAC 2 panel (docs/v2): one sidebar (cards put their pages in it: chats, lists), the chat, lists, models, and the
// node view (card types left, the grid, the selected card's settings right). Plain web component, HA's theme.

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const md = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\n/g, "<br>");
const tip = (html) => `<span class="qm" tabindex="0">?<span class="tip">${html}</span></span>`;
const W = 180, HEAD = 34, ROW = 20;
const COL = W + 110; // one slot column (v1: a column per step in the flow)
const KIND_COLOR = { text: "#f5a623", audio: "#4caf50", pool: "#03a9f4", piece: "#9c27b0" };
// the Conditional's rules editor: tests and actions with the fields each needs ([field, label, kind])
const TESTS = { contains: "contains", is: "is exactly", starts: "starts with", ends: "ends with", matches: "matches (regex)",
  has_tag: "has the tag", count_more: "more entries than", empty: "is empty", always: "always" };
const ACTIONS = {
  replace: ["Replace text", [["a", "find", "text"], ["b", "with", "text"]]], insert: ["Insert text", [["a", "text", "text"], ["b", "at", ["end", "start"]]]],
  remove: ["Remove text", [["a", "text", "text"]]], remove_entries: ["Remove the entries", []], cut: ["Keep only the part", [["a", "after", "text"], ["b", "before", "text"]]],
  split: ["Split into entries", [["a", "at", "text"]]], join: ["Join into one entry", [["b", "with", "text"]]], case: ["Change case", [["a", "to", ["lower", "upper", "title"]]]],
  trim: ["Trim spaces", []], tag: ["Add a tag", [["a", "tag", "tag"]]], call: ["Call a wired card", [["card", "card", "card"], ["function", "function", "function"], ["args", "with ({text} = the entry)", "json"]]],
  stop: ["Send stop", []], signal: ["Send a signal", [["a", "signal", "text"]]], output: ["Send to output", [["a", "output", "output"]]] };
const KINDS = ["said", "answer", "note", "trailer", "system", "call", "result"];
const DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

const STYLE = `
:host { display: block; height: 100vh; color: var(--primary-text-color); background: var(--primary-background-color);
  font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
* { box-sizing: border-box; }
.root { display: flex; height: 100%; }
nav { width: var(--nav-w, 250px); flex: none; background: var(--sidebar-background-color, var(--card-background-color));
  border-right: 1px solid var(--divider-color); display: flex; flex-direction: column; }
nav .top { padding: 14px 16px 6px; display: flex; align-items: baseline; gap: 8px; }
nav .top b { font-size: 20px; } nav .top span { color: var(--secondary-text-color); font-size: 12px; }
nav .dom { display: flex; gap: 6px; padding: 4px 12px 10px; }
nav .dom select { flex: 1; }
nav .scroll { flex: 1; overflow-y: auto; }
h4 { margin: 14px 16px 4px; font-size: 11px; letter-spacing: .08em; color: var(--secondary-text-color); display: flex; align-items: center; }
h4 button { margin-left: auto; }
.item { padding: 6px 16px 6px 22px; cursor: pointer; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; border-radius: 0 16px 16px 0; margin-right: 8px; }
.item:hover { background: rgba(127,127,127,.1); } .item.on { background: rgba(3,169,244,.15); color: var(--primary-color); }
nav .bottom { border-top: 1px solid var(--divider-color); padding: 6px 0; }
main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
header { display: flex; align-items: center; gap: 8px; padding: 0 16px; min-height: 56px; border-bottom: 1px solid var(--divider-color); flex-wrap: wrap; }
header h2 { margin: 0; font-size: 18px; font-weight: 500; margin-right: auto; }
button, select, input, textarea { font: inherit; color: inherit; }
button { background: none; border: 1px solid var(--divider-color); border-radius: 6px; padding: 4px 10px; cursor: pointer; }
button:hover { border-color: var(--primary-color); } button.primary { background: var(--primary-color); color: #fff; border-color: var(--primary-color); }
button.small { padding: 1px 7px; font-size: 12px; } button.danger { color: var(--error-color, #db4437); }
button:disabled { opacity: .4; cursor: default; }
input, select, textarea { background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 6px; padding: 6px 8px; width: 100%; }
textarea { resize: vertical; }
.content { flex: 1; overflow-y: auto; padding: 16px; }
.hint { color: var(--secondary-text-color); font-size: 12px; }
.qm { display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px; border-radius: 50%; border: 1px solid var(--secondary-text-color);
  font-size: 11px; color: var(--secondary-text-color); position: relative; cursor: help; margin-left: 4px; vertical-align: middle; }
.qm .tip { display: none; position: absolute; top: 20px; right: -10px; z-index: 30; width: 300px; background: var(--card-background-color);
  border: 1px solid var(--divider-color); border-radius: 8px; padding: 10px; font-size: 12px; color: var(--primary-text-color); box-shadow: 0 4px 16px rgba(0,0,0,.25); text-align: left; white-space: normal; }
.qm:hover .tip, .qm:focus .tip { display: block; }
/* chat */
.chat { display: flex; flex-direction: column; height: 100%; }
.log { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
.bubble { max-width: min(80%, 760px); padding: 8px 12px; border-radius: 12px; line-height: 1.45; overflow-wrap: anywhere; }
.bubble.user { align-self: flex-end; background: var(--primary-color); color: #fff; }
.bubble.aac { align-self: flex-start; background: var(--card-background-color); border: 1px solid var(--divider-color); }
.bubble.err { border-color: var(--error-color, #db4437); } .bubble .cut { font-size: 11px; color: var(--secondary-text-color); }
.composer { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--divider-color); }
.composer textarea { min-height: 42px; max-height: 40vh; }
.entry { font-size: 13px; border-left: 3px solid var(--divider-color); padding: 4px 10px; margin: 4px 0; }
.entry .k { font-size: 11px; color: var(--secondary-text-color); }
.entry pre.req { white-space: pre-wrap; margin: 2px 0; font: inherit; }
/* lists */
.lst ul { list-style: none; padding-left: 22px; margin: 0; } .lst > ul { padding-left: 0; }
.lst li { margin: 3px 0; } .row { display: flex; align-items: center; gap: 6px; }
.row .t { cursor: text; flex: 1; } .row .t.done { text-decoration: line-through; color: var(--secondary-text-color); }
.row .acts { opacity: 0; } .row:hover .acts { opacity: 1; }
.fields { padding: 1px 0 3px 26px; display: flex; flex-wrap: wrap; gap: 4px; }
.chip { font-size: 11px; background: rgba(127,127,127,.12); border-radius: 10px; padding: 1px 7px; cursor: pointer; }
.card { background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 10px; padding: 14px; margin-bottom: 14px; }
.models td { padding: 4px 8px; border-bottom: 1px solid var(--divider-color); font-size: 14px; }
/* nodes */
.nodes { display: flex; height: 100%; min-height: 0; }
.palette { width: var(--pal-w, 190px); flex: none; border-right: 1px solid var(--divider-color); display: flex; flex-direction: column; }
.palette .types { flex: 1; overflow-y: auto; padding-bottom: 8px; }
.palette .info { border-top: 1px solid var(--divider-color); padding: 10px; font-size: 12px; max-height: 45%; overflow-y: auto; }
.grid { flex: 1; position: relative; overflow: hidden; background-image: radial-gradient(rgba(127,127,127,.25) 1px, transparent 1px); background-size: 20px 20px; }
.grid svg { width: 100%; height: 100%; display: block; user-select: none; }
.side { width: var(--side-w, 300px); flex: none; border-left: 1px solid var(--divider-color); overflow-y: auto; padding: 12px; }
.side label { display: block; font-size: 12px; color: var(--secondary-text-color); margin: 10px 0 3px; }
.side .check { display: flex; gap: 6px; align-items: center; color: var(--primary-text-color); font-size: 14px; }
.side .check input { width: auto; }
.fn { font-size: 12px; margin: 6px 0; } .fn code { font-size: 12px; }
.node rect.box { fill: var(--card-background-color); stroke: var(--divider-color); stroke-width: 1.2; }
.node.sel rect.box { stroke: var(--primary-color); stroke-width: 2.4; }
.node text { fill: var(--primary-text-color); font-size: 13px; } .node text.sub { fill: var(--secondary-text-color); font-size: 10px; } .node text.port { font-size: 10px; fill: var(--secondary-text-color); }
.dot { cursor: crosshair; }
.wire { fill: none; stroke-width: 2; opacity: .7; } .wire.pool { stroke-dasharray: 4 4; } .wire.sel { stroke-width: 4; opacity: 1; }
.wire.flow { stroke-dasharray: 7 5; animation: flow 1s linear infinite; opacity: 1; }
@keyframes flow { to { stroke-dashoffset: -24; } }
.grip { flex: none; width: 7px; margin: 0 -4px 0 -3px; cursor: col-resize; position: relative; z-index: 5; touch-action: none; }
.grip:hover, .grip.drag { background: var(--divider-color); }
.rule { padding: 8px; margin: 6px 0; font-size: 13px; } .rule .row { display: flex; gap: 4px; align-items: center; }
.rule .rh { font-size: 11px; font-weight: 600; color: var(--secondary-text-color); margin: 8px 0 2px; letter-spacing: .05em; }
.rule .rf { display: flex; gap: 4px; align-items: center; margin: 3px 0; } .rule .rf > span { font-size: 12px; color: var(--secondary-text-color); white-space: nowrap; }
.rule input, .rule select { padding: 4px 6px; font-size: 13px; min-width: 0; }
.ract { border-left: 3px solid var(--divider-color); padding: 2px 0 4px 8px; margin: 4px 0; }
.mons { position: absolute; inset: 0; pointer-events: none; overflow: hidden; }
.mon { position: absolute; transform: translate(-50%, calc(-100% - 8px)); pointer-events: auto; cursor: pointer; max-width: 260px; min-width: 110px;
  padding: 4px 8px; font-size: 11px; line-height: 1.35; border: 1px solid var(--primary-color); background: var(--card-background-color);
  border-radius: 6px; box-shadow: 0 2px 8px rgba(0,0,0,.2); }
.mon.sel { border-width: 2px; } .mon .t { display: flex; gap: 6px; color: var(--secondary-text-color); font-size: 10px; }
.mon .t b { color: var(--primary-color); font-weight: 500; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mon .x { overflow: hidden; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; white-space: pre-wrap; }
.mon.flash { animation: monflash .8s; } @keyframes monflash { from { box-shadow: 0 0 14px var(--primary-color); } }
.hit { fill: none; stroke: transparent; stroke-width: 14; cursor: pointer; }
@media (max-width: 800px) { nav { position: absolute; z-index: 20; height: 100%; transform: translateX(-100%); } .root.navopen nav { transform: none; }
  .palette, .side { width: 170px; } .grip { display: none; } }
`;

class AacPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.view = { kind: "chat", card: null, id: null };
    this.chats = {}; this.chat = null; this.live = {}; this.busy = false; this.expert = ""; this.requests = [];
    this.lists = {}; this.sel = null; this.selType = null; this.selWire = null; this.vp = { x: 80, y: 120, k: 1 };
    // one listener for buttons that are redrawn often (the Audio page's meters)
    this.shadowRoot.addEventListener("click", (e) => {
      const b = e.target.closest?.("[data-say]");
      if (b) this.ws("aac/audio/say", { source: b.dataset.say, text: "This is how I sound." });
    });
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._start();
  }

  set narrow(v) { this._narrow = v; }

  ws(type, data = {}) { return this._hass.callWS({ type, ...data }); }

  async _start() {
    this._applyWidths();
    await this._load();
    this._hass.connection.subscribeMessage((ev) => this._event(ev), { type: "aac/subscribe" });
    const chat = this._cards("chat")[0];
    if (chat) await this._openChats(chat.id);
    this.render();
  }

  async _load() {
    [this.state, this.audio] = await Promise.all([this.ws("aac/state"), this.ws("aac/audio").catch(() => null)]);
  }
  _cards(page) { return (this.state?.domain.cards || []).filter((c) => c.page === page); }
  _card(id) { return (this.state?.domain.cards || []).find((c) => c.id === id); }
  _type(t) { return (this.state?.types || []).find((x) => x.type === t); }

  async _openChats(card) { this.chats[card] = (await this.ws("aac/chat/list", { card })).chats; }

  _event(ev) {
    if (ev.what === "delta" && this.view.kind === "chat" && this.busy) {
      this.live = { text: ev.text };
      const box = this.shadowRoot.querySelector("#live");
      if (box) box.innerHTML = md(ev.text);
    } else if (ev.what === "chat") {
      clearTimeout(this._ct);
      this._ct = setTimeout(async () => {
        await this._openChats(ev.card);
        if (this.view.kind === "chat" && this.view.card === ev.card && this.view.id) await this._getChat();
        this.render();
      }, 150);
    } else if (ev.what === "setup") {
      clearTimeout(this._st);
      this._st = setTimeout(async () => { await this._load(); if (this.view.kind !== "nodes" || !this._dragging) this.render(); }, 150);
    } else if (ev.what === "card" && this.view.card === ev.card && ["lists", "log", "memory", "file"].includes(this.view.kind)) {
      this._loadPage().then(() => this.render());
    } else if (ev.what === "audio" && this.view.kind === "audio") {
      clearTimeout(this._at); this._at = setTimeout(async () => { this.audio = await this.ws("aac/audio"); this.render(); }, 300);
    } else if (ev.what === "level") {
      for (const p of this.shadowRoot.querySelectorAll(`.wire[data-from="${CSS.escape(ev.wire)}"]`)) {
        p.style.strokeWidth = (2 + Math.min(100, ev.level) / 100 * 8).toFixed(1);
        clearTimeout(p._lt); p._lt = setTimeout(() => { p.style.strokeWidth = ""; }, 1200);
      }
    } else if (ev.what === "flow") {
      if (this.view.kind === "nodes") for (const key of this._monitors().filter((k) => k.split("|")[0] === ev.wire)) {
        clearTimeout((this._mt ||= {})[key]); this._mt[key] = setTimeout(() => this._loadMonitor(key, true), 250);
      }
      for (const p of this.shadowRoot.querySelectorAll(`.wire[data-from="${CSS.escape(ev.wire)}"]`)) {
        p.classList.add("flow"); clearTimeout(p._t); p._t = setTimeout(() => p.classList.remove("flow"), 1800);
      }
    }
  }

  async go(kind, card = null, id = null) {
    this.view = { kind, card, id };
    if (kind === "chat" && id) await this._getChat();
    if (kind === "chat" && !id) this.chat = null;
    if (["lists", "log", "memory", "file"].includes(kind)) await this._loadPage();
    if (kind === "audio") { this.audio = await this.ws("aac/audio"); clearInterval(this._live); this._live = setInterval(async () => { if (this.view.kind !== "audio") { clearInterval(this._live); return; } this.audio = await this.ws("aac/audio"); const b = this.shadowRoot.querySelector("#meters"); if (b) b.innerHTML = this._meters(); }, 700); }
    this.render();
  }

  async _getChat() {
    this.chat = (await this.ws("aac/chat/get", { card: this.view.card, chat_id: this.view.id })).chat;
    if (this.expert === "request") this.requests = (await this.ws("aac/chat/request", { chat_id: this.view.id })).requests;
  }
  async _loadLists(card) { this.lists[card] = (await this.ws("aac/card/data", { card })).data?.lists || []; }
  async _loadPage() {
    const card = this.view.card, kind = this.view.kind;
    if (kind === "lists") return this._loadLists(card);
    if (kind === "file") { this.page = { text: (await this.ws("aac/card/call", { card, function: "read" })).result }; return; }
    this.page = (await this.ws("aac/card/data", { card })).data || {};
  }

  // ------------------------------------------------------------------ layout
  render() {
    if (!this.state) { this.shadowRoot.innerHTML = `<style>${STYLE}</style><p style="padding:20px">Loading…</p>`; return; }
    const v = this.view;
    const main = v.kind === "nodes" ? this._nodes() : v.kind === "models" ? this._models() : v.kind === "lists" ? this._listsView()
      : v.kind === "log" ? this._logView() : v.kind === "memory" ? this._memoryView() : v.kind === "file" ? this._fileView()
      : v.kind === "audio" ? this._audioView() : this._chatView();
    this.shadowRoot.innerHTML = `<style>${STYLE}</style><div class="root">${this._nav()}<div class="grip" data-grip="nav" data-target="nav" title="Drag: width of the sidebar (double-click: back to normal)"></div><main>${main}</main></div>`;
    this._bind();
    if (v.kind === "nodes") this._drawGrid();
    if (v.kind === "chat") { const log = this.shadowRoot.querySelector(".log"); if (log) log.scrollTop = log.scrollHeight; }
  }

  _nav() {
    const s = this.state, v = this.view;
    const item = (label, kind, card = null, id = null) =>
      `<div class="item ${v.kind === kind && v.card === card && v.id === id ? "on" : ""}" data-go="${kind}" data-card="${esc(card || "")}" data-id="${esc(id || "")}">${esc(label)}</div>`;
    const chats = this._cards("chat").map((c) => `<h4>${esc(c.title.toUpperCase())}<button class="small" data-newchat="${esc(c.id)}">+</button></h4>
      ${(this.chats[c.id] || []).map((ch) => item(ch.title, "chat", c.id, ch.id)).join("") || '<div class="item hint" style="cursor:default">no chats yet</div>'}`).join("");
    const lists = this._cards("lists").map((c) => `<h4>${esc(c.title.toUpperCase())}</h4>${item("All lists", "lists", c.id)}`).join("");
    const pages = ["log", "memory", "file"].flatMap((pg) => this._cards(pg)).map((c) => item(c.title, c.page, c.id)).join("");
    return `<nav><div class="top"><b>AAC</b><span>v${esc(s.version)}</span></div>
      <div class="dom"><select id="domain">${s.domains.map((d) => `<option value="${esc(d.id)}" ${d.id === s.active ? "selected" : ""}>${esc(d.name)}</option>`).join("")}</select>
        <button class="small" id="clone" title="A copy of this setup to try things out">Clone</button></div>
      <div class="scroll">${chats}${lists}${pages ? `<h4>MORE</h4>${pages}` : ""}</div>
      <div class="bottom">${item("Nodes", "nodes")}${item("Models", "models")}${item("Audio", "audio")}</div></nav>`;
  }

  _header(title, extra = "") { return `<header><h2>${esc(title)}</h2>${extra}</header>`; }

  // ------------------------------------------------------------------ chat
  _chatView() {
    const card = this._card(this.view.card) || this._cards("chat")[0];
    if (!card) return `${this._header("AAC")}<div class="content"><p>No chat card in this domain. Add one in Nodes.</p></div>`;
    this.view.card = card.id;
    const msgs = this.chat?.messages || [];
    const name = (id) => this._card(id)?.title || id;
    const log = this.expert === "request" && this.chat ? (this.requests || []).map((r) => `<div class="entry"><div class="k">${esc(name(r.card))} · ${esc(new Date(r.at).toLocaleTimeString())} · tools: ${esc(r.tools.join(", ") || "none")}</div></div>${r.messages.map((m) => `<div class="entry"><div class="k">${esc(m.role)} · from ${esc([...new Set(m._from || [])].map(name).join(", ") || "?")}</div>${m.tool_calls ? esc(m.tool_calls.map((c) => `${c.function.name}(${c.function.arguments})`).join("; ")) : `<pre class="req">${esc(m.content || "")}</pre>`}</div>`).join("")}`).join("")
        || `<div class="hint" style="margin:auto">No request kept for this chat (kept in memory until HA restarts).</div>`
      : this.expert === "doc" && this.chat ? (this.chat.entries || []).map((e) => `<div class="entry"><div class="k">${esc(e.by)} · ${esc(e.kind)} ${e.tags.map((t) => `#${esc(t)}`).join(" ")}</div>${md(e.text || (e.data?.function ? `${e.data.function}(${JSON.stringify(e.data.args || {})})` : ""))}</div>`).join("")
      : msgs.map((m) => `<div class="bubble ${m.role === "user" ? "user" : "aac"} ${m.error ? "err" : ""}">${md(m.text)}${m.cut ? '<div class="cut">stopped</div>' : ""}</div>`).join("")
        || `<div class="hint" style="margin:auto">${esc(card.title)}: ask anything.</div>`;
    const busy = this.busy ? `<div class="bubble aac" id="live">${md(this.live.text || "…")}</div>` : "";
    return `<div class="chat">${this._header(this.chat?.title || `New chat · ${card.title}`,
      `${this.chat ? `<button class="small" data-mode="" ${!this.expert ? "disabled" : ""}>Chat</button><button class="small" data-mode="doc" title="The document: every entry with its card, kind and tags" ${this.expert === "doc" ? "disabled" : ""}>Document</button><button class="small" data-mode="request" title="What the model got for the newest turn: each message with the cards its text came from, and the tools it had" ${this.expert === "request" ? "disabled" : ""}>Request</button><button class="small danger" id="delchat">Delete</button>` : ""}<button class="small" id="stop" title="Stop everything that is working">Stop</button>`)}
      <div class="log">${log}${busy}</div>
      <div class="composer"><textarea id="input" placeholder="Message AAC…" ${this.busy ? "disabled" : ""}></textarea><button class="primary" id="send" ${this.busy ? "disabled" : ""}>Send</button></div></div>`;
  }

  async _send() {
    const box = this.shadowRoot.querySelector("#input"), text = box.value.trim();
    if (!text || this.busy) return;
    const card = this.view.card;
    this.busy = true; this.live = {};
    this.chat = this.chat || { title: text.slice(0, 48), messages: [] };
    this.chat.messages = [...this.chat.messages, { role: "user", text }];
    this.render();
    try {
      const r = await this.ws("aac/chat/send", { card, text, chat_id: this.view.id });
      this.view.id = r.chat_id;
      await this._openChats(card); await this._getChat();
    } catch (e) { this.chat.messages.push({ role: "aac", text: e.message || String(e), error: true }); }
    this.busy = false;
    this.render();
  }

  // ------------------------------------------------------------------ lists
  _listsView() {
    const card = this.view.card, lists = this.lists[card] || [];
    const tree = (items, list) => `<ul>${items.map((it) => `<li><div class="row">
        <input type="checkbox" style="width:auto" data-done="${esc(it.id)}" data-list="${esc(list)}" ${it.done ? "checked" : ""}>
        <span class="t ${it.done ? "done" : ""}" data-edit="${esc(it.id)}" data-list="${esc(list)}">${esc(it.text)}</span>
        <span class="acts"><button class="small" data-sub="${esc(it.id)}" data-list="${esc(list)}" title="Add a sub-entry">+</button>
          <button class="small" data-addfield="${esc(it.id)}" data-list="${esc(list)}" title="Add a field">field</button>
          <button class="small danger" data-del="${esc(it.id)}" data-list="${esc(list)}">✕</button></span></div>
        ${Object.keys(it.fields || {}).length ? `<div class="fields">${Object.entries(it.fields).map(([k, val]) => `<span class="chip" data-field="${esc(k)}" data-item="${esc(it.id)}" data-list="${esc(list)}">${esc(k)}: ${esc(val)}</span>`).join("")}</div>` : ""}
        ${it.children?.length ? tree(it.children, list) : ""}</li>`).join("")}</ul>`;
    const body = lists.map((li) => `<div class="card lst"><h3 style="margin:0 0 8px;display:flex">${esc(li.name)}
        <button class="small danger" style="margin-left:auto" data-dellist="${esc(li.name)}">Delete list</button></h3>
      ${tree(li.items || [], li.name)}
      <div class="row" style="margin-top:8px"><input data-newitem="${esc(li.name)}" placeholder="New entry…"></div></div>`).join("");
    return `${this._header(this._card(card)?.title || "Lists", `${this._undoRemove ? '<button class="small" id="undoremove">Undo remove</button>' : ""}<button class="small" id="newlist">New list</button>`)}
      <div class="content">${body || '<p class="hint">No lists yet.</p>'}</div>`;
  }

  async _listCall(fn, args) {
    try { await this.ws("aac/card/call", { card: this.view.card, function: fn, args }); }
    catch (e) { alert(e.message || e); }
    await this._loadLists(this.view.card); this.render();
  }

  // ------------------------------------------------------------------ pages of cards: log, memory, file
  _logView() {
    const log = [...(this.page?.log || [])].reverse();
    return `${this._header(this._card(this.view.card)?.title || "Log", tip("What woke the chain, and what came back (quiet: the model had nothing to say)."))}
      <div class="content">${log.map((x) => `<div class="card"><div class="hint">${esc(new Date(x.time).toLocaleString())} · ${esc(x.name)}</div>
        <div style="margin:4px 0">${md(x.text)}</div><div>${x.answer === null || x.answer === undefined ? '<span class="hint">working…</span>' : x.answer ? md(x.answer) : '<span class="hint">quiet</span>'}</div></div>`).join("") || '<p class="hint">Nothing yet.</p>'}</div>`;
  }

  _memoryView() {
    const mem = [...(this.page?.memories || [])].reverse();
    return `${this._header(this._card(this.view.card)?.title || "Memory")}<div class="content">
      <div class="row" style="margin-bottom:12px"><input id="newmem" placeholder="Something to remember…"><button id="addmem">Remember</button></div>
      ${mem.map((m) => `<div class="card row"><span class="t">${esc(m.text)}<div class="hint">${esc(new Date(m.time).toLocaleString())}${m.vector ? " · by meaning" : ""}</div></span>
        <button class="small danger" data-forget="${esc(m.id)}">Forget</button></div>`).join("") || '<p class="hint">No memories yet.</p>'}</div>`;
  }

  _fileView() {
    return `${this._header(this._card(this.view.card)?.title || "File", '<button class="small primary" id="savefile">Save</button>')}
      <div class="content"><textarea id="filetext" style="height:70vh;font-family:monospace">${esc(this.page?.text || "")}</textarea></div>`;
  }

  // ------------------------------------------------------------------ audio (core)
  _meters() {
    return (this.audio?.sources || []).map((src) => `<div class="card"><div class="row"><b>${esc(src.name)}</b><span class="hint">${src.playing ? "speaking" : "listening"} · peak ${src.peak_db ?? "silent"} dB</span>
      <button class="small" data-say="${esc(src.id)}" style="margin-left:auto">Test voice</button></div>
      <div style="display:flex;align-items:flex-end;gap:2px;height:40px;margin-top:6px">${(src.meter || []).map(([lvl, sp]) => `<i style="flex:1;height:${Math.max(2, lvl)}%;background:${sp > 0.3 ? "var(--primary-color)" : "var(--divider-color)"}"></i>`).join("")}</div></div>`).join("")
      || '<p class="hint">No satellite connected (the AAC satellite add-on, or the PC program).</p>';
  }

  _audioView() {
    const a = this.audio || { settings: {} }, st = a.settings || {}, models = this.state.models;
    const sttItems = [...models.map((m) => [m.id, m.name]), ...(a.stt_entities || []).map((e) => [e, `HA: ${e}`])];
    const order = st.stt || [];
    return `${this._header("Audio", tip("The satellites, how AAC hears (speech to text, in this order: the first that answers) and speaks (an HA voice engine). Shared by all domains; the audio cards in Nodes decide what happens with it."))}
      <div class="content"><h3>Satellites</h3><div id="meters">${this._meters()}</div>
      <div class="card"><h3 style="margin-top:0">Speech to text (in order)</h3>
        ${order.map((id, i) => `<div class="row"><span class="t">${esc((sttItems.find((x) => x[0] === id) || [id, id])[1])}</span>
          <button class="small" data-sttup="${i}">↑</button><button class="small danger" data-sttdel="${i}">✕</button></div>`).join("") || '<p class="hint">None yet.</p>'}
        <div class="row" style="margin-top:8px"><select id="sttadd"><option value="">Add …</option>${sttItems.filter((x) => !order.includes(x[0])).map(([id, l]) => `<option value="${esc(id)}">${esc(l)}</option>`).join("")}</select></div>
        <h3>Voice</h3><select id="ttsset"><option value="">—</option>${(a.tts_entities || []).map((e) => `<option ${e === st.tts ? "selected" : ""}>${esc(e)}</option>`).join("")}</select>
        <h3>Volume: <span id="volval">${esc(st.volume ?? 40)}</span> %</h3><input type="range" id="volume" min="0" max="100" step="5" value="${esc(st.volume ?? 40)}">
        <p class="hint">0 = AAC never speaks aloud.</p></div></div>`;
  }

  // ------------------------------------------------------------------ models
  _models() {
    const rows = this.state.models.map((m) => `<tr><td><b>${esc(m.name)}</b><div class="hint">${esc(m.model)} · ${esc(m.base_url)}</div></td>
      <td>${m.up ? "✓" : '<span class="hint">skipped (did not answer)</span>'}</td>
      <td><button class="small" data-editmodel="${esc(m.id)}">Edit</button> <button class="small danger" data-delmodel="${esc(m.id)}">Delete</button></td></tr>`).join("");
    const f = this.modelForm || {};
    return `${this._header("Models", tip("Every model AAC may use, for all domains (the core's model pool). A Pool card picks from these for a Model card. Any OpenAI-compatible server works: llama.cpp, Ollama, vLLM, LM Studio, cloud APIs."))}
      <div class="content"><div class="card models"><table style="width:100%;border-collapse:collapse">${rows || '<tr><td class="hint">No models yet.</td></tr>'}</table></div>
      <div class="card"><h3 style="margin-top:0">${f.id ? "Change model" : "Add a model"}</h3>
        <label class="hint">Server address</label><div class="row"><input id="m_url" value="${esc(f.base_url || "http://192.168.1.x:8080/v1")}"><button class="small" id="probe">Find models</button></div>
        <label class="hint">Model</label>${this.probed ? `<select id="m_model">${this.probed.map((x) => `<option ${x === f.model ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>` : `<input id="m_model" value="${esc(f.model || "")}">`}
        <label class="hint">Name</label><input id="m_name" value="${esc(f.name || "")}">
        <label class="hint">API key (only for servers that need one)</label><input id="m_key" type="password" placeholder="${f.has_key ? "kept" : ""}">
        <div style="margin-top:10px;display:flex;gap:8px"><button class="primary" id="savemodel">Save</button>${f.id ? '<button id="cancelmodel">Cancel</button>' : ""}</div></div></div>`;
  }

  // ------------------------------------------------------------------ nodes
  _nodes() {
    const s = this.state, d = s.domain;
    const cats = s.categories.map((c) => {
      const types = s.types.filter((t) => t.category === c);
      return types.length ? `<h4>${esc(c.toUpperCase())}</h4>${types.map((t) => `<div class="item ${this.selType === t.type ? "on" : ""}" data-pick="${esc(t.type)}">${esc(t.title)}</div>`).join("")}` : "";
    }).join("");
    const t = this.selType && this._type(this.selType);
    const info = t ? `<b>${esc(t.title)}</b><p style="margin:6px 0">${esc(t.help)}</p>${this._fnDocs(t.functions)}<p class="hint">Click in the grid to place it.</p>`
      : '<span class="hint">Pick a card type above, then click in the grid to place it.</span>';
    const slots = ["1", "2", "3"].map((n) => `<button class="small" data-slot="${n}" title="Save the setup to slot ${n} (Shift-click: load it)">${d.slots[n] ? "●" : "○"} ${n}</button>`).join("");
    return `<div style="display:flex;flex-direction:column;height:100%">${this._header("Nodes", `${tip("Cards are placed from the left, wired by dragging from an output dot to an input dot of the same colour. Click a wire and press Delete (or the button) to remove it. A model's tools are the cards wired from its output and back into it.")}
        <button class="small" id="undo" ${d.undo ? "" : "disabled"}>Undo</button><button class="small" id="fit">Fit</button><button class="small" id="export" title="Save this setup as a file (cards, settings, wires, positions; the cards' own data such as lists stays here)">Save file</button><button class="small" id="import" title="Load a setup file into this domain (Undo goes back)">Load file</button><input type="file" id="importfile" accept=".json,application/json" hidden><button class="small" id="arrange" title="Put the cards in slots: a column per step in the flow, a card's tools under it (Undo goes back)">Arrange</button>${slots}<button class="small danger" id="stop">Send stop</button>`)}
      <div class="nodes"><div class="palette"><div class="types">${cats}</div><div class="info">${info}</div></div>
        <div class="grip" data-grip="pal" data-target=".palette" title="Drag: width (double-click: back to normal)"></div>
        <div class="grid" id="grid"><svg id="svg"><g id="world"><g id="wires"></g><g id="cards"></g></g></svg><div class="mons"></div></div>
        <div class="grip" data-grip="side" data-target=".side" data-side="left" title="Drag: width (double-click: back to normal)"></div>
        <div class="side">${this._side()}</div></div></div>`;
  }

  _fnDocs(fns) {
    const list = Object.entries(fns || {});
    if (!list.length) return "";
    return `<div class="hint" style="margin-top:8px">FUNCTIONS ${tip("A model calls these as tools (this card wired from its output and back); a Conditional card calls them as actions.")}</div>`
      + list.map(([name, f]) => `<div class="fn"><code>${esc(name)}(${Object.entries(f.args || {}).map(([a, x]) => `${esc(a)}${x.required ? "" : "?"}`).join(", ")})</code> ${esc(f.help || "")}</div>`).join("");
  }

  _side() {
    const d = this.state.domain;
    if (this.selMon) return this._monitorSide(this.selMon);
    if (this.selWire) {
      const key = `${this.selWire.from}|${this.selWire.to}`, on = this._monitors().includes(key);
      return `<b>Wire</b><p class="hint">${esc(this.selWire.from)} → ${esc(this.selWire.to)}</p>
        <div class="row" style="gap:8px"><button class="small" id="monitor" title="A window above the wire with what passes it, live">${on ? "Stop monitoring" : "Monitor"}</button><button class="small danger" id="unwire">Remove wire</button></div>`;
    }
    const c = this.sel && this._card(this.sel);
    if (!c) {
      const groups = {};
      for (const t of d.tags) (groups[t.card] ||= []).push(t);
      return `<p class="hint">Click a card to set it up.</p><b>Tags in this domain</b>${tip("Cards tag the entries they write (card.tag). Your own tags (user.…) are for Conditional rules and Relays: tag entries with them, then pick them up later.")}
        <div class="row" style="margin:8px 0;gap:6px"><input id="newtag" placeholder="your own tag, e.g. urgent"><button class="small" id="addtag">Add</button></div>
        ${Object.entries(groups).map(([card, tags]) => `<label>${esc(card)}</label>${tags.map((t) => `<div class="hint" style="font-size:12px"><code>${esc(t.tag)}</code> ${esc(t.what)}${card === "You" ? ` <button class="small" data-deltag="${esc(t.tag)}" title="Remove">×</button>` : ""}</div>`).join("")}`).join("")}`;
    }
    const t = this._type(c.type);
    const field = ([key, f]) => {
      const v = c.settings[key] ?? f.default, h = f.help ? tip(esc(f.help)) : "";
      if (f.type === "bool") return `<label class="check"><input type="checkbox" data-set="${esc(key)}" ${v ? "checked" : ""}>${esc(f.title)}${h}</label>`;
      if (f.type === "select") return `<label>${esc(f.title)}${h}</label><select data-set="${esc(key)}">${f.options.map(([o, l]) => `<option value="${esc(o)}" ${o === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
      if (f.type === "longtext") return `<label>${esc(f.title)}${h}</label><textarea rows="5" data-set="${esc(key)}">${esc(v)}</textarea>`;
      if (f.type === "number") return `<label>${esc(f.title)}${h}</label><input type="number" data-set="${esc(key)}" data-num value="${esc(v)}">`;
      if (f.type === "models") return `<label>${esc(f.title)}${h}</label>${this.state.models.map((m) => `<label class="check"><input type="checkbox" data-model="${esc(m.id)}" ${(v || []).includes(m.id) ? "checked" : ""}>${esc(m.name)}</label>`).join("") || '<p class="hint">No models yet (Models in the sidebar).</p>'}`;
      if (f.type === "tag") return `<label>${esc(f.title)}${h}</label><select data-set="${esc(key)}"><option value="">—</option>${d.tags.map((x) => `<option ${x.tag === v ? "selected" : ""}>${esc(x.tag)}</option>`).join("")}</select>`;
      if (f.type === "tags") return `<label>${esc(f.title)}${h}</label>${d.tags.map((x) => `<label class="check"><input type="checkbox" data-tag="${esc(key)}" value="${esc(x.tag)}" ${(v || []).includes(x.tag) ? "checked" : ""}>${esc(x.tag)}</label>`).join("") || '<p class="hint">No tags yet.</p>'}`;
      if (f.type === "list") return `<label>${esc(f.title)}${h}</label><input data-set="${esc(key)}" data-list value="${esc((v || []).join(", "))}">`;
      if (f.type === "access") return `<label>${esc(f.title)}${h}</label><textarea rows="6" data-set="${esc(key)}" data-access placeholder="switch.lamp: full">${esc(Object.entries(v || {}).map(([e, l]) => `${e}: ${l}`).join("\n"))}</textarea>`;
      if (f.type === "model") return `<label>${esc(f.title)}${h}</label><select data-set="${esc(key)}"><option value="">—</option>${this.state.models.map((m) => `<option value="${esc(m.id)}" ${m.id === v ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select>`;
      if (f.type === "source") return `<label>${esc(f.title)}${h}</label><select data-set="${esc(key)}"><option value="${key === "source" && c.type === "microphone" ? "any" : ""}">${c.type === "microphone" ? "any satellite" : "where the line came from"}</option>${(this.audio?.settings?.known || []).map((k) => `<option value="${esc(k.id)}" ${k.id === v ? "selected" : ""}>${esc(k.name)}</option>`).join("")}</select>`;
      if (f.type === "outputs") return `<label>${esc(f.title)}${h}</label><textarea rows="4" data-set="${esc(key)}" data-outputs>${esc((v || []).map((o) => `${o.name}: ${o.description || ""}`).join("\n"))}</textarea>`;
      if (f.type === "schedule") return `<label>${esc(f.title)}${h}</label>${(v || []).map((e, i) => `<div class="card" style="padding:8px" data-entry="${i}">
          <div class="row"><input data-e="name" value="${esc(e.name)}" placeholder="Name"><input data-e="at" value="${esc(e.at)}" style="width:80px" placeholder="HH:MM"></div>
          <div class="row" style="margin:4px 0;flex-wrap:wrap">${DAYS.map((d, n) => `<label class="check" style="font-size:12px"><input type="checkbox" data-day="${n}" ${(e.weekdays || []).includes(n) ? "checked" : ""}>${d}</label>`).join("")}</div>
          <textarea rows="2" data-e="text" placeholder="What to do">${esc(e.text)}</textarea>
          <div class="row"><label class="check"><input type="checkbox" data-e="enabled" ${e.enabled !== false ? "checked" : ""}>on</label><button class="small danger" data-delentry="${i}" style="margin-left:auto">Remove</button></div></div>`).join("")}
        <button class="small" id="addentry">Add an entry</button>`;
      if (f.type === "watches") return `<label>${esc(f.title)}${h}</label>${(v || []).map((w, i) => `<div class="card" style="padding:8px" data-watch="${i}">
          <div class="row"><input data-w="name" value="${esc(w.name)}" placeholder="Name"><input data-w="cooldown" value="${esc(w.cooldown ?? 300)}" style="width:70px" title="Cooldown (s)"></div>
          <input data-w="entity_id" value="${esc(w.entity_id)}" placeholder="entity, e.g. binary_sensor.door" style="margin-top:4px">
          <input data-w="to" value="${esc(w.to || "")}" placeholder="state that counts (empty: any change)" style="margin-top:4px">
          <textarea rows="2" data-w="text" placeholder="What to do" style="margin-top:4px">${esc(w.text)}</textarea>
          <button class="small danger" data-delwatch="${i}">Remove</button></div>`).join("")}
        <button class="small" id="addwatch">Add a watch</button>`;
      if (f.type === "rules") return `<label>${esc(f.title)}${h}</label>${this._rulesEditor(c, v || [])}
        <details style="margin-top:8px"><summary class="hint">As JSON</summary><label>${tip(`One rule: {"name": "for me", "select": {"by": "kind" | "tag" | "card" | "any", "value": "said", "which": "newest" | "all"}, "test": "contains" (is, starts, ends, matches, has_tag, count_more, empty, always), "value": "computer", "actions": [{"do": "replace", "a": "x", "b": "y"}, {"do": "tag", "a": "addressed"}, {"do": "call", "card": "lists", "function": "add_items", "args": {"list": "Shopping", "items": ["{text}"]}}, {"do": "output", "a": "yes"}, {"do": "stop"}], "go_on": false}`)}</label>
        <textarea rows="10" data-set="${esc(key)}" data-json style="font-family:monospace;font-size:12px">${esc(JSON.stringify(v || [], null, 1))}</textarea></details>`;
      return `<label>${esc(f.title)}${h}</label><input data-set="${esc(key)}" value="${esc(v)}">`;
    };
    return `<label>Name</label><input id="title" value="${esc(c.title)}">
      <p class="hint" style="margin-top:8px">${esc(t?.help || "")}</p>
      ${Object.entries(t?.settings || {}).map(field).join("")}
      ${this._fnDocs(t?.functions)}
      ${Object.keys(t?.values || {}).length ? `<div class="hint" style="margin-top:8px">VALUES (for templates)${Object.entries(t.values).map(([n, h]) => `<div><code>{{ ${esc(/^[A-Za-z_]\w*$/.test(c.id) ? c.id : `cards['${c.id}']`)}.${esc(n)} }}</code> ${esc(h)}</div>`).join("")}</div>` : ""}
      ${Object.keys(c.tags).length ? `<div class="hint" style="margin-top:8px">TAGS ${Object.keys(c.tags).map((x) => `<code>${esc(x)}</code>`).join(" ")}</div>` : ""}
      <div style="margin-top:14px;display:flex;gap:8px"><button class="danger" id="remove">Remove card</button></div>
      <p class="hint">Fields save when you leave them.</p>`;
  }

  _rulesEditor(c, rules) {
    const d = this.state.domain, opt = (v, cur, label = v) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(label)}</option>`;
    const outputs = Object.keys(c.outputs), cards = d.cards.filter((x) => x.id !== c.id && Object.keys(this._type(x.type)?.functions || {}).length);
    const field = (act, [k, label, kind]) => {
      const v = act[k] ?? "";
      let input;
      if (Array.isArray(kind)) input = `<select data-a="${k}">${kind.map((o) => opt(o, v || kind[0])).join("")}</select>`;
      else if (kind === "output") input = `<select data-a="${k}">${outputs.map((o) => opt(o, v)).join("")}</select>`;
      else if (kind === "card") input = `<select data-a="${k}" data-rerender><option value="">—</option>${cards.map((x) => opt(x.id, v, x.title)).join("")}</select>`;
      else if (kind === "function") input = `<select data-a="${k}">${Object.keys(this._type(this._card(act.card)?.type)?.functions || {}).map((f) => opt(f, v)).join("")}</select>`;
      else if (kind === "json") input = `<input data-a="${k}" data-jsonarg value="${esc(typeof v === "string" ? v : JSON.stringify(v || {}))}" placeholder='{"list": "Shopping", "items": ["{text}"]}'>`;
      else input = `<input data-a="${k}" value="${esc(v)}" ${kind === "tag" ? 'list="aac-tags"' : ""}>`;
      return `<div class="rf"><span>${esc(label)}</span>${input}</div>`;
    };
    const action = (act, j) => `<div class="ract" data-act="${j}"><div class="row"><select data-a="do" data-rerender>${Object.entries(ACTIONS).map(([k, [l]]) => opt(k, act.do, l)).join("")}</select>
        <button class="small" data-delact="${j}" title="Remove this action">×</button></div>${(ACTIONS[act.do]?.[1] || []).map((f) => field(act, f)).join("")}</div>`;
    const sel = (r) => r.select || {};
    const valueList = { kind: KINDS, tag: d.tags.map((t) => t.tag), card: d.cards.map((x) => x.id), any: [] };
    return `<datalist id="aac-tags">${d.tags.map((t) => `<option value="${esc(t.tag)}">`).join("")}</datalist>
      ${rules.map((r, i) => `<div class="rule card" data-rule="${i}">
        <div class="row"><b class="hint">${i + 1}</b><input data-r="name" value="${esc(r.name || "")}" placeholder="Name of the rule">
          <button class="small" data-moverule="${i}" data-dir="-1" ${i ? "" : "disabled"} title="Up">↑</button><button class="small" data-moverule="${i}" data-dir="1" ${i < rules.length - 1 ? "" : "disabled"} title="Down">↓</button><button class="small danger" data-delrule="${i}" title="Remove the rule">×</button></div>
        <div class="rh">IF</div>
        <div class="rf"><span>look at the</span><select data-r="which">${opt("newest", sel(r).which || "newest", "newest")}${opt("all", sel(r).which, "all")}</select>
          <span>entry by</span><select data-r="by" data-rerender>${["kind", "tag", "card", "any"].map((b) => opt(b, sel(r).by || "kind", b === "any" ? "any entry" : b)).join("")}</select></div>
        ${(sel(r).by || "kind") !== "any" ? `<div class="rf"><span>=</span><input data-r="svalue" list="aac-sv-${i}" value="${esc(sel(r).value ?? "said")}"><datalist id="aac-sv-${i}">${(valueList[sel(r).by || "kind"] || []).map((x) => `<option value="${esc(x)}">`).join("")}</datalist></div>` : ""}
        <div class="rf"><select data-r="test" data-rerender>${Object.entries(TESTS).map(([k, l]) => opt(k, r.test || "always", l)).join("")}</select>
          ${["always", "empty"].includes(r.test || "always") ? "" : `<input data-r="value" value="${esc(r.value ?? "")}" ${r.test === "has_tag" ? 'list="aac-tags"' : ""}>`}</div>
        <div class="rh">THEN</div>
        ${(r.actions || []).map(action).join("")}
        <button class="small" data-addact="${i}">Add an action</button>
        <label class="check" style="margin-top:6px"><input type="checkbox" data-r="go_on" ${r.go_on ? "checked" : ""}>then go on checking the next rules</label></div>`).join("")}
      <button class="small" id="addrule">Add a rule</button>`;
  }

  _collectRules() {
    return [...this.shadowRoot.querySelectorAll("[data-rule]")].map((box) => {
      const get = (k) => box.querySelector(`[data-r="${k}"]`);
      const rule = { name: get("name").value, select: { by: get("by").value, value: get("svalue")?.value ?? "", which: get("which").value },
        test: get("test").value, value: get("value")?.value ?? "", go_on: get("go_on").checked, actions: [] };
      for (const a of box.querySelectorAll("[data-act]")) {
        const act = {};
        a.querySelectorAll("[data-a]").forEach((el) => { act[el.dataset.a] = el.value; });
        if (act.args !== undefined) { try { act.args = JSON.parse(act.args || "{}"); } catch (err) { act.args = {}; } }
        rule.actions.push(act);
      }
      return rule;
    });
  }

  _drawGrid() {
    const d = this.state.domain, pos = (id) => d.positions[id] || { x: 0, y: 0 };
    const port = (c, name, side) => {
      const p = pos(c.id), names = Object.keys(side === "out" ? c.outputs : c.inputs);
      return { x: p.x + (side === "out" ? W : 0), y: p.y + HEAD + ROW * names.indexOf(name) + ROW / 2 };
    };
    const height = (c) => this._height(c);
    this.shadowRoot.querySelector("#cards").innerHTML = d.cards.map((c) => {
      const p = pos(c.id), ins = Object.entries(c.inputs), outs = Object.entries(c.outputs);
      return `<g class="node ${this.sel === c.id ? "sel" : ""}" data-node="${esc(c.id)}" transform="translate(${p.x},${p.y})">
        <rect class="box" width="${W}" height="${height(c)}" rx="8"></rect>
        <text x="10" y="18">${esc(c.title)}</text><text class="sub" x="10" y="30">${esc(this._type(c.type)?.title || c.type)}</text>
        ${ins.map(([n, k], i) => `<circle class="dot" data-in="${esc(n)}" data-kind="${k}" cx="0" cy="${HEAD + ROW * i + ROW / 2}" r="6" fill="${KIND_COLOR[k] || "#999"}"></circle><text class="port" x="10" y="${HEAD + ROW * i + ROW / 2 + 3}">${esc(n)}</text>`).join("")}
        ${outs.map(([n, k], i) => `${c.square.includes(n) ? `<rect class="dot" data-out="${esc(n)}" data-kind="${k}" x="${W - 6}" y="${HEAD + ROW * i + ROW / 2 - 6}" width="12" height="12" fill="${KIND_COLOR[k] || "#999"}"></rect>`
          : `<circle class="dot" data-out="${esc(n)}" data-kind="${k}" cx="${W}" cy="${HEAD + ROW * i + ROW / 2}" r="6" fill="${KIND_COLOR[k] || "#999"}"></circle>`}<text class="port" x="${W - 10}" y="${HEAD + ROW * i + ROW / 2 + 3}" text-anchor="end">${esc(n)}</text>`).join("")}
      </g>`;
    }).join("");
    const wires = this._wireList(), routes = this._routes(wires, port);
    this.shadowRoot.querySelector("#wires").innerHTML = wires.map((w) => {
      const path = routes[w.key], sel = this.selWire && `${this.selWire.from}|${this.selWire.to}` === w.key;
      return `<path class="wire ${w.kind === "pool" ? "pool" : ""} ${w.loop ? "loop" : ""} ${sel ? "sel" : ""}" data-from="${esc(`${w.a.id}.${w.ap}`)}" d="${path}" stroke="${KIND_COLOR[w.kind] || "#999"}"></path>
        <path class="hit" data-wire="${esc(w.key)}" d="${path}"><title>${esc(w.a.title)}.${esc(w.ap)} → ${esc(w.b.title)}.${esc(w.bp)} (${esc(w.kind)})</title></path>`;
    }).join("");
    this._view();
  }

  // ------------------------------------------------------------------ layout (v1's slots and wire lanes)
  _height(c) { return HEAD + ROW * Math.max(Object.keys(c.inputs).length, Object.keys(c.outputs).length, 1) + 8; }

  _wireList() {
    const byId = Object.fromEntries(this.state.domain.cards.map((c) => [c.id, c]));
    const wires = this.state.domain.wires.map((w) => {
      const [a, ap] = w.from.split("."), [b, bp] = w.to.split(".");
      return byId[a] && byId[b] ? { a: byId[a], ap, b: byId[b], bp, kind: byId[a].outputs[ap], key: `${w.from}|${w.to}` } : null;
    }).filter(Boolean);
    // a loop: wired both ways between two cards (a card and its tool); the host is the card with more loops (then more wires)
    const has = new Set(wires.map((w) => `${w.a.id}>${w.b.id}`)), partners = {}, degree = {};
    for (const w of wires) { degree[w.a.id] = (degree[w.a.id] || 0) + 1; degree[w.b.id] = (degree[w.b.id] || 0) + 1; }
    for (const w of wires) if (w.a.id !== w.b.id && has.has(`${w.b.id}>${w.a.id}`)) (partners[w.a.id] ||= new Set()).add(w.b.id);
    const score = (id) => (partners[id]?.size || 0) * 1000 + (degree[id] || 0);
    for (const w of wires) {
      if (w.a.id === w.b.id || !has.has(`${w.b.id}>${w.a.id}`)) continue;
      const aHosts = score(w.a.id) > score(w.b.id) || (score(w.a.id) === score(w.b.id) && w.a.id < w.b.id);
      Object.assign(w, { loop: true, host: aHosts ? w.a.id : w.b.id, tool: aHosts ? w.b.id : w.a.id });
    }
    // going back: into a card's answers/status input (the reply), or closing a circle
    for (const w of wires) if (!w.loop && ["answers", "status"].includes(w.bp)) w.back = true;
    const next = {};
    for (const w of wires) if (!w.loop && !w.back) (next[w.a.id] ||= []).push(w);
    const state = {};
    const visit = (id) => { state[id] = 1; for (const w of next[id] || []) { if (state[w.b.id] === 1) w.back = true; else if (!state[w.b.id]) visit(w.b.id); } state[id] = 2; };
    for (const c of [...this.state.domain.cards].sort((x, y) => Object.keys(x.inputs).length - Object.keys(y.inputs).length)) if (!state[c.id]) visit(c.id);
    return wires;
  }

  _arrange() {
    // Slots (v1): a column per step in the flow; a card's tools stacked under it in its column; separate chains below
    // each other, unwired cards in a strip at the bottom; rows ordered so wires cross less
    const cards = this.state.domain.cards, byId = Object.fromEntries(cards.map((c) => [c.id, c]));
    const wires = this._wireList(), hangs = new Set(wires.filter((w) => w.loop).map((w) => w.tool)), rank = {};
    for (const w of wires) if (w.loop) hangs.delete(w.host);
    const forward = wires.filter((w) => !w.back && !w.loop && !hangs.has(w.a.id) && !hangs.has(w.b.id) && w.a.id !== w.b.id);
    for (let i = 0; i < cards.length; i++) {
      let changed = false;
      for (const w of forward) if ((rank[w.b.id] ?? 0) < (rank[w.a.id] ?? 0) + 1) { rank[w.b.id] = (rank[w.a.id] ?? 0) + 1; changed = true; }
      if (!changed) break;
    }
    for (let i = 0; i < 3; i++) // a card that only feeds others sits right before the first of them
      for (const c of cards) {
        if (forward.some((w) => w.b.id === c.id)) continue;
        const nx = forward.filter((w) => w.a.id === c.id).map((w) => rank[w.b.id] ?? 0);
        if (nx.length) rank[c.id] = Math.min(...nx) - 1;
      }
    const wired = new Set(wires.flatMap((w) => [w.a.id, w.b.id])), comp = {}, link = {}, comps = [];
    for (const w of wires) if (!w.loop) { (link[w.a.id] ||= []).push(w.b.id); (link[w.b.id] ||= []).push(w.a.id); }
    for (const c of cards) {
      if (!wired.has(c.id) || hangs.has(c.id) || comp[c.id] !== undefined) continue;
      const todo = [c.id], members = [];
      comp[c.id] = comps.length;
      while (todo.length) { const id = todo.pop(); members.push(id); for (const o of link[id] || []) if (comp[o] === undefined && !hangs.has(o)) { comp[o] = comps.length; todo.push(o); } }
      comps.push(members);
    }
    comps.sort((x, y) => (y.some((id) => byId[id].type === "model") - x.some((id) => byId[id].type === "model")) || y.length - x.length);
    const GAP = 30, at = {};
    const toolsOf = (id) => [...new Set(wires.filter((w) => w.loop && w.host === id).map((w) => w.tool))];
    const slotH = (id) => toolsOf(id).reduce((h, t) => h + GAP + this._height(byId[t]), this._height(byId[id]));
    let top = 0;
    for (const members of comps) {
      const base = Math.min(...members.map((id) => rank[id] ?? 0)), cols = {};
      for (const id of members) (cols[(rank[id] ?? 0) - base] ||= []).push(id);
      const order = Object.keys(cols).map(Number).sort((x, y) => x - y), yOf = {};
      const lay = () => { for (const c of order) { let y = 0; for (const id of cols[c]) { yOf[id] = y; y += slotH(id) + GAP; } } };
      lay();
      for (let pass = 0; pass < 6; pass++) // barycenter sweeps, left to right and back
        for (const c of pass % 2 ? [...order].reverse() : order) {
          const near = (id) => {
            const ys = forward.filter((w) => (pass % 2 ? w.a.id === id : w.b.id === id)).map((w) => yOf[pass % 2 ? w.b.id : w.a.id]).filter((v) => v !== undefined);
            return ys.length ? ys.reduce((t, v) => t + v, 0) / ys.length : yOf[id];
          };
          cols[c].sort((x, y) => near(x) - near(y));
          lay();
        }
      let height = 0;
      for (const c of order) {
        let y = top;
        for (const id of cols[c]) {
          at[id] = { x: c * COL, y };
          let yy = y + this._height(byId[id]) + GAP;
          for (const t of toolsOf(id)) if (!at[t]) { at[t] = { x: c * COL, y: yy }; yy += this._height(byId[t]) + GAP; }
          y += slotH(id) + GAP;
        }
        height = Math.max(height, y - top);
      }
      top += height + 90;
    }
    let parked = 0;
    for (const c of cards) if (!at[c.id]) at[c.id] = { x: (parked++) * COL, y: top };
    return at;
  }

  _snap(id) {
    // a dropped card goes into the nearest column, on the 20 px grid, below any card it would cover there
    const d = this.state.domain, p = d.positions[id], c = this._card(id);
    if (!p || !c) return;
    p.x = Math.round(p.x / COL) * COL; p.y = Math.round(p.y / 20) * 20;
    for (let tries = 0; tries < 50; tries++) {
      const hit = d.cards.find((o) => o.id !== id && d.positions[o.id] && Math.abs(d.positions[o.id].x - p.x) < W
        && d.positions[o.id].y < p.y + this._height(c) + 10 && p.y < d.positions[o.id].y + this._height(o) + 10);
      if (!hit) break;
      p.y = Math.round((d.positions[hit.id].y + this._height(hit) + 20) / 20) * 20;
    }
  }

  _routes(wires, port) {
    // Wire paths (v1: lines should not cross cards or each other): a short wire forward is a curve; a loop between a
    // card and the tools stacked under it runs in a side lane (the deeper the tool, the further out); a wire going
    // back or far runs below the cards between its ends, in a lane of its own
    const d = this.state.domain, pos = (id) => d.positions[id] || { x: 0, y: 0 };
    const box = (c) => { const p = pos(c.id); return { x: p.x + W / 2, top: p.y, bottom: p.y + this._height(c) }; };
    const chain = {}, near = {};
    for (const w of wires) { (near[w.a.id] ||= []).push(w.b.id); (near[w.b.id] ||= []).push(w.a.id); }
    let cid = 0;
    for (const c of d.cards) {
      if (chain[c.id] !== undefined || !near[c.id]) continue;
      const todo = [c.id]; chain[c.id] = cid;
      while (todo.length) for (const o of near[todo.pop()] || []) if (chain[o] === undefined) { chain[o] = cid; todo.push(o); }
      cid++;
    }
    const order = {};
    for (const w of wires) if (w.loop) (order[w.host] ||= new Set()).add(w.tool);
    for (const [m, set] of Object.entries(order)) order[m] = [...set].sort((a, b) => pos(a).y - pos(b).y);
    const path = (pts) => pts.map((q, k) => `${k ? "L" : "M"}${Math.round(q[0])},${Math.round(q[1])}`).join(" ");
    const out = {}, below = [];
    for (const w of wires) {
      const o = port(w.a, w.ap, "out"), i = port(w.b, w.bp, "in");
      if (w.loop && Math.abs(pos(w.a.id).x - pos(w.b.id).x) < 4) { // stacked under its host
        const k = order[w.host].indexOf(w.tool), t = box(w.a.id === w.tool ? w.a : w.b);
        if (w.b.id === w.tool) { // host → tool: down the right side, in above the tool
          const lane = o.x + 18 + k * 7;
          out[w.key] = path([[o.x, o.y], [lane, o.y], [lane, t.top - 9], [i.x - 12, t.top - 9], [i.x - 12, i.y], [i.x, i.y]]);
        } else { // tool → host: out below the tool, up the left side
          const lane = i.x - 18 - k * 7;
          out[w.key] = path([[o.x, o.y], [o.x + 8, o.y], [o.x + 8, t.bottom + 9], [lane, t.bottom + 9], [lane, i.y], [i.x, i.y]]);
        }
        continue;
      }
      if (i.x > o.x + 20 && i.x - o.x < 3.5 * COL) {
        const bend = Math.max(40, Math.abs(i.x - o.x) / 2);
        out[w.key] = `M${o.x},${o.y} C${o.x + bend},${o.y} ${i.x - bend},${i.y} ${i.x},${i.y}`;
        continue;
      }
      below.push({ o, i, w });
    }
    below.sort((p, q) => Math.abs(p.i.x - p.o.x) - Math.abs(q.i.x - q.o.x)); // short ones inside, long ones outside
    const taken = [];
    for (const { o, i, w } of below) {
      const lo = Math.min(o.x, i.x) - W, hi = Math.max(o.x, i.x) + W;
      let floor = Math.max(o.y, i.y);
      for (const c of d.cards) {
        if (chain[c.id] !== chain[w.a.id]) continue;
        const b = box(c);
        if (b.x > lo && b.x < hi) floor = Math.max(floor, b.bottom);
      }
      for (const t of taken) if (t.hi > lo && t.lo < hi && t.chain === chain[w.a.id]) floor = Math.max(floor, t.y);
      const y = floor + 22, k = taken.length % 6;
      taken.push({ lo, hi, y, chain: chain[w.a.id] });
      const loopsA = (order[w.a.id] || []).length, loopsB = (order[w.b.id] || []).length;
      const ox = o.x + 14 + k * 5 + (loopsA ? 18 + loopsA * 7 : 0), ix = i.x - 14 - k * 5 - (loopsB ? 18 + loopsB * 7 : 0);
      out[w.key] = path([[o.x, o.y], [ox, o.y], [ox, y], [ix, y], [ix, i.y], [i.x, i.y]]);
    }
    return out;
  }

  async _doArrange() {
    this.state.domain.positions = this._arrange();
    await this.ws("aac/positions", { positions: this.state.domain.positions, undoable: true });
    await this._load(); this.render(); this._fit();
  }

  // ------------------------------------------------------------------ resizable panels (v1: drag the edge, double-click resets)
  _widths() { try { return JSON.parse(localStorage.getItem("aac.widths") || "{}"); } catch (err) { return {}; } }

  _applyWidths() { for (const [k, v] of Object.entries(this._widths())) if (v) this.style.setProperty(`--${k}-w`, `${v}px`); }

  _grips() {
    for (const grip of this.shadowRoot.querySelectorAll("[data-grip]")) {
      const key = grip.dataset.grip, el = this.shadowRoot.querySelector(grip.dataset.target), sign = grip.dataset.side === "left" ? -1 : 1;
      const save = (v) => { const all = this._widths(); all[key] = v; try { localStorage.setItem("aac.widths", JSON.stringify(all)); } catch (err) { /* private window */ } };
      grip.addEventListener("dblclick", () => { this.style.removeProperty(`--${key}-w`); save(0); });
      grip.addEventListener("pointerdown", (ev) => {
        ev.preventDefault();
        grip.setPointerCapture(ev.pointerId); grip.classList.add("drag");
        const x0 = ev.clientX, w0 = el.getBoundingClientRect().width;
        let w = w0;
        const move = (e) => { w = Math.max(140, Math.min(700, w0 + sign * (e.clientX - x0))); this.style.setProperty(`--${key}-w`, `${w}px`); };
        const up = () => { grip.classList.remove("drag"); grip.removeEventListener("pointermove", move); save(Math.round(w)); };
        grip.addEventListener("pointermove", move);
        grip.addEventListener("pointerup", up, { once: true });
        grip.addEventListener("pointercancel", up, { once: true });
      });
    }
  }

  _view() { const g = this.shadowRoot.querySelector("#world"); if (g) g.setAttribute("transform", `translate(${this.vp.x},${this.vp.y}) scale(${this.vp.k})`); this._layoutMonitors(); }

  // ------------------------------------------------------------------ wire monitors (v1: a window above a wire with what passes it, live)
  _monitors() { try { return JSON.parse(localStorage.getItem(`aac.monitors.${this.state.domain.id}`) || "[]"); } catch (err) { return []; } }

  _setMonitors(list) { try { localStorage.setItem(`aac.monitors.${this.state.domain.id}`, JSON.stringify(list)); } catch (err) { /* private window */ } }

  async _loadMonitor(key, flash = false) {
    try { (this.monData ||= {})[key] = await this.ws("aac/monitor", { wire: key.split("|")[0] }); } catch (err) { return; }
    this._layoutMonitors();
    if (flash) { const el = this.shadowRoot.querySelector(`[data-mon="${CSS.escape(key)}"]`); el?.classList.remove("flash"); void el?.offsetWidth; el?.classList.add("flash"); }
    if (this.selMon === key && !this.shadowRoot.querySelector(".side")?.contains(this.shadowRoot.activeElement)) {
      const side = this.shadowRoot.querySelector(".side");
      if (side) { side.innerHTML = this._side(); this._bindMonitorSide(); }
    }
  }

  _layoutMonitors() {
    const box = this.shadowRoot.querySelector(".mons");
    if (!box) return;
    const wires = Object.fromEntries(this._wireList().map((w) => [w.key, w])), keys = this._monitors().filter((k) => wires[k]);
    for (const key of keys) {
      const w = wires[key], path = this.shadowRoot.querySelector(`.hit[data-wire="${CSS.escape(key)}"]`);
      if (!path) continue;
      const mid = path.getPointAtLength(path.getTotalLength() / 2); // on the wire as drawn (lanes included)
      const x = mid.x * this.vp.k + this.vp.x, y = mid.y * this.vp.k + this.vp.y;
      let el = box.querySelector(`[data-mon="${CSS.escape(key)}"]`);
      if (!el) {
        el = document.createElement("div"); el.className = "mon"; el.dataset.mon = key;
        el.addEventListener("pointerdown", (e) => e.stopPropagation());
        el.addEventListener("click", () => { this.selMon = key; this.sel = null; this.selWire = null; this.render(); });
        box.appendChild(el);
        if (!this.monData?.[key]) this._loadMonitor(key);
      }
      const m = this.monData?.[key] || {}, last = (m.records || []).at(-1);
      el.classList.toggle("sel", this.selMon === key);
      el.innerHTML = `<div class="t"><b>${esc(w.a.title)} › ${esc(w.b.title)}</b><span>${(m.records || []).length}</span></div>
        <div class="x">${last ? esc(last.text || "(no text)") : '<span class="hint">waiting for something to pass…</span>'}</div>`;
      el.style.left = `${x}px`; el.style.top = `${y}px`;
    }
    for (const el of box.querySelectorAll("[data-mon]")) if (!keys.includes(el.dataset.mon)) el.remove();
  }

  _monitorSide(key) {
    const m = this.monData?.[key] || {}, msg = m.message, [from, to] = key.split("|");
    const name = (id) => this._card(String(id || "").split(".")[0])?.title || id;
    const recs = (m.records || []).slice().reverse().map((r) => `<div class="entry"><div class="k">${esc(new Date(r.time * 1000).toLocaleTimeString())}</div>${esc(r.text)}</div>`).join("");
    const entries = (msg?.entries || []).map((e) => `<details class="entry"><summary><b>${esc(name(e.by))}</b> · ${esc(e.kind)} ${e.tags.map((t) => `#${esc(t)}`).join(" ")} <span class="hint">${esc(e.text.replace(/\s+/g, " ").slice(0, 60))}</span></summary>
        <pre class="req">${esc(e.text)}${Object.keys(e.data || {}).length ? `\n\n${esc(JSON.stringify(e.data, null, 1))}` : ""}</pre></details>`).join("");
    return `<b>Monitor</b><p class="hint">${esc(name(from))}.${esc(from.split(".")[1])} › ${esc(name(to))}.${esc(to.split(".")[1])} · live</p>
      <button class="small" data-monoff="${esc(key)}">Stop monitoring</button>
      ${msg?.entries ? `<label>NEWEST MESSAGE · ${esc(new Date(msg.time * 1000).toLocaleTimeString())}</label><p class="hint">Way: ${esc((msg.way || []).map(name).join(" › "))}</p>${entries}`
        : msg?.piece ? `<label>NEWEST</label><p>${esc(msg.piece)} from ${esc(msg.source)}</p>` : ""}
      <label>PASSED LATELY</label>${recs || '<p class="hint">Nothing yet (kept 10 minutes).</p>'}`;
  }

  _bindMonitorSide() {
    for (const el of this.shadowRoot.querySelectorAll("[data-monoff]")) el.addEventListener("click", () => {
      this._setMonitors(this._monitors().filter((k) => k !== el.dataset.monoff)); this.selMon = null; this.render();
    });
  }

  _world(e) { const r = this.shadowRoot.querySelector("#svg").getBoundingClientRect(); return { x: (e.clientX - r.left - this.vp.x) / this.vp.k, y: (e.clientY - r.top - this.vp.y) / this.vp.k }; }

  _fit() {
    const d = this.state.domain, svg = this.shadowRoot.querySelector("#svg"), placed = d.cards.filter((c) => d.positions[c.id]);
    if (!placed.length || !svg) return;
    const xs = placed.map((c) => d.positions[c.id].x), ys = placed.map((c) => d.positions[c.id].y), r = svg.getBoundingClientRect();
    const x0 = Math.min(...xs) - 90, x1 = Math.max(...xs) + W + 90; // room for the side lanes
    const y0 = Math.min(...ys) - 40, y1 = Math.max(...placed.map((c) => d.positions[c.id].y + this._height(c))) + 120; // and the lanes below
    const k = Math.min(1.2, r.width / (x1 - x0), r.height / (y1 - y0));
    this.vp = { k, x: (r.width - (x1 - x0) * k) / 2 - x0 * k, y: (r.height - (y1 - y0) * k) / 2 - y0 * k };
    this._view();
  }

  async _savePositions() { await this.ws("aac/positions", { positions: this.state.domain.positions }); }

  _gridEvents() {
    const svg = this.shadowRoot.querySelector("#svg");
    if (!svg) return;
    let drag = null;
    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = svg.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top, k = Math.min(2.5, Math.max(0.25, this.vp.k * (e.deltaY < 0 ? 1.1 : 0.9)));
      this.vp = { k, x: mx - (mx - this.vp.x) * k / this.vp.k, y: my - (my - this.vp.y) * k / this.vp.k };
      this._view();
    }, { passive: false });
    svg.addEventListener("pointerdown", (e) => {
      const node = e.target.closest(".node"), out = e.target.dataset?.out, hit = e.target.dataset?.wire;
      svg.setPointerCapture(e.pointerId);
      if (node && out) { drag = { kind: "wire", from: `${node.dataset.node}.${out}`, type: e.target.dataset.kind }; return; }
      if (hit) { const [from, to] = hit.split("|"); this.selWire = { from, to }; this.sel = null; this.selMon = null; this.render(); return; }
      if (node) {
        const id = node.dataset.node, p = this.state.domain.positions[id] || (this.state.domain.positions[id] = { x: 0, y: 0 }), w = this._world(e);
        drag = { kind: "move", id, dx: w.x - p.x, dy: w.y - p.y, moved: false };
        return;
      }
      if (this.selType) { const w = this._world(e); this._place(this.selType, w); return; }
      drag = { kind: "pan", x: e.clientX - this.vp.x, y: e.clientY - this.vp.y };
    });
    svg.addEventListener("pointermove", (e) => {
      if (!drag) return;
      if (drag.kind === "pan") { this.vp.x = e.clientX - drag.x; this.vp.y = e.clientY - drag.y; this._view(); }
      if (drag.kind === "move") {
        const w = this._world(e), p = this.state.domain.positions[drag.id];
        p.x = Math.round((w.x - drag.dx) / 20) * 20; p.y = Math.round((w.y - drag.dy) / 20) * 20; drag.moved = true; this._dragging = true;
        this._drawGrid();
      }
      if (drag.kind === "wire") {
        const w = this._world(e), [a, ap] = drag.from.split("."), c = this._card(a), p = this.state.domain.positions[a] || { x: 0, y: 0 };
        const oy = p.y + HEAD + ROW * Object.keys(c.outputs).indexOf(ap) + ROW / 2;
        let line = this.shadowRoot.querySelector("#tmpwire");
        if (!line) { line = document.createElementNS("http://www.w3.org/2000/svg", "path"); line.id = "tmpwire"; line.setAttribute("class", "wire"); line.setAttribute("stroke", KIND_COLOR[drag.type] || "#999"); this.shadowRoot.querySelector("#wires").appendChild(line); }
        line.setAttribute("d", `M${p.x + W},${oy} L${w.x},${w.y}`);
      }
    });
    svg.addEventListener("pointerup", async (e) => {
      const d = drag; drag = null; this._dragging = false;
      if (!d) return;
      if (d.kind === "move") {
        if (d.moved) { this._snap(d.id); this._drawGrid(); await this._savePositions(); }
        else { this.sel = d.id; this.selWire = null; this.selMon = null; this.render(); }
      }
      if (d.kind === "wire") {
        this.shadowRoot.querySelector("#tmpwire")?.remove();
        const el = this.shadowRoot.elementFromPoint(e.clientX, e.clientY), node = el?.closest?.(".node"), inp = el?.dataset?.in;
        if (node && inp && el.dataset.kind === d.type) {
          await this.ws("aac/wires", { wires: [...this.state.domain.wires, { from: d.from, to: `${node.dataset.node}.${inp}` }] });
          await this._load(); this.render();
        }
      }
    });
  }

  async _place(type, at) {
    const col = Math.round((at.x - W / 2) / COL) * COL;
    const card = await this.ws("aac/card/add", { card_type: type, x: col, y: Math.round(at.y / 20) * 20 });
    this.selType = null; this.sel = card.id;
    await this._load(); this.render();
  }

  async _saveSetting(el) {
    const c = this._card(this.sel), key = el.dataset.set;
    let value = el.type === "checkbox" ? el.checked : el.value;
    if (el.dataset.num !== undefined) value = Number(value);
    if (el.dataset.list !== undefined) value = value.split(",").map((x) => x.trim()).filter(Boolean);
    if (el.dataset.access !== undefined) value = Object.fromEntries(value.split("\n").map((l) => l.split(":").map((x) => x.trim())).filter(([e, l]) => e && ["hidden", "read", "full"].includes(l)));
    if (el.dataset.outputs !== undefined) value = value.split("\n").map((l) => { const [n, ...r] = l.split(":"); return { name: n.trim().replace(/[^\w-]/g, "_"), description: r.join(":").trim() }; }).filter((o) => o.name);
    if (el.dataset.json !== undefined) { try { value = JSON.parse(value || "[]"); } catch (err) { alert(`Not valid: ${err.message}`); return; } }
    await this.ws("aac/card/update", { card: c.id, settings: { [key]: value } });
    await this._load(); this.render();
  }

  // ------------------------------------------------------------------ events
  _bind() {
    const $ = (s) => this.shadowRoot.querySelector(s), $$ = (s) => this.shadowRoot.querySelectorAll(s);
    $$("[data-go]").forEach((el) => el.addEventListener("click", () => this.go(el.dataset.go, el.dataset.card || null, el.dataset.id || null)));
    this._grips();
    $$("[data-newchat]").forEach((el) => el.addEventListener("click", () => this.go("chat", el.dataset.newchat, null)));
    $("#domain")?.addEventListener("change", async (e) => { await this.ws("aac/domain/switch", { domain_id: e.target.value }); await this._load(); this.chats = {}; for (const c of this._cards("chat")) await this._openChats(c.id); this.go("chat", this._cards("chat")[0]?.id || null); });
    $("#clone")?.addEventListener("click", async () => { const name = prompt("Name of the copy?", `${this.state.domain.name} (test)`); if (name) { await this.ws("aac/domain/clone", { name }); await this._load(); this.render(); } });
    // chat
    $("#send")?.addEventListener("click", () => this._send());
    $("#input")?.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this._send(); } });
    $$("[data-mode]").forEach((el) => el.addEventListener("click", async () => { this.expert = el.dataset.mode; if (this.expert === "request") this.requests = (await this.ws("aac/chat/request", { chat_id: this.view.id })).requests; this.render(); }));
    $("#delchat")?.addEventListener("click", async () => { if (confirm("Delete this chat?")) { await this.ws("aac/chat/delete", { card: this.view.card, chat_id: this.view.id }); await this._openChats(this.view.card); this.go("chat", this.view.card, null); } });
    $$("#stop").forEach((el) => el.addEventListener("click", () => this.ws("aac/stop")));
    // lists
    $$("[data-done]").forEach((el) => el.addEventListener("change", () => this._listCall("update_item", { list: el.dataset.list, item: el.dataset.done, done: el.checked })));
    $$("[data-edit]").forEach((el) => el.addEventListener("click", () => { const t = prompt("Entry", el.textContent); if (t && t.trim()) this._listCall("update_item", { list: el.dataset.list, item: el.dataset.edit, text: t }); }));
    $$("[data-field]").forEach((el) => el.addEventListener("click", () => { const v = prompt(`${el.dataset.field} (empty removes it)`, el.textContent.split(": ").slice(1).join(": ")); if (v !== null) this._listCall("update_item", { list: el.dataset.list, item: el.dataset.item, field: el.dataset.field, value: v }); }));
    $$("[data-addfield]").forEach((el) => el.addEventListener("click", () => { const f = prompt("Field name (e.g. status)"); if (!f) return; const v = prompt(f); if (v) this._listCall("update_item", { list: el.dataset.list, item: el.dataset.addfield, field: f, value: v }); }));
    $$("[data-sub]").forEach((el) => el.addEventListener("click", () => { const t = prompt("Sub-entry"); if (t) this._listCall("add_items", { list: el.dataset.list, items: [t], under: el.dataset.sub }); }));
    $$("[data-del]").forEach((el) => el.addEventListener("click", async () => { this._undoRemove = true; await this._listCall("remove_items", { list: el.dataset.list, items: [el.dataset.del] }); }));
    $$("[data-newitem]").forEach((el) => el.addEventListener("keydown", (e) => { if (e.key === "Enter" && el.value.trim()) this._listCall("add_items", { list: el.dataset.newitem, items: [el.value.trim()] }); }));
    $$("[data-dellist]").forEach((el) => el.addEventListener("click", async () => { if (confirm(`Delete the list ${el.dataset.dellist}?`)) { this._undoRemove = true; await this._listCall("delete_list", { list: el.dataset.dellist }); } }));
    $("#newlist")?.addEventListener("click", () => { const n = prompt("Name of the new list"); if (n) this._listCall("create_list", { list: n }); });
    $("#undoremove")?.addEventListener("click", async () => { this._undoRemove = false; await this._listCall("undo_remove", {}); });
    // models
    $("#probe")?.addEventListener("click", async () => {
      try { this.probed = (await this.ws("aac/models/probe", { base_url: $("#m_url").value, api_key: $("#m_key").value || null })).models; this.modelForm = { ...(this.modelForm || {}), base_url: $("#m_url").value }; this.render(); }
      catch (e) { alert(`No answer: ${e.message || e}`); }
    });
    $("#savemodel")?.addEventListener("click", async () => {
      const model = { name: $("#m_name").value || $("#m_model").value, base_url: $("#m_url").value, model: $("#m_model").value, api_key: $("#m_key").value || null };
      if (this.modelForm?.id) model.id = this.modelForm.id;
      if (!model.model) { alert("Pick or type a model"); return; }
      await this.ws("aac/models/save", { model }); this.modelForm = null; this.probed = null; await this._load(); this.render();
    });
    $("#cancelmodel")?.addEventListener("click", () => { this.modelForm = null; this.probed = null; this.render(); });
    $$("[data-editmodel]").forEach((el) => el.addEventListener("click", () => { this.modelForm = { ...this.state.models.find((m) => m.id === el.dataset.editmodel) }; this.probed = null; this.render(); }));
    $$("[data-delmodel]").forEach((el) => el.addEventListener("click", async () => { if (confirm("Delete this model from the pool?")) { await this.ws("aac/models/delete", { model_id: el.dataset.delmodel }); await this._load(); this.render(); } }));
    // nodes
    $$("[data-pick]").forEach((el) => el.addEventListener("click", () => { this.selType = this.selType === el.dataset.pick ? null : el.dataset.pick; this.render(); }));
    $("#undo")?.addEventListener("click", async () => { await this.ws("aac/undo"); await this._load(); this.render(); });
    $("#fit")?.addEventListener("click", () => this._fit());
    $("#arrange")?.addEventListener("click", () => this._doArrange());
    $("#export")?.addEventListener("click", async () => {
      const { setup } = await this.ws("aac/setup/export"), a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([JSON.stringify(setup, null, 1)], { type: "application/json" }));
      a.download = `aac-${setup.domain.replace(/\W+/g, "-").toLowerCase()}-${new Date().toISOString().slice(0, 10)}.json`;
      a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    });
    $("#import")?.addEventListener("click", () => $("#importfile").click());
    $("#importfile")?.addEventListener("change", async (e) => {
      const file = e.target.files[0];
      if (!file || !confirm(`Load "${file.name}" into this domain? Its cards and wires replace the current ones (Undo goes back).`)) return;
      try { await this.ws("aac/setup/import", { setup: JSON.parse(await file.text()) }); } catch (err) { alert(`Could not load it: ${err.message || err}`); return; }
      this.sel = null; await this._load(); this.render(); this._fit();
    });
    $("#addtag")?.addEventListener("click", async () => { const t = $("#newtag").value.trim(); if (t) { await this.ws("aac/tags/add", { tag: t }); await this._load(); this.render(); } });
    $("#newtag")?.addEventListener("keydown", (e) => { if (e.key === "Enter") $("#addtag").click(); });
    $$("[data-deltag]").forEach((el) => el.addEventListener("click", async () => { await this.ws("aac/tags/remove", { tag: el.dataset.deltag }); await this._load(); this.render(); }));
    $$("[data-slot]").forEach((el) => el.addEventListener("click", async (e) => {
      const slot = el.dataset.slot;
      if (e.shiftKey) { if (confirm(`Load slot ${slot}? (Undo brings the current setup back)`)) await this.ws("aac/slot", { slot, action: "load" }); }
      else await this.ws("aac/slot", { slot, action: "save" });
      await this._load(); this.render();
    }));
    $("#title")?.addEventListener("change", async (e) => { await this.ws("aac/card/update", { card: this.sel, title: e.target.value }); await this._load(); this.render(); });
    $$("[data-set]").forEach((el) => el.addEventListener("change", () => this._saveSetting(el)));
    $$("[data-model]").forEach((el) => el.addEventListener("change", async () => {
      const ids = [...$$("[data-model]")].filter((x) => x.checked).map((x) => x.dataset.model);
      await this.ws("aac/card/update", { card: this.sel, settings: { models: ids } }); await this._load(); this.render();
    }));
    $$("[data-tag]").forEach((el) => el.addEventListener("change", async () => {
      const key = el.dataset.tag, tags = [...$$(`[data-tag="${key}"]`)].filter((x) => x.checked).map((x) => x.value);
      await this.ws("aac/card/update", { card: this.sel, settings: { [key]: tags } }); await this._load(); this.render();
    }));
    $("#remove")?.addEventListener("click", async () => { if (confirm("Remove this card? (Undo brings it back)")) { await this.ws("aac/card/remove", { card: this.sel }); this.sel = null; await this._load(); this.render(); } });
    $("#unwire")?.addEventListener("click", () => this._unwire());
    // schedule entries and watches: the whole list is saved on any change
    const collect = (sel, attr) => [...$$(sel)].map((box) => {
      const o = {};
      box.querySelectorAll(`[${attr}]`).forEach((el) => { o[el.getAttribute(attr)] = el.type === "checkbox" ? el.checked : el.value; });
      if (attr === "data-e") o.weekdays = [...box.querySelectorAll("[data-day]")].filter((x) => x.checked).map((x) => Number(x.dataset.day));
      if (o.cooldown !== undefined) o.cooldown = Number(o.cooldown) || 300;
      return o;
    });
    const saveList = async (key, list) => { await this.ws("aac/card/update", { card: this.sel, settings: { [key]: list } }); await this._load(); this.render(); };
    $$("[data-entry] input, [data-entry] textarea").forEach((el) => el.addEventListener("change", () => saveList("entries", collect("[data-entry]", "data-e"))));
    $$("[data-watch] input, [data-watch] textarea").forEach((el) => el.addEventListener("change", () => saveList("watches", collect("[data-watch]", "data-w"))));
    // Conditional rules: the whole list is saved on any change
    $$("[data-rule] input, [data-rule] select").forEach((el) => el.addEventListener("change", () => saveList("rules", this._collectRules())));
    $("#addrule")?.addEventListener("click", () => saveList("rules", [...this._collectRules(), { name: "", select: { by: "kind", value: "said", which: "newest" }, test: "contains", value: "", actions: [{ do: "output", a: "out" }] }]));
    $$("[data-delrule]").forEach((el) => el.addEventListener("click", () => { if (confirm("Remove this rule?")) saveList("rules", this._collectRules().filter((_, i) => i !== Number(el.dataset.delrule))); }));
    $$("[data-moverule]").forEach((el) => el.addEventListener("click", () => { const r = this._collectRules(), i = Number(el.dataset.moverule), j = i + Number(el.dataset.dir); [r[i], r[j]] = [r[j], r[i]]; saveList("rules", r); }));
    $$("[data-addact]").forEach((el) => el.addEventListener("click", () => { const r = this._collectRules(); r[Number(el.dataset.addact)].actions.push({ do: "tag", a: "" }); saveList("rules", r); }));
    $$("[data-delact]").forEach((el) => el.addEventListener("click", () => { const r = this._collectRules(), i = Number(el.closest("[data-rule]").dataset.rule); r[i].actions.splice(Number(el.dataset.delact), 1); saveList("rules", r); }));
    $("#addentry")?.addEventListener("click", () => saveList("entries", [...collect("[data-entry]", "data-e"), { name: "New", at: "08:00", weekdays: [], text: "", enabled: true }]));
    $("#addwatch")?.addEventListener("click", () => saveList("watches", [...collect("[data-watch]", "data-w"), { name: "New", entity_id: "", to: "", text: "", cooldown: 300 }]));
    $$("[data-delentry]").forEach((el) => el.addEventListener("click", () => saveList("entries", collect("[data-entry]", "data-e").filter((_, i) => i !== Number(el.dataset.delentry)))));
    $$("[data-delwatch]").forEach((el) => el.addEventListener("click", () => saveList("watches", collect("[data-watch]", "data-w").filter((_, i) => i !== Number(el.dataset.delwatch)))));
    // memory, file
    $("#addmem")?.addEventListener("click", async () => { const t = $("#newmem").value.trim(); if (t) { await this.ws("aac/card/call", { card: this.view.card, function: "remember", args: { text: t } }); await this._loadPage(); this.render(); } });
    $$("[data-forget]").forEach((el) => el.addEventListener("click", async () => { await this.ws("aac/card/call", { card: this.view.card, function: "forget", args: { text: el.dataset.forget } }); await this._loadPage(); this.render(); }));
    $("#savefile")?.addEventListener("click", async () => { await this.ws("aac/card/call", { card: this.view.card, function: "write", args: { text: $("#filetext").value } }); });
    // audio
    const audioSet = async (data) => { this.audio = await this.ws("aac/audio/settings", data); this.render(); };
    $("#sttadd")?.addEventListener("change", (e) => e.target.value && audioSet({ stt: [...(this.audio.settings.stt || []), e.target.value] }));
    $$("[data-sttdel]").forEach((el) => el.addEventListener("click", () => audioSet({ stt: this.audio.settings.stt.filter((_, i) => i !== Number(el.dataset.sttdel)) })));
    $$("[data-sttup]").forEach((el) => el.addEventListener("click", () => { const l = [...this.audio.settings.stt], i = Number(el.dataset.sttup); if (i > 0) { [l[i - 1], l[i]] = [l[i], l[i - 1]]; audioSet({ stt: l }); } }));
    $("#ttsset")?.addEventListener("change", (e) => audioSet({ tts: e.target.value || null }));
    $("#volume")?.addEventListener("input", (e) => { $("#volval").textContent = e.target.value; });
    $("#volume")?.addEventListener("change", (e) => audioSet({ volume: Number(e.target.value) }));

    $("#monitor")?.addEventListener("click", () => {
      const key = `${this.selWire.from}|${this.selWire.to}`, list = this._monitors();
      this._setMonitors(list.includes(key) ? list.filter((k) => k !== key) : [...list, key]);
      if (!list.includes(key)) { this.selMon = key; this.selWire = null; this._loadMonitor(key); }
      this.render();
    });
    this._bindMonitorSide();
    if (!this._keys) {
      this._keys = true;
      window.addEventListener("keydown", (e) => { if (this.view.kind === "nodes" && this.selWire && (e.key === "Delete" || e.key === "Backspace") && !/INPUT|TEXTAREA/.test(e.composedPath()[0]?.tagName)) this._unwire(); });
    }
    if (this.view.kind === "nodes") { this._gridEvents(); if (!this._fitted) { this._fitted = true; requestAnimationFrame(() => this._fit()); } }
  }

  async _unwire() {
    const w = this.selWire;
    await this.ws("aac/wires", { wires: this.state.domain.wires.filter((x) => !(x.from === w.from && x.to === w.to)) });
    this.selWire = null; await this._load(); this.render();
  }
}

if (!customElements.get("aac-panel")) customElements.define("aac-panel", AacPanel);

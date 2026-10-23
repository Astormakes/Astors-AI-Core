// AAC 2 panel (docs/v2): one sidebar (cards put their pages in it: chats, lists), the chat, lists, models, and the
// node view (card types left, the grid, the selected card's settings right). Plain web component, HA's theme.

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const md = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\n/g, "<br>");
const tip = (html) => `<span class="qm" tabindex="0">?<span class="tip">${html}</span></span>`;
const W = 180, HEAD = 34, ROW = 20;
const KIND_COLOR = { text: "#f5a623", audio: "#4caf50", pool: "#03a9f4" };

const STYLE = `
:host { display: block; height: 100vh; color: var(--primary-text-color); background: var(--primary-background-color);
  font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
* { box-sizing: border-box; }
.root { display: flex; height: 100%; }
nav { width: 250px; flex: none; background: var(--sidebar-background-color, var(--card-background-color));
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
.palette { width: 190px; flex: none; border-right: 1px solid var(--divider-color); display: flex; flex-direction: column; }
.palette .types { flex: 1; overflow-y: auto; padding-bottom: 8px; }
.palette .info { border-top: 1px solid var(--divider-color); padding: 10px; font-size: 12px; max-height: 45%; overflow-y: auto; }
.grid { flex: 1; position: relative; overflow: hidden; background-image: radial-gradient(rgba(127,127,127,.25) 1px, transparent 1px); background-size: 20px 20px; }
.grid svg { width: 100%; height: 100%; display: block; user-select: none; }
.side { width: 300px; flex: none; border-left: 1px solid var(--divider-color); overflow-y: auto; padding: 12px; }
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
.hit { fill: none; stroke: transparent; stroke-width: 14; cursor: pointer; }
@media (max-width: 800px) { nav { position: absolute; z-index: 20; height: 100%; transform: translateX(-100%); } .root.navopen nav { transform: none; }
  .palette, .side { width: 170px; } }
`;

class AacPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.view = { kind: "chat", card: null, id: null };
    this.chats = {}; this.chat = null; this.live = {}; this.busy = false; this.expert = false;
    this.lists = {}; this.sel = null; this.selType = null; this.selWire = null; this.vp = { x: 80, y: 120, k: 1 };
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._start();
  }

  set narrow(v) { this._narrow = v; }

  ws(type, data = {}) { return this._hass.callWS({ type, ...data }); }

  async _start() {
    await this._load();
    this._hass.connection.subscribeMessage((ev) => this._event(ev), { type: "aac/subscribe" });
    const chat = this._cards("chat")[0];
    if (chat) await this._openChats(chat.id);
    this.render();
  }

  async _load() { this.state = await this.ws("aac/state"); }
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
    } else if (ev.what === "card" && this.view.kind === "lists" && this.view.card === ev.card) {
      this._loadLists(ev.card).then(() => this.render());
    } else if (ev.what === "flow") {
      for (const p of this.shadowRoot.querySelectorAll(`.wire[data-from="${CSS.escape(ev.wire)}"]`)) {
        p.classList.add("flow"); clearTimeout(p._t); p._t = setTimeout(() => p.classList.remove("flow"), 1800);
      }
    }
  }

  async go(kind, card = null, id = null) {
    this.view = { kind, card, id };
    if (kind === "chat" && id) await this._getChat();
    if (kind === "chat" && !id) this.chat = null;
    if (kind === "lists") await this._loadLists(card);
    this.render();
  }

  async _getChat() { this.chat = (await this.ws("aac/chat/get", { card: this.view.card, chat_id: this.view.id })).chat; }
  async _loadLists(card) { this.lists[card] = (await this.ws("aac/card/data", { card })).data?.lists || []; }

  // ------------------------------------------------------------------ layout
  render() {
    if (!this.state) { this.shadowRoot.innerHTML = `<style>${STYLE}</style><p style="padding:20px">Loading…</p>`; return; }
    const v = this.view;
    const main = v.kind === "nodes" ? this._nodes() : v.kind === "models" ? this._models() : v.kind === "lists" ? this._listsView() : this._chatView();
    this.shadowRoot.innerHTML = `<style>${STYLE}</style><div class="root">${this._nav()}<main>${main}</main></div>`;
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
    return `<nav><div class="top"><b>AAC</b><span>v${esc(s.version)}</span></div>
      <div class="dom"><select id="domain">${s.domains.map((d) => `<option value="${esc(d.id)}" ${d.id === s.active ? "selected" : ""}>${esc(d.name)}</option>`).join("")}</select>
        <button class="small" id="clone" title="A copy of this setup to try things out">Clone</button></div>
      <div class="scroll">${chats}${lists}</div>
      <div class="bottom">${item("Nodes", "nodes")}${item("Models", "models")}</div></nav>`;
  }

  _header(title, extra = "") { return `<header><h2>${esc(title)}</h2>${extra}</header>`; }

  // ------------------------------------------------------------------ chat
  _chatView() {
    const card = this._card(this.view.card) || this._cards("chat")[0];
    if (!card) return `${this._header("AAC")}<div class="content"><p>No chat card in this domain. Add one in Nodes.</p></div>`;
    this.view.card = card.id;
    const msgs = this.chat?.messages || [];
    const log = this.expert && this.chat ? (this.chat.entries || []).map((e) => `<div class="entry"><div class="k">${esc(e.by)} · ${esc(e.kind)} ${e.tags.map((t) => `#${esc(t)}`).join(" ")}</div>${md(e.text || (e.data?.function ? `${e.data.function}(${JSON.stringify(e.data.args || {})})` : ""))}</div>`).join("")
      : msgs.map((m) => `<div class="bubble ${m.role === "user" ? "user" : "aac"} ${m.error ? "err" : ""}">${md(m.text)}${m.cut ? '<div class="cut">stopped</div>' : ""}</div>`).join("")
        || `<div class="hint" style="margin:auto">${esc(card.title)}: ask anything.</div>`;
    const busy = this.busy ? `<div class="bubble aac" id="live">${md(this.live.text || "…")}</div>` : "";
    return `<div class="chat">${this._header(this.chat?.title || `New chat · ${card.title}`,
      `${this.chat ? `<button class="small" id="expert" title="The document as the model read it: every entry with its card, kind and tags">${this.expert ? "Chat" : "Document"}</button><button class="small danger" id="delchat">Delete</button>` : ""}<button class="small" id="stop" title="Stop everything that is working">Stop</button>`)}
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
        <button class="small" id="undo" ${d.undo ? "" : "disabled"}>Undo</button><button class="small" id="fit">Fit</button>${slots}<button class="small danger" id="stop">Send stop</button>`)}
      <div class="nodes"><div class="palette"><div class="types">${cats}</div><div class="info">${info}</div></div>
        <div class="grid" id="grid"><svg id="svg"><g id="world"><g id="wires"></g><g id="cards"></g></g></svg></div>
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
    if (this.selWire) return `<b>Wire</b><p class="hint">${esc(this.selWire.from)} → ${esc(this.selWire.to)}</p><button class="danger" id="unwire">Remove wire</button>
      <div style="margin-top:12px"><button class="small" id="monitor">Show what passed last</button></div><div id="mon"></div>`;
    const c = this.sel && this._card(this.sel);
    if (!c) return `<p class="hint">Click a card to set it up. Tags in this domain: ${d.tags.length}.</p>`;
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
      if (f.type === "rules") return `<label>${esc(f.title)}${h}${tip(`One rule: {"name": "for me", "select": {"by": "kind" | "tag" | "card" | "any", "value": "said", "which": "newest" | "all"}, "test": "contains" (is, starts, ends, matches, has_tag, count_more, empty, always), "value": "computer", "actions": [{"do": "replace", "a": "x", "b": "y"}, {"do": "tag", "a": "addressed"}, {"do": "call", "card": "lists", "function": "add_items", "args": {"list": "Shopping", "items": ["{text}"]}}, {"do": "output", "a": "yes"}, {"do": "stop"}], "go_on": false}`)}</label>
        <textarea rows="10" data-set="${esc(key)}" data-json style="font-family:monospace;font-size:12px">${esc(JSON.stringify(v || [], null, 1))}</textarea>`;
      return `<label>${esc(f.title)}${h}</label><input data-set="${esc(key)}" value="${esc(v)}">`;
    };
    return `<label>Name</label><input id="title" value="${esc(c.title)}">
      <p class="hint" style="margin-top:8px">${esc(t?.help || "")}</p>
      ${Object.entries(t?.settings || {}).map(field).join("")}
      ${this._fnDocs(t?.functions)}
      ${Object.keys(c.tags).length ? `<div class="hint" style="margin-top:8px">TAGS ${Object.keys(c.tags).map((x) => `<code>${esc(x)}</code>`).join(" ")}</div>` : ""}
      <div style="margin-top:14px;display:flex;gap:8px"><button class="danger" id="remove">Remove card</button></div>
      <p class="hint">Fields save when you leave them.</p>`;
  }

  _drawGrid() {
    const d = this.state.domain, pos = (id) => d.positions[id] || { x: 0, y: 0 };
    const port = (c, name, side) => {
      const p = pos(c.id), names = Object.keys(side === "out" ? c.outputs : c.inputs);
      return { x: p.x + (side === "out" ? W : 0), y: p.y + HEAD + ROW * names.indexOf(name) + ROW / 2 };
    };
    const height = (c) => HEAD + ROW * Math.max(Object.keys(c.inputs).length, Object.keys(c.outputs).length, 1) + 8;
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
    const byId = Object.fromEntries(d.cards.map((c) => [c.id, c]));
    this.shadowRoot.querySelector("#wires").innerHTML = d.wires.map((w) => {
      const [a, ap] = w.from.split("."), [b, bp] = w.to.split(".");
      if (!byId[a] || !byId[b]) return "";
      const o = port(byId[a], ap, "out"), i = port(byId[b], bp, "in"), k = byId[a].outputs[ap];
      const bend = Math.max(50, Math.abs(i.x - o.x) / 2), path = `M${o.x},${o.y} C${o.x + bend},${o.y} ${i.x - bend},${i.y} ${i.x},${i.y}`;
      const sel = this.selWire && this.selWire.from === w.from && this.selWire.to === w.to;
      return `<path class="wire ${k === "pool" ? "pool" : ""} ${sel ? "sel" : ""}" data-from="${esc(w.from)}" d="${path}" stroke="${KIND_COLOR[k] || "#999"}"></path>
        <path class="hit" data-wire="${esc(w.from)}|${esc(w.to)}" d="${path}"></path>`;
    }).join("");
    this._view();
  }

  _view() { const g = this.shadowRoot.querySelector("#world"); if (g) g.setAttribute("transform", `translate(${this.vp.x},${this.vp.y}) scale(${this.vp.k})`); }

  _world(e) { const r = this.shadowRoot.querySelector("#svg").getBoundingClientRect(); return { x: (e.clientX - r.left - this.vp.x) / this.vp.k, y: (e.clientY - r.top - this.vp.y) / this.vp.k }; }

  _fit() {
    const ps = Object.values(this.state.domain.positions), svg = this.shadowRoot.querySelector("#svg");
    if (!ps.length || !svg) return;
    const xs = ps.map((p) => p.x), ys = ps.map((p) => p.y), r = svg.getBoundingClientRect();
    const w = Math.max(...xs) - Math.min(...xs) + W + 80, h = Math.max(...ys) - Math.min(...ys) + 200;
    const k = Math.min(1.2, r.width / w, r.height / h);
    this.vp = { k, x: (r.width - w * k) / 2 - (Math.min(...xs) - 40) * k, y: (r.height - h * k) / 2 - (Math.min(...ys) - 60) * k };
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
      if (hit) { const [from, to] = hit.split("|"); this.selWire = { from, to }; this.sel = null; this.render(); return; }
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
        if (d.moved) await this._savePositions();
        else { this.sel = d.id; this.selWire = null; this.render(); }
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
    const card = await this.ws("aac/card/add", { card_type: type, x: Math.round(at.x / 20) * 20, y: Math.round(at.y / 20) * 20 });
    this.selType = null; this.sel = card.id;
    await this._load(); this.render();
  }

  async _saveSetting(el) {
    const c = this._card(this.sel), key = el.dataset.set;
    let value = el.type === "checkbox" ? el.checked : el.value;
    if (el.dataset.num !== undefined) value = Number(value);
    if (el.dataset.list !== undefined) value = value.split(",").map((x) => x.trim()).filter(Boolean);
    if (el.dataset.access !== undefined) value = Object.fromEntries(value.split("\n").map((l) => l.split(":").map((x) => x.trim())).filter(([e, l]) => e && ["hidden", "read", "full"].includes(l)));
    if (el.dataset.json !== undefined) { try { value = JSON.parse(value || "[]"); } catch (err) { alert(`Not valid: ${err.message}`); return; } }
    await this.ws("aac/card/update", { card: c.id, settings: { [key]: value } });
    await this._load(); this.render();
  }

  // ------------------------------------------------------------------ events
  _bind() {
    const $ = (s) => this.shadowRoot.querySelector(s), $$ = (s) => this.shadowRoot.querySelectorAll(s);
    $$("[data-go]").forEach((el) => el.addEventListener("click", () => this.go(el.dataset.go, el.dataset.card || null, el.dataset.id || null)));
    $$("[data-newchat]").forEach((el) => el.addEventListener("click", () => this.go("chat", el.dataset.newchat, null)));
    $("#domain")?.addEventListener("change", async (e) => { await this.ws("aac/domain/switch", { domain_id: e.target.value }); await this._load(); this.chats = {}; for (const c of this._cards("chat")) await this._openChats(c.id); this.go("chat", this._cards("chat")[0]?.id || null); });
    $("#clone")?.addEventListener("click", async () => { const name = prompt("Name of the copy?", `${this.state.domain.name} (test)`); if (name) { await this.ws("aac/domain/clone", { name }); await this._load(); this.render(); } });
    // chat
    $("#send")?.addEventListener("click", () => this._send());
    $("#input")?.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this._send(); } });
    $("#expert")?.addEventListener("click", () => { this.expert = !this.expert; this.render(); });
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
    $("#monitor")?.addEventListener("click", async () => {
      const r = await this.ws("aac/monitor", { wire: this.selWire.from }), m = r.message;
      $("#mon").innerHTML = m ? m.entries.map((e) => `<div class="entry"><div class="k">${esc(e.by)} · ${esc(e.kind)} ${e.tags.map((t) => `#${esc(t)}`).join(" ")}</div>${md(e.text)}</div>`).join("") : '<p class="hint">Nothing passed yet.</p>';
    });
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

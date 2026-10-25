// The Chat card's page (spec/cards.md "A card's page"): one chat (or a new one), the composer, Stop, and the expert
// views (the document, what the model got). The panel hands over .panel (its helpers), .card and .entry (the chat id).

class AacChatPage extends HTMLElement {
  constructor() {
    super();
    this.chat = null; this.busy = false; this.live = ""; this.mode = ""; this.requests = [];
    this.addEventListener("click", (e) => this._click(e));
    this.addEventListener("keydown", (e) => {
      if (e.target.id === "input" && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this._send(); }
    });
  }

  async show() { // the panel set .card / .entry
    if (this.entry === "archive") { this.archived = await this.panel.ui("archived"); this._loaded = "archive"; this.render(); return; }
    if (this.entry !== this._loaded) {
      this._loaded = this.entry; this.mode = ""; this.chat = null;
      if (this.entry) await this._get();
    }
    this.render();
  }

  _archive() { // the archived chats: open, restore, delete
    const { esc, header } = this.panel;
    const rows = (this.archived || []).map((c) => `<div class="card row"><span class="t" data-open="${esc(c.id)}" style="cursor:pointer"><b>${esc(c.title)}</b>
      <div class="hint">${esc(new Date(c.updated).toLocaleString())}</div></span><button class="small" data-restore="${esc(c.id)}">Restore</button>
      <button class="small danger" data-delete="${esc(c.id)}">Delete</button></div>`).join("");
    this.innerHTML = `${header("Archive")}<div class="content">${rows || '<p class="hint">Nothing archived.</p>'}</div>`;
  }

  async _get() {
    this.chat = await this.panel.ui("get", { chat_id: this.entry });
    if (this.mode === "request") this.requests = (await this.panel.ws("aac/chat/request", { chat_id: this.entry })).requests;
  }

  event(ev) { // what the core tells about this card
    if (ev.what === "delta" && (this.busy || (this.entry && ev.chat_id === this.entry))) { // typed here, or spoken into this chat
      this.live = ev.text;
      const box = this.querySelector("#live");
      if (box) box.innerHTML = this.panel.md(ev.text); else if (!this.busy) this.render();
    } else if (ev.what === "chat" && this.entry && !this.busy && (!ev.chat_id || ev.chat_id === this.entry)) {
      clearTimeout(this._t); this._t = setTimeout(async () => { await this._get(); this.live = ""; this.render(); }, 150);
    }
  }

  render() {
    if (this.entry === "archive") return this._archive();
    const { esc, md, header } = this.panel, card = this.card, msgs = this.chat?.messages || [];
    const name = (id) => this.panel.cardTitle(id);
    const log = this.mode === "request" && this.chat ? (this.requests || []).map((r) => `<div class="entry"><div class="k">${esc(name(r.card))} · ${esc(new Date(r.at).toLocaleTimeString())} · tools: ${esc(r.tools.join(", ") || "none")}</div></div>${r.messages.map((m) => `<div class="entry"><div class="k">${esc(m.role)} · from ${esc([...new Set(m._from || [])].map(name).join(", ") || "?")}</div>${m.tool_calls ? esc(m.tool_calls.map((c) => `${c.function.name}(${c.function.arguments})`).join("; ")) : `<pre class="req">${esc(m.content || "")}</pre>`}</div>`).join("")}`).join("")
        || '<div class="hint" style="margin:auto">No request kept for this chat (kept in memory until HA restarts).</div>'
      : this.mode === "doc" && this.chat ? (this.chat.entries || []).map((e) => `<div class="entry"><div class="k">${esc(e.by)} · ${esc(e.kind)} ${e.tags.map((t) => `#${esc(t)}`).join(" ")}</div>${md(e.text || (e.data?.function ? `${e.data.function}(${JSON.stringify(e.data.args || {})})` : ""))}</div>`).join("")
      : msgs.map((m) => `<div class="bubble ${m.role === "user" ? "user" : "aac"} ${m.error ? "err" : ""}">${md(m.text)}${m.cut ? '<div class="cut">stopped</div>' : ""}</div>`).join("")
        || `<div class="hint" style="margin:auto">${esc(card.title)}: ask anything.</div>`;
    const live = this.busy || this.live ? `<div class="bubble aac" id="live">${md(this.live || "…")}</div>` : "";
    const modes = this.chat ? `<button class="small" data-mode="" ${!this.mode ? "disabled" : ""}>Chat</button><button class="small" data-mode="doc" title="The document: every entry with its card, kind and tags" ${this.mode === "doc" ? "disabled" : ""}>Document</button><button class="small" data-mode="request" title="What the model got for the newest turn: each message with the cards its text came from, and the tools it had" ${this.mode === "request" ? "disabled" : ""}>Request</button><button class="small danger" id="delchat">Delete</button>` : "";
    this.innerHTML = `<div class="chat">${header(this.chat?.title || `New chat · ${card.title}`, `${modes}<button class="small" id="chatstop" title="Stop: sends this chat's context with a stop; a model still writing cuts its answer">Stop</button>`)}
      <div class="log">${log}${live}</div>
      ${(this.panel.failing?.() || []).length ? `<div class="warnline">Not answering: ${esc(this.panel.failing().join(", "))} (Settings → Models)</div>` : ""}
      ${this.chat?.archived ? `<div class="composer"><span class="hint" style="flex:1">This chat is archived.</span><button id="restore">Restore</button></div>` : `<div class="composer"><textarea id="input" placeholder="Message AAC…" ${this.busy ? "disabled" : ""}></textarea><button class="primary" id="send" ${this.busy ? "disabled" : ""}>Send</button></div>`}</div>`;
    const box = this.querySelector(".log");
    if (box) box.scrollTop = box.scrollHeight;
    if (!this.busy) this.querySelector("#input")?.focus();
  }

  async _send() {
    const box = this.querySelector("#input"), text = box?.value.trim();
    if (!text || this.busy) return;
    this.busy = true; this.live = "";
    this.chat = this.chat || { title: text.slice(0, 48), messages: [] };
    this.chat.messages = [...this.chat.messages, { role: "user", text }];
    this.render();
    try {
      const r = await this.panel.ui("send", { text, chat_id: this.entry });
      this.busy = false;
      if (r.chat_id !== this.entry) { this._loaded = r.chat_id; this.panel.go(r.chat_id); } // a new chat: the sidebar marks it
      this.entry = r.chat_id;
      await this._get();
    } catch (e) { this.busy = false; this.chat.messages.push({ role: "aac", text: e.message || String(e), error: true }); }
    this.render();
  }

  async _click(e) {
    const t = e.target, d = t.dataset || {};
    if (d.open) return this.panel.go(d.open);
    if (d.restore || d.delete) { // on the Archive page
      await this.panel.ui("nav_action", { entry: d.restore || d.delete, action: d.restore ? "restore" : "delete" });
      return this.show();
    }
    if (t.id === "restore") { await this.panel.ui("nav_action", { entry: this.entry, action: "restore" }); await this._get(); return this.render(); }
    if (t.id === "send") this._send();
    else if (t.dataset?.mode !== undefined) {
      this.mode = t.dataset.mode;
      if (this.mode === "request") this.requests = (await this.panel.ws("aac/chat/request", { chat_id: this.entry })).requests;
      this.render();
    } else if (t.id === "delchat" && await this.panel.confirm("Delete this chat?", "Delete", true)) {
      await this.panel.ui("delete", { chat_id: this.entry });
      this.panel.go(null);
    } else if (t.id === "chatstop") {
      if (this.entry) await this.panel.ui("stop", { chat_id: this.entry }); else await this.panel.ws("aac/stop");
    }
  }
}

if (!customElements.get("aac-card-chat")) customElements.define("aac-card-chat", AacChatPage);

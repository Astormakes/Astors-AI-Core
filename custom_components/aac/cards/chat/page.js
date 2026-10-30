// The Chat card's page (spec/cards.md "A card's page"): one chat (or a new one), the composer, Stop, and Behind the
// scenes: the context as the model got it (docs/v2 page 3). The panel hands over .panel (its helpers), .card and
// .entry (the chat id).

const STYLE = `<style>
.calls { margin-top: 6px; font-size: 12px; opacity: .8; } .calls summary { cursor: pointer; }
.calls .call { margin: 4px 0 0 10px; } .calls pre { white-space: pre-wrap; word-break: break-word; margin: 2px 0 0 10px; font: inherit; opacity: .85; }
.behind { padding: 4px 2px; }
.toolline { align-self: flex-start; margin: 2px 0 2px 8px; }
</style>`;

class AacChatPage extends HTMLElement {
  constructor() {
    super();
    this.chat = null; this.busy = false; this.live = ""; this.mode = ""; this.draft = "";
    this.addEventListener("click", (e) => this._click(e));
    this.addEventListener("keydown", (e) => {
      if (e.target.id === "input" && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this._send(); }
    });
    this.addEventListener("input", (e) => { if (e.target.id === "input") this.draft = e.target.value; }); // survives every redraw
  }

  async show() { // the panel set .card / .entry
    if (this.entry === "archive") { this.archived = await this.panel.ui("archived"); this._loaded = "archive"; this.render(); return; }
    if (this.entry !== this._loaded) {
      this._loaded = this.entry; this.mode = ""; this.chat = null; this.live = ""; // nothing of the last chat stays
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

  async _get() { this.chat = await this.panel.ui("get", { chat_id: this.entry }); }

  event(ev) { // what the core tells about this card
    if (ev.what === "delta" && (this.busy || (this.entry && ev.chat_id === this.entry))) { // typed here, or spoken into this chat
      this.live = ev.text;
      const box = this.querySelector("#live");
      if (box) box.innerHTML = this.panel.md(ev.text); else if (!this.busy) this.render();
    } else if (ev.what === "chat" && this.entry && !this.busy && (!ev.chat_id || ev.chat_id === this.entry)) {
      clearTimeout(this._t); this._t = setTimeout(async () => { await this._get(); this.live = ""; this.render(); }, 150);
    }
  }

  _calls(m) { // the tools an answer called, small; unfolds to what was sent and what came back
    const { esc } = this.panel;
    if (!m.calls?.length) return "";
    const short = (s, n) => { const x = String(s ?? ""); return x.length > n ? `${x.slice(0, n)}…` : x; };
    return `<details class="calls"><summary>🔧 ${m.calls.map((c) => esc(c.name)).join(", ")}</summary>${m.calls.map((c) => `<div class="call"><code>${esc(c.name)}(${esc(JSON.stringify(c.args || {}).slice(1, -1))})</code>
      ${c.result === null || c.result === undefined ? '<div class="hint">no result</div>' : `<pre>→ ${esc(short(c.result, 1500))}</pre>`}</div>`).join("")}</details>`;
  }

  render() {
    if (this.entry === "archive") return this._archive();
    const { esc, md, header } = this.panel, card = this.card, msgs = this.chat?.messages || [];
    const body = this.mode === "behind" && this.chat
      ? `<div class="behind">${this.panel.contextView(this.chat.behind || {})}</div>`
      : msgs.map((m) => (!m.text && m.calls?.length ? `<div class="toolline">${this._calls(m)}</div>` // it only called tools: a small line
        : `<div class="bubble ${m.role === "user" ? "user" : "aac"} ${m.error ? "err" : ""}">${m.text ? md(m.text) : ""}${this._calls(m)}${m.cut ? '<div class="cut">stopped</div>' : ""}</div>`)).join("")
        || `<div class="hint" style="margin:auto">${esc(card.title)}: ask anything.</div>`;
    const live = (this.busy || this.live) && this.mode !== "behind" ? `<div class="bubble aac" id="live">${md(this.live || "…")}</div>` : "";
    const modes = this.chat ? `<button class="small" data-mode="" ${!this.mode ? "disabled" : ""}>Chat</button><button class="small" data-mode="behind" title="The context as the model got it: the system parts with the card each came from, every message with what was attached, the tools it called and what came back, the tools it had" ${this.mode === "behind" ? "disabled" : ""}>Behind the scenes</button><button class="small danger" id="delchat">Delete</button>` : "";
    this.innerHTML = `${STYLE}<div class="chat">${header(this.chat?.title || `New chat · ${card.title}`, `${modes}<button class="small" id="chatstop" title="Stop: sends this chat's context with a stop; a model still writing cuts its answer">Stop</button>`)}
      <div class="log">${body}${live}</div>
      ${(this.panel.failing?.() || []).length ? `<div class="warnline">Not answering: ${esc(this.panel.failing().join(", "))} (Settings → Models)</div>` : ""}
      ${this.chat?.archived ? `<div class="composer"><span class="hint" style="flex:1">This chat is archived.</span><button id="restore">Restore</button></div>` : `<div class="composer"><textarea id="input" placeholder="Message AAC…" ${this.busy ? "disabled" : ""}>${esc(this.draft)}</textarea><button class="primary" id="send" ${this.busy ? "disabled" : ""}>Send</button></div>`}</div>`;
    const box = this.querySelector(".log");
    if (box && this.mode !== "behind") box.scrollTop = box.scrollHeight;
    const input = this.querySelector("#input");
    if (input && !this.busy) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
  }

  async _send() {
    const box = this.querySelector("#input"), text = box?.value.trim();
    if (!text || this.busy) return;
    this.busy = true; this.live = ""; this.draft = "";
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
    this.live = ""; // the finished answer is in the chat now: the growing one goes
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
    else if (d.mode !== undefined && t.tagName === "BUTTON") { this.mode = d.mode; await this._get(); this.render(); }
    else if (t.id === "delchat" && await this.panel.confirm("Delete this chat?", "Delete", true)) {
      await this.panel.ui("delete", { chat_id: this.entry });
      this.panel.go(null);
    } else if (t.id === "chatstop") {
      if (this.entry) await this.panel.ui("stop", { chat_id: this.entry }); else await this.panel.ws("aac/stop");
    }
  }
}

if (!customElements.get("aac-card-chat")) customElements.define("aac-card-chat", AacChatPage);

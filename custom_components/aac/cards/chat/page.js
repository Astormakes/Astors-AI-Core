// The Chat card's page (spec/cards.md "A card's page"): one chat (or a new one), the composer, Stop, and two views of
// the newest turn (docs/v2 page 3): Context (in sections, each part with its card) and Plain text (the request as the
// model read it). The panel hands over .panel (its helpers), .card and .entry (the chat id).

const STYLE = `<style>
.calls { margin-top: 6px; font-size: 12px; opacity: .8; } .calls summary { cursor: pointer; }
.calls .call { margin: 4px 0 0 10px; } .calls pre { white-space: pre-wrap; word-break: break-word; margin: 2px 0 0 10px; font: inherit; opacity: .85; }
.behind { padding: 4px 2px; }
.toolline { align-self: flex-start; margin: 2px 0 2px 8px; }
.bubble { position: relative; }
.pen { position: absolute; bottom: -10px; right: 6px; border: none; background: var(--card-background-color); border-radius: 50%;
  width: 22px; height: 22px; padding: 0; font-size: 12px; cursor: pointer; opacity: 0; box-shadow: 0 1px 3px rgba(0,0,0,.25); }
.bubble:hover .pen, .pen:focus { opacity: .9; } @media (hover: none) { .pen { opacity: .5; } }
.bubble.editing { width: 80%; max-width: 80%; } .bubble.editing textarea { width: 100%; box-sizing: border-box; min-height: 3em; font: inherit; resize: vertical; }
.editbar { display: flex; justify-content: flex-end; gap: 6px; margin-top: 4px; }
.ok { border: none; border-radius: 50%; width: 28px; height: 28px; cursor: pointer; background: #9e9e9e; color: #fff; font-size: 16px; }
.ok:hover, .ok:focus { background: #43a047; }
.plain { white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, monospace; font-size: 12px; line-height: 1.5; margin: 0; }
.plain .tok { opacity: .45; } .plainhead { margin: 0 0 8px; }
.plain .ans { color: var(--primary-color); }
.answered { margin-top: 14px; padding-top: 8px; border-top: 1px dashed var(--divider-color); }
.answered .wrote { color: var(--primary-color); white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, monospace; font-size: 12px; margin: 4px 0 0; }
</style>`;

class AacChatPage extends HTMLElement {
  constructor() {
    super();
    this.chat = null; this.busy = false; this.live = ""; this.mode = ""; this.draft = "";
    this.addEventListener("click", (e) => this._click(e));
    this.addEventListener("keydown", (e) => {
      if (e.target.id === "input" && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this._send(); }
    });
    this.addEventListener("input", (e) => {
      if (e.target.id === "input") this.draft = e.target.value; // survives every redraw
      if (e.target.id === "editbox" && this.editing) this.editing.text = e.target.value;
    });
    this.addEventListener("keydown", (e) => {
      if (e.target.id !== "editbox") return;
      if (e.key === "Escape") { this.editing = null; this.render(); }
      else if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this._saveEdit(); }
    });
  }

  async show() { // the panel set .card / .entry
    if (this.entry === "archive") { this.archived = await this.panel.ui("archived"); this._loaded = "archive"; this.render(); return; }
    if (this.entry !== this._loaded) {
      this._loaded = this.entry; this.mode = ""; this.chat = null; this.live = ""; this.editing = null; // nothing of the last chat stays
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
    if (this.mode === "text") this.read = await this.panel.ui("as_read", { chat_id: this.entry });
  }

  _plain() { // the request as the model read it: the template's markers dimmed, each tool on its own line
    const { esc } = this.panel, r = this.read || {};
    if (!r.text) return '<p class="hint">Nothing kept yet: the next turn in this chat shows here.</p>';
    const TOKEN = /(<\|[^<>\s|]{0,30}\|?>|<[^<>\s|]{1,30}\|>)/g;
    const text = r.text.replace(/(?<!\n)(<\|tool[^<>\s|]*\|?>)/g, "\n$1");
    const marked = (s) => s.split(TOKEN).map((part, i) => (i % 2 ? `<span class="tok">${esc(part)}</span>` : esc(part))).join("");
    let html = "", at = 0; // the model's earlier answers in the text: blue (found in order)
    for (const a of r.answers || []) {
      const i = text.indexOf(a, at);
      if (i < 0) continue;
      html += `${marked(text.slice(at, i))}<span class="ans">${esc(a)}</span>`;
      at = i + a.length;
    }
    html += marked(text.slice(at));
    const rep = r.reply, wrote = rep ? [rep.content, ...(rep.calls || []).map((c) => `[calls ${c}]`)].filter(Boolean).join("\n") : "";
    const reply = rep ? `<div class="answered"><b>The model answered:</b><pre class="wrote">${esc(wrote || "(nothing)")}</pre></div>` : "";
    return `<p class="hint plainhead">${r.template ? `As ${esc(r.model)} read it: the model server wrote it with the model's own template (dim: its markers; blue: what the model wrote earlier).` : `The request as sent to ${esc(r.model)} (its server cannot show the model's template).`}</p><pre class="plain">${html}</pre>${reply}`;
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
    const body = this.mode === "context" && this.chat ? `<div class="behind">${this.panel.contextView(this.chat.behind || {})}</div>`
      : this.mode === "text" && this.chat ? `<div class="behind">${this._plain()}</div>`
      : msgs.map((m) => (!m.text && m.calls?.length ? `<div class="toolline">${this._calls(m)}</div>` // it only called tools: a small line
        : this.editing?.id === m.id ? `<div class="bubble ${m.role === "user" ? "user" : "aac"} editing"><textarea id="editbox">${esc(this.editing.text)}</textarea>
          <div class="editbar"><button class="small" data-editcancel>Cancel</button><button class="ok" data-editok title="${m.role === "user" ? "Send" : "Save"} (Enter)">✓</button></div></div>`
        : `<div class="bubble ${m.role === "user" ? "user" : "aac"} ${m.error ? "err" : ""}">${m.text ? md(m.text) : ""}${this._calls(m)}${m.cut ? '<div class="cut">stopped</div>' : ""}${m.id && m.text && !m.error && !this.busy ? `<button class="pen" data-edit="${esc(m.id)}" title="Edit">✎</button>` : ""}</div>`)).join("")
        || `<div class="hint" style="margin:auto">${esc(card.title)}: ask anything.</div>`;
    const live = (this.busy || this.live) && !this.mode ? `<div class="bubble aac" id="live">${md(this.live || "…")}</div>` : "";
    const mode = (m, label, title) => `<button class="small" data-mode="${m}" title="${title}" ${this.mode === m ? "disabled" : ""}>${label}</button>`;
    const modes = this.chat ? `${mode("", "Chat", "The conversation")}${mode("context", "Context", "The newest turn's context, in sections: the system parts with the card each came from, every message with what was attached, the tools it called and what came back, the tools it had")}${mode("text", "Plain text", "The newest request exactly as the model read it")}<button class="small danger" id="delchat">Delete</button>` : "";
    this.innerHTML = `${STYLE}<div class="chat">${header(this.chat?.title || `New chat · ${card.title}`, `${modes}<button class="small" id="chatstop" title="Stop: sends this chat's context with a stop; a model still writing cuts its answer">Stop</button>`)}
      <div class="log">${body}${live}</div>
      ${(this.panel.failing?.() || []).length ? `<div class="warnline">Not answering: ${esc(this.panel.failing().join(", "))} (Settings → Models)</div>` : ""}
      ${this.chat?.archived ? `<div class="composer"><span class="hint" style="flex:1">This chat is archived.</span><button id="restore">Restore</button></div>` : `<div class="composer"><textarea id="input" placeholder="Message AAC…" ${this.busy ? "disabled" : ""}>${esc(this.draft)}</textarea><button class="primary" id="send" ${this.busy ? "disabled" : ""}>Send</button></div>`}</div>`;
    const box = this.querySelector(".log");
    if (box && !this.mode) box.scrollTop = box.scrollHeight;
    const input = this.querySelector("#editbox") || this.querySelector("#input");
    if (input && !this.busy) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
    if (this.querySelector("#editbox")) this.querySelector(".editing").scrollIntoView({ block: "nearest" }); // it stays where it was
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

  async _saveEdit() { // the pencil's ✓: an answer only changes; a line of yours asks first: resend from there, or only the text
    const ed = this.editing, m = (this.chat?.messages || []).find((x) => x.id === ed?.id), text = (ed?.text || "").trim();
    if (!m || !text) return;
    let resend = false;
    if (m.role === "user") {
      const r = await this.panel.dialog({ text: "Resend: what came after this line goes and the turn runs again from here.\nKeep the rest: only the text changes.",
        buttons: [["cancel", "Cancel"], ["keep", "Keep the rest"], ["resend", "Resend", "primary"]] });
      if (r.button === "cancel") return;
      resend = r.button === "resend";
    }
    this.editing = null;
    if (resend) { // as when sending: what came after goes at once, the answer grows
      this.chat.messages = [...this.chat.messages.slice(0, this.chat.messages.indexOf(m)), { ...m, text }];
      this.busy = true; this.live = ""; this.render();
    }
    try { await this.panel.ui("edit", { chat_id: this.entry, id: m.id, text, resend }); }
    catch (err) { this.panel.alert(err.message || String(err)); }
    this.busy = false; this.live = "";
    await this._get(); this.render();
  }

  async _click(e) {
    const t = e.target, d = t.dataset || {};
    if (d.edit) { const m = this.chat.messages.find((x) => x.id === d.edit); this.editing = { id: d.edit, text: m?.text || "" }; return this.render(); }
    if (d.editcancel !== undefined) { this.editing = null; return this.render(); }
    if (d.editok !== undefined) return this._saveEdit();
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

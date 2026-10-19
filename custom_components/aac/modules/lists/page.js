// Lists module: its page in the AAC panel. No shadow DOM, so the panel's styles and skins apply.
// The panel sets .ws(type, data) (sends the selected domain along) and calls .refresh() on updates.

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

class AacListsPage extends HTMLElement {
  constructor() {
    super();
    this._lists = null;
    this._sel = null;
    this.addEventListener("click", (e) => this._click(e));
    this.addEventListener("change", (e) => this._change(e));
    this.addEventListener("keydown", (e) => this._key(e));
  }

  connectedCallback() { if (!this._lists) this.refresh(); else this._render(); }

  async refresh() {
    if (!this.ws) return;
    try { this._lists = (await this.ws("aac/lists")).lists; } catch (e) { this._error = e.message || String(e); }
    if (this._lists && !this._lists.some((l) => l.id === this._sel)) this._sel = this._lists[0]?.id || null;
    this._render();
  }

  async _op(data) {
    try { this._lists = (await this.ws("aac/lists/op", data)).lists; this._error = null; }
    catch (e) { this._error = e.message || String(e); }
    this._render();
  }

  _entries(items, depth, view) {
    return items.map((e) => {
      const fields = Object.entries(e.fields || {}).map(([k, v]) => `<span class="tag">${esc(k)}: ${esc(typeof v === "object" ? JSON.stringify(v) : v)}</span>`).join(" ");
      return `<div class="src lrow" style="padding-left:${depth * 1.6}em" data-item="${esc(e.id)}">
          ${view === "checklist" || e.done ? `<input type="checkbox" class="ldone" ${e.done ? "checked" : ""} aria-label="done">` : `<span class="dot"></span>`}
          <div style="flex:1;min-width:0"><div class="n ltext" tabindex="0" role="button" title="Click to edit" style="${e.done ? "text-decoration:line-through;opacity:.6" : ""}">${esc(e.text)}</div>
            ${fields ? `<div class="m">${fields}</div>` : ""}</div>
          <span class="actions" style="margin:0">
            <button class="icon-btn" data-act="sub" title="Add a sub-entry">+</button>
            <button class="icon-btn" data-act="fields" title="Fields (qty, store, when, …)">⋯</button>
            <button class="icon-btn" data-act="del" title="Remove">✕</button>
          </span>
        </div>${e.children?.length ? this._entries(e.children, depth + 1, view) : ""}`;
    }).join("");
  }

  _render() {
    if (!this._lists) { this.innerHTML = `<div class="card"><p>${this._error ? esc(this._error) : "Loading…"}</p></div>`; return; }
    const lst = this._lists.find((l) => l.id === this._sel);
    const count = (items) => items.reduce((n, e) => n + 1 + count(e.children || []), 0);
    const open = (items) => items.reduce((n, e) => n + (e.done ? 0 : 1) + open(e.children || []), 0);
    this.innerHTML = `
      <div class="split">
        <div class="card"><h2>${lst ? esc(lst.name) : "LISTS"}
            ${lst ? `<span class="tag">${esc(lst.view)}</span>` : ""}</h2>
          ${lst ? `
            <div class="actions" style="margin-top:0">
              <button class="btn" data-act="rename">Rename</button>
              <button class="btn" data-act="view">${lst.view === "checklist" ? "Show as outline" : "Show as checklist"}</button>
              <button class="btn danger" data-act="dellist">Delete list</button>
            </div>
            <div class="lentries">${lst.items.length ? this._entries(lst.items, 0, lst.view) : '<p class="hint">Empty. Add the first entry below, or ask AAC.</p>'}</div>
            <div class="row" style="margin-top:.8em"><div style="flex:1"><input id="lnew" placeholder="New entry… (Enter)"></div>
              <div style="flex:none"><button class="btn primary" data-act="add">Add</button></div></div>`
          : '<p>No lists yet. Create one, or just tell AAC ("keep a list of …").</p>'}
          ${this._error ? `<div class="msg err">${esc(this._error)}</div>` : ""}
        </div>
        <div class="card"><h2>ALL LISTS</h2>
          ${this._lists.map((l) => `<div class="src" style="cursor:pointer" data-select="${esc(l.id)}">
              <span class="dot ${l.id === this._sel ? "ok" : ""}"></span>
              <div style="flex:1"><div class="n">${esc(l.name)}</div><div class="m">${open(l.items)} open · ${count(l.items)} entries</div></div></div>`).join("") || '<p class="hint">None yet.</p>'}
          <div class="actions"><button class="btn primary" data-act="newlist">New list</button></div>
          <p class="hint">Entries can have sub-entries and fields. A field <code>when</code> (a date, or date and time) puts the entry on AAC's calendar.</p>
        </div>
      </div>`;
  }

  _itemId(el) { return el.closest("[data-item]")?.dataset.item; }

  async _click(e) {
    const t = e.target;
    if (t.closest("[data-select]")) { this._sel = t.closest("[data-select]").dataset.select; this._render(); return; }
    const act = t.closest("[data-act]")?.dataset.act;
    const lst = this._lists?.find((l) => l.id === this._sel);
    if (t.classList.contains("ltext")) {
      const text = prompt("Entry:", t.textContent);
      if (text?.trim()) await this._op({ op: "update", list: lst.id, item: this._itemId(t), text: text.trim() });
      return;
    }
    if (!act) return;
    if (act === "newlist") {
      const name = prompt("Name of the new list:");
      if (!name?.trim()) return;
      await this._op({ op: "create", name: name.trim() });
      this._sel = this._lists.find((l) => l.name === name.trim())?.id || this._sel;
      this._render();
    } else if (act === "add") {
      const input = this.querySelector("#lnew");
      if (input?.value.trim()) { await this._op({ op: "add", list: lst.id, text: input.value.trim() }); this.querySelector("#lnew")?.focus(); }
    } else if (act === "sub") {
      const text = prompt("Sub-entry:");
      if (text?.trim()) await this._op({ op: "add", list: lst.id, text: text.trim(), parent: this._itemId(t) });
    } else if (act === "fields") {
      const id = this._itemId(t);
      const find = (items) => { for (const x of items) { if (x.id === id) return x; const f = find(x.children || []); if (f) return f; } return null; };
      const entry = find(lst.items);
      const now = Object.entries(entry.fields || {}).map(([k, v]) => `${k}=${v}`).join(", ");
      const text = prompt("Fields as key=value, comma separated (e.g. qty=2, store=Lidl, when=2026-10-24). Empty value removes a field:", now);
      if (text === null) return;
      const fields = {};
      for (const k of Object.keys(entry.fields || {})) fields[k] = null;
      for (const part of text.split(",")) { const [k, ...v] = part.split("="); if (k?.trim()) fields[k.trim()] = v.join("=").trim() || null; }
      await this._op({ op: "update", list: lst.id, item: id, fields });
    } else if (act === "del") {
      await this._op({ op: "remove", list: lst.id, item: this._itemId(t) });
    } else if (act === "rename") {
      const name = prompt("Name:", lst.name);
      if (name?.trim()) await this._op({ op: "rename", list: lst.id, name: name.trim() });
    } else if (act === "view") {
      await this._op({ op: "rename", list: lst.id, name: lst.name, view: lst.view === "checklist" ? "outline" : "checklist" });
    } else if (act === "dellist") {
      if (confirm(`Delete the list "${lst.name}" with all entries?`)) await this._op({ op: "delete", list: lst.id });
      this._sel = this._lists?.[0]?.id || null;
      this._render();
    }
  }

  async _change(e) {
    if (!e.target.classList.contains("ldone")) return;
    await this._op({ op: "update", list: this._sel, item: this._itemId(e.target), done: e.target.checked });
  }

  async _key(e) {
    if (e.target.id === "lnew" && e.key === "Enter" && !e.isComposing) { e.preventDefault(); this.querySelector('[data-act="add"]')?.click(); }
  }
}

if (!customElements.get("aac-lists-page")) customElements.define("aac-lists-page", AacListsPage);

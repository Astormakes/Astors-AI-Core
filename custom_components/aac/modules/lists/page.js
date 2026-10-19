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
    this.addEventListener("dragstart", (e) => {
      const row = e.target.closest?.("[data-item]");
      if (!row) return;
      this._drag = row.dataset.item;
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", this._drag);
    });
    this.addEventListener("dragover", (e) => {
      const row = e.target.closest?.("[data-item]");
      if (!row || !this._drag) return;
      e.preventDefault();
      const box = row.getBoundingClientRect(), text = row.querySelector(".ltext")?.getBoundingClientRect();
      const mode = text && e.clientX > text.left + 48 ? "into" : e.clientY < box.top + box.height / 2 ? "before" : "after";
      this.querySelectorAll(".lrow").forEach((r) => r.classList.remove("drop-before", "drop-after", "drop-into"));
      row.classList.add(`drop-${mode}`);
      this._dropMode = mode;
    });
    this.addEventListener("dragend", () => {
      this._drag = null;
      this.querySelectorAll(".lrow").forEach((r) => r.classList.remove("drop-before", "drop-after", "drop-into"));
    });
    this.addEventListener("drop", (e) => {
      const row = e.target.closest?.("[data-item]");
      const id = this._drag;
      this._drag = null;
      if (!row || !id) return;
      e.preventDefault();
      this._moveRel(id, row.dataset.item, this._dropMode || "before");
    });
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
      const fields = Object.entries(e.fields || {}).map(([k, v]) => `<span class="tag">${k === "qty" ? "" : esc(k) + ": "}${esc(typeof v === "object" ? JSON.stringify(v) : v)}</span>`).join(" ");
      return `<div class="lrow ${e.done ? "ldone-row" : ""}" style="--depth:${depth}" data-item="${esc(e.id)}" draggable="true">
          <span class="lgrip" title="Drag to move (further right = as a sub-entry)">⠿</span>
          ${view === "checklist" || e.done ? `<input type="checkbox" class="ldone" ${e.done ? "checked" : ""} aria-label="done">` : `<span class="lbullet">•</span>`}
          <span class="ltext" tabindex="0" role="button" title="Click to edit">${esc(e.text)}</span>${fields ? ` <span class="lfields">${fields}</span>` : ""}
          <span class="lacts">
            <button class="icon-btn" data-act="outdent" title="Outdent">←</button>
            <button class="icon-btn" data-act="indent" title="Indent (into the entry above)">→</button>
            <button class="icon-btn" data-act="sub" title="Add a sub-entry">+</button>
            <button class="icon-btn" data-act="fields" title="Fields (qty, store, when, …)">⋯</button>
            <button class="icon-btn" data-act="del" title="Remove">✕</button>
          </span>
        </div>${e.children?.length ? this._entries(e.children, depth + 1, view) : ""}`;
    }).join("");
  }

  // where an entry sits: its siblings, index and parent entry
  _locate(id, items = this._current()?.items || [], parent = null) {
    for (let i = 0; i < items.length; i++) {
      if (items[i].id === id) return { siblings: items, index: i, parent };
      const found = this._locate(id, items[i].children || [], items[i]);
      if (found) return found;
    }
    return null;
  }

  _current() { return this._lists?.find((l) => l.id === this._sel); }

  // move `id` before/after/into `target` (the backend removes first, then inserts at index)
  async _moveRel(id, target, mode) {
    if (id === target) return;
    const t = this._locate(target), from = this._locate(id);
    if (!t || !from) return;
    let parent, index;
    if (mode === "into") { parent = target; index = null; }
    else {
      parent = t.parent ? t.parent.id : null;
      index = t.index + (mode === "after" ? 1 : 0);
      if (from.siblings === t.siblings && from.index < index) index -= 1;
    }
    await this._op({ op: "move", list: this._sel, item: id, parent, ...(index === null ? {} : { index }) });
  }

  _render() {
    if (!this._lists) { this.innerHTML = `<div class="card"><p>${this._error ? esc(this._error) : "Loading…"}</p></div>`; return; }
    const lst = this._lists.find((l) => l.id === this._sel);
    const count = (items) => items.reduce((n, e) => n + 1 + count(e.children || []), 0);
    const open = (items) => items.reduce((n, e) => n + (e.done ? 0 : 1) + open(e.children || []), 0);
    this.innerHTML = `
      <style>
        aac-lists-page .lrow { display: flex; align-items: center; gap: .45em; padding: .22em .3em .22em calc(.3em + var(--depth) * 1.5em);
          border-bottom: 1px solid color-mix(in srgb, var(--line) 50%, transparent); min-height: 2.1em; }
        aac-lists-page .lrow input[type=checkbox] { width: 1.05em; height: 1.05em; min-height: 0; flex: none; margin: 0; accent-color: var(--magenta); }
        aac-lists-page .lrow .ltext { cursor: text; overflow-wrap: anywhere; flex: 0 1 auto; min-width: 0; }
        aac-lists-page .lrow .lfields { flex: none; }
        aac-lists-page .ldone-row .ltext { text-decoration: line-through; opacity: .55; }
        aac-lists-page .lfields .tag { font-size: .68em; }
        aac-lists-page .lgrip { cursor: grab; opacity: .35; user-select: none; }
        aac-lists-page .lbullet { opacity: .5; width: 1.1em; text-align: center; }
        aac-lists-page .lacts { margin-left: auto; display: flex; gap: .1em; opacity: 0; transition: opacity .1s; }
        aac-lists-page .lacts .icon-btn { padding: .1em .35em; min-height: 0; min-width: 0; }
        aac-lists-page .lrow:hover .lacts, aac-lists-page .lrow:focus-within .lacts { opacity: 1; }
        @media (hover: none) { aac-lists-page .lacts { opacity: .8; } }
        aac-lists-page .drop-before { box-shadow: inset 0 2px 0 var(--magenta); }
        aac-lists-page .drop-after { box-shadow: inset 0 -2px 0 var(--magenta); }
        aac-lists-page .drop-into { box-shadow: inset 3px 0 0 var(--magenta); background: color-mix(in srgb, var(--magenta) 8%, transparent); }
      </style>
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
    const lst = this._current();
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
    } else if (act === "indent") {
      const at = this._locate(this._itemId(t));
      if (at && at.index > 0) await this._moveRel(this._itemId(t), at.siblings[at.index - 1].id, "into");
    } else if (act === "outdent") {
      const at = this._locate(this._itemId(t));
      if (at?.parent) await this._moveRel(this._itemId(t), at.parent.id, "after");
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

// The Lists card's page (spec/cards.md "A card's page"): one list (the sidebar entry picked), or all lists to pick
// from. Entries with fields and sub-entries; everything goes through the card's functions, like a model's calls.

class AacListsPage extends HTMLElement {
  constructor() {
    super();
    this.lists = null; this.undo = false;
    this.addEventListener("click", (e) => this._click(e));
    this.addEventListener("change", (e) => {
      const d = e.target.dataset;
      if (d.done) this._call("update_item", { list: d.list, item: d.done, done: e.target.checked });
    });
    this.addEventListener("keydown", (e) => {
      const d = e.target.dataset;
      if (d.newitem !== undefined && e.key === "Enter" && e.target.value.trim()) this._call("add_items", { list: d.newitem, items: [e.target.value.trim()] });
    });
  }

  async show() { await this._load(); this.render(); }

  async _load() { this.lists = (await this.panel.data())?.lists || []; }

  event(ev) { if (ev.what === "card") { clearTimeout(this._t); this._t = setTimeout(async () => { await this._load(); this.render(); }, 150); } }

  async _call(fn, args) {
    try { await this.panel.call(fn, args); } catch (e) { alert(e.message || e); }
    await this._load(); this.render();
  }

  _tree(items, list) {
    const { esc } = this.panel;
    return `<ul>${items.map((it) => `<li><div class="row">
        <input type="checkbox" style="width:auto" data-done="${esc(it.id)}" data-list="${esc(list)}" ${it.done ? "checked" : ""}>
        <span class="t ${it.done ? "done" : ""}" data-edit="${esc(it.id)}" data-list="${esc(list)}">${esc(it.text)}</span>
        <span class="acts"><button class="small" data-sub="${esc(it.id)}" data-list="${esc(list)}" title="Add a sub-entry">+</button>
          <button class="small" data-addfield="${esc(it.id)}" data-list="${esc(list)}" title="Add a field">field</button>
          <button class="small danger" data-del="${esc(it.id)}" data-list="${esc(list)}">✕</button></span></div>
        ${Object.keys(it.fields || {}).length ? `<div class="fields">${Object.entries(it.fields).map(([k, v]) => `<span class="chip" data-field="${esc(k)}" data-item="${esc(it.id)}" data-list="${esc(list)}">${esc(k)}: ${esc(v)}</span>`).join("")}</div>` : ""}
        ${it.children?.length ? this._tree(it.children, list) : ""}</li>`).join("")}</ul>`;
  }

  render() {
    if (!this.lists) { this.innerHTML = '<p class="hint" style="padding:20px">Loading…</p>'; return; }
    const { esc, header } = this.panel, li = this.lists.find((x) => x.name === this.entry);
    const open = (items) => items.reduce((n, it) => n + (it.done ? 0 : 1) + open(it.children || []), 0);
    const undo = this.undo ? '<button class="small" id="undoremove">Undo remove</button>' : "";
    if (!li) { // no list picked: the lists to pick from
      this.innerHTML = `${header(this.card.title, `${undo}<button class="small" id="newlist">New list</button>`)}
        <div class="content">${this.lists.map((x) => `<div class="card row" data-open="${esc(x.name)}" style="cursor:pointer"><span class="t"><b>${esc(x.name)}</b></span><span class="hint">${open(x.items || [])} open</span></div>`).join("") || '<p class="hint">No lists yet.</p>'}</div>`;
      return;
    }
    this.innerHTML = `${header(li.name, `${undo}<button class="small danger" data-dellist="${esc(li.name)}">Delete list</button>`)}
      <div class="content"><div class="card lst">${this._tree(li.items || [], li.name)}
        <div class="row" style="margin-top:8px"><input data-newitem="${esc(li.name)}" placeholder="New entry…"></div></div></div>`;
    this.querySelector("[data-newitem]")?.focus();
  }

  async _click(e) {
    const t = e.target.closest("[data-open],[data-edit],[data-field],[data-addfield],[data-sub],[data-del],[data-dellist],#newlist,#undoremove");
    if (!t) return;
    const d = t.dataset;
    if (d.open) this.panel.go(d.open);
    else if (d.edit) { const v = prompt("Entry", t.textContent); if (v && v.trim()) this._call("update_item", { list: d.list, item: d.edit, text: v }); }
    else if (d.field) { const v = prompt(`${d.field} (empty removes it)`, t.textContent.split(": ").slice(1).join(": ")); if (v !== null) this._call("update_item", { list: d.list, item: d.item, field: d.field, value: v }); }
    else if (d.addfield) { const f = prompt("Field name (e.g. status)"); if (!f) return; const v = prompt(f); if (v) this._call("update_item", { list: d.list, item: d.addfield, field: f, value: v }); }
    else if (d.sub) { const v = prompt("Sub-entry"); if (v) this._call("add_items", { list: d.list, items: [v], under: d.sub }); }
    else if (d.del) { this.undo = true; this._call("remove_items", { list: d.list, items: [d.del] }); }
    else if (d.dellist) { if (confirm(`Delete the list ${d.dellist}?`)) { this.undo = true; await this._call("delete_list", { list: d.dellist }); this.panel.go(null); } }
    else if (t.id === "newlist") this.add();
    else if (t.id === "undoremove") { this.undo = false; this._call("undo_remove", {}); }
  }

  async add() { // the sidebar's + (and New list)
    const n = prompt("Name of the new list");
    if (n) { await this._call("create_list", { list: n }); this.panel.go(n); }
  }
}

if (!customElements.get("aac-card-lists")) customElements.define("aac-card-lists", AacListsPage);

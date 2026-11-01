// The Schedule card's page (spec/cards.md "A card's page"): its entries (yours and AAC's; AAC's that wait for your OK
// first), each edited in place (saved when a field is left), and what woke the chain with what came back.

const DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

class AacSchedulePage extends HTMLElement {
  constructor() {
    super();
    this.addEventListener("change", (e) => { const box = e.target.closest("[data-entry]"); if (box) this._save(box); });
    this.addEventListener("click", async (e) => {
      const t = e.target.closest("button");
      if (!t) return;
      const id = t.closest("[data-entry]")?.dataset.entry;
      if (t.id === "addentry") await this._do("save", { entry: { name: "New", at: "08:00", weekdays: [], text: "" } });
      else if (t.dataset.act === "remove" && (await this.panel.confirm("Remove this entry?", "Remove", true))) await this._do("remove", { id });
      else if (t.dataset.act === "run") await this._do("run", { id });
      else if (t.dataset.act === "allow" || t.dataset.act === "refuse") await this._do("allow", { id, ok: t.dataset.act === "allow" });
    });
  }

  async show() { this.got = await this.panel.ui("entries"); this.render(); }

  event(ev) { if (ev.what === "card" && !this.querySelector(":focus")) this.show(); } // not while you type in it

  async _do(action, args) {
    try { await this.panel.ui(action, args); } catch (err) { this.panel.alert(err.message || err); }
    await this.show();
  }

  _save(box) {
    const v = (k) => box.querySelector(`[data-e="${k}"]`);
    this._do("save", { entry: { id: box.dataset.entry, name: v("name").value, at: v("at").value, text: v("text").value, date: v("date").value || null,
      enabled: v("enabled").checked, weekdays: [...box.querySelectorAll("[data-day]")].filter((x) => x.checked).map((x) => Number(x.dataset.day)) } });
  }

  _entry(e) {
    const { esc } = this.panel;
    return `<div class="card" data-entry="${esc(e.id)}" style="padding:10px">
      ${e.proposed ? `<div class="row" style="gap:8px;margin-bottom:6px"><b>AAC asks for this entry</b><button class="small" data-act="allow">Allow</button><button class="small danger" data-act="refuse">Refuse</button></div>` : ""}
      <div class="row" style="gap:8px"><input data-e="name" value="${esc(e.name)}" placeholder="Name"><input data-e="at" value="${esc(e.at)}" style="width:80px" placeholder="HH:MM">
        <span class="hint" style="white-space:nowrap">${e.by === "aac" ? "made by AAC" : "yours"}</span></div>
      <div class="row" style="margin:6px 0;flex-wrap:wrap;gap:6px">${DAYS.map((d, n) => `<label class="check" style="font-size:12px"><input type="checkbox" data-day="${n}" ${(e.weekdays || []).includes(n) ? "checked" : ""} ${e.date ? "disabled" : ""}>${d}</label>`).join("")}
        <label class="hint" style="margin-left:8px">or once on <input type="date" data-e="date" value="${esc(e.date || "")}" style="width:auto"></label></div>
      <textarea rows="2" data-e="text" placeholder="What to do then (a reminder: what to say)">${esc(e.text || "")}</textarea>
      <div class="row" style="gap:8px;margin-top:6px"><label class="check"><input type="checkbox" data-e="enabled" ${e.enabled !== false ? "checked" : ""}>on</label>
        <span class="hint">${e.date ? "once" : (e.weekdays || []).length ? "" : "every day"}</span>
        <button class="small" data-act="run" style="margin-left:auto" title="Wake the chain with it now">Run now</button><button class="small danger" data-act="remove">Remove</button></div></div>`;
  }

  render() {
    const { esc, md, header, tip } = this.panel;
    if (!this.got) { this.innerHTML = `${header(this.card.title)}<div class="content"><p class="hint">Loading…</p></div>`; return; }
    const log = [...(this.got.log || [])].reverse();
    const asks = this.got.plans === "ask" ? "AAC's new entries wait for your OK here." : this.got.plans === "off" ? "AAC may not make entries (Permissions)." : "AAC may make entries of its own (Permissions).";
    this.innerHTML = `${header(this.card.title, tip("Its entries wake the chain at their time; they are also on AAC's calendar. A model may add, change and remove only its own entries (at most 20), as the Permissions page allows. Fields save when you leave them."))}
      <div class="content"><p class="hint">${esc(asks)}</p>${(this.got.entries || []).map((e) => this._entry(e)).join("") || '<p class="hint">No entries yet.</p>'}
        <button class="small" id="addentry">Add an entry</button>
        <h3>What woke the chain</h3>${log.map((x) => `<div class="card"><div class="hint">${esc(new Date(x.time).toLocaleString())} · ${esc(x.name)}${x.unanswered ? " · <b>unanswered</b>" : ""}</div>
        <div style="margin:4px 0">${md(x.text.split("\n(It is due now.")[0])}</div><div>${x.failed ? `<span class="warn">${esc(x.failed)}</span>` : x.answer === null || x.answer === undefined ? '<span class="hint">working…</span>' : x.answer ? md(x.answer) : '<span class="hint">quiet</span>'}</div></div>`).join("") || '<p class="hint">Nothing yet.</p>'}</div>`;
  }
}

if (!customElements.get("aac-card-schedule")) customElements.define("aac-card-schedule", AacSchedulePage);

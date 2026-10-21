// Memory module: what AAC remembers (browse, search, confirm, edit, forget), its facts and "Remember".
// On/off is the module's switch, the embedding model the pool role "memory" (SETTINGS → Compute).
// No shadow DOM, so the panel's styles and skins apply. The panel sets .ws(), .panel and calls .refresh().

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const ask = (el, text, value) => (el.panel?._prompt ? el.panel._prompt(text, value ?? "") : Promise.resolve(window.prompt(text, value ?? "")));
const sure = (el, text, ok) => (el.panel?._confirm ? el.panel._confirm(text, ok, true) : Promise.resolve(window.confirm(text)));
const fmtTime = (iso) => {
  if (!iso) return "";
  const d = new Date(iso), now = new Date();
  const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === now.toDateString() ? t : `${d.toLocaleDateString([], { day: "2-digit", month: "2-digit" })} ${t}`;
};

class AacMemoryPage extends HTMLElement {
  constructor() {
    super();
    this._query = "";
    this._openOnly = false;
    this.addEventListener("click", (e) => this._click(e));
    this.addEventListener("change", (e) => {
      if (e.target.id === "mem-open") { this._openOnly = e.target.checked; this._query = ""; this.refresh(); }
    });
    this.addEventListener("keydown", (e) => { if (e.target.id === "mem-q" && e.key === "Enter") this._search(); });
  }

  connectedCallback() { this.refresh(); }

  async refresh() {
    if (!this.ws) return;
    try {
      [this._facts, this._memory] = await Promise.all([
        this.ws("aac/facts").catch(() => ({ keys: [] })),
        this.ws("aac/memory/list", this._query ? { query: this._query } : { unconfirmed: this._openOnly }),
      ]);
      this._error = null;
    } catch (e) { this._error = e.message || String(e); }
    this._render();
  }

  _search() {
    this._query = this.querySelector("#mem-q").value.trim();
    this._openOnly = false;
    this.refresh();
  }

  async _click(e) {
    const t = e.target.closest("button, [data-fact]");
    if (!t) return;
    if (t.dataset.go) return this.panel?._go(t.dataset.go);
    if (t.id === "mem-search") return this._search();
    if (t.id === "mem-reembed") {
      this._note = { ok: true, text: "Embedding…" }; this._render();
      try { const r = await this.ws("aac/memory/reembed"); this._note = { ok: true, text: `Done: ${r.embedded} memories got vectors.` }; }
      catch (err) { this._note = { ok: false, text: err.message || String(err) }; }
      return this.refresh();
    }
    if (t.id === "mem-add") {
      const text = this.querySelector("#mem-text").value.trim();
      if (!text) return;
      await this.ws("aac/memory/save", { text, kind: this.querySelector("#mem-kind").value });
      this._query = "";
      return this.refresh();
    }
    if (t.dataset.fact) {
      if (this._factOpen === t.dataset.fact) { this._factOpen = null; return this._render(); }
      const [subject, attribute] = t.dataset.fact.split("|");
      this._factOpen = t.dataset.fact;
      this._series = (await this.ws("aac/facts", { subject, attribute })).series;
      return this._render();
    }
    if (t.dataset.confirm) { await this.ws("aac/memory/save", { memory_id: t.dataset.confirm, confirmed: true }); return this.refresh(); }
    if (t.dataset.edit) {
      const item = this._memory.memories.find((x) => x.id === t.dataset.edit);
      const text = await ask(this, "Edit memory", item?.text || "");
      if (!text || !text.trim()) return;
      await this.ws("aac/memory/save", { memory_id: t.dataset.edit, text: text.trim(), confirmed: true });
      return this.refresh();
    }
    if (t.dataset.forget) {
      if (!(await sure(this, "Forget this memory?", "Forget"))) return;
      await this.ws("aac/memory/delete", { memory_id: t.dataset.forget });
      return this.refresh();
    }
  }

  _render() {
    const m = this._memory;
    if (!m) { this.innerHTML = `<div class="card"><p>${this._error ? esc(this._error) : "Loading…"}</p></div>`; return; }
    const stats = m.stats || {};
    const role = this.panel?._state?.pools?.roles?.memory;
    const model = role?.items?.length ? (role.items.find((i) => i.id === role.active) || role.items[0]).name || role.items[0].id : "none (keywords only)";
    const rows = !m.memories.length ? `<p class="hint">${this._query ? "Nothing found." : "Nothing remembered yet."}</p>` :
      m.memories.map((x) => `
        <div class="src">
          <span class="dot ${x.confirmed ? "ok" : "open"}"></span>
          <div style="flex:1;min-width:0">
            <div class="n">${esc(x.text)}</div>
            <div class="m">${esc(x.kind)} · ${esc(x.source)} · ${esc(fmtTime(x.updated))}${x.embedded ? "" : " · no vector"}${x.confirmed ? "" : " · <b style='color:var(--amber)'>unconfirmed</b>"}</div>
          </div>
          <span class="actions" style="margin:0">
            ${x.confirmed ? "" : `<button class="btn primary" data-confirm="${esc(x.id)}">Confirm</button>`}
            <button class="btn" data-edit="${esc(x.id)}">Edit</button>
            <button class="btn" data-forget="${esc(x.id)}" title="Forget">Forget</button>
          </span>
        </div>`).join("");
    const facts = !this._facts?.keys?.length ? '<p class="hint">No facts yet.</p>' : this._facts.keys.map((k) => {
      const sm = k.summary || {};
      const open = this._factOpen === `${k.subject}|${k.attribute}`;
      const val = sm.change !== undefined ? `${sm.first} → ${sm.latest}${sm.unit ? " " + sm.unit : ""} (${sm.change > 0 ? "+" : ""}${sm.change})` : `${sm.latest}${sm.unit ? " " + sm.unit : ""}`;
      return `<div class="src" style="cursor:pointer" data-fact="${esc(k.subject)}|${esc(k.attribute)}" tabindex="0" role="button">
        <span class="dot ok"></span>
        <div style="flex:1;min-width:0">
          <div class="n">${esc(k.subject)} · ${esc(k.attribute)}: <b>${esc(val)}</b></div>
          <div class="m">${esc(k.count)} value(s) · last ${esc(fmtTime(k.last))}</div>
          ${open && this._series ? `<div class="m" style="margin-top:.4em">${this._series.map((f) => `${esc(f.time.slice(0, 10))}: ${esc(f.value)}${f.unit ? " " + esc(f.unit) : ""}`).join(" · ")}</div>` : ""}
        </div>
      </div>`;
    }).join("");
    this.innerHTML = `
      <div class="cards">
        <div class="card"><h2>MEMORY</h2>
          <p class="hint">One local file in Home Assistant's config folder (included in HA backups). Search uses keywords and, with an embedding model, meaning ("cellar" finds "basement").</p>
          <div class="kv"><span class="k">Embedding model</span><span class="v">${esc(model)}</span><span class="k">Stored</span><span class="v">${esc(stats.total ?? "…")}</span><span class="k">With vectors</span><span class="v">${esc(stats.embedded ?? "…")}</span><span class="k">Unconfirmed</span><span class="v">${esc(stats.unconfirmed ?? "…")}</span></div>
          <div class="actions"><button class="btn" id="mem-reembed">Re-embed all</button><button class="btn" data-go="models">Compute</button><button class="btn" data-go="settings/module/memory">Settings</button></div>
          ${this._note ? `<div class="msg ${this._note.ok ? "ok" : "err"}">${esc(this._note.text)}</div>` : ""}
        </div>
        <div class="card"><h2>REMEMBER</h2>
          <p class="hint">Tell AAC something directly. "agent believes" entries are AAC's own conclusions; they count as facts only once you confirm them.</p>
          <label for="mem-text">Text</label><textarea id="mem-text" class="thin-scroll" style="min-height:5em" placeholder="e.g. Oat milk from brand X was good."></textarea>
          <label for="mem-kind">Kind ("plan" = something you want to achieve; AAC keeps it in mind when it looks around on its own)</label>
          <select id="mem-kind">${["note", "fact", "preference", "event", "plan"].map((k) => `<option>${k}</option>`).join("")}</select>
          <div class="actions"><button class="btn primary" id="mem-add">Remember</button></div>
        </div>
      </div>
      <div class="card"><h2>FACTS</h2>
        <p class="hint">Things with a value (measurements, places, ratings), stored with fields so they can be compared exactly. Each one is also a text memory below.</p>
        ${facts}
      </div>
      <div class="card"><h2>BROWSE</h2>
        <div class="row">
          <div><input id="mem-q" value="${esc(this._query)}" placeholder="Search by words or meaning…" aria-label="Search memories"></div>
          <div style="flex:none" class="actions"><button class="btn" id="mem-search">Search</button></div>
        </div>
        <label class="check"><input type="checkbox" id="mem-open" ${this._openOnly ? "checked" : ""}>Only unconfirmed</label>
        ${rows}
      </div>`;
  }
}

if (!customElements.get("aac-memory-page")) customElements.define("aac-memory-page", AacMemoryPage);

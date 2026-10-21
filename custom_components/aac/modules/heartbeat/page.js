// Heartbeat module: its last check-ins and "Run once now". The settings live on the module's settings
// page (the panel builds that form from the manifest). No shadow DOM, so the panel's styles and skins apply.
// The panel sets .ws() and .panel.

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const DOT = { running: "run", done: "ok", silent: "silent", failed: "bad" };
const fmtTime = (iso) => {
  if (!iso) return "";
  const d = new Date(iso), now = new Date();
  const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === now.toDateString() ? t : `${d.toLocaleDateString([], { day: "2-digit", month: "2-digit" })} ${t}`;
};

class AacHeartbeatPage extends HTMLElement {
  constructor() {
    super();
    this.addEventListener("click", (e) => this._click(e));
  }

  connectedCallback() { this.refresh(); }

  async refresh() {
    if (!this.ws) return;
    try { this._data = await this.ws("aac/heartbeat/info"); this._error = null; } catch (e) { this._error = e.message || String(e); }
    this._render();
  }

  async _click(e) {
    const go = e.target.closest("[data-hb-go]");
    if (go) { this.panel?._go(go.dataset.hbGo); return; }
    if (e.target.closest("#hb-run")) {
      this._note = "Heartbeat running…";
      this._render();
      try {
        const r = await this.ws("aac/heartbeat/run");
        this._note = null;
        await this.refresh();
        this.panel?._go(`run/${r.run_id}`);
      } catch (err) { this._note = `Not run: ${err.message || err}`; this._render(); }
    }
  }

  _render() {
    const d = this._data;
    if (!d) { this.innerHTML = `<div class="card"><p>${this._error ? esc(this._error) : "Loading…"}</p></div>`; return; }
    const said = d.runs.filter((r) => r.status === "done").length;
    const rows = d.runs.map((r) => `<div class="src">
        <span class="dot ${DOT[r.status] || ""}"></span>
        <div style="flex:1;min-width:0"><div class="n">${esc(fmtTime(r.started))} · ${r.status === "silent" ? "nothing to report" : r.status === "done" ? "said something" : esc(r.status)}</div>
          ${r.status !== "silent" && r.result ? `<div class="m">${esc(r.result.length > 220 ? r.result.slice(0, 220) + "…" : r.result)}</div>` : ""}</div>
        <span class="actions" style="margin:0">${r.chat_id ? `<button class="btn" data-hb-go="chat/${esc(r.chat_id)}">Chat</button>` : ""}${r.chat_deleted ? '<span class="hint">chat deleted</span>' : ""}<button class="btn" data-hb-go="run/${esc(r.id)}">Details</button></span>
      </div>`).join("");
    const when = d.enabled ? `every ${esc(d.interval)} min, quiet ${esc(d.quiet_start)}–${esc(d.quiet_end)}, model: ${esc(d.tier)}${d.tools ? ", with tools" : ""}` : "off in this domain";
    this.innerHTML = `<div class="cards">
      <div class="card"><h2>HEARTBEAT</h2>
        <p>${when}</p>
        <div class="actions"><button class="btn" id="hb-run">Run once now</button><button class="btn" data-hb-go="settings/module/heartbeat">Settings</button></div>
        ${this._note ? `<div class="msg ok">${esc(this._note)}</div>` : ""}
      </div>
      <div class="card"><h2>LAST RUNS <span class="tag">${d.runs.length}</span>${said ? ` <span class="tag aac">${said} SAID SOMETHING</span>` : ""}</h2>
        ${rows || '<p class="hint">No heartbeat yet.</p>'}
      </div>
    </div>`;
  }
}

if (!customElements.get("aac-heartbeat-page")) customElements.define("aac-heartbeat-page", AacHeartbeatPage);

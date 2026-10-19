// Calendar module: an agenda over every calendar provider (lists with dates, AAC's plans, HA calendars).
// No shadow DOM, so the panel's styles and skins apply. The panel sets .ws(type, data).

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

class AacCalendarPage extends HTMLElement {
  connectedCallback() { if (!this._data) this.refresh(); else this._render(); }

  async refresh() {
    if (!this.ws) return;
    try { this._data = await this.ws("aac/calendar/agenda", { days: this._days || 14 }); this._error = null; }
    catch (e) { this._error = e.message || String(e); }
    this._render();
    this.querySelector("#cal-days")?.addEventListener("change", (e) => { this._days = Number(e.target.value); this.refresh(); });
  }

  _render() {
    if (!this._data) { this.innerHTML = `<div class="card"><p>${this._error ? esc(this._error) : "Loading…"}</p></div>`; return; }
    const days = {};
    const undated = [];
    for (const e of this._data.entries) {
      if (!e.start) { undated.push(e); continue; }
      const d = new Date(e.start);
      (days[d.toDateString()] ||= { date: d, items: [] }).items.push(e);
    }
    const label = (e) => ({ core: "AAC plan", lists: "list", calendar: "HA calendar" })[e.source] || e.source;
    const time = (e) => e.all_day ? "all day" : new Date(e.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const row = (e) => `<div class="src"><span class="dot ${e.done ? "" : "ok"}"></span>
        <div style="flex:1;min-width:0"><div class="n" style="${e.done ? "text-decoration:line-through;opacity:.6" : ""}">${esc(e.name)}</div>
        <div class="m">${esc(time(e))} · ${esc(label(e))}${(e.tags || []).length ? ` · ${esc(e.tags.join(" › "))}` : ""}${e.data?.location ? ` · ${esc(e.data.location)}` : ""}</div></div></div>`;
    const sorted = Object.values(days).sort((a, b) => a.date - b.date);
    this.innerHTML = `
      <div class="card"><h2>AGENDA</h2>
        <p>Everything with a date, from every module: your lists, AAC's own plans and reminders, and your Home Assistant calendars. Ask AAC to put something on the calendar; where new entries go is set under SETTINGS → Modules.</p>
        <div class="row"><div style="flex:none;min-width:12em"><label for="cal-days">Days ahead</label>
          <select id="cal-days">${[7, 14, 30, 90].map((n) => `<option value="${n}" ${n === (this._days || 14) ? "selected" : ""}>${n}</option>`).join("")}</select></div></div>
      </div>
      ${sorted.length ? sorted.map((d) => `<div class="card"><h2>${esc(d.date.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" }))}</h2>${d.items.map(row).join("")}</div>`).join("")
        : '<div class="card"><p class="hint">Nothing coming up.</p></div>'}
      ${this._error ? `<div class="msg err">${esc(this._error)}</div>` : ""}`;
  }
}

if (!customElements.get("aac-calendar-page")) customElements.define("aac-calendar-page", AacCalendarPage);

// Calendar module: one calendar over every provider (lists with dates, AAC's plans, HA calendars).
// Views: week grid (days × hours), table, month, agenda. View and number of days are module settings
// (same on every device). No shadow DOM, so the panel's styles and skins apply. The panel sets .ws().

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const VIEWS = [["week", "Week grid"], ["table", "Table"], ["month", "Month"], ["agenda", "Agenda"]];
const DAY = 86400000;
const dayKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
// calendar days, not 24-hour steps (the clock-change day has 23 or 25 hours)
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);

class AacCalendarPage extends HTMLElement {
  constructor() {
    super();
    this.addEventListener("click", (e) => this._click(e));
    this.addEventListener("change", (e) => this._change(e));
  }

  connectedCallback() { if (!this._data) this.refresh(); else this._render(); }

  _range() {
    const view = this._view || this._data?.view || "week";
    let start = this._start || startOfDay(new Date());
    let days = Math.min(31, Math.max(1, this._days || this._data?.days || 7));
    if (view === "month") {
      const first = new Date(start.getFullYear(), start.getMonth(), 1);
      start = addDays(first, -((first.getDay() + 6) % 7)); // back to Monday
      days = 42;
    }
    return { view, start, days };
  }

  async refresh() {
    if (!this.ws) return;
    try {
      if (!this._view) { // the saved view and days first, so the first fetch covers the right range
        const saved = await this.ws("aac/calendar/agenda", { days: 1 });
        this._view = saved.view || "week";
        this._days = this._days || saved.days;
      }
      const { start, days } = this._range();
      this._data = await this.ws("aac/calendar/agenda", { start: dayKey(start), days });
      this._error = null;
    } catch (e) { this._error = e.message || String(e); }
    this._render();
  }

  async _save(values) {
    try { await this.ws("aac/module/settings", { module: "calendar", values }); } catch (e) { /* the view still changes */ }
  }

  _entriesByDay() {
    const by = {};
    for (const e of this._data.entries) {
      if (!e.start) continue;
      const first = startOfDay(new Date(e.start));
      const last = e.end && e.all_day ? new Date(new Date(e.end).getTime() - 1) : first; // all-day spans
      for (let d = first; d <= last; d = addDays(d, 1)) (by[dayKey(d)] ||= []).push(e);
    }
    return by;
  }

  _label(e) { return ({ core: "AAC plan", lists: "list", calendar: (e.tags || [])[0] || "HA calendar" })[e.source] || e.source; }
  _time(e) { return e.all_day ? "all day" : new Date(e.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
  _chip(e) {
    return `<div class="cchip ${e.source === "calendar" ? "ha" : e.source}" title="${esc(`${e.name} · ${this._time(e)} · ${this._label(e)}`)}">${e.all_day ? "" : `<b>${esc(this._time(e))}</b> `}${esc(e.name)}</div>`;
  }

  _week(start, days) {
    const by = this._entriesByDay(), cols = [...Array(days)].map((_, i) => addDays(start, i));
    const timed = this._data.entries.filter((e) => e.start && !e.all_day).map((e) => new Date(e.start).getHours());
    const from = Math.min(7, ...timed), to = Math.max(21, ...timed);
    const today = dayKey(new Date());
    const head = cols.map((d) => `<th class="${dayKey(d) === today ? "today" : ""}">${esc(d.toLocaleDateString([], { weekday: "short" }))}<br><span>${esc(d.toLocaleDateString([], { day: "numeric", month: "short" }))}</span></th>`).join("");
    const allDay = cols.map((d) => `<td>${(by[dayKey(d)] || []).filter((e) => e.all_day).map((e) => this._chip(e)).join("")}</td>`).join("");
    const rows = [];
    for (let h = from; h <= to; h++) {
      rows.push(`<tr><th class="hour">${String(h).padStart(2, "0")}:00</th>${cols.map((d) => `<td>${(by[dayKey(d)] || [])
        .filter((e) => !e.all_day && new Date(e.start).getHours() === h).map((e) => this._chip(e)).join("")}</td>`).join("")}</tr>`);
    }
    return `<div class="cscroll"><table class="cgrid"><thead><tr><th></th>${head}</tr></thead>
      <tbody><tr class="allday"><th class="hour">all day</th>${allDay}</tr>${rows.join("")}</tbody></table></div>`;
  }

  _table() {
    const rows = this._data.entries.filter((e) => e.start).map((e) => {
      const d = new Date(e.start);
      return `<tr><td>${esc(d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" }))}</td><td>${esc(this._time(e))}</td>
        <td class="n">${esc(e.name)}</td><td>${esc(this._label(e))}</td><td class="m">${esc(e.data?.location || e.data?.notes || e.data?.instruction || "")}</td></tr>`;
    }).join("");
    return rows ? `<div class="cscroll"><table class="ctable"><thead><tr><th>Day</th><th>Time</th><th>Entry</th><th>Calendar</th><th>Notes</th></tr></thead><tbody>${rows}</tbody></table></div>`
      : '<p class="hint">Nothing in this period.</p>';
  }

  _month(start) {
    const by = this._entriesByDay(), month = addDays(start, 15).getMonth(), today = dayKey(new Date());
    const names = [...Array(7)].map((_, i) => addDays(start, i).toLocaleDateString([], { weekday: "short" }));
    const weeks = [...Array(6)].map((_, w) => `<tr>${[...Array(7)].map((_, i) => {
      const d = addDays(start, w * 7 + i), items = by[dayKey(d)] || [];
      return `<td class="${d.getMonth() !== month ? "other" : ""} ${dayKey(d) === today ? "today" : ""}"><div class="dnum">${d.getDate()}</div>
        ${items.slice(0, 4).map((e) => this._chip(e)).join("")}${items.length > 4 ? `<div class="hint">+${items.length - 4} more</div>` : ""}</td>`;
    }).join("")}</tr>`).join("");
    return `<div class="cscroll"><table class="cmonth"><thead><tr>${names.map((n) => `<th>${esc(n)}</th>`).join("")}</tr></thead><tbody>${weeks}</tbody></table></div>`;
  }

  _agenda() {
    const by = this._entriesByDay();
    const days = Object.keys(by).sort();
    return days.length ? days.map((k) => `<h3 class="cday">${esc(new Date(k + "T12:00").toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" }))}</h3>
      ${by[k].map((e) => `<div class="src"><span class="dot ok"></span><div style="flex:1;min-width:0"><div class="n">${esc(e.name)}</div>
        <div class="m">${esc(this._time(e))} · ${esc(this._label(e))}${e.data?.location ? ` · ${esc(e.data.location)}` : ""}</div></div></div>`).join("")}`).join("")
      : '<p class="hint">Nothing in this period.</p>';
  }

  _render() {
    if (!this._data) { this.innerHTML = `<div class="card"><p>${this._error ? esc(this._error) : "Loading…"}</p></div>`; return; }
    const { view, start, days } = this._range();
    const end = addDays(start, days - 1);
    const title = view === "month"
      ? addDays(start, 15).toLocaleDateString([], { month: "long", year: "numeric" })
      : `${start.toLocaleDateString([], { day: "numeric", month: "short" })} – ${end.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" })}`;
    const body = view === "week" ? this._week(start, days) : view === "table" ? this._table() : view === "month" ? this._month(start) : this._agenda();
    this.innerHTML = `
      <style>
        aac-calendar-page .cbar { display: flex; flex-wrap: wrap; gap: .6em; align-items: center; margin-bottom: .9em; }
        aac-calendar-page .cbar .grow { flex: 1; font-weight: 600; min-width: 10em; }
        aac-calendar-page .cbar select, aac-calendar-page .cbar input { width: auto; min-height: 0; padding: .35em .5em; }
        aac-calendar-page .cscroll { overflow-x: auto; }
        aac-calendar-page table { border-collapse: collapse; width: 100%; font-size: .9em; }
        aac-calendar-page th, aac-calendar-page td { border: 1px solid color-mix(in srgb, var(--line) 60%, transparent); padding: .3em .4em; vertical-align: top; text-align: left; }
        aac-calendar-page th { font-weight: 600; color: var(--dim); }
        aac-calendar-page th span { font-weight: 400; font-size: .85em; }
        aac-calendar-page .cgrid td { min-width: 7.5em; height: 2.2em; }
        aac-calendar-page .cgrid th.hour { width: 4em; font-size: .8em; white-space: nowrap; }
        aac-calendar-page .cgrid tr.allday td { background: color-mix(in srgb, var(--line) 12%, transparent); }
        aac-calendar-page .today { background: color-mix(in srgb, var(--magenta) 10%, transparent); color: var(--text); }
        aac-calendar-page .cmonth, aac-calendar-page .cgrid { table-layout: fixed; }
        aac-calendar-page .cgrid th:first-child { width: 4.5em; }
        aac-calendar-page .cmonth td { height: 6.5em; }
        aac-calendar-page .cmonth td.other { opacity: .45; }
        aac-calendar-page .dnum { font-size: .8em; color: var(--dim); margin-bottom: .2em; }
        aac-calendar-page .cchip { font-size: .82em; padding: .1em .35em; margin: .12em 0; border-radius: .3em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
          background: color-mix(in srgb, var(--cyan) 14%, transparent); border-left: 3px solid var(--cyan); }
        aac-calendar-page .cchip.lists { background: color-mix(in srgb, var(--green) 14%, transparent); border-left-color: var(--green); }
        aac-calendar-page .cchip.core { background: color-mix(in srgb, var(--magenta) 14%, transparent); border-left-color: var(--magenta); }
        aac-calendar-page .ctable td.n { font-weight: 500; }
        aac-calendar-page .ctable td.m { color: var(--dim); }
        aac-calendar-page h3.cday { font-size: .95em; margin: 1em 0 .2em; }
      </style>
      <div class="card">
        <div class="cbar">
          <button class="btn" data-nav="-1" title="Back">◀</button>
          <button class="btn" data-nav="0">Today</button>
          <button class="btn" data-nav="1" title="Forward">▶</button>
          <span class="grow">${esc(title)}</span>
          <select id="cal-view" aria-label="View">${VIEWS.map(([v, l]) => `<option value="${v}" ${v === view ? "selected" : ""}>${l}</option>`).join("")}</select>
          ${view === "month" ? "" : `<label class="check" style="margin:0">Days <input id="cal-days" type="number" min="1" max="31" value="${days}" style="width:4.5em"></label>`}
        </div>
        ${body}
        <p class="hint" style="margin-top:.8em">Colours: <span class="cchip ha" style="display:inline-block">HA calendars</span> <span class="cchip lists" style="display:inline-block">lists</span> <span class="cchip core" style="display:inline-block">AAC's plans</span> · which calendars, the view and the days are set under SETTINGS → Modules → Calendar.</p>
        ${this._error ? `<div class="msg err">${esc(this._error)}</div>` : ""}
      </div>`;
  }

  _click(e) {
    const nav = e.target.closest("[data-nav]")?.dataset.nav;
    if (nav === undefined) return;
    const { view, start, days } = this._range();
    if (nav === "0") this._start = null;
    else if (view === "month") {
      const mid = addDays(start, 15);
      this._start = new Date(mid.getFullYear(), mid.getMonth() + Number(nav), 1);
    } else this._start = addDays(start, Number(nav) * days);
    this.refresh();
  }

  _change(e) {
    if (e.target.id === "cal-view") { this._view = e.target.value; this._save({ view: this._view }); this.refresh(); }
    if (e.target.id === "cal-days") {
      this._days = Math.min(31, Math.max(1, Number(e.target.value) || 7));
      this._save({ days: this._days });
      this.refresh();
    }
  }
}

if (!customElements.get("aac-calendar-page")) customElements.define("aac-calendar-page", AacCalendarPage);

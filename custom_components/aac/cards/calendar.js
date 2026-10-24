// The Calendar card's page (spec/cards.md "A card's page"; v1's calendar): its own entries, the dated entries of
// lists and the Home Assistant calendars picked in its settings. Views: week grid, table, month, agenda; the view
// and the number of days are the card's settings (the same on every device).

const VIEWS = [["week", "Week grid"], ["table", "Table"], ["month", "Month"], ["agenda", "Agenda"]];
const dayKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n); // calendar days (clock changes)
const STYLE = `
  aac-card-calendar .cbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
  aac-card-calendar .cbar .grow { flex: 1; font-weight: 600; min-width: 10em; }
  aac-card-calendar .cbar select, aac-card-calendar .cbar input { width: auto; }
  aac-card-calendar .cscroll { overflow-x: auto; }
  aac-card-calendar table { border-collapse: collapse; width: 100%; font-size: 13px; }
  aac-card-calendar th, aac-card-calendar td { border: 1px solid var(--divider-color); padding: 4px 5px; vertical-align: top; text-align: left; }
  aac-card-calendar th { font-weight: 600; color: var(--secondary-text-color); }
  aac-card-calendar th span { font-weight: 400; font-size: .85em; }
  aac-card-calendar .cgrid, aac-card-calendar .cmonth { table-layout: fixed; }
  aac-card-calendar .cgrid td { min-width: 7.5em; height: 2.2em; }
  aac-card-calendar .cgrid th.hour { width: 4.5em; font-size: .8em; white-space: nowrap; }
  aac-card-calendar .cgrid tr.allday td { background: rgba(127,127,127,.06); }
  aac-card-calendar .today { background: rgba(3,169,244,.1); }
  aac-card-calendar .cmonth td { height: 6.5em; }
  aac-card-calendar .cmonth td.other { opacity: .45; }
  aac-card-calendar .dnum { font-size: .8em; color: var(--secondary-text-color); margin-bottom: 2px; }
  aac-card-calendar .cchip { font-size: .85em; padding: 1px 5px; margin: 2px 0; border-radius: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    background: rgba(3,169,244,.14); border-left: 3px solid var(--primary-color); }
  aac-card-calendar .cchip.lists { background: rgba(76,175,80,.14); border-left-color: #4caf50; }
  aac-card-calendar .cchip.own { background: rgba(156,39,176,.14); border-left-color: #9c27b0; }
  aac-card-calendar .cchip.error { background: rgba(219,68,55,.14); border-left-color: var(--error-color, #db4437); }
  aac-card-calendar h3.cday { font-size: 14px; margin: 14px 0 4px; }
  aac-card-calendar .agenda { display: flex; gap: 8px; padding: 4px 0; border-bottom: 1px solid var(--divider-color); }`;

class AacCalendarPage extends HTMLElement {
  constructor() {
    super();
    this.addEventListener("click", (e) => this._click(e));
    this.addEventListener("change", (e) => this._change(e));
  }

  async show() { await this.refresh(); }

  event(ev) { if (ev.what === "card") this.refresh(); }

  _range() {
    const view = this._view || this.card.settings.view || "week";
    let start = this._start || startOfDay(new Date());
    let days = Math.min(31, Math.max(1, this._days || Number(this.card.settings.days) || 7));
    if (view === "month") {
      const first = new Date(start.getFullYear(), start.getMonth(), 1);
      start = addDays(first, -((first.getDay() + 6) % 7)); // back to Monday
      days = 42;
    }
    return { view, start, days };
  }

  async refresh() {
    try {
      const { start, days } = this._range();
      this.data = await this.panel.ui("agenda", { start: dayKey(start), days });
      this.error = null;
    } catch (e) { this.error = e.message || String(e); }
    this.render();
  }

  _byDay() {
    const by = {};
    for (const e of this.data.entries) {
      const first = startOfDay(new Date(e.start.length === 10 ? `${e.start}T12:00` : e.start));
      const last = e.all_day && e.end ? addDays(startOfDay(new Date(e.end.length === 10 ? `${e.end}T12:00` : e.end)), -1) : first; // all-day spans
      for (let d = first; d <= last; d = addDays(d, 1)) (by[dayKey(d)] ||= []).push(e);
    }
    return by;
  }

  _label(e) { return e.source === "ha" ? e.calendar : e.source === "lists" ? "list" : e.source === "own" ? this.card.title : "error"; }
  _time(e) { return e.all_day ? "all day" : new Date(e.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
  _chip(e) {
    const { esc } = this.panel;
    return `<div class="cchip ${esc(e.source)}" title="${esc(`${e.name} · ${this._time(e)} · ${this._label(e)}`)}">${e.all_day ? "" : `<b>${esc(this._time(e))}</b> `}${esc(e.name)}</div>`;
  }

  _week(start, days) {
    const { esc } = this.panel, by = this._byDay(), cols = [...Array(days)].map((_, i) => addDays(start, i)), today = dayKey(new Date());
    const timed = this.data.entries.filter((e) => !e.all_day).map((e) => new Date(e.start).getHours());
    const from = Math.min(7, ...timed), to = Math.max(21, ...timed);
    const head = cols.map((d) => `<th class="${dayKey(d) === today ? "today" : ""}">${esc(d.toLocaleDateString([], { weekday: "short" }))}<br><span>${esc(d.toLocaleDateString([], { day: "numeric", month: "short" }))}</span></th>`).join("");
    const allDay = cols.map((d) => `<td>${(by[dayKey(d)] || []).filter((e) => e.all_day).map((e) => this._chip(e)).join("")}</td>`).join("");
    const rows = [];
    for (let h = from; h <= to; h++) {
      rows.push(`<tr><th class="hour">${String(h).padStart(2, "0")}:00</th>${cols.map((d) => `<td>${(by[dayKey(d)] || [])
        .filter((e) => !e.all_day && new Date(e.start).getHours() === h).map((e) => this._chip(e)).join("")}</td>`).join("")}</tr>`);
    }
    return `<div class="cscroll"><table class="cgrid"><thead><tr><th class="hour"></th>${head}</tr></thead>
      <tbody><tr class="allday"><th class="hour">all day</th>${allDay}</tr>${rows.join("")}</tbody></table></div>`;
  }

  _table() {
    const { esc } = this.panel;
    const rows = this.data.entries.map((e) => `<tr><td>${esc(new Date(e.start.length === 10 ? `${e.start}T12:00` : e.start).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" }))}</td>
      <td>${esc(this._time(e))}</td><td><b>${esc(e.name)}</b></td><td>${esc(this._label(e))}</td><td class="hint">${esc(e.notes || "")}</td></tr>`).join("");
    return rows ? `<div class="cscroll"><table><thead><tr><th>Day</th><th>Time</th><th>Entry</th><th>Calendar</th><th>Notes</th></tr></thead><tbody>${rows}</tbody></table></div>`
      : '<p class="hint">Nothing in this period.</p>';
  }

  _month(start) {
    const { esc } = this.panel, by = this._byDay(), month = addDays(start, 15).getMonth(), today = dayKey(new Date());
    const names = [...Array(7)].map((_, i) => addDays(start, i).toLocaleDateString([], { weekday: "short" }));
    const weeks = [...Array(6)].map((_, w) => `<tr>${[...Array(7)].map((_, i) => {
      const d = addDays(start, w * 7 + i), items = by[dayKey(d)] || [];
      return `<td class="${d.getMonth() !== month ? "other" : ""} ${dayKey(d) === today ? "today" : ""}"><div class="dnum">${d.getDate()}</div>
        ${items.slice(0, 4).map((e) => this._chip(e)).join("")}${items.length > 4 ? `<div class="hint">+${items.length - 4} more</div>` : ""}</td>`;
    }).join("")}</tr>`).join("");
    return `<div class="cscroll"><table class="cmonth"><thead><tr>${names.map((n) => `<th>${esc(n)}</th>`).join("")}</tr></thead><tbody>${weeks}</tbody></table></div>`;
  }

  _agenda() {
    const { esc } = this.panel, by = this._byDay(), days = Object.keys(by).sort();
    return days.length ? days.map((k) => `<h3 class="cday">${esc(new Date(`${k}T12:00`).toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" }))}</h3>
      ${by[k].map((e) => `<div class="agenda">${this._chip(e)}<span class="hint">${esc(this._time(e))} · ${esc(this._label(e))}${e.notes ? ` · ${esc(e.notes)}` : ""}</span></div>`).join("")}`).join("")
      : '<p class="hint">Nothing in this period.</p>';
  }

  render() {
    const { esc, header, tip } = this.panel;
    if (!this.data) { this.innerHTML = `${header(this.card.title)}<div class="content"><p class="hint">${this.error ? esc(this.error) : "Loading…"}</p></div>`; return; }
    const { view, start, days } = this._range(), end = addDays(start, days - 1);
    const title = view === "month" ? addDays(start, 15).toLocaleDateString([], { month: "long", year: "numeric" })
      : `${start.toLocaleDateString([], { day: "numeric", month: "short" })} – ${end.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" })}`;
    const body = view === "week" ? this._week(start, days) : view === "table" ? this._table() : view === "month" ? this._month(start) : this._agenda();
    this.innerHTML = `<style>${STYLE}</style>${header(this.card.title, tip("Its own entries, the dated entries of lists (a field when/date/due) and the Home Assistant calendars picked in its settings (Nodes → the card)."))}
      <div class="content"><div class="card">
        <div class="cbar"><button class="small" data-nav="-1" title="Back">◀</button><button class="small" data-nav="0">Today</button><button class="small" data-nav="1" title="Forward">▶</button>
          <span class="grow">${esc(title)}</span>
          <select id="cal-view">${VIEWS.map(([v, l]) => `<option value="${v}" ${v === view ? "selected" : ""}>${l}</option>`).join("")}</select>
          ${view === "month" ? "" : `<label class="check" style="margin:0">Days <input id="cal-days" type="number" min="1" max="31" value="${days}" style="width:4.5em"></label>`}</div>
        ${body}
        <p class="hint" style="margin-top:10px">Colours: <span class="cchip ha" style="display:inline-block">HA calendars</span> <span class="cchip lists" style="display:inline-block">lists</span> <span class="cchip own" style="display:inline-block">${esc(this.card.title)}</span></p>
        ${this.error ? `<p class="hint" style="color:var(--error-color)">${esc(this.error)}</p>` : ""}</div></div>`;
  }

  _click(e) {
    const nav = e.target.closest("[data-nav]")?.dataset.nav;
    if (nav === undefined) return;
    const { view, start, days } = this._range();
    if (nav === "0") this._start = null;
    else if (view === "month") { const mid = addDays(start, 15); this._start = new Date(mid.getFullYear(), mid.getMonth() + Number(nav), 1); }
    else this._start = addDays(start, Number(nav) * days);
    this.refresh();
  }

  _change(e) {
    if (e.target.id === "cal-view") { this._view = e.target.value; this.panel.save({ view: this._view }); this.refresh(); }
    if (e.target.id === "cal-days") { this._days = Math.min(31, Math.max(1, Number(e.target.value) || 7)); this.panel.save({ days: this._days }); this.refresh(); }
  }
}

if (!customElements.get("aac-card-calendar")) customElements.define("aac-card-calendar", AacCalendarPage);

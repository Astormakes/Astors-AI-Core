// Triggers module: the owner's triggers and AAC's own plans, with the form to add or change one.
// "AAC may plan on its own" is a module setting (the panel builds that form from the manifest).
// No shadow DOM, so the panel's styles and skins apply. The panel sets .ws(), .panel and calls .refresh().

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const sure = (el, text, ok) => (el.panel?._confirm ? el.panel._confirm(text, ok, true) : Promise.resolve(window.confirm(text)));
const WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];
const PLANS = { allowed: "yes, and tells you", ask: "only with your approval", off: "no" };

const whenText = (when) => {
  if (when.type === "time") {
    const days = when.date ? `once on ${when.date}` : (when.weekdays || []).length ? when.weekdays.map((d) => WEEKDAYS[d]).join(" ") : "every day";
    return `${days} at ${when.at}`;
  }
  if (when.type === "event") {
    const filter = Object.entries(when.event_data || {}).map(([k, v]) => `${k}=${v}`).join(", ");
    return `HA event ${when.event_type}${filter ? ` (${filter})` : ""}`;
  }
  return `${when.entity_id}${when.attribute ? ` · ${when.attribute}` : ""}${when.to ? ` → ${when.to}` : " changes"}`;
};

const blank = () => ({ type: "state", name: "", entity_id: "", attribute: "", to: "", event_type: "", event_data: "", at: "07:30",
  weekdays: [], instruction: "", cooldown: 300, enabled: true, quiet: null, msg: null });

const fromTrigger = (t) => ({
  id: t.id, name: t.name, enabled: t.enabled, cooldown: t.cooldown, quiet: t.quiet_hours,
  type: t.when.type, entity_id: t.when.entity_id || "", attribute: t.when.attribute || "", to: t.when.to || "", at: t.when.at || "07:30",
  event_type: t.when.event_type || "", event_data: Object.entries(t.when.event_data || {}).map(([k, v]) => `${k}=${v}`).join(", "),
  weekdays: t.when.weekdays || [], instruction: t.do?.instruction || "", msg: null,
});

class AacTriggersPage extends HTMLElement {
  constructor() {
    super();
    this._form = null;
    this.addEventListener("click", (e) => this._click(e));
    this.addEventListener("change", (e) => this._change(e));
  }

  connectedCallback() { this.refresh(); }

  async refresh() {
    if (!this.ws || this._form) return; // never redraw over a form being filled in
    try { this._data = await this.ws("aac/triggers/info"); this._error = null; } catch (e) { this._error = e.message || String(e); }
    this._render();
  }

  async _click(e) {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.go) { this.panel?._go(t.dataset.go); return; }
    if (t.id === "tr-new") { this._form = blank(); this._render(); return; }
    if (t.dataset.edit) {
      const trig = this._data.triggers.find((x) => x.id === t.dataset.edit);
      if (trig) { this._form = fromTrigger(trig); this._render(); }
      return;
    }
    if (t.dataset.approve) { this._data = await this.ws("aac/trigger/approve", { trigger_id: t.dataset.approve }); this._render(); return; }
    if (t.dataset.reject) { this._data = await this.ws("aac/trigger/delete", { trigger_id: t.dataset.reject }); this._render(); return; }
    if (t.id === "t-cancel") { this._form = null; this.refresh(); return; }
    if (t.id === "t-save") { await this._save(); return; }
    if (t.id === "t-delete") {
      if (!(await sure(this, "Delete this trigger?", "Delete"))) return;
      await this.ws("aac/trigger/delete", { trigger_id: this._form.id });
      this._form = null;
      this.refresh();
    }
  }

  _change(e) {
    if (!this._form) return;
    if (e.target.id === "t-type") { this._read(); this._form.type = e.target.value; this._render(); }
    if (e.target.id === "t-quiet") { this._read(); this._form.quiet = e.target.checked ? { start: "22:00", end: "08:00" } : null; this._render(); }
  }

  _read() {
    const f = this._form;
    const val = (id) => this.querySelector(id)?.value ?? "";
    f.name = val("#t-name").trim();
    f.instruction = val("#t-instr").trim();
    f.enabled = this.querySelector("#t-enabled").checked;
    if (f.type === "time") {
      f.at = val("#t-at") || f.at;
      f.weekdays = [...this.querySelectorAll(".t-day:checked")].map((el) => parseInt(el.value, 10));
    } else if (f.type === "event") {
      f.event_type = val("#t-event").trim();
      f.event_data = val("#t-edata").trim();
      f.cooldown = parseInt(val("#t-cool"), 10) || 0;
    } else {
      f.entity_id = val("#t-entity").trim();
      f.attribute = val("#t-attr").trim();
      f.to = val("#t-to").trim();
      f.cooldown = parseInt(val("#t-cool"), 10) || 0;
    }
    if (f.quiet) f.quiet = { start: val("#t-qs") || f.quiet.start, end: val("#t-qe") || f.quiet.end };
  }

  async _save() {
    this._read();
    const f = this._form;
    const states = this.panel?._hass?.states || {};
    const eventData = Object.fromEntries((f.event_data || "").split(",").map((p) => p.split("=").map((x) => x.trim())).filter(([k, v]) => k && v !== undefined));
    const when = f.type === "time" ? { type: "time", at: f.at, weekdays: f.weekdays }
      : f.type === "event" ? { type: "event", event_type: f.event_type, event_data: eventData }
      : { type: "state", entity_id: f.entity_id, attribute: f.attribute || "", to: f.to };
    if (f.type === "event" && !f.event_type) { f.msg = "Give an event type."; return this._render(); }
    if (f.type === "state" && !states[f.entity_id]) { f.msg = "Unknown entity."; return this._render(); }
    const data = { name: f.name, when, instruction: f.instruction, cooldown: f.type === "time" ? 0 : f.cooldown, quiet_hours: f.quiet, enabled: f.enabled };
    if (f.id) data.trigger_id = f.id;
    try {
      await this.ws("aac/trigger/save", data);
      this._form = null;
      this.refresh();
    } catch (err) { f.msg = err.message || String(err); this._render(); }
  }

  _render() {
    if (this._form) { this.innerHTML = this._renderForm(); return; }
    const d = this._data;
    if (!d) { this.innerHTML = `<div class="card"><p>${this._error ? esc(this._error) : "Loading…"}</p></div>`; return; }
    const rows = d.triggers.map((t) => `
      <div class="wrow">
        <span><span class="dot ${t.proposed ? "open" : t.enabled ? "ok" : ""}"></span> ${esc(t.name || t.when.entity_id || t.when.at)}
          ${t.created_by === "agent" ? '<span class="tag aac">AAC</span>' : ""}${t.proposed ? ' <span class="tag plan">PROPOSED</span>' : ""}
          <span class="hint">· ${esc(whenText(t.when))}</span>
          ${t.created_by === "agent" ? `<div class="hint" style="margin:.2em 0 0 1.1em">${esc(t.do?.instruction || "")}</div>` : ""}</span>
        <span class="actions">
          ${t.proposed ? `<button class="btn primary" data-approve="${esc(t.id)}">Approve</button><button class="btn danger" data-reject="${esc(t.id)}">Reject</button>`
            : `<button class="btn" data-edit="${esc(t.id)}">Edit</button>`}
        </span>
      </div>`).join("");
    this.innerHTML = `<div class="cards">
      <div class="card"><h2>TRIGGERS</h2>
        <p class="hint">Wake AAC when something changes in Home Assistant, or at a time (reminders, routines), with your instruction.
          ${d.enabled ? "" : "<br><b>The module is off in this domain: nothing fires.</b>"}</p>
        ${rows || '<p class="hint">No triggers yet.</p>'}
        <div class="actions"><button class="btn primary" id="tr-new">+ New trigger</button></div>
      </div>
      <div class="card"><h2>AAC'S OWN PLANS</h2>
        <p>AAC may plan on its own: <b>${esc(PLANS[d.own_plans] || d.own_plans)}</b>.</p>
        <p class="hint">Reminders, check-ins and things to watch that AAC sets itself. It can never change your triggers, and every plan it makes is posted in "From AAC".</p>
        <div class="actions"><button class="btn" data-go="settings/module/triggers">Settings</button></div>
      </div>
    </div>`;
  }

  _renderForm() {
    const f = this._form;
    const states = this.panel?._hass?.states || {};
    const ids = Object.keys(states).sort();
    const isTime = f.type === "time", isEvent = f.type === "event";
    return `<div class="card"><h2>${f.id ? "EDIT TRIGGER" : "NEW TRIGGER"}</h2>
      <div class="form-grid">
        <div>
          <label for="t-name">Name</label><input id="t-name" value="${esc(f.name)}" placeholder="${isTime ? "e.g. Pills" : "e.g. Front door"}">
          <label for="t-type">When</label>
          <select id="t-type"><option value="state" ${f.type === "state" ? "selected" : ""}>An entity changes (state or attribute)</option>
            <option value="event" ${isEvent ? "selected" : ""}>A Home Assistant event happens</option>
            <option value="time" ${isTime ? "selected" : ""}>At a time</option></select>
          ${isEvent ? `
            <label for="t-event">Event type</label><input id="t-event" value="${esc(f.event_type)}" placeholder="e.g. zha_event, mobile_app_notification_action, tag_scanned">
            <label for="t-edata">Only when its data has (optional)</label><input id="t-edata" value="${esc(f.event_data)}" placeholder="e.g. command=on, device_id=…">
            <label for="t-cool">Cooldown (seconds)</label><input id="t-cool" type="number" min="0" max="86400" value="${esc(f.cooldown)}" inputmode="numeric">
          ` : isTime ? `
            <label for="t-at">Time</label><input id="t-at" type="time" value="${esc(f.at)}">
            <label>Days (none = every day)</label>
            <div class="row" style="gap:.3em">${WEEKDAYS.map((d, i) => `<label class="check" style="flex:none;min-width:0;margin:0 .6em 0 0"><input type="checkbox" class="t-day" value="${i}" ${f.weekdays.includes(i) ? "checked" : ""}>${d}</label>`).join("")}</div>
          ` : `
            <label for="t-entity">Entity</label><input id="t-entity" list="t-entities" value="${esc(f.entity_id)}" placeholder="binary_sensor.front_door" autocomplete="off">
            <datalist id="t-entities">${ids.map((i) => `<option value="${esc(i)}">${esc(states[i].attributes.friendly_name || "")}</option>`).join("")}</datalist>
            <label for="t-attr">Attribute instead of the state (optional)</label><input id="t-attr" value="${esc(f.attribute)}" placeholder="e.g. game (Steam), brightness, temperature">
            <label for="t-to">Only when it becomes (optional)</label><input id="t-to" value="${esc(f.to)}" placeholder="e.g. on, open, home">
            <label for="t-cool">Cooldown (seconds)</label><input id="t-cool" type="number" min="0" max="86400" value="${esc(f.cooldown)}" inputmode="numeric">
          `}
          <label class="check"><input type="checkbox" id="t-quiet" ${f.quiet ? "checked" : ""}>Quiet hours</label>
          ${f.quiet ? `<div class="row"><div><label for="t-qs">From</label><input id="t-qs" type="time" value="${esc(f.quiet.start)}"></div><div><label for="t-qe">Until</label><input id="t-qe" type="time" value="${esc(f.quiet.end)}"></div></div>` : ""}
          <label class="check"><input type="checkbox" id="t-enabled" ${f.enabled ? "checked" : ""}>Enabled</label>
        </div>
        <div><label for="t-instr">Instruction for AAC</label><textarea id="t-instr" class="thin-scroll" placeholder="${isTime ? "e.g. Remind me to take my pills." : "e.g. If nobody is home, tell me."}">${esc(f.instruction)}</textarea></div>
      </div>
      ${f.msg ? `<div class="msg err">${esc(f.msg)}</div>` : ""}
      <div class="actions">
        <button class="btn primary" id="t-save">${f.id ? "Save trigger" : "Create trigger"}</button>
        ${f.id ? '<button class="btn danger" id="t-delete">Delete</button>' : ""}
        <button class="btn" id="t-cancel">Cancel</button>
      </div>
    </div>`;
  }
}

if (!customElements.get("aac-triggers-page")) customElements.define("aac-triggers-page", AacTriggersPage);

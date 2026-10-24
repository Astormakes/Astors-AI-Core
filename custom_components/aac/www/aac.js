// AAC 2 panel (docs/v2): one sidebar (the cards' pages and their entries: chats, lists …; then Nodes and Settings),
// the cards' own pages (each card file's .js, spec/cards.md), the node view (card types left, the grid, the selected
// card's settings right), and the core's settings (Audio, Models, Appearance). Plain web component, HA's theme.

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const md = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\n/g, "<br>");
const tip = (html) => `<span class="qm" tabindex="0">?<span class="tip">${html}</span></span>`;
const W = 180, HEAD = 34, ROW = 20;
const COL = W + 110; // one slot column (v1: a column per step in the flow)
const KIND_NAME = { text: "context", read: "read (dotted)", audio: "audio stream", clip: "audio clip" };
const KIND_COLOR = { text: "#f5a623", read: "#f5a623", audio: "#4caf50", clip: "#9c27b0" };
// the Conditional's rules editor: tests and actions with the fields each needs ([field, label, kind])
const TESTS = { contains: "contains", is: "is exactly", starts: "starts with", ends: "ends with", matches: "matches (regex)",
  has_tag: "has the tag", count_more: "more entries than", empty: "is empty", always: "always", template: "template is true" };
const ACTIONS = {
  replace: ["Replace text", [["a", "find", "text"], ["b", "with", "text"]]], insert: ["Insert text", [["a", "text", "text"], ["b", "at", ["end", "start"]]]],
  remove: ["Remove text", [["a", "text", "text"]]], remove_entries: ["Remove the entries", []], cut: ["Keep only the part", [["a", "after", "text"], ["b", "before", "text"]]],
  split: ["Split into entries", [["a", "at", "text"]]], join: ["Join into one entry", [["b", "with", "text"]]], case: ["Change case", [["a", "to", ["lower", "upper", "title"]]]],
  trim: ["Trim spaces", []], tag: ["Add a tag", [["a", "tag", "tag"]]], call: ["Call a wired card", [["card", "card", "card"], ["function", "function", "function"], ["args", "with ({text} = the entry)", "json"]]],
  stop: ["Send stop", []], signal: ["Send a signal", [["a", "signal", "text"]]], output: ["Send to output", [["a", "output", "output"]]] };
const KINDS = ["said", "answer", "note", "trailer", "system", "call", "result"];
const DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

const STYLE = `
:host { display: block; height: 100vh; color: var(--primary-text-color); background: var(--primary-background-color);
  font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
* { box-sizing: border-box; }
.root { display: flex; height: 100%; position: relative; }
nav { width: var(--nav-w, 250px); flex: none; background: var(--sidebar-background-color, var(--card-background-color));
  border-right: 1px solid var(--divider-color); display: flex; flex-direction: column; }
nav .top { padding: 14px 16px 6px; display: flex; align-items: baseline; gap: 8px; }
nav .top b { font-size: 20px; } nav .top span { color: var(--secondary-text-color); font-size: 12px; }
nav .dom { display: flex; gap: 6px; padding: 4px 12px 10px; }
nav .dom select { flex: 1; }
nav .scroll { flex: 1; overflow-y: auto; }
h4 { margin: 14px 16px 4px; font-size: 11px; letter-spacing: .08em; color: var(--secondary-text-color); display: flex; align-items: center; }
h4 button { margin-left: auto; }
.item { padding: 6px 16px 6px 22px; cursor: pointer; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; border-radius: 0 16px 16px 0; margin-right: 8px; }
.item:hover { background: rgba(127,127,127,.1); } .item.on { background: rgba(3,169,244,.15); color: var(--primary-color); }
nav .bottom { border-top: 1px solid var(--divider-color); padding: 6px 0; }
main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
#cardhost, #cardhost > * { display: flex; flex-direction: column; flex: 1; min-height: 0; }
h4.fold { cursor: pointer; user-select: none; }
header { display: flex; align-items: center; gap: 8px; padding: 0 16px; min-height: 56px; border-bottom: 1px solid var(--divider-color); flex-wrap: wrap; }
header h2 { margin: 0; font-size: 18px; font-weight: 500; margin-right: auto; }
button, select, input, textarea { font: inherit; color: inherit; }
button { background: none; border: 1px solid var(--divider-color); border-radius: 6px; padding: 4px 10px; cursor: pointer; }
button:hover { border-color: var(--primary-color); } button.primary { background: var(--primary-color); color: #fff; border-color: var(--primary-color); }
button.small { padding: 1px 7px; font-size: 12px; } button.danger { color: var(--error-color, #db4437); } button.on { background: rgba(3,169,244,.15); color: var(--primary-color); }
button:disabled { opacity: .4; cursor: default; }
input, select, textarea { background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 6px; padding: 6px 8px; width: 100%; }
textarea { resize: vertical; }
.content { flex: 1; overflow-y: auto; padding: 16px; }
.hint { color: var(--secondary-text-color); font-size: 12px; }
.qm { display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px; border-radius: 50%; border: 1px solid var(--secondary-text-color);
  font-size: 11px; color: var(--secondary-text-color); position: relative; cursor: help; margin-left: 4px; vertical-align: middle; }
.qm .tip { display: none; position: fixed; z-index: 1000; width: 300px; max-width: calc(100vw - 24px); background: var(--card-background-color);
  border: 1px solid var(--divider-color); border-radius: 8px; padding: 10px; font-size: 12px; color: var(--primary-text-color); box-shadow: 0 4px 16px rgba(0,0,0,.25); text-align: left; white-space: normal; }
.qm:hover .tip, .qm:focus .tip { display: block; }
/* chat */
.chat { display: flex; flex-direction: column; height: 100%; }
.log { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
.bubble { max-width: min(80%, 760px); padding: 8px 12px; border-radius: 12px; line-height: 1.45; overflow-wrap: anywhere; }
.bubble.user { align-self: flex-end; background: var(--primary-color); color: #fff; }
.bubble.aac { align-self: flex-start; background: var(--card-background-color); border: 1px solid var(--divider-color); }
.bubble.err { border-color: var(--error-color, #db4437); } .bubble .cut { font-size: 11px; color: var(--secondary-text-color); }
.composer { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--divider-color); }
.composer textarea { min-height: 42px; max-height: 40vh; }
.entry { font-size: 13px; border-left: 3px solid var(--divider-color); padding: 4px 10px; margin: 4px 0; }
.entry .k { font-size: 11px; color: var(--secondary-text-color); }
.entry pre.req { white-space: pre-wrap; margin: 2px 0; font: inherit; }
/* lists */
.lst ul { list-style: none; padding-left: 22px; margin: 0; } .lst > ul { padding-left: 0; }
.lst li { margin: 3px 0; } .row { display: flex; align-items: center; gap: 6px; }
.row .t { cursor: text; flex: 1; } .row .t.done { text-decoration: line-through; color: var(--secondary-text-color); }
.row .acts { opacity: 0; } .row:hover .acts { opacity: 1; }
.fields { padding: 1px 0 3px 26px; display: flex; flex-wrap: wrap; gap: 4px; }
.chip { font-size: 11px; background: rgba(127,127,127,.12); border-radius: 10px; padding: 1px 7px; cursor: pointer; }
.card { background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 10px; padding: 14px; margin-bottom: 14px; }
.models td { padding: 4px 8px; border-bottom: 1px solid var(--divider-color); font-size: 14px; }
/* nodes */
.nodes { display: flex; height: 100%; min-height: 0; }
.palette { width: var(--pal-w, 190px); flex: none; border-right: 1px solid var(--divider-color); display: flex; flex-direction: column; }
.palette .types { flex: 1; overflow-y: auto; padding-bottom: 8px; }
.palette .info { border-top: 1px solid var(--divider-color); padding: 10px; font-size: 12px; max-height: 45%; overflow-y: auto; }
.grid { flex: 1; position: relative; overflow: hidden; background-image: radial-gradient(rgba(127,127,127,.25) 1px, transparent 1px); background-size: 20px 20px; }
.grid svg { width: 100%; height: 100%; display: block; user-select: none; }
.side { width: var(--side-w, 300px); flex: none; border-left: 1px solid var(--divider-color); overflow-y: auto; padding: 12px; }
.side label { display: block; font-size: 12px; color: var(--secondary-text-color); margin: 10px 0 3px; }
.side .check { display: flex; gap: 6px; align-items: center; color: var(--primary-text-color); font-size: 14px; }
.side .check input { width: auto; }
.fn { font-size: 12px; margin: 6px 0; } .fn code { font-size: 12px; }
.node rect.box { fill: var(--card-background-color); stroke: var(--divider-color); stroke-width: 1.2; }
.node.sel rect.box { stroke: var(--primary-color); stroke-width: 2.4; }
.node text { fill: var(--primary-text-color); font-size: 13px; } .node text.sub { fill: var(--secondary-text-color); font-size: 10px; } .node text.port { font-size: 10px; fill: var(--secondary-text-color); }
.dot { cursor: crosshair; }
.wire { fill: none; stroke-width: 2; opacity: .7; } .wire.read { stroke-dasharray: 4 4; } .wire.sel { stroke-width: 4; opacity: 1; }
/* something passed (owner): the wire lights up brighter and pulses thick-thin, fading back to normal over a few seconds */
.wire.lit { animation: lit 4s ease-out; }
@keyframes lit { 0% { filter: brightness(1.9) saturate(1.4); stroke-width: 2; opacity: 1; } 10% { stroke-width: 6; } 20% { stroke-width: 2.5; }
  30% { stroke-width: 5.5; } 40% { stroke-width: 2.5; } 50% { stroke-width: 4.5; } 60% { stroke-width: 2.5; } 70% { stroke-width: 3.5; filter: brightness(1.4); }
  100% { stroke-width: 2; filter: none; opacity: .7; } }
.grip { flex: none; width: 7px; margin: 0 -4px 0 -3px; cursor: col-resize; position: relative; z-index: 5; touch-action: none; }
.grip:hover, .grip.drag { background: var(--divider-color); }
.rule { padding: 8px; margin: 6px 0; font-size: 13px; } .rule .row { display: flex; gap: 4px; align-items: center; }
.rule .rh { font-size: 11px; font-weight: 600; color: var(--secondary-text-color); margin: 8px 0 2px; letter-spacing: .05em; }
.rule .rf { display: flex; gap: 4px; align-items: center; margin: 3px 0; } .rule .rf > span { font-size: 12px; color: var(--secondary-text-color); white-space: nowrap; }
.rule input, .rule select { padding: 4px 6px; font-size: 13px; min-width: 0; }
.ract { border-left: 3px solid var(--divider-color); padding: 2px 0 4px 8px; margin: 4px 0; }
.mons { position: absolute; inset: 0; pointer-events: none; overflow: hidden; }
.mon { position: absolute; transform: translate(-50%, calc(-100% - 8px)); pointer-events: auto; cursor: pointer; max-width: 260px; min-width: 110px;
  padding: 4px 8px; font-size: 11px; line-height: 1.35; border: 1px solid var(--primary-color); background: var(--card-background-color);
  border-radius: 6px; box-shadow: 0 2px 8px rgba(0,0,0,.2); }
.mon.sel { border-width: 2px; } .mon .t { display: flex; gap: 6px; color: var(--secondary-text-color); font-size: 10px; }
.mon .t b { color: var(--primary-color); font-weight: 500; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mon .x { overflow: hidden; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; white-space: pre-wrap; }
.mon.flash { animation: monflash .8s; } @keyframes monflash { from { box-shadow: 0 0 14px var(--primary-color); } }
.fold { margin-top: 8px; font-size: 12px; } .fold > summary { cursor: pointer; color: var(--secondary-text-color); font-weight: 500; }
.rule details > summary { cursor: pointer; margin-top: 4px; }
.legend { position: absolute; right: 8px; bottom: 6px; font-size: 10px; color: var(--secondary-text-color); line-height: 1.5; opacity: .8; }
.legend div { display: flex; align-items: center; gap: 4px; } .grid .legend svg { flex: none; width: 20px; height: 8px; }
.warn { color: var(--error-color, #db4437); font-size: 12px; margin: 4px 0; }
.mlrow select { flex: 1; } .mlrow .check { font-size: 12px; white-space: nowrap; }
.hit { fill: none; stroke: transparent; stroke-width: 14; cursor: pointer; }
.menu { display: none; background: none; border: none; font-size: 22px; padding: 4px 8px 4px 0; cursor: pointer; color: var(--primary-text-color); }
.scrim { display: none; }
@media (max-width: 800px) { nav { position: absolute; z-index: 20; height: 100%; transform: translateX(-100%); transition: transform .2s; } .root.navopen nav { transform: none; }
  .menu { display: inline-block; } .root.navopen .scrim { display: block; position: absolute; inset: 0; z-index: 15; background: rgba(0,0,0,.3); }
  .palette, .side { width: 170px; } .grip { display: none; } }
`;

class AacPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.view = { kind: "card", card: null, id: null };
    this.nav = []; this._els = {}; this.sel = null; this.selType = null; this.selWire = null; this.vp = { x: 80, y: 120, k: 1 };
    // a ? tip floats above everything (fixed), placed under its ? and kept inside the window
    const place = (e) => {
      const qm = e.target.closest?.(".qm"), t = qm?.querySelector(".tip");
      if (!t) return;
      t.style.display = "block";
      const r = qm.getBoundingClientRect(), w = t.offsetWidth, h = t.offsetHeight, m = 12;
      t.style.left = `${Math.max(m, Math.min(r.right - w + 10, innerWidth - w - m))}px`;
      t.style.top = `${r.bottom + 4 + h > innerHeight - m ? Math.max(m, r.top - 4 - h) : r.bottom + 4}px`;
    };
    const hide = (e) => { const t = e.target.closest?.(".qm")?.querySelector(".tip"); if (t && !e.target.closest(".qm").matches(":hover, :focus")) t.style.display = ""; };
    this.shadowRoot.addEventListener("mouseover", place);
    this.shadowRoot.addEventListener("focusin", place);
    this.shadowRoot.addEventListener("mouseout", hide);
    this.shadowRoot.addEventListener("focusout", hide);
    // one listener for what is drawn by others or redrawn often: the sidebar, a card page's ☰, the Audio page's meters
    this.shadowRoot.addEventListener("click", (e) => {
      const t = e.target.closest?.("[data-say],[data-go],[data-navadd],[data-fold],#navbtn");
      if (!t) return;
      const d = t.dataset;
      if (d.say) this.ws("aac/audio/say", { source: d.say, text: "This is how I sound." });
      else if (d.navadd) { e.stopPropagation(); const el = this._els[d.navadd]; if (el?.add) { this.go("card", d.navadd, this.view.card === d.navadd ? this.view.id : null).then(() => el.add()); } else this.go("card", d.navadd, null); }
      else if (d.fold) { const f = this._folds(); f[d.fold] = !f[d.fold]; this._setFolds(f); this.go("card", d.fold, null); }
      else if (d.go) this.go(d.go, d.card || null, d.id || null);
      else if (t.id === "navbtn") { this.navOpen = true; this.shadowRoot.querySelector(".root").classList.add("navopen"); }
    });
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._start();
  }

  set narrow(v) { this._narrow = v; }

  ws(type, data = {}) { return this._hass.callWS({ type, ...data }); }

  async _start() {
    this._applyWidths();
    await this._load();
    this._hass.connection.subscribeMessage((ev) => this._event(ev), { type: "aac/subscribe" });
    this.go("card", null, null);
  }

  async _load() {
    [this.state, this.audio, this.look, this.nav] = await Promise.all([this.ws("aac/state"), this.ws("aac/audio").catch(() => null),
      this.ws("aac/appearance").catch(() => ({ skins: [], skin: "" })), this.ws("aac/nav").then((r) => r.nav).catch(() => [])]);
  }
  _folds() { try { return JSON.parse(localStorage.getItem("aac.folds") || "{}"); } catch (err) { return {}; } }
  _setFolds(f) { try { localStorage.setItem("aac.folds", JSON.stringify(f)); } catch (err) { /* private window */ } }
  _card(id) { return (this.state?.domain.cards || []).find((c) => c.id === id); }
  _type(t) { return (this.state?.types || []).find((x) => x.type === t); }

  _event(ev) {
    if (ev.card && this._els[ev.card]) { try { this._els[ev.card].event?.(ev); } catch (err) { console.error(err); } } // to the card's page
    if (["chat", "card"].includes(ev.what)) { // a card's sidebar entries may have changed
      clearTimeout(this._nt);
      this._nt = setTimeout(async () => { this.nav = (await this.ws("aac/nav")).nav; const n = this.shadowRoot.querySelector("nav"); if (n) n.outerHTML = this._nav(); }, 200);
    } else if (ev.what === "setup") {
      clearTimeout(this._st);
      this._st = setTimeout(async () => { await this._load(); if (this.view.kind !== "nodes" || !this._dragging) this.render(); }, 150);
    } else if (ev.what === "appearance") {
      this.ws("aac/appearance").then((look) => { this.look = look; this.render(); });
    } else if (ev.what === "audio" && this.view.kind === "audio") {
      clearTimeout(this._at); this._at = setTimeout(async () => { this.audio = await this.ws("aac/audio"); this.render(); }, 300);
    } else if (ev.what === "level") {
      for (const p of this.shadowRoot.querySelectorAll(`.wire[data-from="${CSS.escape(ev.wire)}"]`)) {
        p.style.strokeWidth = (2 + Math.min(100, ev.level) / 100 * 8).toFixed(1);
        clearTimeout(p._lt); p._lt = setTimeout(() => { p.style.strokeWidth = ""; }, 1200);
      }
    } else if (ev.what === "flow") {
      if (this.view.kind === "nodes") for (const key of this._monitors().filter((k) => k.split("|")[0] === ev.wire)) {
        clearTimeout((this._mt ||= {})[key]); this._mt[key] = setTimeout(() => this._loadMonitor(key, true), 250);
      }
      for (const p of this.shadowRoot.querySelectorAll(`.wire[data-from="${CSS.escape(ev.wire)}"]`)) {
        if (ev.to && !ev.to.includes(p.dataset.to)) continue; // only the wires it took
        p.classList.remove("lit"); void p.getBoundingClientRect(); p.classList.add("lit"); // restart: a second push lights it up again
        clearTimeout(p._t); p._t = setTimeout(() => p.classList.remove("lit"), 4200);
      }
    }
  }

  async go(kind, card = null, id = null) {
    this.view = { kind, card, id };
    this.navOpen = false; // on a phone the sidebar closes when a page is picked
    if (kind === "card" && !this.nav.some((n) => n.card === card)) this.view.card = this.nav[0]?.card || null;
    if (kind === "audio") { this.audio = await this.ws("aac/audio"); clearInterval(this._live); this._live = setInterval(async () => { if (this.view.kind !== "audio") { clearInterval(this._live); return; } this.audio = await this.ws("aac/audio"); const b = this.shadowRoot.querySelector("#meters"); if (b) b.innerHTML = this._meters(); }, 700); }
    this.render();
    if (kind === "card") await this._showCard();
  }

  // ------------------------------------------------------------------ cards' own pages (spec/cards.md)
  async _element(n) { // the card's page element, kept while the card stays (its state survives redraws)
    const key = `${n.card}|${n.ui}`;
    if (this._els[n.card]?._key === key) return this._els[n.card];
    if (!customElements.get(n.element)) await import(n.ui);
    const el = document.createElement(n.element);
    el._key = key; el.dataset.cardpage = n.card;
    el.panel = this._api(n.card);
    this._els[n.card] = el;
    return el;
  }

  _api(card) { // what a card's page may use
    return {
      ws: (type, data = {}) => this.ws(type, data),
      call: async (fn, args = {}) => (await this.ws("aac/card/call", { card, function: fn, args })).result,
      ui: async (action, args = {}) => (await this.ws("aac/card/ui", { card, action, args })).result,
      data: async () => (await this.ws("aac/card/data", { card })).data,
      save: async (settings) => this.ws("aac/card/update", { card, settings }),
      go: (entry) => this.go("card", card, entry),
      header: (title, extra) => this._header(title, extra), cardTitle: (id) => this._card(id)?.title || id, esc, md, tip,
    };
  }

  async _showCard() {
    const n = this.nav.find((x) => x.card === this.view.card), host = this.shadowRoot.querySelector("#cardhost");
    if (!n || !host) return;
    try {
      const el = await this._element(n);
      el.card = this._card(n.card); el.entry = this.view.id;
      if (this.view.kind !== "card" || this.view.card !== n.card) return; // another page meanwhile
      host.replaceChildren(el);
      await el.show?.();
    } catch (e) { host.innerHTML = `${this._header(n.title)}<div class="content"><p>This card's page could not load: ${esc(e.message || e)}</p></div>`; }
  }

  // ------------------------------------------------------------------ layout
  render() {
    if (!this.state) { this.shadowRoot.innerHTML = `<style>${STYLE}</style><p style="padding:20px">Loading…</p>`; return; }
    const v = this.view;
    const main = v.kind === "nodes" ? this._nodes() : v.kind === "models" ? this._models() : v.kind === "audio" ? this._audioView()
      : v.kind === "appearance" ? this._appearanceView() : v.card ? '<div id="cardhost"></div>'
      : `${this._header("AAC")}<div class="content"><p>No card with a page in this domain (a Chat, Lists …). Add one in Nodes.</p></div>`;
    const skin = this.look?.skins.find((k) => k.id && k.id === this.look.skin);
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>${skin ? `<link rel="stylesheet" href="${esc(skin.url)}?v=${esc(this.state.version)}">` : ""}<div class="root ${this.navOpen ? "navopen" : ""}">${this._nav()}<div class="scrim" id="scrim"></div><div class="grip" data-grip="nav" data-target="nav" title="Drag: width of the sidebar (double-click: back to normal)"></div><main>${main}</main></div>`;
    this._bind();
    if (v.kind === "nodes") this._drawGrid();
    const el = v.kind === "card" && this._els[v.card];
    if (el) this.shadowRoot.querySelector("#cardhost")?.replaceChildren(el); // a redraw keeps the page as it is
  }

  _nav() { // the cards' pages and their entries, then Nodes and the core's settings
    const s = this.state, v = this.view, folds = this._folds();
    const on = (kind, card = null, id = null) => (v.kind === kind && v.card === card && (v.id || null) === id ? "on" : "");
    const item = (label, kind, card = null, id = null, indent = 0) =>
      `<div class="item ${on(kind, card, id)}" data-go="${kind}" data-card="${esc(card || "")}" data-id="${esc(id || "")}" ${indent ? `style="padding-left:${12 + indent * 14}px"` : ""}>${esc(label)}</div>`;
    const entries = (n, list, depth = 1) => list.map((e) => `${item(e.title, "card", n.card, e.id, depth)}${e.children?.length ? entries(n, e.children, depth + 1) : ""}`).join("");
    const groups = this.nav.filter((n) => n.nav).map((n) => {
      const shut = folds[n.card];
      return `<h4 class="fold" data-fold="${esc(n.card)}" title="Open the page; folds its entries">${shut ? "▸" : "▾"} ${esc(n.title.toUpperCase())}${n.nav.add ? `<button class="small" data-navadd="${esc(n.card)}" title="${esc(n.nav.add)}">+</button>` : ""}</h4>
        ${shut ? "" : entries(n, n.nav.entries || []) || '<div class="item hint" style="cursor:default">nothing yet</div>'}`;
    }).join("");
    const single = this.nav.filter((n) => !n.nav).map((n) => item(n.title, "card", n.card)).join("");
    return `<nav><div class="top"><button class="menu" id="habtn" title="Home Assistant's menu">⌂</button><b>AAC</b><span>v${esc(s.version)}</span></div>
      <div class="dom"><select id="domain">${s.domains.map((d) => `<option value="${esc(d.id)}" ${d.id === s.active ? "selected" : ""}>${esc(d.name)}</option>`).join("")}</select>
        <button class="small" id="clone" title="A copy of this setup to try things out">Clone</button></div>
      <div class="scroll">${groups}${single ? `<h4>MORE</h4>${single}` : ""}</div>
      <div class="bottom">${item("Nodes", "nodes")}<h4>SETTINGS</h4>${item("Audio", "audio")}${item("Models", "models")}${item("Appearance", "appearance")}</div></nav>`;
  }

  _header(title, extra = "") { return `<header><button class="menu" id="navbtn" title="Menu">☰</button><h2>${esc(title)}</h2>${extra}</header>`; }

  // ------------------------------------------------------------------ audio (core)
  _meters() {
    return (this.audio?.sources || []).map((src) => `<div class="card"><div class="row"><b>${esc(src.name)}</b><span class="hint">${src.playing ? "speaking" : "listening"} · peak ${src.peak_db ?? "silent"} dB</span>
      <button class="small" data-say="${esc(src.id)}" style="margin-left:auto">Test voice</button></div>
      <div style="display:flex;align-items:flex-end;gap:2px;height:40px;margin-top:6px">${(src.meter || []).map(([lvl, sp]) => `<i style="flex:1;height:${Math.max(2, lvl)}%;background:${sp > 0.3 ? "var(--primary-color)" : "var(--divider-color)"}"></i>`).join("")}</div></div>`).join("")
      || '<p class="hint">No satellite connected (the AAC satellite add-on, or the PC program).</p>';
  }

  _appearanceView() {
    const look = this.look || { skins: [], skin: "" };
    const rows = look.skins.map((k) => `<label class="check" style="display:flex;gap:8px;align-items:flex-start;margin:8px 0"><input type="radio" name="skin" data-skin="${esc(k.id)}" ${k.id === look.skin ? "checked" : ""}>
      <span><b>${esc(k.name)}</b> <span class="hint">${esc(k.source)}${k.author ? ` · ${esc(k.author)}` : ""}</span><br><span class="hint">${esc(k.description)}</span></span></label>`).join("");
    return `${this._header("Appearance", tip("How the panel looks, the same on every device. Own skins: a CSS file in config/aac/skins (a comment header with name, description, author; HA's theme variables inside :host, e.g. --primary-color)."))}
      <div class="content"><div class="card"><h3 style="margin-top:0">Skin</h3>${rows}</div></div>`;
  }

  _audioView() {
    const a = this.audio || { settings: {} }, st = a.settings || {};
    const sttItems = (a.stt_entities || []).map((e) => [e, e]);
    const order = (st.stt || []).filter((e) => e.startsWith("stt."));
    return `${this._header("Audio", tip("The satellites, how AAC hears (speech to text, in this order: the first that answers) and speaks (an HA voice engine). Shared by all domains; the audio cards in Nodes decide what happens with it."))}
      <div class="content"><h3>Satellites</h3><div id="meters">${this._meters()}</div>
      ${(st.known || []).filter((k) => !(a.sources || []).some((s) => s.id === k.id)).map((k) => `<div class="row"><span class="hint">${esc(k.name)} · offline</span><button class="small danger" data-forget="${esc(k.id)}" style="margin-left:auto" title="Remove it from AAC (and its media player in HA)">Forget</button></div>`).join("")}
      <div class="card"><h3 style="margin-top:0">HA's speech to text (in order) ${tip("Home Assistant's engines, tried after the models in a Speech to text card's list (that list is in the card's settings in Nodes).")}</h3>
        ${order.map((id, i) => `<div class="row"><span class="t">${esc((sttItems.find((x) => x[0] === id) || [id, id])[1])}</span>
          <button class="small" data-sttup="${i}">↑</button><button class="small danger" data-sttdel="${i}">✕</button></div>`).join("") || '<p class="hint">None yet.</p>'}
        <div class="row" style="margin-top:8px"><select id="sttadd"><option value="">Add …</option>${sttItems.filter((x) => !order.includes(x[0])).map(([id, l]) => `<option value="${esc(id)}">${esc(l)}</option>`).join("")}</select></div>
        <h3>Voice</h3><select id="ttsset"><option value="">—</option>${(a.tts_entities || []).map((e) => `<option ${e === st.tts ? "selected" : ""}>${esc(e)}</option>`).join("")}</select>
        <h3>Volume: <span id="volval">${esc(st.volume ?? 40)}</span> %</h3><input type="range" id="volume" min="0" max="100" step="5" value="${esc(st.volume ?? 40)}">
        <p class="hint">0 = AAC never speaks aloud.</p></div></div>`;
  }

  // ------------------------------------------------------------------ models
  _models() {
    const rows = this.state.models.map((m) => `<tr><td><b>${esc(m.name)}</b><div class="hint">${esc(m.model)} · ${esc(m.base_url)}</div><div class="hint">${(m.tags || []).map((t) => `<code>${esc(t)}</code>`).join(" ")}</div></td>
      <td>${m.up ? "✓" : '<span class="hint">skipped (did not answer)</span>'}<div class="hint">${m.busy || 0}/${m.parallel || 1} running${m.waiting ? ` · ${m.waiting} waiting` : ""}</div></td>
      <td><button class="small" data-editmodel="${esc(m.id)}">Edit</button> <button class="small danger" data-delmodel="${esc(m.id)}">Delete</button></td></tr>`).join("");
    const f = this.modelForm || {};
    return `${this._header("Models", tip("Every model AAC may use, for all domains. Each card that uses models has its own list, picked from these by what they can do (their tags). Any OpenAI-compatible server works: llama.cpp, Ollama, vLLM, LM Studio, cloud APIs."))}
      <div class="content"><div class="card models"><table style="width:100%;border-collapse:collapse">${rows || '<tr><td class="hint">No models yet.</td></tr>'}</table></div>
      <div class="card"><h3 style="margin-top:0">${f.id ? "Change model" : "Add a model"}</h3>
        <label class="hint">Server address</label><div class="row"><input id="m_url" value="${esc(f.base_url || "http://192.168.1.x:8080/v1")}"><button class="small" id="probe">Find models</button></div>
        <label class="hint">Model</label>${this.probed ? `<select id="m_model">${this.probed.map((x) => `<option ${x === f.model ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>` : `<input id="m_model" value="${esc(f.model || "")}">`}
        <label class="hint">Name</label><input id="m_name" value="${esc(f.name || "")}">
        <label class="hint">API key (only for servers that need one)</label><input id="m_key" type="password" placeholder="${f.has_key ? "kept" : ""}">
        <label class="hint">Runs at once ${tip("How many requests the server works on side by side (llama-server: its slots, --parallel). More wait in the queue of cards that allow it.")}</label><input id="m_parallel" type="number" min="1" value="${esc(f.parallel || 1)}" style="width:80px">
        <label class="hint">What it can do ${tip("Cards use only models with the tag they need: a Model text, Memory embeddings, Speech to text speech to text. None ticked: guessed from the name.")}</label>
        <div class="row" style="flex-wrap:wrap;gap:4px 12px">${(this.state.model_tags || []).map((t) => `<label class="check" style="font-size:13px"><input type="checkbox" data-mtag="${esc(t)}" ${(f.tags || []).includes(t) ? "checked" : ""}>${esc(t)}</label>`).join("")}</div>
        <div style="margin-top:10px;display:flex;gap:8px"><button class="primary" id="savemodel">Save</button>${f.id ? '<button id="cancelmodel">Cancel</button>' : ""}</div></div></div>`;
  }

  // ------------------------------------------------------------------ nodes
  _nodes() {
    const s = this.state, d = s.domain;
    const cats = s.categories.map((c) => {
      const types = s.types.filter((t) => t.category === c);
      return types.length ? `<h4>${esc(c.toUpperCase())}</h4>${types.map((t) => `<div class="item ${this.selType === t.type ? "on" : ""}" data-pick="${esc(t.type)}">${esc(t.title)}</div>`).join("")}` : "";
    }).join("");
    const t = this.selType && this._type(this.selType);
    const ports = (title, list) => (Object.keys(list).length ? `<label>${title}</label>${Object.entries(list).map(([p, k]) => `<div class="fn"><code>${esc(p)}</code> <span class="hint">${esc(KIND_NAME[k] || k)}</span>${t.ports_help?.[p] ? `<br>${esc(t.ports_help[p])}` : ""}</div>`).join("")}` : "");
    const info = t ? `<b>${esc(t.title)}</b> <span class="hint">v${esc(t.version)}</span><p style="margin:6px 0">${esc(t.help)}</p>${ports("Takes", t.inputs)}${ports("Outputs", t.outputs)}${this._fnDocs(t.functions)}<p class="hint">Click in the grid to place it. <button class="small" id="unpick" title="Put it back (Esc, or click the type again)">Cancel</button></p>`
      : '<span class="hint">Pick a card type above, then click in the grid to place it.</span>';
    const slots = ["1", "2", "3"].map((n) => `<button class="small" data-slot="${n}" title="Save the setup to slot ${n} (Shift-click: load it)">${d.slots[n] ? "●" : "○"} ${n}</button>`).join("");
    return `<div style="display:flex;flex-direction:column;height:100%">${this._header("Nodes", `${tip("Cards are placed from the left, wired by dragging from an output dot to an input dot of the same colour. Click a wire and press Delete (or the button) to remove it. A model's tools are the cards wired from its output and back into it.")}
        <button class="small" id="undo" ${d.undo ? "" : "disabled"}>Undo</button><button class="small" id="fit">Fit</button><button class="small" id="export" title="Save this setup as a file (cards, settings, wires, positions; the cards' own data such as lists stays here)">Save file</button><button class="small" id="import" title="Load a setup file into this domain (Undo goes back)">Load file</button><input type="file" id="importfile" accept=".json,application/json" hidden><button class="small ${this.showValues ? "on" : ""}" id="values" title="The value pool: what templates can use now ({{ card.value }}), live">Values</button><button class="small" id="arrange" title="Put the cards in slots: a column per step in the flow, a card's tools under it (Undo goes back)">Arrange</button>${slots}<button class="small danger" id="stop">Send stop</button>`)}
      <div class="nodes"><div class="palette"><div class="types">${cats}</div><div class="info">${info}</div></div>
        <div class="grip" data-grip="pal" data-target=".palette" title="Drag: width (double-click: back to normal)"></div>
        <div class="grid" id="grid"><svg id="svg"><g id="world"><g id="wires"></g><g id="cards"></g></g></svg><div class="mons"></div>${this._legend()}</div>
        <div class="grip" data-grip="side" data-target=".side" data-side="left" title="Drag: width (double-click: back to normal)"></div>
        <div class="side">${this._side()}</div></div></div>`;
  }

  _tri(x, y, mirrored) { // a read port: a triangle pointing along the flow (into a read input, out of what provides it)
    const d = mirrored ? -1 : 1;
    return `${x - 6 * d},${y - 6} ${x + 6 * d},${y} ${x - 6 * d},${y + 6}`;
  }

  _sub(c) { // under a card's name: its type, and the first model it uses
    const t = this._type(c.type), f = Object.values(t?.settings || {}).find((x) => x.type === "modellist");
    if (!f) return t?.title || c.type;
    const first = (c.settings.models || [])[0]?.model || this.state.models.find((m) => (m.tags || []).includes(f.tag))?.id;
    return `${t.title} · ${this.state.models.find((m) => m.id === first)?.name || "no model"}`;
  }

  _legend() { // what the lines mean (lower right of the grid)
    const line = (color, dash, width = 2) => `<svg width="20" height="8"><line x1="1" y1="4" x2="19" y2="4" stroke="${color}" stroke-width="${width}" ${dash ? `stroke-dasharray="${dash}"` : ""}/></svg>`;
    return `<div class="legend">
      <div title="A context arrives and the card runs (the wire swells when one passes)">${line(KIND_COLOR.text)} context</div>
      <div title="Read when the card runs; starts nothing (triangles: what a card provides, into what reads it)">${line(KIND_COLOR.text, "3 3")} read</div>
      <div title="Live audio, flowing all the time (thicker when louder)">${line(KIND_COLOR.audio, "", 3)} audio stream</div>
      <div title="A finished bit of audio: speech cut out of a stream, a sound file, …">${line(KIND_COLOR.clip)} audio clip</div></div>`;
  }

  _fnDocs(fns) {
    const list = Object.entries(fns || {});
    if (!list.length) return "";
    return `<details class="fold"><summary>Functions ${tip("A model calls these as tools (this card wired from its output and back); a Conditional card calls them as actions.")}</summary>`
      + list.map(([name, f]) => `<div class="fn"><code>${esc(name)}(${Object.entries(f.args || {}).map(([a, x]) => `${esc(a)}${x.required ? "" : "?"}`).join(", ")})</code> ${esc(f.help || "")}</div>`).join("") + "</details>";
  }

  _portDocs(c, t) {
    const fns = Object.keys(t?.functions || {}).length;
    const help = { ...(fns ? { in: "used as a tool: wire it from a Model (or Conditional) and back, and its functions are called", out: "back to the card that called it" } : {}), ...(t?.ports_help || {}) };
    const kind = (k) => KIND_NAME[k] || k;
    const row = (dir, [p, k]) => `<div class="fn"><code>${esc(p)}</code> <span class="hint">${dir} · ${esc(kind(k))}${c.square.includes(p) ? " · square: goes to one wire" : dir === "out" ? " · round: every wire gets it" : ""}</span>${help[p] || help["*"] ? `<br>${esc(help[p] || help["*"])}` : ""}</div>`;
    const list = [...Object.entries(c.inputs).map((x) => row("in", x)), ...Object.entries(c.outputs).map((x) => row("out", x))];
    return list.length ? `<details class="fold"><summary>Ports</summary>${list.join("")}</details>` : "";
  }

  _valuesSide() { // the value pool: every card's values and the core's, as templates name them
    const v = this.values;
    if (!v) return '<p class="hint">Loading…</p>';
    const flat = (pre, x) => (x && typeof x === "object" && !Array.isArray(x) ? Object.entries(x).flatMap(([k, y]) => flat(/^[A-Za-z_]\w*$/.test(k) ? `${pre}.${k}` : `${pre}['${k}']`, y)) : [[pre, x]]);
    const what = (id, key) => this._type(this._card(id)?.type)?.values?.[key] || "";
    const groups = Object.entries(v.cards || {}).map(([id, vals]) => `<label>${esc(this._card(id)?.title || id)}</label>${flat(/^[A-Za-z_]\w*$/.test(id) ? id : `cards['${id}']`, vals).map(([k, x]) => `<div class="fn"><code>{{ ${esc(k)} }}</code> <span class="hint">${esc(what(id, k.split(/[.[]/)[1]))}</span><br>${esc(JSON.stringify(x)).slice(0, 300)}</div>`).join("")}`);
    const audio = flat("audio", v.audio || {}).map(([k, x]) => `<div class="fn"><code>{{ ${esc(k)} }}</code><br>${esc(JSON.stringify(x)).slice(0, 300)}</div>`).join("");
    return `<b>Values</b>${tip("What templates (Relays, Conditionals) can use right now. Cards offer them; they change as the cards work. Home Assistant's own (states, now, …) work too.")}
      <div class="row" style="margin:6px 0;gap:6px"><button class="small" id="valrefresh">Refresh</button><button class="small" id="valclose">Close</button></div>
      ${groups.join("") || '<p class="hint">No card offers values.</p>'}<label>The core: when AAC last spoke</label>${audio}`;
  }

  async _removeCard() { // no question: Undo brings it back
    const card = this.sel;
    this.sel = null;
    await this.ws("aac/card/remove", { card });
    await this._load(); this.render();
  }

  async _loadValues() {
    try { this.values = (await this.ws("aac/values", {})).values; } catch (e) { this.values = { cards: {}, audio: {} }; }
    if (this.showValues) this.render();
  }

  _side() {
    const d = this.state.domain;
    if (this.showValues) return this._valuesSide();
    if (this.selMon) return this._monitorSide(this.selMon);
    if (this.selWire) {
      const key = `${this.selWire.from}|${this.selWire.to}`, on = this._monitors().includes(key);
      return `<b>Wire</b><p class="hint">${esc(this.selWire.from)} → ${esc(this.selWire.to)}</p>
        <div class="row" style="gap:8px"><button class="small" id="monitor" title="A window above the wire with what passes it, live">${on ? "Stop monitoring" : "Monitor"}</button><button class="small danger" id="unwire">Remove wire</button></div>`;
    }
    const c = this.sel && this._card(this.sel);
    if (!c) {
      const groups = {};
      for (const t of d.tags) (groups[t.card] ||= []).push(t);
      return `<p class="hint">Click a card to set it up.</p><b>Tags in this domain</b>${tip("Cards tag the entries they write (card.tag). Your own tags (user.…) are for Conditional rules and Relays: tag entries with them, then pick them up later.")}
        <div class="row" style="margin:8px 0;gap:6px"><input id="newtag" placeholder="your own tag, e.g. urgent"><button class="small" id="addtag">Add</button></div>
        ${Object.entries(groups).map(([card, tags]) => `<label>${esc(card)}</label>${tags.map((t) => `<div class="hint" style="font-size:12px"><code>${esc(t.tag)}</code> ${esc(t.what)}${card === "You" ? ` <button class="small" data-deltag="${esc(t.tag)}" title="Remove">×</button>` : ""}</div>`).join("")}`).join("")}`;
    }
    const t = this._type(c.type);
    const field = ([key, f]) => {
      const v = c.settings[key] ?? f.default, h = f.help ? tip(esc(f.help)) : "";
      if (f.type === "bool") return `<label class="check"><input type="checkbox" data-set="${esc(key)}" ${v ? "checked" : ""}>${esc(f.title)}${h}</label>`;
      if (f.type === "select") return `<label>${esc(f.title)}${h}</label><select data-set="${esc(key)}">${f.options.map(([o, l]) => `<option value="${esc(o)}" ${o === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
      if (f.type === "longtext") return `<label>${esc(f.title)}${h}</label><textarea rows="5" data-set="${esc(key)}">${esc(v)}</textarea>`;
      if (f.type === "number") return `<label>${esc(f.title)}${h}</label><input type="number" data-set="${esc(key)}" data-num value="${esc(v)}">`;
      if (f.type === "modellist") {
        const fit = this.state.models.filter((m) => (m.tags || []).includes(f.tag)), list = v || [];
        const rows = list.map((e, i) => `<div class="row mlrow" data-ml="${i}"><select data-mlm>${fit.map((m) => `<option value="${esc(m.id)}" ${m.id === e.model ? "selected" : ""}>${esc(m.name)}</option>`).join("")}${fit.some((m) => m.id === e.model) ? "" : `<option value="${esc(e.model)}" selected>${esc(e.model)} (not for ${esc(f.tag)})</option>`}</select>
          <label class="check" title="When it is busy, wait in its queue (and try the next)"><input type="checkbox" data-mlq ${e.queue ? "checked" : ""}>Queue</label>
          <button class="small" data-mlup="${i}" ${i ? "" : "disabled"}>↑</button><button class="small danger" data-mldel="${i}">×</button></div>`).join("");
        const warn = list.length && !list.some((e) => e.queue) ? '<p class="warn">When every model is busy, a context is dropped (tick Queue on one).</p>' : "";
        return `<label>${esc(f.title)}${h}</label>${rows || `<p class="hint">Empty: every model for ${esc(f.tag)} (${esc(fit.map((m) => m.name).join(", ") || "none yet")}), queued.</p>`}${warn}
          ${fit.length ? `<button class="small" id="mladd" data-key="${esc(key)}">Add a model</button>` : `<p class="hint">No model for ${esc(f.tag)} yet (Models in the sidebar).</p>`}`;
      }
      if (f.type === "tag") return `<label>${esc(f.title)}${h}</label><select data-set="${esc(key)}"><option value="">—</option>${d.tags.map((x) => `<option ${x.tag === v ? "selected" : ""}>${esc(x.tag)}</option>`).join("")}</select>`;
      if (f.type === "tags") return `<label>${esc(f.title)}${h}</label>${d.tags.map((x) => `<label class="check"><input type="checkbox" data-tag="${esc(key)}" value="${esc(x.tag)}" ${(v || []).includes(x.tag) ? "checked" : ""}>${esc(x.tag)}</label>`).join("") || '<p class="hint">No tags yet.</p>'}`;
      if (f.type === "list") return `<label>${esc(f.title)}${h}</label><input data-set="${esc(key)}" data-list value="${esc((v || []).join(", "))}">`;
      if (f.type === "access") return `<label>${esc(f.title)}${h}</label><textarea rows="6" data-set="${esc(key)}" data-access placeholder="switch.lamp: full">${esc(Object.entries(v || {}).map(([e, l]) => `${e}: ${l}`).join("\n"))}</textarea>`;
      if (f.type === "model") return `<label>${esc(f.title)}${h}</label><select data-set="${esc(key)}"><option value="">—</option>${this.state.models.map((m) => `<option value="${esc(m.id)}" ${m.id === v ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select>`;
      if (f.type === "player") return `<label>${esc(f.title)}${h}</label><select data-set="${esc(key)}"><option value="">the satellite the line came from</option>${(this.audio?.settings?.known || []).map((k) => `<option value="${esc(k.id)}" ${k.id === v ? "selected" : ""}>${esc(k.name)} (satellite)</option>`).join("")}${Object.keys(this._hass.states).filter((e) => e.startsWith("media_player.") && !e.startsWith("media_player.aac_satellite_")).sort().map((e) => `<option value="${esc(e)}" ${e === v ? "selected" : ""}>${esc(this._hass.states[e].attributes.friendly_name || e)}</option>`).join("")}</select>`;
      if (f.type === "source") return `<label>${esc(f.title)}${h}</label><select data-set="${esc(key)}"><option value="${key === "source" && c.type === "microphone" ? "any" : ""}">${c.type === "microphone" ? "any satellite" : "where the line came from"}</option>${(this.audio?.settings?.known || []).map((k) => `<option value="${esc(k.id)}" ${k.id === v ? "selected" : ""}>${esc(k.name)}</option>`).join("")}</select>`;
      if (f.type === "outputs") return `<label>${esc(f.title)}${h}</label><textarea rows="4" data-set="${esc(key)}" data-outputs>${esc((v || []).map((o) => `${o.name}: ${o.description || ""}`).join("\n"))}</textarea>`;
      if (f.type === "schedule") return `<label>${esc(f.title)}${h}</label>${(v || []).map((e, i) => `<div class="card" style="padding:8px" data-entry="${i}">
          <div class="row"><input data-e="name" value="${esc(e.name)}" placeholder="Name"><input data-e="at" value="${esc(e.at)}" style="width:80px" placeholder="HH:MM"></div>
          <div class="row" style="margin:4px 0;flex-wrap:wrap">${DAYS.map((d, n) => `<label class="check" style="font-size:12px"><input type="checkbox" data-day="${n}" ${(e.weekdays || []).includes(n) ? "checked" : ""}>${d}</label>`).join("")}</div>
          <textarea rows="2" data-e="text" placeholder="What to do">${esc(e.text)}</textarea>
          <div class="row"><label class="check"><input type="checkbox" data-e="enabled" ${e.enabled !== false ? "checked" : ""}>on</label><button class="small danger" data-delentry="${i}" style="margin-left:auto">Remove</button></div></div>`).join("")}
        <button class="small" id="addentry">Add an entry</button>`;
      if (f.type === "watches") return `<label>${esc(f.title)}${h}</label>${(v || []).map((w, i) => `<div class="card" style="padding:8px" data-watch="${i}">
          <div class="row"><input data-w="name" value="${esc(w.name)}" placeholder="Name"><input data-w="cooldown" value="${esc(w.cooldown ?? 300)}" style="width:70px" title="Cooldown (s)"></div>
          <input data-w="entity_id" value="${esc(w.entity_id)}" placeholder="entity, e.g. binary_sensor.door" style="margin-top:4px">
          <input data-w="to" value="${esc(w.to || "")}" placeholder="state that counts (empty: any change)" style="margin-top:4px">
          <textarea rows="2" data-w="text" placeholder="What to do" style="margin-top:4px">${esc(w.text)}</textarea>
          <button class="small danger" data-delwatch="${i}">Remove</button></div>`).join("")}
        <button class="small" id="addwatch">Add a watch</button>`;
      if (f.type === "rules") return `<label>${esc(f.title)}${h}</label>${this._rulesEditor(c, v || [])}
        <details style="margin-top:8px"><summary class="hint">As JSON</summary><label>${tip(`One rule: {"name": "for me", "select": {"by": "kind" | "tag" | "card" | "any", "value": "said", "which": "newest" | "all"}, "test": "contains" (is, starts, ends, matches, has_tag, count_more, empty, always), "value": "computer", "actions": [{"do": "replace", "a": "x", "b": "y"}, {"do": "tag", "a": "addressed"}, {"do": "call", "card": "lists", "function": "add_items", "args": {"list": "Shopping", "items": ["{text}"]}}, {"do": "output", "a": "yes"}, {"do": "stop"}], "go_on": false}`)}</label>
        <textarea rows="10" data-set="${esc(key)}" data-json style="font-family:monospace;font-size:12px">${esc(JSON.stringify(v || [], null, 1))}</textarea></details>`;
      return `<label>${esc(f.title)}${h}</label><input data-set="${esc(key)}" value="${esc(v)}">`;
    };
    return `<label>Name</label><input id="title" value="${esc(c.title)}">
      <p class="hint" style="margin-top:8px">${esc(t?.help || "")} <span title="The version of its card file">· ${esc(t?.title || c.type)} v${esc(t?.version || "?")}</span></p>
      ${Object.entries(t?.settings || {}).map(field).join("")}
      ${this._fnDocs(t?.functions)}
      ${this._portDocs(c, t)}
      ${Object.keys(t?.values || {}).length ? `<details class="fold"><summary>Values (for templates)</summary>${Object.entries(t.values).map(([n, h]) => `<div class="fn"><code>{{ ${esc(/^[A-Za-z_]\w*$/.test(c.id) ? c.id : `cards['${c.id}']`)}.${esc(n)} }}</code> ${esc(h)}</div>`).join("")}</details>` : ""}
      ${Object.keys(c.tags).length ? `<details class="fold"><summary>Tags</summary>${Object.entries(c.tags).map(([x, w]) => `<div class="fn"><code>${esc(x)}</code> ${esc(w)}</div>`).join("")}</details>` : ""}
      <div style="margin-top:14px;display:flex;gap:8px"><button class="danger" id="remove">Remove card</button></div>
      <p class="hint">Fields save when you leave them.</p>`;
  }

  _rulesEditor(c, rules) {
    const d = this.state.domain, opt = (v, cur, label = v) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(label)}</option>`;
    const outputs = Object.keys(c.outputs), cards = d.cards.filter((x) => x.id !== c.id && Object.keys(this._type(x.type)?.functions || {}).length);
    const field = (act, [k, label, kind]) => {
      const v = act[k] ?? "";
      let input;
      if (Array.isArray(kind)) input = `<select data-a="${k}">${kind.map((o) => opt(o, v || kind[0])).join("")}</select>`;
      else if (kind === "output") input = `<select data-a="${k}">${outputs.map((o) => opt(o, v)).join("")}</select>`;
      else if (kind === "card") input = `<select data-a="${k}" data-rerender><option value="">—</option>${cards.map((x) => opt(x.id, v, x.title)).join("")}</select>`;
      else if (kind === "function") input = `<select data-a="${k}">${Object.keys(this._type(this._card(act.card)?.type)?.functions || {}).map((f) => opt(f, v)).join("")}</select>`;
      else if (kind === "json") input = `<input data-a="${k}" data-jsonarg value="${esc(typeof v === "string" ? v : JSON.stringify(v || {}))}" placeholder='{"list": "Shopping", "items": ["{text}"]}'>`;
      else input = `<input data-a="${k}" value="${esc(v)}" ${kind === "tag" ? 'list="aac-tags"' : ""}>`;
      return `<div class="rf"><span>${esc(label)}</span>${input}</div>`;
    };
    const action = (act, j) => `<div class="ract" data-act="${j}"><div class="row"><select data-a="do" data-rerender>${Object.entries(ACTIONS).map(([k, [l]]) => opt(k, act.do, l)).join("")}</select>
        <button class="small" data-delact="${j}" title="Remove this action">×</button></div>${(ACTIONS[act.do]?.[1] || []).map((f) => field(act, f)).join("")}</div>`;
    const sel = (r) => r.select || {};
    const valueList = { kind: KINDS, tag: d.tags.map((t) => t.tag), card: d.cards.map((x) => x.id), any: [] };
    return `<datalist id="aac-tags">${d.tags.map((t) => `<option value="${esc(t.tag)}">`).join("")}</datalist>
      ${rules.map((r, i) => `<div class="rule card" data-rule="${i}">
        <div class="row"><b class="hint">${i + 1}</b><input data-r="name" value="${esc(r.name || "")}" placeholder="Name of the rule">
          <button class="small" data-moverule="${i}" data-dir="-1" ${i ? "" : "disabled"} title="Up">↑</button><button class="small" data-moverule="${i}" data-dir="1" ${i < rules.length - 1 ? "" : "disabled"} title="Down">↓</button><button class="small danger" data-delrule="${i}" title="Remove the rule">×</button></div>
        <details data-fold="${i}" ${this.openRules?.[c.id]?.has(i) ? "open" : ""}><summary class="hint">${esc(this._ruleLine(r))}</summary>
        <div class="rh">IF</div>
        <div class="rf"><span>look at the</span><select data-r="which">${opt("newest", sel(r).which || "newest", "newest")}${opt("all", sel(r).which, "all")}</select>
          <span>entry by</span><select data-r="by" data-rerender>${["kind", "tag", "card", "any"].map((b) => opt(b, sel(r).by || "kind", b === "any" ? "any entry" : b)).join("")}</select></div>
        ${(sel(r).by || "kind") !== "any" ? `<div class="rf"><span>=</span><input data-r="svalue" list="aac-sv-${i}" value="${esc(sel(r).value ?? "said")}"><datalist id="aac-sv-${i}">${(valueList[sel(r).by || "kind"] || []).map((x) => `<option value="${esc(x)}">`).join("")}</datalist></div>` : ""}
        <div class="rf"><select data-r="test" data-rerender>${Object.entries(TESTS).map(([k, l]) => opt(k, r.test || "always", l)).join("")}</select>
          ${["always", "empty"].includes(r.test || "always") ? "" : `<input data-r="value" value="${esc(r.value ?? "")}" ${r.test === "has_tag" ? 'list="aac-tags"' : ""}>`}</div>
        <div class="rh">THEN</div>
        ${(r.actions || []).map(action).join("")}
        <button class="small" data-addact="${i}">Add an action</button>
        <label class="check" style="margin-top:6px"><input type="checkbox" data-r="go_on" ${r.go_on ? "checked" : ""}>then go on checking the next rules</label></details></div>`).join("")}
      <button class="small" id="addrule">Add a rule</button>`;
  }

  _ruleLine(r) { // a rule folded to one line: "said contains 'computer' → yes"
    const s = r.select || {}, what = (s.by || "kind") === "any" ? "any entry" : `${s.value ?? "said"}`;
    const test = ["always", "empty"].includes(r.test || "always") ? TESTS[r.test || "always"] : r.test === "template" ? `template ${String(r.value || "").slice(0, 40)}` : `${TESTS[r.test] || r.test} '${r.value ?? ""}'`;
    return `${what} ${test} → ${(r.actions || []).map((a) => (a.do === "output" ? a.a : ACTIONS[a.do]?.[0] || a.do)).join(", ") || "nothing"}${r.go_on ? " (then go on)" : ""}`;
  }

  _collectRules() {
    return [...this.shadowRoot.querySelectorAll("[data-rule]")].map((box) => {
      const get = (k) => box.querySelector(`[data-r="${k}"]`);
      const rule = { name: get("name").value, select: { by: get("by").value, value: get("svalue")?.value ?? "", which: get("which").value },
        test: get("test").value, value: get("value")?.value ?? "", go_on: get("go_on").checked, actions: [] };
      for (const a of box.querySelectorAll("[data-act]")) {
        const act = {};
        a.querySelectorAll("[data-a]").forEach((el) => { act[el.dataset.a] = el.value; });
        if (act.args !== undefined) { try { act.args = JSON.parse(act.args || "{}"); } catch (err) { act.args = {}; } }
        rule.actions.push(act);
      }
      return rule;
    });
  }

  _drawGrid() {
    const d = this.state.domain, pos = (id) => d.positions[id] || { x: 0, y: 0 };
    const wires = this._wireList(), mirror = this._mirror = this._mirrorSet(wires);
    const port = (c, name, side) => {
      const p = pos(c.id), names = Object.keys(side === "out" ? c.outputs : c.inputs);
      const right = (side === "out") !== mirror.has(c.id); // a mirrored card: in on the right, out on the left
      return { x: p.x + (right ? W : 0), y: p.y + HEAD + ROW * names.indexOf(name) + ROW / 2 };
    };
    const height = (c) => this._height(c);
    this.shadowRoot.querySelector("#cards").innerHTML = d.cards.map((c) => {
      const p = pos(c.id), ins = Object.entries(c.inputs), outs = Object.entries(c.outputs), m = mirror.has(c.id);
      const inX = m ? W : 0, outX = m ? 0 : W, inT = m ? `x="${W - 10}" text-anchor="end"` : 'x="10"', outT = m ? 'x="10"' : `x="${W - 10}" text-anchor="end"`;
      return `<g class="node ${this.sel === c.id ? "sel" : ""}" data-node="${esc(c.id)}" transform="translate(${p.x},${p.y})">
        <rect class="box" width="${W}" height="${height(c)}" rx="8"></rect>
        <text x="10" y="18">${esc(c.title)}</text><text class="sub" x="10" y="30">${esc(this._sub(c))}</text>
        ${ins.map(([n, k], i) => `${k === "read" ? `<polygon class="dot" data-in="${esc(n)}" data-kind="${k}" points="${this._tri(inX, HEAD + ROW * i + ROW / 2, m)}" fill="${KIND_COLOR[k]}"></polygon>` : `<circle class="dot" data-in="${esc(n)}" data-kind="${k}" cx="${inX}" cy="${HEAD + ROW * i + ROW / 2}" r="6" fill="${KIND_COLOR[k] || "#999"}"></circle>`}<text class="port" ${inT} y="${HEAD + ROW * i + ROW / 2 + 3}">${esc(n)}</text>`).join("")}
        ${outs.map(([n, k], i) => `${k === "read" ? `<polygon class="dot" data-out="${esc(n)}" data-kind="${k}" points="${this._tri(outX, HEAD + ROW * i + ROW / 2, m)}" fill="${KIND_COLOR[k]}"></polygon>` : c.square.includes(n) ? `<rect class="dot" data-out="${esc(n)}" data-kind="${k}" x="${outX - 6}" y="${HEAD + ROW * i + ROW / 2 - 6}" width="12" height="12" fill="${KIND_COLOR[k] || "#999"}"></rect>`
          : `<circle class="dot" data-out="${esc(n)}" data-kind="${k}" cx="${outX}" cy="${HEAD + ROW * i + ROW / 2}" r="6" fill="${KIND_COLOR[k] || "#999"}"></circle>`}<text class="port" ${outT} y="${HEAD + ROW * i + ROW / 2 + 3}">${esc(n)}</text>`).join("")}
      </g>`;
    }).join("");
    const routes = this._routes(wires, port);
    this.shadowRoot.querySelector("#wires").innerHTML = wires.map((w) => {
      const path = routes[w.key], sel = this.selWire && `${this.selWire.from}|${this.selWire.to}` === w.key;
      return `<path class="wire ${w.read ? "read" : ""} ${w.loop ? "loop" : ""} ${sel ? "sel" : ""}" data-from="${esc(`${w.a.id}.${w.ap}`)}" data-to="${esc(`${w.b.id}.${w.bp}`)}" d="${path}" stroke="${KIND_COLOR[w.kind] || "#999"}"></path>
        <path class="hit" data-wire="${esc(w.key)}" d="${path}"><title>${esc(w.a.title)}.${esc(w.ap)} → ${esc(w.b.title)}.${esc(w.bp)} (${esc(w.kind)})</title></path>`;
    }).join("");
    this._view();
  }

  // ------------------------------------------------------------------ layout (v1's slots and wire lanes)
  _height(c) { return HEAD + ROW * Math.max(Object.keys(c.inputs).length, Object.keys(c.outputs).length, 1) + 8; }

  _wireList() {
    const byId = Object.fromEntries(this.state.domain.cards.map((c) => [c.id, c]));
    const wires = this.state.domain.wires.map((w) => {
      const [a, ap] = w.from.split("."), [b, bp] = w.to.split(".");
      return byId[a] && byId[b] ? { a: byId[a], ap, b: byId[b], bp, kind: byId[a].outputs[ap], read: byId[b].inputs[bp] === "read", key: `${w.from}|${w.to}` } : null;
    }).filter(Boolean);
    // a loop: wired both ways between two cards (a card and its tool); the host is the card with more loops (then more wires)
    const has = new Set(wires.map((w) => `${w.a.id}>${w.b.id}`)), partners = {}, degree = {};
    for (const w of wires) { degree[w.a.id] = (degree[w.a.id] || 0) + 1; degree[w.b.id] = (degree[w.b.id] || 0) + 1; }
    for (const w of wires) if (w.a.id !== w.b.id && has.has(`${w.b.id}>${w.a.id}`)) (partners[w.a.id] ||= new Set()).add(w.b.id);
    const score = (id) => (partners[id]?.size || 0) * 1000 + (degree[id] || 0);
    for (const w of wires) {
      if (w.a.id === w.b.id || !has.has(`${w.b.id}>${w.a.id}`)) continue;
      const aHosts = score(w.a.id) > score(w.b.id) || (score(w.a.id) === score(w.b.id) && w.a.id < w.b.id);
      Object.assign(w, { loop: true, host: aHosts ? w.a.id : w.b.id, tool: aHosts ? w.b.id : w.a.id });
    }
    // going back: into a card's answers/status input (the reply), or closing a circle
    for (const w of wires) if (!w.loop && ["answers", "status"].includes(w.bp)) w.back = true;
    const next = {};
    for (const w of wires) if (!w.loop && !w.back) (next[w.a.id] ||= []).push(w);
    const state = {};
    const visit = (id) => { state[id] = 1; for (const w of next[id] || []) { if (state[w.b.id] === 1) w.back = true; else if (!state[w.b.id]) visit(w.b.id); } state[id] = 2; };
    for (const c of [...this.state.domain.cards].sort((x, y) => Object.keys(x.inputs).length - Object.keys(y.inputs).length)) if (!state[c.id]) visit(c.id);
    return wires;
  }

  _arrange() {
    // Slots (v1): a column per step in the flow; a card's tools stacked under it in its column; separate chains below
    // each other, unwired cards in a strip at the bottom; rows ordered so wires cross less
    const cards = this.state.domain.cards, byId = Object.fromEntries(cards.map((c) => [c.id, c]));
    const wires = this._wireList(), hangs = new Set(wires.filter((w) => w.loop).map((w) => w.tool)), rank = {};
    for (const w of wires) if (w.loop) hangs.delete(w.host);
    const forward = wires.filter((w) => !w.back && !w.loop && !hangs.has(w.a.id) && !hangs.has(w.b.id) && w.a.id !== w.b.id);
    for (let i = 0; i < cards.length; i++) {
      let changed = false;
      for (const w of forward) if ((rank[w.b.id] ?? 0) < (rank[w.a.id] ?? 0) + 1) { rank[w.b.id] = (rank[w.a.id] ?? 0) + 1; changed = true; }
      if (!changed) break;
    }
    for (let i = 0; i < 3; i++) // a card that only feeds others sits right before the first of them
      for (const c of cards) {
        if (forward.some((w) => w.b.id === c.id)) continue;
        const nx = forward.filter((w) => w.a.id === c.id).map((w) => rank[w.b.id] ?? 0);
        if (nx.length) rank[c.id] = Math.min(...nx) - 1;
      }
    const wired = new Set(wires.flatMap((w) => [w.a.id, w.b.id])), comp = {}, link = {}, comps = [];
    for (const w of wires) if (!w.loop) { (link[w.a.id] ||= []).push(w.b.id); (link[w.b.id] ||= []).push(w.a.id); }
    for (const c of cards) {
      if (!wired.has(c.id) || hangs.has(c.id) || comp[c.id] !== undefined) continue;
      const todo = [c.id], members = [];
      comp[c.id] = comps.length;
      while (todo.length) { const id = todo.pop(); members.push(id); for (const o of link[id] || []) if (comp[o] === undefined && !hangs.has(o)) { comp[o] = comps.length; todo.push(o); } }
      comps.push(members);
    }
    comps.sort((x, y) => (y.some((id) => byId[id].type === "model") - x.some((id) => byId[id].type === "model")) || y.length - x.length);
    const GAP = 30, at = {};
    const toolsOf = (id) => [...new Set(wires.filter((w) => w.loop && w.host === id).map((w) => w.tool))];
    const slotH = (id) => toolsOf(id).reduce((h, t) => h + GAP + this._height(byId[t]), this._height(byId[id]));
    let top = 0;
    for (const members of comps) {
      const base = Math.min(...members.map((id) => rank[id] ?? 0)), cols = {};
      for (const id of members) (cols[(rank[id] ?? 0) - base] ||= []).push(id);
      const order = Object.keys(cols).map(Number).sort((x, y) => x - y), yOf = {};
      const lay = () => { for (const c of order) { let y = 0; for (const id of cols[c]) { yOf[id] = y; y += slotH(id) + GAP; } } };
      lay();
      for (let pass = 0; pass < 6; pass++) // barycenter sweeps, left to right and back
        for (const c of pass % 2 ? [...order].reverse() : order) {
          const near = (id) => {
            const ys = forward.filter((w) => (pass % 2 ? w.a.id === id : w.b.id === id)).map((w) => yOf[pass % 2 ? w.b.id : w.a.id]).filter((v) => v !== undefined);
            return ys.length ? ys.reduce((t, v) => t + v, 0) / ys.length : yOf[id];
          };
          cols[c].sort((x, y) => near(x) - near(y));
          lay();
        }
      let height = 0;
      for (const c of order) {
        let y = top;
        for (const id of cols[c]) {
          at[id] = { x: c * COL, y };
          let yy = y + this._height(byId[id]) + GAP;
          for (const t of toolsOf(id)) if (!at[t]) { at[t] = { x: c * COL, y: yy }; yy += this._height(byId[t]) + GAP; }
          y += slotH(id) + GAP;
        }
        height = Math.max(height, y - top);
      }
      top += height + 90;
    }
    let parked = 0;
    for (const c of cards) if (!at[c.id]) at[c.id] = { x: (parked++) * COL, y: top };
    return at;
  }

  // Mirrored cards (v1, owner: "when a node is only connected in its row its in and outputs should reverse compared
  // to its partner, if it's not the upper one"): every wire of the card goes to cards in its own column above it (a
  // tool under its model): input on the right, output on the left, so the loop runs straight down the sides
  _mirrorSet(wires) {
    const d = this.state.domain, partners = {}, found = new Set();
    for (const w of wires) { (partners[w.a.id] ||= []).push(w.b.id); (partners[w.b.id] ||= []).push(w.a.id); }
    for (const [id, list] of Object.entries(partners)) {
      const p = d.positions[id];
      if (p && list.every((o) => o !== id && d.positions[o] && Math.abs(d.positions[o].x - p.x) < 4 && d.positions[o].y < p.y)) found.add(id);
    }
    return found;
  }

  _snap(id) {
    // a dropped card goes into the nearest column, on the 20 px grid, below any card it would cover there
    const d = this.state.domain, p = d.positions[id], c = this._card(id);
    if (!p || !c) return;
    p.x = Math.round(p.x / COL) * COL; p.y = Math.round(p.y / 20) * 20;
    for (let tries = 0; tries < 50; tries++) {
      const hit = d.cards.find((o) => o.id !== id && d.positions[o.id] && Math.abs(d.positions[o.id].x - p.x) < W
        && d.positions[o.id].y < p.y + this._height(c) + 10 && p.y < d.positions[o.id].y + this._height(o) + 10);
      if (!hit) break;
      p.y = Math.round((d.positions[hit.id].y + this._height(hit) + 20) / 20) * 20;
    }
  }

  _routes(wires, port) {
    // Wire paths (v1: lines should not cross cards or each other): a short wire forward is a curve; a loop between a
    // card and the tools stacked under it runs in a side lane (the deeper the tool, the further out); a wire going
    // back or far runs below the cards between its ends, in a lane of its own
    const d = this.state.domain, pos = (id) => d.positions[id] || { x: 0, y: 0 };
    const box = (c) => { const p = pos(c.id); return { x: p.x + W / 2, top: p.y, bottom: p.y + this._height(c) }; };
    const chain = {}, near = {};
    for (const w of wires) { (near[w.a.id] ||= []).push(w.b.id); (near[w.b.id] ||= []).push(w.a.id); }
    let cid = 0;
    for (const c of d.cards) {
      if (chain[c.id] !== undefined || !near[c.id]) continue;
      const todo = [c.id]; chain[c.id] = cid;
      while (todo.length) for (const o of near[todo.pop()] || []) if (chain[o] === undefined) { chain[o] = cid; todo.push(o); }
      cid++;
    }
    const order = {};
    for (const w of wires) if (w.loop) (order[w.host] ||= new Set()).add(w.tool);
    for (const [m, set] of Object.entries(order)) order[m] = [...set].sort((a, b) => pos(a).y - pos(b).y);
    const path = (pts) => pts.map((q, k) => `${k ? "L" : "M"}${Math.round(q[0])},${Math.round(q[1])}`).join(" ");
    const out = {}, below = [];
    for (const w of wires) {
      const o = port(w.a, w.ap, "out"), i = port(w.b, w.bp, "in");
      const mirA = this._mirror?.has(w.a.id), mirB = this._mirror?.has(w.b.id);
      if (mirA || mirB) { // a mirrored card: both ends on the same side, a lane straight down that side
        const k = w.loop ? (order[w.host] || []).indexOf(w.tool) : 0, rightSide = !mirA; // normal out = right
        const lane = rightSide ? Math.max(o.x, i.x) + 18 + Math.max(0, k) * 7 : Math.min(o.x, i.x) - 18 - Math.max(0, k) * 7;
        out[w.key] = path([[o.x, o.y], [lane, o.y], [lane, i.y], [i.x, i.y]]);
        continue;
      }
      if (w.loop && Math.abs(pos(w.a.id).x - pos(w.b.id).x) < 4) { // stacked under its host
        const k = order[w.host].indexOf(w.tool), t = box(w.a.id === w.tool ? w.a : w.b);
        if (w.b.id === w.tool) { // host → tool: down the right side, in above the tool
          const lane = o.x + 18 + k * 7;
          out[w.key] = path([[o.x, o.y], [lane, o.y], [lane, t.top - 9], [i.x - 12, t.top - 9], [i.x - 12, i.y], [i.x, i.y]]);
        } else { // tool → host: out below the tool, up the left side
          const lane = i.x - 18 - k * 7;
          out[w.key] = path([[o.x, o.y], [o.x + 8, o.y], [o.x + 8, t.bottom + 9], [lane, t.bottom + 9], [lane, i.y], [i.x, i.y]]);
        }
        continue;
      }
      if (i.x > o.x + 20 && i.x - o.x < 3.5 * COL) {
        const bend = Math.max(40, Math.abs(i.x - o.x) / 2);
        out[w.key] = `M${o.x},${o.y} C${o.x + bend},${o.y} ${i.x - bend},${i.y} ${i.x},${i.y}`;
        continue;
      }
      below.push({ o, i, w });
    }
    below.sort((p, q) => Math.abs(p.i.x - p.o.x) - Math.abs(q.i.x - q.o.x)); // short ones inside, long ones outside
    const taken = [];
    for (const { o, i, w } of below) {
      const lo = Math.min(o.x, i.x) - W, hi = Math.max(o.x, i.x) + W;
      let floor = Math.max(o.y, i.y);
      for (const c of d.cards) {
        if (chain[c.id] !== chain[w.a.id]) continue;
        const b = box(c);
        if (b.x > lo && b.x < hi) floor = Math.max(floor, b.bottom);
      }
      for (const t of taken) if (t.hi > lo && t.lo < hi && t.chain === chain[w.a.id]) floor = Math.max(floor, t.y);
      const y = floor + 22, k = taken.length % 6;
      taken.push({ lo, hi, y, chain: chain[w.a.id] });
      const loopsA = (order[w.a.id] || []).length, loopsB = (order[w.b.id] || []).length;
      const ox = o.x + 14 + k * 5 + (loopsA ? 18 + loopsA * 7 : 0), ix = i.x - 14 - k * 5 - (loopsB ? 18 + loopsB * 7 : 0);
      out[w.key] = path([[o.x, o.y], [ox, o.y], [ox, y], [ix, y], [ix, i.y], [i.x, i.y]]);
    }
    return out;
  }

  async _doArrange() {
    this.state.domain.positions = this._arrange();
    await this.ws("aac/positions", { positions: this.state.domain.positions, undoable: true });
    await this._load(); this.render(); this._fit();
  }

  // ------------------------------------------------------------------ resizable panels (v1: drag the edge, double-click resets)
  _widths() { try { return JSON.parse(localStorage.getItem("aac.widths") || "{}"); } catch (err) { return {}; } }

  _applyWidths() { for (const [k, v] of Object.entries(this._widths())) if (v) this.style.setProperty(`--${k}-w`, `${v}px`); }

  _grips() {
    for (const grip of this.shadowRoot.querySelectorAll("[data-grip]")) {
      const key = grip.dataset.grip, el = this.shadowRoot.querySelector(grip.dataset.target), sign = grip.dataset.side === "left" ? -1 : 1;
      const save = (v) => { const all = this._widths(); all[key] = v; try { localStorage.setItem("aac.widths", JSON.stringify(all)); } catch (err) { /* private window */ } };
      grip.addEventListener("dblclick", () => { this.style.removeProperty(`--${key}-w`); save(0); });
      grip.addEventListener("pointerdown", (ev) => {
        ev.preventDefault();
        grip.setPointerCapture(ev.pointerId); grip.classList.add("drag");
        const x0 = ev.clientX, w0 = el.getBoundingClientRect().width;
        let w = w0;
        const move = (e) => { w = Math.max(140, Math.min(700, w0 + sign * (e.clientX - x0))); this.style.setProperty(`--${key}-w`, `${w}px`); };
        const up = () => { grip.classList.remove("drag"); grip.removeEventListener("pointermove", move); save(Math.round(w)); };
        grip.addEventListener("pointermove", move);
        grip.addEventListener("pointerup", up, { once: true });
        grip.addEventListener("pointercancel", up, { once: true });
      });
    }
  }

  _view() { const g = this.shadowRoot.querySelector("#world"); if (g) g.setAttribute("transform", `translate(${this.vp.x},${this.vp.y}) scale(${this.vp.k})`); this._layoutMonitors(); }

  // ------------------------------------------------------------------ wire monitors (v1: a window above a wire with what passes it, live)
  _monitors() { try { return JSON.parse(localStorage.getItem(`aac.monitors.${this.state.domain.id}`) || "[]"); } catch (err) { return []; } }

  _setMonitors(list) { try { localStorage.setItem(`aac.monitors.${this.state.domain.id}`, JSON.stringify(list)); } catch (err) { /* private window */ } }

  async _loadMonitor(key, flash = false) {
    try { (this.monData ||= {})[key] = await this.ws("aac/monitor", { wire: key.split("|")[0] }); } catch (err) { return; }
    this._layoutMonitors();
    if (flash) { const el = this.shadowRoot.querySelector(`[data-mon="${CSS.escape(key)}"]`); el?.classList.remove("flash"); void el?.offsetWidth; el?.classList.add("flash"); }
    if (this.selMon === key && !this.shadowRoot.querySelector(".side")?.contains(this.shadowRoot.activeElement)) {
      const side = this.shadowRoot.querySelector(".side");
      if (side) { side.innerHTML = this._side(); this._bindMonitorSide(); }
    }
  }

  _layoutMonitors() {
    const box = this.shadowRoot.querySelector(".mons");
    if (!box) return;
    const wires = Object.fromEntries(this._wireList().map((w) => [w.key, w])), keys = this._monitors().filter((k) => wires[k]);
    for (const key of keys) {
      const w = wires[key], path = this.shadowRoot.querySelector(`.hit[data-wire="${CSS.escape(key)}"]`);
      if (!path) continue;
      const mid = path.getPointAtLength(path.getTotalLength() / 2); // on the wire as drawn (lanes included)
      const x = mid.x * this.vp.k + this.vp.x, y = mid.y * this.vp.k + this.vp.y;
      let el = box.querySelector(`[data-mon="${CSS.escape(key)}"]`);
      if (!el) {
        el = document.createElement("div"); el.className = "mon"; el.dataset.mon = key;
        el.addEventListener("pointerdown", (e) => e.stopPropagation());
        el.addEventListener("click", () => { this.selMon = key; this.sel = null; this.selWire = null; this.render(); });
        box.appendChild(el);
        if (!this.monData?.[key]) this._loadMonitor(key);
      }
      const m = this.monData?.[key] || {}, last = (m.records || []).at(-1);
      el.classList.toggle("sel", this.selMon === key);
      el.innerHTML = `<div class="t"><b>${esc(w.a.title)} › ${esc(w.b.title)}</b><span>${(m.records || []).length}</span></div>
        <div class="x">${last ? esc(last.text || "(no text)") : '<span class="hint">waiting for something to pass…</span>'}</div>`;
      el.style.left = `${x}px`; el.style.top = `${y}px`;
    }
    for (const el of box.querySelectorAll("[data-mon]")) if (!keys.includes(el.dataset.mon)) el.remove();
  }

  _monitorSide(key) {
    const m = this.monData?.[key] || {}, msg = m.context, [from, to] = key.split("|");
    const name = (id) => this._card(String(id || "").split(".")[0])?.title || id;
    const recs = (m.records || []).slice().reverse().map((r) => `<div class="entry"><div class="k">${esc(new Date(r.time * 1000).toLocaleTimeString())}</div>${esc(r.text)}</div>`).join("");
    const entries = (msg?.entries || []).map((e) => `<details class="entry"><summary><b>${esc(name(e.by))}</b> · ${esc(e.kind)} ${e.tags.map((t) => `#${esc(t)}`).join(" ")} <span class="hint">${esc(e.text.replace(/\s+/g, " ").slice(0, 60))}</span></summary>
        <pre class="req">${esc(e.text)}${Object.keys(e.data || {}).length ? `\n\n${esc(JSON.stringify(e.data, null, 1))}` : ""}</pre></details>`).join("");
    return `<b>Monitor</b><p class="hint">${esc(name(from))}.${esc(from.split(".")[1])} › ${esc(name(to))}.${esc(to.split(".")[1])} · live</p>
      <button class="small" data-monoff="${esc(key)}">Stop monitoring</button>
      ${msg?.entries ? `<label>NEWEST CONTEXT · ${esc(new Date(msg.time * 1000).toLocaleTimeString())}</label><p class="hint">Way: ${esc((msg.way || []).map(name).join(" › "))}</p>${entries}`
        : msg?.clip ? `<label>NEWEST</label><p>${esc(msg.clip)} from ${esc(msg.source)}</p>` : ""}
      <label>PASSED LATELY</label>${recs || '<p class="hint">Nothing yet (kept 10 minutes).</p>'}`;
  }

  _bindMonitorSide() {
    for (const el of this.shadowRoot.querySelectorAll("[data-monoff]")) el.addEventListener("click", () => {
      this._setMonitors(this._monitors().filter((k) => k !== el.dataset.monoff)); this.selMon = null; this.render();
    });
  }

  _world(e) { const r = this.shadowRoot.querySelector("#svg").getBoundingClientRect(); return { x: (e.clientX - r.left - this.vp.x) / this.vp.k, y: (e.clientY - r.top - this.vp.y) / this.vp.k }; }

  _fit() {
    const d = this.state.domain, svg = this.shadowRoot.querySelector("#svg"), placed = d.cards.filter((c) => d.positions[c.id]);
    if (!placed.length || !svg) return;
    const xs = placed.map((c) => d.positions[c.id].x), ys = placed.map((c) => d.positions[c.id].y), r = svg.getBoundingClientRect();
    const x0 = Math.min(...xs) - 90, x1 = Math.max(...xs) + W + 90; // room for the side lanes
    const y0 = Math.min(...ys) - 40, y1 = Math.max(...placed.map((c) => d.positions[c.id].y + this._height(c))) + 120; // and the lanes below
    const k = Math.min(1.2, r.width / (x1 - x0), r.height / (y1 - y0));
    this.vp = { k, x: (r.width - (x1 - x0) * k) / 2 - x0 * k, y: (r.height - (y1 - y0) * k) / 2 - y0 * k };
    this._view();
  }

  async _savePositions() { await this.ws("aac/positions", { positions: this.state.domain.positions }); }

  _gridEvents() {
    const svg = this.shadowRoot.querySelector("#svg");
    if (!svg) return;
    let drag = null;
    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = svg.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top, k = Math.min(2.5, Math.max(0.25, this.vp.k * (e.deltaY < 0 ? 1.1 : 0.9)));
      this.vp = { k, x: mx - (mx - this.vp.x) * k / this.vp.k, y: my - (my - this.vp.y) * k / this.vp.k };
      this._view();
    }, { passive: false });
    svg.addEventListener("pointerdown", (e) => {
      const node = e.target.closest(".node"), out = e.target.dataset?.out, hit = e.target.dataset?.wire;
      svg.setPointerCapture(e.pointerId);
      if (node && out) { drag = { kind: "wire", from: `${node.dataset.node}.${out}`, type: e.target.dataset.kind }; return; }
      if (hit) { const [from, to] = hit.split("|"); this.selWire = { from, to }; this.sel = null; this.selMon = null; this.showValues = false; this.render(); return; }
      if (node) {
        const id = node.dataset.node, p = this.state.domain.positions[id] || (this.state.domain.positions[id] = { x: 0, y: 0 }), w = this._world(e);
        drag = { kind: "move", id, dx: w.x - p.x, dy: w.y - p.y, moved: false };
        return;
      }
      if (this.selType) { const w = this._world(e); this._place(this.selType, w); return; }
      drag = { kind: "pan", x: e.clientX - this.vp.x, y: e.clientY - this.vp.y, at: [e.clientX, e.clientY] };
    });
    svg.addEventListener("pointermove", (e) => {
      if (!drag) { this._ghost(e); return; }
      if (drag.kind === "pan") { this.vp.x = e.clientX - drag.x; this.vp.y = e.clientY - drag.y; this._view(); }
      if (drag.kind === "move") {
        const w = this._world(e), p = this.state.domain.positions[drag.id];
        p.x = Math.round((w.x - drag.dx) / 20) * 20; p.y = Math.round((w.y - drag.dy) / 20) * 20; drag.moved = true; this._dragging = true;
        this._drawGrid();
      }
      if (drag.kind === "wire") {
        const w = this._world(e), [a, ap] = drag.from.split("."), c = this._card(a), p = this.state.domain.positions[a] || { x: 0, y: 0 };
        const oy = p.y + HEAD + ROW * Object.keys(c.outputs).indexOf(ap) + ROW / 2;
        let line = this.shadowRoot.querySelector("#tmpwire");
        if (!line) { line = document.createElementNS("http://www.w3.org/2000/svg", "path"); line.id = "tmpwire"; line.setAttribute("class", "wire"); line.setAttribute("stroke", KIND_COLOR[drag.type] || "#999"); this.shadowRoot.querySelector("#wires").appendChild(line); }
        line.setAttribute("d", `M${p.x + (this._mirror?.has(a) ? 0 : W)},${oy} L${w.x},${w.y}`);
      }
    });
    svg.addEventListener("pointerleave", () => this.shadowRoot.querySelector("#ghost")?.remove());
    svg.addEventListener("pointerup", async (e) => {
      const d = drag; drag = null; this._dragging = false;
      if (!d) return;
      if (d.kind === "pan" && Math.hypot(e.clientX - d.at[0], e.clientY - d.at[1]) < 4 && (this.sel || this.selWire)) { // a click on empty grid lets go
        this.sel = this.selWire = null; this.render(); return;
      }
      if (d.kind === "move") {
        if (d.moved) { this._snap(d.id); this._drawGrid(); await this._savePositions(); }
        else { this.sel = d.id; this.selWire = null; this.selMon = null; this.showValues = false; this.render(); }
      }
      if (d.kind === "wire") {
        this.shadowRoot.querySelector("#tmpwire")?.remove();
        const el = this.shadowRoot.elementFromPoint(e.clientX, e.clientY), node = el?.closest?.(".node"), inp = el?.dataset?.in;
        if (node && inp && el.dataset.kind === d.type) { // the same kind (a read output only into a read input)
          await this.ws("aac/wires", { wires: [...this.state.domain.wires, { from: d.from, to: `${node.dataset.node}.${inp}` }] });
          await this._load(); this.render();
        }
      }
    });
  }

  _spot(at) { return { x: Math.round((at.x - W / 2) / COL) * COL, y: Math.round(at.y / 20) * 20 }; } // where a new card lands

  _ghost(e) { // the picked card type, see-through, where a click would place it
    const t = this.selType && this._type(this.selType);
    let g = this.shadowRoot.querySelector("#ghost");
    if (!t) { g?.remove(); return; }
    const p = this._spot(this._world(e)), h = this._height({ inputs: t.inputs, outputs: t.outputs });
    if (!g) { g = document.createElementNS("http://www.w3.org/2000/svg", "g"); g.id = "ghost"; g.setAttribute("pointer-events", "none"); this.shadowRoot.querySelector("#cards").appendChild(g); }
    g.setAttribute("transform", `translate(${p.x},${p.y})`);
    g.innerHTML = `<rect class="box" width="${W}" height="${h}" rx="8" style="opacity:.45;stroke-dasharray:5 4"></rect><text x="10" y="18" style="opacity:.6">${esc(t.title)}</text>`;
  }

  _unpick() { this.selType = null; this.shadowRoot.querySelector("#ghost")?.remove(); this.render(); }

  async _place(type, at) {
    const card = await this.ws("aac/card/add", { card_type: type, ...this._spot(at) });
    this.selType = null; this.sel = card.id;
    await this._load(); this.render();
  }

  async _saveSetting(el) {
    const c = this._card(this.sel), key = el.dataset.set;
    let value = el.type === "checkbox" ? el.checked : el.value;
    if (el.dataset.num !== undefined) value = Number(value);
    if (el.dataset.list !== undefined) value = value.split(",").map((x) => x.trim()).filter(Boolean);
    if (el.dataset.access !== undefined) value = Object.fromEntries(value.split("\n").map((l) => l.split(":").map((x) => x.trim())).filter(([e, l]) => e && ["hidden", "read", "full"].includes(l)));
    if (el.dataset.outputs !== undefined) value = value.split("\n").map((l) => { const [n, ...r] = l.split(":"); return { name: n.trim().replace(/[^\w-]/g, "_"), description: r.join(":").trim() }; }).filter((o) => o.name);
    if (el.dataset.json !== undefined) { try { value = JSON.parse(value || "[]"); } catch (err) { alert(`Not valid: ${err.message}`); return; } }
    await this.ws("aac/card/update", { card: c.id, settings: { [key]: value } });
    await this._load(); this.render();
  }

  // ------------------------------------------------------------------ events
  _bind() {
    const $ = (s) => this.shadowRoot.querySelector(s), $$ = (s) => this.shadowRoot.querySelectorAll(s);
    this._grips();
    $("#scrim")?.addEventListener("click", () => { this.navOpen = false; this.shadowRoot.querySelector(".root").classList.remove("navopen"); });
    $("#habtn")?.addEventListener("click", () => this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })));
    $("#domain")?.addEventListener("change", async (e) => { await this.ws("aac/domain/switch", { domain_id: e.target.value }); this._els = {}; await this._load(); this.go("card", null, null); });
    $("#clone")?.addEventListener("click", async () => { const name = prompt("Name of the copy?", `${this.state.domain.name} (test)`); if (name) { await this.ws("aac/domain/clone", { name }); await this._load(); this.render(); } });
    // models
    $("#probe")?.addEventListener("click", async () => {
      try { this.probed = (await this.ws("aac/models/probe", { base_url: $("#m_url").value, api_key: $("#m_key").value || null })).models; this.modelForm = { ...(this.modelForm || {}), base_url: $("#m_url").value }; this.render(); }
      catch (e) { alert(`No answer: ${e.message || e}`); }
    });
    $("#savemodel")?.addEventListener("click", async () => {
      const model = { name: $("#m_name").value || $("#m_model").value, base_url: $("#m_url").value, model: $("#m_model").value, api_key: $("#m_key").value || null,
        tags: [...this.shadowRoot.querySelectorAll("[data-mtag]")].filter((x) => x.checked).map((x) => x.dataset.mtag),
        parallel: Math.max(1, Number($("#m_parallel").value) || 1) };
      if (this.modelForm?.id) model.id = this.modelForm.id;
      if (!model.model) { alert("Pick or type a model"); return; }
      await this.ws("aac/models/save", { model }); this.modelForm = null; this.probed = null; await this._load(); this.render();
    });
    $("#cancelmodel")?.addEventListener("click", () => { this.modelForm = null; this.probed = null; this.render(); });
    $$("[data-editmodel]").forEach((el) => el.addEventListener("click", () => { this.modelForm = { ...this.state.models.find((m) => m.id === el.dataset.editmodel) }; this.probed = null; this.render(); }));
    $$("[data-delmodel]").forEach((el) => el.addEventListener("click", async () => { if (confirm("Delete this model?")) { await this.ws("aac/models/delete", { model_id: el.dataset.delmodel }); await this._load(); this.render(); } }));
    // nodes
    $$("[data-pick]").forEach((el) => el.addEventListener("click", () => { this.selType = this.selType === el.dataset.pick ? null : el.dataset.pick; this.render(); }));
    $("#undo")?.addEventListener("click", async () => { await this.ws("aac/undo"); await this._load(); this.render(); });
    $("#fit")?.addEventListener("click", () => this._fit());
    $("#unpick")?.addEventListener("click", () => this._unpick());
    $$("[data-skin]").forEach((el) => el.addEventListener("change", async () => { await this.ws("aac/appearance/set", { skin: el.dataset.skin }); }));
    $("#values")?.addEventListener("click", () => { this.showValues = !this.showValues; this.values = null; this.render(); if (this.showValues) this._loadValues(); });
    $("#valrefresh")?.addEventListener("click", () => this._loadValues());
    $("#valclose")?.addEventListener("click", () => { this.showValues = false; this.render(); });
    $("#arrange")?.addEventListener("click", () => this._doArrange());
    $("#export")?.addEventListener("click", async () => {
      const { setup } = await this.ws("aac/setup/export"), a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([JSON.stringify(setup, null, 1)], { type: "application/json" }));
      a.download = `aac-${setup.domain.replace(/\W+/g, "-").toLowerCase()}-${new Date().toISOString().slice(0, 10)}.json`;
      a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    });
    $("#import")?.addEventListener("click", () => $("#importfile").click());
    $("#importfile")?.addEventListener("change", async (e) => {
      const file = e.target.files[0];
      if (!file || !confirm(`Load "${file.name}" into this domain? Its cards and wires replace the current ones (Undo goes back).`)) return;
      try { await this.ws("aac/setup/import", { setup: JSON.parse(await file.text()) }); } catch (err) { alert(`Could not load it: ${err.message || err}`); return; }
      this.sel = null; await this._load(); this.render(); this._fit();
    });
    $("#addtag")?.addEventListener("click", async () => { const t = $("#newtag").value.trim(); if (t) { await this.ws("aac/tags/add", { tag: t }); await this._load(); this.render(); } });
    $("#newtag")?.addEventListener("keydown", (e) => { if (e.key === "Enter") $("#addtag").click(); });
    $$("[data-deltag]").forEach((el) => el.addEventListener("click", async () => { await this.ws("aac/tags/remove", { tag: el.dataset.deltag }); await this._load(); this.render(); }));
    $$("[data-slot]").forEach((el) => el.addEventListener("click", async (e) => {
      const slot = el.dataset.slot;
      if (e.shiftKey) { if (confirm(`Load slot ${slot}? (Undo brings the current setup back)`)) await this.ws("aac/slot", { slot, action: "load" }); }
      else await this.ws("aac/slot", { slot, action: "save" });
      await this._load(); this.render();
    }));
    $("#title")?.addEventListener("change", async (e) => { await this.ws("aac/card/update", { card: this.sel, title: e.target.value }); await this._load(); this.render(); });
    $$("[data-set]").forEach((el) => el.addEventListener("change", () => this._saveSetting(el)));
    // a card's list of models: saved whole on any change
    const mlist = () => [...$$("[data-ml]")].map((row) => ({ model: row.querySelector("[data-mlm]").value, queue: row.querySelector("[data-mlq]").checked }));
    const mlsave = (list) => saveList("models", list);
    $$("[data-ml] select, [data-ml] input").forEach((el) => el.addEventListener("change", () => mlsave(mlist())));
    $("#mladd")?.addEventListener("click", () => { const c = this._card(this.sel), t = this._type(c.type), f = t.settings[$("#mladd").dataset.key];
      const fit = this.state.models.filter((m) => (m.tags || []).includes(f.tag)), have = mlist().map((e) => e.model);
      mlsave([...mlist(), { model: (fit.find((m) => !have.includes(m.id)) || fit[0]).id, queue: true }]); });
    $$("[data-mlup]").forEach((el) => el.addEventListener("click", () => { const l = mlist(), i = Number(el.dataset.mlup); [l[i - 1], l[i]] = [l[i], l[i - 1]]; mlsave(l); }));
    $$("[data-mldel]").forEach((el) => el.addEventListener("click", () => mlsave(mlist().filter((_, i) => i !== Number(el.dataset.mldel)))));
    $$("[data-tag]").forEach((el) => el.addEventListener("change", async () => {
      const key = el.dataset.tag, tags = [...$$(`[data-tag="${key}"]`)].filter((x) => x.checked).map((x) => x.value);
      await this.ws("aac/card/update", { card: this.sel, settings: { [key]: tags } }); await this._load(); this.render();
    }));
    $("#remove")?.addEventListener("click", () => this._removeCard());
    $("#unwire")?.addEventListener("click", () => this._unwire());
    // schedule entries and watches: the whole list is saved on any change
    const collect = (sel, attr) => [...$$(sel)].map((box) => {
      const o = {};
      box.querySelectorAll(`[${attr}]`).forEach((el) => { o[el.getAttribute(attr)] = el.type === "checkbox" ? el.checked : el.value; });
      if (attr === "data-e") o.weekdays = [...box.querySelectorAll("[data-day]")].filter((x) => x.checked).map((x) => Number(x.dataset.day));
      if (o.cooldown !== undefined) o.cooldown = Number(o.cooldown) || 300;
      return o;
    });
    const saveList = async (key, list) => { await this.ws("aac/card/update", { card: this.sel, settings: { [key]: list } }); await this._load(); this.render(); };
    $$("[data-entry] input, [data-entry] textarea").forEach((el) => el.addEventListener("change", () => saveList("entries", collect("[data-entry]", "data-e"))));
    $$("[data-watch] input, [data-watch] textarea").forEach((el) => el.addEventListener("change", () => saveList("watches", collect("[data-watch]", "data-w"))));
    // Conditional rules: the whole list is saved on any change
    $$("[data-fold]").forEach((el) => el.addEventListener("toggle", () => { const set = ((this.openRules ||= {})[this.sel] ||= new Set()); if (el.open) set.add(Number(el.dataset.fold)); else set.delete(Number(el.dataset.fold)); }));
    $$("[data-rule] input, [data-rule] select").forEach((el) => el.addEventListener("change", () => saveList("rules", this._collectRules())));
    $("#addrule")?.addEventListener("click", () => { const rules = this._collectRules(); ((this.openRules ||= {})[this.sel] ||= new Set()).add(rules.length); saveList("rules", [...rules, { name: "", select: { by: "kind", value: "said", which: "newest" }, test: "contains", value: "", actions: [{ do: "output", a: "out" }] }]); });
    $$("[data-delrule]").forEach((el) => el.addEventListener("click", () => { if (confirm("Remove this rule?")) saveList("rules", this._collectRules().filter((_, i) => i !== Number(el.dataset.delrule))); }));
    $$("[data-moverule]").forEach((el) => el.addEventListener("click", () => { const r = this._collectRules(), i = Number(el.dataset.moverule), j = i + Number(el.dataset.dir); [r[i], r[j]] = [r[j], r[i]]; saveList("rules", r); }));
    $$("[data-addact]").forEach((el) => el.addEventListener("click", () => { const r = this._collectRules(); r[Number(el.dataset.addact)].actions.push({ do: "tag", a: "" }); saveList("rules", r); }));
    $$("[data-delact]").forEach((el) => el.addEventListener("click", () => { const r = this._collectRules(), i = Number(el.closest("[data-rule]").dataset.rule); r[i].actions.splice(Number(el.dataset.delact), 1); saveList("rules", r); }));
    $("#addentry")?.addEventListener("click", () => saveList("entries", [...collect("[data-entry]", "data-e"), { name: "New", at: "08:00", weekdays: [], text: "", enabled: true }]));
    $("#addwatch")?.addEventListener("click", () => saveList("watches", [...collect("[data-watch]", "data-w"), { name: "New", entity_id: "", to: "", text: "", cooldown: 300 }]));
    $$("[data-delentry]").forEach((el) => el.addEventListener("click", () => saveList("entries", collect("[data-entry]", "data-e").filter((_, i) => i !== Number(el.dataset.delentry)))));
    $$("[data-delwatch]").forEach((el) => el.addEventListener("click", () => saveList("watches", collect("[data-watch]", "data-w").filter((_, i) => i !== Number(el.dataset.delwatch)))));
    // memory, file
    // audio
    const audioSet = async (data) => { this.audio = await this.ws("aac/audio/settings", data); this.render(); };
    const engines = () => (this.audio.settings.stt || []).filter((e) => e.startsWith("stt."));
    $("#sttadd")?.addEventListener("change", (e) => e.target.value && audioSet({ stt: [...engines(), e.target.value] }));
    $$("[data-sttdel]").forEach((el) => el.addEventListener("click", () => audioSet({ stt: engines().filter((_, i) => i !== Number(el.dataset.sttdel)) })));
    $$("[data-sttup]").forEach((el) => el.addEventListener("click", () => { const l = engines(), i = Number(el.dataset.sttup); if (i > 0) { [l[i - 1], l[i]] = [l[i], l[i - 1]]; audioSet({ stt: l }); } }));
    $("#ttsset")?.addEventListener("change", (e) => audioSet({ tts: e.target.value || null }));
    $$("[data-forget]").forEach((el) => el.addEventListener("click", async () => { if (confirm("Forget this satellite? (It comes back when it connects again.)")) { this.audio = await this.ws("aac/audio/forget", { source: el.dataset.forget }); this.render(); } }));
    $("#volume")?.addEventListener("input", (e) => { $("#volval").textContent = e.target.value; });
    $("#volume")?.addEventListener("change", (e) => audioSet({ volume: Number(e.target.value) }));

    $("#monitor")?.addEventListener("click", () => {
      const key = `${this.selWire.from}|${this.selWire.to}`, list = this._monitors();
      this._setMonitors(list.includes(key) ? list.filter((k) => k !== key) : [...list, key]);
      if (!list.includes(key)) { this.selMon = key; this.selWire = null; this._loadMonitor(key); }
      this.render();
    });
    this._bindMonitorSide();
    if (!this._keys) {
      this._keys = true;
      window.addEventListener("keydown", (e) => { // Delete: the selected wire or card goes (Undo brings it back)
        if (this.view.kind !== "nodes" || /INPUT|TEXTAREA|SELECT/.test(e.composedPath()[0]?.tagName)) return;
        if (e.key === "Escape") { // put back a picked card type, or let go of the selected card or wire
          if (this.selType) this._unpick();
          else if (this.sel || this.selWire || this.selMon || this.showValues) { this.sel = this.selWire = this.selMon = null; this.showValues = false; this.render(); }
          return;
        }
        if (!(e.key === "Delete" || e.key === "Backspace")) return;
        if (this.selWire) this._unwire();
        else if (this.sel) this._removeCard();
      });
    }
    if (this.view.kind === "nodes") { this._gridEvents(); if (!this._fitted) { this._fitted = true; requestAnimationFrame(() => this._fit()); } }
  }

  async _unwire() {
    const w = this.selWire;
    await this.ws("aac/wires", { wires: this.state.domain.wires.filter((x) => !(x.from === w.from && x.to === w.to)) });
    this.selWire = null; await this._load(); this.render();
  }
}

if (!customElements.get("aac-panel")) customElements.define("aac-panel", AacPanel);

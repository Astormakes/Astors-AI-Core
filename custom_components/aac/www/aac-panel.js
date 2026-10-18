// AAC – Astor's AI Core: the panel.
// Plain web component, no build step, no external resources (works offline).
// Talks to the backend via the aac/* WebSocket commands.

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const ICONS = {
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  eyeOff: '<path d="M3 3l18 18"/><path d="M10.6 5.1A10 10 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4.2M6.6 6.6C3.8 8.4 2 12 2 12s3.6 7 10 7a9.7 9.7 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  setup: '<path d="M12 2l8.7 5v10L12 22l-8.7-5V7z"/><path d="M12 8v4l3 2"/>',
  chat: '<path d="M4 4h16v12H8l-4 4z"/><path d="M8 9h8M8 12h5"/>',
  workers: '<rect x="4" y="4" width="16" height="16"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 1v4M12 19v4M4.2 4.2l2.8 2.8M17 17l2.8 2.8M1 12h4M19 12h4M4.2 19.8L7 17M17 7l2.8-2.8"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4v5h-5"/>',
  menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
  send: '<path d="M3 11l18-8-8 18-2-8z"/>',
};
const icon = (name, cls = "") =>
  `<svg class="ico ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${ICONS[name]}</svg>`;

const STYLE = `
:host {
  --bg: #07060d; --bg2: #0d0b18; --bg3: #141127;
  --cyan: #00f0ff; --magenta: #ff2bd6; --green: #39ff88; --amber: #ffb000; --red: #ff3860;
  --text: #cfeff5; --dim: #6f8a99; --line: rgba(0, 240, 255, .45);
  --glow: 0 0 4px rgba(0, 240, 255, .55), 0 0 12px rgba(0, 240, 255, .25);
  --font: "JetBrains Mono", "Fira Code", "Cascadia Code", "Source Code Pro", ui-monospace, Menlo, Consolas, monospace;
  display: block; height: 100%; background: var(--bg); color: var(--text); font-family: var(--font);
}
* { box-sizing: border-box; }
.root { position: relative; display: flex; height: 100%; overflow: hidden; }
.root::after { /* scanlines */
  content: ""; position: absolute; inset: 0; pointer-events: none; z-index: 50;
  background: repeating-linear-gradient(0deg, rgba(255,255,255,.025) 0 1px, transparent 1px 3px);
}
.ico { width: 18px; height: 18px; flex: none; filter: drop-shadow(0 0 3px currentColor); }

/* ---------- sidebar ---------- */
nav {
  width: 300px; flex: none; display: flex; flex-direction: column; background: var(--bg2);
  border-right: 1px solid var(--line); box-shadow: 1px 0 14px rgba(0, 240, 255, .18);
}
.brand { display: flex; align-items: center; gap: 10px; padding: 14px 14px 12px; border-bottom: 1px solid var(--line); }
.brand .name { font-size: 20px; font-weight: 700; letter-spacing: 4px; color: var(--magenta);
  text-shadow: 0 0 4px var(--magenta), 0 0 14px rgba(255, 43, 214, .6); }
.brand .ver { font-size: 11px; color: var(--dim); }
.brand .sp { flex: 1; }
button.icon-btn { background: none; border: 1px solid transparent; color: var(--cyan); padding: 4px; cursor: pointer; display: grid; place-items: center; }
button.icon-btn:hover { border-color: var(--line); box-shadow: var(--glow); }
button.icon-btn.on { color: var(--magenta); }
.scroll { flex: 1; overflow-y: scroll; padding: 6px 0 20px; scrollbar-width: thin; scrollbar-color: var(--cyan) transparent; }
.scroll::-webkit-scrollbar { width: 6px; }
.scroll::-webkit-scrollbar-track { background: rgba(0, 240, 255, .05); border-left: 1px solid rgba(0, 240, 255, .15); }
.scroll::-webkit-scrollbar-thumb { background: var(--cyan); box-shadow: 0 0 8px var(--cyan); border-radius: 3px; }
.seg { margin-top: 10px; }
.seg.hidden-seg { opacity: .45; }
.seg-title { display: flex; align-items: center; gap: 10px; padding: 8px 16px; font-size: 14px; font-weight: 700; letter-spacing: 3px;
  color: var(--cyan); text-shadow: 0 0 4px var(--cyan), 0 0 14px rgba(0, 240, 255, .55); }
.seg-title .badge { margin-left: auto; font-size: 11px; letter-spacing: 1px; color: var(--amber); text-shadow: 0 0 6px var(--amber); }
.seg-title .icon-btn { margin-left: auto; }
.seg-line { height: 1px; margin: 0 16px 4px; background: linear-gradient(90deg, var(--cyan), transparent); box-shadow: 0 0 6px var(--cyan); }
.sub { padding: 6px 16px 2px 44px; font-size: 10px; letter-spacing: 3px; color: var(--dim); display: flex; align-items: center; }
.sub .icon-btn { margin-left: auto; padding: 2px; }
.tab { display: flex; align-items: center; gap: 8px; padding: 6px 16px 6px 44px; font-size: 13px; cursor: pointer; color: var(--text);
  text-shadow: 0 0 3px rgba(0, 240, 255, .35); border-left: 2px solid transparent; }
.tab:hover { background: rgba(0, 240, 255, .06); }
.tab.active { color: var(--magenta); border-left-color: var(--magenta); text-shadow: 0 0 4px var(--magenta);
  background: linear-gradient(90deg, rgba(255, 43, 214, .14), transparent); }
.tab .grow { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tab.soon { color: var(--dim); text-shadow: none; }
.dot { width: 7px; height: 7px; border-radius: 50%; flex: none; background: var(--dim); }
.dot.ok { background: var(--green); box-shadow: 0 0 6px var(--green); }
.dot.bad { background: var(--red); box-shadow: 0 0 6px var(--red); }
.dot.open { background: var(--amber); box-shadow: 0 0 6px var(--amber); }
.mark { width: 14px; text-align: center; flex: none; }
.mark.done { color: var(--green); text-shadow: 0 0 6px var(--green); }
.mark.open { color: var(--amber); text-shadow: 0 0 6px var(--amber); }
.mark.skip { color: var(--dim); }
.empty { padding: 4px 16px 4px 44px; font-size: 12px; color: var(--dim); }

/* ---------- main ---------- */
main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
header { display: flex; align-items: center; gap: 10px; padding: 14px 24px; border-bottom: 1px solid var(--line); min-height: 57px; }
header h1 { margin: 0; font-size: 16px; letter-spacing: 3px; font-weight: 700; color: var(--cyan); text-shadow: var(--glow); }
header .meta { margin-left: auto; font-size: 12px; color: var(--dim); }
.content { flex: 1; overflow-y: auto; padding: 24px; scrollbar-width: thin; scrollbar-color: var(--cyan) transparent; }
.card { position: relative; border: 1px solid var(--line); background: rgba(13, 11, 24, .85); padding: 18px; margin-bottom: 18px;
  box-shadow: 0 0 10px rgba(0, 240, 255, .12), inset 0 0 18px rgba(0, 240, 255, .05); max-width: 900px; }
.card::before, .card::after { content: ""; position: absolute; width: 10px; height: 10px; border: 1px solid var(--magenta); }
.card::before { top: -1px; left: -1px; border-right: 0; border-bottom: 0; }
.card::after { bottom: -1px; right: -1px; border-left: 0; border-top: 0; }
.card h2 { margin: 0 0 12px; font-size: 13px; letter-spacing: 3px; color: var(--cyan); text-shadow: var(--glow); }
.card p, .hint { color: var(--dim); font-size: 13px; line-height: 1.5; margin: 0 0 12px; }
label { display: block; font-size: 11px; letter-spacing: 2px; color: var(--dim); margin: 14px 0 6px; text-transform: uppercase; }
input, select, textarea { width: 100%; background: var(--bg); color: var(--text); border: 1px solid rgba(0, 240, 255, .35);
  padding: 8px 10px; font: inherit; font-size: 13px; outline: none; }
input:focus, select:focus, textarea:focus { border-color: var(--cyan); box-shadow: var(--glow); }
textarea { min-height: 140px; resize: vertical; }
.row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.row > * { flex: 1; min-width: 140px; }
.wrow { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 8px 0; flex-wrap: wrap; }
.wrow .actions { margin: 0; }
textarea { scrollbar-width: thin; scrollbar-color: var(--cyan) transparent; }
.check { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text); text-transform: none; letter-spacing: 0; margin: 6px 0; }
.check input { width: auto; accent-color: var(--magenta); }
.btn { background: transparent; color: var(--cyan); border: 1px solid var(--cyan); padding: 8px 16px; font: inherit; font-size: 12px;
  letter-spacing: 2px; text-transform: uppercase; cursor: pointer; text-shadow: 0 0 4px var(--cyan); box-shadow: 0 0 6px rgba(0, 240, 255, .25); }
.btn:hover { background: rgba(0, 240, 255, .1); box-shadow: var(--glow); }
.btn.primary { color: var(--magenta); border-color: var(--magenta); text-shadow: 0 0 4px var(--magenta); box-shadow: 0 0 8px rgba(255, 43, 214, .35); }
.btn.primary:hover { background: rgba(255, 43, 214, .12); }
.btn.danger { color: var(--red); border-color: var(--red); text-shadow: 0 0 4px var(--red); box-shadow: none; }
.btn:disabled { opacity: .4; cursor: default; }
.actions { display: flex; gap: 10px; margin-top: 18px; flex-wrap: wrap; }
.msg { font-size: 12px; margin-top: 10px; }
.msg.err { color: var(--red); text-shadow: 0 0 4px rgba(255, 56, 96, .6); }
.msg.ok { color: var(--green); text-shadow: 0 0 4px rgba(57, 255, 136, .6); }
.step { display: flex; gap: 14px; align-items: flex-start; padding: 12px 0; border-bottom: 1px dashed rgba(0, 240, 255, .2); cursor: pointer; }
.step:last-child { border-bottom: 0; }
.step .t { font-size: 14px; }
.step .h { font-size: 12px; color: var(--dim); margin-top: 3px; }
.kv { display: grid; grid-template-columns: 140px 1fr; gap: 6px 12px; font-size: 13px; }
.kv .k { color: var(--dim); }
.kv .v { word-break: break-all; }

/* ---------- chat ---------- */
.chat { display: flex; flex-direction: column; height: 100%; }
.log { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 14px; scrollbar-width: thin; scrollbar-color: var(--cyan) transparent; }
.bubble { max-width: 760px; padding: 10px 14px; font-size: 14px; line-height: 1.55; white-space: pre-wrap; word-wrap: break-word; }
.bubble.user { align-self: flex-end; border: 1px solid rgba(255, 43, 214, .6); box-shadow: 0 0 8px rgba(255, 43, 214, .25); }
.bubble.aac { align-self: flex-start; border: 1px solid var(--line); box-shadow: 0 0 8px rgba(0, 240, 255, .18); }
.bubble.err { border-color: var(--red); color: var(--red); }
.bubble .who { font-size: 10px; letter-spacing: 3px; color: var(--dim); margin-bottom: 4px; }
.cursor { display: inline-block; width: 8px; height: 15px; background: var(--cyan); box-shadow: 0 0 6px var(--cyan); vertical-align: -2px; animation: blink 1s steps(1) infinite; }
@keyframes blink { 50% { opacity: 0; } }
.composer { display: flex; gap: 10px; padding: 14px 24px 20px; border-top: 1px solid var(--line); }
.composer textarea { min-height: 44px; height: 44px; max-height: 200px; }
.welcome { margin: auto; text-align: center; color: var(--dim); font-size: 13px; }
.welcome .big { font-size: 28px; letter-spacing: 8px; color: var(--magenta); text-shadow: 0 0 6px var(--magenta), 0 0 20px rgba(255, 43, 214, .5); margin-bottom: 10px; }

/* ---------- pipeline flow ---------- */
.flow { display: flex; align-items: stretch; flex-wrap: wrap; gap: 0; margin: 6px 0 4px; }
.node { border: 1px solid var(--line); padding: 8px 10px; min-width: 130px; max-width: 190px; font-size: 12px; background: var(--bg); }
.node .k { font-size: 9px; letter-spacing: 2px; color: var(--dim); text-transform: uppercase; }
.node .v { margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.node.ok { border-color: var(--green); box-shadow: 0 0 8px rgba(57, 255, 136, .3); }
.node.missing { border-color: var(--red); box-shadow: 0 0 8px rgba(255, 56, 96, .35); }
.node.off { border-style: dashed; border-color: rgba(111, 138, 153, .6); color: var(--dim); }
.node.aac { border-color: var(--magenta); box-shadow: 0 0 10px rgba(255, 43, 214, .45); }
.link { align-self: center; width: 26px; height: 1px; background: var(--cyan); box-shadow: 0 0 6px var(--cyan); }
.tag { font-size: 10px; letter-spacing: 2px; padding: 2px 6px; border: 1px solid currentColor; margin-left: 8px; }
.tag.pref { color: var(--amber); }
.tag.aac { color: var(--magenta); }

/* ---------- narrow ---------- */
button.menu-btn { display: none; }
@media (max-width: 800px) {
  nav { position: absolute; z-index: 40; top: 0; bottom: 0; left: 0; transform: translateX(-100%); transition: transform .2s; }
  .root.nav-open nav { transform: none; }
  button.menu-btn { display: grid; }
  .content, .log { padding: 16px; }
  header { padding: 12px 16px; }
  .composer { padding: 12px 16px 16px; }
}
`;

class AacPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._view = "chat";
    this._showAll = false;
    this._navOpen = false;
    this._state = null;
    this._workers = null;
    this._pipelines = null;
    this._chat = { messages: [], conversationId: null, busy: false };
    this._form = null; // worker form state
    this._flash = null;
    this._loaded = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._loaded) {
      this._loaded = true;
      try { this._showAll = localStorage.getItem("aac.showAll") === "1"; } catch (e) { /* ignore */ }
      this._render();
      this._loadAll();
    }
    const mb = this.shadowRoot.querySelector("ha-menu-button");
    if (mb) mb.hass = hass;
  }
  set narrow(v) { this._narrow = v; }
  set panel(v) { this._panel = v; }

  _ws(type, data = {}) { return this._hass.callWS({ type, ...data }); }

  async _loadAll() {
    await Promise.all([this._loadState(), this._loadWorkers(), this._loadPipelines()]);
    // First visit: start in SETUP while anything is open
    if (this._view === "chat" && this._setupOpen() && !this._chat.messages.length) this._view = "setup";
    this._render();
  }
  async _loadState() {
    try { this._state = await this._ws("aac/state"); } catch (e) { this._state = { error: e.message || String(e) }; }
    this._render();
  }
  async _loadWorkers() {
    this._workers = null;
    this._render();
    try { this._workers = await this._ws("aac/workers"); } catch (e) { this._workers = { manual: [], auto: [], error: e.message }; }
    this._render();
  }
  async _loadPipelines() {
    try { this._pipelines = await this._ws("aac/pipelines"); } catch (e) { this._pipelines = []; }
    this._render();
  }

  _steps() { return this._state?.setup || []; }
  _openSteps() { return this._steps().filter((s) => !s.done && !s.skipped); }
  _setupOpen() { return this._openSteps().length > 0; }
  _stepOpen(id) { return this._openSteps().some((s) => s.id === id); }

  _allWorkers() {
    const w = this._workers || { manual: [], auto: [] };
    const list = w.manual.map((m) => ({ id: m.id, label: `${m.name}`, detail: m.model, online: m.online, kind: "manual" }));
    for (const srv of w.auto) {
      for (const a of srv.workers) list.push({ id: a.id, label: a.model, detail: srv.label, online: srv.online, kind: "auto" });
    }
    return list;
  }
  _workerLabel(id) {
    if (!id) return "none";
    const w = this._allWorkers().find((x) => x.id === id);
    if (w) return w.label;
    return id.startsWith("auto|") ? id.split("|").slice(2).join("|") : id;
  }

  _go(view) {
    this._view = view;
    this._navOpen = false;
    this._flash = null;
    if (view === "workers/new") this._form = { name: "", base_url: "", api_key: "", model: "", models: [], msg: null };
    if (view.startsWith("worker/edit/")) {
      const m = (this._workers?.manual || []).find((x) => x.id === view.slice(12));
      this._form = m ? { id: m.id, name: m.name, base_url: m.base_url, api_key: "", has_key: m.has_key, model: m.model, models: [m.model], msg: null } : null;
    }
    if (view === "settings/pipelines") this._loadPipelines();
    this._render();
  }

  // ---------------------------------------------------------------- render
  _render() {
    if (!this._hass) return;
    const sr = this.shadowRoot;
    const chatInput = sr.querySelector("#chat-input");
    const draft = chatInput ? chatInput.value : "";
    const hadFocus = chatInput && sr.activeElement === chatInput;
    const navScroll = sr.querySelector("nav .scroll")?.scrollTop || 0;
    // Keep what the user typed when the same view re-renders (e.g. a scan finished)
    const keep = {};
    const sameView = this._renderedView === this._view;
    if (sameView) {
      sr.querySelectorAll(".content input, .content textarea, .content select").forEach((el) => {
        if (!el.id && !el.classList.contains("m-api")) return;
        keep[el.id || `api:${el.value}`] = el.type === "checkbox" ? el.checked : el.value;
      });
    }
    const contentScroll = sameView ? sr.querySelector(".content")?.scrollTop || 0 : 0;

    sr.innerHTML = `<style>${STYLE}</style>
      <div class="root ${this._navOpen ? "nav-open" : ""}">
        ${this._renderNav()}
        <main>${this._renderMain()}</main>
      </div>`;

    const nav = sr.querySelector("nav .scroll");
    if (nav) nav.scrollTop = navScroll;
    sr.querySelectorAll(".content input, .content textarea, .content select").forEach((el) => {
      const k = el.id || (el.classList.contains("m-api") ? `api:${el.value}` : null);
      if (!k || !(k in keep)) return;
      if (el.type === "checkbox") el.checked = keep[k];
      else if (el.tagName !== "SELECT" || [...el.options].some((o) => o.value === keep[k])) el.value = keep[k];
    });
    const content = sr.querySelector(".content");
    if (content) content.scrollTop = contentScroll;
    this._renderedView = this._view;
    const input = sr.querySelector("#chat-input");
    if (input) { input.value = draft; if (hadFocus) input.focus(); }
    const log = sr.querySelector(".log");
    if (log) log.scrollTop = log.scrollHeight;
    const mb = sr.querySelector("ha-menu-button");
    if (mb) { mb.hass = this._hass; mb.narrow = this._narrow; }
    this._bind();
  }

  _tab(view, label, extra = "", cls = "") {
    return `<div class="tab ${cls} ${this._view === view ? "active" : ""}" data-go="${esc(view)}">${extra}<span class="grow">${esc(label)}</span></div>`;
  }

  _renderNav() {
    const steps = this._steps();
    const open = this._openSteps();
    const showSetup = open.length > 0 || this._showAll;
    const doneCount = steps.filter((s) => s.done).length;
    const w = this._workers;

    const setup = !showSetup ? "" : `
      <div class="seg ${open.length ? "" : "hidden-seg"}">
        <div class="seg-title">${icon("setup")}SETUP<span class="badge">${doneCount}/${steps.length}</span></div>
        <div class="seg-line"></div>
        ${this._tab("setup", "Overview")}
        ${steps.map((s) => {
          const mark = s.done ? '<span class="mark done">✓</span>' : s.skipped ? '<span class="mark skip">–</span>' : '<span class="mark open">›</span>';
          return this._tab(s.target, s.title, mark, `step-tab ${s.done || s.skipped ? "soon" : ""}`);
        }).join("")}
      </div>`;

    let autoTabs = '<div class="empty">scanning…</div>';
    let manualTabs = "";
    if (w) {
      const auto = w.auto.flatMap((srv) => srv.workers.map((a) => ({ ...a, online: srv.online, srv })));
      autoTabs = auto.length
        ? auto.map((a) => this._tab(`worker/${a.id}`, a.model, `<span class="dot ${a.online ? "ok" : "bad"}"></span>`)).join("")
        : '<div class="empty">nothing found</div>';
      manualTabs = w.manual.length
        ? w.manual.map((m) => this._tab(`worker/${m.id}`, m.name, `<span class="dot ${m.online ? "ok" : "bad"}"></span>`)).join("")
        : '<div class="empty">none yet</div>';
    }
    const pipeOpen = this._stepOpen("pipeline");

    return `<nav>
      <div class="brand">
        <span class="name">AAC</span><span class="ver">v${esc(this._state?.version || "")}</span><span class="sp"></span>
        <button class="icon-btn ${this._showAll ? "on" : ""}" id="eye" title="${this._showAll ? "Hide finished segments" : "Show all segments"}">${icon(this._showAll ? "eye" : "eyeOff")}</button>
      </div>
      <div class="scroll">
        ${setup}
        <div class="seg">
          <div class="seg-title">${icon("chat")}CHAT</div><div class="seg-line"></div>
          ${this._tab("chat", "Master")}
        </div>
        <div class="seg">
          <div class="seg-title">${icon("workers")}WORKERS<button class="icon-btn" id="refresh-workers" title="Scan again">${icon("refresh")}</button></div>
          <div class="seg-line"></div>
          ${this._tab("workers", "Overview")}
          <div class="sub">AUTO</div>
          ${autoTabs}
          <div class="sub">MANUAL<button class="icon-btn" data-go="workers/new" title="New worker">${icon("plus")}</button></div>
          ${manualTabs}
        </div>
        <div class="seg">
          <div class="seg-title">${icon("settings")}SETTINGS</div><div class="seg-line"></div>
          ${this._tab("settings/master", "Master agent", this._stepOpen("master") ? '<span class="dot open"></span>' : "")}
          ${this._tab("settings/pipelines", "Pipelines", pipeOpen ? '<span class="dot open"></span>' : "")}
          ${this._tab("settings/mcp", "MCP", "", "soon")}
          ${this._tab("settings/memory", "Memory", "", "soon")}
        </div>
      </div>
    </nav>`;
  }

  _header(title, meta = "") {
    return `<header>
      <button class="icon-btn menu-btn" id="nav-toggle" title="Menu">${icon("menu")}</button>
      ${this._narrow ? "<ha-menu-button></ha-menu-button>" : ""}
      <h1>${esc(title)}</h1><span class="meta">${meta}</span></header>`;
  }

  _renderMain() {
    if (this._state?.error) {
      return `${this._header("AAC")}<div class="content"><div class="card"><h2>NOT AVAILABLE</h2><p>${esc(this._state.error)}</p></div></div>`;
    }
    const v = this._view;
    if (v === "chat") return this._renderChat();
    if (v === "setup") return this._renderSetup();
    if (v === "workers") return this._renderWorkers();
    if (v === "workers/new" || v.startsWith("worker/edit/")) return this._renderWorkerForm();
    if (v.startsWith("worker/")) return this._renderWorker(v.slice(7));
    if (v === "settings/master") return this._renderMaster();
    if (v === "settings/pipelines") return this._renderPipelines();
    if (v === "settings/mcp") return this._renderSoon("MCP", "AAC as an MCP server (memory, modules and HA tools for other clients) and as an MCP client (plug in outside tools). Planned.");
    if (v === "settings/memory") return this._renderSoon("MEMORY", "Facts, timeline, vectors and people in one local SQLite store, with a browser to see, edit and delete everything. Planned.");
    return this._renderChat();
  }

  _renderSoon(title, text) {
    return `${this._header(title)}<div class="content"><div class="card"><h2>${esc(title)} · PLANNED</h2><p>${esc(text)}</p></div></div>`;
  }

  // ---------------------------------------------------------------- views
  _renderChat() {
    const st = this._state;
    const master = st?.master?.worker;
    const msgs = this._chat.messages;
    const log = msgs.length
      ? msgs.map((m) => `<div class="bubble ${m.role} ${m.error ? "err" : ""}"><div class="who">${m.role === "user" ? "YOU" : "AAC"}</div>${esc(m.text)}</div>`).join("")
      : `<div class="welcome"><div class="big">AAC</div>${master ? "Ask me anything." : "No master model yet. Open SETUP to choose one."}</div>`;
    const busy = this._chat.busy ? '<div class="bubble aac"><div class="who">AAC</div><span class="cursor"></span></div>' : "";
    return `${this._header("CHAT · MASTER", `model: ${esc(this._workerLabel(master))}`)}
      <div class="chat">
        <div class="log">${log}${busy}</div>
        <div class="composer">
          <textarea id="chat-input" placeholder="Message AAC…  (Enter to send, Shift+Enter for a new line)" ${this._chat.busy ? "disabled" : ""}></textarea>
          <button class="btn primary" id="chat-send" ${this._chat.busy ? "disabled" : ""}>${icon("send")}</button>
        </div>
      </div>`;
  }

  _renderSetup() {
    const steps = this._steps();
    const open = this._openSteps().length;
    return `${this._header("SETUP", open ? `${open} open` : "all done")}
      <div class="content"><div class="card"><h2>WORKFLOW</h2>
        <p>${open ? "Work through the open steps. Each one opens the place where it is configured." : "Everything is set up. This segment hides itself now; the eye at the top shows it again."}</p>
        ${steps.map((s) => `
          <div class="step" data-go="${esc(s.target)}">
            <span class="mark ${s.done ? "done" : s.skipped ? "skip" : "open"}">${s.done ? "✓" : s.skipped ? "–" : "›"}</span>
            <div style="flex:1"><div class="t">${esc(s.title)}${s.optional ? ' <span class="tag">OPTIONAL</span>' : ""}</div><div class="h">${esc(s.hint)}</div></div>
            ${s.optional && !s.done ? `<button class="btn" data-skip="${esc(s.id)}" data-skipped="${s.skipped ? "0" : "1"}">${s.skipped ? "Undo skip" : "Skip"}</button>` : ""}
          </div>`).join("")}
      </div></div>`;
  }

  _renderWorkers() {
    const w = this._workers;
    if (!w) return `${this._header("WORKERS")}<div class="content"><div class="card"><h2>SCANNING</h2><p>Looking for model servers…<span class="cursor"></span></p></div></div>`;
    const master = this._state?.master?.worker;
    const auto = w.auto.map((srv) => `
      <div class="card"><h2><span class="dot ${srv.online ? "ok" : "bad"}" style="display:inline-block;margin-right:8px"></span>${esc(srv.label)}</h2>
        <p>${esc(srv.base_url)}${srv.error ? ` · <span class="msg err">${esc(srv.error)}</span>` : ""}</p>
        ${srv.workers.length ? srv.workers.map((a) => `
          <div class="wrow">
            <span>${esc(a.model)}${a.id === master ? '<span class="tag aac">MASTER</span>' : ""}</span>
            <span class="actions">
              <button class="btn" data-go="worker/${esc(a.id)}">Details</button>
              ${a.id === master ? "" : `<button class="btn primary" data-master="${esc(a.id)}">Use as master</button>`}
            </span>
          </div>`).join("") : '<p class="hint">No models offered.</p>'}
      </div>`).join("");
    return `${this._header("WORKERS", "models AAC can use")}
      <div class="content">
        <div class="card"><h2>AUTO</h2><p>Found automatically: model servers already set up in Home Assistant (llama.cpp, Ollama) and the servers of your manual workers. Every model they offer can be used.</p>
          ${w.auto.length ? "" : '<p class="hint">Nothing found. Add a manual worker below.</p>'}</div>
        ${auto}
        <div class="card"><h2>MANUAL</h2><p>Workers you define yourself: any OpenAI-compatible server (llama.cpp, Ollama, vLLM, LM Studio, cloud APIs).</p>
          ${w.manual.map((m) => `
            <div class="wrow">
              <span><span class="dot ${m.online ? "ok" : "bad"}" style="display:inline-block;margin-right:8px"></span>${esc(m.name)} <span class="hint">· ${esc(m.model)}</span>${m.id === master ? '<span class="tag aac">MASTER</span>' : ""}</span>
              <span class="actions"><button class="btn" data-go="worker/${esc(m.id)}">Details</button></span>
            </div>`).join("")}
          <div class="actions"><button class="btn primary" data-go="workers/new">${icon("plus")} New worker</button></div>
        </div>
      </div>`;
  }

  _renderWorker(id) {
    const w = this._workers;
    const master = this._state?.master?.worker;
    const manual = w?.manual.find((m) => m.id === id);
    let srv = null, auto = null;
    for (const s of w?.auto || []) { const a = s.workers.find((x) => x.id === id); if (a) { srv = s; auto = a; } }
    if (!manual && !auto) return `${this._header("WORKER")}<div class="content"><div class="card"><h2>NOT FOUND</h2><p>${w ? "This worker is gone (server offline or removed)." : "Scanning…"}</p></div></div>`;
    const online = manual ? manual.online : srv.online;
    const rows = manual
      ? [["Type", "manual"], ["Name", manual.name], ["Model", manual.model], ["Server", manual.base_url], ["API key", manual.has_key ? "set" : "none"]]
      : [["Type", "auto"], ["Model", auto.model], ["Found via", srv.label], ["Server", srv.base_url]];
    rows.push(["State", online ? "online" : "offline"]);
    const err = manual ? manual.error : srv.error;
    return `${this._header(manual ? manual.name : auto.model, id === master ? '<span class="tag aac">MASTER</span>' : "")}
      <div class="content"><div class="card"><h2>WORKER</h2>
        <div class="kv">${rows.map(([k, v]) => `<span class="k">${esc(k)}</span><span class="v">${esc(v)}</span>`).join("")}</div>
        ${err ? `<div class="msg err">${esc(err)}</div>` : ""}
        <div class="actions">
          ${id === master ? "" : `<button class="btn primary" data-master="${esc(id)}">Use as master</button>`}
          ${manual ? `<button class="btn" data-go="worker/edit/${esc(id)}">Edit</button><button class="btn danger" data-delete="${esc(id)}">Delete</button>` : ""}
        </div>
        ${this._flashHtml()}
      </div></div>`;
  }

  _renderWorkerForm() {
    const f = this._form;
    if (!f) return this._renderWorkers();
    const models = f.models.length
      ? `<select id="f-model">${f.models.map((m) => `<option ${m === f.model ? "selected" : ""}>${esc(m)}</option>`).join("")}</select>`
      : `<input id="f-model" value="${esc(f.model)}" placeholder="load the list with Test, or type a model id">`;
    return `${this._header(f.id ? "EDIT WORKER" : "NEW WORKER")}
      <div class="content"><div class="card"><h2>${f.id ? "EDIT" : "NEW"} MANUAL WORKER</h2>
        <p>Any OpenAI-compatible server. The base URL includes <code>/v1</code>, e.g. <code>http://192.168.1.20:8080/v1</code>.</p>
        <label>Name</label><input id="f-name" value="${esc(f.name)}" placeholder="e.g. Server GPU">
        <label>Base URL</label><input id="f-url" value="${esc(f.base_url)}" placeholder="http://host:8080/v1">
        <label>API key</label><input id="f-key" type="password" value="${esc(f.api_key)}" placeholder="${f.has_key ? "unchanged (leave empty to keep)" : "optional"}">
        <div class="actions"><button class="btn" id="f-test">Test &amp; load models</button></div>
        <label>Model</label>${models}
        ${f.msg ? `<div class="msg ${f.msg.ok ? "ok" : "err"}">${esc(f.msg.text)}</div>` : ""}
        <div class="actions"><button class="btn primary" id="f-save">Save</button><button class="btn" data-go="${f.id ? `worker/${esc(f.id)}` : "workers"}">Cancel</button></div>
      </div></div>`;
  }

  _renderMaster() {
    const st = this._state;
    if (!st) return `${this._header("MASTER AGENT")}<div class="content"></div>`;
    const m = st.master;
    const workers = this._allWorkers();
    const known = workers.some((w) => w.id === m.worker);
    const opts = [`<option value="" ${m.worker ? "" : "selected"}>— choose a model —</option>`]
      .concat(!known && m.worker ? [`<option value="${esc(m.worker)}" selected>${esc(this._workerLabel(m.worker))} (offline)</option>`] : [])
      .concat(workers.map((w) => `<option value="${esc(w.id)}" ${w.id === m.worker ? "selected" : ""}>${esc(w.label)} · ${esc(w.detail)}${w.online ? "" : " (offline)"}</option>`));
    return `${this._header("MASTER AGENT", st.agent_entity_id ? esc(st.agent_entity_id) : "")}
      <div class="content"><div class="card"><h2>MASTER AGENT</h2>
        <p>The persistent agent that answers, decides and acts. It is also the conversation agent HA's voice pipelines can use.</p>
        <label>Model (worker)</label><select id="m-worker">${opts.join("")}</select>
        ${this._workers ? "" : '<div class="hint">scanning workers…</div>'}
        <label>Instructions</label><textarea id="m-prompt">${esc(m.prompt)}</textarea>
        <label>Control Home Assistant</label>
        ${st.llm_apis.map((a) => `<label class="check"><input type="checkbox" class="m-api" value="${esc(a.id)}" ${(m.llm_hass_api || []).includes(a.id) ? "checked" : ""}>${esc(a.name)}</label>`).join("")}
        <div class="row">
          <div><label>Temperature</label><input id="m-temp" type="number" min="0" max="2" step="0.05" value="${esc(m.temperature)}"></div>
          <div><label>Max tokens</label><input id="m-max" type="number" min="16" max="131072" step="1" value="${esc(m.max_tokens)}"></div>
        </div>
        <label class="check"><input type="checkbox" id="m-think" ${m.thinking ? "checked" : ""}>Let the model think (slower, llama.cpp / vLLM)</label>
        <div class="actions"><button class="btn primary" id="m-save">Save</button></div>
        ${this._flashHtml()}
      </div></div>`;
  }

  _renderPipelines() {
    const pl = this._pipelines;
    const agent = this._state?.agent_entity_id;
    const body = !pl ? "<p>Loading…</p>" : !pl.length ? '<div class="card"><h2>NO PIPELINES</h2><p>Home Assistant has no voice pipelines (Assist) yet.</p></div>' :
      pl.map((p) => `
        <div class="card"><h2>${esc(p.name)}${p.preferred ? '<span class="tag pref">PREFERRED</span>' : ""}${p.routed ? '<span class="tag aac">AAC</span>' : ""}</h2>
          <div class="flow">${p.stages.map((s, i) => `${i ? '<span class="link"></span>' : ""}
            <div class="node ${s.engine && s.engine === agent ? "aac" : s.state}" title="${esc(s.engine || "not used")}"><div class="k">${esc(s.kind)}</div><div class="v">${esc(s.label)}</div></div>`).join("")}
          </div>
          <div class="actions">
            ${p.routed ? `<button class="btn" data-route="${esc(p.id)}" data-enable="0">Restore previous agent</button>` : `<button class="btn primary" data-route="${esc(p.id)}" data-enable="1">Route through AAC</button>`}
          </div>
        </div>`).join("");
    return `${this._header("PIPELINES", "voice pipelines in Home Assistant")}
      <div class="content">
        <div class="card"><h2>VOICE PIPELINES</h2><p>Each Assist pipeline as a chain: wake word → speech to text → agent → text to speech. Green = working, red = missing/unavailable, dashed = not used. Routing a pipeline through AAC makes the master agent answer it.</p>${this._flashHtml()}</div>
        ${body}
      </div>`;
  }

  _flashHtml() {
    return this._flash ? `<div class="msg ${this._flash.ok ? "ok" : "err"}">${esc(this._flash.text)}</div>` : "";
  }

  // ---------------------------------------------------------------- events
  _bind() {
    const sr = this.shadowRoot;
    sr.querySelectorAll("[data-go]").forEach((el) => el.addEventListener("click", (e) => { e.stopPropagation(); this._go(el.dataset.go); }));
    sr.querySelector("#eye")?.addEventListener("click", () => {
      this._showAll = !this._showAll;
      try { localStorage.setItem("aac.showAll", this._showAll ? "1" : "0"); } catch (e) { /* ignore */ }
      this._render();
    });
    sr.querySelector("#nav-toggle")?.addEventListener("click", () => { this._navOpen = !this._navOpen; this._render(); });
    sr.querySelector("#refresh-workers")?.addEventListener("click", () => this._loadWorkers());

    sr.querySelectorAll("[data-skip]").forEach((el) => el.addEventListener("click", async (e) => {
      e.stopPropagation();
      this._state = await this._ws("aac/setup/skip", { step: el.dataset.skip, skipped: el.dataset.skipped === "1" });
      this._render();
    }));
    sr.querySelectorAll("[data-master]").forEach((el) => el.addEventListener("click", async (e) => {
      e.stopPropagation();
      this._state = await this._ws("aac/master/update", { worker: el.dataset.master });
      this._flash = { ok: true, text: "Master model set." };
      this._render();
    }));
    sr.querySelectorAll("[data-delete]").forEach((el) => el.addEventListener("click", async () => {
      if (!confirm("Delete this worker?")) return;
      await this._ws("aac/worker/delete", { worker_id: el.dataset.delete });
      this._view = "workers";
      await Promise.all([this._loadState(), this._loadWorkers()]);
    }));
    sr.querySelectorAll("[data-route]").forEach((el) => el.addEventListener("click", async () => {
      try {
        this._pipelines = await this._ws("aac/pipeline/route", { pipeline_id: el.dataset.route, enable: el.dataset.enable === "1" });
        this._flash = { ok: true, text: el.dataset.enable === "1" ? "Pipeline now answers through AAC." : "Previous agent restored." };
      } catch (err) { this._flash = { ok: false, text: err.message || String(err) }; }
      await this._loadState();
    }));

    // chat
    const input = sr.querySelector("#chat-input");
    input?.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this._send(); } });
    sr.querySelector("#chat-send")?.addEventListener("click", () => this._send());

    // worker form
    sr.querySelector("#f-test")?.addEventListener("click", () => this._formTest());
    sr.querySelector("#f-save")?.addEventListener("click", () => this._formSave());

    // master
    sr.querySelector("#m-save")?.addEventListener("click", () => this._saveMaster());
  }

  async _send() {
    const input = this.shadowRoot.querySelector("#chat-input");
    const text = input?.value.trim();
    if (!text || this._chat.busy) return;
    input.value = "";
    this._chat.messages.push({ role: "user", text });
    this._chat.busy = true;
    this._render();
    try {
      const r = await this._ws("aac/chat", { text, conversation_id: this._chat.conversationId });
      this._chat.conversationId = r.conversation_id;
      this._chat.messages.push({ role: "aac", text: r.speech || "(no answer)", error: r.error });
    } catch (e) {
      this._chat.messages.push({ role: "aac", text: e.message || String(e), error: true });
    }
    this._chat.busy = false;
    this._render();
    this.shadowRoot.querySelector("#chat-input")?.focus();
  }

  _readForm() {
    const sr = this.shadowRoot, f = this._form;
    f.name = sr.querySelector("#f-name").value.trim();
    f.base_url = sr.querySelector("#f-url").value.trim();
    f.api_key = sr.querySelector("#f-key").value;
    f.model = sr.querySelector("#f-model").value.trim();
  }

  async _formTest() {
    this._readForm();
    const f = this._form;
    if (!f.base_url) { f.msg = { ok: false, text: "Enter a base URL first." }; return this._render(); }
    f.msg = { ok: true, text: "Connecting…" };
    this._render();
    try {
      const data = { base_url: f.base_url };
      if (f.api_key) data.api_key = f.api_key;
      if (f.id) data.worker_id = f.id;
      const r = await this._ws("aac/server/models", data);
      f.models = r.models;
      if (!f.models.includes(f.model)) f.model = f.models[0] || "";
      if (!f.name) f.name = f.model;
      f.msg = { ok: true, text: `Online · ${r.models.length} model(s)` };
    } catch (e) {
      f.msg = { ok: false, text: e.message || String(e) };
    }
    this._render();
  }

  async _formSave() {
    this._readForm();
    const f = this._form;
    if (!f.name || !f.base_url || !f.model) { f.msg = { ok: false, text: "Name, base URL and model are needed." }; return this._render(); }
    const data = { name: f.name, base_url: f.base_url, model: f.model };
    if (f.id) data.worker_id = f.id;
    if (f.api_key || !f.id) data.api_key = f.api_key || null;
    try {
      const r = await this._ws("aac/worker/save", data);
      this._view = `worker/${r.id}`;
      await Promise.all([this._loadState(), this._loadWorkers()]);
    } catch (e) {
      f.msg = { ok: false, text: e.message || String(e) };
      this._render();
    }
  }

  async _saveMaster() {
    const sr = this.shadowRoot;
    try {
      this._state = await this._ws("aac/master/update", {
        worker: sr.querySelector("#m-worker").value || null,
        prompt: sr.querySelector("#m-prompt").value,
        llm_hass_api: [...sr.querySelectorAll(".m-api:checked")].map((el) => el.value),
        temperature: parseFloat(sr.querySelector("#m-temp").value),
        max_tokens: parseInt(sr.querySelector("#m-max").value, 10),
        thinking: sr.querySelector("#m-think").checked,
      });
      this._flash = { ok: true, text: "Saved." };
    } catch (e) {
      this._flash = { ok: false, text: e.message || String(e) };
    }
    this._render();
  }
}

customElements.define("aac-panel", AacPanel);

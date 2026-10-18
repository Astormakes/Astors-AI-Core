// AAC – Astor's AI Core: the panel.
// Plain web component, no build step, no external resources (works offline).
// Talks to the backend via the aac/* WebSocket commands.
// Sizes are in em on top of a fluid base font, so it scales from phone to TV.

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
  mic: '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 17v5M8 22h8"/>',
};
const icon = (name, cls = "") =>
  `<svg class="ico ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;

const STYLE = `
:host {
  --bg: #07060d; --bg2: #0d0b18;
  --cyan: #00f0ff; --magenta: #ff2bd6; --green: #39ff88; --amber: #ffb000; --red: #ff3860;
  --text: #cfeff5; --dim: #7893a2; --line: rgba(0, 240, 255, .45);
  --glow: 0 0 .3em rgba(0, 240, 255, .55), 0 0 .9em rgba(0, 240, 255, .25);
  --font: "JetBrains Mono", "Fira Code", "Cascadia Code", "Source Code Pro", ui-monospace, Menlo, Consolas, monospace;
  --nav-w: max(260px, 19em);
  color-scheme: dark;
  display: block; height: 100vh; height: 100dvh; background: var(--bg); color: var(--text); font-family: var(--font);
}
* { box-sizing: border-box; }
/* Fluid base size: 13px on phones, ~15.5px at 1080p, ~18.5px at 1440p, ~24px on an unscaled 4K TV */
.root { position: relative; display: flex; height: 100%; overflow: hidden; font-size: clamp(13px, calc(.45vw + 7px), 30px); }
.root::after { /* scanlines */
  content: ""; position: absolute; inset: 0; pointer-events: none; z-index: 50;
  background: repeating-linear-gradient(0deg, rgba(255,255,255,.025) 0 1px, transparent 1px 3px);
}
.ico { width: 1.3em; height: 1.3em; flex: none; filter: drop-shadow(0 0 .2em currentColor); }
[tabindex]:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible {
  outline: 1px solid var(--magenta); outline-offset: 2px; box-shadow: 0 0 .7em rgba(255, 43, 214, .6);
}
.thin-scroll { scrollbar-width: thin; scrollbar-color: var(--cyan) transparent; }
.thin-scroll::-webkit-scrollbar { width: .45em; height: .45em; }
.thin-scroll::-webkit-scrollbar-track { background: rgba(0, 240, 255, .05); }
.thin-scroll::-webkit-scrollbar-thumb { background: var(--cyan); box-shadow: 0 0 .5em var(--cyan); border-radius: .25em; }

/* ---------- sidebar ---------- */
nav {
  width: var(--nav-w); flex: none; display: flex; flex-direction: column; background: var(--bg2);
  border-right: 1px solid var(--line); box-shadow: 1px 0 1em rgba(0, 240, 255, .18);
}
.brand { display: flex; align-items: center; gap: .7em; padding: 1em 1em .85em; border-bottom: 1px solid var(--line); }
.brand .name { font-size: 1.45em; font-weight: 700; letter-spacing: .28em; color: var(--magenta);
  text-shadow: 0 0 .2em var(--magenta), 0 0 .7em rgba(255, 43, 214, .6); }
.brand .ver { font-size: .8em; color: var(--dim); }
.brand .sp { flex: 1; }
button.icon-btn { background: none; border: 1px solid transparent; color: var(--cyan); padding: .3em; cursor: pointer; display: grid; place-items: center; font-size: 1em; }
button.icon-btn.on { color: var(--magenta); }
.scroll { flex: 1; overflow-y: scroll; padding: .4em 0 1.5em; }
.seg { margin-top: .7em; }
.seg.dim-seg { opacity: .45; }
.seg-title { display: flex; align-items: center; gap: .7em; padding: .55em 1.15em; font-size: 1em; font-weight: 700; letter-spacing: .22em;
  color: var(--cyan); text-shadow: 0 0 .3em var(--cyan), 0 0 1em rgba(0, 240, 255, .55); }
.seg-title .badge { margin-left: auto; font-size: .8em; letter-spacing: .1em; color: var(--amber); text-shadow: 0 0 .4em var(--amber); }
.seg-title .icon-btn { margin-left: auto; }
.seg-line { height: 1px; margin: 0 1.15em .3em; background: linear-gradient(90deg, var(--cyan), transparent); box-shadow: 0 0 .4em var(--cyan); }
.sub { padding: .45em 1.15em .15em 3.15em; font-size: .72em; letter-spacing: .3em; color: var(--dim); display: flex; align-items: center; }
.sub .icon-btn { margin-left: auto; padding: .15em; font-size: 1.35em; }
.tab { display: flex; align-items: center; gap: .55em; padding: .45em 1.15em .45em 3.15em; font-size: .93em; cursor: pointer; color: var(--text);
  text-shadow: 0 0 .25em rgba(0, 240, 255, .35); border-left: 2px solid transparent; user-select: none; }
.tab.deep { padding-left: 4.4em; font-size: .86em; }
.tab.active { color: var(--magenta); border-left-color: var(--magenta); text-shadow: 0 0 .3em var(--magenta);
  background: linear-gradient(90deg, rgba(255, 43, 214, .14), transparent); }
.tab .grow { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tab.soon { color: var(--dim); text-shadow: none; }
.dot { width: .5em; height: .5em; border-radius: 50%; flex: none; background: var(--dim); display: inline-block; }
.dot.ok { background: var(--green); box-shadow: 0 0 .4em var(--green); }
.dot.bad { background: var(--red); box-shadow: 0 0 .4em var(--red); }
.dot.open { background: var(--amber); box-shadow: 0 0 .4em var(--amber); }
.mark { width: 1em; text-align: center; flex: none; }
.mark.done { color: var(--green); text-shadow: 0 0 .4em var(--green); }
.mark.open { color: var(--amber); text-shadow: 0 0 .4em var(--amber); }
.mark.skip { color: var(--dim); }
.empty { padding: .3em 1.15em .3em 3.15em; font-size: .85em; color: var(--dim); }

/* ---------- main ---------- */
main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
header { display: flex; align-items: center; gap: .7em; padding: 0 1.7em; border-bottom: 1px solid var(--line); min-height: 4em; flex: none; }
header h1 { margin: 0; font-size: 1.15em; letter-spacing: .22em; font-weight: 700; color: var(--cyan); text-shadow: var(--glow);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
header .meta { margin-left: auto; font-size: .85em; color: var(--dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.content { flex: 1; overflow-y: auto; padding: 1.7em; container-type: inline-size; }
.cards { display: grid; gap: 1.3em; grid-template-columns: repeat(auto-fit, minmax(min(100%, 30em), 1fr)); align-items: start; }
.card { position: relative; border: 1px solid var(--line); background: rgba(13, 11, 24, .85); padding: 1.3em;
  box-shadow: 0 0 .7em rgba(0, 240, 255, .12), inset 0 0 1.3em rgba(0, 240, 255, .05); min-width: 0; }
.stack > .card + .card, .stack > .card + .cards, .stack > .cards + .card, .stack > .cards + .cards { margin-top: 1.3em; }
.card::before, .card::after { content: ""; position: absolute; width: .7em; height: .7em; border: 1px solid var(--magenta); }
.card::before { top: -1px; left: -1px; border-right: 0; border-bottom: 0; }
.card::after { bottom: -1px; right: -1px; border-left: 0; border-top: 0; }
.card h2 { margin: 0 0 .85em; font-size: .93em; letter-spacing: .22em; color: var(--cyan); text-shadow: var(--glow); display: flex; align-items: center; gap: .55em; flex-wrap: wrap; }
.card p, .hint { color: var(--dim); font-size: .93em; line-height: 1.55; margin: 0 0 .85em; }
code { color: var(--cyan); }
label { display: block; font-size: .78em; letter-spacing: .15em; color: var(--dim); margin: 1em 0 .45em; text-transform: uppercase; }
input, select, textarea { width: 100%; background: var(--bg); color: var(--text); border: 1px solid rgba(0, 240, 255, .35);
  padding: .6em .7em; font: inherit; font-size: .93em; outline: none; border-radius: 0; }
input:focus, select:focus, textarea:focus { border-color: var(--cyan); box-shadow: var(--glow); }
textarea { min-height: 10em; resize: vertical; }
.row { display: flex; gap: .85em; flex-wrap: wrap; }
.row > * { flex: 1; min-width: 10em; }
.form-grid { display: grid; gap: 0 2em; grid-template-columns: 1fr; }
@container (min-width: 62em) {
  .form-grid { grid-template-columns: minmax(0, 1fr) minmax(0, 1.3fr); }
  .form-grid .span { grid-column: 1 / -1; }
  .form-grid textarea { min-height: 18em; }
}
.check { display: flex; align-items: center; gap: .55em; font-size: .93em; color: var(--text); text-transform: none; letter-spacing: 0; margin: .45em 0; }
.check input { width: 1.1em; height: 1.1em; accent-color: var(--magenta); }
.btn { background: transparent; color: var(--cyan); border: 1px solid var(--cyan); padding: .6em 1.15em; font: inherit; font-size: .85em;
  letter-spacing: .15em; text-transform: uppercase; cursor: pointer; text-shadow: 0 0 .3em var(--cyan); box-shadow: 0 0 .4em rgba(0, 240, 255, .25);
  display: inline-flex; align-items: center; gap: .5em; }
.btn.primary { color: var(--magenta); border-color: var(--magenta); text-shadow: 0 0 .3em var(--magenta); box-shadow: 0 0 .55em rgba(255, 43, 214, .35); }
.btn.danger { color: var(--red); border-color: var(--red); text-shadow: 0 0 .3em var(--red); box-shadow: none; }
.btn:disabled { opacity: .4; cursor: default; }
.actions { display: flex; gap: .7em; margin-top: 1.3em; flex-wrap: wrap; }
.msg { font-size: .85em; margin-top: .7em; }
.msg.err { color: var(--red); text-shadow: 0 0 .3em rgba(255, 56, 96, .6); }
.msg.ok { color: var(--green); text-shadow: 0 0 .3em rgba(57, 255, 136, .6); }
.step { display: flex; gap: 1em; align-items: flex-start; padding: .85em 0; border-bottom: 1px dashed rgba(0, 240, 255, .2); cursor: pointer; }
.step:last-child { border-bottom: 0; }
.step .t { font-size: 1em; }
.step .h { font-size: .85em; color: var(--dim); margin-top: .2em; }
.kv { display: grid; grid-template-columns: minmax(7em, max-content) 1fr; gap: .45em 1em; font-size: .93em; }
.kv .k { color: var(--dim); }
.kv .v { word-break: break-all; }
.wrow { display: flex; align-items: center; justify-content: space-between; gap: .85em; margin: .55em 0; flex-wrap: wrap; }
.wrow .actions { margin: 0; }
.tag { font-size: .7em; letter-spacing: .15em; padding: .15em .45em; border: 1px solid currentColor; color: var(--dim); }
.tag.aac { color: var(--magenta); }
.tag.plan { color: var(--amber); }

/* ---------- chat ---------- */
.chat { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.log { flex: 1; overflow-y: auto; padding: 1.7em; }
.log-inner { max-width: 72em; margin: 0 auto; display: flex; flex-direction: column; gap: 1em; min-height: 100%; }
.bubble { max-width: min(80%, 52em); padding: .7em 1em; font-size: 1em; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; }
.bubble.user { align-self: flex-end; border: 1px solid rgba(255, 43, 214, .6); box-shadow: 0 0 .55em rgba(255, 43, 214, .25); }
.bubble.aac { align-self: flex-start; border: 1px solid var(--line); box-shadow: 0 0 .55em rgba(0, 240, 255, .18); }
.bubble.err { border-color: var(--red); color: var(--red); }
.bubble .who { font-size: .7em; letter-spacing: .3em; color: var(--dim); margin-bottom: .3em; }
.cursor { display: inline-block; width: .55em; height: 1.05em; background: var(--cyan); box-shadow: 0 0 .4em var(--cyan); vertical-align: -.15em; animation: blink 1s steps(1) infinite; }
@keyframes blink { 50% { opacity: 0; } }
.composer { padding: 1em 1.7em 1.4em; border-top: 1px solid var(--line); flex: none; }
.composer-inner { max-width: 72em; margin: 0 auto; display: flex; gap: .7em; }
.composer textarea { min-height: 3.1em; height: 3.1em; max-height: 14em; }
.welcome { margin: auto; text-align: center; color: var(--dim); font-size: .93em; }
.welcome .big { font-size: 2.2em; letter-spacing: .3em; color: var(--magenta); text-shadow: 0 0 .25em var(--magenta), 0 0 .8em rgba(255, 43, 214, .5); margin-bottom: .4em; }

/* ---------- audio pipeline ---------- */
.split { display: grid; gap: 1.3em; grid-template-columns: 1fr; align-items: start; }
@container (min-width: 60em) { .split { grid-template-columns: minmax(0, 1fr) minmax(20em, 26em); } }
.flow { display: flex; flex-direction: column; }
.stage { display: grid; grid-template-columns: 2.4em 1fr; gap: 0 .9em; }
.stage .rail { display: flex; flex-direction: column; align-items: center; }
.stage .node { width: 1.2em; height: 1.2em; border: 1px solid var(--amber); transform: rotate(45deg); margin-top: .35em; box-shadow: 0 0 .5em rgba(255, 176, 0, .5); flex: none; }
.stage.ok .node { border-color: var(--green); box-shadow: 0 0 .5em rgba(57, 255, 136, .5); }
.stage .wire { flex: 1; width: 1px; background: var(--cyan); box-shadow: 0 0 .4em var(--cyan); min-height: 1.2em; margin: .3em 0; }
.stage:last-child .wire { display: none; }
.stage .body { padding-bottom: 1.1em; }
.stage .name { font-size: 1em; color: var(--text); display: flex; align-items: center; gap: .55em; flex-wrap: wrap; }
.stage .desc { font-size: .85em; color: var(--dim); margin-top: .25em; line-height: 1.5; }
.src { display: flex; gap: .7em; align-items: flex-start; padding: .6em 0; border-bottom: 1px dashed rgba(0, 240, 255, .2); }
.src:last-child { border-bottom: 0; }
.src .ico { margin-top: .1em; color: var(--cyan); }
.src .n { font-size: .93em; }
.src .m { font-size: .78em; color: var(--dim); margin-top: .15em; }

/* ---------- interaction ---------- */
@media (hover: hover) {
  button.icon-btn:hover { border-color: var(--line); box-shadow: var(--glow); }
  .tab:hover { background: rgba(0, 240, 255, .06); }
  .tab.active:hover { background: linear-gradient(90deg, rgba(255, 43, 214, .18), transparent); }
  .btn:hover:not(:disabled) { background: rgba(0, 240, 255, .1); box-shadow: var(--glow); }
  .btn.primary:hover:not(:disabled) { background: rgba(255, 43, 214, .12); }
  .step:hover .t { color: var(--magenta); }
}
@media (pointer: coarse) { /* fingers */
  .tab { padding-top: .8em; padding-bottom: .8em; }
  .btn, button.icon-btn { min-height: 2.9em; min-width: 2.9em; justify-content: center; }
  input, select { min-height: 2.9em; }
}

/* ---------- drawer (phones, tablets in portrait) ---------- */
button.menu-btn { display: none; }
.backdrop { display: none; }
@media (max-width: 900px) {
  nav { position: absolute; z-index: 40; top: 0; bottom: 0; left: 0; width: min(var(--nav-w), 85vw); transform: translateX(-100%); transition: transform .2s; }
  .root.nav-open nav { transform: none; box-shadow: 0 0 2em rgba(0, 0, 0, .8), 1px 0 1em rgba(0, 240, 255, .3); }
  .root.nav-open .backdrop { display: block; position: absolute; inset: 0; z-index: 30; background: rgba(0, 0, 0, .55); }
  button.menu-btn { display: grid; }
}
@media (max-width: 600px) {
  .content, .log { padding: 1em; }
  header { padding: 0 1em; }
  .composer { padding: .8em 1em 1em; }
  .bubble { max-width: 92%; }
}
@media (prefers-reduced-motion: reduce) { .cursor { animation: none; } nav { transition: none; } }
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
    this._audio = null;
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
  set narrow(v) {
    const changed = this._narrow !== v;
    this._narrow = v;
    if (changed && this._hass) this._render();
  }
  set panel(v) { this._panel = v; }

  _ws(type, data = {}) { return this._hass.callWS({ type, ...data }); }

  async _loadAll() {
    await Promise.all([this._loadState(), this._loadWorkers(), this._loadAudio()]);
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
  async _loadAudio() {
    try { this._audio = await this._ws("aac/audio"); } catch (e) { this._audio = { stages: [], sources: [], error: e.message }; }
    this._render();
  }

  _steps() { return this._state?.setup || []; }
  _openSteps() { return this._steps().filter((s) => !s.done && !s.skipped); }
  _setupOpen() { return this._openSteps().length > 0; }
  _stepOpen(id) { return this._openSteps().some((s) => s.id === id); }

  _allWorkers() {
    const w = this._workers || { manual: [], auto: [] };
    const list = w.manual.map((m) => ({ id: m.id, label: m.name, detail: m.model, online: m.online }));
    for (const srv of w.auto) {
      for (const a of srv.workers) list.push({ id: a.id, label: a.model, detail: srv.label, online: srv.online });
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
    if (view.startsWith("settings/audio")) this._loadAudio();
    this._render();
  }

  // ---------------------------------------------------------------- render
  _render() {
    if (!this._hass) return;
    const sr = this.shadowRoot;
    const chatInput = sr.querySelector("#chat-input");
    const draft = chatInput ? chatInput.value : "";
    const focusedId = sr.activeElement?.id || null;
    const focusedGo = sr.activeElement?.dataset?.go || null;
    const navScroll = sr.querySelector("nav .scroll")?.scrollTop || 0;
    // Keep what the user typed when the same view re-renders (e.g. a scan finished)
    const keep = {};
    const sameView = this._renderedView === this._view;
    if (sameView) {
      sr.querySelectorAll(".content input, .content textarea, .content select").forEach((el) => {
        const k = el.id || (el.classList.contains("m-api") ? `api:${el.value}` : null);
        if (k) keep[k] = el.type === "checkbox" ? el.checked : el.value;
      });
    }
    const contentScroll = sameView ? sr.querySelector(".content")?.scrollTop || 0 : 0;

    sr.innerHTML = `<style>${STYLE}</style>
      <div class="root ${this._navOpen ? "nav-open" : ""}">
        ${this._renderNav()}
        <div class="backdrop" id="backdrop"></div>
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
    if (input) input.value = draft;
    // Keep keyboard/remote focus where it was
    const refocus = (focusedId && sr.getElementById(focusedId)) ||
      (focusedGo && sr.querySelector(`nav [data-go="${CSS.escape(focusedGo)}"]`));
    if (refocus) refocus.focus({ preventScroll: true });
    const log = sr.querySelector(".log");
    if (log) log.scrollTop = log.scrollHeight;
    const mb = sr.querySelector("ha-menu-button");
    if (mb) { mb.hass = this._hass; mb.narrow = this._narrow; }
    this._bind();
  }

  _tab(view, label, extra = "", cls = "") {
    const active = this._view === view;
    return `<div class="tab ${cls} ${active ? "active" : ""}" data-go="${esc(view)}" tabindex="0" role="button" ${active ? 'aria-current="page"' : ""}>${extra}<span class="grow">${esc(label)}</span></div>`;
  }

  _renderNav() {
    const steps = this._steps();
    const open = this._openSteps();
    const showSetup = open.length > 0 || this._showAll;
    const doneCount = steps.filter((s) => s.done).length;
    const w = this._workers;

    const setup = !showSetup ? "" : `
      <div class="seg ${open.length ? "" : "dim-seg"}">
        <div class="seg-title">${icon("setup")}SETUP<span class="badge">${doneCount}/${steps.length}</span></div>
        <div class="seg-line"></div>
        ${this._tab("setup", "Overview")}
        ${steps.map((s) => {
          const mark = s.done ? '<span class="mark done">✓</span>' : s.skipped ? '<span class="mark skip">–</span>' : '<span class="mark open">›</span>';
          return this._tab(s.target, s.title, mark, s.done || s.skipped ? "soon" : "");
        }).join("")}
      </div>`;

    let autoTabs = '<div class="empty">scanning…</div>';
    let manualTabs = "";
    if (w) {
      const auto = w.auto.flatMap((srv) => srv.workers.map((a) => ({ ...a, online: srv.online })));
      autoTabs = auto.length
        ? auto.map((a) => this._tab(`worker/${a.id}`, a.model, `<span class="dot ${a.online ? "ok" : "bad"}"></span>`)).join("")
        : '<div class="empty">nothing found</div>';
      manualTabs = w.manual.length
        ? w.manual.map((m) => this._tab(`worker/${m.id}`, m.name, `<span class="dot ${m.online ? "ok" : "bad"}"></span>`)).join("")
        : '<div class="empty">none yet</div>';
    }

    return `<nav aria-label="AAC">
      <div class="brand">
        <span class="name">AAC</span><span class="ver">v${esc(this._state?.version || "")}</span><span class="sp"></span>
        <button class="icon-btn ${this._showAll ? "on" : ""}" id="eye" title="${this._showAll ? "Hide finished segments" : "Show all segments"}" aria-pressed="${this._showAll}">${icon(this._showAll ? "eye" : "eyeOff")}</button>
      </div>
      <div class="scroll thin-scroll">
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
          ${this._tab("settings/audio", "Audio pipeline")}
          ${this._tab("settings/audio/sources", "Sources", "", "deep")}
          ${this._tab("settings/mcp", "MCP", "", "soon")}
          ${this._tab("settings/memory", "Memory", "", "soon")}
        </div>
      </div>
    </nav>`;
  }

  _header(title, meta = "") {
    return `<header>
      <button class="icon-btn menu-btn" id="nav-toggle" title="AAC menu" aria-label="AAC menu">${icon("menu")}</button>
      ${this._narrow ? "<ha-menu-button></ha-menu-button>" : ""}
      <h1>${esc(title)}</h1><span class="meta">${meta}</span></header>`;
  }

  _renderMain() {
    if (this._state?.error) {
      return `${this._header("AAC")}<div class="content thin-scroll"><div class="card"><h2>NOT AVAILABLE</h2><p>${esc(this._state.error)}</p></div></div>`;
    }
    const v = this._view;
    if (v === "chat") return this._renderChat();
    if (v === "setup") return this._renderSetup();
    if (v === "workers") return this._renderWorkers();
    if (v === "workers/new" || v.startsWith("worker/edit/")) return this._renderWorkerForm();
    if (v.startsWith("worker/")) return this._renderWorker(v.slice(7));
    if (v === "settings/master") return this._renderMaster();
    if (v === "settings/audio") return this._renderAudio();
    if (v === "settings/audio/sources") return this._renderSources();
    if (v === "settings/mcp") return this._renderSoon("MCP",
      "AAC as an MCP server (memory, modules and tools for other clients) and as an MCP client (plug in outside tools). Home Assistant's own AI features (Assist API, HA's MCP server) can be plugged in here as tools; AAC never changes HA's own voice or AI setup.");
    if (v === "settings/memory") return this._renderSoon("MEMORY",
      "Facts, timeline, vectors and people in one local SQLite store, with a browser to see, edit and delete everything.");
    return this._renderChat();
  }

  _page(title, meta, body) {
    return `${this._header(title, meta)}<div class="content thin-scroll"><div class="stack">${body}</div></div>`;
  }

  _renderSoon(title, text) {
    return this._page(title, "", `<div class="card"><h2>${esc(title)} <span class="tag plan">PLANNED</span></h2><p>${esc(text)}</p></div>`);
  }

  // ---------------------------------------------------------------- views
  _renderChat() {
    const master = this._state?.master?.worker;
    const msgs = this._chat.messages;
    const log = msgs.length
      ? msgs.map((m) => `<div class="bubble ${m.role} ${m.error ? "err" : ""}"><div class="who">${m.role === "user" ? "YOU" : "AAC"}</div>${esc(m.text)}</div>`).join("")
      : `<div class="welcome"><div class="big">AAC</div>${master ? "Ask me anything." : "No master model yet. Open SETUP to choose one."}</div>`;
    const busy = this._chat.busy ? '<div class="bubble aac"><div class="who">AAC</div><span class="cursor"></span></div>' : "";
    return `${this._header("CHAT · MASTER", `model: ${esc(this._workerLabel(master))}`)}
      <div class="chat">
        <div class="log thin-scroll" aria-live="polite"><div class="log-inner">${log}${busy}</div></div>
        <div class="composer"><div class="composer-inner">
          <textarea id="chat-input" class="thin-scroll" aria-label="Message" placeholder="Message AAC…  (Enter to send, Shift+Enter for a new line)" ${this._chat.busy ? "disabled" : ""}></textarea>
          <button class="btn primary" id="chat-send" aria-label="Send" ${this._chat.busy ? "disabled" : ""}>${icon("send")}</button>
        </div></div>
      </div>`;
  }

  _renderSetup() {
    const steps = this._steps();
    const open = this._openSteps().length;
    return this._page("SETUP", open ? `${open} open` : "all done", `<div class="card"><h2>WORKFLOW</h2>
        <p>${open ? "Work through the open steps. Each one opens the place where it is configured." : "Everything is set up. This segment hides itself now; the eye at the top shows it again."}</p>
        ${steps.map((s) => `
          <div class="step" data-go="${esc(s.target)}" tabindex="0" role="button">
            <span class="mark ${s.done ? "done" : s.skipped ? "skip" : "open"}">${s.done ? "✓" : s.skipped ? "–" : "›"}</span>
            <div style="flex:1"><div class="t">${esc(s.title)}${s.optional ? ' <span class="tag">OPTIONAL</span>' : ""}</div><div class="h">${esc(s.hint)}</div></div>
            ${s.optional && !s.done ? `<button class="btn" data-skip="${esc(s.id)}" data-skipped="${s.skipped ? "0" : "1"}">${s.skipped ? "Undo skip" : "Skip"}</button>` : ""}
          </div>`).join("")}
      </div>`);
  }

  _renderWorkers() {
    const w = this._workers;
    if (!w) return this._page("WORKERS", "", '<div class="card"><h2>SCANNING</h2><p>Looking for model servers…<span class="cursor"></span></p></div>');
    const master = this._state?.master?.worker;
    const auto = w.auto.map((srv) => `
      <div class="card"><h2><span class="dot ${srv.online ? "ok" : "bad"}"></span>${esc(srv.label)}</h2>
        <p>${esc(srv.base_url)}${srv.error ? ` · <span class="msg err">${esc(srv.error)}</span>` : ""}</p>
        ${srv.workers.length ? srv.workers.map((a) => `
          <div class="wrow">
            <span>${esc(a.model)} ${a.id === master ? '<span class="tag aac">MASTER</span>' : ""}</span>
            <span class="actions">
              <button class="btn" data-go="worker/${esc(a.id)}">Details</button>
              ${a.id === master ? "" : `<button class="btn primary" data-master="${esc(a.id)}">Use as master</button>`}
            </span>
          </div>`).join("") : '<p class="hint">No extra models here (the ones in use are listed under MANUAL).</p>'}
      </div>`).join("");
    const manual = `<div class="card"><h2>MANUAL</h2><p>Workers you define yourself: any OpenAI-compatible server (llama.cpp, Ollama, vLLM, LM Studio, cloud APIs).</p>
        ${w.manual.map((m) => `
          <div class="wrow">
            <span><span class="dot ${m.online ? "ok" : "bad"}"></span> ${esc(m.name)} <span class="hint">· ${esc(m.model)}</span> ${m.id === master ? '<span class="tag aac">MASTER</span>' : ""}</span>
            <span class="actions"><button class="btn" data-go="worker/${esc(m.id)}">Details</button></span>
          </div>`).join("")}
        <div class="actions"><button class="btn primary" data-go="workers/new">${icon("plus")} New worker</button></div>
      </div>`;
    return this._page("WORKERS", "models AAC can use", `
      <div class="card"><h2>AUTO</h2><p>Found automatically: model servers already set up in Home Assistant (llama.cpp, Ollama; only read, never changed) and the servers of your manual workers. Every model they offer can be used.</p>
        ${w.auto.length ? "" : '<p class="hint">Nothing found. Add a manual worker.</p>'}</div>
      <div class="cards">${auto}${manual}</div>`);
  }

  _renderWorker(id) {
    const w = this._workers;
    const master = this._state?.master?.worker;
    const manual = w?.manual.find((m) => m.id === id);
    let srv = null, auto = null;
    for (const s of w?.auto || []) { const a = s.workers.find((x) => x.id === id); if (a) { srv = s; auto = a; } }
    if (!manual && !auto) return this._page("WORKER", "", `<div class="card"><h2>NOT FOUND</h2><p>${w ? "This worker is gone (server offline or removed)." : "Scanning…"}</p></div>`);
    const online = manual ? manual.online : srv.online;
    const rows = manual
      ? [["Type", "manual"], ["Name", manual.name], ["Model", manual.model], ["Server", manual.base_url], ["API key", manual.has_key ? "set" : "none"]]
      : [["Type", "auto"], ["Model", auto.model], ["Found via", srv.label], ["Server", srv.base_url]];
    rows.push(["State", online ? "online" : "offline"]);
    const err = manual ? manual.error : srv.error;
    return this._page(manual ? manual.name : auto.model, id === master ? '<span class="tag aac">MASTER</span>' : "", `<div class="card"><h2>WORKER</h2>
        <div class="kv">${rows.map(([k, v]) => `<span class="k">${esc(k)}</span><span class="v">${esc(v)}</span>`).join("")}</div>
        ${err ? `<div class="msg err">${esc(err)}</div>` : ""}
        <div class="actions">
          ${id === master ? "" : `<button class="btn primary" data-master="${esc(id)}">Use as master</button>`}
          ${manual ? `<button class="btn" data-go="worker/edit/${esc(id)}">Edit</button><button class="btn danger" data-delete="${esc(id)}">Delete</button>` : ""}
        </div>
        ${this._flashHtml()}
      </div>`);
  }

  _renderWorkerForm() {
    const f = this._form;
    if (!f) return this._renderWorkers();
    const models = f.models.length
      ? `<select id="f-model">${f.models.map((m) => `<option ${m === f.model ? "selected" : ""}>${esc(m)}</option>`).join("")}</select>`
      : `<input id="f-model" value="${esc(f.model)}" placeholder="load the list with Test, or type a model id">`;
    return this._page(f.id ? "EDIT WORKER" : "NEW WORKER", "", `<div class="card"><h2>${f.id ? "EDIT" : "NEW"} MANUAL WORKER</h2>
        <p>Any OpenAI-compatible server. The base URL includes <code>/v1</code>, e.g. <code>http://192.168.1.20:8080/v1</code>.</p>
        <div class="form-grid">
          <div><label for="f-name">Name</label><input id="f-name" value="${esc(f.name)}" placeholder="e.g. Server GPU"></div>
          <div><label for="f-url">Base URL</label><input id="f-url" value="${esc(f.base_url)}" placeholder="http://host:8080/v1" inputmode="url"></div>
          <div><label for="f-key">API key</label><input id="f-key" type="password" value="${esc(f.api_key)}" placeholder="${f.has_key ? "unchanged (leave empty to keep)" : "optional"}" autocomplete="off"></div>
          <div><label for="f-model">Model</label>${models}</div>
        </div>
        ${f.msg ? `<div class="msg ${f.msg.ok ? "ok" : "err"}">${esc(f.msg.text)}</div>` : ""}
        <div class="actions">
          <button class="btn" id="f-test">Test &amp; load models</button>
          <button class="btn primary" id="f-save">Save</button>
          <button class="btn" data-go="${f.id ? `worker/${esc(f.id)}` : "workers"}">Cancel</button>
        </div>
      </div>`);
  }

  _renderMaster() {
    const st = this._state;
    if (!st) return this._page("MASTER AGENT", "", "");
    const m = st.master;
    const workers = this._allWorkers();
    const known = workers.some((w) => w.id === m.worker);
    const opts = [`<option value="" ${m.worker ? "" : "selected"}>— choose a model —</option>`]
      .concat(!known && m.worker ? [`<option value="${esc(m.worker)}" selected>${esc(this._workerLabel(m.worker))} (offline)</option>`] : [])
      .concat(workers.map((w) => `<option value="${esc(w.id)}" ${w.id === m.worker ? "selected" : ""}>${esc(w.label)} · ${esc(w.detail)}${w.online ? "" : " (offline)"}</option>`));
    return this._page("MASTER AGENT", st.agent_entity_id ? esc(st.agent_entity_id) : "", `<div class="card"><h2>MASTER AGENT</h2>
        <p>The persistent agent that answers, decides and acts.</p>
        <div class="form-grid">
          <div>
            <label for="m-worker">Model (worker)</label><select id="m-worker">${opts.join("")}</select>
            ${this._workers ? "" : '<div class="hint">scanning workers…</div>'}
            <label>Tools from Home Assistant</label>
            ${st.llm_apis.map((a) => `<label class="check"><input type="checkbox" class="m-api" value="${esc(a.id)}" ${(m.llm_hass_api || []).includes(a.id) ? "checked" : ""}>${esc(a.name)}</label>`).join("")}
            <div class="row">
              <div><label for="m-temp">Temperature</label><input id="m-temp" type="number" min="0" max="2" step="0.05" value="${esc(m.temperature)}" inputmode="decimal"></div>
              <div><label for="m-max">Max tokens</label><input id="m-max" type="number" min="16" max="131072" step="1" value="${esc(m.max_tokens)}" inputmode="numeric"></div>
            </div>
            <label class="check"><input type="checkbox" id="m-think" ${m.thinking ? "checked" : ""}>Let the model think (slower, llama.cpp / vLLM)</label>
          </div>
          <div><label for="m-prompt">Instructions</label><textarea id="m-prompt" class="thin-scroll">${esc(m.prompt)}</textarea></div>
          <div class="span">
            <div class="actions"><button class="btn primary" id="m-save">Save</button></div>
            ${this._flashHtml()}
          </div>
        </div>
      </div>`);
  }

  _sourceList(sources) {
    if (!sources.length) return '<p class="hint">No audio sources found.</p>';
    return sources.map((s) => `
      <div class="src">${icon("mic")}
        <div style="flex:1;min-width:0">
          <div class="n">${esc(s.name)}</div>
          <div class="m">${esc(s.kind)}${s.area ? ` · ${esc(s.area)}` : ""}${s.integration ? ` · ${esc(s.integration)}` : ""}${s.available ? "" : " · unavailable"}</div>
        </div>
        <span class="tag">${s.connected ? "ON" : "NOT CONNECTED"}</span>
      </div>`).join("");
  }

  _renderAudio() {
    const a = this._audio;
    if (!a) return this._page("AUDIO PIPELINE", "", '<div class="card"><p>Loading…</p></div>');
    const flow = a.stages.map((s) => `
      <div class="stage ${s.state === "ok" ? "ok" : ""}">
        <div class="rail"><span class="node"></span><span class="wire"></span></div>
        <div class="body"><div class="name">${esc(s.name)} ${s.state === "ok" ? "" : '<span class="tag plan">PLANNED</span>'}</div><div class="desc">${esc(s.desc)}</div></div>
      </div>`).join("");
    return this._page("AUDIO PIPELINE", "AAC's own, separate from HA's Assist", `
      <div class="split">
        <div class="card"><h2>PIPELINE</h2>
          <p>AAC listens with its own pipeline, independent of Home Assistant's voice assistants (those stay as they are). It can use many audio sources at once and picks the best mic per sentence.${a.built ? "" : " Not built yet: this is the plan."}</p>
          <div class="flow">${flow}</div>
        </div>
        <div class="card"><h2>${icon("mic")} SOURCES</h2>
          <p>Mics AAC could listen to. Found in Home Assistant, only read.</p>
          ${this._sourceList(a.sources)}
          <div class="actions"><button class="btn" data-go="settings/audio/sources">All sources</button></div>
        </div>
      </div>`);
  }

  _renderSources() {
    const a = this._audio;
    if (!a) return this._page("AUDIO SOURCES", "", '<div class="card"><p>Loading…</p></div>');
    const groups = {};
    for (const s of a.sources) (groups[s.kind] ||= []).push(s);
    return this._page("AUDIO SOURCES", `${a.sources.length} found`, `
      <div class="card"><h2>SOURCES</h2>
        <p>Every source gets its own lightweight pipeline (ring buffer in RAM, voice activity), and the heavy parts (speech to text, speaker ID) are shared. Each source will have a listening mode: off, wake word, or continuous. Connecting sources comes with the audio hub.</p>
      </div>
      <div class="cards">${Object.entries(groups).map(([kind, list]) => `
        <div class="card"><h2>${esc(kind.toUpperCase())}</h2>${this._sourceList(list)}</div>`).join("")}</div>`);
  }

  _flashHtml() {
    return this._flash ? `<div class="msg ${this._flash.ok ? "ok" : "err"}">${esc(this._flash.text)}</div>` : "";
  }

  // ---------------------------------------------------------------- events
  _bind() {
    const sr = this.shadowRoot;
    sr.querySelectorAll("[data-go]").forEach((el) => {
      el.addEventListener("click", (e) => { e.stopPropagation(); this._go(el.dataset.go); });
      if (el.getAttribute("role") === "button") {
        el.addEventListener("keydown", (e) => {
          if (e.target !== el) return;
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); this._go(el.dataset.go); }
        });
      }
    });
    sr.querySelector("#eye")?.addEventListener("click", () => {
      this._showAll = !this._showAll;
      try { localStorage.setItem("aac.showAll", this._showAll ? "1" : "0"); } catch (e) { /* ignore */ }
      this._render();
    });
    sr.querySelector("#nav-toggle")?.addEventListener("click", () => { this._navOpen = !this._navOpen; this._render(); });
    sr.querySelector("#backdrop")?.addEventListener("click", () => { this._navOpen = false; this._render(); });
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

    // chat
    sr.querySelector("#chat-input")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this._send(); }
    });
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
    this._renderedView = null; // values come from the form state now
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

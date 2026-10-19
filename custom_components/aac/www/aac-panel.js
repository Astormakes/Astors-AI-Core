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
  panel: '<rect x="3" y="4" width="18" height="16"/><path d="M9 4v16M5.5 8h1.5M5.5 11h1.5M5.5 14h1.5"/>',
  send: '<path d="M3 11l18-8-8 18-2-8z"/>',
  bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
  chev: '<path d="M9 6l6 6-6 6"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
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
  display: block; height: 100vh; height: 100dvh;
  /* We handle the safe areas (notch, gesture bar) ourselves */
  padding: env(safe-area-inset-top, 0) env(safe-area-inset-right, 0) env(safe-area-inset-bottom, 0) env(safe-area-inset-left, 0);
  box-sizing: border-box; background: var(--bg); color: var(--text); font-family: var(--font);
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
header .meta + .icon-btn { margin-left: .3em; }
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
.tag.ok { color: var(--green); text-shadow: 0 0 .3em rgba(57, 255, 136, .6); }

/* ---------- chat ---------- */
.chat { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.log { flex: 1; overflow-y: auto; padding: 1.7em; }
.log-inner { max-width: 72em; margin: 0 auto; display: flex; flex-direction: column; gap: 1em; min-height: 100%; }
.bubble { max-width: min(80%, 52em); padding: .7em 1em; font-size: 1em; line-height: 1.55; overflow-wrap: anywhere; }
.bubble .txt { white-space: pre-wrap; }
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
.transcript { max-height: 22em; overflow-y: auto; display: flex; flex-direction: column; gap: .5em; }
.tline { border-left: 1px solid var(--line, rgba(0,229,255,.3)); padding: .1em 0 .1em .7em; }
.tline .m { font-size: .8em; opacity: .7; }
.tline.to-aac { border-left-color: var(--magenta, #ff2bd6); }
pre.code { white-space: pre-wrap; word-break: break-all; font-size: .85em; padding: .6em .8em; border: 1px solid rgba(0,229,255,.3); background: rgba(0,229,255,.05); }
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
  header { padding: 0 .6em; gap: .4em; }
  header h1 { font-size: 1em; letter-spacing: .15em; }
  header .meta { display: none; }
  header > .icon-btn:last-child:not(.menu-btn) { margin-left: auto; }
  .composer { padding: .8em 1em 1em; }
  .bubble { max-width: 92%; }
}
/* ---------- workers: chats + automatic runs ---------- */
.seg-title .acts { margin-left: auto; display: flex; gap: .2em; }
.fold { cursor: pointer; user-select: none; }
.fold .ico { width: 1em; height: 1em; transition: transform .15s; }
.fold.open .ico { transform: rotate(90deg); }
.count { color: var(--amber); text-shadow: 0 0 .4em var(--amber); margin-left: .3em; letter-spacing: .05em; }
.count.zero { color: var(--dim); text-shadow: none; }
.unread { margin-left: auto; min-width: 1.6em; text-align: center; font-size: .75em; padding: .05em .4em; border: 1px solid var(--magenta);
  color: var(--magenta); text-shadow: 0 0 .3em var(--magenta); }
.tab .time { margin-left: auto; font-size: .78em; color: var(--dim); text-shadow: none; flex: none; }
.dot.run { background: var(--amber); box-shadow: 0 0 .5em var(--amber); animation: pulse 1.2s ease-in-out infinite; }
.dot.silent { background: transparent; border: 1px solid var(--dim); }
@keyframes pulse { 50% { opacity: .25; } }
.steps { margin-top: .5em; font-size: .78em; color: var(--dim); }
.steps summary { cursor: pointer; letter-spacing: .1em; }
.steps .st { margin: .3em 0 0 .8em; overflow-wrap: anywhere; }
.steps .st b { color: var(--cyan); font-weight: normal; }
.steps .st.err b { color: var(--red); }
.bubble .trig { font-size: .7em; letter-spacing: .15em; color: var(--amber); margin-bottom: .3em; }
.timeline .st { border-left: 1px solid var(--line); padding: .35em 0 .35em 1em; margin-left: .3em; font-size: .88em; overflow-wrap: anywhere; }
.timeline .st b { color: var(--cyan); font-weight: normal; }
.timeline .st.err b { color: var(--red); }
.timeline .st.said b { color: var(--magenta); }
pre { white-space: pre-wrap; overflow-wrap: anywhere; margin: .3em 0 0; font: inherit; font-size: .85em; color: var(--dim); }
.status { font-size: .75em; letter-spacing: .15em; padding: .15em .5em; border: 1px solid currentColor; }
.status.running { color: var(--amber); } .status.done { color: var(--green); } .status.silent { color: var(--dim); } .status.failed { color: var(--red); }
.switch { display: flex; align-items: center; gap: .6em; }

@media (prefers-reduced-motion: reduce) { .cursor, .dot.run { animation: none; } nav { transition: none; } }
`;

const WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];
const STATUS_DOT = { running: "run", done: "ok", silent: "silent", failed: "bad" };
const fmtTime = (iso) => {
  if (!iso) return "";
  const d = new Date(iso), now = new Date();
  const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === now.toDateString() ? t : `${d.toLocaleDateString([], { day: "2-digit", month: "2-digit" })} ${t}`;
};

class AacPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._view = null;
    this._showAll = false;
    this._autoOpen = false;
    this._navOpen = false;
    this._state = null;
    this._models = null;
    this._audio = null;
    this._chats = [];
    this._chat = null; // open chat with messages
    this._busy = false;
    this._runs = []; // automatic runs (summaries)
    this._run = null; // open run with steps
    this._form = null;
    this._flash = null;
    this._loaded = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._loaded) {
      this._loaded = true;
      try {
        this._showAll = localStorage.getItem("aac.showAll") === "1";
        this._autoOpen = localStorage.getItem("aac.autoOpen") === "1";
      } catch (e) { /* ignore */ }
      this._render();
      this._loadAll();
      this._subscribe();
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

  disconnectedCallback() {
    if (this._unsub) { this._unsub.then((u) => u()).catch(() => {}); this._unsub = null; this._loaded = false; }
  }

  _ws(type, data = {}) { return this._hass.callWS({ type, ...data }); }

  _subscribe() {
    this._unsub = this._hass.connection.subscribeMessage((ev) => this._onUpdate(ev), { type: "aac/subscribe" });
    this._unsub.catch(() => {});
  }

  _onUpdate(ev) {
    if (ev.what === "run") {
      const run = ev.run;
      if (run.kind === "auto") {
        const i = this._runs.findIndex((r) => r.id === run.id);
        const summary = { ...run, steps: run.steps.length };
        if (i >= 0) this._runs[i] = summary; else this._runs.unshift(summary);
      }
      if (this._run && this._run.id === run.id) this._run = run;
      this._render();
    } else if (ev.what === "audio") {
      clearTimeout(this._audioTimer);
      this._audioTimer = setTimeout(async () => {
        await this._loadAudio();
        if (this._view.startsWith("settings/audio") && !this._typing()) this._render();
      }, 300);
    } else if (ev.what === "chat" || ev.what === "chats") {
      if (ev.chat_id === "aac") this._loadState(); // AAC may have planned something (Triggers)
      clearTimeout(this._chatTimer);
      this._chatTimer = setTimeout(async () => {
        await this._loadChats();
        if (this._chat?.id && (ev.what === "chats" || ev.chat_id === this._chat.id)) await this._openChat(this._chat.id, false);
      }, 150);
    }
  }

  async _loadAll() {
    await Promise.all([this._loadState(), this._loadModels(), this._loadAudio(), this._loadChats(), this._loadRuns()]);
    if (!this._view) {
      if (this._setupOpen()) this._view = "setup";
      else if (this._chats.length) await this._openChat(this._chats[0].id, false);
      else this._view = "chat/new";
    }
    this._render();
  }
  async _loadState() {
    try { this._state = await this._ws("aac/state"); } catch (e) { this._state = { error: e.message || String(e) }; }
    this._render();
  }
  async _loadModels() {
    this._models = null;
    this._render();
    try { this._models = await this._ws("aac/models"); } catch (e) { this._models = { manual: [], auto: [], error: e.message }; }
    this._render();
  }
  async _loadAudio() {
    try { this._audio = await this._ws("aac/audio"); } catch (e) { this._audio = { stages: [], sources: [], error: e.message }; }
    this._render();
  }
  async _loadChats() {
    try { this._chats = (await this._ws("aac/chats")).chats; } catch (e) { this._chats = []; }
    this._render();
  }
  async _loadRuns() {
    try { this._runs = await this._ws("aac/runs", { kind: "auto" }); } catch (e) { this._runs = []; }
    this._render();
  }
  async _loadMemory() {
    try { this._facts = await this._ws("aac/facts"); } catch (e) { this._facts = { keys: [] }; }
    try {
      this._memory = await this._ws("aac/memory/list", this._memQuery ? { query: this._memQuery } : { unconfirmed: !!this._memOpenOnly });
    } catch (e) { this._memory = { memories: [], stats: {}, error: e.message }; }
    this._render();
  }
  async _openChat(id, navigate = true) {
    try {
      this._chat = await this._ws("aac/chat/get", { chat_id: id });
      if (navigate || !this._view) this._view = `chat/${id}`;
    } catch (e) {
      this._chat = null;
      if (this._view === `chat/${id}`) this._view = "chat/new";
    }
    this._render();
  }
  async _openRun(id) {
    this._run = null;
    this._render();
    try { this._run = await this._ws("aac/run/get", { run_id: id }); } catch (e) { this._run = { error: e.message }; }
    this._render();
  }

  _steps() { return this._state?.setup || []; }
  _openSteps() { return this._steps().filter((s) => !s.done && !s.skipped); }
  _setupOpen() { return this._openSteps().length > 0; }
  _stepOpen(id) { return this._openSteps().some((s) => s.id === id); }

  _allModels() {
    const w = this._models || { manual: [], auto: [] };
    const list = w.manual.map((m) => ({ id: m.id, label: m.name, detail: m.model, online: m.online }));
    for (const srv of w.auto) {
      for (const a of srv.models) list.push({ id: a.id, label: a.model, detail: srv.label, online: srv.online });
    }
    return list;
  }
  _modelLabel(id) {
    if (!id) return "none";
    const w = this._allModels().find((x) => x.id === id);
    if (w) return w.label;
    return id.startsWith("auto|") ? id.split("|").slice(2).join("|") : id;
  }

  _go(view) {
    this._view = view;
    this._navOpen = false;
    this._flash = null;
    if (view === "models/new") this._form = { name: "", base_url: "", api_key: "", model: "", models: [], msg: null };
    if (view.startsWith("model/edit/")) {
      const m = (this._models?.manual || []).find((x) => x.id === view.slice(11));
      this._form = m ? { id: m.id, name: m.name, base_url: m.base_url, api_key: "", has_key: m.has_key, model: m.model, models: [m.model], msg: null } : null;
    }
    if (view === "trigger/new") this._form = { type: "state", name: "", entity_id: "", to: "", at: "07:30", weekdays: [], instruction: "", cooldown: 300, enabled: true, quiet: null, msg: null };
    if (view.startsWith("trigger/edit/")) {
      const t = (this._state?.triggers || []).find((x) => x.id === view.slice(13));
      this._form = t ? {
        id: t.id, name: t.name, enabled: t.enabled, cooldown: t.cooldown, quiet: t.quiet_hours,
        type: t.when.type, entity_id: t.when.entity_id || "", to: t.when.to || "", at: t.when.at || "07:30",
        weekdays: t.when.weekdays || [], instruction: t.do?.instruction || "", msg: null,
      } : null;
    }
    if (view.startsWith("settings/audio")) this._loadAudio();
    if (view === "settings/memory") this._loadMemory();
    if (view.startsWith("chat/") && view !== "chat/new") { this._openChat(view.slice(5)); return; }
    if (view === "chat/new") this._chat = null;
    if (view.startsWith("run/")) this._openRun(view.slice(4));
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
    const log = sr.querySelector(".log");
    const logAtBottom = !log || !sameView || log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    const logScroll = log?.scrollTop || 0;

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
    const refocus = (focusedId && sr.getElementById(focusedId)) ||
      (focusedGo && sr.querySelector(`nav [data-go="${CSS.escape(focusedGo)}"]`));
    if (refocus) refocus.focus({ preventScroll: true });
    const newLog = sr.querySelector(".log");
    if (newLog) newLog.scrollTop = logAtBottom ? newLog.scrollHeight : logScroll;
    const mb = sr.querySelector("ha-menu-button");
    if (mb) { mb.hass = this._hass; mb.narrow = this._narrow; }
    this._bind();
  }

  _tab(view, label, extra = "", cls = "", after = "") {
    const active = this._view === view;
    return `<div class="tab ${cls} ${active ? "active" : ""}" data-go="${esc(view)}" tabindex="0" role="button" ${active ? 'aria-current="page"' : ""}>${extra}<span class="grow">${esc(label)}</span>${after}</div>`;
  }

  _renderNav() {
    const steps = this._steps();
    const open = this._openSteps();
    const showSetup = open.length > 0 || this._showAll;
    const doneCount = steps.filter((s) => s.done).length;

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

    const chats = this._chats.length
      ? this._chats.map((c) => this._tab(`chat/${c.id}`, c.title, c.id === "aac" ? icon("bolt") : "",
          c.id === "aac" ? "" : "", c.unread ? `<span class="unread">${c.unread}</span>` : "")).join("")
      : '<div class="empty">no chats yet</div>';
    const running = this._runs.filter((r) => r.status === "running").length;
    const autoList = !this._autoOpen ? "" : (this._runs.length
      ? this._runs.slice(0, 25).map((r) => this._tab(`run/${r.id}`, r.title, `<span class="dot ${STATUS_DOT[r.status] || ""}"></span>`, "deep",
          `<span class="time">${esc(fmtTime(r.started))}</span>`)).join("")
      : '<div class="empty" style="padding-left:4.4em">nothing yet</div>');

    return `<nav aria-label="AAC">
      <div class="brand">
        <span class="name">AAC</span><span class="ver">v${esc(this._state?.version || "")}</span><span class="sp"></span>
        <button class="icon-btn ${this._showAll ? "on" : ""}" id="eye" title="${this._showAll ? "Hide finished segments" : "Show all segments"}" aria-pressed="${this._showAll}">${icon(this._showAll ? "eye" : "eyeOff")}</button>
      </div>
      <div class="scroll thin-scroll">
        ${setup}
        <div class="seg">
          <div class="seg-title">${icon("workers")}WORKERS<span class="acts"><button class="icon-btn" data-go="chat/new" title="New chat">${icon("plus")}</button></span></div>
          <div class="seg-line"></div>
          ${chats}
          <div class="tab fold ${this._autoOpen ? "open" : ""}" id="auto-fold" tabindex="0" role="button" aria-expanded="${this._autoOpen}">
            ${icon("chev")}<span class="grow">Auto<span class="count ${running ? "" : "zero"}">(${running})</span></span>
          </div>
          ${autoList}
        </div>
        <div class="seg">
          <div class="seg-title">${icon("settings")}SETTINGS</div><div class="seg-line"></div>
          ${this._tab("settings/charter", "Charter")}
          ${this._tab("settings/master", "Master agent", this._stepOpen("master") ? '<span class="dot open"></span>' : "")}
          ${this._tab("settings/triggers", "Triggers")}
          ${this._tab("models", "Models", this._stepOpen("models") ? '<span class="dot open"></span>' : "")}
          ${this._tab("settings/audio", "Audio pipeline")}
          ${this._tab("settings/audio/sources", "Sources", "", "deep")}
          ${this._tab("settings/mcp", "MCP", "", "soon")}
          ${this._tab("settings/memory", "Memory", this._stepOpen("memory") ? '<span class="dot open"></span>' : "")}
        </div>
      </div>
    </nav>`;
  }

  _header(title, meta = "", actions = "") {
    return `<header>
      ${this._narrow ? "<ha-menu-button></ha-menu-button>" : ""}
      <button class="icon-btn menu-btn" id="nav-toggle" title="AAC segments" aria-label="AAC segments">${icon("panel")}</button>
      <h1>${esc(title)}</h1><span class="meta">${meta}</span>${actions}</header>`;
  }

  _renderMain() {
    if (this._state?.error) {
      return `${this._header("AAC")}<div class="content thin-scroll"><div class="card"><h2>NOT AVAILABLE</h2><p>${esc(this._state.error)}</p></div></div>`;
    }
    const v = this._view || "";
    if (v.startsWith("chat/")) return this._renderChat();
    if (v.startsWith("run/")) return this._renderRun();
    if (v === "setup") return this._renderSetup();
    if (v === "models") return this._renderModels();
    if (v === "models/new" || v.startsWith("model/edit/")) return this._renderModelForm();
    if (v.startsWith("model/")) return this._renderModel(v.slice(6));
    if (v === "settings/master") return this._renderMaster();
    if (v === "settings/charter") return this._renderCharter();
    if (v === "settings/triggers") return this._renderTriggers();
    if (v === "trigger/new" || v.startsWith("trigger/edit/")) return this._renderTriggerForm();
    if (v === "settings/audio") return this._renderAudio();
    if (v === "settings/audio/sources") return this._renderSources();
    if (v === "settings/mcp") return this._renderSoon("MCP",
      "AAC as an MCP server (memory, modules and tools for other clients) and as an MCP client (plug in outside tools). Home Assistant's own AI features (Assist API, HA's MCP server) can be plugged in here as tools; AAC never changes HA's own voice or AI setup.");
    if (v === "settings/memory") return this._renderMemory();
    return this._page("AAC", "", '<div class="card"><p>Loading…<span class="cursor"></span></p></div>');
  }

  _page(title, meta, body, actions = "") {
    return `${this._header(title, meta, actions)}<div class="content thin-scroll"><div class="stack">${body}</div></div>`;
  }

  _renderSoon(title, text) {
    return this._page(title, "", `<div class="card"><h2>${esc(title)} <span class="tag plan">PLANNED</span></h2><p>${esc(text)}</p></div>`);
  }

  // ---------------------------------------------------------------- workers: chats + runs
  _stepsHtml(steps) {
    if (!steps || !steps.length) return "";
    const calls = steps.filter((s) => s.type === "tool_call").map((s) => s.tool);
    return `<details class="steps"><summary>⚙ ${esc(calls.join(", ") || "tools")}</summary>
      ${steps.map((s) => s.type === "text"
        ? `<div class="st">💭 ${esc(s.text)}</div>`
        : s.type === "tool_call"
        ? `<div class="st"><b>→ ${esc(s.tool)}</b> ${esc(JSON.stringify(s.args))}</div>`
        : `<div class="st ${s.error ? "err" : ""}"><b>← ${esc(s.tool)}</b> ${esc(s.data)}</div>`).join("")}
    </details>`;
  }

  _renderChat() {
    const master = this._state?.master?.model;
    const chat = this._view === "chat/new" ? null : this._chat;
    const msgs = chat?.messages || [];
    const isAac = chat?.id === "aac";
    const log = msgs.length
      ? msgs.map((m) => `<div class="bubble ${m.role} ${m.error ? "err" : ""}">
          ${m.trigger ? `<div class="trig">⚡ ${esc(m.trigger)}</div>` : `<div class="who">${m.role === "user" ? "YOU" : "AAC"}</div>`}
          <div class="txt">${esc(m.text)}</div>${this._stepsHtml(m.steps)}
          ${m.run_id && isAac ? `<div class="steps"><span tabindex="0" role="button" data-go="run/${esc(m.run_id)}" style="cursor:pointer">details ›</span></div>` : ""}
        </div>`).join("")
      : `<div class="welcome"><div class="big">AAC</div>${master ? "Ask me anything." : "No master model yet. Open SETUP to choose one."}</div>`;
    const busy = this._busy ? '<div class="bubble aac"><div class="who">AAC</div><span class="cursor"></span></div>' : "";
    const title = chat ? chat.title : "NEW CHAT";
    const del = chat ? `<button class="icon-btn" id="chat-delete" title="Delete chat">${icon("trash")}</button>` : "";
    return `${this._header(title, `model: ${esc(this._modelLabel(master))}`, del)}
      <div class="chat">
        <div class="log thin-scroll" aria-live="polite"><div class="log-inner">${log}${busy}</div></div>
        <div class="composer"><div class="composer-inner">
          <textarea id="chat-input" class="thin-scroll" aria-label="Message" placeholder="${isAac ? "Answer AAC…" : "Message AAC…"}" title="Enter to send, Shift+Enter for a new line" ${this._busy ? "disabled" : ""}></textarea>
          <button class="btn primary" id="chat-send" aria-label="Send" ${this._busy ? "disabled" : ""}>${icon("send")}</button>
        </div></div>
      </div>`;
  }

  _renderRun() {
    const r = this._run;
    if (!r) return this._page("RUN", "", '<div class="card"><p>Loading…<span class="cursor"></span></p></div>');
    if (r.error) return this._page("RUN", "", `<div class="card"><h2>NOT FOUND</h2><p>${esc(r.error)}</p></div>`);
    const rows = [["Trigger", r.trigger], ["Started", fmtTime(r.started)], ["Finished", r.finished ? fmtTime(r.finished) : "—"], ["Kind", r.kind === "auto" ? "automatic" : "chat"]];
    return this._page(r.title, `<span class="status ${esc(r.status)}">${esc(r.status.toUpperCase())}</span>`, `
      <div class="cards">
        <div class="card"><h2>${icon("bolt")} RUN</h2>
          <div class="kv">${rows.map(([k, v]) => `<span class="k">${esc(k)}</span><span class="v">${esc(v)}</span>`).join("")}</div>
          <label>Input</label><pre>${esc(r.input)}</pre>
          ${r.context ? `<details class="steps"><summary>CONTEXT GIVEN TO THE MODEL</summary><pre>${esc(r.context)}</pre></details>` : ""}
          <label>Result</label><pre>${esc(r.result || (r.status === "running" ? "working…" : "—"))}</pre>
          ${r.chat_id ? `<div class="actions"><button class="btn" data-go="chat/${esc(r.chat_id)}">Open chat</button></div>` : ""}
        </div>
        <div class="card"><h2>STEPS</h2>
          ${r.steps.length ? `<div class="timeline">${r.steps.map((s) => s.type === "text"
            ? `<div class="st said"><b>💭 model</b><pre>${esc(s.text)}</pre></div>`
            : s.type === "tool_call"
            ? `<div class="st"><b>→ ${esc(s.tool)}</b><pre>${esc(JSON.stringify(s.args, null, 1))}</pre></div>`
            : `<div class="st ${s.error ? "err" : ""}"><b>← ${esc(s.tool)}${s.error ? " (error)" : ""}</b><pre>${esc(s.data)}</pre></div>`).join("")}</div>`
            : '<p class="hint">No tool calls.</p>'}
        </div>
      </div>`);
  }

  // ---------------------------------------------------------------- setup
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
      </div>${open && steps.find((s) => s.id === "master")?.done ? `
      <div class="card"><h2>TOGETHER</h2>
        <p>The master model is wired in, so AAC can walk you through the rest in a chat: it checks what is open, explains each step and asks before it changes anything.</p>
        <div class="actions"><button class="btn primary" id="setup-chat">Set up with AAC</button></div>
      </div>` : ""}`);
  }

  // ---------------------------------------------------------------- settings: triggers
  _renderTriggers() {
    const st = this._state;
    if (!st) return this._page("TRIGGERS", "", "");
    const hb = st.heartbeat;
    const triggers = st.triggers || [];
    return this._page("TRIGGERS", "what wakes the master on its own", `
      <div class="cards">
        <div class="card"><h2>HEARTBEAT</h2>
          <p>Wakes the master every few minutes to look at the situation. It only speaks up when something matters (answers land in "From AAC"); otherwise it stays silent. Runs show up under WORKERS → Auto.</p>
          <label class="check"><input type="checkbox" id="hb-enabled" ${hb.enabled ? "checked" : ""}>Heartbeat on</label>
          <div class="row">
            <div><label for="hb-interval">Every (minutes)</label><input id="hb-interval" type="number" min="1" max="1440" value="${esc(hb.interval)}" inputmode="numeric"></div>
            <div><label for="hb-qs">Quiet from</label><input id="hb-qs" type="time" value="${esc(hb.quiet_start)}"></div>
            <div><label for="hb-qe">Quiet until</label><input id="hb-qe" type="time" value="${esc(hb.quiet_end)}"></div>
          </div>
          <label class="check"><input type="checkbox" id="hb-tools" ${hb.tools ? "checked" : ""}>Allow tools (slower; it already gets a snapshot of the home, and small models tend to tour every room with tools)</label>
          <label for="hb-instr">Instructions</label><textarea id="hb-instr" class="thin-scroll">${esc(hb.instructions)}</textarea>
          <div class="actions"><button class="btn primary" id="hb-save">Save</button><button class="btn" id="hb-run">Run once now</button></div>
          ${this._flashHtml()}
        </div>
        <div class="card"><h2>AAC'S OWN PLANS</h2>
          <p>AAC may set itself reminders and check-ins (e.g. following up on something you want to achieve). It can never change your triggers, and every plan it makes is posted in "From AAC".</p>
          <label for="aut-schedule">AAC may plan on its own</label>
          <select id="aut-schedule">
            ${[["allowed", "Yes, and tell me"], ["ask", "Only with my approval"], ["off", "No"]].map(([v, l]) => `<option value="${v}" ${st.autonomy?.schedule === v ? "selected" : ""}>${l}</option>`).join("")}
          </select>
        </div>
        <div class="card"><h2>TRIGGERS</h2>
          <p>Wake the master when something changes in Home Assistant, or at a time (reminders, routines), with your instruction.</p>
          ${triggers.length ? triggers.map((t) => `
            <div class="wrow">
              <span><span class="dot ${t.proposed ? "open" : t.enabled ? "ok" : ""}"></span> ${esc(t.name || t.when.entity_id || t.when.at)}
                ${t.created_by === "agent" ? '<span class="tag aac">AAC</span>' : ""}${t.proposed ? ' <span class="tag plan">PROPOSED</span>' : ""}
                <span class="hint">· ${esc(this._whenText(t.when))}</span>
                ${t.created_by === "agent" ? `<div class="hint" style="margin:.2em 0 0 1.1em">${esc(t.do?.instruction || "")}</div>` : ""}</span>
              <span class="actions">
                ${t.proposed ? `<button class="btn primary" data-approve="${esc(t.id)}">Approve</button><button class="btn danger" data-reject="${esc(t.id)}">Reject</button>` : `<button class="btn" data-go="trigger/edit/${esc(t.id)}">Edit</button>`}
              </span>
            </div>`).join("") : '<p class="hint">No triggers yet.</p>'}
          <div class="actions"><button class="btn primary" data-go="trigger/new">${icon("plus")} New trigger</button></div>
        </div>
      </div>`);
  }

  _whenText(when) {
    if (when.type === "time") {
      const days = when.date ? `once on ${when.date}` : (when.weekdays || []).length ? when.weekdays.map((d) => WEEKDAYS[d]).join(" ") : "every day";
      return `${days} at ${when.at}`;
    }
    return `${when.entity_id}${when.to ? ` → ${when.to}` : " changes"}`;
  }

  _renderTriggerForm() {
    const f = this._form;
    if (!f) return this._renderTriggers();
    const ids = Object.keys(this._hass.states).sort();
    const isTime = f.type === "time";
    return this._page(f.id ? "EDIT TRIGGER" : "NEW TRIGGER", "", `<div class="card"><h2>TRIGGER</h2>
        <div class="form-grid">
          <div>
            <label for="t-name">Name</label><input id="t-name" value="${esc(f.name)}" placeholder="${isTime ? "e.g. Pills" : "e.g. Front door"}">
            <label for="t-type">When</label>
            <select id="t-type"><option value="state" ${isTime ? "" : "selected"}>Something changes in Home Assistant</option><option value="time" ${isTime ? "selected" : ""}>At a time</option></select>
            ${isTime ? `
              <label for="t-at">Time</label><input id="t-at" type="time" value="${esc(f.at)}">
              <label>Days (none = every day)</label>
              <div class="row" style="gap:.3em">${WEEKDAYS.map((d, i) => `<label class="check" style="flex:none;min-width:0;margin:0 .6em 0 0"><input type="checkbox" class="t-day" value="${i}" ${f.weekdays.includes(i) ? "checked" : ""}>${d}</label>`).join("")}</div>
            ` : `
              <label for="t-entity">Entity</label><input id="t-entity" list="t-entities" value="${esc(f.entity_id)}" placeholder="binary_sensor.front_door" autocomplete="off">
              <datalist id="t-entities">${ids.map((i) => `<option value="${esc(i)}">${esc(this._hass.states[i].attributes.friendly_name || "")}</option>`).join("")}</datalist>
              <label for="t-to">Only when it becomes (optional)</label><input id="t-to" value="${esc(f.to)}" placeholder="e.g. on, open, home">
              <label for="t-cool">Cooldown (seconds)</label><input id="t-cool" type="number" min="0" max="86400" value="${esc(f.cooldown)}" inputmode="numeric">
            `}
            <label class="check"><input type="checkbox" id="t-quiet" ${f.quiet ? "checked" : ""}>Quiet hours</label>
            ${f.quiet ? `<div class="row"><div><label for="t-qs">From</label><input id="t-qs" type="time" value="${esc(f.quiet.start)}"></div><div><label for="t-qe">Until</label><input id="t-qe" type="time" value="${esc(f.quiet.end)}"></div></div>` : ""}
            <label class="check"><input type="checkbox" id="t-enabled" ${f.enabled ? "checked" : ""}>Enabled</label>
          </div>
          <div><label for="t-instr">Instruction for the master</label><textarea id="t-instr" class="thin-scroll" placeholder="${isTime ? "e.g. Remind me to take my pills." : "e.g. If nobody is home, tell me."}">${esc(f.instruction)}</textarea></div>
        </div>
        ${f.msg ? `<div class="msg ${f.msg.ok ? "ok" : "err"}">${esc(f.msg.text)}</div>` : ""}
        <div class="actions">
          <button class="btn primary" id="t-save">Save</button>
          ${f.id ? `<button class="btn danger" id="t-delete">Delete</button>` : ""}
          <button class="btn" data-go="settings/triggers">Cancel</button>
        </div>
      </div>`);
  }

  // ---------------------------------------------------------------- settings: models
  _renderModels() {
    const w = this._models;
    if (!w) return this._page("MODELS", "", '<div class="card"><h2>SCANNING</h2><p>Looking for model servers…<span class="cursor"></span></p></div>');
    const master = this._state?.master?.model;
    // A manual model's own server only matters here if it offers more models
    const servers = w.auto.filter((srv) => srv.models.length || !srv.source.startsWith("w:"));
    const auto = servers.map((srv) => `
      <div class="card"><h2><span class="dot ${srv.online ? "ok" : "bad"}"></span>${esc(srv.label)}</h2>
        <p>${esc(srv.base_url)}${srv.error ? ` · <span class="msg err">${esc(srv.error)}</span>` : ""}</p>
        ${srv.models.length ? srv.models.map((a) => `
          <div class="wrow">
            <span>${esc(a.model)} ${a.id === master ? '<span class="tag aac">MASTER</span>' : ""}</span>
            <span class="actions">
              <button class="btn" data-go="model/${esc(a.id)}">Details</button>
              ${a.id === master ? "" : `<button class="btn primary" data-master="${esc(a.id)}">Use as master</button>`}
            </span>
          </div>`).join("") : '<p class="hint">No models offered.</p>'}
      </div>`).join("");
    const manual = `<div class="card"><h2>MANUAL</h2><p>Models you add yourself: any OpenAI-compatible server (llama.cpp, Ollama, vLLM, LM Studio, cloud APIs).</p>
        ${w.manual.map((m) => `
          <div class="wrow">
            <span><span class="dot ${m.online ? "ok" : "bad"}"></span> ${esc(m.name)} <span class="hint">· ${esc(m.model)}</span> ${m.id === master ? '<span class="tag aac">MASTER</span>' : ""}</span>
            <span class="actions"><button class="btn" data-go="model/${esc(m.id)}">Details</button></span>
          </div>`).join("")}
        <div class="actions"><button class="btn primary" data-go="models/new">${icon("plus")} Add model</button></div>
      </div>`;
    return this._page("MODELS", "what AAC's agents can think with", `
      <div class="card"><h2>AUTO</h2><p>Found automatically: model servers already set up in Home Assistant (llama.cpp, Ollama; only read, never changed) and the servers of your manual models. Every model they offer can be used by AAC's agents.</p>
        ${servers.length ? "" : '<p class="hint">Nothing else found.</p>'}</div>
      <div class="cards">${auto}${manual}</div>`, `<button class="icon-btn" id="refresh-models" title="Scan again">${icon("refresh")}</button>`);
  }

  _renderModel(id) {
    const w = this._models;
    const master = this._state?.master?.model;
    const manual = w?.manual.find((m) => m.id === id);
    let srv = null, auto = null;
    for (const s of w?.auto || []) { const a = s.models.find((x) => x.id === id); if (a) { srv = s; auto = a; } }
    if (!manual && !auto) return this._page("MODEL", "", `<div class="card"><h2>NOT FOUND</h2><p>${w ? "This model is gone (server offline or removed)." : "Scanning…"}</p></div>`);
    const online = manual ? manual.online : srv.online;
    const rows = manual
      ? [["Type", "manual"], ["Name", manual.name], ["Model", manual.model], ["Server", manual.base_url], ["API key", manual.has_key ? "set" : "none"]]
      : [["Type", "auto"], ["Model", auto.model], ["Found via", srv.label], ["Server", srv.base_url]];
    rows.push(["State", online ? "online" : "offline"]);
    const err = manual ? manual.error : srv.error;
    return this._page(manual ? manual.name : auto.model, id === master ? '<span class="tag aac">MASTER</span>' : "", `<div class="card"><h2>MODEL</h2>
        <div class="kv">${rows.map(([k, v]) => `<span class="k">${esc(k)}</span><span class="v">${esc(v)}</span>`).join("")}</div>
        ${err ? `<div class="msg err">${esc(err)}</div>` : ""}
        <div class="actions">
          ${id === master ? "" : `<button class="btn primary" data-master="${esc(id)}">Use as master</button>`}
          ${manual ? `<button class="btn" data-go="model/edit/${esc(id)}">Edit</button><button class="btn danger" data-delete="${esc(id)}">Delete</button>` : ""}
          <button class="btn" data-go="models">All models</button>
        </div>
        ${this._flashHtml()}
      </div>`);
  }

  _renderModelForm() {
    const f = this._form;
    if (!f) return this._renderModels();
    const models = f.models.length
      ? `<select id="f-model">${f.models.map((m) => `<option ${m === f.model ? "selected" : ""}>${esc(m)}</option>`).join("")}</select>`
      : `<input id="f-model" value="${esc(f.model)}" placeholder="load the list with Test, or type a model id">`;
    return this._page(f.id ? "EDIT MODEL" : "ADD MODEL", "", `<div class="card"><h2>${f.id ? "EDIT" : "ADD"} MANUAL MODEL</h2>
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
          <button class="btn" data-go="${f.id ? `model/${esc(f.id)}` : "models"}">Cancel</button>
        </div>
      </div>`);
  }

  // ---------------------------------------------------------------- settings: charter
  _renderCharter() {
    const st = this._state;
    if (!st) return this._page("CHARTER", "", "");
    return this._page("CHARTER", st.charter_is_default ? "built-in draft" : "your own", `<div class="card"><h2>CHARTER</h2>
        <p>Your values in plain words. Every AAC agent gets this first, before anything else, and no agent can change it; only you can, here. It is not a score to optimize: it tells AAC how to behave (honest, sparing, your decisions stay yours).</p>
        <label for="ch-text">Charter</label>
        <textarea id="ch-text" class="thin-scroll" style="min-height:20em">${esc(st.charter)}</textarea>
        <div class="actions"><button class="btn primary" id="ch-save">Save</button></div>
        ${this._flashHtml()}
      </div>`);
  }

  // ---------------------------------------------------------------- settings: master
  _renderMaster() {
    const st = this._state;
    if (!st) return this._page("MASTER AGENT", "", "");
    const m = st.master;
    const models = this._allModels();
    const known = models.some((w) => w.id === m.model);
    const opts = [`<option value="" ${m.model ? "" : "selected"}>— choose a model —</option>`]
      .concat(!known && m.model ? [`<option value="${esc(m.model)}" selected>${esc(this._modelLabel(m.model))} (offline)</option>`] : [])
      .concat(models.map((w) => `<option value="${esc(w.id)}" ${w.id === m.model ? "selected" : ""}>${esc(w.label)} · ${esc(w.detail)}${w.online ? "" : " (offline)"}</option>`));
    return this._page("MASTER AGENT", st.agent_entity_id ? esc(st.agent_entity_id) : "", `<div class="card"><h2>MASTER AGENT</h2>
        <p>The persistent agent that answers, decides and acts. Models are managed under SETTINGS → Models.</p>
        <div class="form-grid">
          <div>
            <label for="m-model">Model</label><select id="m-model">${opts.join("")}</select>
            ${this._models ? "" : '<div class="hint">scanning models…</div>'}
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

  // ---------------------------------------------------------------- settings: memory
  _renderMemory() {
    const st = this._state;
    if (!st) return this._page("MEMORY", "", "");
    const mem = st.memory;
    const m = this._memory;
    const stats = m?.stats || {};
    const models = this._allModels();
    const opts = [`<option value="" ${mem.embed_model ? "" : "selected"}>— none (keywords only) —</option>`]
      .concat(models.map((w) => `<option value="${esc(w.id)}" ${w.id === mem.embed_model ? "selected" : ""}>${esc(w.label)} · ${esc(w.detail)}${w.online ? "" : " (offline)"}</option>`));
    const rows = !m ? '<p class="hint">Loading…</p>' : !m.memories.length ? `<p class="hint">${this._memQuery ? "Nothing found." : "Nothing remembered yet."}</p>` :
      m.memories.map((x) => `
        <div class="src">
          <span class="dot ${x.confirmed ? "ok" : "open"}"></span>
          <div style="flex:1;min-width:0">
            <div class="n">${esc(x.text)}</div>
            <div class="m">${esc(x.kind)} · ${esc(x.source)} · ${esc(fmtTime(x.updated))}${x.embedded ? "" : " · no vector"}${x.confirmed ? "" : " · <b style='color:var(--amber)'>unconfirmed</b>"}</div>
          </div>
          <span class="actions" style="margin:0">
            ${x.confirmed ? "" : `<button class="btn primary" data-mem-confirm="${esc(x.id)}">Confirm</button>`}
            <button class="btn" data-mem-edit="${esc(x.id)}">Edit</button>
            <button class="icon-btn" data-mem-delete="${esc(x.id)}" title="Forget">${icon("trash")}</button>
          </span>
        </div>`).join("");
    return this._page("MEMORY", `${stats.total ?? "…"} memories${stats.unconfirmed ? ` · ${stats.unconfirmed} unconfirmed` : ""}`, `
      <div class="cards">
        <div class="card"><h2>SETTINGS</h2>
          <p>One local file in Home Assistant's config folder (included in HA backups). Search uses keywords and, with an embedding model, meaning ("cellar" finds "basement"). The master always has remember / recall / forget.</p>
          <label class="check"><input type="checkbox" id="mem-enabled" ${mem.enabled ? "checked" : ""}>Memory on</label>
          <label for="mem-model">Embedding model</label><select id="mem-model">${opts.join("")}</select>
          <p class="hint" style="margin-top:.5em">A multilingual one is best (e.g. bge-m3 via llama-server <code>--embeddings</code>). Add it under Models first.</p>
          <div class="kv"><span class="k">Stored</span><span class="v">${esc(stats.total ?? "…")}</span><span class="k">With vectors</span><span class="v">${esc(stats.embedded ?? "…")}</span><span class="k">Unconfirmed</span><span class="v">${esc(stats.unconfirmed ?? "…")}</span></div>
          <div class="actions"><button class="btn primary" id="mem-save">Save</button><button class="btn" id="mem-reembed">Re-embed all</button></div>
          ${this._flashHtml()}
        </div>
        <div class="card"><h2>REMEMBER</h2>
          <p>Tell AAC something directly. "agent believes" entries are AAC's own conclusions; they count as facts only once you confirm them.</p>
          <label for="mem-text">Text</label><textarea id="mem-text" class="thin-scroll" style="min-height:5em" placeholder="e.g. Oat milk from brand X was good."></textarea>
          <label for="mem-kind">Kind</label>
          <select id="mem-kind">${["note", "fact", "preference", "event", "plan"].map((k) => `<option>${k}</option>`).join("")}</select>
          <p class="hint" style="margin-top:.5em">"plan" = something you want to achieve or do; AAC keeps it in mind when it looks around on its own.</p>
          <div class="actions"><button class="btn primary" id="mem-add">Remember</button></div>
        </div>
      </div>
      <div class="card"><h2>FACTS</h2>
        <p>Things with a value (measurements, places, ratings), stored with fields so they can be compared exactly. Each one is also a text memory below.</p>
        ${!this._facts ? '<p class="hint">Loading…</p>' : !this._facts.keys.length ? '<p class="hint">No facts yet.</p>' :
          this._facts.keys.map((k) => {
            const sm = k.summary || {};
            const open = this._factOpen === `${k.subject}|${k.attribute}`;
            const val = sm.change !== undefined ? `${sm.first} → ${sm.latest}${sm.unit ? " " + sm.unit : ""} (${sm.change > 0 ? "+" : ""}${sm.change})` : `${sm.latest}${sm.unit ? " " + sm.unit : ""}`;
            return `<div class="src" style="cursor:pointer" data-fact="${esc(k.subject)}|${esc(k.attribute)}" tabindex="0" role="button">
              <span class="dot ok"></span>
              <div style="flex:1;min-width:0">
                <div class="n">${esc(k.subject)} · ${esc(k.attribute)}: <b>${esc(val)}</b></div>
                <div class="m">${esc(k.count)} value(s) · last ${esc(fmtTime(k.last))}</div>
                ${open && this._factSeries ? `<div class="m" style="margin-top:.4em">${this._factSeries.map((f) => `${esc(f.time.slice(0, 10))}: ${esc(f.value)}${f.unit ? " " + esc(f.unit) : ""}`).join(" · ")}</div>` : ""}
              </div>
            </div>`;
          }).join("")}
      </div>
      <div class="card"><h2>BROWSE</h2>
        <div class="row">
          <div><input id="mem-q" value="${esc(this._memQuery || "")}" placeholder="Search by words or meaning…" aria-label="Search memories"></div>
          <div style="flex:none" class="actions"><button class="btn" id="mem-search">Search</button></div>
        </div>
        <label class="check"><input type="checkbox" id="mem-open" ${this._memOpenOnly ? "checked" : ""}>Only unconfirmed</label>
        ${rows}
      </div>`);
  }

  // ---------------------------------------------------------------- settings: audio
  _renderAudio() {
    const a = this._audio;
    if (!a || !a.stages) return this._page("AUDIO PIPELINE", "", `<div class="card"><p>${a?.error ? esc(a.error) : "Loading…"}</p></div>`);
    const flow = a.stages.map((s) => `
      <div class="stage ${s.state === "built" ? "ok" : ""}">
        <div class="rail"><span class="node"></span><span class="wire"></span></div>
        <div class="body"><div class="name">${esc(s.name)} ${s.state === "built" ? "" : '<span class="tag plan">PLANNED</span>'}</div><div class="desc">${esc(s.desc)}</div></div>
      </div>`).join("");
    const set = a.settings || {}, eng = a.engines || {};
    const opt = (list, chosen, current) => `<option value="" ${chosen ? "" : "selected"}>Local first (now: ${esc(current || "none")})</option>` +
      (list || []).map((e) => `<option value="${esc(e)}" ${chosen === e ? "selected" : ""}>${esc(e)}</option>`).join("");
    const lines = (a.transcript || []).slice().reverse().map((l) => `
      <div class="tline ${l.addressed ? "to-aac" : ""}"><span class="m">${esc(new Date(l.time).toLocaleTimeString())} · ${esc(l.name)}${l.addressed ? " → AAC" : ""}</span><div>${esc(l.text)}</div></div>`).join("");
    return this._page("AUDIO PIPELINE", `${(a.connected || []).length} connected · separate from HA's Assist`, `
      <div class="split">
        <div class="card"><h2>${icon("mic")} CONNECTED</h2>
          ${this._liveSources(a.connected || [])}
          <div class="actions"><button class="btn" data-go="settings/audio/sources">All sources</button></div>
        </div>
        <div class="card"><h2>TRANSCRIPT</h2>
          <p class="hint">What AAC heard (RAM only, the last lines). Lines with one of its names, or right after it spoke, go to the master.</p>
          <div class="transcript thin-scroll">${lines || '<p class="hint">Nothing heard yet.</p>'}</div>
        </div>
      </div>
      <div class="split">
        <div class="card"><h2>SETTINGS</h2>
          <label for="au-names">Names that wake AAC (comma separated)</label><input id="au-names" value="${esc((set.names || []).join(", "))}">
          <div class="row">
            <div><label for="au-follow">Follow-up without name (s)</label><input id="au-follow" type="number" min="0" max="120" value="${esc(set.follow_up ?? 10)}" inputmode="numeric"></div>
            <div><label for="au-lang">Language (empty = HA's)</label><input id="au-lang" value="${esc(set.language || "")}" placeholder="en, de, …"></div>
          </div>
          <label for="au-stt">Speech to text</label><select id="au-stt">${opt(eng.stt_options, set.stt, eng.stt)}</select>
          <label for="au-tts">Voice (text to speech)</label><select id="au-tts">${opt(eng.tts_options, set.tts, eng.tts)}</select>
          <p class="hint">HA's speech engines are used as tools; HA's own voice assistants stay as they are. "Local first" prefers engines running in your home (Wyoming: faster-whisper, Piper) over cloud ones.</p>
          <div class="actions"><button class="btn primary" id="au-save">Save</button></div>
          ${this._flashHtml()}
        </div>
        <div class="card"><h2>PIPELINE</h2>
          <p>Every source gets its own light pipeline; the heavy parts are shared.</p>
          <div class="flow">${flow}</div>
        </div>
      </div>`);
  }

  _liveSources(list) {
    if (!list.length) return `<p>No source connected. Run the AAC satellite on the machine with the microphone (a PC, or next to a headset):</p>
      <pre class="code">python aac_satellite.py --url ${esc(location.origin)} --token-file ~/.aac_token --name PC --area Office</pre>
      <p class="hint">It is in the <code>satellite/</code> folder of the AAC repository. The token is a long-lived access token (your HA profile → Security). Or ask AAC in a chat to walk you through it.</p>`;
    return list.map((s) => `
      <div class="src">${icon("mic")}
        <div style="flex:1;min-width:0">
          <div class="n">${esc(s.name)}</div>
          <div class="m">${s.area ? `${esc(s.area)} · ` : ""}${s.heard} heard${s.playing ? " · speaking" : ""}</div>
          <div class="row" style="margin-top:.4em"><div style="flex:1"><input data-say-text="${esc(s.id)}" placeholder="Say something here…"></div><div style="flex:none"><button class="btn" data-say="${esc(s.id)}">Say</button></div></div>
        </div>
        <span class="tag ok">LIVE</span>
      </div>`).join("");
  }

  _renderSources() {
    const a = this._audio;
    if (!a || !a.stages) return this._page("AUDIO SOURCES", "", '<div class="card"><p>Loading…</p></div>');
    const live = new Set((a.connected || []).map((s) => s.id));
    const known = (a.settings?.known || []).filter((k) => !live.has(k.id));
    return this._page("AUDIO SOURCES", `${(a.connected || []).length} connected`, `
      <div class="cards">
        <div class="card"><h2>AAC SATELLITES</h2>
          <p>A satellite streams one microphone to AAC and plays its answers. The same program runs on a PC or next to a USB/Bluetooth headset.</p>
          ${this._liveSources(a.connected || [])}
          ${known.map((k) => `<div class="src">${icon("mic")}<div style="flex:1"><div class="n">${esc(k.name)}</div><div class="m">seen before</div></div><span class="tag">OFFLINE</span></div>`).join("")}
        </div>
        <div class="card"><h2>FROM HOME ASSISTANT</h2>
          <p>Devices with a mic that HA knows. Using them as sources is planned; for now they are only listed.</p>
          ${this._sourceList(a.candidates || [])}
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
        <span class="tag plan">PLANNED</span>
      </div>`).join("");
  }

  _flashHtml() {
    return this._flash ? `<div class="msg ${this._flash.ok ? "ok" : "err"}">${esc(this._flash.text)}</div>` : "";
  }

  // ---------------------------------------------------------------- events
  _bind() {
    const sr = this.shadowRoot;
    const onActivate = (el, fn) => {
      el.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
      if (el.getAttribute("role") === "button") {
        el.addEventListener("keydown", (e) => {
          if (e.target !== el) return;
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(); }
        });
      }
    };
    sr.querySelectorAll("[data-go]").forEach((el) => onActivate(el, () => this._go(el.dataset.go)));
    const fold = sr.querySelector("#auto-fold");
    if (fold) onActivate(fold, () => {
      this._autoOpen = !this._autoOpen;
      try { localStorage.setItem("aac.autoOpen", this._autoOpen ? "1" : "0"); } catch (e) { /* ignore */ }
      this._render();
    });
    sr.querySelector("#eye")?.addEventListener("click", () => {
      this._showAll = !this._showAll;
      try { localStorage.setItem("aac.showAll", this._showAll ? "1" : "0"); } catch (e) { /* ignore */ }
      this._render();
    });
    sr.querySelector("#nav-toggle")?.addEventListener("click", () => { this._navOpen = !this._navOpen; this._render(); });
    sr.querySelector("#backdrop")?.addEventListener("click", () => { this._navOpen = false; this._render(); });
    sr.querySelector("#refresh-models")?.addEventListener("click", () => this._loadModels());

    sr.querySelector("#setup-chat")?.addEventListener("click", () => {
      this._go("chat/new");
      const input = this.shadowRoot.querySelector("#chat-input");
      if (input) { input.value = "Help me set up the rest of AAC."; this._send(); }
    });
    sr.querySelectorAll("[data-skip]").forEach((el) => el.addEventListener("click", async (e) => {
      e.stopPropagation();
      this._state = await this._ws("aac/setup/skip", { step: el.dataset.skip, skipped: el.dataset.skipped === "1" });
      this._render();
    }));
    sr.querySelectorAll("[data-master]").forEach((el) => el.addEventListener("click", async (e) => {
      e.stopPropagation();
      this._state = await this._ws("aac/master/update", { model: el.dataset.master });
      this._flash = { ok: true, text: "Master model set." };
      this._render();
    }));
    sr.querySelectorAll("[data-delete]").forEach((el) => el.addEventListener("click", async () => {
      if (!confirm("Delete this model?")) return;
      await this._ws("aac/model/delete", { model_id: el.dataset.delete });
      this._view = "models";
      await Promise.all([this._loadState(), this._loadModels()]);
    }));

    // chat
    sr.querySelector("#chat-input")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this._send(); }
    });
    sr.querySelector("#chat-send")?.addEventListener("click", () => this._send());
    sr.querySelector("#chat-delete")?.addEventListener("click", async () => {
      if (!this._chat || !confirm("Delete this chat?")) return;
      await this._ws("aac/chat/delete", { chat_id: this._chat.id });
      this._chat = null;
      this._view = "chat/new";
      await this._loadChats();
    });

    // forms
    sr.querySelector("#f-test")?.addEventListener("click", () => this._formTest());
    sr.querySelector("#f-save")?.addEventListener("click", () => this._formSave());
    sr.querySelector("#m-save")?.addEventListener("click", () => this._saveMaster());
    sr.querySelector("#ch-save")?.addEventListener("click", async () => {
      try {
        this._state = await this._ws("aac/charter", { text: sr.querySelector("#ch-text").value });
        this._flash = { ok: true, text: "Saved. Every agent follows it from the next message on." };
      } catch (e) { this._flash = { ok: false, text: e.message || String(e) }; }
      this._render();
    });
    sr.querySelector("#hb-save")?.addEventListener("click", () => this._saveHeartbeat());
    sr.querySelector("#au-save")?.addEventListener("click", () => this._saveAudio());
    sr.querySelectorAll("[data-say]").forEach((el) => el.addEventListener("click", async () => {
      const input = sr.querySelector(`[data-say-text="${CSS.escape(el.dataset.say)}"]`);
      const text = input?.value.trim() || "Hello, this is AAC.";
      try { await this._ws("aac/audio/say", { source_id: el.dataset.say, text }); if (input) input.value = ""; }
      catch (e) { this._flash = { ok: false, text: e.message || String(e) }; this._render(); }
    }));
    sr.querySelector("#hb-run")?.addEventListener("click", () => this._runHeartbeat());
    sr.querySelector("#t-save")?.addEventListener("click", () => this._saveTrigger());
    sr.querySelector("#aut-schedule")?.addEventListener("change", async (e) => {
      this._state = await this._ws("aac/autonomy", { schedule: e.target.value });
      this._render();
    });
    sr.querySelectorAll("[data-approve]").forEach((el) => el.addEventListener("click", async () => {
      this._state = await this._ws("aac/trigger/approve", { trigger_id: el.dataset.approve });
      this._render();
    }));
    sr.querySelectorAll("[data-reject]").forEach((el) => el.addEventListener("click", async () => {
      await this._ws("aac/trigger/delete", { trigger_id: el.dataset.reject });
      await this._loadState();
    }));
    sr.querySelector("#t-type")?.addEventListener("change", (e) => { this._readTriggerForm(); this._form.type = e.target.value; this._renderedView = null; this._render(); });
    sr.querySelector("#t-quiet")?.addEventListener("change", (e) => { this._readTriggerForm(); this._form.quiet = e.target.checked ? { start: "22:00", end: "08:00" } : null; this._renderedView = null; this._render(); });
    sr.querySelector("#t-delete")?.addEventListener("click", () => this._deleteTrigger());

    // memory
    const memSearch = () => { this._memQuery = sr.querySelector("#mem-q").value.trim(); this._memOpenOnly = false; this._loadMemory(); };
    sr.querySelector("#mem-search")?.addEventListener("click", memSearch);
    sr.querySelector("#mem-q")?.addEventListener("keydown", (e) => { if (e.key === "Enter") memSearch(); });
    sr.querySelector("#mem-open")?.addEventListener("change", (e) => { this._memOpenOnly = e.target.checked; this._memQuery = ""; this._loadMemory(); });
    sr.querySelector("#mem-save")?.addEventListener("click", async () => {
      try {
        this._state = await this._ws("aac/memory/settings", { enabled: sr.querySelector("#mem-enabled").checked, embed_model: sr.querySelector("#mem-model").value || null });
        this._flash = { ok: true, text: "Saved. A new embedding model fills in vectors in the background." };
      } catch (e) { this._flash = { ok: false, text: e.message || String(e) }; }
      this._render();
    });
    sr.querySelector("#mem-reembed")?.addEventListener("click", async () => {
      this._flash = { ok: true, text: "Embedding…" }; this._render();
      try { const r = await this._ws("aac/memory/reembed"); this._flash = { ok: true, text: `Done: ${r.embedded} memories got vectors.` }; }
      catch (e) { this._flash = { ok: false, text: e.message || String(e) }; }
      await this._loadMemory();
    });
    sr.querySelector("#mem-add")?.addEventListener("click", async () => {
      const text = sr.querySelector("#mem-text").value.trim();
      if (!text) return;
      await this._ws("aac/memory/save", { text, kind: sr.querySelector("#mem-kind").value });
      sr.querySelector("#mem-text").value = "";
      this._memQuery = "";
      await this._loadMemory();
    });
    sr.querySelectorAll("[data-fact]").forEach((el) => el.addEventListener("click", async () => {
      const [subject, attribute] = el.dataset.fact.split("|");
      if (this._factOpen === el.dataset.fact) { this._factOpen = null; return this._render(); }
      this._factOpen = el.dataset.fact;
      this._factSeries = (await this._ws("aac/facts", { subject, attribute })).series;
      this._render();
    }));
    sr.querySelectorAll("[data-mem-confirm]").forEach((el) => el.addEventListener("click", async () => {
      await this._ws("aac/memory/save", { memory_id: el.dataset.memConfirm, confirmed: true });
      await this._loadMemory();
    }));
    sr.querySelectorAll("[data-mem-edit]").forEach((el) => el.addEventListener("click", async () => {
      const item = this._memory.memories.find((x) => x.id === el.dataset.memEdit);
      const text = prompt("Edit memory", item?.text || "");
      if (text === null || !text.trim()) return;
      await this._ws("aac/memory/save", { memory_id: el.dataset.memEdit, text: text.trim(), confirmed: true });
      await this._loadMemory();
    }));
    sr.querySelectorAll("[data-mem-delete]").forEach((el) => el.addEventListener("click", async () => {
      if (!confirm("Forget this memory?")) return;
      await this._ws("aac/memory/delete", { memory_id: el.dataset.memDelete });
      await this._loadMemory();
    }));
  }

  _typing() {
    const el = this.shadowRoot.activeElement;
    return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT");
  }

  async _saveAudio() {
    const sr = this.shadowRoot;
    try {
      this._audio = await this._ws("aac/audio/settings", {
        names: sr.querySelector("#au-names").value.split(",").map((n) => n.trim()).filter(Boolean),
        follow_up: Number(sr.querySelector("#au-follow").value) || 0,
        language: sr.querySelector("#au-lang").value.trim() || null,
        stt: sr.querySelector("#au-stt").value || null,
        tts: sr.querySelector("#au-tts").value || null,
      });
      this._flash = { ok: true, text: "Saved." };
    } catch (e) { this._flash = { ok: false, text: e.message || String(e) }; }
    this._render();
  }

  async _send() {
    const input = this.shadowRoot.querySelector("#chat-input");
    const text = input?.value.trim();
    if (!text || this._busy) return;
    input.value = "";
    const chatId = this._view === "chat/new" ? null : this._chat?.id;
    if (!this._chat || !chatId) this._chat = { id: null, title: text.slice(0, 48), messages: [] };
    this._chat.messages = [...this._chat.messages, { role: "user", text }];
    this._busy = true;
    this._render();
    try {
      const r = await this._ws("aac/chat", { text, chat_id: chatId });
      this._busy = false;
      await this._openChat(r.chat_id);
      await this._loadChats();
    } catch (e) {
      this._busy = false;
      this._chat.messages = [...this._chat.messages, { role: "aac", text: e.message || String(e), error: true }];
      this._render();
    }
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
      if (f.id) data.model_id = f.id;
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
    if (f.id) data.model_id = f.id;
    if (f.api_key || !f.id) data.api_key = f.api_key || null;
    try {
      const r = await this._ws("aac/model/save", data);
      this._view = `model/${r.id}`;
      await Promise.all([this._loadState(), this._loadModels()]);
    } catch (e) {
      f.msg = { ok: false, text: e.message || String(e) };
      this._render();
    }
  }

  async _saveMaster() {
    const sr = this.shadowRoot;
    try {
      this._state = await this._ws("aac/master/update", {
        model: sr.querySelector("#m-model").value || null,
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

  async _saveHeartbeat() {
    const sr = this.shadowRoot;
    try {
      this._state = await this._ws("aac/heartbeat/update", {
        enabled: sr.querySelector("#hb-enabled").checked,
        interval: parseInt(sr.querySelector("#hb-interval").value, 10),
        quiet_start: sr.querySelector("#hb-qs").value || "00:00",
        quiet_end: sr.querySelector("#hb-qe").value || "00:00",
        instructions: sr.querySelector("#hb-instr").value,
        tools: sr.querySelector("#hb-tools").checked,
      });
      this._flash = { ok: true, text: "Saved." };
    } catch (e) {
      this._flash = { ok: false, text: e.message || String(e) };
    }
    this._render();
  }

  async _runHeartbeat() {
    this._flash = { ok: true, text: "Heartbeat running… (see WORKERS → Auto)" };
    this._autoOpen = true;
    this._render();
    try {
      const r = await this._ws("aac/heartbeat/run");
      const run = this._runs.find((x) => x.id === r.run_id);
      this._flash = { ok: true, text: run?.status === "silent" ? "Done: nothing worth saying." : "Done: AAC said something (see From AAC)." };
    } catch (e) {
      this._flash = { ok: false, text: e.message || String(e) };
    }
    this._render();
  }

  _readTriggerForm() {
    const sr = this.shadowRoot, f = this._form;
    const val = (id) => sr.querySelector(id)?.value;
    f.name = val("#t-name").trim();
    f.instruction = val("#t-instr").trim();
    f.enabled = sr.querySelector("#t-enabled").checked;
    if (f.type === "time") {
      f.at = val("#t-at") || f.at;
      f.weekdays = [...sr.querySelectorAll(".t-day:checked")].map((el) => parseInt(el.value, 10));
    } else {
      f.entity_id = val("#t-entity").trim();
      f.to = val("#t-to").trim();
      f.cooldown = parseInt(val("#t-cool"), 10) || 0;
    }
    if (f.quiet) f.quiet = { start: val("#t-qs") || f.quiet.start, end: val("#t-qe") || f.quiet.end };
  }

  async _saveTrigger() {
    this._readTriggerForm();
    const f = this._form;
    const when = f.type === "time"
      ? { type: "time", at: f.at, weekdays: f.weekdays }
      : { type: "state", entity_id: f.entity_id, to: f.to };
    if (f.type === "state" && !this._hass.states[f.entity_id]) { f.msg = { ok: false, text: "Unknown entity." }; return this._render(); }
    const data = { name: f.name, when, instruction: f.instruction, cooldown: f.type === "time" ? 0 : f.cooldown, quiet_hours: f.quiet, enabled: f.enabled };
    if (f.id) data.trigger_id = f.id;
    try {
      await this._ws("aac/trigger/save", data);
      this._view = "settings/triggers";
      await this._loadState();
    } catch (e) {
      f.msg = { ok: false, text: e.message || String(e) };
      this._render();
    }
  }

  async _deleteTrigger() {
    if (!this._form?.id || !confirm("Delete this trigger?")) return;
    await this._ws("aac/trigger/delete", { trigger_id: this._form.id });
    this._view = "settings/triggers";
    await this._loadState();
  }
}

if (!customElements.get("aac-panel")) customElements.define("aac-panel", AacPanel);

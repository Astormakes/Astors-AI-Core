// AAC – Astor's AI Core: the panel.
// Plain web component, no build step, no external resources (works offline).
// Talks to the backend via the aac/* WebSocket commands.

// The version this script was loaded as (HA adds ?v=<version>); a newer backend means: reload
const PANEL_VERSION = (() => { try { return new URL(import.meta.url).searchParams.get("v"); } catch (e) { return null; } })();
// Sizes are in em on top of a fluid base font, so it scales from phone to TV.

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// Markdown as the models write it (headings, lists, tables, code, bold/italic, links); escaped first, so no HTML gets in
const mdInline = (t) => esc(t)
  .replace(/`([^`]+)`/g, "<code>$1</code>")
  .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
  .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>")
  .replace(/(^|[\s(])_([^_\s][^_]*)_(?=[\s.,;:!?)]|$)/g, "$1<i>$2</i>")
  .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
const md = (text) => {
  const lines = String(text ?? "").replace(/\r/g, "").split("\n");
  const out = [];
  let para = [];
  const flush = () => { if (para.length) out.push(`<p>${para.map(mdInline).join("<br>")}</p>`); para = []; };
  const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (/^\s*```/.test(l)) {  // code block (also an unfinished one while streaming)
      flush();
      const code = [];
      while (++i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i]);
      out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
    } else if (/^\s*$/.test(l)) {
      flush();
    } else if (/^#{1,6}\s/.test(l)) {
      flush();
      const n = Math.min(l.match(/^#+/)[0].length + 2, 6);
      out.push(`<h${n}>${mdInline(l.replace(/^#+\s*/, ""))}</h${n}>`);
    } else if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(l)) {
      flush();
      out.push("<hr>");
    } else if (/^\s*\|.*\|\s*$/.test(l) && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1] || "") && (lines[i + 1] || "").includes("-")) {
      flush();
      const head = cells(l);
      i++;
      const rows = [];
      while (i + 1 < lines.length && /^\s*\|.*\|\s*$/.test(lines[i + 1])) rows.push(cells(lines[++i]));
      out.push(`<div class="md-table"><table><thead><tr>${head.map((c) => `<th>${mdInline(c)}</th>`).join("")}</tr></thead>
        <tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${mdInline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
    } else if (/^\s*([-*+]|\d+[.)])\s+/.test(l)) {
      flush();
      const ordered = /^\s*\d/.test(l);
      const items = [];
      i--;
      while (i + 1 < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i + 1])) {
        const item = lines[++i];
        const depth = Math.floor(item.match(/^\s*/)[0].length / 2);
        items.push(`<li style="margin-left:${depth * 1.2}em">${mdInline(item.replace(/^\s*([-*+]|\d+[.)])\s+/, ""))}</li>`);
      }
      out.push(ordered ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`);
    } else if (/^\s*>\s?/.test(l)) {
      flush();
      out.push(`<blockquote>${mdInline(l.replace(/^\s*>\s?/, ""))}</blockquote>`);
    } else {
      para.push(l);
    }
  }
  flush();
  return out.join("");
};

// Where each pool is set: thinking in Compute, voice in the audio pipeline, vision under Video
const POOL_HOME = { thinking: "models", voice: "settings/audio", vision: "settings/video" };
const POOL_HOME_NAME = { models: "Compute", "settings/audio": "Audio pipeline", "settings/video": "Video" };

const ICONS = {
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  eyeOff: '<path d="M3 3l18 18"/><path d="M10.6 5.1A10 10 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4.2M6.6 6.6C3.8 8.4 2 12 2 12s3.6 7 10 7a9.7 9.7 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  setup: '<path d="M12 2l8.7 5v10L12 22l-8.7-5V7z"/><path d="M12 8v4l3 2"/>',
  chat: '<path d="M4 4h16v12H8l-4 4z"/><path d="M8 9h8M8 12h5"/>',
  workers: '<rect x="4" y="4" width="16" height="16"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4v5h-5"/>',
  panel: '<rect x="3" y="4" width="18" height="16"/><path d="M9 4v16M5.5 8h1.5M5.5 11h1.5M5.5 14h1.5"/>',
  send: '<path d="M3 11l18-8-8 18-2-8z"/>',
  bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
  chev: '<path d="M9 6l6 6-6 6"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  mic: '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 17v5M8 22h8"/>',
};
// A small "?" whose text shows on hover / focus (tap on a phone): explanations stay out of the way
const tip = (html) => `<span class="qm" tabindex="0" role="button" aria-label="Explain">?<span class="tip" role="tooltip">${html}</span></span>`;

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
.brand { display: flex; align-items: center; gap: .7em; padding: 1em 1em .6em; }
.brand .name { font-size: 1.45em; font-weight: 700; letter-spacing: .28em; color: var(--magenta);
  text-shadow: 0 0 .2em var(--magenta), 0 0 .7em rgba(255, 43, 214, .6); }
.brand .ver { font-size: .8em; color: var(--dim); }
.ent-table { width: 100%; border-collapse: collapse; }
.ent-table td { padding: .3em .4em; border-bottom: 1px solid color-mix(in srgb, var(--line) 50%, transparent); vertical-align: middle; }
.ent-table td:last-child { width: 9em; }
.ent-table select { min-height: 0; padding: .3em .4em; }
.lits { white-space: nowrap; width: 11em; text-align: right; }
.lit { font: inherit; font-size: .8em; padding: .3em .8em; margin-left: .3em; cursor: pointer; border: 1px solid var(--line); background: transparent; color: var(--dim); border-radius: 1em; }
.lit.on { color: var(--bg); background: var(--cyan); border-color: var(--cyan); box-shadow: 0 0 .5em color-mix(in srgb, var(--cyan) 60%, transparent); }
.lit[data-what="write"].on { background: var(--magenta); border-color: var(--magenta); box-shadow: 0 0 .5em color-mix(in srgb, var(--magenta) 60%, transparent); }
.vis-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(18em, 1fr)); gap: .1em 1.2em; }
.vis-item { align-items: flex-start; margin: .25em 0; min-width: 0; }
.vis-item > span { min-width: 0; overflow: hidden; }
.vis-item .hint { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: .8em; }
.vis-item input { margin-top: .2em; }
.msg-acts { display: flex; justify-content: flex-end; gap: .3em; margin-top: .3em; opacity: 0; transition: opacity .1s; }
.bubble:hover .msg-acts, .bubble:focus-within .msg-acts { opacity: .8; }
@media (hover: none) { .msg-acts { opacity: .6; } }
.msg-acts .icon-btn { font-size: .75em; padding: .1em .5em; color: var(--dim); }
#msg-edit { min-height: 5em; width: 100%; }
.unread.drop { animation: req-drop .7s cubic-bezier(.55, 0, 1, .45) both; transform-origin: center bottom; }
@keyframes req-drop {
  0% { transform: translateY(-1.6em) scale(2.6); opacity: .2; }
  80% { transform: translateY(0) scale(1); opacity: 1; }
  90% { transform: translateY(0) scale(1.15, .85); }
  100% { transform: none; }
}
@media (prefers-reduced-motion: reduce) { .unread.drop { animation: none; } }
.dlg-back { position: absolute; inset: 0; z-index: 60; background: rgba(0, 0, 0, .55); display: grid; place-items: center; padding: 1em; }
.dlg { width: min(32em, 100%); margin: 0; }
.dlg textarea { min-height: 8em; }
.update-banner { display: flex; align-items: center; gap: 1em; padding: .5em 1.7em; background: rgba(255, 176, 0, .12); color: var(--amber); border-bottom: 1px solid var(--amber); font-size: .9em; }
.tab .chatdel { margin-left: .3em; padding: 0 .3em; font-size: .85em; line-height: 1; opacity: 0; }
.tab .chatdel .ico { width: 1em; height: 1em; filter: none; }
.tab:hover .chatdel, .tab:focus-within .chatdel, .tab.active .chatdel { opacity: .6; }
@media (hover: none) { .tab .chatdel { opacity: .5; } }
.tab .modmore { margin-left: auto; padding: 0 .3em; font-size: 1.1em; line-height: 1; opacity: .6; }
.modwrap { position: relative; }
.qm { position: relative; display: inline-flex; align-items: center; justify-content: center; width: 1.25em; height: 1.25em; margin-left: .35em;
  border: 1px solid currentColor; border-radius: 50%; font-size: .8em; line-height: 1; letter-spacing: 0; text-transform: none; opacity: .55; cursor: help; vertical-align: middle; flex: none; }
.qm:hover, .qm:focus { opacity: 1; outline: none; }
.qm .tip { display: none; position: absolute; z-index: 60; left: 50%; top: calc(100% + .5em); transform: translateX(-50%); width: max-content; max-width: min(26em, 80vw);
  padding: .7em .9em; background: var(--bg2); color: var(--text); border: 1px solid var(--line); border-radius: .4em; box-shadow: 0 .4em 1.4em rgba(0, 0, 0, .5);
  font-size: .95rem; font-weight: normal; letter-spacing: normal; line-height: 1.45; text-shadow: none; text-align: left; white-space: normal; cursor: auto; }
.qm:hover .tip, .qm:focus .tip, .qm:focus-within .tip { display: block; }
@media (max-width: 700px) { .qm .tip { position: fixed; left: 16px; right: 16px; top: auto; bottom: 16px; transform: none; max-width: none; width: auto; } }
.modmenu { top: 100%; }
.tab.hidden-mod { opacity: .45; }
.dswitch { position: relative; padding: 0 1em .7em; border-bottom: 1px solid var(--line); }
.dbtn { width: 100%; display: flex; align-items: center; gap: .5em; background: transparent; color: var(--cyan); border: 1px solid var(--line);
  padding: .45em .7em; font: inherit; font-size: .93em; letter-spacing: .12em; cursor: pointer; text-align: left; text-shadow: 0 0 .3em rgba(0, 240, 255, .5); }
.dbtn .grow { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dbtn .ico { width: 1em; height: 1em; transform: rotate(90deg); transition: transform .15s; }
.dbtn .ico.rot { transform: rotate(-90deg); }
.dmenu { position: absolute; left: 1em; right: 1em; top: calc(100% - .5em); z-index: 45; background: var(--bg2); border: 1px solid var(--line);
  box-shadow: 0 .5em 1.5em rgba(0, 0, 0, .6); display: flex; flex-direction: column; padding: .3em 0; }
.ditem { background: none; border: 0; color: var(--text); font: inherit; font-size: .9em; text-align: left; padding: .5em .9em; cursor: pointer; display: flex; gap: .5em; align-items: center; }
.ditem.on { color: var(--magenta); }
.ditem.danger { color: var(--red); }
.dsep { height: 1px; background: var(--line); margin: .3em 0; }
@media (hover: hover) { .ditem:hover { background: rgba(0, 240, 255, .08); } }
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
.toast { position: fixed; right: 16px; bottom: 1.2em; z-index: 50; max-width: calc(100vw - 32px); padding: .55em 1.1em; border-radius: .5em; background: var(--bg2); border: 1px solid currentColor; font-size: .88em; box-shadow: 0 .3em 1.2em rgba(0, 0, 0, .45); pointer-events: none; }
.toast.ok { color: var(--green); }
.toast.err { color: var(--red); }
.card.editing > h2::after { content: "· saved when you leave the field"; font-size: .78em; letter-spacing: .05em; color: var(--amber); text-shadow: none; }
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
.machine { padding-top: .6em; padding-bottom: .6em; }
.live-src { margin: .6em 0; }
.live-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: .3em; }
.meter { display: flex; align-items: flex-end; gap: 1px; height: 4.5em; border-bottom: 1px solid var(--line); }
.meter i { flex: 1; background: var(--dim); opacity: .45; min-width: 1px; transition: height .2s; }
.meter i.seg { background: var(--magenta); opacity: .8; }
.meter i.sp { background: var(--cyan); opacity: 1; box-shadow: 0 0 .3em var(--cyan); }
.mhead { display: flex; align-items: center; gap: .6em; cursor: pointer; user-select: none; flex-wrap: wrap; }
.mhead .fold { color: var(--dim); width: 1em; }
.mhead .mname { font-weight: bold; letter-spacing: .05em; }
.mhead .hint { margin: 0; flex: 1; }
.mbody { margin-top: .5em; border-top: 1px solid var(--line); padding-top: .2em; }
.mrow { flex-wrap: nowrap; }
.mrow > span:first-child { flex: 1; min-width: 0; }
.mrow .tag { font-size: .7em; }
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
.bubble .md { white-space: normal; }
.md > :first-child { margin-top: 0; }
.md > :last-child { margin-bottom: 0; }
.md p, .md ul, .md ol, .md pre, .md blockquote, .md .md-table { margin: .45em 0; }
.md ul, .md ol { padding-left: 1.3em; }
.md h3, .md h4, .md h5, .md h6 { margin: .7em 0 .3em; font-size: 1em; letter-spacing: .05em; }
.md h3 { font-size: 1.1em; }
.md code { font-family: ui-monospace, monospace; font-size: .9em; padding: .05em .3em; background: rgba(127, 127, 127, .15); border-radius: .25em; }
.md pre { padding: .6em .8em; overflow-x: auto; background: rgba(127, 127, 127, .12); border-radius: .3em; }
.md pre code { padding: 0; background: none; }
.md blockquote { border-left: 2px solid var(--line); padding-left: .8em; color: var(--dim); }
.md hr { border: 0; border-top: 1px solid var(--line); }
.md a { color: var(--cyan); }
.md-table { overflow-x: auto; }
.md table { border-collapse: collapse; font-size: .92em; }
.md th, .md td { border: 1px solid var(--line); padding: .25em .6em; text-align: left; vertical-align: top; }
.md td { min-width: 4.5em; }
.md th { white-space: nowrap; color: var(--dim); font-weight: normal; letter-spacing: .05em; }
.work { margin: 0 0 .5em; font-size: .78em; color: var(--dim); }
.work-head { cursor: pointer; letter-spacing: .1em; user-select: none; }
.work-head::before { content: "▸ "; }
.work.open .work-head::before { content: "▾ "; }
.work .row { white-space: pre-wrap; overflow-wrap: anywhere; opacity: .8; margin: .2em 0 0 .8em; }
.work:not(.open) .row { display: none; }
.work:not(.open) .row.fold { display: block; overflow: hidden; animation: fold 1.1s cubic-bezier(.5, 0, .2, 1) forwards; }
.work .row b { color: var(--cyan); font-weight: normal; }
.txt.said { margin-bottom: .5em; }
.runlog { min-height: 0; }
.runlog .bubble { max-width: 100%; }
.txt.pre { white-space: pre-wrap; font: inherit; font-size: .85em; margin: 0; max-height: 22em; overflow: auto; }
.why { margin-top: .6em; padding-top: .5em; border-top: 1px dashed var(--line); font-size: .85em; }
.why details { margin: .4em 0; }
.why summary { cursor: pointer; color: var(--cyan); letter-spacing: .08em; }
.why pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 28em; overflow: auto; font-size: .9em; margin: .3em 0 .6em; }
.why .wm { margin: .5em 0 0 .6em; }
.why .wm b { color: var(--magenta); font-weight: normal; letter-spacing: .1em; text-transform: uppercase; font-size: .85em; }
.prefwrap { position: relative; display: inline-flex; }
.chatprefs { left: auto; right: 0; top: 100%; width: 13em; padding: .3em .9em .8em; }
.chatprefs label { margin-top: .6em; }
.work .steprow { letter-spacing: .1em; opacity: .85; }
.work.thought .work-head { font-style: italic; }
.work .row.err b { color: var(--red); }
@keyframes fold { 0% { max-height: 14em; opacity: 1; } 35% { max-height: 14em; opacity: .9; } 100% { max-height: 0; opacity: 0; margin-top: 0; transform: translateY(-.8em); } }
.bubble .edited { font-size: .7em; color: var(--dim); margin-left: .5em; letter-spacing: .1em; }
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
.role { border-top: 1px solid rgba(0, 240, 255, .2); padding-top: .7em; margin-top: .7em; }
.role h3 { font-size: .95em; letter-spacing: .15em; color: var(--cyan); margin: 0 0 .2em; font-weight: 600; }
.buffer { max-height: 26em; overflow-y: auto; font-family: ui-monospace, monospace; font-size: .85em; padding: .3em 0; }
.buffer .bl { border-left: 2px solid var(--line); padding: .15em 0 .15em .7em; margin: .35em 0; }
.buffer .bl.to-aac { border-left-color: var(--magenta); }
.buffer .bl.live { border-left-color: var(--cyan); opacity: .85; }
.buffer .bl.sound { border-left-color: var(--amber); }
.buffer .bl.aac { border-left-color: var(--magenta); }
.buffer .bl.aac .bt { color: var(--magenta); }
.buffer .bm b.why { color: var(--dim); font-style: italic; }
.buffer .bl.sound .bt { color: var(--amber); }
.buffer .bl.live .bt { font-style: italic; }
.buffer .bm { font-size: .82em; color: var(--dim); }
.buffer .bm b { font-weight: normal; margin-left: .4em; }
.buffer .bm b.to { color: var(--magenta); }
.buffer .bm b.no { color: var(--amber); }
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
.fold > .ico { width: 1em; height: 1em; transition: transform .15s; }
.fold.open > .ico { transform: rotate(90deg); }
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

@media (prefers-reduced-motion: reduce) { .cursor, .dot.run { animation: none; } .work:not(.open) .row.fold { display: none; } nav { transition: none; } }
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
    // A module's menu: right-click opens it too; a click elsewhere or Escape closes it
    this.shadowRoot.addEventListener("contextmenu", (e) => {
      const wrap = e.target.closest?.("[data-mod-wrap]");
      if (!wrap) return;
      e.preventDefault();
      this._modMenu = wrap.dataset.modWrap;
      this._render();
    });
    this.shadowRoot.addEventListener("click", (e) => {
      if (this._modMenu && !e.target.closest?.(".modmenu, [data-mod-menu]")) { this._modMenu = null; this._render(); }
    });
    this.shadowRoot.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this._modMenu) { this._modMenu = null; this._render(); }
    });
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
        this._archOpen = localStorage.getItem("aac.archOpen") === "1";
        this._archHidden = localStorage.getItem("aac.archHidden") === "1";
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

  _ws(type, data = {}) { return this._hass.callWS({ type, domain: this._domainId(), ...data }); }

  _domainId() {
    if (this._domain === undefined) { try { this._domain = localStorage.getItem("aac-domain") || null; } catch (e) { this._domain = null; } }
    return this._domain;
  }

  _subscribe() {
    this._unsub = this._hass.connection.subscribeMessage((ev) => this._onUpdate(ev), { type: "aac/subscribe" });
    this._unsub.catch(() => {});
  }

  _onUpdate(ev) {
    if (ev.domain && ev.domain !== (this._state?.domain?.id || this._domainId())) return; // another domain
    if (ev.what === "run") {
      const run = ev.run;
      if (run.kind === "auto") {
        const i = this._runs.findIndex((r) => r.id === run.id);
        const summary = { ...run, steps: run.steps.length };
        if (i >= 0) this._runs[i] = summary; else this._runs.unshift(summary);
      }
      if (this._run && this._run.id === run.id) this._run = run;
      this._render();
    } else if (ev.what === "delta") {
      this._onDelta(ev);
    } else if (ev.what === "lists") {
      for (const [key, el] of Object.entries(this._modEls || {})) if (key.endsWith("|lists/lists") || key.endsWith("|calendar/agenda")) el.refresh?.();
    } else if (ev.what === "modules") {
      for (const [key, el] of Object.entries(this._modEls || {})) if (key.endsWith("|core/nodes")) el.refresh?.();
      this._loadState();
    } else if (ev.what === "requests" || ev.what === "entities") {
      this._loadState();
      if (this._view === "requests") this._loadRequests();
      if (this._view === "settings/entities" && ev.what === "entities") this._loadEntities();
    } else if (ev.what === "jobs") {
      clearTimeout(this._jobsTimer);
      this._jobsTimer = setTimeout(() => this._loadJobs(), 300);
    } else if (ev.what === "pools") {
      clearTimeout(this._poolTimer);
      this._poolTimer = setTimeout(async () => { await this._loadState(); if (!this._typing()) this._render(); }, 300);
    } else if (ev.what === "audio") {
      clearTimeout(this._audioTimer);
      this._audioTimer = setTimeout(async () => {
        await this._loadAudio();
        if (this._view.startsWith("settings/audio") && !this._typing()) this._render();
      }, 300);
    } else if (ev.what === "chat" || ev.what === "chats") {
      clearTimeout(this._chatTimer);
      this._chatTimer = setTimeout(async () => {
        await this._loadChats();
        if (this._chat?.id && (ev.what === "chats" || ev.chat_id === this._chat.id)) await this._openChat(this._chat.id, false);
      }, 150);
    }
  }

  async _loadAll() {
    await Promise.all([this._loadState(), this._loadModels(), this._loadAudio(), this._loadChats(), this._loadRuns(), this._loadJobs()]);
    if (!this._view) {
      if (this._setupOpen()) this._view = "setup";
      else if (this._chats.length) await this._openChat(this._chats[0].id, false);
      else this._view = "chat/new";
    }
    this._render();
  }
  // ---------------------------------------------------------------- entities and requests
  async _loadEntities() {
    try { this._ents = await this._ws("aac/entities"); } catch (e) { this._ents = { entities: [], error: e.message }; }
    this._render();
  }

  async _loadRequests() {
    try { this._reqs = (await this._ws("aac/requests")).requests; } catch (e) { this._reqs = []; }
    this._render();
  }

  _entityGroups(list) {
    const q = (this._entQuery || "").toLowerCase(), groups = {};
    for (const e of list) if (!q || `${e.name} ${e.entity_id} ${e.area || ""} ${e.integration || ""} ${e.integration_title || ""}`.toLowerCase().includes(q)) (groups[e.area || "No room"] ||= []).push(e);
    return groups;
  }

  _entitySearch() {
    return `<div class="row"><div><label for="ent-q">Search</label><input id="ent-q" value="${esc(this._entQuery || "")}" placeholder="name, room, entity id, integration"></div></div>`;
  }

  async _loadVisible() {
    try {
      const r = await this._ws("aac/domain/visible");
      this._vis = { entities: r.entities, chosen: r.chosen, picked: new Set(r.entities.filter((e) => e.visible).map((e) => e.entity_id)), dirty: false };
    } catch (e) { this._vis = { entities: [], picked: new Set(), error: e.message }; }
    this._render();
  }

  _renderVisible() {
    const v = this._vis, dom = this._state?.domain?.name || "";
    if (!v) return this._page("WHAT THIS DOMAIN CAN SEE", "", '<div class="card"><p>Loading…</p></div>');
    const groups = this._entityGroups(v.entities);
    return this._page(`WHAT "${dom.toUpperCase()}" CAN SEE`, `${v.picked.size} of ${v.entities.length} ticked`, `
      <div class="card">
        <p>Only ticked entities exist for "${esc(dom)}": its agent, its automatic runs and the modules in it. Whether it may read or switch them is set separately under SETTINGS → Entities. ${v.chosen ? "" : "Not chosen yet: the ticks show what you exposed to Assist in HA."}</p>
        ${this._entitySearch()}
        <div class="actions"><button class="btn" id="vis-all">Tick all</button><button class="btn" id="vis-none">Untick all</button></div>
      </div>
      ${Object.entries(groups).map(([area, list]) => {
        const on = list.filter((e) => v.picked.has(e.entity_id)).length;
        return `<div class="card"><h2>${esc(area.toUpperCase())} <span class="tag">${on}/${list.length}</span>
          <span class="actions" style="margin:0 0 0 auto"><button class="btn" data-vis-area="${esc(area)}" data-vis-to="1">all</button><button class="btn" data-vis-area="${esc(area)}" data-vis-to="0">none</button></span></h2>
          <div class="vis-grid">${list.map((e) => `<label class="check vis-item"><input type="checkbox" class="vis-tick" value="${esc(e.entity_id)}" ${v.picked.has(e.entity_id) ? "checked" : ""}>
            <span title="${esc(e.entity_id)}">${esc(e.name)}<span class="hint" style="display:block;margin:0">${esc(e.entity_id)}</span></span></label>`).join("")}</div></div>`;
      }).join("")}`);
  }

  _renderEntities() {
    const d = this._ents;
    if (!d) return this._page("ENTITIES", "", '<div class="card"><p>Loading…</p></div>');
    const groups = this._entityGroups(d.entities);
    const reads = d.entities.filter((e) => e.read).length, writes = d.entities.filter((e) => e.write).length;
    const tog = (e, what) => `<button class="lit ${e[what] ? "on" : ""}" data-acc="${esc(e.entity_id)}" data-what="${what}" aria-pressed="${!!e[what]}">${what === "read" ? "Read" : "Write"}</button>`;
    return this._page("ENTITIES", `${writes} write · ${reads} read · ${d.entities.length} in total`, `
      <div class="card"><h2>READ AND WRITE ${tip(`For every domain and module: <b>Read</b> = AAC may see the state, <b>Write</b> = it may also switch it (locks, alarms and doors only while you are in the chat). What a domain can see at all is its own tick list (domain menu → "What … can see"); both together decide. Home Assistant's Assist settings stay as they are. ${d.chosen ? "" : "Not chosen yet: this shows what you exposed to Assist in HA; your first change makes it AAC's own."}`)}</h2>
        ${this._entitySearch()}
      </div>
      ${(this._state?.master?.llm_hass_api || []).length ? `<div class="card"><h2>HOME ASSISTANT'S OWN TOOLS ARE ALSO ON</h2>
        <p>The master agent of "${esc(this._state?.domain?.name || "")}" also has Home Assistant's tools (${esc(this._state.master.llm_hass_api.join(", "))}). Those follow HA's own "expose to Assist" setting, not these lists. They also bring things like timers and HA's to-do lists.</p>
        <div class="actions"><button class="btn primary" id="ent-only-aac">Use only AAC's lists</button><button class="btn" data-go="nodes">Home Assistant tools card (Nodes)</button></div></div>` : ""}
      ${Object.entries(groups).map(([area, list]) => `<div class="card"><h2>${esc(area.toUpperCase())} <span class="tag">${list.length}</span>
          <span class="actions" style="margin:0 0 0 auto"><button class="btn" data-acc-area="${esc(area)}" data-acc-to="read">all read</button><button class="btn" data-acc-area="${esc(area)}" data-acc-to="write">all write</button><button class="btn" data-acc-area="${esc(area)}" data-acc-to="none">none</button></span></h2>
        <table class="ent-table"><tbody>${list.map((e) => `<tr><td>${esc(e.name)}<div class="hint" style="margin:0">${esc(e.entity_id)}</div></td><td class="hint">${esc(e.device_class || e.domain)}</td><td class="lits">${tog(e, "read")}${tog(e, "write")}</td></tr>`).join("")}</tbody></table>
      </div>`).join("")}`);
  }

  _renderRequests() {
    const reqs = this._reqs;
    if (!reqs) return this._page("REQUESTS", "", '<div class="card"><p>Loading…</p></div>');
    const open = reqs.filter((r) => r.status === "pending"), done = reqs.filter((r) => r.status !== "pending");
    const card = (r) => `<div class="card"><h2>${esc(r.title)}</h2>
        <p><b>Why:</b> ${esc(r.reason)}</p>
        ${r.items.map((i) => `<label class="check"><input type="checkbox" class="req-item" data-req="${esc(r.id)}" value="${esc(i.id)}" checked>${esc(i.label)} <span class="hint" style="margin:0">${esc(i.detail || "")}</span></label>`).join("")}
        <div class="actions"><button class="btn primary" data-req-ok="${esc(r.id)}">Allow selected</button><button class="btn danger" data-req-no="${esc(r.id)}">Decline</button></div>
        <p class="hint">${esc(fmtTime(r.created))}</p></div>`;
    return this._page("REQUESTS", `${open.length} open`, `
      <div class="card"><p>Things AAC may only do with your OK. It asked with a reason; allow all, some or none. It hears the outcome in "From AAC".</p></div>
      ${open.length ? open.map(card).join("") : '<div class="card"><p class="hint">Nothing waiting for you.</p></div>'}
      ${done.length ? `<div class="card"><h2>DECIDED</h2>${done.map((r) => `<div class="src"><span class="dot ${r.status === "rejected" ? "bad" : "ok"}"></span><div style="flex:1"><div class="n">${esc(r.title)}</div><div class="m">${esc(r.status)} · ${esc(fmtTime(r.decided))} · ${esc(r.reason)}</div></div></div>`).join("")}</div>` : ""}`);
  }

  // ---------------------------------------------------------------- dialogs
  // Own dialogs instead of the browser's ones: the HA app's web view swallows those silently.
  _ask(opts) {
    return new Promise((resolve) => {
      this._dialog = { ...opts, value: opts.input ?? null, resolve };
      this._render();
      setTimeout(() => (this.shadowRoot.querySelector("#dlg-input") || this.shadowRoot.querySelector("#dlg-ok"))?.focus(), 0);
    });
  }
  _prompt(text, value = "") { return this._ask({ text, input: value ?? "" }); }
  _confirm(text, ok = "OK", danger = false, cancel = "Cancel") { return this._ask({ text, ok, danger, cancel }); }

  _dialogHtml() {
    const d = this._dialog;
    if (!d) return "";
    const isPrompt = d.input !== undefined && d.input !== null;
    const multi = isPrompt && (String(d.input).length > 60 || String(d.input).includes("\n"));
    return `<div class="dlg-back"><div class="dlg card" role="dialog" aria-modal="true">
      <p style="white-space:pre-wrap">${esc(d.text)}</p>
      ${isPrompt ? (multi ? `<textarea id="dlg-input" class="thin-scroll">${esc(d.value)}</textarea>` : `<input id="dlg-input" value="${esc(d.value)}">`) : ""}
      <div class="actions"><button class="btn ${d.danger ? "danger" : "primary"}" id="dlg-ok">${esc(d.ok || "OK")}</button>
        <button class="btn" id="dlg-cancel">${esc(d.cancel || "Cancel")}</button></div>
    </div></div>`;
  }

  _closeDialog(ok) {
    const d = this._dialog;
    if (!d) return;
    this._dialog = null;
    const isPrompt = d.input !== undefined && d.input !== null;
    d.resolve(isPrompt ? (ok ? d.value : null) : ok);
    this._render();
  }

  // ---------------------------------------------------------------- skins
  _syncSkin() {
    if (this._skinCss === undefined) { // first render: last skin from this browser, so there is no neon flash
      try { const c = JSON.parse(localStorage.getItem("aac-skin") || "null"); this._skinId = c?.id; this._skinCss = c?.css || ""; }
      catch (e) { this._skinCss = ""; }
    }
    const want = this._state?.ui?.skin;
    if (!want || want === this._skinId || this._skinLoading === want) return;
    this._skinLoading = want;
    this._loadSkin(want);
  }

  async _loadSkin(id) {
    let css = "";
    try {
      if (id !== "neon") {
        this._skins = await this._ws("aac/skins");
        const skin = this._skins.skins.find((x) => x.id === id);
        if (skin?.url) css = await (await fetch(`${skin.url}?v=${Date.now()}`)).text();
      }
    } catch (e) { css = this._skinCss || ""; }
    this._skinId = id; this._skinCss = css; this._skinLoading = null;
    try { localStorage.setItem("aac-skin", JSON.stringify({ id, css })); } catch (e) { /* private mode */ }
    this._render();
  }

  async _loadJobs() {
    try { this._jobs = await this._ws("aac/jobs"); } catch (e) { this._jobs = null; }
    this._render();
  }

  async _loadState() {
    const before = this._state?.requests_open || 0;
    try { this._state = await this._ws("aac/state"); } catch (e) { this._state = { error: e.message || String(e) }; }
    if ((this._state?.requests_open || 0) > before) this._reqAnimAt = Date.now();
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
    try { this._voices = (await this._ws("aac/voices")).voices; } catch (e) { this._voices = null; }  // module off or missing
    this._render();
  }

  // The Voices module's voices: the owner can see, name and forget them
  _voicesCard() {
    if (!this._voices) return "";
    const kindText = { person: "person", media: "video/TV", unknown: "unknown" };
    const rows = this._voices.slice().sort((a, b) => (b.named - a.named) || (b.heard || 0) - (a.heard || 0)).map((v) => `
      <div class="wrow"><span>🗣 <b>${esc(v.name)}</b> <span class="tag">${esc(kindText[v.kind] || v.kind)}</span>
        <span class="hint">heard ${esc(v.heard || 0)}× · last ${esc(new Date(v.last).toLocaleString([], { hour12: false }))}${v.note ? ` · ${esc(v.note)}` : ""}${v.merged?.length ? ` · joined: ${esc(v.merged.join(", "))}` : ""}</span></span>
        <span class="actions"><button class="btn" data-voice-name="${esc(v.id)}">Name</button><button class="icon-btn" data-voice-forget="${esc(v.id)}" title="Forget this voice">${icon("trash")}</button></span></div>`).join("");
    return `<div class="card"><h2>VOICES ${tip(`The voices AAC tells apart (Voices module). A voice print cannot be split again: if two people got mixed into one, forget it and AAC learns them anew. Unnamed voices are forgotten on their own after a few days.`)}</h2>
      ${rows || '<p class="hint">No voices yet.</p>'}
      ${this._voices.length ? '<div class="actions"><button class="btn danger" id="voices-forget-all">Forget all voices</button></div>' : ""}</div>`;
  }

  async _voiceChange(data) {
    try { this._voices = (await this._ws("aac/voices/change", data)).voices; }
    catch (e) { this._flash = { ok: false, text: e.message || String(e) }; }
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
  async _openChat(id, navigate = true) {
    if (this._chat?.id !== id) { this._editing = null; this._editText = null; }
    try {
      this._chat = await this._ws("aac/chat/get", { chat_id: id });
      if (navigate || !this._view) this._view = `chat/${id}`;
    } catch (e) {
      this._chat = null;
      if (this._view === `chat/${id}`) { this._view = "chat/new"; this._chatGone = true; }  // say so instead of a silent empty chat
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
    if (view === "settings/triggers") view = "mod/triggers/list"; // old links: the triggers are a module since 0.45
    if (view === "settings/memory") view = "mod/memory/browse"; // and memory since 0.47
    if (view === "settings/charter") view = "settings/module/charter"; // and the charter since 0.48
    this._view = view;
    this._navOpen = false;
    this._flash = null;
    this._chatGone = false;
    if (view === "models/new") this._form = { name: "", base_url: "", api_key: "", model: "", models: [], msg: null };
    if (view.startsWith("model/edit/")) {
      const m = (this._models?.manual || []).find((x) => x.id === view.slice(11));
      this._form = m ? { id: m.id, name: m.name, base_url: m.base_url, api_key: "", has_key: m.has_key, model: m.model, models: [m.model], msg: null } : null;
    }
    if (view.startsWith("settings/audio")) this._loadAudio();
    if (view === "settings/audio" && !this._liveTimer) this._liveTick();
    if (view === "models") this._loadJobs();
    if (view === "settings/entities") this._loadEntities();
    if (view === "domain/visible") this._loadVisible();
    if (view === "requests") this._loadRequests();
    if (view === "settings/modules" || view.startsWith("settings/module/")) this._loadModuleSettings();
    if (view === "settings/languages") this._ws("aac/languages").then((r) => { this._langs = r; this._render(); }).catch(() => {});
    if (view === "settings/appearance") this._ws("aac/skins").then((r) => { this._skins = r; this._render(); }).catch(() => {});
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

    this._syncSkin();
    sr.innerHTML = `<style>${STYLE}</style><style>${this._skinCss || ""}</style>
      <div class="root ${this._navOpen ? "nav-open" : ""}">
        ${this._renderNav()}
        <div class="backdrop" id="backdrop"></div>
        ${this._dialogHtml()}
        <main>${this._renderMain()}</main>
        ${this._toastHtml()}
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
    this._mountModule();
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

    const live = this._chats.filter((c) => !c.archived), archived = this._chats.filter((c) => c.archived);
    const chats = live.length
      ? live.map((c) => this._tab(`chat/${c.id}`, c.title, c.id === "aac" ? icon("bolt") : "",
          "", `${c.unread ? `<span class="unread">${c.unread}</span>` : ""}<button class="icon-btn chatdel" data-chat-archive="${esc(c.id)}" title="Archive">${icon("trash")}</button>`)).join("")
      : '<div class="empty">no chats yet</div>';
    // The archive: chats put away (not deleted); its own eye hides the section, the eye at the top shows it again
    const archive = !archived.length || (this._archHidden && !this._showAll) ? "" : `
      <div class="tab fold ${this._archOpen ? "open" : ""} ${this._archHidden ? "hidden-mod" : ""}" id="arch-fold" tabindex="0" role="button" aria-expanded="${!!this._archOpen}">
        ${icon("chev")}<span class="grow">Archived<span class="count">(${archived.length})</span></span>
        <button class="icon-btn chatdel" id="arch-clear" title="Delete all archived chats permanently">${icon("trash")}</button>
        <button class="icon-btn chatdel" id="arch-hide" title="${this._archHidden ? "Show this section again" : "Hide this section (the eye at the top shows it)"}">${icon(this._archHidden ? "eye" : "eyeOff")}</button>
      </div>
      ${this._archOpen ? archived.map((c) => this._tab(`chat/${c.id}`, c.title, "", "deep",
        `<button class="icon-btn chatdel" data-chat-del="${esc(c.id)}" title="Delete permanently">${icon("trash")}</button>`)).join("") : ""}`;
    const running = this._runs.filter((r) => r.status === "running").length;
    const openRequests = this._state?.requests_open || 0;
    // Only there while something waits (or the eye shows everything); a new request drops in to be noticed
    const since = this._reqAnimAt ? Date.now() - this._reqAnimAt : 9999;
    const drop = since < 900 ? ` drop" style="animation-delay:-${since}ms` : "";
    const requestsTab = openRequests || this._showAll || this._view === "requests"
      ? this._tab("requests", "Requests", "", "", openRequests ? `<span class="unread${drop}">${openRequests}</span>` : "") : "";
    const autoList = !this._autoOpen ? "" : (this._runs.length
      ? this._runs.slice(0, 25).map((r) => this._tab(`run/${r.id}`, r.title, `<span class="dot ${STATUS_DOT[r.status] || ""}"></span>`, "deep",
          `<span class="time">${esc(fmtTime(r.started))}</span>`)).join("")
      : '<div class="empty" style="padding-left:4.4em">nothing yet</div>');

    return `<nav aria-label="AAC">
      <div class="brand">
        <span class="name">AAC</span><span class="ver">v${esc(this._state?.version || "")}</span><span class="sp"></span>
        <button class="icon-btn ${this._showAll ? "on" : ""}" id="eye" title="${this._showAll ? "Hide finished segments" : "Show all segments"}" aria-pressed="${this._showAll}">${icon(this._showAll ? "eye" : "eyeOff")}</button>
      </div>
      ${this._renderDomainSwitch()}
      <div class="scroll thin-scroll">
        ${setup}
        <div class="seg">
          <div class="seg-title">${icon("workers")}CHATS<span class="acts"><button class="icon-btn" data-go="chat/new" title="New chat">${icon("plus")}</button></span></div>
          <div class="seg-line"></div>
          ${chats}
          ${archive}
          ${requestsTab}
          <div class="tab fold ${this._autoOpen ? "open" : ""}" id="auto-fold" tabindex="0" role="button" aria-expanded="${this._autoOpen}">
            ${icon("chev")}<span class="grow">Auto<span class="count ${running ? "" : "zero"}">(${running})</span>${this._jobs?.waiting?.length ? `<span class="count" title="waiting for a model">+${this._jobs.waiting.length} waiting</span>` : ""}</span>
          </div>
          ${autoList}
        </div>
        ${(this._state?.modules || []).length ? `<div class="seg">
          <div class="seg-title">${icon("workers")}MODULES</div><div class="seg-line"></div>
          ${(this._state.modules).filter((m) => !m.hidden || this._showAll || this._modMenu === m.id).map((m) => {
            const more = `<button class="icon-btn modmore" data-mod-menu="${esc(m.id)}" aria-expanded="${this._modMenu === m.id}" title="${esc(m.name)}: more">⋯</button>`;
            const menu = this._modMenu !== m.id ? "" : `<div class="dmenu modmenu">
                <button class="ditem" data-mod-open="${esc(m.id)}">Properties, permissions, settings…</button>
                <button class="ditem" data-mod-hide="${esc(m.id)}" data-hidden="${m.hidden ? "0" : "1"}">${m.hidden ? "Show in this menu again" : "Hide from this menu"}</button>
              </div>`;
            const cls = m.hidden ? "hidden-mod" : "";
            const tabs = !m.pages.length ? this._tab(`settings/module/${m.id}`, m.name, "", `${m.enabled ? "" : "soon"} ${cls}`, more)
              : m.pages.map((p, i) => this._tab(`mod/${m.id}/${p.id}`, p.title, "", cls, i === 0 ? more : "")).join("");
            return `<div class="modwrap" data-mod-wrap="${esc(m.id)}">${tabs}${menu}</div>`;
          }).join("")}
          ${!this._showAll && this._state.modules.some((m) => m.hidden) ? `<div class="empty" title="The eye button at the top shows them">${this._state.modules.filter((m) => m.hidden).length} hidden</div>` : ""}
        </div>` : ""}
        <div class="seg">
          <div class="seg-title">${icon("settings")}SETTINGS</div><div class="seg-line"></div>
          ${this._tab("settings/appearance", "Appearance")}
          ${this._tab("settings/languages", "Languages", this._stepOpen("languages") ? '<span class="dot open"></span>' : "")}
          ${this._tab("settings/entities", "Entities", this._stepOpen("entities") ? '<span class="dot open"></span>' : "")}
          ${this._tab("models", "Compute", ["models", "master"].some((x) => this._stepOpen(x)) ? '<span class="dot open"></span>' : "")}
          ${this._tab("settings/audio", "Audio pipeline")}
          ${this._tab("settings/audio/sources", "Sources", "", "deep")}
          ${this._tab("settings/video", "Video")}
          ${this._tab("settings/modules", "Modules")}
          ${this._tab("nodes", "Nodes", "", "deep")}
          ${this._tab("settings/mcp", "MCP", "", "soon")}
        </div>
      </div>
    </nav>`;
  }

  _header(title, meta = "", actions = "") {
    const stale = PANEL_VERSION && this._state?.version && PANEL_VERSION !== this._state.version;
    return `${stale ? `<div class="update-banner">AAC was updated to v${esc(this._state.version)} (this page runs v${esc(PANEL_VERSION)}). <button class="btn primary" id="reload-page">Reload</button></div>` : ""}<header>
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
    if (v.startsWith("machine/")) return this._renderMachine(v.slice(8));
    if (v === "models/new" || v.startsWith("model/edit/")) return this._renderModelForm();
    if (v.startsWith("model/")) return this._renderModel(v.slice(6));
    if (v === "settings/master") { this._view = "nodes"; return this._renderMain(); } // old links: the agent's settings are on its cards now
    if (v === "settings/pools") return this._renderModels();  // old links: the thinking pool lives in Compute now
    if (v === "settings/video") return this._renderVideo();
    if (v === "settings/appearance") return this._renderAppearance();
    if (v.startsWith("mod/")) return this._renderModulePage(v);
    if (v === "nodes") return this._page("NODES", "how the parts are wired", '<div id="modhost"></div>');
    if (v === "settings/modules") return this._renderModules();
    if (v === "settings/entities") return this._renderEntities();
    if (v === "domain/visible") return this._renderVisible();
    if (v === "requests") return this._renderRequests();
    if (v.startsWith("settings/module/")) return this._renderModuleSettings(v.slice(16));
    if (v === "settings/languages") return this._renderLanguages();
    if (v === "settings/audio") return this._renderAudio();
    if (v === "settings/audio/sources") return this._renderSources();
    if (v === "settings/mcp") return this._renderSoon("MCP",
      "AAC as an MCP server (memory, modules and tools for other clients) and as an MCP client (plug in outside tools). Home Assistant's own AI features (Assist API, HA's MCP server) can be plugged in here as tools; AAC never changes HA's own voice or AI setup.");
    return this._page("AAC", "", '<div class="card"><p>Loading…<span class="cursor"></span></p></div>');
  }

  _page(title, meta, body, actions = "") {
    return `${this._header(title, meta, actions)}<div class="content thin-scroll"><div class="stack">${body}</div></div>`;
  }

  _renderSoon(title, text) {
    return this._page(title, "", `<div class="card"><h2>${esc(title)} <span class="tag plan">PLANNED</span></h2><p>${esc(text)}</p></div>`);
  }

  // ---------------------------------------------------------------- workers: chats + runs
  // What AAC did on the way (thinking text, tool calls): one folded block above the answer, click to open
  // Old runs stored the model's words and AAC's own notes both as "text"
  _stepKind(s) {
    if (s.type !== "text") return s.type;
    return /^(Context full|decision:)|unreachable, switched to /.test(s.text) ? "note" : "say";
  }

  // An answer as it happened: the model's words for the reader, its steps folded in between, its thinking
  // (if any) folded apart; then the final text
  _answerHtml(steps, finalHtml, key) {
    const parts = [];
    let work = null;
    for (const s of steps || []) {
      const k = this._stepKind(s);
      if (k === "say") { work = null; parts.push(`<div class="txt md said">${md(s.text)}</div>`); }
      else if (k === "thinking") { work = null; parts.push({ thought: s.text }); }
      else { if (!work) { work = []; parts.push(work); } work.push(s); }
    }
    return parts.map((p, i) => Array.isArray(p) ? this._stepsHtml(p, `${key}w${i}`)
      : p.thought !== undefined ? this._thoughtHtml(p.thought, `${key}t${i}`) : p).join("") + finalHtml;
  }

  _thoughtHtml(text, key) {
    const pref = this._chatPrefs().thinking;
    if (pref === "hidden") return "";
    const open = !!this._openWork?.has(key) !== (pref === "open");
    return `<div class="work thought ${open ? "open" : ""}" data-work="${esc(key)}"><div class="work-head" role="button" tabindex="0">💭 thought</div><div class="row">${esc(String(text).trim())}</div></div>`;
  }

  _stepRow(s) {
    const cut = (t) => (t = String(t ?? "").trim()).length > 600 ? `${t.slice(0, 600)} …` : t;
    const k = this._stepKind(s);
    return k === "say" || k === "thinking" || k === "note" ? `<div class="row">${k === "note" ? (/^Context full/.test(s.text) ? "⚠" : "ℹ") : k === "thinking" ? "💭" : "💬"} ${esc(cut(s.text))}</div>`
      : s.type === "tool_call" ? `<div class="row"><b>→ ${esc(s.tool.split("__").pop())}</b> ${esc(cut(JSON.stringify(s.args)))}</div>`
      : `<div class="row ${s.error ? "err" : ""}"><b>← ${esc(s.tool.split("__").pop())}</b> ${esc(cut(s.data))}</div>`;
  }

  _workHead(n, tools, full = false) {
    return `<div class="work-head" role="button" tabindex="0">⚙ ${n} step${n === 1 ? "" : "s"}${tools.length ? ` · ${esc([...new Set(tools)].join(", "))}` : ""}${full ? ' · <span style="color:var(--amber)">⚠ context full, older messages left out</span>' : ""}</div>`;
  }

  _stepsHtml(steps, key) {
    if (!steps || !steps.length) return "";
    const tools = steps.filter((s) => s.type === "tool_call").map((s) => s.tool.split("__").pop());
    const n = steps.filter((s) => s.type !== "tool_result").length;
    const full = steps.some((s) => this._stepKind(s) === "note" && /^Context full/.test(s.text));
    const open = !!this._openWork?.has(key) !== (this._chatPrefs().steps === "open");
    return `<div class="work ${open ? "open" : ""}" data-work="${esc(key)}">${this._workHead(n, tools, full)}${steps.map((s) => this._stepRow(s)).join("")}</div>`;
  }

  // ---------------------------------------------------------------- the chat view
  // Shared by chats and run details (later modules too). One message; opts:
  // editable (edit / rerun), details (a link to the run), key (unique per view, for the folds)
  // A model's traits (speed, size, tool mode …) from the pool status
  _traitsOf(id) {
    for (const r of Object.values(this._state?.pools?.roles || {})) {
      const it = (r.items || []).find((x) => x.id === id);
      if (it?.traits) return it.traits;
    }
    return null;
  }

  _bubbleHtml(m, i, msgs, opts = {}) {
    const key = `${opts.key || "m"}${i}`;
    const editing = opts.editable && this._editing === i;
    const prevUser = m.role === "aac" ? msgs.slice(0, i).map((x) => x.role).lastIndexOf("user") : -1;
    const editBtn = `<button class="icon-btn" data-msg-edit="${i}" title="${m.role === "user" ? "Edit and send again (the chat continues from here)" : "Change what AAC said (the model takes it as its own words)"}">✎ Edit</button>`;
    const why = m.role === "aac" && m.run_id ? `<button class="icon-btn" data-why="${esc(m.run_id)}" aria-expanded="${!!this._why?.[m.run_id]}" title="Everything this answer was based on: instructions, chat history, tools">🔍 What the model got</button>` : "";
    const own = !opts.editable || m.error ? "" : m.role === "user" ? editBtn
      : `${editBtn}${prevUser >= 0 ? `<button class="icon-btn" data-msg-rerun="${i}" title="Answer again (this answer is replaced)">↻ Rerun</button>` : ""}`;
    const acts = this._busy || editing || !(own || why) ? "" : `<div class="msg-acts">${own}${why}</div>`;
    const editHint = m.role === "user" ? "Everything after this message is removed; AAC answers from here."
      : "AAC takes the changed text as its own words from now on; the rest of the chat stays. Putting words in a model's mouth can make it act oddly, so keep it plausible.";
    return `<div class="bubble ${m.role} ${m.error ? "err" : ""}">
      ${m.trigger ? `<div class="trig">⚡ ${esc(m.trigger)}</div>` : `<div class="who">${m.role === "user" ? (m.who || "YOU") : "AAC"}${m.via === "voice" ? `<span class="edited" title="${m.role === "user" ? "spoken at" : "said aloud at"} ${esc(m.mic || "")}">🎤 ${esc((m.mic || "VOICE").toUpperCase())}</span>` : ""}${m.edited ? `<span class="edited">EDITED</span>` : ""}</div>`}
      ${m.role === "aac" && (editing || m.error) ? this._stepsHtml(m.steps, key) : ""}
      ${editing ? `<textarea id="msg-edit" class="thin-scroll">${esc(this._editText ?? m.text)}</textarea>
        <div class="actions" style="margin-top:.5em"><button class="btn primary" id="msg-edit-send">${m.role === "user" ? "Send" : "Save"}</button><button class="btn" id="msg-edit-cancel">Cancel</button></div>
        <div class="hint" style="margin:.3em 0 0">${editHint}</div>`
      : m.role === "aac" && !m.error ? this._answerHtml(m.steps, m.text ? `<div class="txt md">${md(m.text)}</div>` : "", key)
      : m.pre ? `<pre class="txt pre">${esc(m.text)}</pre>` : `<div class="txt">${esc(m.text)}</div>`}
      ${m.run_id && opts.details ? `<div class="steps"><span tabindex="0" role="button" data-go="run/${esc(m.run_id)}" style="cursor:pointer">details ›</span></div>` : ""}
      ${acts}
      ${m.run_id && this._why?.[m.run_id] ? this._whyHtml(this._why[m.run_id]) : ""}
    </div>`;
  }

  // What one answer was based on: the last request of its run (system prompt, messages, tools)
  _whyHtml(w) {
    if (w === "loading") return '<div class="why"><span class="hint">Loading…</span></div>';
    if (w.error) return `<div class="why"><span class="hint">${esc(w.error)}</span></div>`;
    const sec = (title, body, open = false) => `<details ${open ? "open" : ""}><summary>${title}</summary>${body}</details>`;
    const msgs = w.messages.map((m) => `<div class="wm"><b>${esc(m.role === "tool" ? "tool result" : m.role)}</b>${m.tool_calls ? ` → ${esc(m.tool_calls.map((c) => c.name.split("__").pop()).join(", "))}` : ""}
      ${m.content ? `<pre>${esc(m.content)}</pre>` : ""}</div>`).join("");
    return `<div class="why">
      <div class="hint">${esc(this._resourceLabel(w.model))} · about ${esc(w.tokens ?? "?")} tokens${w.n_ctx ? ` of ${esc(w.n_ctx)}` : ""}${w.left_out ? ` · ${esc(w.left_out)} older messages left out` : ""} · ${esc(fmtTime(w.at))}</div>
      ${sec(`Instructions <span class="tag">${esc(w.system.length)} chars</span>`, `<pre>${esc(w.system)}</pre>`)}
      ${sec(`Messages <span class="tag">${w.messages.length}</span>`, msgs)}
      ${sec(`Tools offered <span class="tag">${w.tools.length}</span>`, `<div class="hint">${w.tools.length ? esc(w.tools.map((t) => t.split("__").pop()).join(", ")) : "none"}</div>`)}
    </div>`;
  }

  async _toggleWhy(runId) {
    this._why = this._why || {};
    if (this._why[runId]) { delete this._why[runId]; return this._render(); }
    this._why[runId] = "loading";
    this._render();
    try { this._why[runId] = await this._ws("aac/run/request", { run_id: runId }); }
    catch (e) { this._why[runId] = { error: e.message || String(e) }; }
    this._render();
  }

  // How the chat view shows things (per browser): steps folded or open, thinking folded / open / hidden
  _chatPrefs() {
    if (!this._prefs) {
      try { this._prefs = JSON.parse(localStorage.getItem("aac.chatPrefs") || "{}"); } catch (e) { this._prefs = {}; }
    }
    return { steps: "folded", thinking: "folded", ...this._prefs };
  }

  _setChatPref(key, value) {
    this._prefs = { ...this._chatPrefs(), [key]: value };
    try { localStorage.setItem("aac.chatPrefs", JSON.stringify(this._prefs)); } catch (e) { /* private mode */ }
    this._openWork = new Set();
    this._render();
  }

  _chatPrefsHtml() {
    const p = this._chatPrefs();
    const sel = (key, opts) => `<select data-chat-pref="${key}">${opts.map(([v, l]) => `<option value="${v}" ${p[key] === v ? "selected" : ""}>${l}</option>`).join("")}</select>`;
    return `<div class="dmenu chatprefs"><label>Steps</label>${sel("steps", [["folded", "Folded"], ["open", "Open"]])}
      <label>Thinking</label>${sel("thinking", [["folded", "Folded"], ["open", "Open"], ["hidden", "Hidden"]])}</div>`;
  }

  _renderChat() {
    const master = this._poolItems("master")[0];
    const chat = this._view === "chat/new" && !this._busy ? null : this._chat;  // a new chat shows what is being sent
    const msgs = chat?.messages || [];
    const isAac = chat?.id === "aac";
    const log = msgs.length
      ? msgs.map((m, i) => this._bubbleHtml(m, i, msgs, { editable: !isAac, details: isAac, key: "m" })).join("")
      : `<div class="welcome"><div class="big">AAC</div>${this._chatGone ? "That chat no longer exists (it was deleted). This is a new chat."
          : !master ? "No master model yet. Open SETUP to choose one."
          : this._state?.pools?.roles?.master?.active ? "Ask me anything." : "No master model is reachable right now (see Compute)."}</div>`;
    const busy = this._busy ? `<div class="bubble aac busy"><div class="who">AAC</div>${this._streamHtml()}</div>` : "";
    const title = chat ? chat.title : "NEW CHAT";
    const del = !chat?.id ? "" : chat.archived
      ? `<button class="btn" id="chat-restore">Restore</button><button class="icon-btn" id="chat-delete" title="Delete permanently">${icon("trash")}</button>`
      : `<button class="icon-btn" id="chat-archive" title="Archive">${icon("trash")}</button>`;
    const prefs = `<span class="prefwrap"><button class="icon-btn" id="chat-prefs" title="How this view shows steps and thinking" aria-expanded="${!!this._prefsOpen}">${icon("settings")}</button>${this._prefsOpen ? this._chatPrefsHtml() : ""}</span>`;
    return `${this._header(title, `model: ${esc(this._modelLabel(master))}`, prefs + del)}
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
    const rows = [["Trigger", r.trigger], ["Started", fmtTime(r.started)], ["Finished", r.finished ? fmtTime(r.finished) : "—"],
      ["Kind", r.kind === "auto" ? "automatic" : "chat"], ["Model", r.model ? this._resourceLabel(r.model) : "—"]];
    // the run as a short conversation, in the same view as the chats
    const msgs = [
      { role: "user", who: r.kind === "auto" ? r.title.toUpperCase() : "YOU", text: r.input, pre: r.kind === "auto" },
      { role: "aac", text: r.status === "silent" ? `*Stayed silent* (answered: ${r.result || "NOTHING"})` : r.result || (r.status === "running" ? "working…" : "—"),
        steps: r.steps, run_id: r.request ? r.id : null, error: r.status === "failed" },
    ];
    return this._page(r.title, `<span class="status ${esc(r.status)}">${esc(r.status.toUpperCase())}</span>`, `
      <div class="card"><div class="log-inner runlog">${msgs.map((m, i) => this._bubbleHtml(m, i, msgs, { key: `r${r.id}` })).join("")}</div>
        ${r.request ? "" : '<p class="hint">What the model got is kept only for the newest runs (from v0.38 on).</p>'}</div>
      <div class="card"><h2>${icon("bolt")} RUN</h2>
        <div class="kv">${rows.map(([k, v]) => `<span class="k">${esc(k)}</span><span class="v">${esc(v)}</span>`).join("")}</div>
        ${r.context ? `<details class="steps"><summary>CONTEXT ADDED FOR THIS RUN</summary><pre>${esc(r.context)}</pre></details>` : ""}
        ${!r.chat_id ? "" : (this._chats || []).some((c) => c.id === r.chat_id)
          ? `<div class="actions"><button class="btn" data-go="chat/${esc(r.chat_id)}">Open chat</button></div>`
          : '<p class="hint">Its chat was deleted.</p>'}
      </div>`);
  }

  // ---------------------------------------------------------------- setup
  _renderSetup() {
    const steps = this._steps();
    const open = this._openSteps().length;
    return this._page("SETUP", open ? `${open} open` : "all done", `<div class="card"><h2>WORKFLOW ${tip(`${open ? "Work through the open steps. Each one opens the place where it is configured." : "Everything is set up. This segment hides itself now; the eye at the top shows it again."}`)}</h2>
        ${steps.map((s) => `
          <div class="step" data-go="${esc(s.target)}" tabindex="0" role="button">
            <span class="mark ${s.done ? "done" : s.skipped ? "skip" : "open"}">${s.done ? "✓" : s.skipped ? "–" : "›"}</span>
            <div style="flex:1"><div class="t">${esc(s.title)}${s.optional ? ' <span class="tag">OPTIONAL</span>' : ""}</div><div class="h">${esc(s.hint)}</div></div>
            ${s.optional && !s.done ? `<button class="btn" data-skip="${esc(s.id)}" data-skipped="${s.skipped ? "0" : "1"}">${s.skipped ? "Undo skip" : "Skip"}</button>` : ""}
          </div>`).join("")}
      </div>${open && steps.find((s) => s.id === "master")?.done ? `
      <div class="card"><h2>TOGETHER ${tip(`The master model is wired in, so AAC can walk you through the rest in a chat: it checks what is open, explains each step and asks before it changes anything.`)}</h2>
        <div class="actions"><button class="btn primary" id="setup-chat">Set up with AAC</button></div>
      </div>` : ""}`);
  }

  // ---------------------------------------------------------------- the queue (the broker's, shown on Compute)
  _renderQueueCard() {
    const q = this._jobs;
    if (!q) return "";
    const row = (j) => `<div class="src"><span class="dot ${j.status === "queued" ? "open" : j.status === "done" ? "ok" : j.status === "notified" ? "open" : ""}"></span>
      <div style="flex:1;min-width:0"><div class="n">${esc(j.title)} <span class="tag">${esc(j.priority.toUpperCase())}</span>${j.critical ? ' <span class="tag plan">TIME-CRITICAL</span>' : ""}</div>
      <div class="m">${esc(j.status)}${j.reason && j.status === "queued" ? ` · ${esc(j.reason)}` : ""} · since ${esc(fmtTime(j.created))}${j.due ? ` · due ${esc(fmtTime(j.due))}` : ""}</div></div></div>`;
    const recent = q.history.filter((j) => j.status !== "done").slice(0, 5);
    return `<div class="card" data-autosave="queue" data-label="Queue settings"><h2>QUEUE ${tip(`Automatic jobs wait here when no allowed model is free, most important first. Reminders and appointments are time-critical: if no model comes before they are due, the reminder reaches you anyway as a plain notification. Others expire. Your own chats never wait here.`)}</h2>
      <label class="check"><input type="checkbox" id="q-notify" ${q.settings.notify_no_model ? "checked" : ""}>Tell me when no model is available (once per outage)</label>
      <div class="row"><div><label for="q-postpone">Tell a job it was late after (minutes)</label><input id="q-postpone" type="number" min="0" max="1440" value="${esc(q.settings.postpone_notice)}" inputmode="numeric"></div></div>
      <h3 style="margin-top:1em">Waiting</h3>${q.waiting.length ? q.waiting.map(row).join("") : '<p class="hint">Nothing waits.</p>'}
      ${recent.length ? `<h3>Recently expired or sent without a model</h3>${recent.map(row).join("")}` : ""}
    </div>`;
  }

  // ---------------------------------------------------------------- settings: models
  // COMPUTE: the machines AAC can think with; unfold one for its models, ⚙ for its settings
  _modelRoles(id) {
    const roles = this._state?.pools?.roles || {};
    return Object.entries(roles).flatMap(([role, r]) => {
      const i = (r.items || []).findIndex((x) => x.id === id);
      return i < 0 ? [] : [`${(r.title || role).toUpperCase()}${r.items.length > 1 ? ` ${i + 1}` : ""}`];
    });
  }

  _renderModels() {
    const w = this._models;
    if (!w) return this._page("COMPUTE", "", '<div class="card"><h2>SCANNING</h2><p>Looking for model servers…<span class="cursor"></span></p></div>');
    const open = this._openMachines || new Set();
    const kindLabel = { chat: "chat", embedding: "embeddings", stt: "speech-to-text", speech: "speech-to-text" };
    const machines = (w.machines || []).map((mc) => {
      const isOpen = open.has(mc.host);
      const n = mc.models.length;
      const rows = mc.models.map((a) => {
        const roles = this._modelRoles(a.id);
        return `<div class="wrow mrow">
            <span><span class="dot ${a.online === false ? "bad" : "ok"}"></span> ${esc(a.name)}${a.manual && a.name !== a.model ? ` <span class="hint">· ${esc(a.model)}</span>` : ""}
              <span class="tag">${esc(kindLabel[a.kind] || a.kind)}</span>${roles.map((r) => ` <span class="tag aac">${esc(r)}</span>`).join("")}</span>
            <span class="actions"><button class="btn" data-go="model/${esc(a.id)}">Details</button></span>
          </div>`;
      }).join("");
      return `<div class="card machine ${isOpen ? "open" : ""}">
          <div class="mhead" role="button" tabindex="0" data-machine-toggle="${esc(mc.host)}">
            <span class="fold">${isOpen ? "▾" : "▸"}</span>
            <span class="dot ${mc.online ? "ok" : "bad"}"></span>
            <span class="mname">${esc(mc.name)}</span>
            <span class="hint">${mc.named ? `${esc(mc.host)} · ` : ""}${n} model${n === 1 ? "" : "s"}${mc.online ? "" : " · offline"}</span>
            <button class="icon-btn" data-go="machine/${esc(mc.host)}" title="Machine settings">${icon("settings")}</button>
          </div>
          ${isOpen ? `<div class="mbody">${rows || '<p class="hint">No models offered.</p>'}</div>` : ""}
        </div>`;
    }).join("");
    return this._page("COMPUTE", "the machines AAC thinks with", `
      ${machines || '<div class="card"><p>No machines yet. Add a model server: any OpenAI-compatible one (llama.cpp, Ollama, vLLM, LM Studio, cloud APIs).</p></div>'}
      <div class="actions"><button class="btn primary" data-go="models/new">${icon("plus")} Add model</button>${tip(`Found on their own: model servers set up in Home Assistant (only read, never changed) and everything the servers of your models offer. Speech and voice engines are ordered under Audio pipeline, vision under Video.`)}</div>
      ${this._poolCard("thinking")}
      ${this._renderQueueCard()}`,
      `<button class="icon-btn" id="refresh-models" title="Scan again">${icon("refresh")}</button>`);
  }

  _renderMachine(host) {
    const mc = (this._models?.machines || []).find((m) => m.host === host);
    if (!mc) return this._page("MACHINE", "", `<div class="card"><h2>NOT FOUND</h2><p>${this._models ? "No models on this machine any more." : "Scanning…"}</p></div>`);
    return this._page(mc.name, mc.named ? esc(mc.host) : "", `<div class="card" data-autosave="machine" data-arg="${esc(mc.host)}" data-label="Machine name"><h2>MACHINE</h2>
        <label for="mc-name">Name ${tip(`How AAC and this panel call it, e.g. "Main PC". Empty = the default.`)}</label><input id="mc-name" value="${esc(mc.named ? mc.name : "")}" placeholder="${esc(mc.name)}">
        <label>Servers on it</label>
        ${mc.servers.map((sv) => `<div class="wrow"><span><span class="dot ${sv.online ? "ok" : "bad"}"></span> ${esc(sv.url)}</span>${sv.error ? `<span class="msg err">${esc(sv.error)}</span>` : ""}</div>`).join("")}
        <div class="actions"><button class="btn" id="mc-add" data-url="${esc(mc.servers[0]?.url || "")}">${icon("plus")} Add model here</button>
          <button class="btn" data-go="models">Compute</button></div>
        ${this._flashHtml()}
      </div>
      <div class="card"><h2>LOADING <span class="tag plan">PLANNED</span></h2><p>Which models this machine can load, which one is loaded now, how long loading takes, and "free this machine" (e.g. when you start a game).</p></div>`);
  }

  _renderModel(id) {
    const w = this._models;
    const master = this._poolItems("master")[0];
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
    const mode = this._traitsOf(id)?.tool_mode || "menu";
    const toolCard = `<div class="card" data-autosave="toolmode" data-arg="${esc(id)}" data-label="Tool mode"><h2>TOOLS ${tip("Menu: the model first gets one line per area (home, memory, lists …) and opens what it needs; requests stay small and the server's cache keeps working. All: every tool up front (about 9,000 tokens), better for small models that get lost in the menu. Compare with 🔍 What the model got under an answer.")}</h2>
        <label for="tm-mode">Tool mode</label><select id="tm-mode">${[["menu", "Menu (areas, opened when needed)"], ["all", "All tools up front"]].map(([v, l]) => `<option value="${v}" ${mode === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>`;
    return this._page(manual ? manual.name : auto.model, id === master ? '<span class="tag aac">MASTER</span>' : "", `${toolCard}<div class="card"><h2>MODEL</h2>
        <div class="kv">${rows.map(([k, v]) => `<span class="k">${esc(k)}</span><span class="v">${esc(v)}</span>`).join("")}</div>
        ${err ? `<div class="msg err">${esc(err)}</div>` : ""}
        <div class="actions">
          ${id === master ? "" : `<button class="btn primary" data-master="${esc(id)}">Use as master</button>`}
          ${manual ? `<button class="btn" data-go="model/edit/${esc(id)}">Edit</button><button class="btn danger" data-delete="${esc(id)}">Delete</button>` : ""}
          <button class="btn" data-go="models">Compute</button>
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
    return this._page(f.id ? "EDIT MODEL" : "ADD MODEL", "", `<div class="card"><h2>${f.id ? "EDIT" : "ADD"} MANUAL MODEL ${tip(`Any OpenAI-compatible server. The base URL includes <code>/v1</code>, e.g. <code>http://192.168.1.20:8080/v1</code>.`)}</h2>
        <div class="form-grid">
          <div><label for="f-name">Name</label><input id="f-name" value="${esc(f.name)}" placeholder="e.g. Server GPU"></div>
          <div><label for="f-url">Base URL</label><input id="f-url" value="${esc(f.base_url)}" placeholder="http://host:8080/v1" inputmode="url"></div>
          <div><label for="f-key">API key</label><input id="f-key" type="password" value="${esc(f.api_key)}" placeholder="${f.has_key ? "unchanged (leave empty to keep)" : "optional"}" autocomplete="off"></div>
          <div><label for="f-model">Model</label>${models}</div>
        </div>
        ${f.msg ? `<div class="msg ${f.msg.ok ? "ok" : "err"}">${esc(f.msg.text)}</div>` : ""}
        <div class="actions">
          <button class="btn" id="f-test">Test &amp; load models</button>
          <button class="btn primary" id="f-save">${f.id ? "Save model" : "Add model"}</button>
          <button class="btn" data-go="${f.id ? `model/${esc(f.id)}` : "models"}">Cancel</button>
        </div>
      </div>`);
  }

  // The home's languages (global): from HA until the owner sets them; first = main
  _renderLanguages() {
    const L = this._langs;
    if (!L) return this._page("LANGUAGES", "", '<p class="hint">Loading…</p>');
    const name = (c) => L.names[c] || c, list = L.languages;
    const ha = `HA: language ${esc(L.ha.language || "–")}, country ${esc(L.ha.country || "–")} → ${esc(L.from_ha.map(name).join(", "))}`;
    const heard = Object.entries(L.seen).filter(([c]) => !list.includes(c) && L.names[c]).sort((a, b) => b[1] - a[1]);
    const rest = Object.keys(L.names).filter((c) => !list.includes(c)).sort((a, b) => name(a).localeCompare(name(b)));
    return this._page("LANGUAGES", L.set_by_owner ? "your list" : "from Home Assistant", `<div class="card"><h2>SPOKEN HERE ${tip(`One list for all of AAC, the main language first. Speech recognition and the voices only consider these, modules ask for them too, and a line in another language is the audio modules' business (e.g. Speech to text recognizes it again in a home language). Until you change it, AAC takes HA's language and country (English if HA says nothing).`)}</h2>
      ${list.map((c, i) => `<div class="row" style="display:flex;align-items:center;gap:.5em;padding:.35em 0">
        <span style="flex:1">${esc(name(c))} <span class="hint">${esc(c)}${i === 0 ? " · main" : ""}</span></span>
        <span style="display:flex;gap:.5em;justify-content:flex-end;align-items:center">
          ${i ? `<button class="btn" style="flex:none" data-lang-up="${esc(c)}" title="Make it the main language">↑ Main</button>` : ""}
          ${list.length > 1 ? `<button class="icon-btn" data-lang-remove="${esc(c)}" title="Remove">${icon("trash")}</button>` : ""}
        </span>
      </div>`).join("")}
      <label for="lang-new">Add a language</label>
      <div style="display:flex;gap:.5em"><select id="lang-new" style="flex:1">${rest.map((c) => `<option value="${esc(c)}">${esc(name(c))}</option>`).join("")}</select>
        <button class="btn" id="lang-add">Add</button></div>
      ${heard.length ? `<label>Heard here, not on the list ${tip(`What the recognizer thought it heard (lines, answers). A language heard only a few times is usually a mistake or a video.`)}</label>
        <div>${heard.map(([c, n]) => `<button class="btn" data-lang-add="${esc(c)}">+ ${esc(name(c))} <span class="hint">${n}×</span></button>`).join(" ")}</div>` : ""}
      <p class="hint" style="margin-top:1em">${ha}</p>
      <div class="actions">${L.set_by_owner ? '<button class="btn" id="lang-reset">Use HA\'s again</button>'
        : '<button class="btn" id="lang-keep" title="Keep this list as yours (closes the setup step)">This is right</button>'}</div>
    </div>`);
  }

  async _setLanguages(list) {
    try {
      this._langs = await this._ws("aac/languages/set", { languages: list });
      this._toast("✓ Languages saved", true);
      this._state = await this._ws("aac/state", {});  // the setup step closes
    } catch (e) { this._toast(`Languages: not saved: ${e.message || e}`, false); }
    this._render();
  }

  // ---------------------------------------------------------------- modules
  _modulePages() {
    return (this._state?.modules || []).flatMap((m) => (m.pages || []).map((p) => ({ ...p, module: m.id, moduleName: m.name })));
  }

  async _loadModuleSettings() {
    try { this._mods = (await this._ws("aac/modules")).modules; } catch (e) { this._mods = []; }
    this._render();
  }

  _settingField(m, key, f) {
    const v = m.values[key], id = `ms-${m.id}-${key}`;
    const help = f.help ? `<div class="hint">${esc(f.help)}</div>` : "";
    if (f.type === "bool") return `<label class="check"><input type="checkbox" data-ms="${esc(m.id)}" data-key="${esc(key)}" ${v ? "checked" : ""}>${esc(f.title || key)}</label>${help}`;
    if (f.type === "entities") {
      const opts = f.options || [];
      return `<label>${esc(f.title || key)}</label>${opts.length ? opts.map(([val, name]) => `<label class="check"><input type="checkbox" data-ms="${esc(m.id)}" data-key="${esc(key)}" data-multi="${esc(val)}" ${v === "all" || (v || []).includes(val) ? "checked" : ""}>${esc(name)}</label>`).join("") : '<p class="hint">None in Home Assistant yet.</p>'}${help}`;
    }
    if (f.type === "select" || f.type === "entity") {
      const opts = f.options || [];
      return `<label for="${id}">${esc(f.title || key)}</label><select id="${id}" data-ms="${esc(m.id)}" data-key="${esc(key)}">${opts.map(([val, name]) => `<option value="${esc(val)}" ${val === v ? "selected" : ""}>${esc(name)}</option>`).join("")}</select>${help}`;
    }
    const ph = f.placeholder ? ` placeholder="${esc(f.placeholder)}"` : "";
    if (f.type === "longtext") return `<label for="${id}">${esc(f.title || key)}</label><textarea id="${id}" class="thin-scroll" data-ms="${esc(m.id)}" data-key="${esc(key)}"${ph}${f.rows ? ` style="min-height:${Number(f.rows) * 1.4}em"` : ""}>${esc(v ?? "")}</textarea>${help}`;
    return `<label for="${id}">${esc(f.title || key)}</label><input id="${id}" data-ms="${esc(m.id)}" data-key="${esc(key)}" type="${f.type === "number" ? "number" : f.type === "time" ? "time" : "text"}" value="${esc(v ?? "")}"${ph}>${help}`;
  }

  _renderModules() {
    if (!this._mods) return this._page("MODULES", "", '<div class="card"><p>Loading…</p></div>');
    const field = (m, key, f) => this._settingField(m, key, f);
    return this._page("MODULES", `${this._mods.length} loaded · settings for ${esc(this._state?.domain?.name || "")}`, `
      <div class="cards">${this._mods.map((m) => `
        <div class="card" data-autosave="module" data-arg="${esc(m.id)}" data-label="${esc(m.name)}"><h2>${esc(m.name)} <span class="tag">${esc(m.version || "")}</span>${m.enabled ? "" : ' <span class="tag plan">OFF</span>'}</h2>
          <p>${esc(m.description)}</p>
          <div class="actions" style="margin-top:0"><button class="btn" data-go="settings/module/${esc(m.id)}">Properties, instructions, permissions</button></div>
          ${Object.entries(m.settings).map(([k, f]) => field(m, k, f)).join("") || '<p class="hint">No settings.</p>'}
        </div>`).join("")}</div>`);
  }

  _renderModuleSettings(id) {
    const m = (this._mods || []).find((x) => x.id === id);
    if (!m) return this._page("MODULE", "", `<div class="card"><p>${this._mods ? "This module is not loaded." : "Loading…"}</p></div>`);
    const a = m.access, provides = m.manifest.provides || {}, uses = m.manifest.uses || {};
    const list = (o) => Object.entries(o).map(([k, v]) => `<span class="k">${esc(k)}</span><span class="v">${esc(Array.isArray(v) ? v.join(", ") : typeof v === "object" ? Object.keys(v).join(", ") : v)}</span>`).join("");
    const settings = Object.entries(m.settings);
    return this._page(m.name.toUpperCase(), `module · settings for ${esc(this._state?.domain?.name || "")}`, `
      <div class="cards">
        <div class="card" data-autosave="access" data-arg="${esc(m.id)}" data-label="${esc(m.name)} properties"><h2>PROPERTIES</h2>
          <label for="ma-name">Name</label><input id="ma-name" value="${esc(a.name || "")}" placeholder="${esc(m.default_name)}">
          <p class="hint">Empty = "${esc(m.default_name)}". Shown in the sidebar for this domain.</p>
          <label class="check"><input type="checkbox" id="ma-shown" ${a.hidden ? "" : "checked"}>Show in the sidebar</label>
          <div class="kv"><span class="k">Id</span><span class="v">${esc(m.id)}</span><span class="k">Version</span><span class="v">${esc(m.version || "")}</span>
            <span class="k">Kind</span><span class="v">${esc(m.manifest.kind || "")}</span>${list(provides)}${Object.keys(uses).length ? list(uses) : ""}</div>
          <p style="margin-top:.8em">${esc(m.description)}</p>
        </div>
        <div class="card" data-autosave="access" data-arg="${esc(m.id)}" data-label="${esc(m.name)} permissions"><h2>PERMISSIONS</h2>
          <label class="check"><input type="checkbox" id="ma-enabled" ${a.enabled ? "checked" : ""}>On in this domain ${tip(`Off: no tools for AAC, no page, no calendar entries in "${esc(this._state?.domain?.name || "")}". Its data stays.`)}</label>
          ${m.has_tools ? `
          <label for="ma-access">What AAC may do with it</label>
          <select id="ma-access">${[["full", "Everything"], ["read", "Only read"], ["none", "Nothing"]].map(([v, l]) => `<option value="${v}" ${a.access === v ? "selected" : ""}>${l}</option>`).join("")}</select>
          <label class="check"><input type="checkbox" id="ma-auto" ${a.auto ? "checked" : ""}>Automatic runs (heartbeat, triggers) may use it</label>
          <label class="check"><input type="checkbox" id="ma-destr" ${a.destructive_needs_owner ? "checked" : ""}>Deleting only while I am in the chat</label>` : '<p class="hint">This module brings no tools for AAC.</p>'}
        </div>
      </div>
      ${m.has_tools ? `<div class="card" data-autosave="access" data-arg="${esc(m.id)}" data-label="${esc(m.name)} instructions"><h2>INSTRUCTIONS FOR AAC ${tip(`How AAC should use this module (sent along with its tools). Change it to your liking; empty or "Reset" = the module's own.`)}</h2>
        <textarea id="ma-instr" class="thin-scroll" placeholder="${esc(m.default_instructions)}">${esc(a.instructions || m.default_instructions)}</textarea>
        <div class="actions"><button class="btn" id="ma-reset">Reset to the module's instructions</button></div>
      </div>` : ""}
      ${settings.length ? `<div class="card" data-autosave="module" data-arg="${esc(m.id)}" data-label="${esc(m.name)} settings"><h2>SETTINGS</h2>${settings.map(([k, f]) => this._settingField(m, k, f)).join("")}</div>` : ""}
      <div class="card"><h2>MANIFEST</h2><details><summary class="hint" style="cursor:pointer">Show</summary><pre class="code">${esc(JSON.stringify(m.manifest, null, 2))}</pre></details></div>`);
  }

  async _saveModuleAccess(id) {
    const sr = this.shadowRoot, m = this._mods.find((x) => x.id === id);
    const instr = sr.querySelector("#ma-instr");
    const values = {
      name: sr.querySelector("#ma-name").value.trim() || null,
      enabled: sr.querySelector("#ma-enabled").checked,
      hidden: !sr.querySelector("#ma-shown").checked,
    };
    if (m.has_tools) {
      Object.assign(values, {
        access: sr.querySelector("#ma-access").value,
        auto: sr.querySelector("#ma-auto").checked,
        destructive_needs_owner: sr.querySelector("#ma-destr").checked,
        instructions: instr && instr.value.trim() !== (m.default_instructions || "").trim() ? instr.value : null,
      });
    }
    const updated = await this._ws("aac/module/access", { module: id, values });
    this._mods = this._mods.map((x) => (x.id === id ? updated : x));
    await this._loadState();
  }

  async _saveModule(id) {
    const sr = this.shadowRoot, m = this._mods.find((x) => x.id === id), values = {};
    for (const [key, f] of Object.entries(m.settings)) {
      const els = [...sr.querySelectorAll(`[data-ms="${CSS.escape(id)}"][data-key="${CSS.escape(key)}"]`)];
      if (f.type === "entities") {
        const picked = els.filter((e) => e.checked).map((e) => e.dataset.multi);
        values[key] = picked.length === els.length && els.length ? "all" : picked;
      } else if (f.type === "bool") values[key] = !!els[0]?.checked;
      else if (f.type === "number") values[key] = Number(els[0]?.value);
      else if (els[0]) values[key] = els[0].value;
    }
    const updated = await this._ws("aac/module/settings", { module: id, values });
    this._mods = this._mods.map((x) => (x.id === id ? updated : x));
    await this._loadState();
  }

  _renderModulePage(view) {
    const page = this._modulePages().find((p) => `mod/${p.module}/${p.id}` === view);
    if (!page) return this._page("MODULE", "", '<div class="card"><p>This module is not loaded.</p></div>');
    return this._page(page.title.toUpperCase(), esc(page.moduleName), '<div id="modhost"></div>');
  }

  // The module's element survives the panel's re-renders (it keeps its own state)
  async _mountModule() {
    const host = this.shadowRoot.querySelector("#modhost");
    if (!host) return;
    // The node view is the core's own page element, kept alive like a module's (its layout survives re-renders)
    const page = this._view === "nodes"
      ? { module: "core", id: "nodes", element: "aac-nodes", url: new URL(`aac-nodes.js${new URL(import.meta.url).search}`, import.meta.url).href }
      : this._modulePages().find((p) => `mod/${p.module}/${p.id}` === this._view);
    if (!page) return;
    const key = `${this._state?.domain?.id}|${page.module}/${page.id}`;
    this._modEls ||= {};
    if (!this._modEls[key]) {
      if (!customElements.get(page.element)) {
        try { await import(page.url); } catch (e) { host.innerHTML = `<div class="card"><p>Could not load the page: ${esc(e.message)}</p></div>`; return; }
      }
      const el = document.createElement(page.element);
      el.ws = (type, data = {}) => this._ws(type, data);
      el.panel = this;
      this._modEls[key] = el;
    }
    const el = this._modEls[key];
    el.hass = this._hass;
    if (el.parentNode !== host) host.appendChild(el);
  }

  // ---------------------------------------------------------------- domains
  _renderDomainSwitch() {
    const st = this._state;
    if (!st?.domain) return "";
    const voice = st.voice_domain;
    const menu = !this._domainMenu ? "" : `<div class="dmenu">
        ${st.domains.map((d) => `<button class="ditem ${d.id === st.domain.id ? "on" : ""}" data-domain-go="${esc(d.id)}">${esc(d.name)}${d.id === voice ? ' <span class="tag">VOICE</span>' : ""}</button>`).join("")}
        <div class="dsep"></div>
        <button class="ditem" id="dom-new">New domain…</button>
        <button class="ditem" id="dom-visible">What "${esc(st.domain.name)}" can see…</button>
        <button class="ditem" id="dom-rename">Rename "${esc(st.domain.name)}"…</button>
        ${st.domain.id === voice ? "" : `<button class="ditem" id="dom-voice">Let "${esc(st.domain.name)}" own the voice</button>`}
        ${st.domain.first ? "" : `<button class="ditem danger" id="dom-delete">Delete "${esc(st.domain.name)}"…</button>`}
      </div>`;
    return `<div class="dswitch"><button class="dbtn" id="dom-toggle" aria-expanded="${!!this._domainMenu}" title="Domain: a whole AAC setup with its own charter, memory, chats and triggers">
        <span class="grow">${esc(st.domain.name)}</span>${icon("chev", this._domainMenu ? "rot" : "")}</button>${menu}</div>`;
  }

  async _switchDomain(id) {
    this._domain = id;
    try { localStorage.setItem("aac-domain", id); } catch (e) { /* private mode */ }
    this._domainMenu = false;
    this._chat = null; this._run = null; this._modEls = {};
    this._view = "chat/new";
    await this._loadAll();
  }

  async _afterReload(domainId) {
    // AAC reloads after domain changes; wait until it answers again
    this._domainMenu = false;
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, 500));
      try { await this._hass.callWS({ type: "aac/state", domain: domainId }); break; } catch (e) { /* still reloading */ }
    }
    await this._switchDomain(domainId);
  }

  async _domainAction(kind) {
    const st = this._state;
    try {
      if (kind === "new") {
        const name = await this._prompt("Name of the new domain (e.g. Work):");
        if (!name?.trim()) return;
        const copy = await this._confirm(`Start with the settings of "${st.domain.name}" (charter, master, which modules are on and how they are set)? Its data (memory, lists, triggers, chats) is never copied.`, "Copy settings", false, "Start empty");
        const r = await this._ws("aac/domain/create", { name: name.trim(), copy_from: copy ? st.domain.id : null });
        await this._afterReload(r.domain);
      } else if (kind === "rename") {
        const name = await this._prompt("New name:", st.domain.name);
        if (!name?.trim() || name.trim() === st.domain.name) return;
        await this._ws("aac/domain/rename", { domain_id: st.domain.id, name: name.trim() });
        await this._afterReload(st.domain.id);
      } else if (kind === "delete") {
        if (!(await this._confirm(`Delete "${st.domain.name}" with its memory, chats, runs and triggers? This cannot be undone.`, "Delete", true))) return;
        await this._ws("aac/domain/delete", { domain_id: st.domain.id });
        await this._afterReload(st.domains[0].id);
      } else if (kind === "voice") {
        this._state = await this._ws("aac/domain/voice", { domain_id: st.domain.id });
        this._domainMenu = false;
        this._render();
      }
    } catch (e) { this._flash = { ok: false, text: e.message || String(e) }; this._render(); }
  }

  // ---------------------------------------------------------------- settings: appearance
  _renderAppearance() {
    const sk = this._skins;
    if (!sk) return this._page("APPEARANCE", "", '<div class="card"><p>Loading…</p></div>');
    const current = this._state?.ui?.skin || sk.skin;
    return this._page("APPEARANCE", "skin", `
      <div class="cards">${sk.skins.map((x) => `
        <div class="card"><h2>${esc(x.name)} ${x.id === current ? '<span class="tag ok">IN USE</span>' : ""} <span class="tag">${esc(x.source)}</span></h2>
          <p>${esc(x.description)}</p>
          ${x.author ? `<p class="hint">by ${esc(x.author)}</p>` : ""}
          <div class="actions">${x.id === current ? "" : `<button class="btn primary" data-skin="${esc(x.id)}">Use</button>`}</div>
        </div>`).join("")}
      </div>
      <div class="card"><h2>OWN SKINS ${tip(`A skin is one CSS file with a short header (name, description, author). Put it into <code>${esc(sk.folder)}</code> and it shows up here; a file with the same name as a built-in skin replaces it. Downloading skins from inside AAC comes later.`)}</h2>
      </div>`);
  }

  // ---------------------------------------------------------------- settings: pools
  _poolItems(role) { return (this._state?.pools?.roles?.[role]?.items || []).map((i) => i.id); }

  async _setPool(role, items) {
    const pools = await this._ws("aac/pool/set", { role, items });
    if (this._state) this._state.pools = pools;
    this._render();
  }

  _resourceLabel(id) {
    if (id.startsWith("stt.") || id.startsWith("tts.")) return this._hass?.states?.[id]?.attributes?.friendly_name || id;
    return this._modelLabel(id);
  }

  _roleSummary(role, now) {
    const r = this._state?.pools?.roles?.[role];
    const active = r?.active || now;
    const state = !r || !r.items.length ? (now ? `automatic: ${esc(this._resourceLabel(now))}` : "none")
      : `${esc(this._resourceLabel(active || r.items[0].id))}${r.fallback ? ' <span class="tag plan">FALLBACK</span>' : ""}${r.items.length > 1 ? ` <span class="hint">· ${r.items.length} in the pool</span>` : ""}`;
    const home = POOL_HOME[r?.pool || "voice"], here = home === this._view;
    return `<div class="src" style="margin:.2em 0 .6em"><div style="flex:1">${state}</div>${here ? "" : `<button class="btn" data-go="${home}">${POOL_HOME_NAME[home]}</button>`}</div>`;
  }

  _ruleText(rule) {
    return (rule.when || []).map((c) => c.type === "state" ? `${this._resourceLabel(c.entity_id)} is ${c.not ? "not " : ""}${[].concat(c.state).map((v) => v === "" ? "empty" : v).join(" or ")}`
      : `${c.after || "00:00"}–${c.before || "24:00"}${c.weekdays?.length ? " " + c.weekdays.map((d) => WEEKDAYS[d]).join(" ") : ""}`).join(" and ");
  }

  _tierOptions(chosen) {
    const p = this._state?.pools;
    const tiers = { ...(p?.tiers || { master: "", fast: "", smart: "", small: "" }) };
    for (const it of p?.roles?.master?.items || []) for (const t of it.traits?.tags || []) tiers[t] ||= "own tag";
    if (chosen && !(chosen in tiers)) tiers[chosen] = "";
    return Object.keys(tiers).map((t) => {
      const who = p?.by_tier?.[t] ? ` → now ${this._resourceLabel(p.by_tier[t])}` : "";
      return `<option value="${esc(t)}" ${t === chosen ? "selected" : ""}>${esc(t)}${esc(who)}</option>`;
    }).join("");
  }

  _roleOptions(role) {
    const take = this._state?.pools?.roles?.[role]?.takes;
    const have = new Set(this._poolItems(role));
    const models = this._allModels().filter((m) => !have.has(m.id));
    const kind = (m) => /senses/i.test(m.detail + " " + m.label) ? "senses"
      : /whisper|large-v\d|parakeet|canary|voxtral|moonshine|speech|stt|asr/i.test(m.detail + " " + m.label) ? "speech"
      : /embed|bge|e5|nomic|minilm|gte/i.test(m.detail + " " + m.label) ? "embedding" : "chat";
    const ents = (d) => Object.keys(this._hass?.states || {}).filter((e) => e.startsWith(d + ".") && !have.has(e)).sort();
    let opts = [];
    if (take === "tts") opts = ents("tts").map((e) => [e, this._resourceLabel(e)]);
    else {
      const fit = models.filter((m) => kind(m) === take), rest = models.filter((m) => kind(m) !== take);
      opts = [...fit, ...rest].map((m) => [m.id, `${m.label} · ${m.detail}${m.online ? "" : " (offline)"}`]);
      if (take === "speech") opts = [...opts, ...ents("stt").map((e) => [e, `HA: ${this._resourceLabel(e)}`])];
    }
    return opts.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
  }

  // A pool's roles in their order (each role uses the first entry that answers); shown on the page it belongs to
  _poolCard(name) {
    const p = this._state?.pools;
    if (!p) return '<div class="card"><p>Loading…</p></div>';
    const stateText = { ok: "ready", down: "not answering", missing: "gone", unused: "unused (one model only)" };
    const role = (id) => {
      const r = p.roles[id];
      const rows = r.items.map((it, i) => `
        <div class="src">
          <span class="dot ${it.state === "ok" ? "ok" : it.state === "down" ? "bad" : "open"}"></span>
          <div style="flex:1;min-width:0"><div class="n">${i + 1}. ${esc(this._resourceLabel(it.id))} ${it.active ? '<span class="tag ok">IN USE</span>' : ""}</div>
            <div class="m">${esc(stateText[it.state] || it.state)}${it.error ? ` · ${esc(it.error)}` : ""}${it.traits ? ` · ${it.traits.speed ? `${esc(it.traits.speed)} tok/s` : "speed unknown"}${it.traits.params_b ? ` · ${esc(it.traits.params_b)}B` : ""}${it.traits.n_ctx ? ` · ${esc(Math.round(it.traits.n_ctx / 1024))}k context` : ""}` : ""}</div>
            ${it.rule ? `<div class="m">rule: ${it.rule.note ? `${esc(it.rule.note)} (${esc(this._ruleText(it.rule))})` : esc(this._ruleText(it.rule))} · ${it.rule.applies_to === "all" ? "everything" : "background jobs"} · ${it.background_now ? "allowed now" : "not now"} <button class="btn" style="padding:.1em .6em" data-pool-rule-clear="${esc(it.id)}">Remove rule</button></div>` : ""}
            ${it.tags?.length ? `<div class="m">${it.tags.map((t) => `<span class="tag ${["fast", "smart", "small"].includes(t) ? "aac" : "ok"}">${esc(t.toUpperCase())}</span>`).join(" ")}</div>` : ""}</div>
          <span class="actions" style="margin:0">
            ${i > 0 ? `<button class="icon-btn" data-pool-up="${esc(id)}|${i}" title="Up">↑</button>` : ""}
            ${i < r.items.length - 1 ? `<button class="icon-btn" data-pool-down="${esc(id)}|${i}" title="Down">↓</button>` : ""}
            ${it.traits ? `<button class="btn" data-pool-tags="${esc(it.id)}" title="Your own tags, e.g. efficient">Tags</button>` : ""}
            <button class="icon-btn" data-pool-del="${esc(id)}|${i}" title="Remove">${icon("trash")}</button>
          </span>
        </div>`).join("");
      const opts = this._roleOptions(id);
      return `<div class="role"><h3>${esc(r.title)}</h3><p class="hint">${esc(r.help)}</p>
        ${rows || '<p class="hint">Empty.</p>'}
        ${opts && !(r.single && r.items.length) ? `<div class="row" style="margin-top:.4em"><div style="flex:1"><select data-pool-pick="${esc(id)}">${opts}</select></div><div style="flex:none"><button class="btn" data-pool-add="${esc(id)}">Add</button></div></div>` : ""}
      </div>`;
    };
    const pool = (name) => {
      const roles = Object.keys(p.roles).filter((r) => p.roles[r].pool === name);
      const extra = name !== "thinking" ? "" : `<div class="role"><h3>Requests ${tip(`Rules for when a model may be used (e.g. "my PC only while I am at work") are yours: tell AAC in a chat and it writes them down. Events and agents can ask for a kind of model instead of a fixed one, chosen among the master's models that answer right now. fast = measured speed, smart = biggest, small = smallest, plus your own tags (e.g. efficient for energy-saving hardware).`)}</h3>
        <div class="kv">${Object.entries(p.by_tier || {}).map(([t, r]) => `<span class="k">${esc(t)}</span><span class="v">${r ? esc(this._resourceLabel(r)) : "—"}</span>`).join("")}</div>
        <div class="actions"><button class="btn" id="pool-measure">Measure speed</button></div></div>`;
      return `<div class="card"><h2>${esc(name.toUpperCase())} POOL</h2><p>${esc(p.pools[name])}. Each role uses the first entry that answers: put a fast machine that is not always on (a PC) first and an always-on one after it; when the first does not answer, the next takes over and the first is tried again a minute later.</p>${roles.map(role).join("") || '<p class="hint">Nothing here yet.</p>'}${extra}</div>`;
    };
    return pool(name);
  }

  _renderVideo() {
    return this._page("VIDEO", "cameras and what AAC can see", `${this._poolCard("vision")}
      <div class="card"><h2>CAMERAS <span class="tag plan">PLANNED</span></h2><p>Camera sources and detectors, like the audio pipeline: light per source, the heavy models shared from the vision pool.</p></div>`);
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
    // in the order the lines ENDED (= roughly when they appeared): a long sentence does not jump above what came in meanwhile
    const end = (l) => new Date(l.time).getTime() + (l.seconds || 0) * 1000;
    const lines = (a.transcript || []).slice().sort((x, y) => end(x) - end(y))
      .map((l) => this._bufferLine(l)).join("");
    return this._page("AUDIO PIPELINE", `${(a.connected || []).length} connected · separate from HA's Assist`, `
      ${this._liveTimer ? "" : (setTimeout(() => this._liveTimer || this._liveTick(), 0), "")}<div class="card"><h2>LIVE ${tip(`What each microphone hears right now: the last 10 seconds of loudness; the bright part is what the speech detector cut out as speech, which then goes to speech to text.`)}</h2>
        <div id="audio-live">${this._liveHtml2()}</div>
        <label style="margin-top:1em">Buffer ${tip(`Everything heard (RAM only, the last lines), oldest at the top. While someone talks, the words appear live and get corrected; the finished line is handed to the hearing modules that are on. Per line: start · length · language · loudness · how sure the recognizer was · which engine and how fast.`)}</label>
        <div class="buffer thin-scroll" id="audio-buffer">${lines || '<p class="hint">Nothing heard yet.</p>'}<div id="audio-partial">${this._partialHtml()}</div></div>
      </div>
      ${this._voicesCard()}
      <div class="split">
        <div class="card"><h2>${icon("mic")} CONNECTED</h2>
          ${this._liveSources(a.connected || [])}
          <div class="actions"><button class="btn" data-go="settings/audio/sources">All sources</button></div>
        </div>
      </div>
      <div class="split">
        <div class="card" data-autosave="audio" data-label="Audio settings"><h2>SETTINGS</h2>
          <label for="au-names">AAC's names (comma separated)</label><input id="au-names" value="${esc((set.names || []).join(", "))}">
          <p class="hint">Languages: ${esc((a.languages || []).map((c) => (eng.language_names || {})[c] || c).join(", "))} · <button class="btn" data-go="settings/languages">Change</button> ${tip(`AAC always hears and transcribes; what it does with it is up to the hearing modules (SETTINGS → Modules: Wake word, …). ${(a.hearing || []).length ? `On now: ${esc(a.hearing.join(", "))}.` : "None is on: AAC only listens."}`)}</p>
          <label for="au-pause">Pause that ends a line (ms) ${tip(`Shorter = shorter lines (snappier, but the recognizer gets less context); longer = whole thoughts. Watch the LIVE meter while you try.`)}</label>
          <input id="au-pause" type="number" min="200" max="3000" step="100" value="${esc(set.pause_ms ?? 800)}" inputmode="numeric">
          <label for="au-volume">AAC's voice volume: <span id="au-volume-val">${esc(set.volume ?? 40)}</span> % ${tip(`Saved when you let go, with a short test sentence. 0 = AAC never speaks aloud (answers still appear in the Voice chat). The speaker's own buttons still work on top.`)}</label>
          <input id="au-volume" data-nosave type="range" min="0" max="100" step="5" value="${esc(set.volume ?? 40)}" style="width:100%">
          <label>Speech to text</label>${this._roleSummary("stt", eng.stt)}
          <label>Voice (text to speech) ${tip(`Engines come from the voice pool below: speech models from Compute (e.g. a multilingual whisper; without a language it detects German, English, … per sentence) or HA's engines, in your order, with fallback. HA's own voice assistants stay as they are.`)}</label>${this._roleSummary("tts", eng.tts)}
          ${this._voicePicks(set, eng)}
          ${this._flashHtml()}
        </div>
        <div class="card"><h2>PIPELINE ${tip(`Every source gets its own light pipeline; the heavy parts are shared.`)}</h2>
          <div class="flow">${flow}</div>
        </div>
      </div>
      ${this._poolCard("voice")}`);
  }

  // A voice per language: the languages heard or spoken here (most first) and those with a voice are shown;
  // every other language the engine has stays folded away
  _voicePicks(set, eng) {
    const avail = eng.tts_voices || {}, names = eng.language_names || {}, chosen = set.voices || {};
    const seen = set.languages_seen || {};
    const shown = [...new Set([...(this._audio?.languages || []), ...Object.keys(chosen)])].filter((l) => avail[l]);
    const rest = Object.keys(avail).filter((l) => !shown.includes(l)).sort((a, b) => (names[a] || a).localeCompare(names[b] || b));
    const pick = (lang) => {
      const cur = chosen[lang];
      return `<label for="au-voice-${lang}">${esc(names[lang] || lang)}${seen[lang] ? ` <span class="hint">· heard or spoken ${seen[lang]}×</span>` : ""}</label>
        <select id="au-voice-${lang}" data-voice-lang="${lang}"><option value="">(engine's default)</option>${avail[lang].map((v) => {
          const val = `${v.language}|${v.voice}`;
          return `<option value="${esc(val)}" ${cur && `${cur.language}|${cur.voice}` === val ? "selected" : ""}>${esc(v.name || v.voice)}</option>`;
        }).join("")}</select>`;
    };
    return `<label>Voice per language ${tip(`AAC detects the language of each answer among the home's languages (SETTINGS → Languages) and reads it with the voice you pick for it. The rest is folded away. Empty = the engine's default voice.`)}</label>
      ${shown.map(pick).join("") || '<p class="hint">The voice engine has none of the home\'s languages.</p>'}
      ${rest.length ? `<details><summary class="hint" style="cursor:pointer">All languages (${rest.length})</summary>${rest.map(pick).join("")}</details>` : ""}`;
  }

  // The live view of the audio page (refreshed a few times a second while it is open)
  _liveHtml2() {
    const list = this._audioLive || [];
    if (!list.length) return '<p class="hint">No source connected.</p>';
    return list.map((src) => {
      const pts = src.meter || [];
      const pad = Array(Math.max(0, 100 - pts.length)).fill([0, 0, false]);
      const bars = [...pad, ...pts].map(([lvl, sp, seg]) =>
        `<i class="${seg ? (sp > 0.3 ? "sp" : "seg") : ""}" style="height:${Math.max(2, lvl)}%"></i>`).join("");
      return `<div class="live-src"><div class="live-head"><b>${esc(src.name)}</b><span class="tag ${src.state === "listening" ? "" : "ok"}">${esc(src.state.toUpperCase())}</span></div>
        <div class="meter">${bars}</div></div>`;
    }).join("");
  }

  _bufferLine(l) {
    const t = new Date(l.time);
    const hms = `${t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })}.${String(Math.floor(t.getMilliseconds() / 100))}`;
    const meta = [hms, l.seconds != null ? `${l.seconds} s` : null, l.language, l.loudness_db != null ? `${l.loudness_db} dB` : null,
      l.highs != null ? (l.highs < 0.3 ? "phone quality (≤ 4 kHz)" : `highs ${l.highs} %`) : null,
      l.confidence != null ? `${Math.round(l.confidence * 100)} % sure` : null,
      l.engine ? `${this._resourceLabel(l.engine)}${l.stt_ms != null ? ` ${l.stt_ms} ms` : ""}` : null].filter(Boolean);
    if (l.speaker) meta.push(`🗣 ${l.speaker.sure === false ? "probably " : ""}${l.speaker.name}${l.speaker.new ? " (new)" : ""}${l.speaker.kind === "media" ? " · video/TV" : ""}${l.speaker.score > 0 ? ` ${Math.round(l.speaker.score * 100)} %` : ""}`);
    if (l.sounds?.length) meta.push(`♪ ${l.sounds.slice(0, 3).map(([n, p]) => `${n} ${Math.round(p * 100)} %`).join(", ")}`);
    const mark = (l.addressed ? '<b class="to">→ AAC</b>' : l.gate ? `<b class="no">${esc(l.gate)}</b>` : "")
      + (l.judged ? ` <b class="why">${esc(l.judged)}</b>` : "");
    if (l.kind === "aac") return `<div class="bl aac"><div class="bm">${esc(meta[0])} · AAC said</div><div class="bt">${esc(l.text)}</div></div>`;
    const text = l.kind === "sound" ? `<i>${esc(l.text)}</i>` : l.text ? esc(l.text) : '<i class="dim">[speech, not transcribed]</i>';
    return `<div class="bl ${l.addressed ? "to-aac" : ""} ${l.kind === "sound" ? "sound" : ""}"><div class="bm">${esc(meta.join(" · "))} ${mark}</div><div class="bt">${text}</div></div>`;
  }

  _partialHtml() {
    return (this._audioLive || []).filter((x) => x.state !== "listening" || x.partial).map((x) => `
      <div class="bl live"><div class="bm">${esc(x.name)} · ${esc(x.state)}${x.speech_seconds ? ` · ${esc(x.speech_seconds)} s` : ""}</div>
        <div class="bt">${esc(x.partial || "")}<span class="cursor"></span></div></div>`).join("");
  }

  _liveTick() {
    clearTimeout(this._liveTimer);
    this._liveTimer = null;
    if (this._view !== "settings/audio" || !this.isConnected) return;
    this._liveTimer = setTimeout(() => this._liveTick(), 250);
    if (document.hidden) return;
    this._ws("aac/audio/live").then((r) => {
      this._audioLive = r.sources;
      const box = this.shadowRoot.querySelector("#audio-live");
      if (box) box.innerHTML = this._liveHtml2();
      const part = this.shadowRoot.querySelector("#audio-partial"), buf = this.shadowRoot.querySelector("#audio-buffer");
      if (part && buf) {
        const follow = buf.scrollHeight - buf.scrollTop - buf.clientHeight < 60;  // at the bottom: keep following
        part.innerHTML = this._partialHtml();
        if (follow) buf.scrollTop = buf.scrollHeight;
      }
    }).catch(() => {});
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
        <div class="card"><h2>AAC SATELLITES ${tip(`A satellite streams one microphone to AAC and plays its answers. The same program runs on a PC or next to a USB/Bluetooth headset.`)}</h2>
          ${this._liveSources(a.connected || [])}
          ${known.map((k) => `<div class="src">${icon("mic")}<div style="flex:1"><div class="n">${esc(k.name)}</div><div class="m">seen before</div></div><span class="tag">OFFLINE</span></div>`).join("")}
        </div>
        <div class="card"><h2>HOME ASSISTANT APP ${tip(`For a microphone/speaker attached to the Home Assistant machine itself (USB, or Bluetooth through a dongle): install the app "AAC satellite" (in this project's <code>addons/</code> folder) and let AAC give it access. AAC makes a local-only HA user "AAC satellite" with a token the app reads from <code>config/aac/satellite.json</code>; you can revoke it in HA's user list.`)}</h2>
          <div class="actions"><button class="btn primary" id="sat-app">Set up the Home Assistant app</button></div>
          ${this._flashHtml()}
        </div>
        <div class="card"><h2>FROM HOME ASSISTANT ${tip(`Devices with a mic that HA knows. Using them as sources is planned; for now they are only listed.`)}</h2>
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

  // One save per settings card (data-autosave): coalesced, one at a time, then a short note at the bottom
  _autosave(key, arg, label) {
    const id = `${key}|${arg || ""}`;
    this._saveTimers = this._saveTimers || {};
    clearTimeout(this._saveTimers[id]);
    this._saveTimers[id] = setTimeout(async () => {
      this._saving = (this._saving || Promise.resolve()).then(async () => {
        try {
          await this._saveCard(key, arg);
          this._toast(`✓ ${label || "Settings"} saved`, true);
        } catch (e) { this._toast(`${label || "Settings"}: not saved: ${e.message || e}`, false); }
        this._render();
      });
    }, key === "visible" ? 600 : 150);
  }

  async _saveCard(key, arg) {
    const sr = this.shadowRoot;
    if (key === "toolmode") { this._state.pools = await this._ws("aac/pool/tool_mode", { resource: arg, mode: sr.querySelector("#tm-mode").value }); return; }
    if (key === "audio") return this._saveAudio();
    if (key === "module") return this._saveModule(arg);
    if (key === "access") return this._saveModuleAccess(arg);
    if (key === "queue") {
      this._jobs = await this._ws("aac/jobs/settings", {
        notify_no_model: sr.querySelector("#q-notify").checked,
        postpone_notice: Number(sr.querySelector("#q-postpone").value) || 0,
      });
      return;
    }
    if (key === "machine") {
      await this._ws("aac/machine/set", { host: arg, name: sr.querySelector("#mc-name").value });
      await this._loadModels();
      return;
    }
    if (key === "visible") {
      const r = await this._ws("aac/domain/visible/set", { visible: [...this._vis.picked] });
      this._vis = { entities: r.entities, chosen: true, picked: new Set(r.entities.filter((e) => e.visible).map((e) => e.entity_id)) };
      this._loadState();
      return;
    }
    throw new Error(`unknown settings card ${key}`);
  }

  // A short note at the bottom of the screen, outside the page (survives re-renders)
  _toast(text, ok) {
    this._toastMsg = { text, ok };
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => { this._toastMsg = null; this.shadowRoot.querySelector(".toast")?.remove(); }, ok ? 2200 : 8000);
    this.shadowRoot.querySelector(".toast")?.remove();
    this.shadowRoot.querySelector(".root")?.insertAdjacentHTML("beforeend", this._toastHtml());
  }

  _toastHtml() {
    return this._toastMsg ? `<div class="toast ${this._toastMsg.ok ? "ok" : "err"}" role="status">${esc(this._toastMsg.text)}</div>` : "";
  }

  _savePref(key, on) {
    try { localStorage.setItem(key, on ? "1" : "0"); } catch (e) { /* private mode */ }
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
    const langs = () => this._langs?.languages || [];
    sr.querySelectorAll("[data-lang-up]").forEach((el) => onActivate(el, () =>
      this._setLanguages([el.dataset.langUp, ...langs().filter((c) => c !== el.dataset.langUp)])));
    sr.querySelectorAll("[data-lang-remove]").forEach((el) => onActivate(el, () =>
      this._setLanguages(langs().filter((c) => c !== el.dataset.langRemove))));
    sr.querySelectorAll("[data-lang-add]").forEach((el) => onActivate(el, () => this._setLanguages([...langs(), el.dataset.langAdd])));
    sr.querySelector("#lang-add")?.addEventListener("click", () => this._setLanguages([...langs(), sr.querySelector("#lang-new").value]));
    sr.querySelector("#lang-reset")?.addEventListener("click", () => this._setLanguages(null));
    sr.querySelector("#lang-keep")?.addEventListener("click", () => this._setLanguages(langs()));
    const fold = sr.querySelector("#auto-fold");
    if (fold) onActivate(fold, () => {
      this._autoOpen = !this._autoOpen;
      try { localStorage.setItem("aac.autoOpen", this._autoOpen ? "1" : "0"); } catch (e) { /* ignore */ }
      this._render();
    });
    sr.querySelector("#dom-toggle")?.addEventListener("click", () => { this._domainMenu = !this._domainMenu; this._render(); });
    sr.querySelectorAll(".qm").forEach((el) => {
      el.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); el.focus(); });
      // keep the tip inside the page (not under the sidebar or off the right edge)
      const place = () => {
        const t = el.querySelector(".tip"), box = sr.querySelector(".content")?.getBoundingClientRect();
        if (!t || !box || innerWidth <= 700) return;
        t.style.left = ""; t.style.display = "block";
        const r = t.getBoundingClientRect();
        const dx = r.left < box.left + 16 ? box.left + 16 - r.left : r.right > box.right - 16 ? box.right - 16 - r.right : 0;
        t.style.left = `calc(50% + ${dx}px)`; t.style.display = "";
      };
      el.addEventListener("mouseenter", place);
      el.addEventListener("focus", place);
    });
    sr.querySelectorAll("[data-mod-menu]").forEach((el) => el.addEventListener("click", (e) => {
      e.stopPropagation();  // not the tab under it
      this._modMenu = this._modMenu === el.dataset.modMenu ? null : el.dataset.modMenu;
      this._render();
    }));
    sr.querySelectorAll("[data-mod-open]").forEach((el) => el.addEventListener("click", () => { this._modMenu = null; this._go(`settings/module/${el.dataset.modOpen}`); }));
    sr.querySelectorAll("[data-mod-hide]").forEach((el) => el.addEventListener("click", async () => {
      const id = el.dataset.modHide, hidden = el.dataset.hidden === "1", m = this._state.modules.find((x) => x.id === id);
      this._modMenu = null;
      try {
        await this._ws("aac/module/access", { module: id, values: { hidden } });
        await this._loadState();
        if (this._mods) this._mods = (await this._ws("aac/modules")).modules;
        this._toast(hidden ? `${m?.name || id} hidden · the eye button above shows it again` : `${m?.name || id} is back in the menu`, true);
      } catch (err) { this._toast(err.message || String(err), false); }
      this._render();
    }));
    sr.querySelectorAll("[data-domain-go]").forEach((el) => el.addEventListener("click", () => {
      if (el.dataset.domainGo === this._state?.domain?.id) { this._domainMenu = false; this._render(); return; }
      this._switchDomain(el.dataset.domainGo);
    }));
    sr.querySelector("#dom-visible")?.addEventListener("click", () => { this._domainMenu = false; this._go("domain/visible"); });
    for (const [id, kind] of [["#dom-new", "new"], ["#dom-rename", "rename"], ["#dom-delete", "delete"], ["#dom-voice", "voice"]]) {
      sr.querySelector(id)?.addEventListener("click", () => this._domainAction(kind));
    }
    sr.querySelector("#eye")?.addEventListener("click", () => {
      this._showAll = !this._showAll;
      try { localStorage.setItem("aac.showAll", this._showAll ? "1" : "0"); } catch (e) { /* ignore */ }
      this._render();
    });
    sr.querySelector("#nav-toggle")?.addEventListener("click", () => { this._navOpen = !this._navOpen; this._render(); });
    sr.querySelector("#backdrop")?.addEventListener("click", () => { this._navOpen = false; this._render(); });
    sr.querySelector("#refresh-models")?.addEventListener("click", () => this._loadModels());
    const buf = sr.querySelector("#audio-buffer");
    if (buf) {  // the buffer follows new lines unless you scrolled up to read
      buf.scrollTop = this._bufFollow === false ? this._bufScroll || 0 : buf.scrollHeight;
      buf.addEventListener("scroll", () => {
        this._bufFollow = buf.scrollHeight - buf.scrollTop - buf.clientHeight < 60;
        this._bufScroll = buf.scrollTop;
      });
    }
    sr.querySelectorAll("[data-voice-forget]").forEach((el) => el.addEventListener("click", async () => {
      const v = (this._voices || []).find((x) => x.id === el.dataset.voiceForget);
      if (await this._confirm(`Forget ${v?.name || "this voice"}? Its voice print is deleted.`, "Forget", true)) this._voiceChange({ action: "forget", voice: el.dataset.voiceForget });
    }));
    sr.querySelectorAll("[data-voice-name]").forEach((el) => el.addEventListener("click", async () => {
      const v = (this._voices || []).find((x) => x.id === el.dataset.voiceName);
      const name = await this._prompt("Who is this voice?", v?.named ? v.name : "");
      if (!name?.trim()) return;
      const media = await this._confirm(`Is ${name} from a video, the TV or the radio (not someone in the room)?`, "Yes, video/TV", false, "No, a person");
      this._voiceChange({ action: "name", voice: el.dataset.voiceName, name: name.trim(), kind: media ? "media" : "person" });
    }));
    sr.querySelector("#voices-forget-all")?.addEventListener("click", async () => {
      if (await this._confirm("Forget all voices? Every voice print is deleted and AAC learns voices anew.", "Forget all", true)) this._voiceChange({ action: "forget_all" });
    });
    sr.querySelector("#au-volume")?.addEventListener("input", (e) => { sr.querySelector("#au-volume-val").textContent = e.target.value; });
    sr.querySelector("#au-volume")?.addEventListener("change", async (e) => {  // saves on release and lets you hear it
      const volume = Number(e.target.value);
      try {
        this._audio = { ...this._audio, ...(await this._ws("aac/audio/settings", { volume })) };
        const src = (this._audio.connected || [])[0];
        if (src && volume > 0) await this._ws("aac/audio/say", { source_id: src.id, text: `This is my voice at ${volume} percent.` });
        this._toast(`✓ Volume ${volume} % saved`, true);
      } catch (err) { this._toast(err.message || String(err), false); }
      this._render();
    });
    sr.querySelectorAll("[data-machine-toggle]").forEach((el) => {
      const toggle = (e) => {
        if (e.target.closest("button")) return;  // the ⚙ button opens the settings instead
        this._openMachines = this._openMachines || new Set();
        const host = el.dataset.machineToggle;
        this._openMachines.has(host) ? this._openMachines.delete(host) : this._openMachines.add(host);
        this._render();
      };
      el.addEventListener("click", toggle);
      el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(e); } });
    });
    sr.querySelector("#mc-add")?.addEventListener("click", (e) => {
      this._go("models/new");
      if (this._form) this._form.base_url = e.target.closest("button").dataset.url;
      this._render();
    });

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
    const poolMove = (attr, fn) => sr.querySelectorAll(`[${attr}]`).forEach((el) => el.addEventListener("click", async (e) => {
      e.stopPropagation();
      const [role, i] = el.getAttribute(attr).split("|");
      const items = this._poolItems(role);
      fn(items, Number(i));
      try { await this._setPool(role, items); } catch (err) { this._flash = { ok: false, text: err.message || String(err) }; this._render(); }
    }));
    poolMove("data-pool-up", (a, i) => a.splice(i - 1, 2, a[i], a[i - 1]));
    poolMove("data-pool-down", (a, i) => a.splice(i, 2, a[i + 1], a[i]));
    poolMove("data-pool-del", (a, i) => a.splice(i, 1));
    sr.querySelector("#ent-q")?.addEventListener("input", (e) => {
      this._entQuery = e.target.value;
      clearTimeout(this._entTimer);
      this._entTimer = setTimeout(() => { this._render(); const el = this.shadowRoot.querySelector("#ent-q"); el?.focus(); el?.setSelectionRange(el.value.length, el.value.length); }, 250);
    });
    sr.querySelector("#ent-only-aac")?.addEventListener("click", async () => {
      this._state = await this._ws("aac/master/update", { llm_hass_api: [] });
      this._flash = { ok: true, text: "AAC now only uses its own lists." };
      this._render();
    });
    sr.querySelector("#ent-show")?.addEventListener("change", (e) => { this._entShow = e.target.value; this._render(); });
    sr.querySelectorAll("[data-acc]").forEach((el) => el.addEventListener("click", async () => {
      const on = !el.classList.contains("on");
      el.classList.toggle("on", on);  // light up at once, the save follows
      this._ents = await this._ws("aac/entities/set", { access: { [el.dataset.acc]: { [el.dataset.what]: on } } });
      this._render();
    }));
    sr.querySelectorAll("[data-acc-area]").forEach((el) => el.addEventListener("click", async () => {
      const to = el.dataset.accTo, list = this._entityGroups(this._ents.entities)[el.dataset.accArea] || [];
      const value = to === "write" ? { write: true } : to === "read" ? { read: true, write: false } : { read: false };
      this._ents = await this._ws("aac/entities/set", { access: Object.fromEntries(list.map((e) => [e.entity_id, value])) });
      this._render();
    }));
    sr.querySelectorAll(".vis-tick").forEach((el) => el.addEventListener("change", () => {
      if (el.checked) this._vis.picked.add(el.value); else this._vis.picked.delete(el.value);
      this._render();
      this._autosave("visible", null, "Visible entities");
    }));
    sr.querySelectorAll("[data-vis-area]").forEach((el) => el.addEventListener("click", () => {
      for (const e of this._entityGroups(this._vis.entities)[el.dataset.visArea] || []) {
        if (el.dataset.visTo === "1") this._vis.picked.add(e.entity_id); else this._vis.picked.delete(e.entity_id);
      }
      this._render();
      this._autosave("visible", null, "Visible entities");
    }));
    sr.querySelector("#vis-all")?.addEventListener("click", () => {
      for (const list of Object.values(this._entityGroups(this._vis.entities))) for (const e of list) this._vis.picked.add(e.entity_id);
      this._render();
      this._autosave("visible", null, "Visible entities");
    });
    sr.querySelector("#vis-none")?.addEventListener("click", () => {
      for (const list of Object.values(this._entityGroups(this._vis.entities))) for (const e of list) this._vis.picked.delete(e.entity_id);
      this._render();
      this._autosave("visible", null, "Visible entities");
    });
    sr.querySelectorAll("[data-req-ok]").forEach((el) => el.addEventListener("click", async () => {
      const ids = [...sr.querySelectorAll(`.req-item[data-req="${CSS.escape(el.dataset.reqOk)}"]:checked`)].map((c) => c.value);
      await this._ws("aac/request/decide", { request_id: el.dataset.reqOk, approve: ids });
      await Promise.all([this._loadRequests(), this._loadState()]);
    }));
    sr.querySelectorAll("[data-req-no]").forEach((el) => el.addEventListener("click", async () => {
      await this._ws("aac/request/decide", { request_id: el.dataset.reqNo, approve: false });
      await Promise.all([this._loadRequests(), this._loadState()]);
    }));
    sr.querySelector("#sat-app")?.addEventListener("click", async () => {
      try {
        const r = await this._ws("aac/satellite/app");
        this._flash = { ok: true, text: `Ready: the app can connect now (access in ${r.file}).` };
      } catch (e) { this._flash = { ok: false, text: e.message || String(e) }; }
      this._render();
    });
    sr.querySelector("#dlg-input")?.addEventListener("input", (e) => { if (this._dialog) this._dialog.value = e.target.value; });
    sr.querySelector("#dlg-input")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); this._closeDialog(true); }
      if (e.key === "Escape") this._closeDialog(false);
    });
    sr.querySelector("#dlg-ok")?.addEventListener("click", () => this._closeDialog(true));
    sr.querySelector("#dlg-cancel")?.addEventListener("click", () => this._closeDialog(false));
    sr.querySelector("#reload-page")?.addEventListener("click", () => location.reload());
    sr.querySelector("#ma-reset")?.addEventListener("click", () => {
      const m = this._mods.find((x) => `settings/module/${x.id}` === this._view);
      const t = sr.querySelector("#ma-instr");
      if (m && t) { t.value = m.default_instructions || ""; this._autosave("access", m.id, `${m.name} instructions`); }
    });
    sr.querySelectorAll("[data-skin]").forEach((el) => el.addEventListener("click", async () => {
      this._skins = await this._ws("aac/skin/set", { skin: el.dataset.skin });
      if (this._state) this._state.ui = { ...(this._state.ui || {}), skin: el.dataset.skin };
      this._render();
    }));
    sr.querySelectorAll("[data-pool-tags]").forEach((el) => el.addEventListener("click", async () => {
      const id = el.dataset.poolTags;
      const item = (this._state?.pools?.roles?.master?.items || []).find((x) => x.id === id);
      const now = (item?.traits?.tags || []).join(", ");
      const text = await this._prompt("Your own tags for this model, comma separated (e.g. efficient):", now);
      if (text === null) return;
      const pools = await this._ws("aac/pool/tags", { resource: id, tags: text.split(",").map((t) => t.trim()).filter(Boolean) });
      if (this._state) this._state.pools = pools;
      this._render();
    }));
    sr.querySelectorAll("[data-pool-rule-clear]").forEach((el) => el.addEventListener("click", async () => {
      const pools = await this._ws("aac/pool/rule", { resource: el.dataset.poolRuleClear, rule: null });
      if (this._state) this._state.pools = pools;
      this._render();
    }));
    sr.querySelector("#pool-measure")?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Measuring…";
      try { const pools = await this._ws("aac/pool/measure"); if (this._state) this._state.pools = pools; }
      catch (err) { this._flash = { ok: false, text: err.message || String(err) }; }
      this._render();
    });
    sr.querySelectorAll("[data-pool-add]").forEach((el) => el.addEventListener("click", async () => {
      const role = el.dataset.poolAdd;
      const pick = sr.querySelector(`[data-pool-pick="${CSS.escape(role)}"]`)?.value;
      if (pick) await this._setPool(role, [...this._poolItems(role), pick]);
    }));
    sr.querySelectorAll("[data-master]").forEach((el) => el.addEventListener("click", async (e) => {
      e.stopPropagation();
      const items = [el.dataset.master, ...this._poolItems("master").filter((x) => x !== el.dataset.master)];
      await this._setPool("master", items);
      this._flash = { ok: true, text: "First choice for the master now." };
      this._render();
    }));
    sr.querySelectorAll("[data-delete]").forEach((el) => el.addEventListener("click", async () => {
      if (!(await this._confirm("Delete this model?", "Delete", true))) return;
      await this._ws("aac/model/delete", { model_id: el.dataset.delete });
      this._view = "models";
      await Promise.all([this._loadState(), this._loadModels()]);
    }));

    // chat
    sr.querySelector("#chat-input")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this._send(); }
    });
    sr.querySelector("#chat-send")?.addEventListener("click", () => this._send());
    sr.querySelectorAll("[data-msg-edit]").forEach((el) => el.addEventListener("click", () => {
      this._editing = Number(el.dataset.msgEdit);
      this._editText = null;
      this._render();
      this.shadowRoot.querySelector("#msg-edit")?.focus();
    }));
    sr.querySelector("#msg-edit")?.addEventListener("input", (e) => { this._editText = e.target.value; });
    sr.querySelector("#msg-edit-cancel")?.addEventListener("click", () => { this._editing = null; this._editText = null; this._render(); });
    sr.querySelector("#msg-edit-send")?.addEventListener("click", async () => {
      const i = this._editing, text = sr.querySelector("#msg-edit")?.value ?? this._chat.messages[i]?.text;
      if (this._chat.messages[i]?.role === "user") { this._resend(i, text); return; }
      if (!text?.trim()) return;
      try {
        this._chat = { ...this._chat, ...(await this._ws("aac/chat/edit", { chat_id: this._chat.id, index: i, text: text.trim() })) };
      } catch (e) { this._flash = { ok: false, text: e.message || String(e) }; }
      this._editing = null;
      this._editText = null;
      this._render();
    });
    sr.querySelectorAll("[data-why]").forEach((el) => el.addEventListener("click", () => this._toggleWhy(el.dataset.why)));
    sr.querySelector("#chat-prefs")?.addEventListener("click", () => { this._prefsOpen = !this._prefsOpen; this._render(); });
    sr.querySelectorAll("[data-chat-pref]").forEach((el) => el.addEventListener("change", () => this._setChatPref(el.dataset.chatPref, el.value)));
    sr.querySelectorAll("[data-work] .work-head").forEach((el) => {
      const toggle = () => {
        const box = el.parentElement, key = box.dataset.work;
        this._openWork = this._openWork || new Set();
        box.classList.toggle("open");
        this._openWork.has(key) ? this._openWork.delete(key) : this._openWork.add(key);  // flipped from the default
      };
      el.addEventListener("click", toggle);
      el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
    });
    sr.querySelectorAll("[data-msg-rerun]").forEach((el) => el.addEventListener("click", () => {
      const i = Number(el.dataset.msgRerun), msgs = this._chat.messages;
      const u = msgs.slice(0, i).map((x) => x.role).lastIndexOf("user");
      if (u >= 0) this._resend(u, msgs[u].text);
    }));
    const archiveChat = async (id, archived = true) => {
      await this._ws("aac/chat/archive", { chat_id: id, archived });
      if (archived && this._chat?.id === id) { this._chat = null; this._view = "chat/new"; }
      await this._loadChats();
      if (archived) this._toast("Archived · under CHATS → Archived", true);
      else await this._openChat(id);
    };
    sr.querySelectorAll("[data-chat-archive]").forEach((el) => el.addEventListener("click", (e) => { e.stopPropagation(); archiveChat(el.dataset.chatArchive); }));
    sr.querySelectorAll("[data-chat-del]").forEach((el) => el.addEventListener("click", async (e) => {
      e.stopPropagation();
      const chat = this._chats.find((c) => c.id === el.dataset.chatDel);
      if (!(await this._confirm(`Delete "${chat?.title || ""}" permanently? It cannot be restored.`, "Delete", true))) return;
      await this._ws("aac/chat/delete", { chat_id: el.dataset.chatDel });
      if (this._chat?.id === el.dataset.chatDel) { this._chat = null; this._view = "chat/new"; }
      await this._loadChats();
    }));
    sr.querySelector("#chat-archive")?.addEventListener("click", () => this._chat?.id && archiveChat(this._chat.id));
    sr.querySelector("#chat-restore")?.addEventListener("click", () => this._chat?.id && archiveChat(this._chat.id, false));
    sr.querySelector("#arch-fold")?.addEventListener("click", () => { this._archOpen = !this._archOpen; this._savePref("aac.archOpen", this._archOpen); this._render(); });
    sr.querySelector("#arch-clear")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      const gone = this._chats.filter((c) => c.archived);
      if (!gone.length || !(await this._confirm(`Delete all ${gone.length} archived chat${gone.length === 1 ? "" : "s"} permanently? They cannot be restored.`, "Delete all", true))) return;
      for (const c of gone) await this._ws("aac/chat/delete", { chat_id: c.id });
      if (gone.some((c) => c.id === this._chat?.id)) { this._chat = null; this._view = "chat/new"; }
      await this._loadChats();
      this._toast(`${gone.length} archived chat${gone.length === 1 ? "" : "s"} deleted`, true);
    });
    sr.querySelector("#arch-hide")?.addEventListener("click", (e) => {
      e.stopPropagation();
      this._archHidden = !this._archHidden;
      this._savePref("aac.archHidden", this._archHidden);
      this._render();
    });
    sr.querySelector("#chat-delete")?.addEventListener("click", async () => {
      if (!this._chat || !(await this._confirm(`Delete "${this._chat.title}" permanently? It cannot be restored.`, "Delete", true))) return;
      await this._ws("aac/chat/delete", { chat_id: this._chat.id });
      this._chat = null;
      this._view = "chat/new";
      await this._loadChats();
    });

    // forms
    sr.querySelector("#f-test")?.addEventListener("click", () => this._formTest());
    sr.querySelector("#f-save")?.addEventListener("click", () => this._formSave());
    // Settings cards save themselves: toggles, selects, sliders at once, text fields when you leave them
    sr.querySelectorAll("[data-autosave]").forEach((card) => {
      card.addEventListener("input", (e) => {
        if (e.target.matches("textarea, input:not([type=checkbox]):not([type=radio]):not([type=range])") && !e.target.closest("[data-nosave]")) card.classList.add("editing");
      });
      card.addEventListener("change", (e) => {
        if (!e.target.matches("input, select, textarea") || e.target.closest("[data-nosave]")) return;
        card.classList.remove("editing");
        this._autosave(card.dataset.autosave, card.dataset.arg, card.dataset.label);
      });
    });
    sr.querySelectorAll("[data-say]").forEach((el) => el.addEventListener("click", async () => {
      const input = sr.querySelector(`[data-say-text="${CSS.escape(el.dataset.say)}"]`);
      const text = input?.value.trim() || "Hello, this is AAC.";
      try { await this._ws("aac/audio/say", { source_id: el.dataset.say, text }); if (input) input.value = ""; }
      catch (e) { this._flash = { ok: false, text: e.message || String(e) }; this._render(); }
    }));

  }

  _typing() {
    const el = this.shadowRoot.activeElement;
    return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT");
  }

  async _saveAudio() {
    const sr = this.shadowRoot;
    this._audio = await this._ws("aac/audio/settings", {
      names: sr.querySelector("#au-names").value.split(",").map((n) => n.trim()).filter(Boolean),
      pause_ms: Number(sr.querySelector("#au-pause").value) || 800,
      voices: Object.fromEntries([...sr.querySelectorAll("[data-voice-lang]")].map((el) => [el.dataset.voiceLang, el.value])
        .filter(([, v]) => v).map(([lang, v]) => { const [language, voice] = v.split("|"); return [lang, { language, voice }]; })),
    });
  }

  // The answer while it is written (deltas from the backend). Every model call is a round; a finished
  // round (text before a tool call) folds up into the steps block instead of vanishing.
  _liveHtml() {
    const rounds = this._stream?.rounds || [];
    return rounds.map((r, i) => `${r.text?.trim() ? `<div class="txt md said">${md(r.text)}</div>` : ""}${r.tool
      ? `<div class="work"><div class="steprow">⚙ ${esc(r.tool.split("__").pop())}${i === rounds.length - 1 ? "…" : ""}</div></div>` : ""}`).join("")
      + '<span class="cursor"></span>';
  }

  _streamHtml() {
    return `<div class="live">${this._liveHtml()}</div>`;
  }

  _onDelta(ev) {
    if (!this._busy) return;
    const mine = this._chat?.id ? ev.chat_id === this._chat.id : !this._streamChat || ev.chat_id === this._streamChat;
    if (!mine) return;
    this._streamChat = ev.chat_id;
    const st = this._stream = this._stream || { rounds: [] };
    const r = ev.round ?? 0;
    st.rounds[r] = { text: ev.text, tool: ev.tool || st.rounds[r]?.tool };
    const bubble = this.shadowRoot.querySelector(".bubble.busy");
    if (!bubble?.querySelector(".live")) { this._render(); return; }
    bubble.querySelector(".live").innerHTML = this._liveHtml();
    const log = this.shadowRoot.querySelector(".log");
    if (log && log.scrollHeight - log.scrollTop - log.clientHeight < 160) log.scrollTop = log.scrollHeight;  // follow, unless scrolled up
  }

  // Edit or rerun: cut the chat off at a message and send again (the backend gives the model the rest as context)
  async _resend(index, text) {
    if (!this._chat?.id || this._busy || !text?.trim()) return;
    this._editing = null;
    this._editText = null;
    await this._ws("aac/chat/rewind", { chat_id: this._chat.id, index });
    this._chat.messages = this._chat.messages.slice(0, index);
    const input = this.shadowRoot.querySelector("#chat-input");
    if (input) input.value = text.trim();
    await this._send();
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
    this._stream = null;
    this._streamChat = null;
    this._render();
    try {
      const r = await this._ws("aac/chat", { text, chat_id: chatId });
      this._busy = false;
      this._stream = null;
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
      if (!r.unlisted && !f.models.includes(f.model)) f.model = f.models[0] || "";
      if (!f.name) f.name = f.model;
      f.msg = { ok: true, text: r.unlisted ? "Online · it cannot list its models, type the model name" : `Online · ${r.models.length} model(s)` };
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
}

if (!customElements.get("aac-panel")) customElements.define("aac-panel", AacPanel);

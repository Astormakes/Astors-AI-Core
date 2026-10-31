// AAC 2 panel (docs/v2): one sidebar (the cards' pages and their entries: chats, lists …; then Nodes and Settings),
// the cards' own pages (each card file's .js, spec/cards.md), the node view (card types left, the grid, the selected
// card's settings right), and the core's settings (Audio, Models, Appearance). Plain web component, HA's theme.

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const md = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\n/g, "<br>");
const tip = (html) => `<span class="qm" tabindex="0">?<span class="tip">${html}</span></span>`;
const W = 180, HEAD = 34, ROW = 20;
const COL = W + 110; // one slot column (v1: a column per step in the flow)
const crosses = (a, b, c, d) => { // do the segments a–b and c–d cross
  const side = (p, q, r) => (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
  return side(a, b, c) * side(a, b, d) < 0 && side(c, d, a) * side(c, d, b) < 0;
};
const KIND_NAMES = { text: "context", read: "read (dotted)", audio: "audio stream", clip: "audio clip", any: "anything" };
const KIND_NAME = new Proxy(KIND_NAMES, { get: (t, k) => String(k).split("|").map((x) => t[x] || x).join(" or ") }); // "audio|clip": either
// A context as the model gets it (docs/v2 page 3), top to bottom in the order the model reads it; a colour per part
// (system, tools, said, answer, tool result), only what belongs together folds (the tool list, a call with its result, a
// long text); the events below, set apart. The monitor and a chat's Context view show it.
// ctx: {system, messages, tools, events} (a context's summary); name(id): a card's name.
const contextView = (ctx, name) => {
  const body = (text) => {
    const s = String(text ?? "");
    return s.length > 600 ? `<details><summary class="cvmore">${esc(s.slice(0, 220).replace(/\s+/g, " "))}…</summary><pre class="cvtext">${esc(s)}</pre></details>`
      : `<pre class="cvtext">${esc(s)}</pre>`;
  };
  const tags = (e) => (e.tags || []).map((x) => `<code class="cvtag">#${esc(x)}</code>`).join(" ");
  const label = (kind, text, e) => `<div class="cvhead"><span class="cvlab ${kind}">${esc(text)}</span> ${e ? tags(e) : ""}</div>`;
  const results = Object.fromEntries((ctx.messages || []).filter((m) => m.kind === "result").map((m) => [m.data?.call_id, m]));
  const att = (a) => `<div class="cvatt">${label("att", `attached by ${name(a.by)} · ${a.kind === "note" ? "before" : "after"}`, a)}${body(a.text)}</div>`;
  const call = (c) => { const r = results[c.id]; return `<details class="cvcall"><summary><code>${esc(c.name)}(${esc(JSON.stringify(c.args || {}).slice(1, -1))})</code> <span class="hint">→ ${esc(name(c.card || ""))}${r ? "" : " · no result yet"}</span></summary>
    ${r ? `<div class="cvpart result">${label("result", `tool result · ${name(r.by)}`, r)}${body(r.text)}</div>` : ""}</details>`; };
  const msg = (m) => {
    if (m.kind === "result") return ""; // under the call it answers
    const kind = m.kind === "said" ? "said" : "answer", attached = m.attachments || [];
    const who = m.kind === "said" ? `${name(m.by)} · said` : `${name(m.by)} · answered${m.state === "cut" ? " (stopped)" : m.state === "open" ? " (writing…)" : ""}`;
    return `<div class="cvpart ${kind}">${attached.filter((a) => a.kind === "note").map(att).join("")}${label(kind, who, m)}${m.text ? body(m.text) : ""}
      ${(m.data?.calls || []).map(call).join("")}${attached.filter((a) => a.kind !== "note").map(att).join("")}</div>`;
  };
  const tools = ctx.tools || [];
  return (ctx.system || []).map((e) => `<div class="cvpart system">${label("system", `system · ${name(e.by)}`, e)}${body(e.text)}</div>`).join("")
    + (tools.length ? `<details class="cvpart tools"><summary>${label("tools", `tools · ${tools.length}`)}<span class="hint">${tools.map((x) => esc(x.name)).join(", ")}</span></summary>
      ${tools.map((x) => `<div class="cvtool"><code>${esc(x.name)}(${Object.entries(x.args || {}).map(([a, y]) => `${esc(a)}${y.required ? "" : "?"}`).join(", ")})</code> <span class="hint">${esc(name(x.card))}</span><div>${esc(x.description || "")}</div></div>`).join("")}</details>` : "")
    + (ctx.messages || []).map(msg).join("")
    + ((ctx.events || []).length ? `<div class="cvaac"><div class="hint">AAC only, never sent to the model</div>${(ctx.events || []).map((e) => `<div class="cvpart event">${label("event", `${e.kind} · ${name(e.by)}`, e)}${e.text ? body(e.text) : ""}</div>`).join("")}</div>` : "");
};

const ICON = { // the grid's round buttons (drawn, so they look the same everywhere)
  select: '<svg viewBox="0 0 16 16"><rect x="2.5" y="2.5" width="11" height="11" rx="1" stroke-dasharray="2.5 2"/></svg>',
  cut: '<svg viewBox="0 0 16 16"><circle cx="4" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><path d="M5.5 10.5 12 2M10.5 10.5 4 2"/></svg>',
  bin: '<svg viewBox="0 0 16 16"><path d="M2.5 4h11M6 4V2.5h4V4M4 4l.7 9.5h6.6L12 4M6.8 6.5v5M9.2 6.5v5"/></svg>',
};
const KIND_COLOR = { text: "#f5a623", read: "#f5a623", audio: "#4caf50", clip: "#9c27b0", any: "#ffffff" };
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
.dom { position: relative; } .dbtn { width: 100%; text-align: left; font-weight: 600; padding: 6px 10px; }
.dmenu { position: absolute; left: 0; right: 0; top: 100%; z-index: 50; background: var(--card-background-color); border: 1px solid var(--divider-color);
  border-radius: 8px; padding: 4px 0; box-shadow: 0 4px 16px rgba(0,0,0,.25); } .dmenu hr { border: 0; border-top: 1px solid var(--divider-color); margin: 4px 0; }
.dot[data-kind="any"] { stroke: #888; stroke-width: 1.5; } /* takes anything: white, with a rim to show on light themes */
.item[data-warn]::after { content: " !"; color: var(--error-color, #db4437); font-weight: 700; }
h4[data-alarm] { color: var(--error-color, #db4437); animation: alarm 2s ease-in-out infinite; }
@keyframes alarm { 50% { opacity: .45; } }
.warnline { color: var(--error-color, #db4437); font-size: 12px; padding: 4px 16px 0; }
#wave { position: fixed; inset: 0; z-index: 1500; background: rgba(0,0,0,.45); display: flex; align-items: center; justify-content: center; padding: 12px; }
#wave .wavebox { background: var(--card-background-color, #fff); color: var(--primary-text-color); border-radius: 10px; width: min(1300px, 100%); max-height: 100%; overflow: auto; padding: 12px 14px; box-shadow: 0 8px 30px rgba(0,0,0,.35); }
#wave canvas { width: 100%; height: 340px; display: block; border: 1px solid var(--divider-color); border-radius: 6px; touch-action: none; cursor: grab; background: var(--primary-background-color); }
#wave .row { flex-wrap: wrap; gap: 6px 14px; margin-top: 8px; } #wave .check { display: inline-flex; gap: 6px; align-items: center; } #wave .check input { width: auto; margin: 0; } #wave input[type=number] { width: 90px; } #wave input[type=range] { width: 200px; }
#dlg { position: fixed; inset: 0; z-index: 2000; background: rgba(0,0,0,.45); display: flex; align-items: center; justify-content: center; padding: 16px; }
.dlgbox { background: var(--card-background-color, #fff); color: var(--primary-text-color); border-radius: 12px; padding: 18px 20px; width: min(440px, 100%);
  box-shadow: 0 8px 32px rgba(0,0,0,.35); } .dlgbox h3 { margin: 0 0 8px; } .dlgbox p { margin: 0 0 8px; white-space: normal; }
.dlgbox input, .dlgbox textarea { width: 100%; box-sizing: border-box; }
button.danger:not(.small) { background: var(--error-color, #db4437); color: #fff; border-color: transparent; }
nav .item { position: relative; } .item .navacts { display: none; position: absolute; right: 6px; top: 4px; gap: 2px;
  background: var(--sidebar-background-color, var(--card-background-color)); border-radius: 6px; } .item .navacts button { padding: 0 5px; }
.item:hover .navacts, .item.on .navacts, .item .navacts.open { display: inline-flex; }
.navmenu { background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 8px; margin: 4px 0; padding: 2px 0;
  box-shadow: 0 4px 12px rgba(0,0,0,.2); clear: both; }
.permhead { margin: 0; cursor: pointer; user-select: none; } .card .permhead + * { margin-top: 10px; }
.access .acclist { max-height: 420px; overflow-y: auto; border: 1px solid var(--divider-color); border-radius: 8px; }
.accrow { display: flex; align-items: center; gap: 8px; padding: 4px 8px; border-bottom: 1px solid var(--divider-color); }
.accrow .t { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; } .accrow .seg { display: flex; gap: 2px; flex: 0 0 auto; }
.dmenu .item.danger { color: var(--error-color, #db4437); }
h4.fold { cursor: pointer; user-select: none; border-radius: 6px; } h4.fold:hover { background: rgba(127,127,127,.1); }
h4.fold.lit { color: var(--primary-color); background: rgba(3,169,244,.12); } h4.fold .arrow { display: inline-block; width: 12px; }
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
.grid svg { width: 100%; height: 100%; display: block; user-select: none; touch-action: none; }
.side { width: var(--side-w, 300px); flex: none; border-left: 1px solid var(--divider-color); overflow-y: auto; padding: 12px; }
.nodes { position: relative; } .nodes.nopal .palette, .nodes.nopal [data-grip="pal"], .nodes.noside .side, .nodes.noside [data-grip="side"] { display: none; }
.edge { position: absolute; top: 50%; transform: translateY(-50%); z-index: 6; width: 22px; height: 56px; padding: 0; font-size: 16px; cursor: pointer;
  border: 1px solid var(--divider-color); background: var(--card-background-color, #fff); color: var(--primary-text-color); opacity: .85; }
.edge.left { left: 0; border-left: none; border-radius: 0 8px 8px 0; } .edge.right { right: 0; border-right: none; border-radius: 8px 0 0 8px; }
.side label { display: block; font-size: 12px; color: var(--secondary-text-color); margin: 10px 0 3px; }
.side .check, .content .check { display: flex; gap: 6px; align-items: center; color: var(--primary-text-color); font-size: 14px; } .content .check input { width: auto; flex: 0 0 auto; }
.side .check input { width: auto; }
.fn { font-size: 12px; margin: 6px 0; } .fn code { font-size: 12px; }
.node rect.box { fill: var(--card-background-color); stroke: var(--divider-color); stroke-width: 1.2; }
.node.sel rect.box { stroke: var(--primary-color); stroke-width: 2.4; }
#frame { fill: var(--primary-color); fill-opacity: .08; stroke: var(--primary-color); stroke-dasharray: 5 4; vector-effect: non-scaling-stroke; pointer-events: none; }
.node text { fill: var(--primary-text-color); font-size: 13px; } .node text.sub { fill: var(--secondary-text-color); font-size: 10px; } .node text.port { font-size: 10px; fill: var(--secondary-text-color); }
.dot { cursor: crosshair; }
.wire { fill: none; stroke-width: 2; opacity: .7; } .wire.read { stroke-dasharray: 4 4; } .wire.sel { stroke-width: 4; opacity: 1; }
/* something passed (owner): the wire lights up brighter and pulses thick-thin, fading back to normal over a few seconds */
.wire.lit { animation: lit 4s ease-out; }
@keyframes lit { 0% { stroke: #fff; filter: brightness(1.9) saturate(1.4) drop-shadow(0 0 3px #fff); stroke-width: 2; opacity: 1; } 10% { stroke-width: 6; } 20% { stroke-width: 2.5; }
  30% { stroke-width: 5.5; } 40% { stroke-width: 2.5; } 50% { stroke-width: 4.5; } 60% { stroke-width: 2.5; } 70% { stroke-width: 3.5; filter: brightness(1.4); }
  100% { stroke-width: 2; filter: none; opacity: .7; } }
.grip { flex: none; width: 7px; margin: 0 -4px 0 -3px; cursor: col-resize; position: relative; z-index: 5; touch-action: none; }
.grip:hover, .grip.drag { background: var(--divider-color); }
.mons { position: absolute; inset: 0; pointer-events: none; overflow: hidden; }
.mon { position: absolute; transform: translate(-50%, calc(-100% - 8px)); pointer-events: auto; cursor: pointer; max-width: 260px; min-width: 110px;
  padding: 4px 8px; font-size: 11px; line-height: 1.35; border: 1px solid var(--primary-color); background: var(--card-background-color);
  border-radius: 6px; box-shadow: 0 2px 8px rgba(0,0,0,.2); }
.mon.sel { border-width: 2px; } .mon .t { display: flex; gap: 6px; color: var(--secondary-text-color); font-size: 10px; }
.mon .t b { color: var(--primary-color); font-weight: 500; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mon .x { overflow: hidden; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; white-space: pre-wrap; }
.mon.flash { animation: monflash .8s; } @keyframes monflash { from { box-shadow: 0 0 14px var(--primary-color); } }
.fold { margin-top: 8px; font-size: 12px; } .fold > summary { cursor: pointer; color: var(--secondary-text-color); font-weight: 500; }
.cvpart { border-left: 3px solid var(--divider-color); padding: 4px 8px; margin: 6px 0; font-size: 13px; }
.cvpart.system { border-left-color: #8e8e93; } .cvpart.tools { border-left-color: #26a69a; } .cvpart.said { border-left-color: var(--primary-color); }
.cvpart.answer { border-left-color: #f5a623; } .cvpart.result { border-left-color: #9c27b0; margin-left: 10px; } .cvpart.event { border-left-style: dashed; }
.cvlab { font-size: 11px; font-weight: 500; text-transform: uppercase; letter-spacing: .03em; }
.cvlab.system { color: #8e8e93; } .cvlab.tools { color: #26a69a; } .cvlab.said { color: var(--primary-color); } .cvlab.answer { color: #f5a623; }
.cvlab.result { color: #9c27b0; } .cvlab.att, .cvlab.event { color: var(--secondary-text-color); }
.cvatt { margin: 4px 0 4px 10px; padding: 2px 8px; border-left: 2px dashed var(--divider-color); font-size: 12px; }
.cvcall { margin: 4px 0 2px 10px; font-size: 12px; } .cvcall > summary, .cvpart.tools > summary { cursor: pointer; }
.cvtool { margin: 4px 0 4px 10px; font-size: 12px; } .cvpart.tools > summary .cvhead { display: inline; margin-right: 6px; }
.cvhead { font-size: 12px; } .cvtag { font-size: 10px; opacity: .6; } .cvmore { cursor: pointer; font-size: 12px; color: var(--secondary-text-color); }
.cvtext { white-space: pre-wrap; word-break: break-word; margin: 2px 0; font: inherit; font-size: 12px; }
.cvaac { margin-top: 16px; padding-top: 6px; border-top: 1px dashed var(--divider-color); opacity: .8; }
.fnrow { border-left: 3px solid var(--divider-color); padding: 4px 8px; margin: 6px 0; } .fnrow.off { opacity: .55; }
.fnrow input[type=text], .fnrow textarea { width: 100%; box-sizing: border-box; font-size: 12px; }
.gridtools { position: absolute; right: 30px; top: 10px; z-index: 6; display: flex; flex-direction: column; gap: 6px; }
.gridtools button { width: 30px; height: 30px; border-radius: 50%; padding: 0; display: grid; place-items: center; cursor: pointer; border: 1px solid var(--divider-color); background: var(--card-background-color, #fff); color: var(--primary-text-color); box-shadow: 0 1px 3px rgba(0,0,0,.2); }
.gridtools svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
.gridtools button:disabled { opacity: .35; cursor: default; }
.gridtools button.on { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
.grid.tool-select svg, .grid.tool-cut svg { cursor: crosshair; }
.wire.cut { stroke: #e53935 !important; stroke-width: 3.5; stroke-dasharray: 6 4; }
#cutline { fill: none; stroke: #e53935; stroke-width: 2; stroke-dasharray: 4 3; vector-effect: non-scaling-stroke; pointer-events: none; }
.legend { position: absolute; right: 8px; bottom: 6px; font-size: 10px; color: var(--secondary-text-color); line-height: 1.5; opacity: .8; }
.legend div { display: flex; align-items: center; gap: 4px; } .grid .legend svg { flex: none; width: 20px; height: 8px; }
.warn { color: var(--error-color, #db4437); font-size: 12px; margin: 4px 0; }
.mlrow select { flex: 1; } .mlrow .check { font-size: 12px; white-space: nowrap; }
.hit { fill: none; stroke: transparent; stroke-width: 14; cursor: pointer; }
.menu { display: none; background: none; border: none; font-size: 22px; padding: 4px 8px 4px 0; cursor: pointer; color: var(--primary-text-color); }
.scrim { display: none; }
@media (max-width: 800px) { nav { position: absolute; z-index: 20; height: 100%; transform: translateX(-100%); transition: transform .2s; } .root.navopen nav { transform: none; }
  .menu { display: inline-block; } .root.navopen .scrim { display: block; position: absolute; inset: 0; z-index: 15; background: rgba(0,0,0,.3); }
  .grip { display: none; }
  .nodes .palette, .nodes .side { position: absolute; top: 0; bottom: 0; z-index: 8; width: min(85vw, 320px); background: var(--primary-background-color); box-shadow: 0 0 12px rgba(0,0,0,.3); }
  .nodes .palette { left: 0; } .nodes .side { right: 0; }
  .nodes:not(.nopal) .edge.left { left: min(85vw, 320px); } .nodes:not(.noside) .edge.right { right: min(85vw, 320px); } }
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
    this.shadowRoot.addEventListener("keydown", (e) => { // a dialog: Enter confirms, Esc cancels
      if (!this._dialog) return;
      if (e.key === "Escape") { e.stopPropagation(); this._closeDialog("cancel"); }
      if (e.key === "Enter" && !(e.target.tagName === "TEXTAREA" && !e.ctrlKey)) {
        e.preventDefault(); e.stopPropagation();
        const main = this._dialog.buttons.find(([, , k]) => k === "primary" || k === "danger") || this._dialog.buttons.at(-1);
        this._closeDialog(main[0]);
      }
    }, true);
    this.shadowRoot.addEventListener("change", (e) => { if (e.target.id === "importfile") { this._loadFile(e.target.files[0]); e.target.value = ""; } });
    this.shadowRoot.addEventListener("input", (e) => { // an access list's search: only its rows are drawn again
      const box = e.target.dataset?.accsearch;
      if (!box) return;
      (this._accFilter ||= {})[box] = e.target.value;
      const [cardId, key] = box.split("|"), rows = this.shadowRoot.querySelector(`[data-accbox="${CSS.escape(box)}"]`);
      if (rows) rows.innerHTML = this._accessRows(this._card(cardId), key);
    });
    // one listener for what is drawn by others or redrawn often: the sidebar, a card page's ☰, the Audio page's meters
    this.shadowRoot.addEventListener("click", (e) => {
      if (this.navMenu && !e.target.closest?.(".navacts.open, [data-navmore]")) { this.navMenu = null; this._redrawNav(); } // a click anywhere else closes ⋯
      if (e.target.id === "dlg") { this._closeDialog("cancel"); return; } // a click beside the dialog
      const dlg = e.target.closest?.("[data-dlg]");
      if (dlg) { this._closeDialog(dlg.dataset.dlg); return; }
      const wave = e.target.closest?.("[data-wave]");
      if (wave) { this._openWave(wave.dataset.wave); return; }
      const m = e.target.closest?.("[data-rload],[data-ract],[data-rdl],[data-srvadd],[data-srvsave],[data-srvedit],[data-srvdel],[data-srvcancel],[data-amadd],[data-amsave],[data-amedit],[data-amdel],[data-amcancel]");
      if (m) { this._modelsClick(m); return; }
      const t = e.target.closest?.("[data-navact],[data-navmore],[data-say],[data-fold],[data-navadd],[data-go],[data-domgo],[data-domact],[data-acclevel],[data-accall],[data-permopen],#dombtn,#navbtn");
      if (!t) return;
      const d = t.dataset;
      if (d.say) this.ws("aac/audio/say", { source: d.say, text: "This is how I sound." });
      else if (t.id === "dombtn") { this.domOpen = !this.domOpen; this.render(); }
      else if (d.navmore) { e.stopPropagation(); this.navMenu = this.navMenu === d.navmore ? null : d.navmore; this._redrawNav(); }
      else if (d.navact) { e.stopPropagation(); this._navAction(d.entry, d.navact); }
      else if (d.acclevel) this._setAccess(d.acc, [d.accid], d.acclevel);
      else if (d.accall) {
        const [cardId] = d.acc.split("|"), q = ((this._accFilter || {})[d.acc] || "").toLowerCase();
        const shown = (this._accItems?.[cardId] || []).filter((x) => !q || `${x.name} ${x.id} ${x.area} ${x.group}`.toLowerCase().includes(q));
        (async () => {
          if (shown.length > 20 && !(await this.confirm(`Set ${shown.length} entries to ${d.accall === "read" ? "read-only" : d.accall}?`))) return;
          this._setAccess(d.acc, shown.map((x) => x.id), d.accall);
        })();
      } else if (d.permopen) { this.permOpen = this.permOpen === d.permopen ? null : d.permopen; this.render(); }
      else if (d.domact) this._domainAction(d.domact);
      else if (d.domgo) { this.domOpen = false; this.ws("aac/domain/switch", { domain_id: d.domgo }).then(async () => { this._els = {}; await this._load(); this.go("card", null, null); }); }
      else if (d.navadd) { e.stopPropagation(); const el = this._els[d.navadd]; if (el?.add) { this.go("card", d.navadd, this.view.card === d.navadd ? this.view.id : null).then(() => el.add()); } else this.go("card", d.navadd, null); }
      else if (d.fold) { e.stopPropagation(); const f = this._folds(); f[d.fold] = !f[d.fold]; this._setFolds(f); const n = this.shadowRoot.querySelector("nav"); if (n) n.outerHTML = this._nav(); }
      else if (d.peek && this._phone()) { this.navPeek = this.navPeek === d.peek ? null : d.peek; this._redrawNav(); } // on a phone Settings only opens in the sidebar (a page would close it)
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
  get sel() { return this._sel; } // the card whose settings show; picking one (or none) lets go of several
  set sel(id) { this._sel = id; this.many = new Set(); }
  _picked() { return this.many.size ? this.many : new Set(this.sel ? [this.sel] : []); } // the selected cards on the Nodes page
  _pick(ids) { const s = new Set(ids); if (s.size > 1) { this.sel = null; this.many = s; } else this.sel = [...s][0] || null; } // several: no settings, they move together
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
    } else if (ev.what === "models") { // a model stopped or started answering: the warnings follow
      clearTimeout(this._mt2);
      this._mt2 = setTimeout(async () => {
        this.state = await this.ws("aac/state");
        const n = this.shadowRoot.querySelector("nav");
        if (n) n.outerHTML = this._nav();
        if (this.view.kind === "card") this._els[this.view.card]?.render?.(); else if (this.view.kind === "models") this.render();
      }, 300);
    } else if (ev.what === "appearance") {
      this.ws("aac/appearance").then((look) => { this.look = look; this.render(); });
    } else if (ev.what === "audio" && this.view.kind === "audio") {
      clearTimeout(this._at); this._at = setTimeout(async () => { this.audio = await this.ws("aac/audio"); this.render(); }, 300);
    } else if (ev.what === "view") {
      this._viewEls?.[ev.card]?.frame?.(ev.data);
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

  // the Nodes page's side panels (card types left, settings right): arrows at the edges; on a phone they lie over the
  // grid, each open one is an entry in the history, so Back closes the right one, then the left, then goes to the last chat
  _phone() { return matchMedia("(max-width: 800px)").matches; }

  _panel(which, open) {
    const key = which === "pal" ? "palOpen" : "sideOpen";
    if (!!this[key] === open) return;
    if (this._phone()) {
      if (open) { history.pushState({ aac: which }, ""); this[key] = true; this.render(); }
      else history.back(); // the Back handler closes it
      return;
    }
    this[key] = open; this.render();
  }

  _closePanels() { // a tap on the empty grid (phone): every open panel goes at once (one step back per panel)
    const n = (this.sideOpen ? 1 : 0) + (this.palOpen ? 1 : 0);
    if (!n) return false;
    this.sideOpen = this.palOpen = false; this._quietBack = true; history.go(-n); this.render();
    return true;
  }

  _onBack() {
    if (this._quietBack) { this._quietBack = false; return; } // the panels closed already
    if (this.view.kind !== "nodes") return;
    if (this.sideOpen) this.sideOpen = false;
    else if (this.palOpen) this.palOpen = false;
    else { const last = this._lastCard || {}; this.go("card", last.card || null, last.id || null); return; }
    this.render();
  }

  connectedCallback() { this._back ||= () => this._onBack(); window.addEventListener("popstate", this._back); }
  disconnectedCallback() { window.removeEventListener("popstate", this._back); }

  async go(kind, card = null, id = null) {
    if (kind === "card") this._lastCard = { card, id }; // Back from the Nodes page on a phone goes here
    if (kind === "nodes" && this.view.kind !== "nodes") {
      const phone = this._phone();
      this.palOpen = this.sideOpen = !phone;
      if (phone) history.pushState({ aac: "nodes" }, ""); // Back with both panels closed leaves the page
    }
    this.view = { kind, card, id };
    this.navOpen = false; this.navPeek = null; // on a phone the sidebar closes when a page is picked
    if (kind === "nodes") this._watchIds = null; // say at once which views it shows
    if (kind === "card" && !this.nav.some((n) => n.card === card)) this.view.card = this.nav[0]?.card || null;
    if (kind === "models") { this.servers = null; this._loadServers(); clearInterval(this._srvLive); this._srvLive = setInterval(() => { if (this.view.kind !== "models") { clearInterval(this._srvLive); return; } this._loadServers(); }, 3000); }
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
      contextView: (ctx) => contextView(ctx, (id) => this._card(id)?.title || id), // a context in sections
      failing: () => this._failing(), // names of models that do not answer (a page may warn)
      confirm: (text, ok, danger) => this.confirm(text, ok, danger), prompt: (text, value, multiline) => this.prompt(text, value, multiline),
      alert: (text) => this.alert(text), dialog: (o) => this.dialog(o), // AAC's own dialogs
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
      : v.kind === "appearance" ? this._appearanceView() : v.kind === "permissions" ? this._permissionsView() : v.card ? '<div id="cardhost"></div>'
      : `${this._header("AAC")}<div class="content"><p>No card with a page in this domain (a Chat, Lists …). Add one in Nodes.</p></div>`;
    const skin = this.look?.skins.find((k) => k.id && k.id === this.look.skin);
    const page = `${v.kind}/${v.card || ""}`, before = this.shadowRoot.querySelector("main .content");
    const scrolled = before && this._page === page ? before.scrollTop : 0; // a redraw of the same page stays where it was
    Object.values(this._fieldEls || {}).forEach((el) => el.remember?.()); // an editor being typed in keeps its caret
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>${skin ? `<link rel="stylesheet" href="${esc(skin.url)}?v=${esc(this.state.version)}">` : ""}<div class="root ${this.navOpen ? "navopen" : ""}">${this._nav()}<div class="scrim" id="scrim"></div><div class="grip" data-grip="nav" data-target="nav" title="Drag: width of the sidebar (double-click: back to normal)"></div><main>${main}</main></div>`;
    this._page = page;
    if (scrolled) this.shadowRoot.querySelector("main .content")?.scrollTo(0, scrolled);
    this._bind();
    if (this._wave) this._drawWave(); // and the waveforms view
    if (this._dialog) this._drawDialog(); // a redraw keeps an open dialog
    if (v.kind === "nodes") { if (!this._dragging && this._settle()) this._savePositions(); this._drawGrid(); } // no card stays on another
    this._fieldElements();
    const el = v.kind === "card" && this._els[v.card];
    if (el) this.shadowRoot.querySelector("#cardhost")?.replaceChildren(el); // a redraw keeps the page as it is
  }

  // ------------------------------------------------------------------ waveforms (a Microphone's echo subtraction)
  _openWave(cardId) {
    const c = this._card(cardId), st = c.settings || {};
    this._wave = { card: cardId, mic: null, ref: null, delay: Number(st.echo_delay) || 0, gain: st.echo_gain ?? 1, invert: !!st.echo_invert,
      start: 0, spp: 40, show: { mic: true, ref: true, res: true }, info: "Play the test sound (or take the last seconds while AAC spoke)." };
    this._drawWave();
  }

  async _waveFetch(action) {
    const w = this._wave;
    w.info = action === "test" ? "Playing the test sound …" : "Taking the last seconds …"; this._drawWave();
    try {
      const r = (await this.ws("aac/card/ui", { card: w.card, action, args: { seconds: 5 } })).result;
      const pcm = (b64) => { if (!b64) return null; const b = atob(b64), u = new Uint8Array(b.length); for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i); return new Int16Array(u.buffer); };
      w.mic = pcm(r.mic); w.ref = pcm(r.reference);
      w.start = 0; w.spp = Math.max(1, (w.mic?.length || 1) / 1200);
      w.info = `${(w.mic.length / r.rate).toFixed(1)} s · microphone: ${r.names[0]} · speaker: ${r.names[1]}`;
    } catch (e) { w.info = e.message || String(e); }
    this._drawWave();
  }

  _waveAuto() { // the delay with the strongest match (coarse on every 4th sample, then exact), the gain by least squares
    const w = this._wave, mic = w.mic, ref = w.ref;
    if (!mic || !ref) return;
    const best = (lo, hi, step) => {
      let top = -1, at = 0;
      for (let d = lo; d <= hi; d += step) {
        let num = 0, den = 1e-9;
        for (let i = Math.max(0, d); i < mic.length && i - d < ref.length; i += step) { const r = ref[i - d]; num += mic[i] * r; den += r * r; }
        const score = Math.abs(num) / Math.sqrt(den);
        if (score > top) { top = score; at = d; }
      }
      return at;
    };
    const coarse = best(-1600, 16000, 4), d = best(coarse - 8, coarse + 8, 1); // −100 ms … +1 s (Bluetooth adds a lot)
    let num = 0, den = 1e-9;
    for (let i = Math.max(0, d); i < mic.length && i - d < ref.length; i++) { const r = ref[i - d]; num += mic[i] * r; den += r * r; }
    const g = num / den;
    w.delay = d / 16; w.gain = Math.round(Math.abs(g) * 1000) / 1000; w.invert = g < 0;
    w.info = `Lined up: ${w.delay} ms, gain ${w.gain}${w.invert ? ", inverted" : ""}`;
    this._drawWave();
  }

  _drawWave() {
    const w = this._wave;
    this.shadowRoot.querySelector("#wave")?.remove();
    if (!w) return;
    const box = document.createElement("div");
    box.id = "wave";
    const sw = (k, color, label) => `<label class="check"><input type="checkbox" data-wvshow="${k}" ${w.show[k] ? "checked" : ""}><span style="color:${color}">■</span> ${label}</label>`;
    box.innerHTML = `<div class="wavebox"><div class="row" style="margin:0"><h3 style="margin:0">Waveforms · ${esc(this._card(w.card)?.title || "")}</h3><span style="flex:1"></span><button class="small" data-wv="close">✕</button></div>
      ${this._field(this._card(w.card), ["source", this._type("microphone").settings.source])}${this._field(this._card(w.card), ["echo_from", this._type("microphone").settings.echo_from])}
      <canvas id="wavecv"></canvas>
      <div class="row">${sw("mic", "#2196f3", "microphone")}${sw("ref", "#ff9800", "what the speaker played (shifted, scaled: what is subtracted)")}${sw("res", "#4caf50", "after the subtraction")}</div>
      <div class="row"><label>Delay</label><input type="range" min="-100" max="1000" step="0.0625" data-wvin="delay" value="${w.delay}"><input type="number" step="0.0625" data-wvin="delay" value="${w.delay}"> ms
        <label>Gain</label><input type="range" min="0" max="4" step="0.005" data-wvin="gain" value="${w.gain}"><input type="number" step="0.005" min="0" data-wvin="gain" value="${w.gain}">
        <label class="check"><input type="checkbox" data-wvin="invert" ${w.invert ? "checked" : ""}>inverted</label></div>
      <div class="row"><button class="small" data-wv="test">Play test sound & record</button><button class="small" data-wv="recent">Take the last 5 s</button><button class="small" data-wv="auto" ${w.ref ? "" : "disabled"}>Line up automatically</button>
        <span class="hint" id="wvinfo">${esc(w.info)}</span><span style="flex:1"></span><button class="primary small" data-wv="save">Save to the card</button></div>
      <p class="hint" style="margin:6px 0 0">Wheel or pinch: zoom · drag: move · one step of the delay is one sample (1/16 ms)</p></div>`;
    this.shadowRoot.appendChild(box);
    box.addEventListener("click", async (e) => {
      if (e.target === box) { this._wave = null; box.remove(); return; }
      const a = e.target.closest("[data-wv]")?.dataset.wv;
      if (a === "close") { this._wave = null; box.remove(); }
      else if (a === "test" || a === "recent") this._waveFetch(a);
      else if (a === "auto") this._waveAuto();
      else if (a === "save") {
        await this.ws("aac/card/update", { card: w.card, settings: { echo: true, echo_delay: w.delay, echo_gain: w.gain, echo_invert: w.invert } });
        w.info = "Saved: the card subtracts with these values now."; await this._load(); this.render();
      }
    });
    box.querySelectorAll("[data-set]").forEach((el) => el.addEventListener("change", () => this._saveSetting(el))); // which microphone, whose speaker
    box.querySelectorAll("[data-wvshow]").forEach((el) => el.addEventListener("change", () => { w.show[el.dataset.wvshow] = el.checked; this._waveCanvas(); }));
    box.querySelectorAll("[data-wvin]").forEach((el) => el.addEventListener("input", () => {
      const k = el.dataset.wvin;
      if (k === "invert") w.invert = el.checked;
      else { w[k] = Number(el.value) || 0; box.querySelectorAll(`[data-wvin="${k}"]`).forEach((o) => { if (o !== el) o.value = el.value; }); }
      this._waveCanvas();
    }));
    const cv = box.querySelector("#wavecv"), fingers = new Map();
    let drag = null, pinch = null;
    const zoom = (px, f) => { const at = w.start + px * w.spp; w.spp = Math.min(Math.max(0.05, w.spp * f), Math.max(1, (w.mic?.length || 1000) / 100)); w.start = at - px * w.spp; this._waveCanvas(); };
    cv.addEventListener("wheel", (e) => { e.preventDefault(); zoom(e.offsetX, e.deltaY < 0 ? 0.8 : 1.25); }, { passive: false });
    cv.addEventListener("pointerdown", (e) => {
      cv.setPointerCapture(e.pointerId); fingers.set(e.pointerId, e.offsetX);
      if (fingers.size === 2) { const [a, b] = [...fingers.values()]; pinch = { d: Math.abs(a - b) || 1, mid: (a + b) / 2, spp: w.spp, start: w.start }; drag = null; }
      else drag = { x: e.offsetX, start: w.start };
    });
    cv.addEventListener("pointermove", (e) => {
      if (!fingers.has(e.pointerId)) return;
      fingers.set(e.pointerId, e.offsetX);
      if (pinch && fingers.size === 2) { const [a, b] = [...fingers.values()], at = pinch.start + pinch.mid * pinch.spp; w.spp = Math.max(0.05, pinch.spp * pinch.d / (Math.abs(a - b) || 1)); w.start = at - (a + b) / 2 * w.spp; this._waveCanvas(); }
      else if (drag) { w.start = drag.start - (e.offsetX - drag.x) * w.spp; this._waveCanvas(); }
    });
    const up = (e) => { fingers.delete(e.pointerId); if (fingers.size < 2) pinch = null; if (!fingers.size) drag = null; };
    cv.addEventListener("pointerup", up); cv.addEventListener("pointercancel", up);
    this._waveCanvas();
  }

  _waveCanvas() { // three traces over the visible samples: min/max per pixel when zoomed out, lines when zoomed in
    const w = this._wave, cv = this.shadowRoot.querySelector("#wavecv");
    if (!w || !cv) return;
    const dpr = devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
    cv.width = W * dpr; cv.height = H * dpr;
    const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
    if (!w.mic) return;
    const mic = w.mic, ref = w.ref, d = Math.round(w.delay * 16), k = (w.invert ? -1 : 1) * w.gain;
    const echo = (i) => (ref && i - d >= 0 && i - d < ref.length ? k * ref[i - d] : 0);
    const traces = [["mic", "#2196f3", (i) => mic[i]], ["ref", "#ff9800", echo], ["res", "#4caf50", (i) => mic[i] - echo(i)]].filter(([key]) => w.show[key] && (key === "mic" || ref));
    const top = H * 0.62, lanes = { mic: [0, top], ref: [0, top], res: [top, H] };
    let peakMic = 300, peakRef = 300;
    for (let i = 0; i < mic.length; i += 5) { peakMic = Math.max(peakMic, Math.abs(mic[i])); peakRef = Math.max(peakRef, Math.abs(echo(i))); }
    const scale = { mic: peakMic, ref: w.show.mic ? Math.max(peakMic, peakRef) : peakRef, res: peakMic };
    scale.mic = scale.ref; // microphone and speaker on one scale: lined up, they lie on each other
    g.strokeStyle = "rgba(127,127,127,.4)"; g.beginPath(); g.moveTo(0, top); g.lineTo(W, top); g.moveTo(0, (top + H) / 2); g.lineTo(W, (top + H) / 2); g.stroke();
    g.fillStyle = "rgba(127,127,127,.9)"; g.font = "11px sans-serif"; g.fillText("after the subtraction", 6, top + 14);
    for (const [key, color, val] of traces) {
      const [y0, y1] = lanes[key], mid = (y0 + y1) / 2, half = (y1 - y0) / 2 - 6, y = (v) => mid - (v / scale[key]) * half;
      g.save(); g.beginPath(); g.rect(0, y0, W, y1 - y0); g.clip();
      g.strokeStyle = color; g.globalAlpha = 0.85; g.lineWidth = 1; g.beginPath();
      if (w.spp >= 1) {
        for (let x = 0; x < W; x++) {
          const a = Math.floor(w.start + x * w.spp), b = Math.floor(w.start + (x + 1) * w.spp);
          let lo = Infinity, hi = -Infinity;
          for (let i = Math.max(0, a); i < Math.min(mic.length, Math.max(b, a + 1)); i++) { const v = val(i); if (v < lo) lo = v; if (v > hi) hi = v; }
          if (hi >= lo) { g.moveTo(x + 0.5, y(hi)); g.lineTo(x + 0.5, y(lo) + 0.5); }
        }
      } else {
        let first = true;
        for (let i = Math.max(0, Math.floor(w.start)); i < Math.min(mic.length, Math.ceil(w.start + W * w.spp) + 1); i++) {
          const x = (i - w.start) / w.spp;
          if (first) { g.moveTo(x, y(val(i))); first = false; } else g.lineTo(x, y(val(i)));
        }
      }
      g.stroke(); g.restore();
    }
    g.globalAlpha = 1; g.fillStyle = "rgba(127,127,127,.9)"; g.font = "11px sans-serif";
    g.fillText(`${(Math.max(0, w.start) / 16000).toFixed(3)} s · ${(w.spp / 16).toFixed(w.spp < 16 ? 3 : 2)} ms per pixel`, 6, 14);
  }

  // ------------------------------------------------------------------ dialogs (AAC's own, not the browser's)
  dialog({ title = "", text = "", input, multiline = false, buttons = [["ok", "OK", "primary"]] }) { // → {button, value}
    return new Promise((resolve) => { this._dialog = { title, text, input, multiline, buttons, resolve }; this._drawDialog(); });
  }

  async confirm(text, ok = "OK", danger = false) {
    return (await this.dialog({ text, buttons: [["cancel", "Cancel"], ["ok", ok, danger ? "danger" : "primary"]] })).button === "ok";
  }

  async prompt(text, value = "", multiline = false) {
    const r = await this.dialog({ text, input: value ?? "", multiline, buttons: [["cancel", "Cancel"], ["ok", "OK", "primary"]] });
    return r.button === "ok" ? r.value : null;
  }

  async alert(text) { await this.dialog({ text }); }

  _drawDialog() {
    this.shadowRoot.querySelector("#dlg")?.remove();
    const d = this._dialog;
    if (!d) return;
    const box = document.createElement("div");
    box.id = "dlg";
    box.innerHTML = `<div class="dlgbox" role="dialog">${d.title ? `<h3>${esc(d.title)}</h3>` : ""}<p>${esc(d.text).replace(/\n/g, "<br>")}</p>
      ${d.input === undefined ? "" : d.multiline ? `<textarea id="dlginput" rows="5">${esc(d.input)}</textarea>` : `<input id="dlginput" value="${esc(d.input)}">`}
      <div class="row" style="justify-content:flex-end;gap:8px;margin-top:12px">${d.buttons.map(([v, l, k]) => `<button class="${k || ""}" data-dlg="${esc(v)}">${esc(l)}</button>`).join("")}</div></div>`;
    this.shadowRoot.appendChild(box);
    const field = box.querySelector("#dlginput");
    (field || box.querySelector("button.primary, button.danger, button:last-child"))?.focus();
    if (field?.select) field.select();
  }

  _closeDialog(button) {
    const d = this._dialog;
    if (!d) return;
    const value = this.shadowRoot.querySelector("#dlginput")?.value;
    this._dialog = null;
    this.shadowRoot.querySelector("#dlg")?.remove();
    d.resolve({ button, value });
  }

  _domainMenu() { // the domain: switch, new, copy, rename, permissions, save and load a file, delete
    const s = this.state, name = esc(s.domain.name);
    const item = (act, label, cls = "") => `<div class="item ${cls}" data-domact="${act}">${label}</div>`;
    const menu = !this.domOpen ? "" : `<div class="dmenu">
      ${s.domains.map((d) => `<div class="item ${d.id === s.active ? "on" : ""}" data-domgo="${esc(d.id)}">${esc(d.name)}</div>`).join("")}
      <hr>${item("new", "New domain…")}${item("copy", `Copy of "${name}"…`)}${item("rename", `Rename "${name}"…`)}
      ${item("permissions", `Permissions of "${name}"…`)}${item("save", `Save "${name}" to a file`)}${item("load", `Load a file into "${name}"…`)}
      ${s.domains.length > 1 ? item("delete", `Delete "${name}"…`, "danger") : ""}</div>`;
    return `<button class="dbtn" id="dombtn" title="Domain: a whole AAC setup (cards, wires, their data); one runs at a time">${name} ▾</button>${menu}
      <input type="file" id="importfile" accept=".json,application/json" hidden>`;
  }

  async _domainAction(act) {
    const s = this.state, name = s.domain.name;
    this.domOpen = false;
    try {
      if (act === "new" || act === "copy") {
        const n = await this.prompt(act === "new" ? "Name of the new domain (it starts empty)" : "Name of the copy (cards, wires and their data)", act === "copy" ? `${name} (copy)` : "");
        if (!n?.trim()) return this.render();
        const { id } = await this.ws(act === "new" ? "aac/domain/create" : "aac/domain/clone", { name: n.trim() });
        await this.ws("aac/domain/switch", { domain_id: id });
        this._els = {}; await this._load(); return this.go(act === "new" ? "nodes" : "card", null, null);
      }
      if (act === "rename") {
        const n = await this.prompt("New name", name);
        if (n?.trim() && n.trim() !== name) await this.ws("aac/domain/rename", { domain_id: s.domain.id, name: n.trim() });
        await this._load(); return this.render();
      }
      if (act === "delete") {
        if (!(await this.confirm(`Delete "${name}" with all its cards and their data (chats, lists, memories)? This cannot be undone.`, "Delete", true))) return this.render();
        await this.ws("aac/domain/delete", { domain_id: s.domain.id });
        this._els = {}; await this._load(); return this.go("card", null, null);
      }
      if (act === "permissions") return this.go("permissions");
      if (act === "save") {
        const { setup } = await this.ws("aac/setup/export"), a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([JSON.stringify(setup, null, 1)], { type: "application/json" }));
        a.download = `aac-${setup.domain.replace(/\W+/g, "-").toLowerCase()}-${new Date().toISOString().slice(0, 10)}.json`;
        a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
        return this.render();
      }
      if (act === "load") { this.render(); this.shadowRoot.querySelector("#importfile")?.click(); }
    } catch (e) { this.alert(e.message || e); this.render(); }
  }

  async _loadFile(file) {
    if (!file || !(await this.confirm(`Load "${file.name}" into this domain? Its cards and wires replace the current ones (Undo goes back).`, "Load"))) return;
    try { await this.ws("aac/setup/import", { setup: JSON.parse(await file.text()) }); } catch (err) { this.alert(`Could not load it: ${err.message || err}`); return; }
    this.sel = null; this._els = {}; await this._load(); this.go("nodes");
  }

  _permSummary(c, fs) { // a folded card's permissions in a few words
    return fs.map(([k, f]) => { const v = c.settings[k] ?? f.default;
      if (f.type === "bool") return `${v ? "✓" : "✗"} ${f.title.toLowerCase()}`;
      if (f.type === "access") { const n = Object.values(v || {}); return `${n.filter((x) => x === "read").length} read-only, ${n.filter((x) => x === "full").length} full`; }
      return `${f.title}: ${v}`; }).join(" · ");
  }

  _permissionsView() { // every setting a card marks as a permission, all cards of this domain on one page
    const cards = this.state.domain.cards.map((c) => [c, Object.entries(this._type(c.type)?.settings || {}).filter(([, f]) => f.permission)]).filter(([, fs]) => fs.length);
    return `${this._header(`Permissions of "${this.state.domain.name}"`, tip("What this domain's cards may do, gathered from their settings (the same fields as in Nodes): which entities AAC may see or switch, what the Node editor may change, which cards models may use as tools."))}
      <div class="content">${cards.map(([c, fs]) => { const open = this.permOpen === c.id; return `<div class="card"><h3 class="permhead" data-permopen="${esc(c.id)}">${open ? "▾" : "▸"} ${esc(c.title)} <span class="hint">${esc(this._type(c.type)?.title || c.type)} · ${esc(this._permSummary(c, fs))}</span></h3>${open ? fs.map((e) => this._field(c, e)).join("") : ""}</div>`; }).join("") || '<p class="hint">No card here has permissions.</p>'}
      <p class="hint">Fields save when you leave them.</p></div>`;
  }

  _redrawNav() { const n = this.shadowRoot.querySelector("nav"); if (n) n.outerHTML = this._nav(); }

  async _navAction(key, action) { // an action on a card's sidebar entry: the card does it (ui_call "nav_action")
    const [card, entry] = key.split("|"), n = this.nav.find((x) => x.card === card);
    const find = (list) => list.flatMap((e) => [e, ...find(e.children || [])]);
    const spec = find(n?.nav?.entries || []).find((e) => e.id === entry), act = (spec?.actions || []).find((a) => a.id === action) || {};
    this.navMenu = null;
    let value;
    if (act.ask && (value = await this.prompt(act.ask, spec?.title || "")) === null) return this._redrawNav();
    if (act.confirm && !(await this.confirm(act.confirm, act.label, true))) return this._redrawNav();
    try { await this.ws("aac/card/ui", { card, action: "nav_action", args: { entry, action, value } }); } catch (err) { this.alert(err.message || err); }
    this.nav = (await this.ws("aac/nav")).nav;
    if (this.view.kind === "card" && this.view.card === card && this.view.id === entry && ["delete", "archive"].includes(action)) this.go("card", card, null);
    else this._redrawNav();
  }

  _failing() { return (this.state?.models || []).filter((m) => m.failing).map((m) => m.name); }

  _foldMode(key) { return this.look?.folds?.[key] || (key === "chat" ? "arrow" : "auto"); } // how a sidebar group folds

  _nav() { // the cards' pages and their entries, then Nodes and the core's settings
    const s = this.state, v = this.view, folds = this._folds();
    const on = (kind, card = null, id = null) => (v.kind === kind && v.card === card && (v.id || null) === id ? "on" : "");
    const item = (label, kind, card = null, id = null, indent = 0) =>
      `<div class="item ${on(kind, card, id)}" data-go="${kind}" data-card="${esc(card || "")}" data-id="${esc(id || "")}" ${indent ? `style="padding-left:${12 + indent * 14}px"` : ""}>${esc(label)}</div>`;
    // a group: "auto" opens while one of its pages is in use (lit) and closes when you go elsewhere; "arrow" stays as the arrow left it
    const group = (key, title, active, body, add = "", go = "") => {
      const auto = this._foldMode(key) === "auto", open = auto ? active || this.navPeek === key : !folds[key];
      return `<h4 class="fold ${auto && active ? "lit" : ""}" ${go}>${auto ? "" : `<span class="arrow" data-fold="${esc(key)}" title="Keep it open or closed">${open ? "▾" : "▸"}</span> `}${esc(title.toUpperCase())}${add}</h4>${open ? body : ""}`;
    };
    const acts = (n, e) => { // an entry's actions (the card says which): icons at once, the rest in its ⋯ menu
      const all = e.actions || [], key = `${n.card}|${e.id}`;
      if (!all.length) return "";
      const open = this.navMenu === key;
      return `<span class="navacts ${open ? "open" : ""}">${all.filter((a) => a.icon).map((a) => `<button class="small" data-navact="${esc(a.id)}" data-entry="${esc(key)}" title="${esc(a.label)}">${esc(a.icon)}</button>`).join("")}${all.some((a) => !a.icon) ? `<button class="small" data-navmore="${esc(key)}" title="More">⋯</button>` : ""}</span>
        ${open ? `<div class="navmenu">${all.filter((a) => !a.icon).map((a) => `<div class="item" data-navact="${esc(a.id)}" data-entry="${esc(key)}">${esc(a.label)}</div>`).join("")}</div>` : ""}`;
    };
    const entries = (n, list, depth = 1) => list.map((e) => `${item(e.title, "card", n.card, e.id, depth).replace(/<\/div>$/, `${acts(n, e)}</div>`)}${e.children?.length ? entries(n, e.children, depth + 1) : ""}`).join("");
    const groups = this.nav.filter((n) => n.nav).map((n) => group(n.card, n.title, v.kind === "card" && v.card === n.card,
      entries(n, n.nav.entries || []) || '<div class="item hint" style="cursor:default">nothing yet</div>',
      n.nav.add ? `<button class="small" data-navadd="${esc(n.card)}" title="${esc(n.nav.add)}">+</button>` : "",
      `data-go="card" data-card="${esc(n.card)}" data-id="" title="Open its page"`)).join("");
    const single = this.nav.filter((n) => !n.nav).map((n) => item(n.title, "card", n.card)).join("");
    const failing = this._failing();
    const settings = group("settings", "Settings", ["audio", "models", "appearance"].includes(v.kind),
      `${item("Audio", "audio")}${item("Models", "models").replace('data-go="models"', `data-go="models" ${failing.length ? "data-warn" : ""}`)}${item("Appearance", "appearance")}`, "",
      `data-go="${["audio", "models", "appearance"].includes(v.kind) ? v.kind : failing.length ? "models" : "audio"}" data-peek="settings" ${failing.length ? `data-alarm title="Not answering: ${esc(failing.join(", "))}"` : ""}`);
    return `<nav><div class="top"><button class="menu" id="habtn" title="Home Assistant's menu">⌂</button><b>AAC</b><span>v${esc(s.version)}</span></div>
      <div class="dom">${this._domainMenu()}</div>
      <div class="scroll">${groups}${single ? `<h4>MORE</h4>${single}` : ""}</div>
      <div class="bottom">${item("Nodes", "nodes")}${settings}</div></nav>`;
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
    const rows = look.skins.map((k) => `<label class="check" style="display:flex;gap:8px;align-items:flex-start;margin:8px 0;justify-content:flex-start;text-align:left"><input type="radio" style="width:auto;flex:0 0 auto;margin-top:3px" name="skin" data-skin="${esc(k.id)}" ${k.id === look.skin ? "checked" : ""}>
      <span><b>${esc(k.name)}</b> <span class="hint">${esc(k.source)}${k.author ? ` · ${esc(k.author)}` : ""}</span><br><span class="hint">${esc(k.description)}</span></span></label>`).join("");
    return `${this._header("Appearance", tip("How the panel looks, the same on every device. Own skins: a CSS file in config/aac/skins (a comment header with name, description, author; HA's theme variables inside :host, e.g. --primary-color)."))}
      <div class="content"><div class="card"><h3 style="margin-top:0">Skin</h3>${rows}</div>
      <div class="card"><h3 style="margin-top:0">Sidebar ${tip("How each group in the sidebar folds. Opens while in use: click it, it opens and is lit; go elsewhere and it closes. Arrow: the arrow keeps it open or closed.")}</h3>
        ${[...this.nav.filter((n) => n.nav).map((n) => [n.card, n.title]), ["settings", "Settings"]].map(([key, title]) => `<div class="row"><span style="flex:0 0 170px">${esc(title)}</span>
          <select data-foldmode="${esc(key)}"><option value="auto" ${this._foldMode(key) === "auto" ? "selected" : ""}>Opens while in use</option><option value="arrow" ${this._foldMode(key) === "arrow" ? "selected" : ""}>Arrow keeps it open or closed</option></select></div>`).join("")}</div></div>`;
  }

  async _voicesOf(engine) { // an engine's voices for each home language (HA's own tts commands), kept per engine
    if (!engine || (this._vc ||= {})[engine]) return this._vc?.[engine];
    this._vc[engine] = { loading: true };
    const found = {};
    try {
      const langs = (await this.ws("tts/engine/get", { engine_id: engine })).provider?.supported_languages || [];
      for (const { code } of this.audio?.languages || []) { // every variant of the language (en_GB, en_US …)
        const locales = langs.filter((l) => l.toLowerCase().split(/[-_]/)[0] === code), voices = [];
        for (const loc of locales) for (const v of (await this.ws("tts/engine/voices", { engine_id: engine, language: loc })).voices || []) voices.push({ ...v, locale: loc, label: locales.length > 1 ? `${v.name} · ${loc}` : v.name });
        found[code] = { locale: locales[0] || null, voices };
      }
    } catch (e) { found.error = e.message || String(e); }
    this._vc[engine] = found;
    if (this.view.kind === "nodes") this.render();
    return found;
  }

  _timingCard() { // where the time of a spoken turn goes (core.audio.mark)
    const rows = this.audio?.timing || [], steps = ["text ready", "first sentence", "voice ready", "playing"];
    if (!rows.length) return "";
    const cell = (v, prev) => (v === undefined ? '<td class="hint">–</td>' : `<td>${v.toFixed(1)} s${prev !== undefined ? `<div class="hint">+${(v - prev).toFixed(1)}</div>` : ""}</td>`);
    return `<div class="card"><h3 style="margin-top:0">Timing of the last spoken turns ${tip("Seconds after you stopped speaking (the Audio clipper's pause is before this). Under each: how long that step took.")}</h3>
      <table style="width:100%;font-size:13px;border-collapse:collapse"><tr class="hint"><td>When</td>${steps.map((x) => `<td>${esc(x)}</td>`).join("")}</tr>
      ${rows.map((r) => `<tr><td>${esc(new Date(r.at * 1000).toLocaleTimeString())}<div class="hint">${esc(r.source)}</div></td>${steps.map((x, i) => cell(r.stages[x], i ? r.stages[steps[i - 1]] : 0)).join("")}</tr>`).join("")}</table></div>`;
  }

  _languagesCard() {
    const a = this.audio || {}, names = a.language_names || {};
    const rows = (a.languages || []).map(({ code, name }, i) => {
      return `<div class="row"><span class="t"><b>${esc(name)}</b> <span class="hint">${esc(code)}${i === 0 ? " · main" : ""}</span></span>
        ${i > 0 ? `<button class="small" data-langup="${i}" title="Make it the main language">↑</button>` : ""}<button class="small danger" data-langdel="${i}">✕</button></div>`;
    }).join("");
    const ha = (a.languages_ha || []).map((l) => l.name).join(", ");
    return `<div class="card"><h3 style="margin-top:0">Home languages ${tip("The languages spoken here, main first. Speech to text listens for them (one: that one; several: detected among them), Text to speech speaks each in the voice set in its settings, templates read {{ languages }}. A card can still set its own.")}</h3>
      ${rows}
      <div class="row" style="margin-top:8px"><select id="langadd"><option value="">Add a language …</option>${Object.entries(names).sort((x, y) => x[1].localeCompare(y[1])).filter(([c]) => !(a.languages || []).some((l) => l.code === c)).map(([c, n]) => `<option value="${esc(c)}">${esc(n)}</option>`).join("")}</select>
        ${a.languages_own ? `<button class="small" id="langha" title="Use what Home Assistant says about the home">As in Home Assistant (${esc(ha)})</button>` : `<span class="hint">From Home Assistant (its language and country)</span>`}</div></div>`;
  }

  _audioView() {
    const a = this.audio || { settings: {} }, st = a.settings || {};
    const sttItems = (a.stt_entities || []).map((e) => [e, e]);
    const order = (st.stt || []).filter((e) => e.startsWith("stt."));
    return `${this._header("Audio", tip("The satellites, how AAC hears (speech to text, in this order: the first that answers) and speaks (an HA voice engine). Shared by all domains; the audio cards in Nodes decide what happens with it."))}
      <div class="content"><h3>Satellites</h3><div id="meters">${this._meters()}</div>
      ${(st.known || []).filter((k) => !(a.sources || []).some((s) => s.id === k.id)).map((k) => `<div class="row"><span class="hint">${esc(k.name)} · offline</span><button class="small danger" data-forget="${esc(k.id)}" style="margin-left:auto" title="Remove it from AAC (and its media player in HA)">Forget</button></div>`).join("")}
      ${this._languagesCard()}
      ${this._timingCard()}
      <div class="card"><h3 style="margin-top:0">HA's speech to text (in order) ${tip("Home Assistant's engines, tried after the models in a Speech to text card's list (that list is in the card's settings in Nodes).")}</h3>
        ${order.map((id, i) => `<div class="row"><span class="t">${esc((sttItems.find((x) => x[0] === id) || [id, id])[1])}</span>
          <button class="small" data-sttup="${i}">↑</button><button class="small danger" data-sttdel="${i}">✕</button></div>`).join("") || '<p class="hint">None yet.</p>'}
        <div class="row" style="margin-top:8px"><select id="sttadd"><option value="">Add …</option>${sttItems.filter((x) => !order.includes(x[0])).map(([id, l]) => `<option value="${esc(id)}">${esc(l)}</option>`).join("")}</select></div>
        <h3>Default voice engine ${tip("Used by cards that speak and set no engine of their own (and by Test voice). The voice per language is set in the Text to speech card.")}</h3><select id="ttsset"><option value="">—</option>${(a.tts_entities || []).map((e) => `<option ${e === st.tts ? "selected" : ""}>${esc(e)}</option>`).join("")}</select>
        <h3>Volume: <span id="volval">${esc(st.volume ?? 40)}</span> %</h3><input type="range" id="volume" min="0" max="100" step="5" value="${esc(st.volume ?? 40)}">
        <p class="hint">0 = AAC never speaks aloud.</p></div></div>`;
  }

  // ------------------------------------------------------------------ models
  _models() { // on top only the models; servers below, each with what it offers and AAC's models on it (their settings)
    const srv = (id) => (this.servers || []).find((x) => x.id === id);
    const rows = this.state.models.map((m) => `<tr><td><b>${esc(m.name)}</b></td><td class="hint">${esc(srv(m.server)?.name || "")}</td>
      <td class="hint">${m.follow ? `follows what is loaded · now <b>${esc(m.model)}</b>` : esc(m.model)}</td>
      <td>${(m.tags || []).map((t) => `<code>${esc(t)}</code>`).join(" ")}</td>
      <td>${m.failing ? `<span style="color:var(--error-color,#db4437)">✗ not answering${m.up ? " (tried again with the next request)" : " (skipped for a minute)"}</span>` : "✓"}<div class="hint">${m.busy || 0}/${m.parallel || 1} running${m.waiting ? ` · ${m.waiting} waiting` : ""}</div></td></tr>`).join("");
    return `${this._header("Models", tip("The models AAC may use, for all domains (each card that uses models picks from these by what they can do, their tags). They are set up below, on their servers: any OpenAI-compatible server works (llama.cpp, Ollama, vLLM, LM Studio, cloud APIs); a llama-server router also lists, loads, downloads and deletes its models here."))}
      <div class="content"><div class="card models"><table style="width:100%;border-collapse:collapse">${rows || '<tr><td class="hint">No models yet: add a server below, then its models.</td></tr>'}</table></div>
      ${this.srvForm === "new" ? this._srvForm(null) : '<button data-srvadd="1" style="margin-bottom:12px">+ Add a server</button>'}
      ${this._servers()}</div>`;
  }

  _srvForm(s) {
    const f = s || {}, key = s ? s.id : "new";
    return `<div class="card" data-srvform="${esc(key)}"><h3 style="margin-top:0">${s ? "Change the server" : "Add a server"}</h3>
      <label class="hint">Name</label><input data-sf="name" value="${esc(f.name || "")}" placeholder="e.g. PC">
      <label class="hint">Address</label><input data-sf="base_url" value="${esc(f.base_url || "http://192.168.1.x:8080/v1")}">
      <label class="hint">API key (only for servers that need one)</label><input type="password" data-sf="api_key" placeholder="${f.has_key ? "kept" : ""}">
      <div style="margin-top:10px;display:flex;gap:8px"><button class="primary" data-srvsave="${esc(key)}">Save</button><button data-srvcancel="1">Cancel</button></div></div>`;
  }

  _servers() {
    if (!this.servers) return '<div class="card"><p class="hint">Asking the servers …</p></div>';
    return this.servers.map((s) => `<div class="card"><div class="row"><h3 style="margin:0">${esc(s.name)}</h3><span class="hint">${esc(s.base_url)}</span>
        <span style="margin-left:auto"></span><button class="small" data-srvedit="${esc(s.id)}">Edit</button><button class="small danger" data-srvdel="${esc(s.id)}">Delete</button></div>
      ${this.srvForm === s.id ? this._srvForm(s) : ""}
      <div data-srvlist="${esc(s.id)}">${this._serverRows(s)}</div>
      <h4 style="margin:12px 0 4px">AAC's models here ${tip("What AAC uses from this server: each with its name in AAC, the server's model it asks for (or, on a router, whatever is loaded), what it can do (its tags; cards pick models by them) and how many requests it runs at once.")}</h4>
      ${s.uses.map((u) => (this.amForm === u.id ? this._amForm(s, u) : `<div class="row"><b>${esc(u.name)}</b><span class="hint">${u.follow ? `follows what is loaded · now ${esc(u.model)}` : esc(u.model)} · ${u.tags.map((t) => `<code>${esc(t)}</code>`).join(" ")} · runs ${esc(u.parallel)}</span>
        <span style="margin-left:auto"></span><button class="small" data-amedit="${esc(u.id)}">Edit</button><button class="small danger" data-amdel="${esc(u.id)}">Remove</button></div>`)).join("") || '<p class="hint">None yet.</p>'}
      ${this.amForm === `new:${s.id}` ? this._amForm(s, null) : `<button class="small" data-amadd="${esc(s.id)}" title="Add one of this server's models to AAC (or type one it does not list${s.router ? ", or download one" : ""})">+ model</button>`}</div>`).join("");
  }

  _amForm(s, m) { // one of AAC's models on server s (m null: a new one)
    const key = m ? m.id : `new:${s.id}`, f = m || { follow: !!s.router, parallel: 1, tags: [] };
    const names = s.router ? (s.models || []).map((x) => x.id) : (s.offered || []);
    const opts = [...new Set([...names, ...(f.model ? [f.model] : [])])];
    const download = !m && s.router ? `<label class="hint">Download one to the server ${tip("From Hugging Face: user/repo:quant. It appears above when it is done (progress there); then add it here.")}</label>
      <div class="row"><input id="dl-${esc(s.id)}" placeholder="user/repo:quant, e.g. unsloth/gemma-4-12B-it-qat-GGUF:Q4_K_XL">
        <label class="check" style="font-size:13px" title="The image part, for models that see"><input type="checkbox" id="dlmm-${esc(s.id)}">vision</label>
        <label class="check" style="font-size:13px" title="A draft head that makes writing faster (MTP), if the repo has one"><input type="checkbox" id="dlmtp-${esc(s.id)}">mtp</label>
        <button class="small" data-rdl="${esc(s.id)}">Download</button></div><hr style="border:none;border-top:1px solid var(--divider-color);margin:10px 0">` : "";
    return `<div class="card" data-amform="${esc(key)}" style="margin:6px 0">${download}
      <label class="hint">Name in AAC</label><input data-am="name" value="${esc(f.name || "")}" placeholder="e.g. Main PC">
      ${s.router ? `<label class="check" style="font-size:13px"><input type="checkbox" data-am="follow" ${f.follow ? "checked" : ""}>Follows what is loaded ${tip("AAC asks for whatever model the router has loaded now (tick another one as loaded above and AAC uses it); with none loaded, the one it used last.")}</label>` : ""}
      <label class="hint">The server's model ${s.router ? "(when nothing is loaded, or without follow)" : ""}</label><input data-am="model" list="ml-${esc(key)}" value="${esc(f.model || "")}" placeholder="pick or type"><datalist id="ml-${esc(key)}">${opts.map((o) => `<option value="${esc(o)}">`).join("")}</datalist>
      <label class="hint">Runs at once ${tip("How many requests the server works on side by side (llama-server: its slots, --parallel). More wait in the queue of cards that allow it.")}</label><input data-am="parallel" type="number" min="1" value="${esc(f.parallel || 1)}" style="width:80px">
      <label class="hint">What it can do ${tip("Cards use only models with the tag they need: a Model text, Memory embeddings, Speech to text speech to text. None ticked: guessed from the name.")}</label>
      <div class="row" style="flex-wrap:wrap;gap:4px 12px">${(this.state.model_tags || []).map((t) => `<label class="check" style="font-size:13px"><input type="checkbox" data-amtag="${esc(t)}" ${(f.tags || []).includes(t) ? "checked" : ""}>${esc(t)}</label>`).join("")}</div>
      <div style="margin-top:10px;display:flex;gap:8px"><button class="primary small" data-amsave="${esc(key)}">Save</button><button class="small" data-amcancel="1">Cancel</button></div></div>`;
  }

  _serverRows(s) {
    if (s.error) return `<p style="color:var(--error-color,#db4437)">Not answering: ${esc(s.error)}</p>`;
    if (!s.router) return `<p class="hint">${(s.offered || []).length ? `It offers: ${s.offered.map(esc).join(", ")}` : "It lists no models (add them with + model)."} Not a llama-server router: one fixed model.</p>`;
    const gb = (b) => (b ? `${(b / 1e9).toFixed(1)} GB` : "");
    const rows = (s.models || []).map((m) => {
      const st = m.status, on = ["loaded", "loading", "sleeping"].includes(st);
      const users = s.uses.filter((u) => u.model === m.id && (!u.follow || on));
      const facts = [st === "downloading" || st === "loading" ? `${st} ${m.progress ?? 0} %` : st + (m.failed ? " (failed to start)" : ""),
        m.quant, m.params ? `${(m.params / 1e9).toFixed(1)} B` : "", gb(m.size),
        m.context ? `context ${m.context.toLocaleString()}${m.context_max ? ` of ${m.context_max.toLocaleString()}` : ""}` : "",
        m.tools ? "tools" : "", (m.inputs || []).filter((x) => x !== "text").join(", "),
        m.speed && (m.speed.read || m.speed.write) ? `reads ${m.speed.read} · writes ${m.speed.write} tokens/s` : ""].filter(Boolean).join(" · ");
      const b = m.bench, benching = (this._benching || new Set()).has(`${s.id} ${m.id}`);
      const benchLine = b ? `<div class="hint">Bench ${esc(new Date(b.at).toLocaleString([], { dateStyle: "short", timeStyle: "short" }))}: reads <b>${esc(b.read)}</b> · writes <b>${esc(b.write)}</b> tokens/s <span title="tokens read, tokens written, seconds">(${esc(b.prompt)} in, ${esc(b.written)} out, ${esc(b.seconds)} s)</span></div>` : "";
      const act = (a, label, cls = "", title = "") => `<button class="small ${cls}" data-ract="${a}" data-srv="${esc(s.id)}" data-rname="${esc(m.id)}" ${title ? `title="${esc(title)}"` : ""}>${label}</button>`;
      const del = !m.can_remove ? `<button class="small" disabled title="Set up on the server itself (its preset file or models folder): it can't be deleted from here, only downloaded models can">Delete</button>`
        : st === "loaded" ? '<button class="small" disabled title="Unload it first">Delete</button>' : act("delete", "Delete", "danger");
      const buttons = st === "downloading" ? act("unload", "Pause") : st === "incomplete" ? `${act("download", "Resume")} ${act("delete", "Delete", "danger")}`
        : `${on && st !== "loading" ? (benching ? '<button class="small" disabled>Benching …</button>' : act("bench", "Bench", "", "A short fixed test: reads ~1,500 tokens, writes 256; the result is kept")) : ""} ${del}`;
      const tick = st === "downloading" || st === "incomplete" ? "" : `<input type="checkbox" data-rload="1" data-srv="${esc(s.id)}" data-rname="${esc(m.id)}" ${on ? "checked" : ""} ${st === "loading" ? "disabled" : ""} title="Loaded (with one model at a time, ticking another unloads this one)">`;
      return `<tr><td style="width:28px;vertical-align:top">${tick}</td><td><b>${esc(m.id)}</b>${users.length ? ` <span class="hint">← ${users.map((u) => esc(u.name)).join(", ")}</span>` : ""}<div class="hint">${esc(facts)}</div>${benchLine}</td><td style="text-align:right;white-space:nowrap;vertical-align:top">${buttons}</td></tr>`;
    }).join("");
    return `<table style="width:100%;border-collapse:collapse;margin-top:6px"><tr><th class="hint" style="text-align:left;font-weight:normal">loaded</th><th></th><th></th></tr>${rows || '<tr><td></td><td class="hint">It offers no models.</td><td></td></tr>'}</table>`;
  }

  async _loadServers() {
    try { this.servers = (await this.ws("aac/models/servers")).servers; } catch (e) { this.servers = []; }
    const lists = [...this.shadowRoot.querySelectorAll("[data-srvlist]")];
    if (lists.length !== this.servers.length || lists.some((el) => !this.servers.some((x) => x.id === el.dataset.srvlist))) { if (this.view.kind === "models") this.render(); return; }
    lists.forEach((el) => { el.innerHTML = this._serverRows(this.servers.find((x) => x.id === el.dataset.srvlist)); });
  }

  async _router(server_id, action, name, sidecars) {
    if (action === "delete" && !(await this.confirm(`Delete ${name} from the server's disk?`))) return;
    const key = `${server_id} ${name}`;
    if (action === "bench") { (this._benching ||= new Set()).add(key); this._loadServers(); }
    try { await this.ws("aac/models/router", { server_id, action, name, ...(sidecars ? { sidecars } : {}) }); }
    catch (e) { this.alert(`${action}: ${e.message || e}`); }
    finally { this._benching?.delete(key); }
    await this._loadServers();
  }

  async _modelsClick(t) { // the Models page's buttons (its lists are redrawn every few seconds: one listener for all)
    const d = t.dataset, q = (sel) => this.shadowRoot.querySelector(sel);
    const done = async () => { await this._load(); this.servers = null; this.render(); await this._loadServers(); };
    if (d.rload) this._router(d.srv, t.checked ? "load" : "unload", d.rname);
    else if (d.ract) this._router(d.srv, d.ract, d.rname);
    else if (d.rdl) {
      const name = q(`#dl-${CSS.escape(d.rdl)}`).value.trim();
      if (!name) { this.alert("Type a Hugging Face model: user/repo:quant"); return; }
      this._router(d.rdl, "download", name, [...(q(`#dlmm-${CSS.escape(d.rdl)}`).checked ? ["mmproj"] : []), ...(q(`#dlmtp-${CSS.escape(d.rdl)}`).checked ? ["mtp"] : [])]);
      q(`#dl-${CSS.escape(d.rdl)}`).value = "";
    } else if (d.srvadd) { this.srvForm = "new"; this.render(); }
    else if (d.srvedit) { this.srvForm = this.srvForm === d.srvedit ? null : d.srvedit; this.render(); }
    else if (d.srvcancel) { this.srvForm = null; this.render(); }
    else if (d.srvsave) {
      const box = q(`[data-srvform="${CSS.escape(d.srvsave)}"]`), v = (k) => box.querySelector(`[data-sf="${k}"]`).value.trim();
      const server = { name: v("name") || v("base_url").split("//").pop().split("/")[0], base_url: v("base_url"), ...(v("api_key") ? { api_key: v("api_key") } : {}), ...(d.srvsave !== "new" ? { id: d.srvsave } : {}) };
      if (!server.base_url) { this.alert("Type the server's address"); return; }
      await this.ws("aac/servers/save", { server }); this.srvForm = null; await done();
    } else if (d.srvdel) {
      const s = this.servers.find((x) => x.id === d.srvdel);
      if (await this.confirm(`Delete the server ${s.name}${s.uses.length ? ` and AAC's ${s.uses.length} model(s) on it` : ""}?`)) { await this.ws("aac/servers/delete", { server_id: d.srvdel }); await done(); }
    } else if (d.amadd) { this.amForm = `new:${d.amadd}`; this.render(); }
    else if (d.amedit) { this.amForm = d.amedit; this.render(); }
    else if (d.amcancel) { this.amForm = null; this.render(); }
    else if (d.amsave) {
      const box = q(`[data-amform="${CSS.escape(d.amsave)}"]`), get = (k) => box.querySelector(`[data-am="${k}"]`);
      const isNew = d.amsave.startsWith("new:"), serverId = isNew ? d.amsave.slice(4) : this.state.models.find((m) => m.id === d.amsave).server;
      const model = { name: get("name").value.trim() || get("model").value.trim(), model: get("model").value.trim(), server: serverId,
        follow: !!get("follow")?.checked, parallel: Math.max(1, Number(get("parallel").value) || 1),
        tags: [...box.querySelectorAll("[data-amtag]")].filter((x) => x.checked).map((x) => x.dataset.amtag), ...(isNew ? {} : { id: d.amsave }) };
      if (!model.model && !model.follow) { this.alert("Pick or type the server's model"); return; }
      if (!model.name) { this.alert("Give it a name"); return; }
      await this.ws("aac/models/save", { model }); this.amForm = null; await done();
    } else if (d.amdel) {
      if (await this.confirm("Remove this model from AAC? (The server keeps it.)")) { await this.ws("aac/models/delete", { model_id: d.amdel }); await done(); }
    }
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
    const info = t ? `<b>${esc(t.title)}</b> <span class="hint">v${esc(t.version)}</span><p style="margin:6px 0">${esc(t.help)}</p>${ports("Takes", t.inputs)}${ports("Outputs", t.outputs)}${Object.keys(t.functions || {}).length ? `<label>Functions (for models: For models on the card)</label>${Object.entries(t.functions).map(([n, f]) => `<div class="fn"><code>${esc(n)}(${Object.entries(f.args || {}).map(([a, x]) => `${esc(a)}${x.required ? "" : "?"}`).join(", ")})</code> ${esc(f.help || "")}</div>`).join("")}` : ""}<p class="hint">Click in the grid to place it. <button class="small" id="unpick" title="Put it back (Esc, or click the type again)">Cancel</button></p>`
      : '<span class="hint">Pick a card type above, then click in the grid to place it.</span>';
    const slots = ["1", "2", "3"].map((n) => `<button class="small" data-slot="${n}" title="Save the setup to slot ${n} (Shift-click: load it)">${d.slots[n] ? "●" : "○"} ${n}</button>`).join("");
    return `<div style="display:flex;flex-direction:column;height:100%">${this._header("Nodes", `${tip("Cards are placed from the left, wired by dragging from an output dot to an input dot of the same colour. Click a wire and press Delete (or the button) to remove it. A model's tools are the cards wired from its output and back into it. Cards never lie on each other: a dropped or new card goes into its column below what it would cover, and a card that grows pushes the ones under it down. The round buttons (top right): Select (dashed square) draws a frame around cards and adds or removes cards by a click (or Ctrl+click and Ctrl+drag without it, a long press on a phone); drag one of them and they all move. Cut (scissors) removes every wire a drawn line crosses. Esc turns them off. The bin (or Del) removes the selected cards or wire.")}
        <button class="small" id="undo" ${d.undo ? "" : "disabled"}>Undo</button><button class="small" id="redo" ${d.redo ? "" : "disabled"} title="What the last Undo took back">Redo</button><button class="small" id="fit">Fit</button><button class="small ${this.showValues ? "on" : ""}" id="values" title="The value pool: what scripts can read now (card.value), live">Values</button><button class="small" id="arrange" title="Put the cards in slots: a column per step in the flow, a card's tools under it (Undo goes back)">Arrange</button>${slots}<button class="small danger" id="stop">Send stop</button>`)}
      <div class="nodes ${this.palOpen ? "" : "nopal"} ${this.sideOpen ? "" : "noside"}"><div class="palette"><div class="types">${cats}</div><div class="info">${info}</div></div>
        <div class="grip" data-grip="pal" data-target=".palette" title="Drag: width (double-click: back to normal)"></div>
        <div class="grid ${this.tool ? `tool-${this.tool}` : ""}" id="grid"><div class="gridtools">${[["select", ICON.select, "Select: drag a frame around cards, or click cards to add or remove them; drag one of them and they all move"], ["cut", ICON.cut, "Cut wires: draw a line across the wires that should go (Undo brings them back)"]].map(([k, icon, t]) => `<button class="${this.tool === k ? "on" : ""}" data-tool="${k}" title="${t}">${icon}</button>`).join("")}<button id="delsel" title="Remove the selected cards or wire (Del; Undo brings them back)" ${this._picked().size || this.selWire ? "" : "disabled"}>${ICON.bin}</button></div><svg id="svg"><g id="world"><g id="wires"></g><g id="cards"></g></g></svg><div class="mons"></div>${this._legend()}
          <button class="edge left" id="paltog" title="${this.palOpen ? "Hide" : "Show"} the card types">${this.palOpen ? "‹" : "›"}</button>
          <button class="edge right" id="sidetog" title="${this.sideOpen ? "Hide" : "Show"} the settings">${this.sideOpen ? "›" : "‹"}</button></div>
        <div class="grip" data-grip="side" data-target=".side" data-side="left" title="Drag: width (double-click: back to normal)"></div>
        <div class="side">${this._side()}</div></div></div>`;
  }

  _halves(x, y, port, kinds) { // an input that takes two kinds: a dot half in each colour (e.g. audio stream | clip)
    const [a, b] = kinds.split("|"), at = `data-in="${esc(port)}" data-kind="${esc(kinds)}"`;
    return `<path class="dot" ${at} d="M${x - 6},${y} A6,6 0 0 1 ${x + 6},${y} Z" fill="${KIND_COLOR[a] || "#999"}"></path><path class="dot" ${at} d="M${x - 6},${y} A6,6 0 0 0 ${x + 6},${y} Z" fill="${KIND_COLOR[b] || "#999"}"></path>`;
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

  _portDocs(c, t) {
    const fns = Object.keys(t?.functions || {}).length;
    const help = { ...(fns ? { in: "used as a tool: wire it from a Model (or a Relay) and back, and its functions are called", out: "back to the card that called it" } : {}), ...(t?.ports_help || {}) };
    const kind = (k) => KIND_NAME[k] || k;
    const row = (dir, [p, k]) => `<div class="fn"><code>${esc(p)}</code> <span class="hint">${dir} · ${esc(kind(k))}${c.square.includes(p) ? " · square: goes to one wire" : dir === "out" ? " · round: every wire gets it" : ""}</span>${help[p] || help["*"] ? `<br>${esc(help[p] || help["*"])}` : ""}</div>`;
    const list = [...Object.entries(c.inputs).map((x) => row("in", x)), ...Object.entries(c.outputs).map((x) => row("out", x))];
    return list.length ? `<details class="fold"><summary>Ports</summary>${list.join("")}</details>` : "";
  }

  _valuesSide() { // the value pool: every card's values and the core's, as scripts name them
    const v = this.values;
    if (!v) return '<p class="hint">Loading…</p>';
    const flat = (pre, x) => (x && typeof x === "object" && !Array.isArray(x) ? Object.entries(x).flatMap(([k, y]) => flat(/^[A-Za-z_]\w*$/.test(k) ? `${pre}.${k}` : `${pre}['${k}']`, y)) : [[pre, x]]);
    const what = (id, key) => this._type(this._card(id)?.type)?.values?.[key] || "";
    const groups = Object.entries(v.cards || {}).map(([id, vals]) => `<label>${esc(this._card(id)?.title || id)}</label>${flat(/^[A-Za-z_]\w*$/.test(id) ? id : `cards['${id}']`, vals).map(([k, x]) => `<div class="fn"><code>${esc(k)}</code> <span class="hint">${esc(what(id, k.split(/[.[]/)[1]))}</span><br>${esc(JSON.stringify(x)).slice(0, 300)}</div>`).join("")}`);
    const audio = flat("audio", v.audio || {}).map(([k, x]) => `<div class="fn"><code>${esc(k)}</code><br>${esc(JSON.stringify(x)).slice(0, 300)}</div>`).join("");
    return `<b>Values</b>${tip("What a Relay's script can read right now (lists.total, audio.last.seconds). Cards offer them; they change as the cards work. Home Assistant's states work too: states(\"sensor.x\").")}
      <div class="row" style="margin:6px 0;gap:6px"><button class="small" id="valrefresh">Refresh</button><button class="small" id="valclose">Close</button></div>
      ${groups.join("") || '<p class="hint">No card offers values.</p>'}<label>The core: when AAC last spoke</label>${audio}`;
  }

  async _removeCards() { // the selected cards, no question: Undo brings them back (several in one step)
    const cards = [...this._picked()];
    this.sel = null;
    await this.ws("aac/card/remove", { cards });
    await this._load(); this.render();
  }

  async _loadValues() {
    try { this.values = (await this.ws("aac/values", {})).values; } catch (e) { this.values = { cards: {}, audio: {} }; }
    if (this.showValues) this.render();
  }

  _accessField(c, key) { // an access list: the card's own entries, each hidden / read-only / full (not set: hidden)
    const box = `${c.id}|${key}`, items = (this._accItems ||= {})[c.id];
    if (!items) {
      this._accItems[c.id] = "loading";
      this.ws("aac/card/ui", { card: c.id, action: "access_items" }).then((r) => { this._accItems[c.id] = r.result || []; this.render(); })
        .catch(() => { this._accItems[c.id] = []; this.render(); });
    }
    return `<div class="access"><input data-accsearch="${esc(box)}" placeholder="Search: name, id, area, kind (e.g. light.)" value="${esc((this._accFilter || {})[box] || "")}">
      <div data-accbox="${esc(box)}">${this._accessRows(c, key)}</div></div>`;
  }

  _accessRows(c, key) {
    const box = `${c.id}|${key}`, items = this._accItems?.[c.id], levels = c.settings[key] || {};
    if (!Array.isArray(items)) return '<p class="hint">Loading…</p>';
    const q = ((this._accFilter || {})[box] || "").toLowerCase();
    const shown = items.filter((x) => !q || `${x.name} ${x.id} ${x.area} ${x.group}`.toLowerCase().includes(q));
    const seg = (x) => ["hidden", "read", "full"].map((l) => `<button class="small ${(levels[x.id] || "hidden") === l ? "on" : ""}" data-acclevel="${l}" data-accid="${esc(x.id)}" data-acc="${esc(box)}">${{ hidden: "hidden", read: "read-only", full: "full" }[l]}</button>`).join("");
    const counts = ["read", "full"].map((l) => Object.values(levels).filter((v) => v === l).length);
    return `<div class="row" style="flex-wrap:wrap;gap:4px;margin:6px 0">${[["hidden", "Hide"], ["read", "Read-only"], ["full", "Full"]].map(([l, t]) => `<button class="small" data-accall="${l}" data-acc="${esc(box)}" title="Every entry shown">${t} all${q ? " shown" : ""}</button>`).join("")}
        <span class="hint" style="margin-left:auto">${shown.length} shown · ${counts[0]} read-only · ${counts[1]} full</span></div>
      <div class="acclist">${shown.slice(0, 400).map((x) => `<div class="accrow"><span class="t">${esc(x.name)}<div class="hint">${esc(x.id)}${x.area ? ` · ${esc(x.area)}` : ""}</div></span><span class="seg">${seg(x)}</span></div>`).join("")}
        ${shown.length > 400 ? `<p class="hint">${shown.length - 400} more: search to narrow it down.</p>` : ""}</div>`;
  }

  async _setAccess(box, ids, level) {
    const [cardId, key] = box.split("|"), c = this._card(cardId), levels = { ...(c.settings[key] || {}) };
    for (const id of ids) { if (level === "hidden") delete levels[id]; else levels[id] = level; }
    await this.ws("aac/card/update", { card: cardId, settings: { [key]: levels } });
    await this._load(); this.render();
  }

  _field(c, entry) { // one setting of a card, as its schema says; its inputs say which card (a page may show several)
    return this._fieldHtml(c, entry).replace(/data-set="/g, `data-card="${esc(c.id)}" data-set="`);
  }

  _fieldHtml(c, [key, f]) {
    const d = this.state.domain, t = this._type(c.type);
    const v = c.settings[key] ?? f.default, h = f.help ? tip(esc(f.help)) : "";
    if (f.type === "waveforms") return `<button class="small" data-wave="${esc(c.id)}" style="margin-top:8px" title="Line up the microphone and what the speaker played">Waveforms …</button>`;
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
    if (f.type === "access") return `<label>${esc(f.title)}${h}</label>${this._accessField(c, key)}`;
    if (f.type === "model") return `<label>${esc(f.title)}${h}</label><select data-set="${esc(key)}"><option value="">—</option>${this.state.models.map((m) => `<option value="${esc(m.id)}" ${m.id === v ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select>`;
    if (f.type === "tts_engine") return `<label>${esc(f.title)}${h}</label><select data-set="${esc(key)}"><option value="">the Audio page's default${this.audio?.settings?.tts ? ` (${esc(this.audio.settings.tts)})` : ""}</option>${(this.audio?.tts_entities || []).map((e) => `<option ${e === v ? "selected" : ""}>${esc(e)}</option>`).join("")}</select>`;
    if (f.type === "voices") { // a voice per home language, from the card's engine
      const engine = c.settings.engine || this.audio?.settings?.tts, got = engine && this._vc?.[engine];
      if (engine && !got) this._voicesOf(engine);
      const rows = !engine ? '<p class="hint">Pick a voice engine first.</p>' : !got || got.loading ? '<p class="hint">Loading voices…</p>' : got.error ? `<p class="hint">${esc(got.error)}</p>`
        : (this.audio?.languages || []).map(({ code, name }) => `<div class="row"><span style="flex:0 0 90px">${esc(name)}</span>${got[code]?.locale
          ? `<select data-voicefor="${esc(code)}" data-key="${esc(key)}"><option value="">the engine's default</option>${got[code].voices.map((x) => `<option value="${esc(x.voice_id)}" data-locale="${esc(x.locale)}" ${x.voice_id === (v || {})[code]?.voice ? "selected" : ""}>${esc(x.label)}</option>`).join("")}</select>`
          : '<span class="hint">the engine has no voice for it</span>'}</div>`).join("");
      return `<label>${esc(f.title)}${h}</label>${rows}`;
    }
    if (f.type === "player") return `<label>${esc(f.title)}${h}</label><select data-set="${esc(key)}"><option value="">the satellite the line came from</option>${(this.audio?.settings?.known || []).map((k) => `<option value="${esc(k.id)}" ${k.id === v ? "selected" : ""}>${esc(k.name)} (satellite)</option>`).join("")}${Object.keys(this._hass.states).filter((e) => e.startsWith("media_player.") && !e.startsWith("media_player.aac_satellite_")).sort().map((e) => `<option value="${esc(e)}" ${e === v ? "selected" : ""}>${esc(this._hass.states[e].attributes.friendly_name || e)}</option>`).join("")}</select>`;
    if (f.type === "source") return `<label>${esc(f.title)}${h}</label><select data-set="${esc(key)}"><option value="${key === "source" && c.type === "microphone" ? "any" : ""}">${esc(f.empty || (c.type === "microphone" ? "any satellite" : "where the line came from"))}</option>${(this.audio?.settings?.known || []).map((k) => `<option value="${esc(k.id)}" ${k.id === v ? "selected" : ""}>${esc(k.name)}</option>`).join("")}</select>`;
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
    if (f.type === "functions") { // For models: each function with its tick, the name and description a model sees, Reset
      const own = v || {}, defOn = t?.offers !== false;
      return `<p class="hint" style="margin:4px 0">${esc(f.help || "")}</p>` + Object.entries(c.functions || {}).map(([fn, spec]) => {
        const o = own[fn] || {}, on = o.on ?? defOn, args = Object.entries(spec.args || {}).map(([a, x]) => `${esc(a)}${x.required ? "" : "?"}`).join(", ");
        return `<div class="fnrow ${on ? "" : "off"}" data-fnrow="${esc(fn)}" data-key="${esc(key)}"><label class="check"><input type="checkbox" data-fnon ${on ? "checked" : ""}>offered <code>${esc(fn)}(${args})</code></label>
          <label>Name the model sees</label><input type="text" data-fnname value="${esc(o.name || "")}" placeholder="${esc(fn)}">
          <label>Description the model reads</label><textarea rows="2" data-fnhelp placeholder="${esc(spec.help || "")}">${esc(o.help || "")}</textarea>
          ${o.name || o.help ? `<button class="small" data-fnreset title="Back to the card's own name and description">Reset</button>` : ""}</div>`;
      }).join("") || '<p class="hint">No functions.</p>';
    }
    if (f.type === "element") return `<label>${esc(f.title)}</label><div data-elfield="${esc(c.id)}|${esc(key)}"><p class="hint">Loading…</p></div>`; // the card's own editor
    return `<label>${esc(f.title)}${h}</label><input data-set="${esc(key)}" value="${esc(v)}">`;
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
    if (this.many.size) return `<b>${this.many.size} cards selected</b><p class="hint">Drag one of them and they all move, the bin or Del removes them. With Select on, a click on a card or a long press on a phone adds or removes it (Ctrl+click without it), a frame adds the cards in it. Esc or a click on the empty grid lets go.</p>
      ${[...this.many].map((id) => `<div class="item" data-selcard="${esc(id)}" style="cursor:pointer">${esc(this._card(id)?.title || id)}</div>`).join("")}`;
    const c = this.sel && this._card(this.sel);
    if (!c) {
      const groups = {};
      for (const t of d.tags) (groups[t.card] ||= []).push(t);
      return `<p class="hint">Click a card to set it up.</p><b>Tags in this domain</b>${tip("Cards tag the entries they write (card.tag). Your own tags (user.…) are for Relay scripts: tag entries with them, then pick them up later.")}
        <div class="row" style="margin:8px 0;gap:6px"><input id="newtag" placeholder="your own tag, e.g. urgent"><button class="small" id="addtag">Add</button></div>
        ${Object.entries(groups).map(([card, tags]) => `<label>${esc(card)}</label>${tags.map((t) => `<div class="hint" style="font-size:12px"><code>${esc(t.tag)}</code> ${esc(t.what)}${card === "You" ? ` <button class="small" data-deltag="${esc(t.tag)}" title="Remove">×</button>` : ""}</div>`).join("")}`).join("")}`;
    }
    const t = this._type(c.type);
    const field = (e) => this._field(c, e);
    return `<label>Name</label><input id="title" value="${esc(c.title)}">
      <p class="hint" style="margin-top:8px">${esc(t?.help || "")} <span title="The version of its card file">· ${esc(t?.title || c.type)} v${esc(t?.version || "?")}</span></p>
      ${Object.entries(t?.settings || {}).filter(([, f]) => !f.fold).map(field).join("")}
      ${c.tools ? `<label>Its tools ${tip("The cards in its loop (wired from its output back into it, straight or round through other cards) with at least one function ticked under For models. A call goes there along the wires and comes back.")}</label>
        <div class="row" style="flex-wrap:wrap;gap:4px">${c.tools.map((id) => `<button class="small" data-selcard="${esc(id)}" title="Select it">${esc(this._card(id)?.title || id)}</button>`).join("") || '<span class="hint">none: wire a card with functions into its loop</span>'}</div>` : ""}
      ${Object.entries(Object.entries(t?.settings || {}).filter(([, f]) => f.fold).reduce((g, e) => ({ ...g, [e[1].fold]: [...(g[e[1].fold] || []), e] }), {}))
        .map(([name, fs]) => `<details class="fold" ${fs.some(([k]) => c.settings[k]) ? "open" : ""}><summary>${esc(name)}</summary>${fs.map(field).join("")}</details>`).join("")}
      ${this._portDocs(c, t)}
      ${Object.keys(t?.values || {}).length ? `<details class="fold"><summary>Values (for scripts)</summary>${Object.entries(t.values).map(([n, h]) => `<div class="fn"><code>${esc(/^[A-Za-z_]\w*$/.test(c.id) ? c.id : `cards['${c.id}']`)}.${esc(n)}</code> ${esc(h)}</div>`).join("")}</details>` : ""}
      ${Object.keys(c.tags).length ? `<details class="fold"><summary>Tags</summary>${Object.entries(c.tags).map(([x, w]) => `<div class="fn"><code>${esc(x)}</code> ${esc(w)}</div>`).join("")}</details>` : ""}
      <div style="margin-top:14px;display:flex;gap:8px"><button class="danger" id="remove">Remove card</button></div>
      <p class="hint">Fields save when you leave them.</p>`;
  }

  async _fieldElements() { // settings a card edits with its own element (a file next to its card.py), kept across redraws
    for (const host of this.shadowRoot.querySelectorAll("[data-elfield]")) {
      const [cardId, key] = host.dataset.elfield.split("|"), c = this._card(cardId), f = this._type(c?.type)?.settings?.[key];
      if (!f?.url) continue;
      const id = `${cardId}|${key}|${f.url}`;
      let el = (this._fieldEls ||= {})[id];
      if (!el) {
        try { if (!customElements.get(f.element)) await import(f.url); } catch (err) { host.innerHTML = `<p class="warn">${esc(err)}</p>`; continue; }
        el = this._fieldEls[id] = document.createElement(f.element);
        el.panel = { ...this._api(cardId), type: (t) => this._type(t), domain: () => this.state.domain, card: () => this._card(cardId),
          save: async (settings) => { await this.ws("aac/card/update", { card: cardId, settings }); await this._load(); this.render(); } };
      }
      el.setup?.({ card: c, key, value: c.settings[key] ?? f.default, settings: c.settings });
      if (host.isConnected) host.replaceChildren(el);
    }
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
      return `<g class="node ${this.sel === c.id || this.many.has(c.id) ? "sel" : ""}" data-node="${esc(c.id)}" transform="translate(${p.x},${p.y})">
        <rect class="box" width="${W}" height="${height(c)}" rx="8"></rect>
        <text x="10" y="18">${esc(c.title)}</text><text class="sub" x="10" y="30">${esc(this._sub(c))}</text>
        ${ins.map(([n, k], i) => `${k === "read" ? `<polygon class="dot" data-in="${esc(n)}" data-kind="${k}" points="${this._tri(inX, HEAD + ROW * i + ROW / 2, m)}" fill="${KIND_COLOR[k]}"></polygon>` : k.includes("|") ? this._halves(inX, HEAD + ROW * i + ROW / 2, n, k) : `<circle class="dot" data-in="${esc(n)}" data-kind="${k}" cx="${inX}" cy="${HEAD + ROW * i + ROW / 2}" r="6" fill="${KIND_COLOR[k] || "#999"}"></circle>`}<text class="port" ${inT} y="${HEAD + ROW * i + ROW / 2 + 3}">${esc(n)}</text>`).join("")}
        ${outs.map(([n, k], i) => `${k === "read" ? `<polygon class="dot" data-out="${esc(n)}" data-kind="${k}" points="${this._tri(outX, HEAD + ROW * i + ROW / 2, m)}" fill="${KIND_COLOR[k]}"></polygon>` : c.square.includes(n) ? `<rect class="dot" data-out="${esc(n)}" data-kind="${k}" x="${outX - 6}" y="${HEAD + ROW * i + ROW / 2 - 6}" width="12" height="12" fill="${KIND_COLOR[k] || "#999"}"></rect>`
          : `<circle class="dot" data-out="${esc(n)}" data-kind="${k}" cx="${outX}" cy="${HEAD + ROW * i + ROW / 2}" r="6" fill="${KIND_COLOR[k] || "#999"}"></circle>`}<text class="port" ${outT} y="${HEAD + ROW * i + ROW / 2 + 3}">${esc(n)}</text>`).join("")}
        ${c.view ? `<foreignObject ${c.view.inline ? `x="34" y="${HEAD + 2}" width="${W - 76}" height="${ROW - 4}"` : `x="8" y="${this._ports(c) + 2}" width="${W - 16}" height="${c.view.height}"`}><div xmlns="http://www.w3.org/1999/xhtml" data-viewhost="${esc(c.id)}" style="width:100%;height:100%"></div></foreignObject>` : ""}
      </g>`;
    }).join("");
    this._placeViews();
    const routes = this._routes(wires, port);
    this.shadowRoot.querySelector("#wires").innerHTML = wires.map((w) => {
      const path = routes[w.key], sel = this.selWire && `${this.selWire.from}|${this.selWire.to}` === w.key;
      return `<path class="wire ${w.read ? "read" : ""} ${w.loop ? "loop" : ""} ${sel ? "sel" : ""}" data-from="${esc(`${w.a.id}.${w.ap}`)}" data-to="${esc(`${w.b.id}.${w.bp}`)}" d="${path}" stroke="${KIND_COLOR[w.kind] || "#999"}"></path>
        <path class="hit" data-wire="${esc(w.key)}" d="${path}"><title>${esc(w.a.title)}.${esc(w.ap)} → ${esc(w.b.title)}.${esc(w.bp)} (${esc(w.kind)})</title></path>`;
    }).join("");
    this._view();
  }

  // ------------------------------------------------------------------ layout (v1's slots and wire lanes)
  async _placeViews() { // each card's view inside it (spec/cards.md "A card's view"); kept, so a redraw does not reset it
    const cards = this.state.domain.cards.filter((c) => c.view);
    this._watchViews(cards.map((c) => c.id));
    for (const c of cards) {
      const host = this.shadowRoot.querySelector(`[data-viewhost="${CSS.escape(c.id)}"]`);
      if (!host) continue;
      try {
        let el = (this._viewEls ||= {})[c.id];
        if (!el || el._url !== c.view.url) {
          if (!customElements.get(c.view.element)) await import(c.view.url);
          el = this._viewEls[c.id] = document.createElement(c.view.element);
          el._url = c.view.url; el.style.cssText = "display:block;width:100%;height:100%";
        }
        el.card = c;
        if (host.isConnected) host.replaceChildren(el);
      } catch (e) { host.textContent = `view: ${e.message || e}`; }
    }
  }

  _watchViews(ids) { // AAC sends views' frames (and live wires) only while a Nodes page says it is open
    const say = () => (this.view.kind === "nodes" && this.isConnected ? this.ws("aac/view/watch", { cards: this._watchIds || [] }).catch(() => {}) : null);
    const changed = JSON.stringify(ids) !== JSON.stringify(this._watchIds);
    this._watchIds = ids;
    if (changed) say();
    if (!this._watchTimer) this._watchTimer = setInterval(() => { if (this.view.kind === "nodes") say(); }, 8000);
  }

  _ports(c) { return HEAD + ROW * Math.max(Object.keys(c.inputs).length, Object.keys(c.outputs).length, 1); } // where its view starts
  _height(c) { return this._ports(c) + (c.view && !c.view.inline ? c.view.height + 6 : 0) + 8; }

  _wireList() {
    const byId = Object.fromEntries(this.state.domain.cards.map((c) => [c.id, c]));
    const wires = this.state.domain.wires.map((w) => {
      const [a, ap] = w.from.split("."), [b, bp] = w.to.split(".");
      return byId[a] && byId[b] ? { a: byId[a], ap, b: byId[b], bp, kind: byId[a].outputs[ap], read: byId[b].inputs[bp] === "read" || byId[a].outputs[ap] === "read", key: `${w.from}|${w.to}` } : null;
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

  // No card lies on or right against another (10 px apart at least): a card that would goes below the one it covers
  _hit(x, y, h, among) { // the card of `among` that a card at x, y (h high) would cover
    const d = this.state.domain;
    return among.find((o) => d.positions[o.id] && Math.abs(d.positions[o.id].x - x) < W && d.positions[o.id].y < y + h + 10 && y < d.positions[o.id].y + this._height(o) + 10);
  }

  _under(o) { return Math.round((this.state.domain.positions[o.id].y + this._height(o) + 20) / 20) * 20; } // the first free y below a card

  _snap(id, group = [id]) {
    // dragged cards go into the nearest column, on the 20 px grid, below any card they would cover there; the cards
    // dragged together (the one held and the rest of the selection) move as one and keep their places to each other
    const d = this.state.domain, p = d.positions[id], ours = group.filter((g) => d.positions[g] && this._card(g));
    if (!p || !this._card(id)) return;
    const shift = (dx, dy) => { for (const g of ours) { d.positions[g].x += dx; d.positions[g].y += dy; } };
    shift(Math.round(p.x / COL) * COL - p.x, Math.round(p.y / 20) * 20 - p.y);
    const others = d.cards.filter((o) => !ours.includes(o.id));
    for (let tries = 0; tries < 50; tries++) {
      const g = ours.find((x) => this._hit(d.positions[x].x, d.positions[x].y, this._height(this._card(x)), others));
      if (!g) break;
      const q = d.positions[g];
      shift(0, this._under(this._hit(q.x, q.y, this._height(this._card(g)), others)) - q.y);
    }
  }

  _settle() { // cards on each other (one grew, or came without a place, e.g. from a model): the lower one moves down; true if any moved
    const d = this.state.domain, done = [];
    let moved = false;
    const placed = d.cards.filter((c) => d.positions[c.id]).sort((a, b) => d.positions[a.id].y - d.positions[b.id].y || d.positions[a.id].x - d.positions[b.id].x);
    for (const c of [...placed, ...d.cards.filter((c) => !d.positions[c.id])]) {
      if (!d.positions[c.id]) { d.positions[c.id] = { x: 0, y: 0 }; moved = true; } // no place yet: the first column, below what is there
      const p = d.positions[c.id];
      for (let tries = 0, hit; tries < 50 && (hit = this._hit(p.x, p.y, this._height(c), done)); tries++) { p.y = this._under(hit); moved = true; }
      done.push(c);
    }
    return moved;
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
    return `<b>Monitor</b><p class="hint">${esc(name(from))}.${esc(from.split(".")[1])} › ${esc(name(to))}.${esc(to.split(".")[1])} · live</p>
      <button class="small" data-monoff="${esc(key)}">Stop monitoring</button>
      ${msg?.messages ? `<label>NEWEST CONTEXT · ${esc(new Date(msg.time * 1000).toLocaleTimeString())}</label><p class="hint">Way: ${esc((msg.way || []).map(name).join(" › "))}</p>${contextView(msg, name)}`
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
    let drag = null, pinch = null;
    const fingers = new Map(); // pointers down now: two make a pinch (zoom around the point between them, and move)
    const two = () => { const [a, b] = [...fingers.values()]; return { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }; };
    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = svg.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top, k = Math.min(2.5, Math.max(0.05, this.vp.k * (e.deltaY < 0 ? 1.1 : 0.9)));
      this.vp = { k, x: mx - (mx - this.vp.x) * k / this.vp.k, y: my - (my - this.vp.y) * k / this.vp.k };
      this._view();
    }, { passive: false });
    svg.addEventListener("pointerdown", (e) => {
      const node = e.target.closest(".node"), out = e.target.dataset?.out, hit = e.target.dataset?.wire;
      svg.setPointerCapture(e.pointerId);
      fingers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (fingers.size >= 2) { // a second finger: whatever the first began becomes a pinch
        if (drag?.kind === "move" && drag.moved) { this._snap(drag.id, Object.keys(drag.from)); this._savePositions(); }
        if (drag?.kind === "cut") this._drawGrid();
        clearTimeout(drag?.press); this.shadowRoot.querySelectorAll("#frame, #cutline, #tmpwire").forEach((x) => x.remove());
        drag = null; this._dragging = false;
        pinch = { ...two(), vp: { ...this.vp } };
        return;
      }
      if (this.tool === "cut") { drag = { kind: "cut", trail: [this._world(e)], lines: this._wireLines(), hits: new Set() }; return; } // the knife cuts across cards too
      if (node && out) { drag = { kind: "wire", from: `${node.dataset.node}.${out}`, type: e.target.dataset.kind }; return; }
      if (hit) { const [from, to] = hit.split("|"); this.selWire = { from, to }; this.sel = null; this.selMon = null; this.showValues = false; this.render(); return; }
      if (node) { // a card in the selection takes the others along
        const id = node.dataset.node, pos = this.state.domain.positions, p = pos[id] || (pos[id] = { x: 0, y: 0 }), w = this._world(e);
        const group = this.many.has(id) ? [...this.many] : [id];
        drag = { kind: "move", id, dx: w.x - p.x, dy: w.y - p.y, moved: false, add: e.ctrlKey || e.metaKey || this.tool === "select", at: [e.clientX, e.clientY], from: Object.fromEntries(group.filter((g) => pos[g]).map((g) => [g, { ...pos[g] }])) };
        if (e.pointerType === "touch") { const d = drag; d.press = setTimeout(() => { // a long press: in or out of the selection
          if (drag !== d || d.moved) return;
          d.long = true; navigator.vibrate?.(20); this._toggle(id);
        }, 500); }
        return;
      }
      if (this.selType) { const w = this._world(e); this._place(this.selType, w); return; }
      if (this.tool === "select" || e.ctrlKey || e.metaKey) { drag = { kind: "frame", at: this._world(e), screen: [e.clientX, e.clientY], add: e.ctrlKey || e.metaKey, had: this._picked() }; return; }
      drag = { kind: "pan", x: e.clientX - this.vp.x, y: e.clientY - this.vp.y, at: [e.clientX, e.clientY] };
    });
    svg.addEventListener("contextmenu", (e) => e.preventDefault()); // a long press on a phone selects, no menu
    svg.addEventListener("pointermove", (e) => {
      if (fingers.has(e.pointerId)) fingers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pinch) {
        if (fingers.size < 2) return;
        const now = two(), r = svg.getBoundingClientRect(), k = Math.min(2.5, Math.max(0.05, pinch.vp.k * now.d / pinch.d));
        const wx = (pinch.x - r.left - pinch.vp.x) / pinch.vp.k, wy = (pinch.y - r.top - pinch.vp.y) / pinch.vp.k; // the spot the pinch began on
        this.vp = { k, x: now.x - r.left - wx * k, y: now.y - r.top - wy * k };
        this._view();
        return;
      }
      if (!drag) { this._ghost(e); return; }
      if (drag.kind === "pan") { this.vp.x = e.clientX - drag.x; this.vp.y = e.clientY - drag.y; this._view(); }
      if (drag.kind === "move" && !drag.moved && (drag.long || (e.pointerType === "touch" && Math.hypot(e.clientX - drag.at[0], e.clientY - drag.at[1]) < 8))) return; // a finger holds still (long press)
      if (drag.kind === "move") {
        const w = this._world(e), pos = this.state.domain.positions, p = pos[drag.id];
        p.x = Math.round((w.x - drag.dx) / 20) * 20; p.y = Math.round((w.y - drag.dy) / 20) * 20; drag.moved = true; this._dragging = true;
        const a = drag.from[drag.id];
        for (const [g, f] of Object.entries(drag.from)) pos[g] = { x: f.x + p.x - a.x, y: f.y + p.y - a.y };
        this._snap(drag.id, Object.keys(drag.from)); // shown where it lands: in its column, below what it would cover
        this._drawGrid();
      }
      if (drag.kind === "frame") {
        const w = this._world(e), x = Math.min(w.x, drag.at.x), y = Math.min(w.y, drag.at.y), fw = Math.abs(w.x - drag.at.x), fh = Math.abs(w.y - drag.at.y);
        drag.hits = this.state.domain.cards.filter((c) => { const p = this.state.domain.positions[c.id]; return p && p.x < x + fw && x < p.x + W && p.y < y + fh && y < p.y + this._height(c); }).map((c) => c.id);
        drag.moved ||= Math.hypot(e.clientX - drag.screen[0], e.clientY - drag.screen[1]) >= 4; this._dragging = true;
        let r = this.shadowRoot.querySelector("#frame");
        if (!r) { r = document.createElementNS("http://www.w3.org/2000/svg", "rect"); r.id = "frame"; this.shadowRoot.querySelector("#world").appendChild(r); }
        r.setAttribute("x", x); r.setAttribute("y", y); r.setAttribute("width", fw); r.setAttribute("height", fh);
        const on = new Set(drag.add ? [...drag.had, ...drag.hits] : drag.hits);
        for (const n of this.shadowRoot.querySelectorAll("#cards .node")) n.classList.toggle("sel", on.has(n.dataset.node));
      }
      if (drag.kind === "cut") {
        const w = this._world(e), last = drag.trail.at(-1);
        drag.trail.push(w); this._dragging = true;
        for (const [key, pts] of Object.entries(drag.lines)) if (!drag.hits.has(key) && pts.some((q, i) => i && crosses(last, w, pts[i - 1], q))) drag.hits.add(key);
        let line = this.shadowRoot.querySelector("#cutline");
        if (!line) { line = document.createElementNS("http://www.w3.org/2000/svg", "polyline"); line.id = "cutline"; this.shadowRoot.querySelector("#world").appendChild(line); }
        line.setAttribute("points", drag.trail.map((q) => `${q.x},${q.y}`).join(" "));
        for (const el of this.shadowRoot.querySelectorAll("#wires path.wire")) el.classList.toggle("cut", drag.hits.has(`${el.dataset.from}|${el.dataset.to}`));
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
    svg.addEventListener("pointercancel", (e) => {
      fingers.delete(e.pointerId); if (fingers.size < 2) pinch = null;
      if (drag?.kind === "cut") this._drawGrid();
      clearTimeout(drag?.press); drag = null; this._dragging = false; this.shadowRoot.querySelectorAll("#frame, #cutline").forEach((x) => x.remove());
    });
    svg.addEventListener("pointerup", async (e) => {
      fingers.delete(e.pointerId);
      if (pinch) { if (!fingers.size) pinch = null; return; } // a pinch ends when the last finger lifts
      const d = drag; drag = null; this._dragging = false;
      if (!d) return;
      clearTimeout(d.press);
      if (d.kind === "cut") { // the wires the knife crossed go (Undo brings them back)
        this.shadowRoot.querySelector("#cutline")?.remove();
        if (!d.hits.size) { this._drawGrid(); return; }
        if (this.selWire && d.hits.has(`${this.selWire.from}|${this.selWire.to}`)) this.selWire = null;
        await this.ws("aac/wires", { wires: this.state.domain.wires.filter((w) => !d.hits.has(`${w.from}|${w.to}`)) });
        await this._load(); this.render(); return;
      }
      if (d.long) return; // the long press already did it
      if (d.kind === "frame") this.shadowRoot.querySelector("#frame")?.remove();
      if (d.kind === "frame" && d.moved) { this._pick(d.add ? [...d.had, ...d.hits] : d.hits); this.selWire = this.selMon = null; this.showValues = false; this.render(); return; }
      if ((d.kind === "pan" && Math.hypot(e.clientX - d.at[0], e.clientY - d.at[1]) < 4) || (d.kind === "frame" && !d.add)) { // a tap on empty grid lets go (on a phone it closes the panels too)
        const closed = this._phone() && this._closePanels();
        if (this._picked().size || this.selWire) { this.sel = this.selWire = null; this.render(); return; }
        if (closed) return;
      }
      if (d.kind === "move") {
        if (d.moved) { this._snap(d.id, Object.keys(d.from)); this._drawGrid(); await this._savePositions(); }
        else if (d.add) this._toggle(d.id); // Ctrl+click or the Select tool: in or out of the selection
        else { this.sel = d.id; this.selWire = null; this.selMon = null; this.showValues = false; if (!this.sideOpen) this._panel("side", true); else this.render(); }
      }
      if (d.kind === "wire") {
        this.shadowRoot.querySelector("#tmpwire")?.remove();
        const el = this.shadowRoot.elementFromPoint(e.clientX, e.clientY), node = el?.closest?.(".node"), inp = el?.dataset?.in;
        if (node && inp && (el.dataset.kind === "any" || el.dataset.kind.split("|").includes(d.type))) { // a kind the input takes (a read output only into a read input; "any" takes all)
          await this.ws("aac/wires", { wires: [...this.state.domain.wires, { from: d.from, to: `${node.dataset.node}.${inp}` }] });
          await this._load(); this.render();
        }
      }
    });
  }

  _toggle(id) { const s = this._picked(); if (!s.delete(id)) s.add(id); this._pick(s); this.selWire = this.selMon = null; this.showValues = false; this.render(); }

  _wireLines() { // each wire as a line of points (world), for the knife
    const lines = {};
    for (const el of this.shadowRoot.querySelectorAll("#wires path.hit")) {
      const len = el.getTotalLength(), n = Math.max(2, Math.ceil(len / 8));
      lines[el.dataset.wire] = Array.from({ length: n + 1 }, (_, i) => el.getPointAtLength(len * i / n));
    }
    return lines;
  }

  _spot(at, h) { // where a new card (h high) lands: in a column, below any card it would cover
    const p = { x: Math.round((at.x - W / 2) / COL) * COL, y: Math.round(at.y / 20) * 20 };
    for (let tries = 0, hit; tries < 50 && (hit = this._hit(p.x, p.y, h, this.state.domain.cards)); tries++) p.y = this._under(hit);
    return p;
  }

  _ghost(e) { // the picked card type, see-through, where a click would place it
    const t = this.selType && this._type(this.selType);
    let g = this.shadowRoot.querySelector("#ghost");
    if (!t) { g?.remove(); return; }
    const h = this._height({ inputs: t.inputs, outputs: t.outputs }), p = this._spot(this._world(e), h);
    if (!g) { g = document.createElementNS("http://www.w3.org/2000/svg", "g"); g.id = "ghost"; g.setAttribute("pointer-events", "none"); this.shadowRoot.querySelector("#cards").appendChild(g); }
    g.setAttribute("transform", `translate(${p.x},${p.y})`);
    g.innerHTML = `<rect class="box" width="${W}" height="${h}" rx="8" style="opacity:.45;stroke-dasharray:5 4"></rect><text x="10" y="18" style="opacity:.6">${esc(t.title)}</text>`;
  }

  _unpick() { this.selType = null; this.shadowRoot.querySelector("#ghost")?.remove(); this.render(); }

  async _place(type, at) {
    const t = this._type(type), card = await this.ws("aac/card/add", { card_type: type, ...this._spot(at, this._height({ inputs: t.inputs, outputs: t.outputs })) });
    this.selType = null; this.sel = card.id;
    await this._load(); this.render();
  }

  async _saveSetting(el) {
    const c = this._card(el.dataset.card || this.sel), key = el.dataset.set;
    let value = el.type === "checkbox" ? el.checked : el.value;
    if (el.dataset.num !== undefined) value = Number(value);
    if (el.dataset.list !== undefined) value = value.split(",").map((x) => x.trim()).filter(Boolean);
    if (el.dataset.outputs !== undefined) value = value.split("\n").map((l) => { const [n, ...r] = l.split(":"); return { name: n.trim().replace(/[^\w-]/g, "_"), description: r.join(":").trim() }; }).filter((o) => o.name);
    if (el.dataset.json !== undefined) { try { value = JSON.parse(value || "[]"); } catch (err) { this.alert(`Not valid: ${err.message}`); return; } }
    const f = this._type(c.type)?.settings?.[key] || {}, old = c.settings[key] ?? f.default;
    if (f.min !== undefined && value < f.min && value !== f.off) { // too small to work: say so instead of doing nothing
      this.alert(`${f.title}: at least ${f.min}${f.off !== undefined ? ` (or ${f.off} = off)` : ""}.`); this.render(); return;
    }
    if (f.confirm && !old && value && !(await this.confirm(f.confirm))) { this.render(); return; } // a setting that changes more than itself asks first
    await this.ws("aac/card/update", { card: c.id, settings: { [key]: value } });
    await this._load(); this.render();
  }

  // ------------------------------------------------------------------ events
  _bind() {
    const $ = (s) => this.shadowRoot.querySelector(s), $$ = (s) => this.shadowRoot.querySelectorAll(s);
    this._grips();
    $("#scrim")?.addEventListener("click", () => { this.navOpen = false; this.shadowRoot.querySelector(".root").classList.remove("navopen"); });
    $("#habtn")?.addEventListener("click", () => this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })));
    // nodes
    $$("[data-pick]").forEach((el) => el.addEventListener("click", () => { this.selType = this.selType === el.dataset.pick ? null : el.dataset.pick; if (this.selType && this._phone()) this._panel("pal", false); else this.render(); }));
    $("#paltog")?.addEventListener("click", () => this._panel("pal", !this.palOpen));
    $("#sidetog")?.addEventListener("click", () => this._panel("side", !this.sideOpen));
    $("#undo")?.addEventListener("click", async () => { await this.ws("aac/undo"); await this._load(); this.render(); });
    $("#redo")?.addEventListener("click", async () => { await this.ws("aac/redo"); await this._load(); this.render(); });
    $("#fit")?.addEventListener("click", () => this._fit());
    $("#delsel")?.addEventListener("click", () => this.selWire ? this._unwire() : this._removeCards());
    $$("[data-tool]").forEach((el) => el.addEventListener("click", () => { this.tool = this.tool === el.dataset.tool ? null : el.dataset.tool; this.render(); }));
    $("#unpick")?.addEventListener("click", () => this._unpick());
    $$("[data-selcard]").forEach((el) => el.addEventListener("click", () => { this.sel = el.dataset.selcard; this.render(); }));
    $$("[data-skin]").forEach((el) => el.addEventListener("change", async () => { await this.ws("aac/appearance/set", { skin: el.dataset.skin }); }));
    $$("[data-foldmode]").forEach((el) => el.addEventListener("change", async () => { await this.ws("aac/appearance/set", { folds: { [el.dataset.foldmode]: el.value } }); }));
    $("#values")?.addEventListener("click", () => { this.showValues = !this.showValues; this.values = null; this.render(); if (this.showValues) this._loadValues(); });
    $("#valrefresh")?.addEventListener("click", () => this._loadValues());
    $("#valclose")?.addEventListener("click", () => { this.showValues = false; this.render(); });
    $("#arrange")?.addEventListener("click", () => this._doArrange());
    $("#addtag")?.addEventListener("click", async () => { const t = $("#newtag").value.trim(); if (t) { await this.ws("aac/tags/add", { tag: t }); await this._load(); this.render(); } });
    $("#newtag")?.addEventListener("keydown", (e) => { if (e.key === "Enter") $("#addtag").click(); });
    $$("[data-deltag]").forEach((el) => el.addEventListener("click", async () => { await this.ws("aac/tags/remove", { tag: el.dataset.deltag }); await this._load(); this.render(); }));
    $$("[data-slot]").forEach((el) => el.addEventListener("click", async (e) => {
      const slot = el.dataset.slot;
      if (e.shiftKey) { if (await this.confirm(`Load slot ${slot}? (Undo brings the current setup back)`)) await this.ws("aac/slot", { slot, action: "load" }); }
      else await this.ws("aac/slot", { slot, action: "save" });
      await this._load(); this.render();
    }));
    $("#title")?.addEventListener("change", async (e) => { await this.ws("aac/card/update", { card: this.sel, title: e.target.value }); await this._load(); this.render(); });
    $$("[data-set]").forEach((el) => el.addEventListener("change", () => this._saveSetting(el)));
    const fnSave = async (row, reset) => { // For models: one function's tick, name, description (empty: the card's own)
      const c = this._card(this.sel), key = row.dataset.key; // only functions the card has now (a renamed block's old entry goes)
      const all = Object.fromEntries(Object.entries(c.settings[key] || {}).filter(([fn]) => fn in (c.functions || {})));
      const name = row.querySelector("[data-fnname]").value.trim(), help = row.querySelector("[data-fnhelp]").value.trim();
      all[row.dataset.fnrow] = { on: row.querySelector("[data-fnon]").checked, ...(reset ? {} : { ...(name ? { name } : {}), ...(help ? { help } : {}) }) };
      await this.ws("aac/card/update", { card: c.id, settings: { [key]: all } }); await this._load(); this.render();
    };
    $$("[data-fnrow]").forEach((row) => {
      row.querySelectorAll("input,textarea").forEach((el) => el.addEventListener("change", () => fnSave(row, false)));
      row.querySelector("[data-fnreset]")?.addEventListener("click", () => fnSave(row, true));
    });
    $$("[data-voicefor]").forEach((el) => el.addEventListener("change", async () => {
      const c = this._card(this.sel), voices = { ...(c.settings[el.dataset.key] || {}) };
      if (el.value) voices[el.dataset.voicefor] = { language: el.selectedOptions[0].dataset.locale, voice: el.value }; else delete voices[el.dataset.voicefor];
      await this.ws("aac/card/update", { card: c.id, settings: { [el.dataset.key]: voices } }); await this._load(); this.render();
    }));
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
    $("#remove")?.addEventListener("click", () => this._removeCards());
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
    const langs = () => (this.audio?.languages || []).map((l) => l.code);
    const langSet = async (list) => { this.audio = await this.ws("aac/languages/set", { languages: list }); this._vc = {}; this.render(); };
    $("#langadd")?.addEventListener("change", (e) => e.target.value && langSet([...langs(), e.target.value]));
    $("#langha")?.addEventListener("click", () => langSet(null));
    $$("[data-langdel]").forEach((el) => el.addEventListener("click", () => { const l = langs().filter((_, i) => i !== Number(el.dataset.langdel)); if (l.length) langSet(l); }));
    $$("[data-langup]").forEach((el) => el.addEventListener("click", () => { const l = langs(), i = Number(el.dataset.langup); [l[0], l[i]] = [l[i], l[0]]; langSet(l); }));
    $$("[data-forget]").forEach((el) => el.addEventListener("click", async () => { if (await this.confirm("Forget this satellite? (It comes back when it connects again.)")) { this.audio = await this.ws("aac/audio/forget", { source: el.dataset.forget }); this.render(); } }));
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
      window.addEventListener("keydown", (e) => { // Delete: the selected wire or cards go (Undo brings them back)
        if (this.view.kind !== "nodes" || /INPUT|TEXTAREA|SELECT/.test(e.composedPath()[0]?.tagName)) return;
        if (e.key === "Escape") { // put back a picked card type, or let go of the selected card or wire
          if (this.selType) this._unpick();
          else if (this.tool) { this.tool = null; this.render(); }
          else if (this._picked().size || this.selWire || this.selMon || this.showValues) { this.sel = this.selWire = this.selMon = null; this.showValues = false; this.render(); }
          return;
        }
        if (!(e.key === "Delete" || e.key === "Backspace")) return;
        if (this.selWire) this._unwire();
        else if (this._picked().size) this._removeCards();
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

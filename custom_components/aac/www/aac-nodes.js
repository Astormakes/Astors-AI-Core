// The node view (spec/streams.md): every node of the domain and how it is wired.
// Owner idea (2026-10-21): it should feel physical, like a force field. With Physics on, nodes have inertia,
// push each other apart and wires pull wired nodes together into a
// left-to-right flow; drag one and the others give way, let go and it floats into its place. Double-click
// pins a node where it is. With Physics off, everything stays where it is and you place nodes by hand.
// Switching Physics on saves the layout first; Undo goes back to it. Positions are kept per browser and
// domain. Named containers (frames) group nodes: Shift/Ctrl-click nodes, "Group", name it; drag its name bar
// to move it with its members, its corner to resize, double-click the name to rename. A node dropped inside a
// frame joins it, dragged out it leaves; members drift to its middle, other nodes are pushed off its border. Frames are kept
// per domain in AAC (the start of real groups, spec/streams.md). Wiring: drag from an output dot to an input
// dot of the same colour (type); click a wire to select it, Delete removes it. The first change keeps the
// current wiring plus the change; "Automatic wiring" goes back (aac/nodes/wires). No shadow DOM, so the panel's colours and skins
// apply. The panel sets .ws(type, data) and .panel, and calls .refresh() when modules change.

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const SVG = "http://www.w3.org/2000/svg";
const short = (t, n) => (t.length > n ? `${t.slice(0, n - 1)}…` : t); // the box is narrow; the details are below
const W = 176, HEAD = 40, ROW = 17; // node box: width, title area, one port row
const LINK = 70; // how far right of its source a wired node wants to sit (at least)
const STACK = 14; // gap between the cards of a column (a chain adding to the same record)
const COL = W + 110; // one slot column (owner, 2026-10-23: cards in slots, a column per step in the flow)
const FIELD = 150; // reach of a node's soft push beyond its box
const COLORS = ["var(--cyan)", "var(--magenta)", "var(--green)", "var(--amber)", "#8ab4ff", "#ff8a65", "#c792ea", "#7fdbca"];
const KIND = { source: "source", sink: "sink", transform: "transform", annotating: "adds to the record" };
// Settings that still live on a page of their own (owner: the settings are Appearance, Languages and Nodes; the
// rest moves into the cards): the card links there
const PAGES = { home: ["settings/entities", "Entities: what AAC may see and switch"], hearing: ["settings/audio", "Audio pipeline"],
  stt: ["settings/audio", "Audio pipeline"], voice: ["settings/audio/sources", "Microphones and speakers"],
  ha_tools: ["settings/entities", "Entities"], modules: ["settings/modules", "All modules"] };
const ROLE = { pool: "pool (dotted)", source: "source", endpoint: "in and out", transform: "in the line", model: "model", decide: "decide", sink: "reads along", tool: "tools (a loop)" };
const UNDO_STEPS = 10;
// A setting that works against the server's cache shows a warning while it is set that way (owner, 2026-10-23: the
// "Replace" tick of Header and Trailer); its checkbox toggles it live
const warnHtml = (key, f, v) => f.warn
  ? `<p class="hint" data-warn-key="${esc(key)}" data-warn-when="${f.warn.when}" style="color:var(--amber);margin:.1em 0 .5em;${Boolean(v) === f.warn.when ? "" : "display:none"}">⚠ ${esc(f.warn.text)}</p>` : "";
const BAR = 26, PAD = 26, GRIP = 16, EDGE = 50;
const FOLD_W = W + 30, FOLD_H = BAR + 30; // a folded frame: one card // frame: name bar, room around members, resize corner, border push reach

class AacNodes extends HTMLElement {
  constructor() {
    super();
    this._pos = {}; // node id → {x, y, vx, vy, pinned}
    this._view = { x: 0, y: 0, k: 1 }; // pan and zoom
    this._sel = null;
    this._raf = null;
    this._colors = {};
    this._undo = []; // layouts saved when Physics went on (newest last)
    this._physics = true;
    this._loaded = null; // the domain whose saved layout is loaded
    this._frames = []; // named containers {id, name, nodes, x, y, w, h} (AAC keeps them per domain)
    this._multi = new Set(); // nodes selected with Shift/Ctrl for "Group"
  }

  connectedCallback() {
    if (!this._svg) this._build();
    this.refresh();
    this._timer = setInterval(() => this.refresh(true), 10000); // the flow counts
    this._resize = new ResizeObserver(() => this._layout());
    this._resize.observe(this);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._resize?.disconnect();
    cancelAnimationFrame(this._raf);
    this._raf = null;
    this._save();
  }

  get _key() { return `aac-nodes-${this.panel?._state?.domain?.id}`; }

  async refresh(quiet = false) {
    if (!this.ws) return;
    if (!this.panel?._state?.domain) { setTimeout(() => this.refresh(quiet), 300); return; } // layouts are per domain
    try { this._data = await this.ws("aac/nodes"); this._error = null; } catch (e) { this._error = e.message || String(e); }
    if (this._error) { this._msg.textContent = this._error; return; }
    // the panel's sidebar lists the cards (click: select, drag into the grid: place or add)
    this.dispatchEvent(new CustomEvent("aac-nodes", { bubbles: true, composed: true, detail: this._data }));
    // the grid leaves out unwired built-in cards that were hidden, and those of modules that are off (owner:
    // "nodes at the bottom that just linger around"); the sidebar still lists them, a click brings one back
    this._all = this._data.nodes;
    this._data = { ...this._data, nodes: this._all.filter((n) => this._shown(n)) };
    const first = this._loaded !== this._key;
    if (!this._frameDrag) this._frames = (this._data.containers || []).map((f) => ({ ...f, nodes: [...f.nodes] }));
    this._place();
    this._mode();
    this._draw();
    if (first) { if (this._physics) this._fitWhenQuiet = true; else this._fit(); }
    if (!quiet) this._kick();
    if (!(quiet && this._editing && this._editing === (this._sel || this._selFrame))) this._info();
  }

  // ------------------------------------------------------------------ layout and storage
  _infoGrip() {
    const grip = this.querySelector(".nv-grip"), info = this.querySelector(".nv-info");
    const set = (w) => { if (w) this.style.setProperty("--nv-info-w", `${w}px`); else this.style.removeProperty("--nv-info-w"); };
    try { set(+localStorage.getItem("aac-nodes-info-w") || 0); } catch (e) { /* no storage: the default width */ }
    const save = (w) => { try { if (w) localStorage.setItem("aac-nodes-info-w", w); else localStorage.removeItem("aac-nodes-info-w"); } catch (e) { /* ignore */ } };
    grip.addEventListener("dblclick", () => { set(0); save(0); this._layout(); });
    grip.addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      grip.setPointerCapture(ev.pointerId);
      grip.classList.add("drag");
      const right = info.getBoundingClientRect().right;
      let w = 0;
      const move = (e) => { w = Math.round(Math.max(260, Math.min(right - e.clientX, window.innerWidth * .6))); set(w); };
      const up = () => { grip.classList.remove("drag"); grip.removeEventListener("pointermove", move); save(w); this._layout(); };
      grip.addEventListener("pointermove", move);
      grip.addEventListener("pointerup", up, { once: true });
      grip.addEventListener("pointercancel", up, { once: true });
    });
  }

  _build() {
    this.style.display = "block";
    this.classList.add("nv-host");
    this.innerHTML = `
      <details class="card nv-how"><summary style="cursor:pointer"><b>HOW IT WORKS</b> <span class="hint">· building your own chain, step by step</span></summary>
        <ol style="margin:.6em 0 0 1.2em;line-height:1.6">
          <li><b>A message starts at its card</b>: Panel chat, Voice, HA Assist, or automatic ones (heartbeat, triggers). The message is the whole conversation; every card writes into it.</li>
          <li><b>It runs along the line</b>: headers, context and trailers write their part (a header replaces its own entry, so the start of the conversation stays the same: fast answers).</li>
          <li><b>A model reads it all</b>. The cards wired to its output (square port: it decides) are its tools: calling one sends the conversation there; what that card writes comes back (wire it back into the model: a loop, dotted).</li>
          <li><b>The answer goes home</b>: to the card it started at (wired from the model's output). Round ports always fire; several wires each get a copy.</li>
          <li><b>Build your own:</b> Add node (e.g. Header, Trailer, Context, another Model, Decide), then drag from an output dot to an input dot of the same colour. Name a card's input (its settings) and models see that name as the tool. Click a wire and press Delete to remove it. The first change switches to "Wired by hand"; Automatic wiring goes back.</li>
          <li><b>Set a card up</b>: click it, change its settings in the panel on the right, Save.</li>
          <li><b>Look around</b>: the wheel scrolls the graph (Shift: sideways), Ctrl + wheel zooms, the middle button or the background drags it. The chat's ⚡ expert view shows the way each message took.</li>
          <li><b>Tidy up</b>: Shift-click cards, Group, name it; fold the frame (▾) into one card.</li>
        </ol></details>
      <style>
        /* the page fills the window and never scrolls itself (owner: a wheel a bit off moved the page) */
        .nv-host { display: flex !important; flex-direction: column; height: 100%; gap: 1em; }
        .nv-host > .nv-how { flex: none; margin: 0; max-height: 40%; overflow-y: auto; }
        .nv-main { display: flex; align-items: stretch; flex: 1; min-height: 0; }
        .nv-main > .nv-graph { flex: 1; min-width: 0; margin: 0; display: flex; flex-direction: column; }
        .nv-main > .nv-graph > .nv-stage { flex: 1; min-height: 240px; }
        .nv-main > .nv-info { flex: none; width: var(--nv-info-w, 380px); margin: 0; overflow-y: auto; }
        /* drag: the info panel's width (owner: "drag the sizes of the left and right sidebars") */
        .nv-grip { flex: none; width: 1em; cursor: col-resize; position: relative; touch-action: none; }
        .nv-grip::after { content: ""; position: absolute; top: 0; bottom: 0; left: calc(50% - 1px); width: 2px; }
        .nv-grip:hover::after, .nv-grip.drag::after { background: var(--line); }
        /* wire monitors (owner: "tap a wire, hit monitor, a window above it with the text") */
        .nv-mons { position: absolute; inset: 0; pointer-events: none; overflow: hidden; z-index: 2; }
        .nv-mon { position: absolute; transform: translate(-50%, calc(-100% - 8px)); pointer-events: auto; cursor: pointer;
          max-width: 20em; min-width: 8em; padding: .35em .6em; font-size: .78em; line-height: 1.35; border: 1px solid var(--cyan);
          background: var(--bg2, #111); box-shadow: 0 0 .6em rgba(0, 240, 255, .35); border-radius: .3em; }
        .nv-mon .t { color: var(--dim); font-size: .85em; display: flex; gap: .5em; }
        .nv-mon .t b { color: var(--cyan); font-weight: normal; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .nv-mon .x { overflow: hidden; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; white-space: pre-wrap; }
        .nv-mon.flash { animation: nvflash .8s; }
        @keyframes nvflash { from { box-shadow: 0 0 1.4em var(--cyan); } }
        @media (max-width: 1100px) {
          .nv-host { height: auto; }
          .nv-main { flex-direction: column; } .nv-main > .nv-info { width: auto; } .nv-grip { display: none; }
          .nv-main > .nv-graph > .nv-stage { flex: none; height: clamp(420px, calc(100vh - 260px), 1400px); }
        }
      </style>
      <div class="nv-main">
      <div class="card nv-graph" style="padding:0;overflow:hidden;position:relative">
        <div class="nv-bar" style="display:flex;gap:.6em;align-items:center;flex-wrap:wrap;padding:.6em .9em;border-bottom:1px solid var(--line)">
          <label class="check" style="margin:0;display:none"><input type="checkbox" class="nv-physics">Physics</label>
          <span class="hint nv-help"></span>
          <span style="flex:1"></span>
          <span class="hint nv-wiring"></span>
          <button class="btn" data-nv="auto" title="Forget the wires set by hand: everything wired by type again">Automatic wiring</button>
          <span style="position:relative"><button class="btn" data-nv="add" title="Add another node of a type that can exist more than once (e.g. a second model)">Add node</button>
            <span class="nv-addmenu" style="display:none;position:absolute;top:100%;left:0;z-index:5;min-width:16em;max-width:min(24em, 70vw);background:var(--bg2, #111);border:1px solid var(--line);border-radius:.4em;padding:.3em 0"></span></span>
          <button class="btn" data-nv="group" title="Put the nodes selected with Shift/Ctrl-click into a named container">Group</button>
          <button class="btn" data-nv="tidy" title="Lay every card out again in its slot (column = its place in the flow)">Tidy</button>
          <button class="btn" data-nv="undo" title="Back to the layout before Tidy">Undo</button>
          <button class="btn" data-nv="fit">Fit</button>
          <button class="btn" data-nv="vars" title="Every variable the cards' texts can use, and where it comes from">Variables</button>
          <button class="btn" data-go="settings/modules" title="Every module: switch on or off, hide, open its page">Modules</button>

        </div>
        <div class="nv-stage" style="position:relative;touch-action:none"><div class="nv-mons"></div></div>
        <div class="nv-legend" style="display:flex;gap:.9em;flex-wrap:wrap;padding:.5em .9em;border-top:1px solid var(--line);font-size:.8em"></div>
        <p class="hint nv-msg" style="margin:.5em .9em"></p>
      </div>
      <div class="nv-grip" title="Drag: width of this panel (double-click: back to normal)"></div>
      <div class="card nv-info thin-scroll"></div>
      </div>`;
    this._infoGrip();
    this._stage = this.querySelector(".nv-stage");
    this._msg = this.querySelector(".nv-msg");
    this._svg = document.createElementNS(SVG, "svg");
    this._svg.setAttribute("width", "100%");
    this._svg.setAttribute("height", "100%");
    this._svg.style.display = "block";
    this._svg.innerHTML = `<style>
        .nv-wire { fill: none; stroke-width: 1.6; opacity: .55; pointer-events: none; }
        .nv-wire.sel { opacity: 1; stroke-width: 3.4; }
        .nv-wire.card { stroke-dasharray: 2 5; stroke-linecap: round; stroke-width: 2; }
        .nv-hit { fill: none; stroke: transparent; stroke-width: 12; cursor: pointer; }
        .nv-link { fill: none; stroke: var(--text); stroke-width: 1.8; stroke-dasharray: 4 4; pointer-events: none; }
        .nv-dot { cursor: crosshair; }
        .nv-dot.ok { stroke: var(--text); stroke-width: 2; }
        .nv-wire.flow { stroke-dasharray: 6 5; animation: nvflow 1.2s linear infinite; opacity: .95; stroke-width: 2.2; }
        @keyframes nvflow { to { stroke-dashoffset: -22; } }
        .nv-node rect.box { fill: var(--bg2, #111); stroke: var(--line); stroke-width: 1.2; }
        .nv-node.sel rect.box { stroke: var(--cyan); stroke-width: 2.2; }
        .nv-node.off { opacity: .35; }
        .nv-node.off rect.box { stroke-dasharray: 4 3; }
        .nv-node .pin { display: none; }
        .nv-node.pinned .pin { display: inline; }
        .nv-node text { fill: var(--text); font-size: 12px; font-family: inherit; pointer-events: none; }
        .nv-node text.sub, .nv-node text.port { fill: var(--dim); font-size: 10px; }
        .nv-node { cursor: grab; }
        .nv-node.multi rect.box { stroke: var(--magenta); stroke-width: 2.2; }
        .nv-node.lit rect.box { stroke: var(--amber); stroke-width: 2.6; filter: drop-shadow(0 0 6px var(--amber)); }
        .nv-wire.lit { stroke: var(--amber) !important; opacity: 1; stroke-width: 3; stroke-dasharray: 8 6; animation: nvflow .8s linear infinite; }
        .nv-frame .fbox { fill: var(--cyan); fill-opacity: .035; stroke: var(--line); stroke-dasharray: 5 4; }
        .nv-frame.sel .fbox { stroke: var(--cyan); stroke-dasharray: none; }
        .nv-frame .fbar { fill: var(--cyan); fill-opacity: .09; cursor: move; }
        .nv-frame .fname { fill: var(--text); font-size: 13px; font-weight: 600; letter-spacing: .04em; pointer-events: none; }
        .nv-frame .fgrip { fill: var(--line); cursor: nwse-resize; }
        .nv-frame.folded .fgrip { display: none; }
        .nv-frame.folded .fbox { fill-opacity: .12; stroke-dasharray: none; }
        .nv-frame .ffold { fill: var(--text); font-size: 14px; cursor: pointer; }
        .nv-frame .fcount { fill: var(--dim); font-size: 11px; pointer-events: none; }
        .nv-node.hidden { display: none; }
      </style><g class="nv-world"><g class="nv-frames"></g><g class="nv-wires"></g><g class="nv-nodes"></g></g>`;
    this._stage.appendChild(this._svg);
    this._world = this._svg.querySelector(".nv-world");
    this._bind();
  }

  _color(type) {
    if (!this._colors[type]) this._colors[type] = COLORS[Object.keys(this._colors).length % COLORS.length];
    return this._colors[type];
  }

  _height(n) { return HEAD + ROW * Math.max(Object.keys(n.inputs).length, Object.keys(n.outputs).length, 1) + 6; }

  _folded(id) {
    // The folded frame a node is in (its wires attach to the frame's card), or nothing
    return this._frames.find((f) => f.collapsed && f.nodes.includes(id));
  }

  _port(n, port, side) {
    const f = this._folded(n.id);
    if (f) return { x: f.x + (side === "out" ? FOLD_W : 0), y: f.y + FOLD_H / 2 + 6 };
    const p = this._pos[n.id], names = Object.keys(side === "out" ? n.outputs : n.inputs);
    const right = (side === "out") !== !!this._mirror?.has(n.id); // a mirrored card: in on the right, out on the left
    return { x: p.x + (right ? W / 2 : -W / 2), y: p.y - this._height(n) / 2 + HEAD + ROW * names.indexOf(port) + ROW / 2 };
  }

  _read() {
    try { return JSON.parse(localStorage.getItem(this._key) || "{}"); } catch (e) { return {}; }
  }

  _place() {
    if (this._loaded !== this._key) { // another domain (or the first time): its saved layout
      // Positions live on the server (every browser the same); a layout from before that comes from this browser once
      const saved = this._read(), server = this._data.positions;
      this._pos = {};
      for (const [id, p] of Object.entries(server || saved.pos || {})) this._pos[id] = { x: p.x, y: p.y, vx: 0, vy: 0, pinned: !!p.pinned };
      this._sent = server ? JSON.stringify(server) : null;
      this._undo = Array.isArray(saved.undo) ? saved.undo : [];
      this._physics = false; // owner, 2026-10-23: no forces any more, cards sit in slots
      this._loaded = this._key;
      this._mode();
    }
    const nodes = this._data.nodes, byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
    // Slots (owner, 2026-10-23): a column per step in the flow: wired to a card's input = one column left of it,
    // to its output = one column right, to both (a loop) = its column, stacked below it. Separate chains (the
    // audio) below each other, unwired cards in a strip at the bottom. Rows ordered so wires cross less.
    const wires = this._wires(), back = this._backEdges(wires), rank = {};
    const hangs = new Set(wires.filter((w) => w.loop).map((w) => w.tool));
    const forward = wires.filter((w) => !back.has(w) && !w.loop && !hangs.has(w.a.id) && !hangs.has(w.b.id));
    for (let i = 0; i < nodes.length; i++) { // the longest way from a source
      let changed = false;
      for (const w of forward) if ((rank[w.b.id] ?? 0) < (rank[w.a.id] ?? 0) + 1) { rank[w.b.id] = (rank[w.a.id] ?? 0) + 1; changed = true; }
      if (!changed) break;
    }
    for (let i = 0; i < 3; i++) // a card that only feeds others sits right before the first of them
      for (const n of nodes) {
        if (forward.some((w) => w.b.id === n.id)) continue;
        const next = forward.filter((w) => w.a.id === n.id).map((w) => rank[w.b.id] ?? 0);
        if (next.length) rank[n.id] = Math.min(...next) - 1;
      }
    const wired = new Set(wires.flatMap((w) => [w.a.id, w.b.id]));
    const comp = {}, link = {}, comps = [];
    for (const w of forward) { (link[w.a.id] ||= []).push(w.b.id); (link[w.b.id] ||= []).push(w.a.id); }
    for (const n of nodes) {
      if (!wired.has(n.id) || hangs.has(n.id) || comp[n.id] !== undefined) continue;
      const todo = [n.id], members = [];
      comp[n.id] = comps.length;
      while (todo.length) { const id = todo.pop(); members.push(id); for (const o of link[id] || []) if (comp[o] === undefined) { comp[o] = comps.length; todo.push(o); } }
      comps.push(members);
    }
    comps.sort((x, y) => (y.some((id) => byId[id].role === "model") - x.some((id) => byId[id].role === "model")) || y.length - x.length);
    const want = {}, GAP = 30;
    const loopsOf = (m) => [...new Set(wires.filter((w) => w.loop && w.model === m && w.a.id === w.tool).map((w) => w.tool))];
    // a card with its loop cards under it is one tall slot
    const slotH = (id) => loopsOf(id).reduce((h, c) => h + GAP + this._height(byId[c]), this._height(byId[id]));
    let top = 0;
    for (const members of comps) {
      const base = Math.min(...members.map((id) => rank[id] ?? 0));
      const cols = {};
      for (const id of members) (cols[(rank[id] ?? 0) - base] ||= []).push(id);
      const order = Object.keys(cols).map(Number).sort((x, y) => x - y);
      const yOf = {};
      const lay = () => { for (const c of order) { let y = 0; for (const id of cols[c]) { yOf[id] = y; y += slotH(id) + GAP; } } };
      lay();
      for (let pass = 0; pass < 6; pass++) { // barycenter sweeps, left to right and back
        for (const c of pass % 2 ? [...order].reverse() : order) {
          const near = (id) => {
            const ys = forward.filter((w) => (pass % 2 ? w.a.id === id : w.b.id === id)).map((w) => yOf[pass % 2 ? w.b.id : w.a.id]).filter((v) => v !== undefined);
            return ys.length ? ys.reduce((t, v) => t + v, 0) / ys.length : yOf[id];
          };
          cols[c].sort((x, y) => near(x) - near(y));
          lay();
        }
      }
      const colH = (c) => cols[c].reduce((h, id) => h + slotH(id) + GAP, -GAP);
      const height = Math.max(0, ...order.map(colH));
      for (const c of order) {
        let y = top;
        for (const id of cols[c]) {
          const x = c * COL;
          want[id] = { x, y: y + this._height(byId[id]) / 2 };
          let yy = y + this._height(byId[id]) + GAP;
          for (const t of loopsOf(id)) { want[t] = { x, y: yy + this._height(byId[t]) / 2 }; yy += this._height(byId[t]) + GAP; }
          y += slotH(id) + GAP;
        }
      }
      top += height + 120;
    }
    let parked = 0;
    const fresh = new Set(); // placed now (saved positions are never moved)
    for (const n of nodes) {
      if (this._pos[n.id]) continue;
      fresh.add(n.id);
      const at = want[n.id] || { x: (parked++) * (W + 30), y: top + 60 }; // nothing wired: the strip at the bottom
      this._pos[n.id] = { x: at.x, y: at.y, vx: 0, vy: 0, pinned: false };
    }
    for (const id of Object.keys(this._pos)) if (!nodes.some((n) => n.id === id)) delete this._pos[id];
  }

  _snapshot() {
    return { pos: Object.fromEntries(Object.entries(this._pos).map(([id, p]) => [id, { x: Math.round(p.x), y: Math.round(p.y), pinned: p.pinned }])),
             frames: this._frames.map((f) => ({ ...f, nodes: [...f.nodes] })) };
  }

  _save() {
    if (!this._loaded) return;
    try {
      localStorage.setItem(this._key, JSON.stringify({ physics: this._physics, undo: this._undo.slice(-UNDO_STEPS) }));
    } catch (e) { /* private window: Physics and Undo are not kept */ }
    clearTimeout(this._posTimer); // the positions to the server, when the moving stops
    this._posTimer = setTimeout(() => {
      const pos = this._snapshot().pos, text = JSON.stringify(pos);
      if (text === this._sent || !this.ws) return;
      this._sent = text;
      this.ws("aac/nodes/positions", { positions: pos }).catch((e) => { this._sent = null; this._msg.textContent = `Positions not saved: ${e.message || e}`; });
    }, 800);
  }

  _wires() {
    if (!this._data) return [];
    const byId = Object.fromEntries(this._data.nodes.map((n) => [n.id, n]));
    const list = this._data.wires.map((w) => {
      const [a, ap] = w.from.split("."), [b, bp] = w.to.split(".");
      return byId[a] && byId[b] ? { a: byId[a], ap, b: byId[b], bp, type: byId[a].outputs[ap] } : null;
    }).filter(Boolean);
    // A node that offers tools and is reached only from models hangs under its model: those wires are its loop
    const fromModels = (id) => list.filter((w) => w.b.id === id).every((w) => w.a.role === "model");
    for (const w of list) {
      const tool = w.a.role === "tool" ? w.a : w.b.role === "tool" ? w.b : null, model = w.a.role === "model" ? w.a : w.b;
      w.loop = !!tool && model.role === "model" && tool !== model && fromModels(tool.id);
      w.model = w.loop ? model.id : null; w.tool = w.loop ? tool.id : null;
    }
    return list;
  }

  // ------------------------------------------------------------------ physics
  _mode() {
    this.querySelector(".nv-physics").checked = this._physics;
    this.querySelector(".nv-help").textContent = this._physics
      ? ""
      : "";
    this.querySelector('[data-nv="undo"]').disabled = !this._undo.length;
    const byHand = !!this._data?.by_hand;
    this.querySelector(".nv-wiring").textContent = byHand ? "Wired by hand" : "Wired automatically";
    this.querySelector('[data-nv="auto"]').style.display = byHand ? "" : "none";
  }

  _setPhysics(on) {
    if (on === this._physics) return;
    if (on) { // save the layout first, so Undo can bring it back
      this._undo.push(this._snapshot());
      this._undo = this._undo.slice(-UNDO_STEPS);
    } else {
      for (const p of Object.values(this._pos)) p.vx = p.vy = 0;
    }
    this._physics = on;
    this._mode();
    this._save();
    this._kick();
  }

  _tidy() {
    // every card back into its slot (Undo brings the layout before back)
    this._undo.push(this._snapshot());
    this._undo = this._undo.slice(-UNDO_STEPS);
    this._pos = {};
    this._place();
    this._save(); this._draw(); this._fit();
  }

  _snap(id) {
    // a dropped card goes into the nearest column, on the 20 px grid, below any card it would cover there
    const p = this._pos[id], n = this._data.nodes.find((x) => x.id === id);
    if (!p || !n) return;
    p.x = Math.round(p.x / COL) * COL;
    p.y = Math.round(p.y / 20) * 20;
    const h = this._height(n);
    for (let tries = 0; tries < 50; tries++) {
      const hit = this._data.nodes.find((o) => o.id !== id && this._pos[o.id] && Math.abs(this._pos[o.id].x - p.x) < W
        && Math.abs(this._pos[o.id].y - p.y) < (h + this._height(o)) / 2 + 10);
      if (!hit) break;
      p.y = this._pos[hit.id].y + (h + this._height(hit)) / 2 + 20;
    }
  }

  _undoLayout() {
    const last = this._undo.pop();
    if (!last) return;
    this._physics = false; // the restored layout stays as it was
    for (const [id, p] of Object.entries(last.pos || last)) if (this._pos[id]) Object.assign(this._pos[id], { x: p.x, y: p.y, vx: 0, vy: 0, pinned: !!p.pinned });
    if (last.frames) { this._frames = last.frames; this._saveFrames(); }
    this._mode();
    this._save();
    this._draw();
  }

  _kick() {
    if (!this._raf) this._raf = requestAnimationFrame(() => this._step());
  }

  _step() {
    this._raf = null;
    if (!this._physics || !this._data) { this._svg.classList.add("still"); this._layout(); return; }
    this._svg.classList.remove("still");
    const nodes = this._data.nodes.filter((n) => !this._folded(n.id)), P = this._pos; // folded cards rest
    // Every node pushes the others away, strongly when their boxes touch, softly within FIELD
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = P[nodes[i].id], b = P[nodes[j].id];
        let dx = b.x - a.x, dy = b.y - a.y;
        if (!dx && !dy) dx = Math.random() - .5;
        const d = Math.max(Math.hypot(dx, dy), 1), ux = dx / d, uy = dy / d;
        const touch = (W + (this._height(nodes[i]) + this._height(nodes[j])) / 2) / 2 + 20;
        let f = 0;
        if (d < touch) f = (touch - d) * .09; // boxes in each other: a firm push
        if (d < touch + FIELD) f += (1 - (d - Math.min(d, touch)) / FIELD) * .9; // the soft push
        a.vx -= ux * f; a.vy -= uy * f; b.vx += ux * f; b.vy += uy * f;
        // and boxes that overlap are moved apart along the axis where they overlap least
        const ox = W + 24 - Math.abs(dx), oy = (this._height(nodes[i]) + this._height(nodes[j])) / 2 + 18 - Math.abs(dy);
        if (ox > 0 && oy > 0) {
          if (oy < ox) { const s = Math.sign(dy) || 1, m = oy * .12; a.vy -= s * m; b.vy += s * m; }
          else { const s = Math.sign(dx) || 1, m = ox * .12; a.vx -= s * m; b.vx += s * m; }
        }
      }
    }
    // Wires want to be straight and run left to right (owner: runs that space each other out by themselves):
    // a target left of LINK past its source is pushed right firmly, a longer wire is fine (only a weak pull);
    // the height difference along a wire is pulled out
    const wires = this._wires(), ins = {}, outs = {}, back = this._backEdges(wires);
    for (const w of wires) {
      if (this._folded(w.a.id) || this._folded(w.b.id)) continue; // a folded frame stays where it is
      const o = this._port(w.a, w.ap, "out"), i = this._port(w.b, w.bp, "in");
      if (w.loop && w.b.id === w.tool) continue; // the model's call into its tool: the hanging below does it
      if (w.loop) { // a tool card hangs below its model (spread out by the push between cards)
        const m = P[w.b.id], c = P[w.a.id];
        const low = m.y + this._height(w.b) / 2 + 70 + this._height(w.a) / 2 - c.y;
        if (low > 0) c.vy += low * .08; else c.vy += low * .004;
        c.vx += (m.x - c.x) * .006;
        continue;
      }
      if (back.has(w)) { // closes a loop (an answer going back to its chat): no left-to-right, it arcs back
        const dy = (o.y - i.y) * .004;
        P[w.b.id].vy += dy; P[w.a.id].vy -= dy;
        continue;
      }
      (ins[w.b.id] ||= []).push(w); (outs[w.a.id] ||= []).push(w);
      if (w.a.kind === "annotating" && w.b.kind === "annotating") {
        // A chain of nodes adding to the same record stacks into a column (owner: one row was far too wide):
        // the next card sits right under the previous one, aligned
        const gap = (P[w.a.id].y + this._height(w.a) / 2 + STACK) - (P[w.b.id].y - this._height(w.b) / 2);
        const ax = P[w.a.id].x - P[w.b.id].x;
        P[w.b.id].vx += ax * .04; P[w.a.id].vx -= ax * .04;
        if (gap > 0) { P[w.b.id].vy += gap * .08; P[w.a.id].vy -= gap * .08; }
        else { P[w.b.id].vy += gap * .01; P[w.a.id].vy -= gap * .01; } // and not far below either
        continue;
      }
      // too short or backwards: pushed apart; longer: no pull at all (a long wire pulling its ends in made
      // whole chains telescope together)
      const dx = (o.x + LINK) - i.x, dy = o.y - i.y, kx = dx > 0 ? .06 : 0, ky = .03;
      P[w.b.id].vx += dx * kx; P[w.b.id].vy += dy * ky;
      P[w.a.id].vx -= dx * kx; P[w.a.id].vy -= dy * ky;
    }
    // Wires push nodes off them (owner): a card lying on a wire it does not belong to moves aside, so lines do
    // not run through cards and it stays clear which line goes where
    for (const w of wires) {
      if (back.has(w) || w.loop || (w.a.kind === "annotating" && w.b.kind === "annotating") || this._folded(w.a.id) || this._folded(w.b.id)) continue;
      const o = this._port(w.a, w.ap, "out"), i = this._port(w.b, w.bp, "in");
      const lx = i.x - o.x, ly = i.y - o.y, len2 = lx * lx + ly * ly;
      if (len2 < 1) continue;
      for (const n of nodes) {
        if (n.id === w.a.id || n.id === w.b.id || n.id === this._drag?.id) continue;
        const p = P[n.id], t = ((p.x - o.x) * lx + (p.y - o.y) * ly) / len2;
        if (t < 0 || t > 1) continue;
        const cx = o.x + lx * t, cy = o.y + ly * t, dx = p.x - cx, dy = p.y - cy;
        const reach = this._height(n) / 2 + 26 + (Math.abs(ly) / Math.sqrt(len2)) * W / 2; // a steep wire meets the card's width
        const d = Math.hypot(dx, dy);
        if (d >= reach) continue;
        const f = (reach - d) * .05, ux = d ? dx / d : 0, uy = d ? dy / d : 1;
        p.vx += ux * f; p.vy += uy * f;
        // the wire's ends get the counter-push, vertically only: without it the push drove a steady drift
        // downwards, sideways it made long wires telescope chains together
        P[w.a.id].vy -= uy * f / 2; P[w.b.id].vy -= uy * f / 2;
      }
    }
    // A node in the middle of a run is pulled onto the line between its neighbours: runs become straight rows
    for (const n of nodes) {
      if (n.kind === "annotating" || ins[n.id]?.length !== 1 || outs[n.id]?.length !== 1) continue; // columns stand
      const a = ins[n.id][0], b = outs[n.id][0];
      const before = this._port(a.a, a.ap, "out"), after = this._port(b.b, b.bp, "in");
      const mine = (this._port(n, a.bp, "in").y + this._port(n, b.ap, "out").y) / 2;
      P[n.id].vy += ((before.y + after.y) / 2 - mine) * .05;
    }
    // Nodes without wires (or switched off) settle in a strip below the pipelines, out of the way
    // (set once per wiring: following the lowest card was a ratchet, the parked cards pushed it down and the
    // strip followed, forever)
    if (this._floorKey !== this._backKey) {
      const wired = nodes.filter((n) => ins[n.id] || outs[n.id]);
      this._floor = wired.length ? Math.max(...wired.map((n) => P[n.id].y + this._height(n) / 2)) + 140 : 0;
      this._floorKey = this._backKey;
    }
    for (const n of nodes) if (!ins[n.id] && !outs[n.id]) P[n.id].vy += (this._floor - P[n.id].y) * .02;
    // Frames (owner): a gentle pull to the middle for members, a weak push off the border for everyone else
    // (within EDGE outside it, firmer when a node sits inside); the hand always wins while a node is held
    for (const f of this._frames) {
      const fw = f.collapsed ? FOLD_W : f.w, fh = f.collapsed ? FOLD_H : f.h;
      const cx = f.x + fw / 2, cy = f.y + (BAR + fh) / 2;
      for (const n of nodes) {
        if (n.id === this._drag?.id) continue;
        const p = P[n.id], hw = W / 2, hh = this._height(n) / 2;
        if (f.nodes.includes(n.id) && !f.collapsed) {
          p.vx += (cx - p.x) * .004; p.vy += (cy - p.y) * .004; // to the middle
          const l = f.x + PAD / 2 + hw, r = f.x + f.w - PAD / 2 - hw, t = f.y + BAR + PAD / 2 + hh, b = f.y + f.h - PAD / 2 - hh;
          if (p.x < l) p.vx += (Math.min(l, r) - p.x) * .06; else if (p.x > r) p.vx -= (p.x - Math.max(l, r)) * .06;
          if (p.y < t) p.vy += (Math.min(t, b) - p.y) * .06; else if (p.y > b) p.vy -= (p.y - Math.max(t, b)) * .06;
        } else {
          // gap between the node's box and the frame (negative: they overlap)
          const gx = Math.max(f.x - (p.x + hw), (p.x - hw) - (f.x + fw)), gy = Math.max(f.y - (p.y + hh), (p.y - hh) - (f.y + fh));
          const gap = Math.max(gx, gy);
          if (gap >= EDGE) continue;
          const push = gap < 0 ? Math.min(-gap, 60) * .08 + .6 : (1 - gap / EDGE) * .6;
          if (gx > gy) p.vx += Math.sign(p.x - cx || 1) * push; else p.vy += Math.sign(p.y - cy || 1) * push;
        }
      }
    }
    // No pull to the middle (owner: "only in boxes"); then inertia with friction
    let energy = 0;
    for (const n of nodes) {
      const p = P[n.id];
      if (n.id === this._drag?.id) { // held on an elastic band: it follows the cursor, the field still pushes it
        p.vx += (this._drag.tx - p.x) * .18; p.vy += (this._drag.ty - p.y) * .18;
        p.vx *= .62; p.vy *= .62;
      } else if (p.pinned) { p.vx = p.vy = 0; continue; } else { p.vx *= .7; p.vy *= .7; } // friction (owner: more drag)
      if (n.id !== this._drag?.id && Math.abs(p.vx) + Math.abs(p.vy) < .05) { p.vx = p.vy = 0; continue; } // small motion dies out
      const v = Math.hypot(p.vx, p.vy);
      if (v > 25) { p.vx *= 25 / v; p.vy *= 25 / v; } // no node flies off
      p.x += p.vx; p.y += p.vy;
      energy += v;
    }
    this._layout();
    // Quiet: the field rests until something moves (no CPU for an open view)
    // A local minimum is fine (owner): rest as soon as the motion is small
    if (energy > .15 * nodes.length || this._drag) this._raf = requestAnimationFrame(() => this._step());
    else {
      this._svg.classList.add("still");
      this._save();
      if (this._fitWhenQuiet) { this._fitWhenQuiet = false; this._fit(); }
    }
  }

  _backEdges(wires) {
    // Wires that close a loop, by name ("a.port>b.port"; wire objects are made anew on every step), found by a
    // depth-first walk from the sources and cached until the wiring changes. has(w) checks a wire.
    const name = (w) => `${w.a.id}.${w.ap}>${w.b.id}.${w.bp}`;
    const key = wires.map(name).join(",");
    if (this._backKey !== key) {
      // Answers flow back to where the message came from (into an endpoint), a tool hands back to its model:
      // those wires always arc back
      const back = new Set(wires.filter((w) => w.b.role === "endpoint" || (w.loop && w.a.id === w.tool)).map(name));
      const out = {}, state = {};
      for (const w of wires) if (!back.has(name(w)) && w.type !== "pool") (out[w.a.id] ||= []).push(w); // a pool is no step
      const visit = (id) => {
        state[id] = 1;
        for (const w of out[id] || []) {
          if (state[w.b.id] === 1) back.add(name(w));
          else if (!state[w.b.id]) visit(w.b.id);
        }
        state[id] = 2;
      };
      const hasIn = new Set(wires.map((w) => w.b.id));
      for (const n of this._data.nodes) if (["source", "endpoint"].includes(n.role) && !state[n.id]) visit(n.id); // messages start here
      for (const n of this._data.nodes) if (!hasIn.has(n.id) && !state[n.id]) visit(n.id); // other sources
      for (const n of this._data.nodes) if (!state[n.id]) visit(n.id);
      this._backKey = key; this._back = back;
    }
    const back = this._back;
    return { has: (w) => back.has(name(w)) };
  }

  // ------------------------------------------------------------------ drawing
  _draw() {
    this._drawing = true;
    try { this._drawNow(); } finally { this._drawing = false; }
  }

  _drawNow() {
    const nodesG = this._svg.querySelector(".nv-nodes"), wiresG = this._svg.querySelector(".nv-wires");
    this._mirror = this._mirrorSet(this._wires()); this._mirrorKey = [...this._mirror].sort().join(",");
    this._svg.querySelector(".nv-frames").innerHTML = this._frames.map((f) => `<g class="nv-frame ${f.collapsed ? "folded" : ""} ${this._selFrame === f.id ? "sel" : ""}" data-frame="${esc(f.id)}">
        <rect class="fbox" rx="10"></rect><rect class="fbar" rx="10" height="${BAR}"></rect>
        <text class="fname">${esc(f.name || "Unnamed")}</text><rect class="fgrip" width="${GRIP}" height="${GRIP}" rx="3"></rect>
        <text class="ffold" data-fold="${esc(f.id)}"><title>${f.collapsed ? "Unfold" : "Fold into one card"}</title>${f.collapsed ? "▸" : "▾"}</text>
        <text class="fcount">${f.collapsed ? `${f.nodes.length} card${f.nodes.length === 1 ? "" : "s"} inside` : ""}</text></g>`).join("");
    nodesG.innerHTML = this._data.nodes.map((n) => {
      const h = this._height(n), ins = Object.entries(n.inputs), outs = Object.entries(n.outputs);
      const row = (i) => -h / 2 + HEAD + ROW * i + ROW / 2;
      const m = this._mirror.has(n.id) ? -1 : 1, inX = -m * W / 2, outX = m * W / 2; // mirrored: in right, out left
      const flow = Object.values(n.recent || {}).reduce((s, v) => s + v, 0);
      return `<g class="nv-node ${this._folded(n.id) ? "hidden" : ""} ${n.on ? "" : "off"} ${this._lit?.has(n.id) ? "lit" : ""} ${this._sel === n.id ? "sel" : ""} ${this._multi.has(n.id) && this._multi.size > 1 ? "multi" : ""} ${this._pos[n.id]?.pinned ? "pinned" : ""}" data-node="${esc(n.id)}">
        <rect class="box" x="${-W / 2}" y="${-h / 2}" width="${W}" height="${h}" rx="7"><title>${esc(n.title)}: ${esc(n.description || "")}</title></rect>
        <text x="${-W / 2 + 10}" y="${-h / 2 + 17}">${esc(n.title)}<tspan class="pin" dx="4">📌</tspan></text>
        <text class="sub" x="${-W / 2 + 10}" y="${-h / 2 + 31}">${esc(short(`${n.module_name}${n.kind === "annotating" ? ` · adds ${n.adds.join(", ")}` : ""}${flow ? ` · ${flow} recent` : ""}`, 30))}</text>
        ${ins.map(([p, t], i) => `<circle class="nv-dot" data-dot="in" data-port="${esc(p)}" data-type="${esc(t)}" cx="${inX}" cy="${row(i)}" r="5.5" fill="${this._color(t)}"><title>${esc(p)}: ${esc(t)}</title></circle>
          <text class="port" x="${inX + m * 9}" y="${row(i) + 3.5}" ${m < 0 ? 'text-anchor="end"' : ""}>${esc(p)}</text>`).join("")}
        ${outs.map(([p, t], i) => `${(n.square || []).includes(p)
          ? `<rect class="nv-dot" data-dot="out" data-port="${esc(p)}" data-type="${esc(t)}" x="${outX - 5.5}" y="${row(i) - 5.5}" width="11" height="11" rx="1.5" fill="${this._color(t)}"><title>${esc(p)}: ${esc(t)} · square: the card decides which wire (drag to an input to wire)</title></rect>`
          : `<circle class="nv-dot" data-dot="out" data-port="${esc(p)}" data-type="${esc(t)}" cx="${outX}" cy="${row(i)}" r="5.5" fill="${this._color(t)}"><title>${esc(p)}: ${esc(t)} · round: every wire gets it (drag to an input to wire)</title></circle>`}
          <text class="port" x="${outX - m * 9}" y="${row(i) + 3.5}" ${m > 0 ? 'text-anchor="end"' : ""}>${esc(p)}</text>`).join("")}
      </g>`;
    }).join("");
    wiresG.innerHTML = this._wires().map((w, i) => {
      const flow = (w.a.recent || {})[w.ap] > 0;
      const key = `${w.a.id}.${w.ap}|${w.b.id}.${w.bp}`;
      const lit = this._litWires?.has(`${w.a.id}>${w.b.id}`) || (this._folded(w.a.id) && this._lit?.has(w.a.id) && this._lit?.has(w.b.id));
      const inner = this._folded(w.a.id) && this._folded(w.a.id) === this._folded(w.b.id); // inside a folded frame: not drawn
      if (inner) return "";
      return `<path class="nv-wire ${w.loop || w.type === "pool" ? "card" : ""} ${flow ? "flow" : ""} ${lit ? "lit" : ""} ${this._selWire === key ? "sel" : ""}" data-wire="${i}" stroke="${this._color(w.type)}"></path>
        <path class="nv-hit" data-wire="${i}" data-key="${esc(key)}"><title>${esc(w.a.title)}.${esc(w.ap)} → ${esc(w.b.title)}.${esc(w.bp)} (${esc(w.type)}) · click to select</title></path>`;
    }).join("");
    this.querySelector(".nv-legend").innerHTML = Object.keys(this._colors).sort()
      .map((t) => `<span><span style="display:inline-block;width:.75em;height:.75em;border-radius:50%;background:${this._color(t)};vertical-align:-.05em"></span> ${esc(t)}</span>`).join("");
    const away = (this._all || []).length - this._data.nodes.length;
    this._msg.textContent = [this._data.nodes.some((n) => !n.on) ? "Faded nodes belong to modules that are off in this domain." : "",
      away ? `${away} card${away > 1 ? "s" : ""} hidden (modules that are off, or hidden by you): the sidebar lists them, a click brings one back.` : ""].filter(Boolean).join(" ");
    this._layout();
  }

  _layout() {
    if (!this._stage) return;
    this._layoutMonitors();
    const v = this._view;
    this._world.setAttribute("transform", `translate(${v.x + this._stage.clientWidth / 2},${v.y + this._stage.clientHeight / 2}) scale(${v.k})`);
    for (const g of this._svg.querySelectorAll(".nv-node")) {
      const p = this._pos[g.dataset.node];
      if (p) g.setAttribute("transform", `translate(${p.x},${p.y})`);
    }
    for (const g of this._svg.querySelectorAll(".nv-frame")) {
      const f = this._frames.find((x) => x.id === g.dataset.frame);
      if (!f) continue;
      g.setAttribute("transform", `translate(${f.x},${f.y})`);
      const [box, bar, name, grip, fold, count] = g.children, fw = f.collapsed ? FOLD_W : f.w, fh = f.collapsed ? FOLD_H : f.h;
      box.setAttribute("width", fw); box.setAttribute("height", fh);
      bar.setAttribute("width", fw);
      name.setAttribute("x", 12); name.setAttribute("y", BAR / 2 + 4.5);
      grip.setAttribute("x", fw - GRIP - 3); grip.setAttribute("y", fh - GRIP - 3);
      fold.setAttribute("x", fw - 20); fold.setAttribute("y", BAR / 2 + 5);
      count.setAttribute("x", 12); count.setAttribute("y", BAR + 18);
    }
    const wires = this._wires(), mirror = this._mirrorSet(wires), key = [...mirror].sort().join(",");
    if (key !== this._mirrorKey) { // a card came into or left its partner's column: its ports change sides
      this._mirror = mirror; this._mirrorKey = key;
      if (!this._drawing) { this._draw(); return; }
    }
    const d = this._routes(wires);
    for (const path of this._svg.querySelectorAll(".nv-wire, .nv-hit")) if (d[path.dataset.wire]) path.setAttribute("d", d[path.dataset.wire]);
  }

  // Mirrored cards (owner, 2026-10-23: "when a node is only connected in its row its in and outputs should reverse
  // compared to its partner, if it's not the upper one"): every wire of the card goes to cards in its own column
  // above it (a tool card under its model): input on the right, output on the left, so both wires of the loop run
  // straight down the sides instead of around the cards
  _mirrorSet(wires) {
    const partners = {};
    for (const w of wires) { (partners[w.a.id] ||= []).push(w.b.id); (partners[w.b.id] ||= []).push(w.a.id); }
    const found = new Set();
    for (const [id, list] of Object.entries(partners)) {
      const p = this._pos[id];
      if (!p || this._folded(id)) continue;
      if (list.every((o) => o !== id && this._pos[o] && !this._folded(o) && Math.abs(this._pos[o].x - p.x) < 4 && this._pos[o].y < p.y)) found.add(id);
    }
    return found;
  }

  _routes(wires) {
    // Wire paths (owner: lines should not cross cards or each other): a short wire forward is a curve; a loop
    // between a model and the cards stacked under it runs in a side lane (the deeper the card, the further out,
    // so they do not cross); a wire going back or far runs below the cards between its ends, in a lane of its own
    const box = (n) => { const p = this._pos[n.id], h = this._height(n); return { x: p.x, top: p.y - h / 2, bottom: p.y + h / 2 }; };
    const out = {}, below = [];
    // which chain a card belongs to (connected by any wire): a lane only steps around cards of its own chain
    const chain = {}, near = {};
    for (const w of wires) { (near[w.a.id] ||= []).push(w.b.id); (near[w.b.id] ||= []).push(w.a.id); }
    let cid = 0;
    for (const n of this._data.nodes) {
      if (chain[n.id] !== undefined || !near[n.id]) continue;
      const todo = [n.id]; chain[n.id] = cid;
      while (todo.length) for (const o of near[todo.pop()] || []) if (chain[o] === undefined) { chain[o] = cid; todo.push(o); }
      cid++;
    }
    const order = {}; // a model's loop cards from the top down
    for (const w of wires) if (w.loop && this._pos[w.tool]) (order[w.model] ||= new Set()).add(w.tool);
    for (const [m, set] of Object.entries(order)) order[m] = [...set].sort((a, b) => this._pos[a].y - this._pos[b].y);
    const path = (pts) => pts.map((q, k) => `${k ? "L" : "M"}${Math.round(q[0])},${Math.round(q[1])}`).join(" ");
    wires.forEach((w, idx) => {
      if (!this._pos[w.a.id] || !this._pos[w.b.id]) return;
      const o = this._port(w.a, w.ap, "out"), i = this._port(w.b, w.bp, "in");
      const mirA = this._mirror?.has(w.a.id), mirB = this._mirror?.has(w.b.id);
      if (mirA || mirB) { // a mirrored card: both ends on the same side, a lane straight down that side
        const k = w.loop ? (order[w.model] || []).indexOf(w.tool) : 0, rightSide = !mirA; // normal out = right
        const lane = rightSide ? Math.max(o.x, i.x) + 18 + Math.max(0, k) * 7 : Math.min(o.x, i.x) - 18 - Math.max(0, k) * 7;
        out[idx] = path([[o.x, o.y], [lane, o.y], [lane, i.y], [i.x, i.y]]);
        return;
      }
      const stacked = w.loop && Math.abs(this._pos[w.a.id].x - this._pos[w.b.id].x) < 4;
      if (stacked) {
        const k = order[w.model].indexOf(w.tool), t = box(w.loop && w.a.id === w.tool ? w.a : w.b);
        if (w.b.id === w.tool) { // model → its card: down the right side, in above the card
          const lane = o.x + 18 + k * 7;
          out[idx] = path([[o.x, o.y], [lane, o.y], [lane, t.top - 9], [i.x - 12, t.top - 9], [i.x - 12, i.y], [i.x, i.y]]);
        } else { // card → model: out below the card, up the left side
          const lane = i.x - 18 - k * 7;
          out[idx] = path([[o.x, o.y], [o.x + 8, o.y], [o.x + 8, t.bottom + 9], [lane, t.bottom + 9], [lane, i.y], [i.x, i.y]]);
        }
        return;
      }
      if (i.x > o.x + 20 && i.x - o.x < 3.5 * COL) {
        const bend = Math.max(40, Math.abs(i.x - o.x) / 2);
        out[idx] = `M${o.x},${o.y} C${o.x + bend},${o.y} ${i.x - bend},${i.y} ${i.x},${i.y}`;
        return;
      }
      below.push({ idx, o, i, w });
    });
    // back or far: below the lowest card between the ends (and below the lanes already taken there)
    below.sort((p, q) => Math.abs(p.i.x - p.o.x) - Math.abs(q.i.x - q.o.x)); // short ones inside, long ones outside
    const taken = [];
    for (const { idx, o, i, w } of below) {
      const lo = Math.min(o.x, i.x) - W, hi = Math.max(o.x, i.x) + W;
      let floor = Math.max(o.y, i.y);
      for (const n of this._data.nodes) {
        if (!this._pos[n.id] || this._folded(n.id) || chain[n.id] !== chain[w.a.id]) continue;
        const b = box(n);
        if (b.x > lo && b.x < hi) floor = Math.max(floor, b.bottom);
      }
      for (const t of taken) if (t.hi > lo && t.lo < hi && t.chain === chain[w.a.id]) floor = Math.max(floor, t.y);
      const y = floor + 22, k = taken.length % 6;
      taken.push({ lo, hi, y, chain: chain[w.a.id] });
      const ox = o.x + 14 + k * 5, ix = i.x - 14 - k * 5;
      out[idx] = path([[o.x, o.y], [ox, o.y], [ox, y], [ix, y], [ix, i.y], [i.x, i.y]]);
    }
    return out;
  }

  _fit() {
    const ps = Object.values(this._pos);
    if (!ps.length || !this._stage.clientWidth) return;
    const xs = [...ps.map((p) => p.x), ...this._frames.flatMap((f) => [f.x + W / 2, f.x + f.w - W / 2])];
    const ys = [...ps.map((p) => p.y), ...this._frames.flatMap((f) => [f.y + 60, f.y + f.h - 60])];
    const w = Math.max(...xs) - Math.min(...xs) + W + 80, h = Math.max(...ys) - Math.min(...ys) + 200;
    const k = Math.min(1.3, this._stage.clientWidth / w, this._stage.clientHeight / h);
    this._view = { k, x: -(Math.max(...xs) + Math.min(...xs)) / 2 * k, y: -(Math.max(...ys) + Math.min(...ys)) / 2 * k };
    this._layout();
  }

  // ------------------------------------------------------------------ interaction
  _world2(e) {
    const r = this._stage.getBoundingClientRect(), v = this._view;
    return { x: (e.clientX - r.left - r.width / 2 - v.x) / v.k, y: (e.clientY - r.top - r.height / 2 - v.y) / v.k };
  }

  _bind() {
    this.querySelector(".nv-physics").addEventListener("change", (e) => this._setPhysics(e.target.checked));
    this.addEventListener("change", (e) => {  // a setting's warning follows its checkbox
      const key = e.target?.type === "checkbox" && (e.target.dataset.instKey || e.target.dataset.modKey);
      const warn = key && this.querySelector(`.nv-info [data-warn-key="${key}"]`);
      if (warn) warn.style.display = String(e.target.checked) === warn.dataset.warnWhen ? "" : "none";
    });
    this.addEventListener("change", async (e) => {  // an output's "only one wire"
      const key = e.target?.dataset?.single;
      if (!key) return;
      const single = new Set(this._data.single || []);
      if (e.target.checked) single.add(key); else single.delete(key);
      try { await this.ws("aac/nodes/single", { single: [...single] }); } catch (err) { this._msg.textContent = `Not saved: ${err.message || err}`; }
      await this.refresh();
    });
    // Typing in the info box: the 10 s refresh leaves it alone until it is saved or another card is chosen
    this.querySelector(".nv-info").addEventListener("input", () => { this._editing = this._sel || this._selFrame; });
    this.addEventListener("click", (e) => {
      const b = e.target.closest("[data-nv]");
      if (b?.dataset.nv === "fit") this._fit();
      if (b?.dataset.nv === "undo") this._undoLayout();
      if (b?.dataset.nv === "tidy") this._tidy();
      if (b?.dataset.nv === "group") this._group();
      if (b?.dataset.nv === "auto") this._automatic();
      if (b?.dataset.nv === "add") this._addNode();
      if (b?.dataset.nv === "add-type") this._addOf(b.dataset.type);
      const step = e.target.closest("[data-try-node]");
      if (step) { this._sel = step.dataset.tryNode; this._selFrame = null; this._selWire = null; this._draw(); this._info(); }
      if (b?.dataset.nv === "inst-save") this._saveInstance(b.dataset.node);
      if (b?.dataset.nv === "inst-remove") this._removeInstance(b.dataset.node);
      if (b?.dataset.nv === "hide") this._hide(b.dataset.node, true);
      if (b?.dataset.nv === "title-save") this._retitle(b.dataset.node);
      if (b?.dataset.nv === "monitor" || b?.dataset.nv === "mon-off") this._toggleMonitor(b.dataset.mon);
      if (b?.dataset.nv === "vars") { this._sel = null; this._selWire = null; this._selFrame = null; this._showVars = true; this._draw(); this._info(); }
      if (b?.dataset.nv === "mod-save") this._saveModule(b.dataset.module);
      if (b?.dataset.nv === "core-save") this._saveCore(b.dataset.node);
      if (b?.dataset.nv === "names-save") this._saveNames(b.dataset.node);
      if (b?.dataset.nv === "unwire") this._unwire(this._selWire);
      if (b?.dataset.nv === "rename") this._rename(b.dataset.frame);
      if (b?.dataset.nv === "fold") this._fold(b.dataset.frame);
      if (b?.dataset.nv === "ungroup") {
        this._frames = this._frames.filter((f) => f.id !== b.dataset.frame);
        this._selFrame = null;
        this._saveFrames(); this._draw(); this._info(); this._kick();
      }
      const go = e.target.closest("[data-go]");
      if (go) this.panel?._go(go.dataset.go);
    });
    this._svg.addEventListener("mousedown", (e) => { if (e.button === 1) e.preventDefault(); }); // no browser autoscroll
    this._stage.addEventListener("dragover", (e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; });
    this._stage.addEventListener("drop", (e) => {
      e.preventDefault();
      const what = e.dataTransfer.getData("text/plain") || "";
      if (what.startsWith("node:") || what.startsWith("type:")) this._drop(what, e);
    });
    // Two fingers (a phone, a tablet): pinch to zoom around the fingers, move them to pan
    this._touches = new Map();
    const pinchState = () => {
      const [a, b] = [...this._touches.values()], r = this._stage.getBoundingClientRect();
      return { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, mx: (a.x + b.x) / 2 - r.left - r.width / 2, my: (a.y + b.y) / 2 - r.top - r.height / 2 };
    };
    this._svg.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "touch") {
        this._touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (this._touches.size === 2) { // a second finger: whatever the first one started becomes a pinch
          if (this._link) { this._link.path.remove(); this._link = null; }
          if (this._drag) { this._drag = null; }
          this._pan = null;
          this._pinch = { start: pinchState(), view: { ...this._view } };
          return;
        }
      }
    }, { capture: true });
    this._svg.addEventListener("pointermove", (e) => {
      if (e.pointerType !== "touch" || !this._touches.has(e.pointerId)) return;
      this._touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (!this._pinch || this._touches.size < 2) return;
      e.stopImmediatePropagation();
      const now = pinchState(), s0 = this._pinch.start, v0 = this._pinch.view;
      const k = Math.min(3, Math.max(.2, v0.k * now.d / s0.d));
      // the world point that was under the fingers' midpoint stays under it
      this._view = { k, x: now.mx - (s0.mx - v0.x) * k / v0.k, y: now.my - (s0.my - v0.y) * k / v0.k };
      this._layout();
    }, { capture: true });
    const lift = (e) => {
      if (e.pointerType !== "touch") return;
      this._touches.delete(e.pointerId);
      if (this._touches.size < 2 && this._pinch) { this._pinch = null; this._pan = null; e.stopImmediatePropagation(); }
    };
    this._svg.addEventListener("pointerup", lift, { capture: true });
    this._svg.addEventListener("pointercancel", lift, { capture: true });
    this._svg.addEventListener("pointerdown", (e) => {
      if (this._pinch) return;
      const g = e.target.closest(".nv-node"), fg = e.target.closest(".nv-frame");
      this._svg.setPointerCapture(e.pointerId);
      if (e.button === 1) { // the middle button drags the view, wherever you grab
        e.preventDefault();
        this._pan = { x: e.clientX - this._view.x, y: e.clientY - this._view.y };
        return;
      }
      this.focus({ preventScroll: true }); // for Delete on a selected wire
      if (g && e.target.dataset.dot === "out") { // start a wire
        this._link = { from: `${g.dataset.node}.${e.target.dataset.port}`, type: e.target.dataset.type,
                       o: this._port(this._data.nodes.find((n) => n.id === g.dataset.node), e.target.dataset.port, "out") };
        const path = document.createElementNS(SVG, "path");
        path.setAttribute("class", "nv-link");
        this._svg.querySelector(".nv-wires").appendChild(path);
        this._link.path = path;
        for (const d of this._svg.querySelectorAll('.nv-dot[data-dot="in"]')) d.classList.toggle("ok", d.dataset.type === this._link.type);
        return;
      }
      if (e.target.classList.contains("nv-hit")) { // select a wire
        this._selWire = e.target.dataset.key; this._sel = null; this._selFrame = null;
        this._draw(); this._info();
        return;
      }
      if (this._selWire) { this._selWire = null; this._draw(); }
      if (g) {
        const id = g.dataset.node, w = this._world2(e), p = this._pos[id];
        this._drag = { id, dx: p.x - w.x, dy: p.y - w.y, tx: p.x, ty: p.y, moved: false };
        // Shift/Ctrl adds to the selection for "Group"; a plain click selects only this one
        if (e.shiftKey || e.ctrlKey || e.metaKey) { if (this._multi.has(id)) this._multi.delete(id); else this._multi.add(id); }
        else this._multi = new Set([id]);
        this._sel = id; this._selFrame = null;
        this._draw();
        this._kick();
      } else if (e.target.dataset.fold) { // fold or unfold a frame
        this._fold(e.target.dataset.fold);
        return;
      } else if (fg && (e.target.classList.contains("fbar") || e.target.classList.contains("fgrip"))) {
        const f = this._frames.find((x) => x.id === fg.dataset.frame), w = this._world2(e);
        this._frameDrag = { f, resize: e.target.classList.contains("fgrip"), last: w, moved: false };
        this._selFrame = f.id; this._sel = null;
        this._draw(); this._info();
      } else {
        this._pan = { x: e.clientX - this._view.x, y: e.clientY - this._view.y };
      }
    });
    this._svg.addEventListener("pointermove", (e) => {
      if (this._link) {
        const w = this._world2(e), o = this._link.o, bend = Math.max(40, Math.abs(w.x - o.x) / 2);
        this._link.path.setAttribute("d", `M${o.x},${o.y} C${o.x + bend},${o.y} ${w.x - bend},${w.y} ${w.x},${w.y}`);
        return;
      }
      if (this._drag) {
        const w = this._world2(e), p = this._pos[this._drag.id];
        this._drag.tx = w.x + this._drag.dx; this._drag.ty = w.y + this._drag.dy;
        this._drag.moved = true;
        if (this._physics) this._kick();
        else { p.x = this._drag.tx; p.y = this._drag.ty; this._layout(); } // Physics off: exactly where you put it
      } else if (this._frameDrag) {
        const fd = this._frameDrag, f = fd.f, w = this._world2(e), dx = w.x - fd.last.x, dy = w.y - fd.last.y;
        fd.last = w; fd.moved = true;
        if (fd.resize) { f.w = Math.max(W + PAD + 20, f.w + dx); f.h = Math.max(BAR + 90, f.h + dy); }
        else { // the frame takes its members along
          f.x += dx; f.y += dy;
          for (const id of f.nodes) if (this._pos[id]) { this._pos[id].x += dx; this._pos[id].y += dy; }
        }
        this._layout();
        if (this._physics) this._kick();
      } else if (this._pan) {
        this._view.x = e.clientX - this._pan.x; this._view.y = e.clientY - this._pan.y;
        this._layout();
      }
    });
    const up = (e) => {
      if (this._link) { // dropped on an input of the same type: a new wire
        const link = this._link;
        this._link = null;
        link.path.remove();
        for (const d of this._svg.querySelectorAll(".nv-dot.ok")) d.classList.remove("ok");
        const hit = e && this.getRootNode().elementFromPoint?.(e.clientX, e.clientY);
        const dot = hit?.closest?.('.nv-dot[data-dot="in"]'), g = dot?.closest(".nv-node");
        if (dot && g && dot.dataset.type === link.type && !link.from.startsWith(`${g.dataset.node}.`)) {
          this._wire(link.from, `${g.dataset.node}.${dot.dataset.port}`);
        } else if (dot) {
          this._msg.textContent = `Not wired: ${link.type} only goes into an input of the same type (same colour).`;
        }
        return;
      }
      if (this._drag) {
        const p = this._pos[this._drag.id];
        if (this._drag.moved) this._snap(this._drag.id); // into its slot
        if (this._drag.moved) this._joinFrame(this._drag.id, this._physics ? this._drag.tx : p.x, this._physics ? this._drag.ty : p.y);
        this._drag = null;
        this._save();
        this._draw(); this._info(); this._kick();
      }
      if (this._frameDrag) {
        if (this._frameDrag.moved) this._saveFrames();
        this._frameDrag = null;
        this._save(); this._kick();
      }
      this._pan = null;
    };
    this._svg.addEventListener("pointerup", up);
    this._svg.addEventListener("pointercancel", up);
    this._svg.addEventListener("dblclick", (e) => {
      const fg = e.target.closest(".nv-frame");
      if (fg && e.target.classList.contains("fbar")) { this._rename(fg.dataset.frame); return; }
      const g = e.target.closest(".nv-node");
      if (!g) return;
      const p = this._pos[g.dataset.node];
      p.pinned = !p.pinned;
      this._save(); this._draw(); this._kick();
    });
    this.tabIndex = 0;
    this.addEventListener("keydown", (e) => {
      if ((e.key === "Delete" || e.key === "Backspace") && this._selWire && !e.target.closest("input, textarea")) {
        e.preventDefault();
        this._unwire(this._selWire);
      }
    });
    this._svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = this._stage.getBoundingClientRect(), v = this._view;
      if (!e.ctrlKey && !e.metaKey) { // the wheel scrolls the graph (Shift: sideways); Ctrl + wheel (or a pinch) zooms
        const f = e.deltaMode === 1 ? 16 : 1;
        v.x -= (e.shiftKey ? e.deltaY : e.deltaX) * f; v.y -= (e.shiftKey ? 0 : e.deltaY) * f;
        this._layout();
        return;
      }
      const k = Math.min(3, Math.max(.2, v.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
      const mx = e.clientX - r.left - r.width / 2, my = e.clientY - r.top - r.height / 2;
      v.x = mx - (mx - v.x) * k / v.k; v.y = my - (my - v.y) * k / v.k; v.k = k;
      this._layout();
    }, { passive: false });
  }

  // ------------------------------------------------------------------ wiring by hand
  _current() {
    return this._data.wires.map((w) => ({ from: w.from, to: w.to }));
  }

  async _setWires(wires) {
    try { await this.ws("aac/nodes/wires", { wires }); this._msg.textContent = ""; }
    catch (err) { this._msg.textContent = `Not saved: ${err.message || err}`; }
    await this.refresh();
  }

  _wire(from, to) {
    let wires = this._current();
    if (wires.some((w) => w.from === from && w.to === to)) return;
    if ((this._data.single || []).includes(from)) wires = wires.filter((w) => w.from !== from); // one wire: it moves
    this._setWires([...wires, { from, to }]);
  }

  _unwire(key) {
    if (!key) return;
    const [from, to] = key.split("|");
    this._selWire = null;
    this._setWires(this._current().filter((w) => !(w.from === from && w.to === to)));
  }

  async _automatic() {
    const ok = this.panel?._confirm ? await this.panel._confirm("Forget the wires set by hand? Everything is wired by type again.", "Automatic wiring", true)
      : window.confirm("Forget the wires set by hand?");
    if (ok) this._setWires(null);
  }

  // ------------------------------------------------------------------ added nodes (types vs. instances)
  _instances() {
    return Object.fromEntries((this._all || this._data.nodes).filter((n) => n.type)
      .map((n) => [n.id, { type: n.type, title: n.title, settings: { ...n.settings } }]));
  }

  async _setInstances(instances) {
    try { await this.ws("aac/nodes/instances", { instances }); this._msg.textContent = ""; }
    catch (err) { this._msg.textContent = `Not saved: ${err.message || err}`; }
    await this.refresh();
  }

  _addNode() {
    // A small menu of the types that can be added (one: add it at once)
    const types = this._data.types || [];
    const menu = this.querySelector(".nv-addmenu");
    if (!types.length) { this._msg.textContent = "No node type can be added yet."; return; }
    if (types.length === 1) { this._addOf(types[0].id); return; }
    if (menu.style.display !== "none") { menu.style.display = "none"; return; }
    // grouped by category (In and out, Text, Models, Tools, Keep and write, Audio …)
    const order = this._data.categories || [...new Set(types.map((t) => t.category))];
    menu.innerHTML = order.map((g) => {
      const items = types.filter((t) => (t.category || "Other") === g);
      return items.length ? `<div class="hint" style="padding:.4em .9em .1em;font-size:.75em;letter-spacing:.08em">${esc(g.toUpperCase())}</div>
        ${items.map((t) => `<button class="ditem" data-nv="add-type" data-type="${esc(t.id)}" title="${esc(t.description || "")}" style="display:block;width:100%;white-space:normal">
          ${esc(t.title)}<span class="hint" style="display:block;font-size:.78em;line-height:1.3;margin-top:.15em">${esc(short(t.description || "", 90))}</span></button>`).join("")}` : "";
    }).join("");
    menu.style.maxHeight = "60vh"; menu.style.overflow = "auto"; menu.style.width = "22em";
    menu.style.display = "";
  }

  // ------------------------------------------------------------------ wire monitors
  // A window above a wire with what passes it, live; a click shows everything in the newest message on the right
  _monKey(w) { return `${w.a.id}.${w.ap}|${w.b.id}.${w.bp}`; }

  _layoutMonitors() {
    const box = this.querySelector(".nv-mons");
    if (!box) return;
    this._monitors ||= new Map();
    const wires = Object.fromEntries(this._wires().map((w) => [this._monKey(w), w])), v = this._view;
    const W2 = this._stage.clientWidth / 2, H2 = this._stage.clientHeight / 2;
    for (const [key, m] of this._monitors) {
      const w = wires[key];
      if (!w) { this._monitors.delete(key); continue; }
      const a = this._port(w.a, w.ap, "out"), b = this._port(w.b, w.bp, "in");
      const x = (a.x + b.x) / 2 * v.k + v.x + W2, y = (a.y + b.y) / 2 * v.k + v.y + H2;
      let el = box.querySelector(`[data-mon="${CSS.escape(key)}"]`);
      if (!el) {
        el = document.createElement("div");
        el.className = "nv-mon";
        el.dataset.mon = key;
        el.addEventListener("click", () => { this._monSel = key; this._sel = null; this._selWire = null; this._selFrame = null; this._draw(); this._info(); });
        box.appendChild(el);
      }
      const last = (m.records || []).at(-1);
      el.innerHTML = `<div class="t"><b>${esc(w.a.title)} › ${esc(w.b.title)}</b><span>${(m.records || []).length}</span></div>
        <div class="x">${last ? esc(String(last.text ?? last.name ?? JSON.stringify(last)).slice(0, 300)) : '<span class="hint">waiting for something to pass…</span>'}</div>`;
      el.style.left = `${x}px`;
      el.style.top = `${y}px`;
    }
    for (const el of box.querySelectorAll("[data-mon]")) if (!this._monitors.has(el.dataset.mon)) el.remove();
  }

  async _loadMonitor(key, flash = false) {
    const m = this._monitors?.get(key);
    if (!m) return;
    try { Object.assign(m, await this.ws("aac/nodes/monitor", { wire: key.split("|")[0] })); } catch (e) { m.error = e.message || String(e); }
    this._layoutMonitors();
    if (flash) {
      const el = this.querySelector(`.nv-mons [data-mon="${CSS.escape(key)}"]`);
      el?.classList.remove("flash"); void el?.offsetWidth; el?.classList.add("flash");
    }
    if (this._monSel === key && !this._editingNow()) this._info();
  }

  _editingNow() { return this.querySelector(".nv-info")?.contains(document.activeElement) && document.activeElement.matches("input, textarea, select"); }

  onFlow(ev) {  // the panel: something passed an output
    if (!this._monitors?.size || ev.domain !== this.panel?._state?.domain?.id) return;
    for (const key of this._monitors.keys()) {
      if (key.split("|")[0] !== ev.wire) continue;
      clearTimeout(this._monTimers?.[key]);
      (this._monTimers ||= {})[key] = setTimeout(() => this._loadMonitor(key, true), 250);
    }
  }

  _toggleMonitor(key) {
    this._monitors ||= new Map();
    if (this._monitors.has(key)) { this._monitors.delete(key); if (this._monSel === key) this._monSel = null; }
    else { this._monitors.set(key, {}); this._loadMonitor(key); }
    this._layoutMonitors();
    this._info();
  }

  _monitorHtml(key) {
    const m = this._monitors.get(key) || {}, msg = m.message, [from, to] = key.split("|");
    const name = (id) => (this._all || this._data.nodes).find((n) => n.id === id.split(".")[0])?.title || id;
    const recs = (m.records || []).slice().reverse().map((r) => `<div style="padding:.3em 0;border-bottom:1px solid var(--line)">
        <span class="hint">${esc(new Date(r.time).toLocaleTimeString())}</span> ${esc(String(r.text ?? r.name ?? JSON.stringify(r)).slice(0, 400))}</div>`).join("");
    const entries = (msg?.entries || []).map((e) => `<details style="margin:.2em 0"><summary><b>${esc(name(e.by))}</b> · ${esc(e.kind)} <span class="hint">${esc(e.text.replace(/\s+/g, " ").slice(0, 70))}</span></summary>
        <pre style="white-space:pre-wrap;font-size:.85em;margin:.3em 0">${esc(e.text)}${Object.keys(e.data || {}).length ? `\n\n${esc(JSON.stringify(e.data, null, 1))}` : ""}</pre></details>`).join("");
    return `<h2>MONITOR</h2><p class="hint">${esc(name(from))} › ${esc(name(to))} · live while this view is open</p>
      <div class="actions"><button class="btn" data-nv="mon-off" data-mon="${esc(key)}">Stop monitoring</button></div>
      ${msg ? `<h3 style="margin-top:1em">NEWEST MESSAGE</h3>
        <div class="kv"><span class="k">Time</span><span class="v">${esc(new Date(msg.time).toLocaleTimeString())}</span>
          <span class="k">Started at</span><span class="v">${esc(name(msg.origin))}</span><span class="k">Answer to</span><span class="v">${esc(msg.reply_to ? name(msg.reply_to) : "where it started")}</span>
          <span class="k">Kind</span><span class="v">${esc(msg.kind)} · ${esc(msg.trigger)}</span><span class="k">Way</span><span class="v">${esc(msg.way.map(name).join(" › "))}</span></div>
        ${Object.keys(msg.vars || {}).length ? `<h3 style="margin-top:1em">VARIABLES</h3><pre style="white-space:pre-wrap;font-size:.85em">${esc(JSON.stringify(msg.vars, null, 1))}</pre>` : ""}
        <h3 style="margin-top:1em">ENTRIES <span class="hint">${msg.entries.length}</span></h3>${entries}` : ""}
      <h3 style="margin-top:1em">PASSED LATELY</h3>${recs || '<p class="hint">Nothing yet (kept 10 minutes).</p>'}`;
  }

  _shown(n) {
    const wired = (this._data.wires || []).some((w) => w.from.split(".")[0] === n.id || w.to.split(".")[0] === n.id);
    return wired || this._reveal?.has(n.id) || (n.on && !(this._data.hidden || []).includes(n.id));
  }

  async _retitle(id) {
    const title = this.querySelector(".nv-info [data-core-title]")?.value ?? "";
    try { await this.ws("aac/nodes/title", { node: id, title }); this._msg.textContent = "Renamed."; }
    catch (err) { this._msg.textContent = `Not saved: ${err.message || err}`; }
    await this.refresh(true);
    this._info();
  }

  async _hide(id, hide) {
    const hidden = new Set(this._data.hidden || []);
    if (hide) { hidden.add(id); this._reveal?.delete(id); if (this._sel === id) this._sel = null; } else hidden.delete(id);
    try { await this.ws("aac/nodes/hidden", { hidden: [...hidden] }); } catch (err) { this._msg.textContent = `Not saved: ${err.message || err}`; }
    await this.refresh(true);
    this._info();
  }

  async pick(id) {
    // chosen in the sidebar: selected, shown on the right, the view centred on it
    if (!this._data.nodes.some((n) => n.id === id)) {  // hidden or off: back into the grid
      (this._reveal ||= new Set()).add(id);
      if ((this._data.hidden || []).includes(id)) await this._hide(id, false); else await this.refresh(true);
    }
    this._sel = id; this._selFrame = null; this._selWire = null; this._multi = new Set([id]);
    const p = this._pos[id];
    if (p) { this._view.x = -p.x * this._view.k; this._view.y = -p.y * this._view.k; }
    this._draw(); this._layout(); this._info();
  }

  async _drop(what, e) {
    // a card from the sidebar dropped into the grid: an existing one moves there, a type is added there
    const at = this._world2(e);
    if (what.startsWith("node:")) {
      const id = what.slice(5);
      if (!this._data.nodes.some((n) => n.id === id)) await this.pick(id);  // hidden: back into the grid first
      if (!this._pos[id]) return;
      Object.assign(this._pos[id], at);
      this._snap(id); this._save(); this._draw(); this._layout(); this.pick(id);
    } else if (what.startsWith("type:")) {
      await this._addOf(what.slice(5), at);
    }
  }

  async _addOf(typeId, at = null) {
    this.querySelector(".nv-addmenu").style.display = "none";
    const type = (this._data.types || []).find((n) => n.id === typeId);
    if (!type) return;
    const title = await this._ask(`Name of the new ${type.title.toLowerCase()} node`, `${type.title} 2`);
    if (title === null || title === undefined || !String(title).trim()) return;
    const base = String(title).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 30) || "node";
    let id = base, k = 2;
    while ((this._all || this._data.nodes).some((n) => n.id === id) || id.length < 2) id = `${base}_${k++}`;
    const settings = Object.fromEntries(Object.entries(type.settings_schema || {}).map(([key, f]) => [key, f.default ?? ""]));
    this._sel = id;
    await this._setInstances({ ...this._instances(), [id]: { type: type.id, title: String(title).trim(), settings } });
    if (at && this._pos[id]) { Object.assign(this._pos[id], at); this._snap(id); this._save(); this._draw(); this._layout(); this._info(); }
    this._msg.textContent = `${String(title).trim()} added: wire it in (drag from an output to its input) to use it.`;
  }

  _saveInstance(id) {
    const box = this.querySelector(".nv-info"), all = this._instances(), inst = all[id];
    if (!inst) return;
    inst.title = box.querySelector("[data-inst-title]")?.value.trim() || inst.title;
    for (const el of box.querySelectorAll("[data-inst-key]")) {
      inst.settings[el.dataset.instKey] = el.dataset.kind === "outputs"
        ? el.value.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
          const [name, ...rest] = l.split(":");
          return { name: name.trim().replace(/[^\w-]/g, "_"), description: rest.join(":").trim() };
        }).filter((o) => o.name)
        : el.type === "checkbox" ? el.checked : el.value;
    }
    this._setInstances(all);
  }

  async _removeInstance(id) {
    const n = this._data.nodes.find((x) => x.id === id);
    const ok = this.panel?._confirm ? await this.panel._confirm(`Remove the node "${n?.title || id}" and its wires?`, "Remove", true)
      : window.confirm(`Remove ${id}?`);
    if (!ok) return;
    const all = this._instances();
    delete all[id];
    this._sel = null;
    this._setInstances(all);
  }

  _moduleForm(id) {
    // A module's settings right here (simple kinds; entity lists and the rest on its settings page)
    const m = this._data.modules?.[id];
    if (!m) return "";
    let more = false;
    const fields = Object.entries(m.settings || {}).map(([key, f]) => {
      const v = m.values?.[key], label = `<label>${esc(f.title || key)}</label>`;
      const help = f.help || f.hint ? `<div class="hint" style="margin:-.2em 0 .5em">${esc([f.help, f.hint].filter(Boolean).join(" "))}</div>` : "";
      if (f.type === "bool") return `<label class="check"><input type="checkbox" data-mod-key="${esc(key)}" ${(v ?? f.default) ? "checked" : ""}>${esc(f.title || key)}</label>${help}${warnHtml(key, f, v ?? f.default)}`;
      if (f.type === "select" && f.options) return `${label}<select data-mod-key="${esc(key)}">${f.options.map(([val, l]) =>
        `<option value="${esc(val)}" ${String(val) === String(v) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>${help}`;
      if (f.type === "longtext") return `${label}<textarea data-mod-key="${esc(key)}" class="thin-scroll" style="min-height:${(f.rows || 4) * 1.3}em" placeholder="${esc(f.placeholder || "")}">${esc(v ?? "")}</textarea>${help}`;
      if (["number", "time", "text", "string"].includes(f.type) || !f.type)
        return `${label}<input data-mod-key="${esc(key)}" ${f.type === "number" ? 'type="number" inputmode="decimal"' : f.type === "time" ? 'type="time"' : ""} value="${esc(v ?? "")}">${help}`;
      more = true;
      return "";
    }).join("");
    return `<h3 style="margin-top:1em">MODULE: ${esc((m.name || id).toUpperCase())}</h3>
      <label class="check"><input type="checkbox" data-mod-on ${m.enabled ? "checked" : ""}>On in this domain</label>
      ${fields}${more ? '<p class="hint">More settings on the module\'s page.</p>' : ""}
      <div class="actions"><button class="btn primary" data-nv="mod-save" data-module="${esc(id)}">Save</button>
        ${(m.pages || []).map((pg) => `<button class="btn" data-go="mod/${esc(id)}/${esc(pg.id)}">Open ${esc(pg.title)}</button>`).join("")}
        <button class="btn" data-go="settings/module/${esc(id)}">Permissions</button></div>`;
  }

  async _saveModule(id) {
    const box = this.querySelector(".nv-info"), m = this._data.modules?.[id];
    if (!m) return;
    const values = {};
    for (const el of box.querySelectorAll("[data-mod-key]")) {
      const f = m.settings[el.dataset.modKey] || {};
      values[el.dataset.modKey] = f.type === "bool" ? el.checked : f.type === "number" ? Number(el.value) : el.value;
    }
    const on = box.querySelector("[data-mod-on]")?.checked;
    try {
      if (Object.keys(values).length) await this.ws("aac/module/settings", { module: id, values });
      if (on !== undefined && on !== m.enabled) await this.ws("aac/module/access", { module: id, values: { enabled: on } });
      this._msg.textContent = "Saved.";
    } catch (err) { this._msg.textContent = `Not saved: ${err.message || err}`; }
    await this.refresh();
  }

  _coreForm(n) {
    // A core card's own settings (what SETTINGS → Master agent had): the model's temperature, …
    const field = ([key, f]) => {
      const v = n.edit_values?.[key], help = f.help || f.hint ? `<p class="hint" style="margin:.2em 0 0">${esc([f.help, f.hint].filter(Boolean).join(" "))}</p>` : "";
      if (f.type === "bool") return `<label class="check"><input type="checkbox" data-core-key="${esc(key)}" ${v ? "checked" : ""}>${esc(f.title || key)}</label>${help}`;
      if (f.type === "multiselect") return `<label>${esc(f.title || key)}</label>${(f.options || []).map(([val, label]) =>
        `<label class="check"><input type="checkbox" data-core-key="${esc(key)}" data-val="${esc(val)}" ${(v || []).includes(val) ? "checked" : ""}>${esc(label)}</label>`).join("")
        || '<p class="hint">Home Assistant offers none.</p>'}${help}`;
      if (f.type === "longtext") return `<label>${esc(f.title || key)}</label><textarea data-core-key="${esc(key)}" class="thin-scroll" style="min-height:${(f.rows || 5) * 1.3}em">${esc(v ?? "")}</textarea>${help}`;
      return `<label>${esc(f.title || key)}</label><input data-core-key="${esc(key)}" ${f.type === "number" ? 'type="number" step="any"' : ""} value="${esc(v ?? "")}">${help}`;
    };
    return `<h3 style="margin-top:1em">SETTINGS</h3>${Object.entries(n.edit_schema).map(field).join("")}
      <div class="actions"><button class="btn primary" data-nv="core-save" data-node="${esc(n.id)}">Save</button></div>`;
  }

  _poolInfo(n) {
    // the models a pool hands out (the thinking pool's list, in the owner's order; the Pools page changes it)
    const items = this.panel?._state?.pools?.roles?.master?.items || [];
    const label = (id) => this.panel?._resourceLabel ? this.panel._resourceLabel(id) : id;
    return `<h3 style="margin-top:1em">MODELS</h3>${items.length ? items.map((it) => `<div class="src"><span class="dot ${it.state === "ok" ? "ok" : it.state === "down" ? "bad" : ""}"></span><div><div class="n">${esc(label(it.id))}${it.active ? ' <span class="tag">IN USE</span>' : ""}</div><div class="m">${esc(it.state)}</div></div></div>`).join("")
      : '<p class="hint">No model yet.</p>'}
      <div class="actions"><button class="btn" data-go="models">Pools page</button></div>`;
  }

  _outputsForm(n) {
    // Round text outputs: every wire gets a copy, or (owner's setting) only one wire
    const outs = Object.entries(n.outputs).filter(([p, t]) => t === "text" && !(n.square || []).includes(p));
    if (!outs.length) return "";
    const single = this._data.single || [];
    return `<h3 style="margin-top:1em">OUTPUTS</h3>${outs.map(([p]) => `<label class="check"><input type="checkbox" data-single="${esc(`${n.id}.${p}`)}" ${single.includes(`${n.id}.${p}`) ? "checked" : ""}>${outs.length > 1 ? `${esc(p)}: ` : ""}only one wire (a new wire replaces it; otherwise every wire gets a copy)</label>`).join("")}`;
  }

  _namesForm(n) {
    // What the models call this card when it is wired to them (owner: "naming inputs so models can use them as
    // custom tools"): empty = its own name
    const ins = Object.entries(n.inputs).filter(([, t]) => t === "text");
    if (!ins.length || n.role === "endpoint") return "";
    const names = this._data.names || {};
    return `<h3 style="margin-top:1em">FOR THE MODELS ${ins.length > 1 ? "" : `<span class="hint">· when a model's output is wired to it</span>`}</h3>
      ${ins.map(([p]) => { const v = names[`${n.id}.${p}`] || {};
        return `${ins.length > 1 ? `<div class="hint">input ${esc(p)}</div>` : ""}
          <label>Tool name</label><input data-name-port="${esc(p)}" data-name-key="name" value="${esc(v.name || "")}" placeholder="${esc(n.id)}">
          <label>What it is for</label><input data-name-port="${esc(p)}" data-name-key="description" value="${esc(v.description || "")}" placeholder="e.g. Call this when the user talks to you">`; }).join("")}
      <div class="actions"><button class="btn primary" data-nv="names-save" data-node="${esc(n.id)}">Save</button></div>`;
  }

  async _saveNames(id) {
    const box = this.querySelector(".nv-info"), names = { ...(this._data.names || {}) };
    for (const el of box.querySelectorAll("[data-name-port]")) {
      const key = `${id}.${el.dataset.namePort}`;
      names[key] = { ...(names[key] || {}), [el.dataset.nameKey]: el.value.trim() };
    }
    try { await this.ws("aac/nodes/names", { names }); this._msg.textContent = "Saved."; }
    catch (err) { this._msg.textContent = `Not saved: ${err.message || err}`; }
    await this.refresh();
  }

  async _saveCore(id) {
    const box = this.querySelector(".nv-info"), n = this._data.nodes.find((x) => x.id === id);
    if (!n?.edit_schema) return;
    const values = {};
    for (const [key, f] of Object.entries(n.edit_schema)) {
      const els = [...box.querySelectorAll(`[data-core-key="${key}"]`)];
      if (!els.length) continue;
      values[key] = f.type === "multiselect" ? els.filter((el) => el.checked).map((el) => el.dataset.val)
        : f.type === "bool" ? els[0].checked : f.type === "number" ? Number(els[0].value) : els[0].value;
    }
    try { await this.ws("aac/nodes/settings", { node: id, values }); this._msg.textContent = "Saved."; }
    catch (err) { this._msg.textContent = `Not saved: ${err.message || err}`; }
    await this.refresh();
  }

  _settingsForm(n) {
    const field = ([key, f]) => {
      const v = n.settings?.[key] ?? f.default ?? "";
      if (f.type === "select") {
        const opts = (f.options_from ? this._data.options?.[f.options_from] : f.options) || [];
        return `<label>${esc(f.title || key)}</label><select data-inst-key="${esc(key)}">${opts.map(([val, label]) =>
          `<option value="${esc(val)}" ${val === v ? "selected" : ""}>${esc(label)}</option>`).join("")}</select>`;
      }
      if (f.type === "longtext") return `<label>${esc(f.title || key)}</label><textarea data-inst-key="${esc(key)}" class="thin-scroll" style="min-height:5em">${esc(v)}</textarea>`;
      if (f.type === "outputs") // one output per line: "name: what it is for"
        return `<label>${esc(f.title || key)} <span class="hint">one per line, "name: what it is for"</span></label><textarea data-inst-key="${esc(key)}" data-kind="outputs" class="thin-scroll" style="min-height:5em">${esc((v || []).map((o) => `${o.name}: ${o.description || ""}`).join("\n"))}</textarea>`;
      return `<label>${esc(f.title || key)}</label><input data-inst-key="${esc(key)}" value="${esc(v)}">`;
    };
    const withHelp = ([key, f]) => {
      const v = n.settings?.[key] ?? f.default ?? "";
      const help = f.help || f.hint ? `<div class="hint" style="margin:.2em 0 .5em">${esc([f.help, f.hint].filter(Boolean).join(" "))}</div>` : "";
      if (f.type === "bool") return `<label class="check"><input type="checkbox" data-inst-key="${esc(key)}" ${v ? "checked" : ""}>${esc(f.title || key)}</label>${help}${warnHtml(key, f, v)}`;
      return field([key, f]) + help;
    };
    return `<label>Name</label><input data-inst-title value="${esc(n.title)}">${Object.entries(n.settings_schema || {}).map(withHelp).join("")}
      <div class="actions"><button class="btn primary" data-nv="inst-save" data-node="${esc(n.id)}">Save</button>
        <button class="btn danger" data-nv="inst-remove" data-node="${esc(n.id)}">Remove node</button></div>`;
  }

  // ------------------------------------------------------------------ frames
  _saveFrames() {
    this.ws?.("aac/nodes/containers", { containers: this._frames }).catch((e) => { this._msg.textContent = `Frames not saved: ${e.message || e}`; });
  }

  async _ask(text, value) {
    return this.panel?._prompt ? this.panel._prompt(text, value) : window.prompt(text, value);
  }

  async _group() {
    const ids = [...this._multi].filter((id) => this._pos[id]);
    if (!ids.length) { this._msg.textContent = "Select nodes first: Shift- or Ctrl-click them."; return; }
    const name = await this._ask("Name of the container", "Group");
    if (name === null || name === undefined) return;
    const boxes = ids.map((id) => { const n = this._data.nodes.find((x) => x.id === id), p = this._pos[id]; return { p, hh: this._height(n) / 2 }; });
    const x0 = Math.min(...boxes.map((b) => b.p.x - W / 2)) - PAD, x1 = Math.max(...boxes.map((b) => b.p.x + W / 2)) + PAD;
    const y0 = Math.min(...boxes.map((b) => b.p.y - b.hh)) - PAD - BAR, y1 = Math.max(...boxes.map((b) => b.p.y + b.hh)) + PAD;
    for (const f of this._frames) f.nodes = f.nodes.filter((id) => !ids.includes(id)); // one container per node
    const f = { id: `f_${Date.now().toString(36)}`, name: String(name).trim().slice(0, 60) || "Group", nodes: ids,
                x: Math.round(x0), y: Math.round(y0), w: Math.round(x1 - x0), h: Math.round(y1 - y0) };
    this._frames.push(f);
    this._selFrame = f.id; this._multi = new Set(); this._sel = null;
    this._saveFrames(); this._draw(); this._info(); this._kick();
  }

  _fold(id) {
    const f = this._frames.find((x) => x.id === id);
    if (!f) return;
    f.collapsed = !f.collapsed;
    this._selFrame = id;
    this._saveFrames(); this._draw(); this._info(); this._kick();
  }

  async _rename(id) {
    const f = this._frames.find((x) => x.id === id);
    if (!f) return;
    const name = await this._ask("Name of the container", f.name);
    if (name === null || name === undefined) return;
    f.name = String(name).trim().slice(0, 60) || f.name;
    this._saveFrames(); this._draw(); this._info();
  }

  _joinFrame(id, x, y) {
    // Dropped inside a frame: it belongs there (the smallest, if frames overlap); dropped outside: it leaves
    const inside = this._frames.filter((f) => !f.collapsed && x > f.x && x < f.x + f.w && y > f.y + BAR && y < f.y + f.h)
      .sort((a, b) => a.w * a.h - b.w * b.h)[0];
    const before = this._frames.find((f) => f.nodes.includes(id));
    if (before === inside) return;
    if (before) before.nodes = before.nodes.filter((n) => n !== id);
    if (inside) inside.nodes.push(id);
    this._saveFrames();
  }

  // Every variable a card's text can use ({{ … }}), and where it comes from (owner, 2026-10-23: "an overview of the
  // variables available in total and where they came from")
  _varsHtml() {
    const v = this._data.variables || {}, row = (name, about, from = "") =>
      `<div class="nv-var" data-copy="{{ ${esc(name)} }}" title="Click: copy" style="cursor:pointer;padding:.35em 0;border-bottom:1px solid var(--line)">
        <code style="color:var(--cyan)">{{ ${esc(name)} }}</code>${from ? ` <span class="tag">${esc(from)}</span>` : ""}<div class="hint" style="margin:.1em 0 0">${esc(about)}</div></div>`;
    const cards = (this._all || this._data.nodes).filter((n) => Object.keys(n.variables || {}).length);
    return `<h2>VARIABLES</h2>
      <p class="hint">For the texts of Header, Trailer and Context (HA's template syntax). Click one to copy it.</p>
      <h3 style="margin-top:1em">BUILT IN</h3>${Object.entries(v.builtin || {}).map(([k, a]) => row(k, a, "AAC")).join("")}
      <h3 style="margin-top:1em">FROM CARDS</h3>
      <p class="hint">A card's values exist once the message has passed it: use them in cards after it.</p>
      ${cards.length ? cards.map((n) => Object.entries(n.variables).map(([k, a]) => row(`${n.id}.${k}`, a, n.title)).join("")).join("")
        : '<p class="hint">No card hands on values yet. Heard lines and Buffer do: add one and it shows here.</p>'}
      <h3 style="margin-top:1em">FROM HOME ASSISTANT</h3>${Object.entries(v.ha || {}).map(([k, a]) => row(k.replace(/^\{\{\s*|\s*\}\}$/g, ""), a, "HA")).join("")}
      <p class="hint" style="margin-top:.6em">Everything else HA's templates offer works too (filters, area_name(), …).</p>`;
  }

  _info() {
    this._editing = null;
    const box = this.querySelector(".nv-info"), n = this._data?.nodes.find((x) => x.id === this._sel);
    if (this._sel || this._selWire || this._selFrame) { this._showVars = false; this._monSel = null; }
    if (this._monSel && this._monitors?.has(this._monSel)) {
      box.style.display = "";
      box.innerHTML = this._monitorHtml(this._monSel);
      return;
    }
    if (this._showVars) {
      box.style.display = "";
      box.innerHTML = this._varsHtml();
      box.querySelectorAll("[data-copy]").forEach((el) => el.addEventListener("click", () => {
        try { navigator.clipboard.writeText(el.dataset.copy); this._msg.textContent = `Copied ${el.dataset.copy}`; } catch (e) { /* no clipboard */ }
      }));
      return;
    }
    const fr = this._frames.find((f) => f.id === this._selFrame);
    const wire = this._selWire && this._wires().find((w) => `${w.a.id}.${w.ap}|${w.b.id}.${w.bp}` === this._selWire);
    if (wire) {
      box.style.display = "";
      box.innerHTML = `<h2>WIRE <span class="tag" style="border-color:${this._color(wire.type)}">${esc(wire.type)}</span></h2>
        <div class="kv"><span class="k">From</span><span class="v">${esc(wire.a.title)} · ${esc(wire.ap)}</span>
          <span class="k">To</span><span class="v">${esc(wire.b.title)} · ${esc(wire.bp)}</span></div>
        <div class="actions"><button class="btn" data-nv="monitor" data-mon="${esc(this._monKey(wire))}">${this._monitors?.has(this._monKey(wire)) ? "Stop monitoring" : "Monitor"}</button>
          <button class="btn danger" data-nv="unwire">Remove wire</button></div>
        <p class="hint">Monitor: a window above the wire shows what passes it, live; click the window for everything inside.</p>`;
      return;
    }
    if (fr) {
      const titles = fr.nodes.map((id) => this._data.nodes.find((x) => x.id === id)?.title || id);
      box.style.display = "";
      box.innerHTML = `<h2>${esc((fr.name || "Unnamed").toUpperCase())} <span class="tag">CONTAINER</span></h2>
        <div class="kv"><span class="k">Nodes</span><span class="v">${esc(titles.join(", ") || "none yet: drop nodes into it")}</span></div>
        <p class="hint">Drag the name bar to move it with its nodes, the corner to resize. Drop a node inside to add it, drag it out to take it away.</p>
        <div class="actions"><button class="btn" data-nv="fold" data-frame="${esc(fr.id)}">${fr.collapsed ? "Unfold" : "Fold into one card"}</button>
          <button class="btn" data-nv="rename" data-frame="${esc(fr.id)}">Rename</button>
          <button class="btn danger" data-nv="ungroup" data-frame="${esc(fr.id)}">Ungroup</button></div>`;
      return;
    }
    if (!n) {
      box.style.display = "";
      box.innerHTML = '<h2>CARDS</h2><p class="hint">Click a card to see what it does and set it up. Drag from an output dot to an input dot to wire; click a wire, then Delete, to remove it.</p>';
      return;
    }
    const ports = (o) => Object.entries(o).map(([p, t]) => `<span class="tag" style="border-color:${this._color(t)}">${esc(p)}: ${esc(t)}</span>`).join(" ") || "—";
    const into = this._wires().filter((w) => w.b.id === n.id).map((w) => `${esc(w.a.title)}.${esc(w.ap)}`).join(", ") || "—";
    const out = this._wires().filter((w) => w.a.id === n.id).map((w) => `${esc(w.b.title)}.${esc(w.bp)}`).join(", ") || "—";
    box.style.display = "";
    box.innerHTML = `<h2>${esc(n.title.toUpperCase())} <span class="tag">${esc(ROLE[n.role] || KIND[n.kind] || n.kind)}</span>${n.on ? "" : ' <span class="tag plan">MODULE OFF</span>'}${this._pos[n.id]?.pinned ? ' <span class="tag">PINNED</span>' : ""}</h2>
      ${n.description ? `<p style="margin:.2em 0 .8em">${esc(n.description)}</p>` : ""}
      <div class="kv">
        <span class="k">Module</span><span class="v">${esc(n.module_name)}</span>
        <span class="k">Inputs</span><span class="v">${ports(n.inputs)}</span>
        <span class="k">Outputs</span><span class="v">${ports(n.outputs)}</span>
        ${n.kind === "annotating" ? `<span class="k">Adds</span><span class="v">${esc(n.adds.join(", "))} (place ${esc(n.order)} in the chain)</span>` : ""}
        <span class="k">Gets from</span><span class="v">${into}</span>
        <span class="k">Gives to</span><span class="v">${out}</span>
        <span class="k">Last 10 min</span><span class="v">${Object.entries(n.recent || {}).map(([p, c]) => `${esc(p)}: ${c}`).join(", ") || "—"}</span>
      </div>
      ${n.type ? `<h3 style="margin-top:1em">THIS NODE</h3>${this._settingsForm(n)}`
        : n.addable ? '<p class="hint">The standard one. "Add node" makes another with its own settings.</p>' : ""}
      ${n.role === "pool" ? this._poolInfo(n) : ""}
      ${PAGES[n.id] ? `<div class="actions"><button class="btn" data-go="${esc(PAGES[n.id][0])}">${esc(PAGES[n.id][1])}</button></div>` : ""}
      ${!n.type ? `<label>Name</label><div style="display:flex;gap:.5em"><input data-core-title value="${esc(n.title)}" style="flex:1">
        <button class="btn" data-nv="title-save" data-node="${esc(n.id)}" title="Your own name for this card in this domain (empty: its own name)">Rename</button></div>` : ""}
      ${!n.type && !(this._data.wires || []).some((w) => w.from.split(".")[0] === n.id || w.to.split(".")[0] === n.id)
        ? `<div class="actions"><button class="btn" data-nv="hide" data-node="${esc(n.id)}" title="Built-in cards cannot be removed; hidden ones stay in the sidebar list (click one to bring it back)">Hide from the grid</button></div>` : ""}
      ${n.edit_schema ? this._coreForm(n) : ""}
      ${this._outputsForm(n)}
      ${this._namesForm(n)}
      ${n.module && !n.type ? this._moduleForm(n.module) : ""}`;
  }
}

if (!customElements.get("aac-nodes")) customElements.define("aac-nodes", AacNodes);

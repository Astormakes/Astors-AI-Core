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
const FIELD = 150; // reach of a node's soft push beyond its box
const COLORS = ["var(--cyan)", "var(--magenta)", "var(--green)", "var(--amber)", "#8ab4ff", "#ff8a65", "#c792ea", "#7fdbca"];
const KIND = { source: "source", sink: "sink", transform: "transform", annotating: "adds to the record" };
const UNDO_STEPS = 10;
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
    const first = this._loaded !== this._key;
    if (!this._frameDrag) this._frames = (this._data.containers || []).map((f) => ({ ...f, nodes: [...f.nodes] }));
    this._place();
    this._mode();
    this._draw();
    if (first) { if (this._physics) this._fitWhenQuiet = true; else this._fit(); }
    if (!quiet) this._kick();
    this._info();
  }

  // ------------------------------------------------------------------ layout and storage
  _build() {
    this.style.display = "block";
    this.innerHTML = `
      <div class="card" style="padding:0;overflow:hidden;position:relative">
        <div class="nv-bar" style="display:flex;gap:.6em;align-items:center;flex-wrap:wrap;padding:.6em .9em;border-bottom:1px solid var(--line)">
          <label class="check" style="margin:0"><input type="checkbox" class="nv-physics">Physics</label>
          <span class="hint nv-help"></span>
          <span style="flex:1"></span>
          <span class="hint nv-wiring"></span>
          <button class="btn" data-nv="auto" title="Forget the wires set by hand: everything wired by type again">Automatic wiring</button>
          <span style="position:relative"><button class="btn" data-nv="add" title="Add another node of a type that can exist more than once (e.g. a second model)">Add node</button>
            <span class="nv-addmenu" style="display:none;position:absolute;top:100%;right:0;z-index:5;min-width:12em;background:var(--bg2, #111);border:1px solid var(--line);border-radius:.4em;padding:.3em 0"></span></span>
          <button class="btn" data-nv="group" title="Put the nodes selected with Shift/Ctrl-click into a named container">Group</button>
          <button class="btn" data-nv="undo" title="Back to the layout saved when Physics went on">Undo</button>
          <button class="btn" data-nv="fit">Fit</button>
          <button class="btn" data-nv="free" title="Release every pinned node">Unpin all</button>
        </div>
        <div class="nv-stage" style="position:relative;height:clamp(420px, calc(100vh - 260px), 1400px);touch-action:none"></div>
        <div class="nv-legend" style="display:flex;gap:.9em;flex-wrap:wrap;padding:.5em .9em;border-top:1px solid var(--line);font-size:.8em"></div>
        <p class="hint nv-msg" style="margin:.5em .9em"></p>
      </div>
      <div class="card nv-info" style="display:none"></div>
      <div class="card nv-try"><h2>TRY IT</h2>
        <p class="hint">Send a message through the graph and watch its way: the cards it passes light up, and you see what each one added.</p>
        <div class="row" style="align-items:flex-end;gap:.6em">
          <div style="flex:none;min-width:10em"><label>From</label><select class="nv-try-source">
            <option value="chat">Panel chat</option><option value="voice">Voice</option><option value="assist">HA Assist</option><option value="auto">Automatic</option></select></div>
          <div style="flex:1"><label>Message</label><input class="nv-try-text" placeholder="e.g. Is the bed light on?"></div>
          <div style="flex:none" class="actions"><button class="btn primary" data-nv="try">Send</button></div>
        </div>
        <div class="nv-try-out"></div>
      </div>`;
    this._stage = this.querySelector(".nv-stage");
    this._msg = this.querySelector(".nv-msg");
    this._svg = document.createElementNS(SVG, "svg");
    this._svg.setAttribute("width", "100%");
    this._svg.setAttribute("height", "100%");
    this._svg.style.display = "block";
    this._svg.innerHTML = `<style>
        .nv-wire { fill: none; stroke-width: 1.6; opacity: .55; pointer-events: none; }
        .nv-wire.sel { opacity: 1; stroke-width: 3.4; }
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
    return { x: p.x + (side === "out" ? W / 2 : -W / 2), y: p.y - this._height(n) / 2 + HEAD + ROW * names.indexOf(port) + ROW / 2 };
  }

  _read() {
    try { return JSON.parse(localStorage.getItem(this._key) || "{}"); } catch (e) { return {}; }
  }

  _place() {
    if (this._loaded !== this._key) { // another domain (or the first time): its saved layout
      const saved = this._read();
      this._pos = {};
      for (const [id, p] of Object.entries(saved.pos || {})) this._pos[id] = { x: p.x, y: p.y, vx: 0, vy: 0, pinned: !!p.pinned };
      this._undo = Array.isArray(saved.undo) ? saved.undo : [];
      this._physics = saved.physics !== false;
      this._loaded = this._key;
      this._mode();
    }
    const nodes = this._data.nodes;
    // New nodes start at their place in the flow: column = steps after a source (answer wires left out),
    // so the field only has to refine, not untangle (a folded chain jams in a local minimum)
    const wires = this._wires(), back = this._backEdges(wires), rank = {}, depth = {};
    const forward = wires.filter((w) => !back.has(w));
    for (let i = 0; i < nodes.length; i++) {
      let changed = false;
      for (const w of forward) {
        const stacked = w.a.kind === "annotating" && w.b.kind === "annotating"; // a column: same place, one lower
        const r = (rank[w.a.id] ?? 0) + (stacked ? 0 : 1), d = stacked ? (depth[w.a.id] ?? 0) + 1 : 0;
        if ((rank[w.b.id] ?? 0) < r || (stacked && (rank[w.b.id] ?? 0) === r && (depth[w.b.id] ?? 0) < d)) {
          rank[w.b.id] = r; depth[w.b.id] = d; changed = true;
        }
      }
      if (!changed) break;
    }
    const wired = new Set(wires.flatMap((w) => [w.a.id, w.b.id]));
    const columns = {};
    for (const n of nodes) if (wired.has(n.id)) (columns[rank[n.id] ?? 0] ||= []).push(n.id);
    const ranks = Object.keys(columns).map(Number), mid = (Math.max(0, ...ranks)) / 2;
    let parked = 0;
    for (const n of nodes) {
      if (this._pos[n.id]) continue;
      if (wired.has(n.id)) {
        const col = columns[rank[n.id] ?? 0].filter((id) => !depth[id]), row = col.indexOf(n.id) - (col.length - 1) / 2;
        const y = depth[n.id] ? null : row * 110;
        this._pos[n.id] = { x: ((rank[n.id] ?? 0) - mid) * (W + LINK), y: y ?? 0, vx: 0, vy: 0, pinned: false, depth: depth[n.id] || 0 };
      } else { // nothing wired: the strip at the bottom
        this._pos[n.id] = { x: (parked++ - 3) * (W + 30), y: 400, vx: 0, vy: 0, pinned: false };
      }
    }
    // the cards of a column under the head of their chain
    for (const w of forward) {
      if (w.a.kind !== "annotating" || w.b.kind !== "annotating") continue;
      const head = this._pos[w.a.id], p = this._pos[w.b.id];
      if (p && head && p.depth) { p.x = head.x; p.y = head.y + this._height(w.a) / 2 + STACK + this._height(w.b) / 2; }
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
      localStorage.setItem(this._key, JSON.stringify({ pos: this._snapshot().pos, physics: this._physics, undo: this._undo.slice(-UNDO_STEPS) }));
    } catch (e) { /* private window: the layout is not kept */ }
  }

  _wires() {
    if (!this._data) return [];
    const byId = Object.fromEntries(this._data.nodes.map((n) => [n.id, n]));
    return this._data.wires.map((w) => {
      const [a, ap] = w.from.split("."), [b, bp] = w.to.split(".");
      return byId[a] && byId[b] ? { a: byId[a], ap, b: byId[b], bp, type: byId[a].outputs[ap] } : null;
    }).filter(Boolean);
  }

  // ------------------------------------------------------------------ physics
  _mode() {
    this.querySelector(".nv-physics").checked = this._physics;
    this.querySelector(".nv-help").textContent = this._physics
      ? "Drag a node: the others give way · double-click pins it · Shift-click several, then Group · wheel zooms · drag the background to move"
      : "Physics off: nodes stay where you put them · wheel zooms · drag the background to move";
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
      if (back.has(w) || (w.a.kind === "annotating" && w.b.kind === "annotating") || this._folded(w.a.id) || this._folded(w.b.id)) continue;
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
      // Answers flow back to where the request came from: those wires always arc back
      const back = new Set(wires.filter((w) => w.type.endsWith(".answer")).map(name));
      const out = {}, state = {};
      for (const w of wires) if (!back.has(name(w))) (out[w.a.id] ||= []).push(w);
      const visit = (id) => {
        state[id] = 1;
        for (const w of out[id] || []) {
          if (state[w.b.id] === 1) back.add(name(w));
          else if (!state[w.b.id]) visit(w.b.id);
        }
        state[id] = 2;
      };
      const hasIn = new Set(wires.map((w) => w.b.id));
      for (const n of this._data.nodes) if (!hasIn.has(n.id) && !state[n.id]) visit(n.id); // sources first
      for (const n of this._data.nodes) if (!state[n.id]) visit(n.id);
      this._backKey = key; this._back = back;
    }
    const back = this._back;
    return { has: (w) => back.has(name(w)) };
  }

  // ------------------------------------------------------------------ drawing
  _draw() {
    const nodesG = this._svg.querySelector(".nv-nodes"), wiresG = this._svg.querySelector(".nv-wires");
    this._svg.querySelector(".nv-frames").innerHTML = this._frames.map((f) => `<g class="nv-frame ${f.collapsed ? "folded" : ""} ${this._selFrame === f.id ? "sel" : ""}" data-frame="${esc(f.id)}">
        <rect class="fbox" rx="10"></rect><rect class="fbar" rx="10" height="${BAR}"></rect>
        <text class="fname">${esc(f.name || "Unnamed")}</text><rect class="fgrip" width="${GRIP}" height="${GRIP}" rx="3"></rect>
        <text class="ffold" data-fold="${esc(f.id)}"><title>${f.collapsed ? "Unfold" : "Fold into one card"}</title>${f.collapsed ? "▸" : "▾"}</text>
        <text class="fcount">${f.collapsed ? `${f.nodes.length} card${f.nodes.length === 1 ? "" : "s"} inside` : ""}</text></g>`).join("");
    nodesG.innerHTML = this._data.nodes.map((n) => {
      const h = this._height(n), ins = Object.entries(n.inputs), outs = Object.entries(n.outputs);
      const row = (i) => -h / 2 + HEAD + ROW * i + ROW / 2;
      const flow = Object.values(n.recent || {}).reduce((s, v) => s + v, 0);
      return `<g class="nv-node ${this._folded(n.id) ? "hidden" : ""} ${n.on ? "" : "off"} ${this._lit?.has(n.id) ? "lit" : ""} ${this._sel === n.id ? "sel" : ""} ${this._multi.has(n.id) && this._multi.size > 1 ? "multi" : ""} ${this._pos[n.id]?.pinned ? "pinned" : ""}" data-node="${esc(n.id)}">
        <rect class="box" x="${-W / 2}" y="${-h / 2}" width="${W}" height="${h}" rx="7"></rect>
        <text x="${-W / 2 + 10}" y="${-h / 2 + 17}">${esc(n.title)}<tspan class="pin" dx="4">📌</tspan></text>
        <text class="sub" x="${-W / 2 + 10}" y="${-h / 2 + 31}">${esc(short(`${n.module_name}${n.kind === "annotating" ? ` · adds ${n.adds.join(", ")}` : ""}${flow ? ` · ${flow} recent` : ""}`, 30))}</text>
        ${ins.map(([p, t], i) => `<circle class="nv-dot" data-dot="in" data-port="${esc(p)}" data-type="${esc(t)}" cx="${-W / 2}" cy="${row(i)}" r="5.5" fill="${this._color(t)}"><title>${esc(p)}: ${esc(t)}</title></circle>
          <text class="port" x="${-W / 2 + 9}" y="${row(i) + 3.5}">${esc(p)}</text>`).join("")}
        ${outs.map(([p, t], i) => `<circle class="nv-dot" data-dot="out" data-port="${esc(p)}" data-type="${esc(t)}" cx="${W / 2}" cy="${row(i)}" r="5.5" fill="${this._color(t)}"><title>${esc(p)}: ${esc(t)} (drag to an input to wire)</title></circle>
          <text class="port" x="${W / 2 - 9}" y="${row(i) + 3.5}" text-anchor="end">${esc(p)}</text>`).join("")}
      </g>`;
    }).join("");
    wiresG.innerHTML = this._wires().map((w, i) => {
      const flow = (w.a.recent || {})[w.ap] > 0;
      const key = `${w.a.id}.${w.ap}|${w.b.id}.${w.bp}`;
      const lit = this._litWires?.has(`${w.a.id}>${w.b.id}`) || (this._folded(w.a.id) && this._lit?.has(w.a.id) && this._lit?.has(w.b.id));
      const inner = this._folded(w.a.id) && this._folded(w.a.id) === this._folded(w.b.id); // inside a folded frame: not drawn
      if (inner) return "";
      return `<path class="nv-wire ${flow ? "flow" : ""} ${lit ? "lit" : ""} ${this._selWire === key ? "sel" : ""}" data-wire="${i}" stroke="${this._color(w.type)}"></path>
        <path class="nv-hit" data-wire="${i}" data-key="${esc(key)}"><title>${esc(w.a.title)}.${esc(w.ap)} → ${esc(w.b.title)}.${esc(w.bp)} (${esc(w.type)}) · click to select</title></path>`;
    }).join("");
    this.querySelector(".nv-legend").innerHTML = Object.keys(this._colors).sort()
      .map((t) => `<span><span style="display:inline-block;width:.75em;height:.75em;border-radius:50%;background:${this._color(t)};vertical-align:-.05em"></span> ${esc(t)}</span>`).join("");
    this._msg.textContent = this._data.nodes.some((n) => !n.on) ? "Faded nodes belong to modules that are off in this domain." : "";
    this._layout();
  }

  _layout() {
    if (!this._stage) return;
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
    const wires = this._wires();
    for (const path of this._svg.querySelectorAll(".nv-wire, .nv-hit")) {
      const w = wires[path.dataset.wire];
      if (!w) continue;
      const o = this._port(w.a, w.ap, "out"), i = this._port(w.b, w.bp, "in");
      const bend = Math.max(40, Math.abs(i.x - o.x) / 2);
      path.setAttribute("d", `M${o.x},${o.y} C${o.x + bend},${o.y} ${i.x - bend},${i.y} ${i.x},${i.y}`);
    }
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
    this.addEventListener("click", (e) => {
      const b = e.target.closest("[data-nv]");
      if (b?.dataset.nv === "fit") this._fit();
      if (b?.dataset.nv === "undo") this._undoLayout();
      if (b?.dataset.nv === "free") { for (const p of Object.values(this._pos)) p.pinned = false; this._save(); this._draw(); this._kick(); }
      if (b?.dataset.nv === "group") this._group();
      if (b?.dataset.nv === "auto") this._automatic();
      if (b?.dataset.nv === "add") this._addNode();
      if (b?.dataset.nv === "add-type") this._addOf(b.dataset.type);
      if (b?.dataset.nv === "try") this._try();
      const step = e.target.closest("[data-try-node]");
      if (step) { this._sel = step.dataset.tryNode; this._selFrame = null; this._selWire = null; this._draw(); this._info(); }
      if (b?.dataset.nv === "inst-save") this._saveInstance(b.dataset.node);
      if (b?.dataset.nv === "inst-remove") this._removeInstance(b.dataset.node);
      if (b?.dataset.nv === "mod-save") this._saveModule(b.dataset.module);
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
    this._svg.addEventListener("pointerdown", (e) => {
      const g = e.target.closest(".nv-node"), fg = e.target.closest(".nv-frame");
      this._svg.setPointerCapture(e.pointerId);
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
        // Let go: with Physics on it keeps its swing and floats into its place
        if (this._drag.moved && this._physics && p.pinned) { p.x = this._drag.tx; p.y = this._drag.ty; }
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
    this.querySelector(".nv-try-text").addEventListener("keydown", (e) => { if (e.key === "Enter") this._try(); });
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
      const k = Math.min(3, Math.max(.25, v.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
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
    const wires = this._current();
    if (wires.some((w) => w.from === from && w.to === to)) return;
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

  // ------------------------------------------------------------------ try it
  async _try() {
    const text = this.querySelector(".nv-try-text").value.trim(), source = this.querySelector(".nv-try-source").value;
    const out = this.querySelector(".nv-try-out");
    if (!text) return;
    out.innerHTML = '<p class="hint">On its way…</p>';
    let r;
    try { r = await this.ws("aac/nodes/try", { text, source }); }
    catch (err) { out.innerHTML = `<div class="msg err">${esc(err.message || err)}</div>`; return; }
    // The way: source → the chain's nodes → the model node; it lights up in the graph
    const path = [r.source, ...r.trace.map((t) => t.node), ...(r.model_node ? [r.model_node] : [])];
    this._lit = new Set(path);
    this._litWires = new Set(path.slice(1).map((id, i) => `${path[i]}>${id}`));
    this._draw();
    clearTimeout(this._litTimer);
    this._litTimer = setTimeout(() => { this._lit = null; this._litWires = null; this._draw(); }, 30000);
    const title = (id) => this._data.nodes.find((n) => n.id === id)?.title || id;
    const part = (label, items) => items?.length ? `<div class="m"><b>${label}:</b> ${items.map((x) => esc(x.length > 160 ? x.slice(0, 160) + "…" : x)).join(" · ")}</div>` : "";
    out.innerHTML = `
      <div class="msg ${r.status === "failed" ? "err" : "ok"}" style="white-space:pre-wrap">${esc(r.answer || "(no answer)")}</div>
      <h3 style="margin-top:.8em">THE WAY</h3>
      <div class="src" data-try-node="${esc(r.source)}" style="cursor:pointer"><span class="dot ok"></span><div><div class="n">${esc(title(r.source))}</div><div class="m">the message comes in here</div></div></div>
      ${r.trace.map((t) => `<div class="src" data-try-node="${esc(t.node)}" style="cursor:pointer"><span class="dot ${t.error ? "bad" : "ok"}"></span>
        <div style="flex:1;min-width:0"><div class="n">${esc(t.title)}</div>
          ${t.error ? `<div class="m">failed: ${esc(t.error)}</div>` : ""}${part("system", t.system)}${part("context", t.context)}${part("tools", t.tools)}
          ${!t.error && !t.system && !t.context && !t.tools ? '<div class="m">added nothing this time</div>' : ""}</div></div>`).join("")}
      ${r.model_node ? `<div class="src" data-try-node="${esc(r.model_node)}" style="cursor:pointer"><span class="dot ok"></span><div><div class="n">${esc(title(r.model_node))}</div>
        <div class="m">answered${r.model ? ` with ${esc(r.model)}` : ""}${r.steps?.filter((s) => s.type === "tool_call").length ? ` · tools used: ${esc(r.steps.filter((s) => s.type === "tool_call").map((s) => s.tool.split("__").pop()).join(", "))}` : ""}</div></div></div>` : ""}
      <div class="actions"><button class="btn" data-go="run/${esc(r.run_id)}">Run details</button></div>`;
  }

  // ------------------------------------------------------------------ added nodes (types vs. instances)
  _instances() {
    return Object.fromEntries(this._data.nodes.filter((n) => n.type)
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
    // grouped by what they add: text for the system prompt, context, tools; the model
    const group = (t) => t.id === "broker" ? "Model" : t.adds?.includes("tools") && !t.adds.includes("system") ? "Tools"
      : t.adds?.includes("context") ? "Context and memory" : "Text for the system prompt";
    const order = ["Text for the system prompt", "Context and memory", "Tools", "Model"];
    menu.innerHTML = order.map((g) => {
      const items = types.filter((t) => group(t) === g);
      return items.length ? `<div class="hint" style="padding:.4em .9em .1em;font-size:.75em;letter-spacing:.08em">${esc(g.toUpperCase())}</div>
        ${items.map((t) => `<button class="ditem" data-nv="add-type" data-type="${esc(t.id)}" style="display:block;width:100%">${esc(t.title)}</button>`).join("")}` : "";
    }).join("");
    menu.style.maxHeight = "60vh"; menu.style.overflow = "auto";
    menu.style.display = "";
  }

  async _addOf(typeId) {
    this.querySelector(".nv-addmenu").style.display = "none";
    const type = (this._data.types || []).find((n) => n.id === typeId);
    if (!type) return;
    const title = await this._ask(`Name of the new ${type.title.toLowerCase()} node`, `${type.title} 2`);
    if (title === null || title === undefined || !String(title).trim()) return;
    const base = String(title).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 30) || "node";
    let id = base, k = 2;
    while (this._data.nodes.some((n) => n.id === id) || id.length < 2) id = `${base}_${k++}`;
    const settings = Object.fromEntries(Object.entries(type.settings_schema || {}).map(([key, f]) => [key, f.default ?? ""]));
    this._sel = id;
    await this._setInstances({ ...this._instances(), [id]: { type: type.id, title: String(title).trim(), settings } });
    this._msg.textContent = `${String(title).trim()} added: wire it in (drag from an output to its input) to use it.`;
  }

  _saveInstance(id) {
    const box = this.querySelector(".nv-info"), all = this._instances(), inst = all[id];
    if (!inst) return;
    inst.title = box.querySelector("[data-inst-title]")?.value.trim() || inst.title;
    for (const el of box.querySelectorAll("[data-inst-key]")) inst.settings[el.dataset.instKey] = el.value;
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
      const help = f.help ? `<div class="hint" style="margin:-.2em 0 .5em">${esc(f.help)}</div>` : "";
      if (f.type === "bool") return `<label class="check"><input type="checkbox" data-mod-key="${esc(key)}" ${v ? "checked" : ""}>${esc(f.title || key)}</label>${help}`;
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
        <button class="btn" data-go="settings/module/${esc(id)}">Module page</button></div>`;
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

  _settingsForm(n) {
    const field = ([key, f]) => {
      const v = n.settings?.[key] ?? f.default ?? "";
      if (f.type === "select") {
        const opts = (f.options_from ? this._data.options?.[f.options_from] : f.options) || [];
        return `<label>${esc(f.title || key)}</label><select data-inst-key="${esc(key)}">${opts.map(([val, label]) =>
          `<option value="${esc(val)}" ${val === v ? "selected" : ""}>${esc(label)}</option>`).join("")}</select>`;
      }
      if (f.type === "longtext") return `<label>${esc(f.title || key)}</label><textarea data-inst-key="${esc(key)}" class="thin-scroll" style="min-height:5em">${esc(v)}</textarea>`;
      return `<label>${esc(f.title || key)}</label><input data-inst-key="${esc(key)}" value="${esc(v)}">`;
    };
    return `<label>Name</label><input data-inst-title value="${esc(n.title)}">${Object.entries(n.settings_schema || {}).map(field).join("")}
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

  _info() {
    const box = this.querySelector(".nv-info"), n = this._data?.nodes.find((x) => x.id === this._sel);
    const fr = this._frames.find((f) => f.id === this._selFrame);
    const wire = this._selWire && this._wires().find((w) => `${w.a.id}.${w.ap}|${w.b.id}.${w.bp}` === this._selWire);
    if (wire) {
      box.style.display = "";
      box.innerHTML = `<h2>WIRE <span class="tag" style="border-color:${this._color(wire.type)}">${esc(wire.type)}</span></h2>
        <div class="kv"><span class="k">From</span><span class="v">${esc(wire.a.title)} · ${esc(wire.ap)}</span>
          <span class="k">To</span><span class="v">${esc(wire.b.title)} · ${esc(wire.bp)}</span></div>
        <div class="actions"><button class="btn danger" data-nv="unwire">Remove wire</button></div>`;
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
    if (!n) { box.style.display = "none"; return; }
    const ports = (o) => Object.entries(o).map(([p, t]) => `<span class="tag" style="border-color:${this._color(t)}">${esc(p)}: ${esc(t)}</span>`).join(" ") || "—";
    const into = this._wires().filter((w) => w.b.id === n.id).map((w) => `${esc(w.a.title)}.${esc(w.ap)}`).join(", ") || "—";
    const out = this._wires().filter((w) => w.a.id === n.id).map((w) => `${esc(w.b.title)}.${esc(w.bp)}`).join(", ") || "—";
    box.style.display = "";
    box.innerHTML = `<h2>${esc(n.title.toUpperCase())} <span class="tag">${esc(KIND[n.kind] || n.kind)}</span>${n.on ? "" : ' <span class="tag plan">MODULE OFF</span>'}${this._pos[n.id]?.pinned ? ' <span class="tag">PINNED</span>' : ""}</h2>
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
        : n.addable ? '<p class="hint">The standard one. "Add node" makes another with its own settings; the one wired at the end of the chain answers.</p>' : ""}
      ${n.module && !n.type ? this._moduleForm(n.module) : ""}`;
  }
}

if (!customElements.get("aac-nodes")) customElements.define("aac-nodes", AacNodes);

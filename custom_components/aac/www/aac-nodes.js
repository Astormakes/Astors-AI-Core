// The node view (spec/streams.md): every node of the domain and how it is wired.
// Owner idea (2026-10-21): it should feel physical, like a force field. With Physics on, nodes have inertia,
// push each other apart and wires pull wired nodes together into a
// left-to-right flow; drag one and the others give way, let go and it floats into its place. Double-click
// pins a node where it is. With Physics off, everything stays where it is and you place nodes by hand.
// Switching Physics on saves the layout first; Undo goes back to it. Positions are kept per browser and
// domain. Named containers (frames) group nodes: Shift/Ctrl-click nodes, "Group", name it; drag its name bar
// to move it with its members, its corner to resize, double-click the name to rename. A node dropped inside a
// frame joins it, dragged out it leaves; members drift to its middle, other nodes are pushed off its border. Frames are kept
// per domain in AAC (the start of real groups, spec/streams.md). Read-only for now: wiring by hand comes later. No shadow DOM, so the panel's colours and skins
// apply. The panel sets .ws(type, data) and .panel, and calls .refresh() when modules change.

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const SVG = "http://www.w3.org/2000/svg";
const short = (t, n) => (t.length > n ? `${t.slice(0, n - 1)}…` : t); // the box is narrow; the details are below
const W = 176, HEAD = 40, ROW = 17; // node box: width, title area, one port row
const LINK = 90; // how far right of its source a wired node wants to sit
const FIELD = 150; // reach of a node's soft push beyond its box
const COLORS = ["var(--cyan)", "var(--magenta)", "var(--green)", "var(--amber)", "#8ab4ff", "#ff8a65", "#c792ea", "#7fdbca"];
const KIND = { source: "source", sink: "sink", transform: "transform", annotating: "adds to the record" };
const UNDO_STEPS = 10;
const BAR = 26, PAD = 26, GRIP = 16, EDGE = 50; // frame: name bar, room around members, resize corner, border push reach

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

  get _key() { return `aac-nodes-${this.panel?._state?.domain?.id || "default"}`; }

  async refresh(quiet = false) {
    if (!this.ws) return;
    try { this._data = await this.ws("aac/nodes"); this._error = null; } catch (e) { this._error = e.message || String(e); }
    if (this._error) { this._msg.textContent = this._error; return; }
    const first = this._loaded !== this._key;
    if (!this._frameDrag) this._frames = (this._data.containers || []).map((f) => ({ ...f, nodes: [...f.nodes] }));
    this._place();
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
          <button class="btn" data-nv="group" title="Put the nodes selected with Shift/Ctrl-click into a named container">Group</button>
          <button class="btn" data-nv="undo" title="Back to the layout saved when Physics went on">Undo</button>
          <button class="btn" data-nv="fit">Fit</button>
          <button class="btn" data-nv="free" title="Release every pinned node">Unpin all</button>
        </div>
        <div class="nv-stage" style="position:relative;height:clamp(420px, calc(100vh - 260px), 1400px);touch-action:none"></div>
        <div class="nv-legend" style="display:flex;gap:.9em;flex-wrap:wrap;padding:.5em .9em;border-top:1px solid var(--line);font-size:.8em"></div>
        <p class="hint nv-msg" style="margin:.5em .9em"></p>
      </div>
      <div class="card nv-info" style="display:none"></div>`;
    this._stage = this.querySelector(".nv-stage");
    this._msg = this.querySelector(".nv-msg");
    this._svg = document.createElementNS(SVG, "svg");
    this._svg.setAttribute("width", "100%");
    this._svg.setAttribute("height", "100%");
    this._svg.style.display = "block";
    this._svg.innerHTML = `<style>
        .nv-wire { fill: none; stroke-width: 1.6; opacity: .55; }
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
        .nv-frame .fbox { fill: var(--cyan); fill-opacity: .035; stroke: var(--line); stroke-dasharray: 5 4; }
        .nv-frame.sel .fbox { stroke: var(--cyan); stroke-dasharray: none; }
        .nv-frame .fbar { fill: var(--cyan); fill-opacity: .09; cursor: move; }
        .nv-frame .fname { fill: var(--text); font-size: 13px; font-weight: 600; letter-spacing: .04em; pointer-events: none; }
        .nv-frame .fgrip { fill: var(--line); cursor: nwse-resize; }
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

  _port(n, port, side) {
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
    nodes.forEach((n, i) => {
      if (this._pos[n.id]) return;
      // new: next to a node it is wired to, else in a loose column by kind
      const peer = this._wires().map((w) => (w.a.id === n.id ? w.b.id : w.b.id === n.id ? w.a.id : null)).find((id) => id && this._pos[id]);
      const col = { source: 0, annotating: 1, transform: 2, sink: 3 }[n.kind] ?? 2;
      this._pos[n.id] = peer
        ? { x: this._pos[peer].x + (Math.random() - .5) * 60, y: this._pos[peer].y + 80 + Math.random() * 40, vx: 0, vy: 0, pinned: false }
        : { x: (col - 1.5) * (W + LINK) + (Math.random() - .5) * 40, y: (i % 6 - 2.5) * 90 + (Math.random() - .5) * 40, vx: 0, vy: 0, pinned: false };
    });
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
    const nodes = this._data.nodes, P = this._pos;
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
    // Wires are springs: the target's input wants to sit LINK right of the source's output, at the same height
    for (const w of this._wires()) {
      const o = this._port(w.a, w.ap, "out"), i = this._port(w.b, w.bp, "in");
      const dx = (o.x + LINK) - i.x, dy = o.y - i.y, k = .012;
      P[w.b.id].vx += dx * k; P[w.b.id].vy += dy * k;
      P[w.a.id].vx -= dx * k; P[w.a.id].vy -= dy * k;
    }
    // Frames (owner): a gentle pull to the middle for members, a weak push off the border for everyone else
    // (within EDGE outside it, firmer when a node sits inside); the hand always wins while a node is held
    for (const f of this._frames) {
      const cx = f.x + f.w / 2, cy = f.y + (BAR + f.h) / 2;
      for (const n of nodes) {
        if (n.id === this._drag?.id) continue;
        const p = P[n.id], hw = W / 2, hh = this._height(n) / 2;
        if (f.nodes.includes(n.id)) {
          p.vx += (cx - p.x) * .004; p.vy += (cy - p.y) * .004; // to the middle
          const l = f.x + PAD / 2 + hw, r = f.x + f.w - PAD / 2 - hw, t = f.y + BAR + PAD / 2 + hh, b = f.y + f.h - PAD / 2 - hh;
          if (p.x < l) p.vx += (Math.min(l, r) - p.x) * .06; else if (p.x > r) p.vx -= (p.x - Math.max(l, r)) * .06;
          if (p.y < t) p.vy += (Math.min(t, b) - p.y) * .06; else if (p.y > b) p.vy -= (p.y - Math.max(t, b)) * .06;
        } else {
          // gap between the node's box and the frame (negative: they overlap)
          const gx = Math.max(f.x - (p.x + hw), (p.x - hw) - (f.x + f.w)), gy = Math.max(f.y - (p.y + hh), (p.y - hh) - (f.y + f.h));
          const gap = Math.max(gx, gy);
          if (gap >= EDGE) continue;
          const push = gap < 0 ? Math.min(-gap, 60) * .08 + .6 : (1 - gap / EDGE) * .6;
          if (gx > gy) p.vx += Math.sign(p.x - cx || 1) * push; else p.vy += Math.sign(p.y - cy || 1) * push;
        }
      }
    }
    // A soft pull to the middle, then inertia with friction
    let energy = 0;
    for (const n of nodes) {
      const p = P[n.id];
      p.vx -= p.x * .0015; p.vy -= p.y * .0025;
      if (n.id === this._drag?.id) { // held on an elastic band: it follows the cursor, the field still pushes it
        p.vx += (this._drag.tx - p.x) * .18; p.vy += (this._drag.ty - p.y) * .18;
        p.vx *= .62; p.vy *= .62;
      } else if (p.pinned) { p.vx = p.vy = 0; continue; } else { p.vx *= .82; p.vy *= .82; } // friction
      const v = Math.hypot(p.vx, p.vy);
      if (v > 25) { p.vx *= 25 / v; p.vy *= 25 / v; } // no node flies off
      p.x += p.vx; p.y += p.vy;
      energy += v;
    }
    this._layout();
    // Quiet: the field rests until something moves (no CPU for an open view)
    if (energy > .08 * nodes.length || this._drag) this._raf = requestAnimationFrame(() => this._step());
    else {
      this._svg.classList.add("still");
      this._save();
      if (this._fitWhenQuiet) { this._fitWhenQuiet = false; this._fit(); }
    }
  }

  // ------------------------------------------------------------------ drawing
  _draw() {
    const nodesG = this._svg.querySelector(".nv-nodes"), wiresG = this._svg.querySelector(".nv-wires");
    this._svg.querySelector(".nv-frames").innerHTML = this._frames.map((f) => `<g class="nv-frame ${this._selFrame === f.id ? "sel" : ""}" data-frame="${esc(f.id)}">
        <rect class="fbox" rx="10"></rect><rect class="fbar" rx="10" height="${BAR}"></rect>
        <text class="fname">${esc(f.name || "Unnamed")}</text><rect class="fgrip" width="${GRIP}" height="${GRIP}" rx="3"></rect></g>`).join("");
    nodesG.innerHTML = this._data.nodes.map((n) => {
      const h = this._height(n), ins = Object.entries(n.inputs), outs = Object.entries(n.outputs);
      const row = (i) => -h / 2 + HEAD + ROW * i + ROW / 2;
      const flow = Object.values(n.recent || {}).reduce((s, v) => s + v, 0);
      return `<g class="nv-node ${n.on ? "" : "off"} ${this._sel === n.id ? "sel" : ""} ${this._multi.has(n.id) && this._multi.size > 1 ? "multi" : ""} ${this._pos[n.id]?.pinned ? "pinned" : ""}" data-node="${esc(n.id)}">
        <rect class="box" x="${-W / 2}" y="${-h / 2}" width="${W}" height="${h}" rx="7"></rect>
        <text x="${-W / 2 + 10}" y="${-h / 2 + 17}">${esc(n.title)}<tspan class="pin" dx="4">📌</tspan></text>
        <text class="sub" x="${-W / 2 + 10}" y="${-h / 2 + 31}">${esc(short(`${n.module_name}${n.kind === "annotating" ? ` · adds ${n.adds.join(", ")}` : ""}${flow ? ` · ${flow} recent` : ""}`, 30))}</text>
        ${ins.map(([p, t], i) => `<circle cx="${-W / 2}" cy="${row(i)}" r="4.5" fill="${this._color(t)}"><title>${esc(p)}: ${esc(t)}</title></circle>
          <text class="port" x="${-W / 2 + 9}" y="${row(i) + 3.5}">${esc(p)}</text>`).join("")}
        ${outs.map(([p, t], i) => `<circle cx="${W / 2}" cy="${row(i)}" r="4.5" fill="${this._color(t)}"><title>${esc(p)}: ${esc(t)}</title></circle>
          <text class="port" x="${W / 2 - 9}" y="${row(i) + 3.5}" text-anchor="end">${esc(p)}</text>`).join("")}
      </g>`;
    }).join("");
    wiresG.innerHTML = this._wires().map((w, i) => {
      const flow = (w.a.recent || {})[w.ap] > 0;
      return `<path class="nv-wire ${flow ? "flow" : ""}" data-wire="${i}" stroke="${this._color(w.type)}"><title>${esc(w.a.title)}.${esc(w.ap)} → ${esc(w.b.title)}.${esc(w.bp)} (${esc(w.type)})</title></path>`;
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
      const [box, bar, name, grip] = g.children;
      box.setAttribute("width", f.w); box.setAttribute("height", f.h);
      bar.setAttribute("width", f.w);
      name.setAttribute("x", 12); name.setAttribute("y", BAR / 2 + 4.5);
      grip.setAttribute("x", f.w - GRIP - 3); grip.setAttribute("y", f.h - GRIP - 3);
    }
    const wires = this._wires();
    for (const path of this._svg.querySelectorAll(".nv-wire")) {
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
      if (b?.dataset.nv === "rename") this._rename(b.dataset.frame);
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
      if (g) {
        const id = g.dataset.node, w = this._world2(e), p = this._pos[id];
        this._drag = { id, dx: p.x - w.x, dy: p.y - w.y, tx: p.x, ty: p.y, moved: false };
        // Shift/Ctrl adds to the selection for "Group"; a plain click selects only this one
        if (e.shiftKey || e.ctrlKey || e.metaKey) { if (this._multi.has(id)) this._multi.delete(id); else this._multi.add(id); }
        else this._multi = new Set([id]);
        this._sel = id; this._selFrame = null;
        this._draw();
        this._kick();
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
    const up = () => {
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
    this._svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = this._stage.getBoundingClientRect(), v = this._view;
      const k = Math.min(3, Math.max(.25, v.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
      const mx = e.clientX - r.left - r.width / 2, my = e.clientY - r.top - r.height / 2;
      v.x = mx - (mx - v.x) * k / v.k; v.y = my - (my - v.y) * k / v.k; v.k = k;
      this._layout();
    }, { passive: false });
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
    const inside = this._frames.filter((f) => x > f.x && x < f.x + f.w && y > f.y + BAR && y < f.y + f.h)
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
    if (fr) {
      const titles = fr.nodes.map((id) => this._data.nodes.find((x) => x.id === id)?.title || id);
      box.style.display = "";
      box.innerHTML = `<h2>${esc((fr.name || "Unnamed").toUpperCase())} <span class="tag">CONTAINER</span></h2>
        <div class="kv"><span class="k">Nodes</span><span class="v">${esc(titles.join(", ") || "none yet: drop nodes into it")}</span></div>
        <p class="hint">Drag the name bar to move it with its nodes, the corner to resize. Drop a node inside to add it, drag it out to take it away.</p>
        <div class="actions"><button class="btn" data-nv="rename" data-frame="${esc(fr.id)}">Rename</button>
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
      ${n.module ? `<div class="actions"><button class="btn" data-go="settings/module/${esc(n.module)}">Module settings</button></div>` : ""}`;
  }
}

if (!customElements.get("aac-nodes")) customElements.define("aac-nodes", AacNodes);

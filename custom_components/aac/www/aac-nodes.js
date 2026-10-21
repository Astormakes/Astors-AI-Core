// The node view (spec/streams.md): every node of the domain and how it is wired, laid out by itself.
// Owner idea (2026-10-21): wires pull like springs, nodes keep their distance, the whole thing floats into a
// stable shape; drag a node and it stays where you put it (double-click lets it float again). Read-only for
// now: wiring by hand comes later. No shadow DOM, so the panel's colours and skins apply.
// The panel sets .ws(type, data) and .panel, and calls .refresh() when modules change.

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const SVG = "http://www.w3.org/2000/svg";
const W = 176, HEAD = 40, ROW = 17; // node box: width, title area, one port row
const LINK = 90; // how far right of its source a wired node wants to sit
const COLORS = ["var(--cyan)", "var(--magenta)", "var(--green)", "var(--amber)", "#8ab4ff", "#ff8a65", "#c792ea", "#7fdbca"];
const KIND = { source: "source", sink: "sink", transform: "transform", annotating: "adds to the record" };

class AacNodes extends HTMLElement {
  constructor() {
    super();
    this._pos = {}; // node id → {x, y, vx, vy, pinned}
    this._view = { x: 0, y: 0, k: 1 }; // pan and zoom
    this._sel = null;
    this._raf = null;
    this._colors = {};
  }

  connectedCallback() {
    if (!this._svg) this._build();
    this.refresh();
    this._timer = setInterval(() => this.refresh(true), 10000); // the flow counts
    this._resize = new ResizeObserver(() => this._fit());
    this._resize.observe(this);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._resize?.disconnect();
    cancelAnimationFrame(this._raf);
    this._raf = null;
  }

  get _key() { return `aac-nodes-${this.panel?._state?.domain?.id || "default"}`; }

  async refresh(quiet = false) {
    if (!this.ws) return;
    try { this._data = await this.ws("aac/nodes"); this._error = null; } catch (e) { this._error = e.message || String(e); }
    if (this._error) { this._msg.textContent = this._error; return; }
    this._place();
    this._draw();
    if (!quiet) this._kick();
    this._info();
  }

  // ------------------------------------------------------------------ layout
  _build() {
    this.style.display = "block";
    this.innerHTML = `
      <div class="card" style="padding:0;overflow:hidden;position:relative">
        <div class="nv-bar" style="display:flex;gap:.6em;align-items:center;flex-wrap:wrap;padding:.6em .9em;border-bottom:1px solid var(--line)">
          <span class="hint">Drag a node to pin it · double-click to let it float · wheel to zoom · drag the background to move</span>
          <span style="flex:1"></span>
          <button class="btn" data-nv="fit">Fit</button><button class="btn" data-nv="free">Let all float</button>
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
        .nv-node.pinned .pin { display: inline; }
        .nv-node .pin { display: none; }
        .nv-node text { fill: var(--text); font-size: 12px; font-family: inherit; pointer-events: none; }
        .nv-node text.sub { fill: var(--dim); font-size: 10px; }
        .nv-node text.port { fill: var(--dim); font-size: 10px; }
        .nv-node { cursor: grab; }
      </style><g class="nv-world"><g class="nv-wires"></g><g class="nv-nodes"></g></g>`;
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

  _place() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(this._key) || "{}"); } catch (e) { saved = {}; }
    const nodes = this._data.nodes;
    nodes.forEach((n, i) => {
      if (this._pos[n.id]) return;
      if (saved[n.id]) { this._pos[n.id] = { ...saved[n.id], vx: 0, vy: 0, pinned: true }; return; }
      // new: in a loose column by kind, so the first frame is already roughly ordered
      const col = { source: 0, annotating: 1, transform: 2, sink: 3 }[n.kind] ?? 2;
      this._pos[n.id] = { x: (col - 1.5) * (W + LINK) + (Math.random() - .5) * 40, y: (i % 6 - 2.5) * 90 + (Math.random() - .5) * 40, vx: 0, vy: 0, pinned: false };
    });
    for (const id of Object.keys(this._pos)) if (!nodes.some((n) => n.id === id)) delete this._pos[id];
  }

  _save() {
    const pinned = Object.fromEntries(Object.entries(this._pos).filter(([, p]) => p.pinned).map(([id, p]) => [id, { x: Math.round(p.x), y: Math.round(p.y) }]));
    try { localStorage.setItem(this._key, JSON.stringify(pinned)); } catch (e) { /* private window: positions are not kept */ }
  }

  _wires() {
    const byId = Object.fromEntries(this._data.nodes.map((n) => [n.id, n]));
    return this._data.wires.map((w) => {
      const [a, ap] = w.from.split("."), [b, bp] = w.to.split(".");
      return byId[a] && byId[b] ? { a: byId[a], ap, b: byId[b], bp, type: byId[a].outputs[ap] } : null;
    }).filter(Boolean);
  }

  // ------------------------------------------------------------------ physics
  _kick() {
    this._energy = 1;
    if (!this._raf) this._raf = requestAnimationFrame(() => this._step());
  }

  _step() {
    this._raf = null;
    const nodes = this._data?.nodes || [];
    const P = this._pos;
    // Repulsion: boxes keep their distance (stronger when they overlap)
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = P[nodes[i].id], b = P[nodes[j].id];
        let dx = b.x - a.x, dy = b.y - a.y;
        if (!dx && !dy) dx = Math.random() - .5;
        const d2 = Math.max(dx * dx + dy * dy, 400), d = Math.sqrt(d2);
        const reach = (W + 40) ** 2, f = Math.min(30, 9000 / d2 + (d2 < reach ? (reach - d2) / reach * 6 : 0));
        a.vx -= dx / d * f; a.vy -= dy / d * f; b.vx += dx / d * f; b.vy += dy / d * f;
      }
    }
    // Wires: the target's input wants to sit LINK to the right of the source's output, at the same height
    for (const w of this._wires()) {
      const o = this._port(w.a, w.ap, "out"), i = this._port(w.b, w.bp, "in");
      const dx = (o.x + LINK) - i.x, dy = o.y - i.y, k = .035;
      P[w.b.id].vx += dx * k; P[w.b.id].vy += dy * k;
      P[w.a.id].vx -= dx * k; P[w.a.id].vy -= dy * k;
    }
    // A gentle pull to the middle, damping, move
    let energy = 0;
    for (const n of nodes) {
      const p = P[n.id];
      p.vx -= p.x * .004; p.vy -= p.y * .006;
      p.vx *= .78; p.vy *= .78;
      if (p.pinned || n.id === this._drag?.id) { p.vx = p.vy = 0; continue; }
      p.x += p.vx; p.y += p.vy;
      energy += Math.abs(p.vx) + Math.abs(p.vy);
    }
    this._layout();
    // Settled: stop, so an open view costs nothing
    if (energy > .3 * Math.max(1, nodes.length * .2) || this._drag) this._raf = requestAnimationFrame(() => this._step());
  }

  // ------------------------------------------------------------------ drawing
  _draw() {
    const nodesG = this._svg.querySelector(".nv-nodes"), wiresG = this._svg.querySelector(".nv-wires");
    nodesG.innerHTML = this._data.nodes.map((n) => {
      const h = this._height(n), ins = Object.entries(n.inputs), outs = Object.entries(n.outputs);
      const row = (i) => -h / 2 + HEAD + ROW * i + ROW / 2;
      const flow = Object.values(n.recent || {}).reduce((s, v) => s + v, 0);
      return `<g class="nv-node ${n.on ? "" : "off"} ${this._sel === n.id ? "sel" : ""} ${this._pos[n.id]?.pinned ? "pinned" : ""}" data-node="${esc(n.id)}">
        <rect class="box" x="${-W / 2}" y="${-h / 2}" width="${W}" height="${h}" rx="7"></rect>
        <text x="${-W / 2 + 10}" y="${-h / 2 + 17}">${esc(n.title)}<tspan class="pin" dx="4">📌</tspan></text>
        <text class="sub" x="${-W / 2 + 10}" y="${-h / 2 + 31}">${esc(n.module_name)}${n.kind === "annotating" ? ` · adds ${esc(n.adds.join(", "))}` : ""}${flow ? ` · ${flow} recent` : ""}</text>
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
    const v = this._view;
    this._world.setAttribute("transform", `translate(${v.x + this._stage.clientWidth / 2},${v.y + this._stage.clientHeight / 2}) scale(${v.k})`);
    for (const g of this._svg.querySelectorAll(".nv-node")) {
      const p = this._pos[g.dataset.node];
      if (p) g.setAttribute("transform", `translate(${p.x},${p.y})`);
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
    const xs = ps.map((p) => p.x), ys = ps.map((p) => p.y);
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
    this.addEventListener("click", (e) => {
      const b = e.target.closest("[data-nv]");
      if (b?.dataset.nv === "fit") this._fit();
      if (b?.dataset.nv === "free") { for (const p of Object.values(this._pos)) p.pinned = false; this._save(); this._draw(); this._kick(); }
      const go = e.target.closest("[data-go]");
      if (go) this.panel?._go(go.dataset.go);
    });
    this._svg.addEventListener("pointerdown", (e) => {
      const g = e.target.closest(".nv-node");
      this._svg.setPointerCapture(e.pointerId);
      if (g) {
        const id = g.dataset.node, w = this._world2(e), p = this._pos[id];
        this._drag = { id, dx: p.x - w.x, dy: p.y - w.y, moved: false };
        this._sel = id;
        this._kick();
      } else {
        this._pan = { x: e.clientX - this._view.x, y: e.clientY - this._view.y };
      }
    });
    this._svg.addEventListener("pointermove", (e) => {
      if (this._drag) {
        const w = this._world2(e), p = this._pos[this._drag.id];
        p.x = w.x + this._drag.dx; p.y = w.y + this._drag.dy;
        this._drag.moved = true;
        this._kick();
      } else if (this._pan) {
        this._view.x = e.clientX - this._pan.x; this._view.y = e.clientY - this._pan.y;
        this._layout();
      }
    });
    const up = () => {
      if (this._drag) {
        if (this._drag.moved) { this._pos[this._drag.id].pinned = true; this._save(); }
        this._drag = null;
        this._draw(); this._info(); this._kick();
      }
      this._pan = null;
    };
    this._svg.addEventListener("pointerup", up);
    this._svg.addEventListener("pointercancel", up);
    this._svg.addEventListener("dblclick", (e) => {
      const g = e.target.closest(".nv-node");
      if (!g) return;
      this._pos[g.dataset.node].pinned = false;
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

  _info() {
    const box = this.querySelector(".nv-info"), n = this._data?.nodes.find((x) => x.id === this._sel);
    if (!n) { box.style.display = "none"; return; }
    const ports = (o) => Object.entries(o).map(([p, t]) => `<span class="tag" style="border-color:${this._color(t)}">${esc(p)}: ${esc(t)}</span>`).join(" ") || "—";
    const into = this._wires().filter((w) => w.b.id === n.id).map((w) => `${esc(w.a.title)}.${esc(w.ap)}`).join(", ") || "—";
    const out = this._wires().filter((w) => w.a.id === n.id).map((w) => `${esc(w.b.title)}.${esc(w.bp)}`).join(", ") || "—";
    box.style.display = "";
    box.innerHTML = `<h2>${esc(n.title.toUpperCase())} <span class="tag">${esc(KIND[n.kind] || n.kind)}</span>${n.on ? "" : ' <span class="tag plan">MODULE OFF</span>'}</h2>
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

// The Level card's view (spec/cards.md "A card's view"): loudness over the last seconds, newest on the right, moving
// right to left with the clock (also while nothing comes in), and lines where clips begin (dashed) and end. Small in
// the card's port row, or bigger under it (its settings).

const MARKS = { clip: "#4caf50", phrase: "#9c27b0", sentence: "#db4437" };

class AacLevelView extends HTMLElement {
  connectedCallback() {
    if (!this.canvas) {
      this.canvas = document.createElement("canvas");
      this.canvas.style.cssText = "width:100%;height:100%;display:block";
      this.appendChild(this.canvas);
      this.points = []; this.marks = []; // {t: ms (performance.now), db} / {t, kind, edge}
    }
    clearInterval(this.timer);
    this.timer = setInterval(() => (this.isConnected ? this.draw() : clearInterval(this.timer)), 33);
  }

  disconnectedCallback() { clearInterval(this.timer); }

  frame(data) {
    const now = performance.now();
    if (data.mark) { // a clip ended now and began `seconds` ago
      this.marks.push({ t: now, kind: data.mark, edge: "end" });
      if (data.seconds) this.marks.push({ t: now - data.seconds * 1000, kind: data.mark, edge: "start" });
    } else this.points.push({ t: now, db: data.db });
  }

  draw() {
    const c = this.canvas, r = devicePixelRatio, w = (c.width = c.clientWidth * r), h = (c.height = c.clientHeight * r);
    if (!w || !h) return;
    const span = Math.max(1, Number(this.card?.settings?.seconds) || 8) * 1000, now = performance.now();
    this.points = this.points.filter((p) => now - p.t <= span + 1000);
    this.marks = this.marks.filter((m) => now - m.t <= span);
    const g = c.getContext("2d"), color = getComputedStyle(this).getPropertyValue("--primary-color").trim() || "#03a9f4";
    const x = (t) => w - ((now - t) / span) * w, y = (v) => h - Math.max(0, Math.min(1, (v + 70) / 70)) * h; // -70 … 0 dB
    g.clearRect(0, 0, w, h);
    for (const m of this.marks) {
      g.strokeStyle = MARKS[m.kind] || "#999"; g.lineWidth = (m.kind === "sentence" ? 2.5 : 1.5) * r;
      g.setLineDash(m.edge === "start" ? [3 * r, 3 * r] : []);
      g.beginPath(); g.moveTo(x(m.t), 0); g.lineTo(x(m.t), h); g.stroke();
    }
    g.setLineDash([]);
    g.beginPath();
    this.points.forEach((p, i) => (i ? g.lineTo(x(p.t), y(p.db)) : g.moveTo(x(p.t), y(p.db))));
    g.strokeStyle = color; g.lineWidth = 1.5 * r; g.stroke();
  }
}

if (!customElements.get("aac-view-level")) customElements.define("aac-view-level", AacLevelView);

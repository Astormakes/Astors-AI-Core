// The Level card's view (spec/cards.md "A card's view"): loudness over the last seconds, newest on the right, moving
// right to left with the clock (also while nothing comes in), and lines where an Audio clipper cuts: speech starts
// (green), a phrase is cut (purple), the sentence is done (red). Small in the card's port row, or bigger under it.

const MARKS = { start: "#4caf50", phrase: "#9c27b0", end: "#db4437" };

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
    if (data.mark) this.marks.push({ t: now - (data.ago || 0) * 1000, kind: data.mark }); // a cut (a start: `ago` s back)
    else this.points.push({ t: now, db: data.db });
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
      g.strokeStyle = MARKS[m.kind] || "#999"; g.lineWidth = (m.kind === "phrase" ? 1.5 : 2.5) * r;
      g.beginPath(); g.moveTo(x(m.t), 0); g.lineTo(x(m.t), h); g.stroke();
    }
    g.beginPath();
    this.points.forEach((p, i) => (i ? g.lineTo(x(p.t), y(p.db)) : g.moveTo(x(p.t), y(p.db))));
    g.strokeStyle = color; g.lineWidth = 1.5 * r; g.stroke();
  }
}

if (!customElements.get("aac-view-level")) customElements.define("aac-view-level", AacLevelView);

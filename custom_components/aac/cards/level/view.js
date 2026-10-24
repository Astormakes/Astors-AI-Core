// The Level card's view (spec/cards.md "A card's view"): loudness over the last seconds, newest on the right, moving
// right to left, and lines where clips begin and end. Small in the card's port row, or bigger under it (its settings).

const MARKS = { clip: "#4caf50", phrase: "#9c27b0", sentence: "#db4437" };

class AacLevelView extends HTMLElement {
  connectedCallback() {
    if (this.canvas) return;
    this.canvas = document.createElement("canvas");
    this.canvas.style.cssText = "width:100%;height:100%;display:block";
    this.appendChild(this.canvas);
    this.points = []; this.marks = []; this.count = 0; // count: points so far (marks are placed on it)
  }

  frame(data) {
    if (!this.canvas) return;
    const fps = Math.max(5, Math.min(30, Number(this.card?.settings?.fps) || 20));
    if (data.mark) { // a clip ended now and began `seconds` ago
      const end = this.count;
      this.marks.push({ at: end, kind: data.mark, edge: "end" });
      if (data.seconds) this.marks.push({ at: end - Math.round(data.seconds * fps), kind: data.mark, edge: "start" });
    } else {
      this.points.push(data.db);
      this.count += 1;
    }
    this.draw(fps);
  }

  draw(fps) {
    const c = this.canvas, r = devicePixelRatio, w = (c.width = c.clientWidth * r), h = (c.height = c.clientHeight * r);
    const shown = Math.max(1, Number(this.card?.settings?.seconds) || 8) * fps, step = w / shown;
    this.points = this.points.slice(-shown - 1);
    this.marks = this.marks.filter((m) => this.count - m.at <= shown);
    const g = c.getContext("2d"), color = getComputedStyle(this).getPropertyValue("--primary-color").trim() || "#03a9f4";
    const x = (at) => w - (this.count - at) * step, y = (v) => h - Math.max(0, Math.min(1, (v + 70) / 70)) * h; // -70 … 0 dB
    g.clearRect(0, 0, w, h);
    for (const m of this.marks) { // lines where clips begin (dashed) and end (solid)
      g.strokeStyle = MARKS[m.kind] || "#999"; g.lineWidth = (m.kind === "sentence" ? 2.5 : 1.5) * r;
      g.setLineDash(m.edge === "start" ? [3 * r, 3 * r] : []);
      g.beginPath(); g.moveTo(x(m.at), 0); g.lineTo(x(m.at), h); g.stroke();
    }
    g.setLineDash([]);
    g.beginPath();
    const first = this.count - this.points.length;
    this.points.forEach((v, i) => (i ? g.lineTo(x(first + i + 1), y(v)) : g.moveTo(x(first + i + 1), y(v))));
    g.strokeStyle = color; g.lineWidth = 1.5 * r; g.stroke();
  }
}

if (!customElements.get("aac-view-level")) customElements.define("aac-view-level", AacLevelView);

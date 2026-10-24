// The Level card's view (spec/cards.md "A card's view"): loudness over the last seconds, newest on the right, moving
// right to left; it sits in the card's port row.

class AacLevelView extends HTMLElement {
  connectedCallback() {
    if (this.canvas) return;
    this.canvas = document.createElement("canvas");
    this.canvas.style.cssText = "width:100%;height:100%;display:block";
    this.appendChild(this.canvas);
    this.points = this.points || [];
  }

  frame(db) { // one point: dB of the loudest bit since the last one
    const c = this.canvas;
    if (!c) return;
    const r = devicePixelRatio, w = (c.width = c.clientWidth * r), h = (c.height = c.clientHeight * r);
    this.points = [...this.points, db].slice(-Math.ceil(w / (2 * r)));
    const g = c.getContext("2d"), color = getComputedStyle(this).getPropertyValue("--primary-color").trim() || "#03a9f4";
    const y = (v) => h - Math.max(0, Math.min(1, (v + 70) / 70)) * h; // -70 … 0 dB
    g.clearRect(0, 0, w, h);
    g.beginPath();
    this.points.forEach((v, i) => { const x = w - (this.points.length - 1 - i) * 2 * r; i ? g.lineTo(x, y(v)) : g.moveTo(x, y(v)); });
    g.strokeStyle = color; g.lineWidth = 1.5 * r; g.stroke();
  }
}

if (!customElements.get("aac-view-level")) customElements.define("aac-view-level", AacLevelView);

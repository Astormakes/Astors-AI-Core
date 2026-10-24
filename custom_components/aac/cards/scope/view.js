// The Scope card's view (spec/cards.md "A card's view"): level bars scrolling by, the waveform or the spectrum,
// drawn from the frames the card sends while a Nodes page shows it.

class AacScopeView extends HTMLElement {
  connectedCallback() {
    if (this.canvas) return;
    this.canvas = document.createElement("canvas");
    this.canvas.style.cssText = "width:100%;height:100%;display:block;border-radius:6px;background:rgba(127,127,127,.08)";
    this.appendChild(this.canvas);
    this.history = [];
  }

  frame(data) { // one frame from the card
    const c = this.canvas;
    if (!c) return;
    const w = (c.width = c.clientWidth * devicePixelRatio), h = (c.height = c.clientHeight * devicePixelRatio);
    const g = c.getContext("2d"), color = getComputedStyle(this).getPropertyValue("--primary-color").trim() || "#03a9f4";
    const y = (db) => h - Math.max(0, Math.min(1, (db + 80) / 80)) * h; // -80 … 0 dB
    g.clearRect(0, 0, w, h);
    g.fillStyle = color; g.strokeStyle = color;
    if (data.mode === "levels") {
      this.history = [...this.history, data].slice(-Math.floor(w / (3 * devicePixelRatio)));
      const bw = 3 * devicePixelRatio;
      this.history.forEach((f, i) => { const top = y(f.db); g.fillRect(i * bw, top, bw - devicePixelRatio, h - top); });
      g.fillStyle = "#db4437"; g.fillRect(w - bw, y(data.peak), bw, 2 * devicePixelRatio);
    } else if (data.mode === "waveform") {
      const n = data.max.length, bw = w / n;
      data.max.forEach((top, i) => { const a = (1 - top) * h / 2, b = (1 - data.min[i]) * h / 2; g.fillRect(i * bw, a, Math.max(1, bw - 1), Math.max(1, b - a)); });
    } else {
      const n = data.db.length, bw = w / n;
      data.db.forEach((db, i) => { const top = y(db); g.fillRect(i * bw, top, Math.max(1, bw - devicePixelRatio), h - top); });
    }
  }
}

if (!customElements.get("aac-view-scope")) customElements.define("aac-view-scope", AacScopeView);

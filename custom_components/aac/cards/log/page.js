// The Log card's page (spec/cards.md "A card's page"): what reached it, newest first.

class AacLogCardPage extends HTMLElement {
  constructor() {
    super();
    this.addEventListener("click", async (e) => { if (e.target.id === "logclear") { await this.panel.ui("clear"); this.show(); } });
  }

  async show() { this.log = (await this.panel.data())?.log || []; this.render(); }

  event(ev) { if (ev.what === "card") { clearTimeout(this._t); this._t = setTimeout(() => this.show(), 300); } }

  render() {
    const { esc, header, tip } = this.panel;
    const rows = [...(this.log || [])].reverse().map((x) => `<tr><td class="hint" style="white-space:nowrap">${esc(new Date(x.time).toLocaleTimeString())}</td>
      <td><code>${esc(x.what)}</code></td><td class="hint">${esc(x.from)}</td><td style="word-break:break-word">${esc(x.text)}</td></tr>`).join("");
    this.innerHTML = `${header(this.card.title, `${tip("Whatever reaches the card's input: contexts (their newest entry), clips, audio starting and stopping, and what is new on a dotted wire.")}<button class="small" id="logclear">Clear</button>`)}
      <div class="content">${rows ? `<table style="width:100%;font-size:13px;border-collapse:collapse">${rows}</table>` : '<p class="hint">Nothing yet: wire anything into the card.</p>'}</div>`;
  }
}

if (!customElements.get("aac-card-log")) customElements.define("aac-card-log", AacLogCardPage);

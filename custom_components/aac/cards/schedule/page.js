// The Schedule card's page (spec/cards.md "A card's page"): what woke the chain, and what came
// back (quiet: the model had nothing to say).

class AacLogPage extends HTMLElement {
  async show() { this.log = (await this.panel.data())?.log || []; this.render(); }

  event(ev) { if (ev.what === "card") this.show(); }

  render() {
    const { esc, md, header, tip } = this.panel;
    const log = [...(this.log || [])].reverse();
    this.innerHTML = `${header(this.card.title, tip("What woke the chain, and what came back (quiet: the model had nothing to say)."))}
      <div class="content">${log.map((x) => `<div class="card"><div class="hint">${esc(new Date(x.time).toLocaleString())} · ${esc(x.name)}</div>
        <div style="margin:4px 0">${md(x.text)}</div><div>${x.answer === null || x.answer === undefined ? '<span class="hint">working…</span>' : x.answer ? md(x.answer) : '<span class="hint">quiet</span>'}</div></div>`).join("") || '<p class="hint">Nothing yet.</p>'}</div>`;
  }
}

if (!customElements.get("aac-card-schedule")) customElements.define("aac-card-schedule", class extends AacLogPage {});

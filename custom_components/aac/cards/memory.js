// The Memory card's page (spec/cards.md "A card's page"): what it remembers; add or forget by hand.

class AacMemoryPage extends HTMLElement {
  constructor() {
    super();
    this.addEventListener("click", async (e) => {
      const t = e.target;
      if (t.id === "addmem") { const v = this.querySelector("#newmem").value.trim(); if (v) { await this.panel.call("remember", { text: v }); this.show(); } }
      if (t.dataset?.forget) { await this.panel.call("forget", { text: t.dataset.forget }); this.show(); }
    });
  }

  async show() { this.memories = (await this.panel.data())?.memories || []; this.render(); }

  event(ev) { if (ev.what === "card") this.show(); }

  render() {
    const { esc, header } = this.panel;
    const mem = [...(this.memories || [])].reverse();
    this.innerHTML = `${header(this.card.title)}<div class="content">
      <div class="row" style="margin-bottom:12px"><input id="newmem" placeholder="Something to remember…"><button id="addmem">Remember</button></div>
      ${mem.map((m) => `<div class="card row"><span class="t">${esc(m.text)}<div class="hint">${esc(new Date(m.time).toLocaleString())}${m.vector ? " · by meaning" : ""}</div></span>
        <button class="small danger" data-forget="${esc(m.id)}">Forget</button></div>`).join("") || '<p class="hint">No memories yet.</p>'}</div>`;
  }
}

if (!customElements.get("aac-card-memory")) customElements.define("aac-card-memory", AacMemoryPage);

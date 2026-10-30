// The File card's page (spec/cards.md "A card's page"): the Markdown file, to read and edit.

class AacFilePage extends HTMLElement {
  constructor() {
    super();
    this.addEventListener("click", async (e) => {
      if (e.target.id === "savefile") { await this.panel.call("write", { text: this.querySelector("#filetext").value }); e.target.textContent = "Saved"; }
    });
  }

  async show() { this.text = await this.panel.call("read", {}); this.render(); }

  event(ev) { if (ev.what === "card" && document.activeElement !== this.querySelector("#filetext")) this.show(); }

  render() {
    const { esc, header } = this.panel;
    this.innerHTML = `${header(this.card.title, '<button class="small primary" id="savefile">Save</button>')}
      <div class="content"><textarea id="filetext" style="height:70vh;font-family:monospace">${esc(this.text || "")}</textarea></div>`;
  }
}

if (!customElements.get("aac-card-file")) customElements.define("aac-card-file", AacFilePage);

// The Log card's page (spec/cards.md "A card's page"): AAC's log, newest first, when the card may read it (Permissions);
// otherwise what the card wrote itself. A kept clip plays with ▶.

class AacLogCardPage extends HTMLElement {
  constructor() {
    super();
    this.addEventListener("click", async (e) => {
      if (e.target.id === "logolder") { await this.show(true); return; }
      const file = e.target.dataset?.play;
      if (file) { // a kept clip: fetched when played
        try {
          const bytes = Uint8Array.from(atob(await this.panel.ui("audio", { file })), (ch) => ch.charCodeAt(0));
          this.player?.pause();
          this.player = new Audio(URL.createObjectURL(new Blob([bytes], { type: "audio/wav" })));
          this.player.play();
        } catch (err) { e.target.textContent = "gone"; }
      }
    });
  }

  async show(older = false) {
    const got = await this.panel.ui("entries", older && this.log?.length ? { before: this.log[0].id } : {});
    this.all = got.all; this.more = got.entries.length >= 300;
    this.log = older ? [...got.entries, ...(this.log || [])] : got.entries;
    this.render();
  }

  event(ev) { // a new log entry (or a repeat counted up)
    const e = ev.entry;
    if (ev.what !== "log" || !this.log || !(this.all || e.card === this.card.id)) return;
    this.log = [...this.log.filter((x) => x.id !== e.id), e].slice(-2000);
    clearTimeout(this._t); this._t = setTimeout(() => this.render(), 300);
  }

  render() {
    const { header, tip } = this.panel;
    const play = (x) => (x.audio && x.card === this.card.id ? `<button class="small" data-play="${x.audio}" title="Listen to what came in">▶</button> ` : "");
    const rows = (this.log || []).length ? this.panel.logRows(this.log, play) : `<p class="hint">${this.all ? "The log is empty." : "Nothing yet: wire anything into the card."}</p>`;
    this.innerHTML = `${header(this.card.title, tip(this.all ? "AAC's log of this domain (Settings → Logs shows the same, with filters). What reaches this card's input is written there too."
      : "What reaches the card's input, written into AAC's log. Allowed to read AAC's log (Permissions), this page shows all of it."))}
      <div class="content">${this.all ? "" : '<p class="hint">Only what this card wrote: it may not read the rest of AAC\'s log (Permissions page).</p>'}${rows}
      ${this.more ? '<button class="small" id="logolder" style="margin-top:8px">Show older</button>' : ""}</div>`;
  }
}

if (!customElements.get("aac-card-log")) customElements.define("aac-card-log", AacLogCardPage);

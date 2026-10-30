// The Relay's script editor (a setting with the card's own element, spec/cards.md): the code with colours, line
// numbers and suggestions as you type; the ? panel (every function with an example, this domain's tags, values and
// functions, a note on the cache); the last run; and a simple view whose rows write the code.

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const py = (s) => (/\n/.test(s) && !/"""|\\/.test(s) && !s.endsWith('"') ? `"""${s}"""` : JSON.stringify(String(s ?? ""))); // a Python string
const KEYWORDS = ["def", "if", "elif", "else", "for", "in", "not", "and", "or", "is", "return", "break", "continue", "pass", "True", "False", "None"];
const GLOBALS = [
  ["now()", "the time now: now().hour, f\"{now():%H:%M}\""], ["states(\"\")", "a Home Assistant state"], ["state_attr(\"\", \"\")", "an attribute of an entity"],
  ["is_state(\"\", \"on\")", "True when the entity is in that state"], ["stop()", "sends stop"], ["output(\"\")", "the context leaves only at this output (instead of out)"],
  ["signal(\"\")", "adds a signal entry"], ["between(e.text, \"\", \"\")", "the part between two texts"], ["print()", "shows under Last run"],
  ["context", "the whole context"], ["audio", "when AAC last spoke"], ["len()", "how long"], ["str()", "as text"], ["int()", "as a whole number"],
  ["any(… for x in …)", "True when one is"], ["re.search(r\"\", e.text, re.I)", "a pattern"], ["re.sub(r\"\", \"\", e.text)", "replace a pattern"],
];
const ENTRY = [["text", "its text (set it to change it)"], ["tags", "its tags"], ["kind", "said, answer, note …"], ["calls", "an answer's tool calls"], ["data", "what its card added"],
  ["state", "open, closed or cut"], ["add_tag(\"\")", "adds a tag"], ["remove()", "takes it out"]];
const TEXT = [["replace(\"\", \"\")", "every a becomes b"], ["strip()", "spaces and empty lines off both ends"], ["lower()", "small letters"],
  ["upper()", "capitals"], ["title()", "Each Word Capital"], ["startswith(\"\")", "starts so?"], ["endswith(\"\")", "ends so?"],
  ["split(\",\")", "a list of the parts"], ["find(\"\")", "where it is (-1: not there)"], ["count(\"\")", "how often"]];
const CONTEXT = [["text", "the whole context as one text"], ["entries", "every entry"], ["find(\"*.said\")", "the newest entry with a tag"], ["find_all(\"*.said\")", "every entry with a tag"]];
const SNIPPETS = [
  ["@on", '@on("${tag}")\ndef ${name}(e):\n    ', "a block that runs when an entry with that tag passes"],
  ["@new", '@new("${name}")\ndef ${name}(e):\n    e.text = """"""', "a text of its own, in the system prompt"],
  ["@new end", '@new("${name}", at="end", replace=False)\ndef ${name}(e):\n    e.text = ""', "a text attached after the newest thing said"],
  ["@on context", '@on("context")\ndef ${name}(context):\n    ', "once with the whole context"],
];
const TESTS = { contains: "contains", is: "is exactly", starts: "starts with", ends: "ends with", always: "always" };
const DOS = { set: "Set the text to", setfrom: "Set the text to the text of", replace: "Replace", start: "Add at its start", end: "Add at its end", tag: "Add the tag", stop: "Send stop", output: "Send to output" };
const LINE_H = 18;

const STYLE = `
:host { display: block; font-size: 13px; }
:host([wide]) { position: fixed; inset: 56px 24px 24px 24px; z-index: 60; background: var(--card-background-color); padding: 10px 14px;
  border-radius: 10px; box-shadow: 0 8px 40px rgba(0,0,0,.45); display: flex; flex-direction: column; }
:host([wide]) .split { flex: 1; min-height: 0; display: flex; gap: 12px; flex-direction: row-reverse; }
:host([wide]) #side { flex: 0 0 40%; min-width: 0; overflow-y: auto; } :host([wide]) #side:empty { display: none; }
:host([wide]) #side .help, :host([wide]) #side .run { max-height: none; margin: 0; }
:host([wide]) #main { flex: 1; min-width: 0; display: flex; flex-direction: column; } :host([wide]) .ed { flex: 1; height: auto; resize: none; }
* { box-sizing: border-box; }
.bar { display: flex; gap: 6px; align-items: center; margin: 4px 0 6px; flex-wrap: wrap; }
.bar .grow { flex: 1; }
button { font: inherit; font-size: 12px; padding: 3px 9px; border-radius: 12px; border: 1px solid var(--divider-color); cursor: pointer;
  background: var(--card-background-color); color: var(--primary-text-color); }
button.on { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
button.x { padding: 0 7px; } button:disabled { opacity: .5; cursor: default; }
label.check { display: flex; gap: 5px; align-items: center; font-size: 12px; cursor: pointer; }
.ed { position: relative; display: flex; border: 1px solid var(--divider-color); border-radius: 6px; background: var(--code-editor-background-color, var(--secondary-background-color));
  font: 13px/${LINE_H}px ui-monospace, "SF Mono", Menlo, Consolas, monospace; height: 340px; resize: vertical; overflow: hidden; }
.gut { flex: none; width: 34px; padding: 6px 4px 6px 0; text-align: right; color: var(--secondary-text-color); opacity: .7; overflow: hidden; user-select: none; white-space: pre; }
.gut .bad { color: var(--error-color, #db4437); opacity: 1; font-weight: 700; }
.box { position: relative; flex: 1; min-width: 0; }
pre, textarea { position: absolute; inset: 0; margin: 0; padding: 6px 8px; border: 0; font: inherit; white-space: pre; tab-size: 4; overflow: auto; }
pre { pointer-events: none; color: var(--primary-text-color); }
textarea { background: transparent; color: transparent; caret-color: var(--primary-text-color); resize: none; outline: none; }
textarea::selection { background: rgba(3,169,244,.3); }
.k { color: #c678dd; } .s { color: #6a9f3a; } .c { color: var(--secondary-text-color); font-style: italic; } .d { color: #d19a66; }
.n { color: #d19a66; } .f { color: #3d8fd1; } .v { color: #e5a00d; }
.pop { position: absolute; z-index: 5; min-width: 220px; max-width: 360px; max-height: 210px; overflow-y: auto; background: var(--card-background-color);
  border: 1px solid var(--divider-color); border-radius: 6px; box-shadow: 0 4px 14px rgba(0,0,0,.25); font-size: 12px; }
.pop div { padding: 3px 8px; cursor: pointer; display: flex; gap: 8px; } .pop div.on, .pop div:hover { background: rgba(3,169,244,.18); }
.pop code { white-space: nowrap; } .pop span { color: var(--secondary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.problems { color: var(--error-color, #db4437); font-size: 12px; margin: 4px 0; } .ok { color: var(--success-color, #43a047); font-size: 12px; margin: 4px 0; }
.hint { color: var(--secondary-text-color); font-size: 12px; margin: 4px 0; }
.help, .run { border: 1px solid var(--divider-color); border-radius: 6px; padding: 8px; margin: 6px 0; max-height: 460px; overflow-y: auto; }
.help h5 { margin: 10px 0 4px; font-size: 11px; letter-spacing: .06em; color: var(--secondary-text-color); text-transform: uppercase; }
.help .fn { margin: 3px 0; } .help .fn code { cursor: pointer; color: var(--primary-color); } .help pre.ex { position: static; pointer-events: auto;
  background: var(--secondary-background-color); padding: 4px 6px; border-radius: 4px; font-size: 12px; margin: 2px 0 6px; white-space: pre-wrap; }
.note { background: rgba(255,152,0,.12); border-left: 3px solid #ff9800; padding: 6px 8px; margin: 6px 0; font-size: 12px; }
.blk { border: 1px solid var(--divider-color); border-radius: 6px; padding: 6px; margin: 6px 0; }
.row { display: flex; gap: 4px; align-items: center; margin: 3px 0; flex-wrap: wrap; } .row > span { color: var(--secondary-text-color); font-size: 12px; }
.blk input, .blk select, .blk textarea { position: static; font: inherit; font-size: 13px; padding: 4px 6px; border: 1px solid var(--divider-color); border-radius: 4px;
  background: var(--card-background-color); color: var(--primary-text-color); min-width: 0; white-space: normal; }
.blk input { flex: 1; } .blk textarea { width: 100%; min-height: 60px; resize: vertical; }
.act { border-left: 3px solid var(--divider-color); padding-left: 6px; margin-left: 8px; }
`;

class AacRelayScript extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.code = ""; this.dirty = false; this.problems = []; this.pop = null; this.view = "code"; this.ref = null; this.run = null;
  }

  setup({ card, key, value, settings }) { // the panel hands it the card (again after every redraw)
    this.card = card; this.key = key;
    this.simple = settings.simple || null;
    if (!this.dirty) this.code = value || "";
    if (!this.built) { this.built = true; this._draw(); this._check(); } else if (!this.dirty) this._fill();
  }

  remember() { // the panel redraws: keep where the caret was
    const ta = this.shadowRoot.querySelector("textarea");
    this._caret = ta && this.shadowRoot.activeElement === ta ? [ta.selectionStart, ta.selectionEnd, ta.scrollTop] : null;
  }

  connectedCallback() {
    if (!this._caret) return;
    const [a, b, top] = this._caret, ta = this.shadowRoot.querySelector("textarea");
    this._caret = null;
    if (ta) requestAnimationFrame(() => { ta.focus(); ta.setSelectionRange(a, b); ta.scrollTop = top; });
  }

  // ------------------------------------------------------------------ what the domain offers
  get dom() { return this.panel.domain(); }
  _cards() { return this.dom.cards.filter((c) => /^[A-Za-z_]\w*$/.test(c.id)); }
  _tags() { // every tag, and the same with * for "any card"
    const tags = this.dom.tags.map((t) => [t.tag, `${t.card}: ${t.what}`]);
    const any = [...new Set(tags.map(([t]) => `*.${t.split(".").slice(1).join(".")}`))].map((t) => [t, "any card"]);
    return [...tags, ...any, ["*", "every entry"]];
  }
  _vars() { return [...new Set([...this.code.matchAll(/^\s*([A-Za-z]\w*)\s*=[^=]/gm)].map((m) => m[1]))]; }

  // ------------------------------------------------------------------ drawing
  _draw() {
    const simple = !!this.simple;
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>
      <div class="bar"><label class="check" title="Rows with menus that write the code (fewer possibilities)"><input type="checkbox" id="simple" ${simple ? "checked" : ""}>Simple view</label>
        <span class="grow"></span><button id="run" class="${this.view === "run" ? "on" : ""}" title="What the script did the last time a context passed">Last run</button>
        <button id="help" class="${this.view === "help" ? "on" : ""}" title="Every function with an example, and what this domain offers">?</button>
        <button id="wide" title="Large, with the ? panel beside the code (Esc: back)">${this.hasAttribute("wide") ? "Close" : "Wide"}</button></div>
      <div class="split"><div id="side"></div>
      <div id="main"></div></div>
      <div id="status"></div>`;
    const $ = (s) => this.shadowRoot.querySelector(s);
    $("#simple").addEventListener("change", (e) => this._toggleSimple(e.target.checked));
    $("#help").addEventListener("click", () => this._side(this.view === "help" ? "code" : "help"));
    $("#run").addEventListener("click", () => this._side(this.view === "run" ? "code" : "run"));
    $("#wide").addEventListener("click", () => this._wide(!this.hasAttribute("wide")));
    this.shadowRoot.addEventListener("keydown", (e) => { if (e.key === "Escape" && !this.pop && this.hasAttribute("wide")) this._wide(false); });
    this._fill();
    this._side(this.view);
  }

  _wide(on) {
    this.toggleAttribute("wide", on);
    this.shadowRoot.querySelector("#wide").textContent = on ? "Close" : "Wide";
    if (on && this.view === "code") this._side("help");
  }

  _fill() { // the main part: the code, or the simple view's rows
    const main = this.shadowRoot.querySelector("#main");
    const box = this.shadowRoot.querySelector("#simple");
    if (box) box.checked = !!this.simple;
    if (this.simple) { main.innerHTML = this._simpleHtml(); this._bindSimple(); this._status(); return; }
    if (!main.querySelector(".ed")) {
      main.innerHTML = `<div class="ed"><div class="gut"></div><div class="box"><pre></pre><textarea spellcheck="false" autocapitalize="off" autocomplete="off" wrap="off"></textarea></div></div>
        <p class="hint">Saved when you click outside the box (or Ctrl+S). Tab and Enter take a suggestion.</p>`;
      this._bindCode();
    }
    const ta = main.querySelector("textarea");
    if (ta.value !== this.code) ta.value = this.code;
    this._paint();
    this._status();
  }

  _status() {
    const el = this.shadowRoot.querySelector("#status");
    if (!el) return;
    const outs = this.outputs?.length ? ` · outputs: out, ${this.outputs.join(", ")}` : "";
    el.innerHTML = this.problems.length ? `<div class="problems">${this.problems.map(esc).join("<br>")}</div>`
      : `<div class="ok">${this.code.trim() ? "The script is fine" : "Empty: it only passes the context on"}${esc(outs)}${this.dirty ? " · not saved yet" : ""}</div>`;
  }

  // ------------------------------------------------------------------ the code
  _bindCode() {
    const ta = this.shadowRoot.querySelector("textarea"), pre = this.shadowRoot.querySelector(".ed pre"), gut = this.shadowRoot.querySelector(".gut");
    ta.addEventListener("input", () => { this.code = ta.value; this.dirty = true; this._paint(); this._suggest(); clearTimeout(this._ct); this._ct = setTimeout(() => this._check(), 500); });
    ta.addEventListener("scroll", () => { pre.scrollTop = ta.scrollTop; pre.scrollLeft = ta.scrollLeft; gut.scrollTop = ta.scrollTop; this._closePop(); });
    ta.addEventListener("blur", () => setTimeout(() => { if (this.shadowRoot.activeElement !== ta) { this._closePop(); this._save(); } }, 150));
    ta.addEventListener("click", () => this._closePop());
    for (const ev of ["keyup", "mouseup", "input"]) ta.addEventListener(ev, () => { this._lastCaret = ta.selectionStart; }); // the ? panel inserts there
    ta.addEventListener("keydown", (e) => this._key(e, ta));
  }

  _paint() {
    const pre = this.shadowRoot.querySelector(".ed pre"), gut = this.shadowRoot.querySelector(".gut");
    if (!pre) return;
    pre.innerHTML = this._colour(this.code) + "\n ";
    const bad = new Set(this.problems.map((p) => Number((p.match(/^line (\d+)/) || [])[1])));
    const lines = this.code.split("\n").length;
    gut.innerHTML = Array.from({ length: lines }, (_, i) => (bad.has(i + 1) ? `<span class="bad">${i + 1}</span>` : `${i + 1}`)).join("\n") + "\n ";
  }

  _colour(code) {
    const cards = new Set(this._cards().map((c) => c.id));
    const re = /("""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)|[rfRF]{0,2}"(?:\\.|[^"\\\n])*"?|[rfRF]{0,2}'(?:\\.|[^'\\\n])*'?)|(#[^\n]*)|(@\w+)|\b(\d+(?:\.\d+)?)\b|\b([A-Za-z_]\w*)\b(\s*\()?/g;
    let out = "", at = 0, m;
    while ((m = re.exec(code))) {
      out += esc(code.slice(at, m.index));
      const [all, str, com, dec, num, name, call] = m;
      if (str) out += `<span class="s">${esc(str)}</span>`;
      else if (com) out += `<span class="c">${esc(com)}</span>`;
      else if (dec) out += `<span class="d">${esc(dec)}</span>`;
      else if (num) out += `<span class="n">${esc(num)}</span>`;
      else if (KEYWORDS.includes(name)) out += `<span class="k">${esc(name)}</span>${esc(call || "")}`;
      else if (call) out += `<span class="f">${esc(name)}</span>${esc(call)}`;
      else if (cards.has(name) || name === "context" || name === "audio") out += `<span class="v">${esc(name)}</span>`;
      else out += esc(all);
      at = m.index + all.length;
    }
    return out + esc(code.slice(at));
  }

  _key(e, ta) {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); this._save(); return; }
    if ((e.ctrlKey || e.metaKey) && e.key === " ") { e.preventDefault(); this._suggest(true); return; }
    if (this.pop) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); this.pop.at = (this.pop.at + (e.key === "ArrowDown" ? 1 : -1) + this.pop.items.length) % this.pop.items.length; this._drawPop(); return; }
      if (e.key === "Tab" || e.key === "Enter") { e.preventDefault(); this._take(this.pop.items[this.pop.at]); return; }
      if (e.key === "Escape") { e.preventDefault(); this._closePop(); return; }
    }
    const v = ta.value, a = ta.selectionStart, b = ta.selectionEnd;
    if (e.key === "Tab") { e.preventDefault(); this._insert(e.shiftKey ? "" : "    ", a, b, true, e.shiftKey); return; }
    if (e.key === "Enter") { // keep the indentation; one more after a colon
      e.preventDefault();
      const line = v.slice(v.lastIndexOf("\n", a - 1) + 1, a), indent = line.match(/^\s*/)[0] + (/:\s*$/.test(line) ? "    " : "");
      this._insert(`\n${indent}`, a, b);
      return;
    }
    if (e.key === "Backspace" && a === b) { // four spaces at once
      const line = v.slice(v.lastIndexOf("\n", a - 1) + 1, a);
      if (line.length && /^ +$/.test(line) && line.length % 4 === 0) { e.preventDefault(); this._insert("", a - 4, a); }
    }
  }

  _insert(text, a, b, tab = false, out = false) {
    const ta = this.shadowRoot.querySelector("textarea");
    if (tab && out) { // shift+tab: the line four spaces back
      const start = ta.value.lastIndexOf("\n", a - 1) + 1;
      if (ta.value.startsWith("    ", start)) { ta.setSelectionRange(start, start + 4); document.execCommand("insertText", false, ""); }
    } else {
      ta.focus(); ta.setSelectionRange(a, b);
      if (!document.execCommand("insertText", false, text)) ta.setRangeText(text, a, b, "end"); // execCommand keeps the browser's undo
    }
    this.code = ta.value; this.dirty = true; this._paint(); this._status();
    clearTimeout(this._ct); this._ct = setTimeout(() => this._check(), 500);
  }

  // ------------------------------------------------------------------ suggestions
  _suggest(force = false) {
    const ta = this.shadowRoot.querySelector("textarea"), v = ta.value, at = ta.selectionStart;
    const line = v.slice(v.lastIndexOf("\n", at - 1) + 1, at);
    let items = [], word = "";
    const inQuote = line.match(/(?:@on\(|@new\([^)]*(?:before|after)=|find(?:_all)?\(|\bin\s+e\.tags.*|add_tag\()\s*(?:[^)]*,\s*)?"([^"]*)$/) || line.match(/(?:before|after)="([^"]*)$/);
    if (inQuote) { word = inQuote[1]; items = this._tags().map(([t, w]) => ({ label: t, text: t, what: w })); }
    else if (/^\s*@\w*$/.test(line)) {
      word = line.trim();
      items = SNIPPETS.map(([l, t, w]) => ({ label: l, what: w, snippet: t }));
    } else {
      const m = line.match(/([A-Za-z_][\w.]*\.)?([A-Za-z_]\w*)?$/);
      const base = (m?.[1] || "").replace(/\.$/, ""), last = m?.[2] || "";
      word = last;
      if (!base) {
        if (!force && last.length < 2) return this._closePop();
        items = [...GLOBALS.map(([t, w]) => ({ label: t, text: t, what: w })), ...this._cards().map((c) => ({ label: c.id, text: c.id, what: c.title })),
          ...this._vars().map((x) => ({ label: x, text: x, what: "your variable" })), ...KEYWORDS.map((k) => ({ label: k, text: k, what: "" }))];
      } else items = this._members(base, v, at);
    }
    const low = word.toLowerCase();
    items = items.filter((i) => i.label.toLowerCase().startsWith(low) || (low.length > 1 && i.label.toLowerCase().includes(low)))
      .filter((i) => i.label !== word).slice(0, 40);
    if (!items.length) return this._closePop();
    this.pop = { items, at: 0, word };
    this._drawPop();
  }

  _members(base, code, at) { // what can follow "base."
    const parts = base.split("."), card = this._cards().find((c) => c.id === parts[0]);
    const params = [...code.slice(0, at).matchAll(/def \w+\((\w+)\)/g)].map((m) => m[1]);
    if (parts.length === 1 && card) {
      const t = this.panel.type(card.type) || {};
      return [...Object.keys(card.tags || {}).map((tag) => ({ label: tag.split(".").slice(1).join("."), text: tag.split(".").slice(1).join("."), what: `entry: ${card.tags[tag]}` })),
        ...Object.entries(t.values || {}).map(([n, w]) => ({ label: n, text: n, what: `value: ${w}` })),
        ...Object.entries(t.functions || {}).map(([n, f]) => ({ label: `${n}(…)`, text: `${n}(${Object.entries(f.args || {}).filter(([, a]) => a.required).map(([a]) => `${a}=`).join(", ")})`, what: f.help || "" }))];
    }
    if (parts.length === 2 && card) return ENTRY.map(([t, w]) => ({ label: t, text: t, what: w }));
    if (base === "context") return CONTEXT.map(([t, w]) => ({ label: t, text: t, what: w }));
    if (base === "re") return ["search(r\"\", e.text, re.I)", "sub(r\"\", \"\", e.text)", "findall(r\"\", e.text)", "I"].map((t) => ({ label: t, text: t, what: "" }));
    if (base === "audio") return [["last", "when AAC last spoke: .seconds ago"], ["satellites", "per satellite: [id].seconds"]].map(([t, w]) => ({ label: t, text: t, what: w }));
    if (parts.length === 1 && params.includes(base)) return ENTRY.map(([t, w]) => ({ label: t, text: t, what: w }));
    return TEXT.map(([t, w]) => ({ label: t, text: t, what: w })); // a text (e.text., chat.said.text., a variable)
  }

  _drawPop() {
    const box = this.shadowRoot.querySelector(".box"), ta = this.shadowRoot.querySelector("textarea");
    let el = box.querySelector(".pop");
    if (!el) { el = document.createElement("div"); el.className = "pop"; box.appendChild(el); }
    const v = ta.value.slice(0, ta.selectionStart), row = v.split("\n").length - 1, col = v.length - v.lastIndexOf("\n") - 1;
    const cw = this._charWidth();
    el.style.left = `${Math.max(0, Math.min(8 + col * cw - ta.scrollLeft, box.clientWidth - 230))}px`;
    const top = 6 + (row + 1) * LINE_H - ta.scrollTop;
    el.style.top = top + 210 > box.clientHeight && top > 220 ? `${top - LINE_H - 214}px` : `${top + 2}px`;
    el.innerHTML = this.pop.items.map((i, n) => `<div class="${n === this.pop.at ? "on" : ""}" data-n="${n}"><code>${esc(i.label)}</code><span>${esc(i.what || "")}</span></div>`).join("");
    el.querySelectorAll("[data-n]").forEach((d) => d.addEventListener("mousedown", (e) => { e.preventDefault(); this._take(this.pop.items[Number(d.dataset.n)]); }));
    el.querySelector(".on")?.scrollIntoView({ block: "nearest" });
  }

  _charWidth() {
    if (this._cw) return this._cw;
    const pre = this.shadowRoot.querySelector(".ed pre"), span = document.createElement("span");
    span.textContent = "x".repeat(40); pre.appendChild(span);
    this._cw = span.getBoundingClientRect().width / 40 || 7.8; span.remove();
    return this._cw;
  }

  _take(item) {
    const ta = this.shadowRoot.querySelector("textarea"), at = ta.selectionStart, from = at - this.pop.word.length;
    this._closePop();
    if (item.snippet) { // a whole block: its name asked for in the code (tag and name to fill in)
      const line = ta.value.lastIndexOf("\n", at - 1) + 1, n = this._vars().length + (ta.value.match(/^def /gm) || []).length + 1;
      const text = item.snippet.replaceAll("${tag}", "").replaceAll("${name}", item.label === "@on" || item.label === "@on context" ? `block_${n}` : `text_${n}`);
      this._insert(text, line, at);
      const quote = ta.value.indexOf('("', line) + 2;
      ta.setSelectionRange(quote, item.label === "@on" ? quote : ta.value.indexOf('"', quote));
      if (item.label === "@on") this._suggest(true);
      return;
    }
    this._insert(item.text, from, at);
    const inside = item.text.match(/\((["]?)/); // into the brackets (and the first quotes)
    if (inside && /\(\S/.test(item.text) && !/\(\)$/.test(item.text)) {
      const pos = from + item.text.indexOf("(") + 1 + (inside[1] ? 1 : 0);
      ta.setSelectionRange(pos, pos);
    }
  }

  _closePop() { this.pop = null; this.shadowRoot.querySelector(".pop")?.remove(); }

  // ------------------------------------------------------------------ check, save
  async _check() {
    try {
      const r = await this.panel.ui("check", { script: this.code });
      this.problems = r.problems || []; this.outputs = r.outputs || [];
    } catch (err) { this.problems = [String(err.message || err)]; }
    this._paint(); this._status();
  }

  async _save(extra = {}) {
    if (!this.dirty && !Object.keys(extra).length) return;
    this.dirty = false;
    await this.panel.save({ [this.key || "script"]: this.code, ...extra });
  }

  // ------------------------------------------------------------------ the ? panel and the last run
  async _side(view) {
    this.view = view;
    const side = this.shadowRoot.querySelector("#side");
    this.shadowRoot.querySelector("#help")?.classList.toggle("on", view === "help");
    this.shadowRoot.querySelector("#run")?.classList.toggle("on", view === "run");
    if (view === "help") {
      this.ref ||= await this.panel.ui("reference");
      side.innerHTML = `<div class="help">${this._helpHtml()}</div>`;
      side.querySelectorAll("[data-ins]").forEach((el) => el.addEventListener("mousedown", (e) => { e.preventDefault(); this._insertAtCaret(el.dataset.ins); }));
    } else if (view === "run") {
      const r = await this.panel.ui("last_run");
      const when = r?.time ? new Date(r.time * 1000).toLocaleTimeString() : null;
      side.innerHTML = `<div class="run">${when ? `<div class="hint">${esc(when)} · blocks that ran: ${esc((r.ran || []).join(", ") || "none")} · went to: ${esc((r.outputs || []).join(", "))}</div>
        ${(r.printed || []).length ? `<b>print</b><pre class="ex" style="position:static">${esc(r.printed.join("\n"))}</pre>` : ""}
        ${(r.problems || []).length ? `<div class="problems">${r.problems.map(esc).join("<br>")}</div>` : ""}` : '<p class="hint">Nothing passed since the card was last changed.</p>'}
        <button id="rerun">Refresh</button></div>`;
      side.querySelector("#rerun").addEventListener("click", () => this._side("run"));
    } else side.innerHTML = "";
  }

  _helpHtml() {
    const groups = {};
    for (const r of this.ref || []) (groups[r.group] ||= []).push(r);
    const ins = (s) => `<code data-ins="${esc(s)}" title="Click: put it in the code">${esc(s)}</code>`;
    const tools = (this.panel.card()?.tools || []).map((id) => this._cards().find((c) => c.id === id)).filter(Boolean);
    const example = `@new("charter")\ndef charter(e):\n    e.text = """You are AAC, a helpful assistant."""\n\n@on("*.said")\ndef clean(e):\n    e.text = e.text.replace("computer", "").strip()\n    if e.text.lower().startswith("stop"):\n        stop()\n\n@new("time", at="end", replace=False)\ndef time(e):\n    e.text = f"It is {now():%H:%M}."`;
    return `<div class="note"><b>How a script works</b> (Python; no experience needed)<br>
      • A script is made of <b>blocks</b>. A block starts with a line with <b>@</b> (when it runs), then <b>def name(e):</b>, then the lines it does, each moved in by 4 spaces.<br>
      • <b>@on("tag")</b>: the block runs when an entry with that tag passes through the Relay. <b>e</b> is that entry: <b>e.text = "…"</b> changes its text.<br>
      • <b>@new("name")</b>: the block writes a text of the Relay's own. Fill <b>e.text</b>.<br>
      • The context then leaves at <b>out</b>. <b>output("x")</b> sends it out at <b>x</b> instead (the card gets that output).<br>
      • Text goes in quotes; <b>#</b> starts a note that does not run. Click any name below to put it in the code; the Simple view writes code from forms.</div>
      <b>Example</b><pre class="ex">${esc(example)}</pre>
      <div class="note"><b>Speed:</b> the model server keeps what it has already read (its cache). A change near the start or the middle of the context (the system prompt, earlier turns) makes it read everything from there again: answers take longer. Change texts at the end (the newest turn, at="end"); a text at the start is replaced in place and stays fast as long as it stays the same.</div>
      <div class="note"><b>Streams:</b> entries no block works on pass at once, piece by piece. An entry a block works on is held back until its card closes it; then the block runs once. live=True runs it on every piece instead (a stop word mid-sentence).</div>
      ${Object.entries(groups).map(([g, rows]) => `<h5>${esc(g)}</h5>${rows.map((r) => `<div class="fn">${ins(r.sig)} ${esc(r.what)}${r.example ? `<pre class="ex">${esc(r.example)}</pre>` : ""}</div>`).join("")}`).join("")}
      <h5>Tags in this domain</h5>${this.dom.tags.map((t) => `<div class="fn">${ins(t.tag)} <span class="hint">${esc(t.card)}: ${esc(t.what)}</span></div>`).join("")}
      <h5>Cards in its loop (their functions)</h5>${tools.length ? tools.map((c) => Object.entries(this.panel.type(c.type)?.functions || {}).map(([n, f]) => `<div class="fn">${ins(`${c.id}.${n}(${Object.keys(f.args || {}).map((a) => `${a}=`).join(", ")})`)} <span class="hint">${esc(f.help || "")}</span></div>`).join("")).join("")
        : '<p class="hint">None: wire a card with functions into the loop of this Relay (from it, back into it) to call its functions.</p>'}
      <h5>Values of the cards</h5>${this._cards().flatMap((c) => Object.entries(this.panel.type(c.type)?.values || {}).map(([n, w]) => `<div class="fn">${ins(`${c.id}.${n}`)} <span class="hint">${esc(w)}</span></div>`)).join("") || '<p class="hint">No card offers values.</p>'}`;
  }

  _insertAtCaret(text) {
    if (this.simple) return;
    const ta = this.shadowRoot.querySelector("textarea");
    const a = this._lastCaret ?? ta.value.length;
    this._insert(text, a, a);
  }

  // ------------------------------------------------------------------ the simple view: rows that write the code
  async _toggleSimple(on) {
    if (on) {
      const blocks = this.simple || [];
      if (this.code.trim() && this.code.trim() !== this._generate(blocks).trim()
          && !(await this.panel.confirm("The simple view writes the code anew from its rows; the code as it is now goes (Undo brings it back).", "Use the simple view"))) {
        this.shadowRoot.querySelector("#simple").checked = false;
        return;
      }
      this.simple = blocks;
      this.code = this._generate(blocks);
      this.dirty = true;
      await this._save({ simple: blocks });
    } else {
      this.simple = null;
      await this._save({ simple: null });
    }
    this._fill();
  }

  _simpleHtml() {
    const tags = this._tags().map(([t]) => t), outs = this.outputs || [];
    const opt = (v, cur, label = v) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(label)}</option>`;
    const tagList = `<datalist id="tg">${tags.map((t) => `<option value="${esc(t)}">`).join("")}</datalist>`;
    const action = (a, i, j) => {
      const field = { set: `<input data-f="a" value="${esc(a.a || "")}" placeholder="text">`, setfrom: `<input data-f="a" list="tg" value="${esc(a.a || "")}" placeholder="tag">`,
        replace: `<input data-f="a" value="${esc(a.a || "")}" placeholder="this"><span>with</span><input data-f="b" value="${esc(a.b || "")}" placeholder="that">`,
        start: `<input data-f="a" value="${esc(a.a || "")}" placeholder="text">`, end: `<input data-f="a" value="${esc(a.a || "")}" placeholder="text">`,
        tag: `<input data-f="a" value="${esc(a.a || "")}" placeholder="urgent">`, stop: "",
        output: `<input data-f="a" list="outs" value="${esc(a.a || "")}" placeholder="name, e.g. yes">` }[a.do] || "";
      return `<div class="row act" data-act="${j}"><select data-f="do">${Object.entries(DOS).map(([k, l]) => opt(k, a.do, l)).join("")}</select>${field}<button class="x" data-delact="${i}|${j}" title="Remove">×</button></div>`;
    };
    const block = (b, i) => b.type === "new"
      ? `<div class="blk" data-b="${i}"><div class="row"><b>Add a text</b><span>named</span><input data-f="name" value="${esc(b.name || "")}" placeholder="name" style="max-width:110px">
          <span>at</span><select data-f="where">${opt("start", b.where, "the start (system prompt)")}${opt("end", b.where, "the end (after the turn)")}${opt("before", b.where, "before the tag")}${opt("after", b.where, "after the tag")}</select>
          ${["before", "after"].includes(b.where) ? `<input data-f="anchor" list="tg" value="${esc(b.anchor || "")}" placeholder="tag">` : ""}${this._moveBtns(i)}</div>
          <textarea data-f="text" placeholder="The text">${esc(b.text || "")}</textarea></div>`
      : `<div class="blk" data-b="${i}"><div class="row"><b>If</b><input data-f="tag" list="tg" value="${esc(b.tag || "")}" placeholder="tag, e.g. *.said" style="max-width:150px">
          <select data-f="test">${Object.entries(TESTS).map(([k, l]) => opt(k, b.test, l)).join("")}</select>
          ${b.test === "always" ? "" : `<input data-f="value" value="${esc(b.value || "")}" placeholder="text (capitals do not count)">`}${this._moveBtns(i)}</div>
          <div class="row"><b>then</b></div>${(b.actions || []).map((a, j) => action(a, i, j)).join("")}
          <div class="row act"><button data-addact="${i}">+ then …</button></div></div>`;
    return `${tagList}<datalist id="outs">${["out", ...outs].map((o) => `<option value="${esc(o)}">`).join("")}</datalist>
      ${(this.simple || []).map(block).join("") || '<p class="hint">No rows yet: it only passes the context on.</p>'}
      <div class="row"><button id="addif">+ If … then …</button><button id="addnew">+ Add a text</button></div>
      <p class="hint">Rows write the code (untick Simple view to see and change it). Changes save at once.</p>`;
  }

  _moveBtns(i) {
    const n = (this.simple || []).length;
    return `<span style="flex:1"></span><button class="x" data-move="${i}|-1" ${i ? "" : "disabled"} title="Up">↑</button><button class="x" data-move="${i}|1" ${i < n - 1 ? "" : "disabled"} title="Down">↓</button><button class="x" data-del="${i}" title="Remove the row">×</button>`;
  }

  _bindSimple() {
    const root = this.shadowRoot, blocks = () => this.simple;
    const store = async (redraw = false) => {
      this.code = this._generate(blocks()); this.dirty = true;
      await this._save({ simple: blocks() });
      this._check();
      if (redraw) this._fill();
    };
    root.querySelectorAll("[data-b]").forEach((box) => {
      const b = blocks()[Number(box.dataset.b)];
      box.querySelectorAll(":scope > .row [data-f], :scope > textarea[data-f]").forEach((el) => el.addEventListener("change", () => {
        b[el.dataset.f] = el.value; store(["where", "test"].includes(el.dataset.f));
      }));
      box.querySelectorAll("[data-act]").forEach((row) => {
        const a = b.actions[Number(row.dataset.act)];
        row.querySelectorAll("[data-f]").forEach((el) => el.addEventListener("change", () => { a[el.dataset.f] = el.value; store(el.dataset.f === "do"); }));
      });
    });
    root.querySelectorAll("[data-addact]").forEach((el) => el.addEventListener("click", () => { blocks()[Number(el.dataset.addact)].actions.push({ do: "output", a: "yes" }); store(true); }));
    root.querySelectorAll("[data-delact]").forEach((el) => el.addEventListener("click", () => { const [i, j] = el.dataset.delact.split("|").map(Number); blocks()[i].actions.splice(j, 1); store(true); }));
    root.querySelectorAll("[data-del]").forEach((el) => el.addEventListener("click", () => { blocks().splice(Number(el.dataset.del), 1); store(true); }));
    root.querySelectorAll("[data-move]").forEach((el) => el.addEventListener("click", () => { const [i, d] = el.dataset.move.split("|").map(Number), l = blocks(); [l[i], l[i + d]] = [l[i + d], l[i]]; store(true); }));
    root.querySelector("#addif")?.addEventListener("click", () => { blocks().push({ type: "if", tag: "*.said", test: "contains", value: "", actions: [{ do: "output", a: "yes" }] }); store(true); });
    root.querySelector("#addnew")?.addEventListener("click", () => { blocks().push({ type: "new", name: `text_${blocks().length + 1}`, where: "start", text: "" }); store(true); });
  }

  _generate(blocks) { // the simple view's rows as Python
    const name = (s, i) => (String(s || "").toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "") || `text_${i + 1}`).replace(/^(\d)/, "t$1");
    return (blocks || []).map((b, i) => {
      if (b.type === "new") {
        const n = name(b.name, i), where = b.where === "end" ? ', at="end", replace=False' : ["before", "after"].includes(b.where) ? `, ${b.where}=${py(b.anchor || "*")}` : "";
        return `@new(${py(n)}${where})\ndef ${n}(e):\n    e.text = ${py(b.text || "")}\n`;
      }
      const v = py(String(b.value || "").toLowerCase());
      const test = { contains: `${v} in e.text.lower()`, is: `e.text.strip().lower() == ${v}`, starts: `e.text.lower().startswith(${v})`, ends: `e.text.lower().endswith(${v})` }[b.test];
      const acts = (b.actions || []).map((a) => ({
        set: `e.text = ${py(a.a)}`, setfrom: `e.text = context.find(${py(a.a || "*")}).text`, replace: `e.text = e.text.replace(${py(a.a)}, ${py(a.b)})`,
        start: `e.text = ${py(a.a)} + e.text`, end: `e.text = e.text + ${py(a.a)}`, tag: `e.add_tag(${py(a.a || "tagged")})`, stop: "stop()",
        output: `output(${py(name(a.a, 0) === "text_1" ? "yes" : name(a.a, 0))})` }[a.do])).filter(Boolean);
      const body = (acts.length ? acts : ["pass"]).map((x) => (test ? `        ${x}` : `    ${x}`)).join("\n");
      return `@on(${py(b.tag || "*")})\ndef rule_${i + 1}(e):\n${test ? `    if ${test}:\n` : ""}${body}\n`;
    }).join("\n");
  }
}

if (!customElements.get("aac-relay-script")) customElements.define("aac-relay-script", AacRelayScript);

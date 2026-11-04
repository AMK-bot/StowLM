/* Run the complete production page script against a minimal DOM surface.
 * This verifies emitted markup and status updates, not CSS layout or browser paint.
 * No packages, browser downloads or network requests are needed. */
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const staticDir = path.join(__dirname, "..", "static");
const html = fs.readFileSync(path.join(staticDir, "index.html"), "utf8");
class Element {
  constructor(tag, attrs) {
    this.tagName = tag.toUpperCase();
    this.hidden = /\bhidden\b/.test(attrs);
    this.disabled = false;
    this.style = {};
    this.dataset = {};
    this.value = "";
    this.options = [];
    this.attributes = {};
    this.classes = new Set();
    this.classList = {toggle: (name, active) => active ? this.classes.add(name) : this.classes.delete(name)};
    this._html = "";
    this._text = "";
  }
  set innerHTML(value) {
    this._html = String(value); this._text = "";
    if (this.tagName === "SELECT") {
      this.options = [...this._html.matchAll(/<option(?:\s+value="([^"]*)")?[^>]*>([^<]*)<\/option>/g)]
        .map(match => ({value: match[1] ?? match[2]}));
      this.value = this.options[0]?.value || "";
    }
  }
  get innerHTML() { return this._html; }
  set textContent(value) { this._text = String(value); this._html = ""; }
  get textContent() { return this._text; }
  setAttribute(name, value) { this.attributes[name] = value; }
}
const elements = new Map([...html.matchAll(/<(\w+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)]
  .map(match => [match[3], new Element(match[1], match[2])]));
const document = {
  body: new Element("body", ""),
  getElementById: id => {
    if (!elements.has(id)) throw new Error(`Page has no element #${id}`);
    return elements.get(id);
  },
  querySelectorAll: () => [],
  addEventListener: () => {},
};
const context = vm.createContext({document, URLSearchParams, location: {search: ""},
  // Startup fetch deliberately remains pending; tests inject real backend replay JSON.
  fetch: () => new Promise(() => {}), clearInterval: () => {}, setInterval: () => 1,
  setTimeout: () => 1, input});
vm.runInContext(fs.readFileSync(path.join(staticDir, "app.js"), "utf8"), context);
vm.runInContext(`checkerPanel = input.panel || "stability";
  acceptReplay(input.replay); setIndex(input.index || 0); setView(input.view || "operations");
  if (input.catalog) {
    flowCatalog = input.catalog;
    $("instance-select").value = input.selected_instance || data.instance.id;
    $("planner-select").value = input.planner || "solver.scalable";
    updateFlowControls();
  }
  if (input.comparison) { comparison = input.comparison; renderComparison(); setView("compare"); }`, context);
process.stdout.write(JSON.stringify(Object.fromEntries([...elements].map(([id, element]) => [id, {
  html: element.innerHTML, text: element.textContent, hidden: element.hidden,
  color: element.style.color, className: element.className, disabled: element.disabled,
}]))));

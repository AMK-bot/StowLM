/* Run the unmodified production stage-instruct.js against a minimal DOM and read what its card renders.
 * Input (stdin JSON): {search, job, record}. A `job` reaches the real renderJob through ?instruct_job=<id>; a `record`
 * reaches the real renderRecordBadge through ?plan_id=<id>. No packages, browser or network. */
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.hidden = false;
    this.dataset = {};
    this.value = "";
    this.innerHTML = "";
    this.textContent = "";
    this.title = "";
    this.parentNode = this;
    this.classList = {add() {}, remove() {}};
  }
  setAttribute() {}
  appendChild(child) { return child; }
  insertBefore(child) { return child; }
  querySelectorAll() { return []; }
}
const elements = new Map();
const document = {
  readyState: "complete",
  addEventListener() {},
  createElement: tag => new Element(tag),
  querySelector: () => new Element("div"),
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new Element("div"));
    return elements.get(id);
  },
};
const respond = body => ({ok: true, status: 200, json: async () => body});
const fetch = async url => {
  if (url.startsWith("/api/instruct/examples")) return respond({examples: [], live: false});
  if (url.startsWith("/api/instruct/jobs/")) return respond(input.job);
  if (url.startsWith("/api/instruct/plan")) return respond({instruction: input.record});
  throw new Error(`unexpected fetch ${url}`);
};
const context = vm.createContext({document, fetch, URLSearchParams, setTimeout, location: {search: input.search},
  window: {addEventListener() {}}, console});
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "static", "stage-instruct.js"), "utf8"), context);
setTimeout(() => process.stdout.write(JSON.stringify({html: document.getElementById("ins-out").innerHTML})), 20);

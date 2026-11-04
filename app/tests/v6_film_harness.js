/* Runs the unmodified production chapters against a minimal DOM. stdin JSON: {chapter: 'replan'|'xray', ...scenario}. No packages, browser or network. */
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const STATIC = path.join(__dirname, "..", "static");

class El {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase(); this.dataset = {}; this.style = {}; this.hidden = false; this.textContent = ""; this.className = "";
    this.on = {}; this.found = new Map(); this.kids = []; this.html = ""; this.cleared = false;
    this.classList = {add() {}, remove() {}, toggle() {}};
  }
  get innerHTML() { return this.html; }
  set innerHTML(v) { this.html = v; this.found = new Map(); }
  querySelector(sel) { if (!this.found.has(sel)) this.found.set(sel, new El()); return this.found.get(sel); }
  querySelectorAll(sel) { return sel.includes("v6r-gauges") ? ["stability", "balance", "draught", "hull"].map(g => Object.assign(new El(), {dataset: {g}})) : []; }
  addEventListener(type, fn) { this.on[type] = fn; }
  appendChild(child) {
    this.kids.push(child);
    if (child.tagName === "SCRIPT") setTimeout(() => (input.script === "error" ? child.onerror(new Error("load")) : child.onload()), 0);
    return child;
  }
  replaceChildren() { this.cleared = true; this.kids = []; }
  remove() {}
  get children() { return []; }
}

global.window = global;
global.location = {search: ""};
global.addEventListener = () => {};
global.requestAnimationFrame = () => 0;
global.document = {head: new El("head"), body: new El("body"), readyState: "complete", addEventListener() {}, createElement: tag => new El(tag), querySelectorAll: () => [], getElementById: () => null};
const ok = body => ({ok: true, status: 200, json: async () => body});
const settle = (ms = 30) => new Promise(res => setTimeout(res, ms));

function bundle(slot, id) {
  return {meta: {plan_id: id}, initial: {ship: [{box: "A", slot: "0010101"}]}, steps: [{box: "B", src: "yard", dst: slot, slot_cells: [slot]}, {box: "B", src: "yard", dst: slot, slot_cells: [slot]}], physics: {frames: []}};
}

async function replan() {
  require(path.join(STATIC, "stage-story.js"));
  let def;
  global.StageV6 = {chapter(d) { def = d; }};
  require(path.join(STATIC, "stage-v6replan.js"));
  const plans = {before: bundle("0010101", "before"), after: bundle("0010201", "after"), live: bundle("0010301", input.wrong_plan ? "another" : "live")};
  if (input.live_morph === "unfit") plans.live.initial.ship = [{box: "NEW", slot: "0010101"}]; // a box the plan before never held: no morph exists
  const gate = {status: "shown", computed_utc: "2026-11-01T00:00:00Z", recheck: "checked when computed", before: {plan_id: "before", lifts: 12, recheck: "checked when computed"}, after: {plan_id: "after", lifts: 12}, boxes_moved: 1, forced_boxes: ["B"], envelope: {}};
  const job = {status: "done", elapsed_s: 7, incumbents: [{t_s: 1, lifts: 4}], result: {passed: true, lifts: 4, plan_id: "live", forced_boxes: ["B"]}};
  global.fetch = async url => {
    if (url === "/api/v6/confirm") return ok({token: "t"});
    if (url === "/api/v6/replan") return ok({id: "job"});
    const id = new URLSearchParams(url.split("?")[1]).get("plan_id");
    return id === "live" && input.live_bundle === "fail" ? {ok: false, status: 500, json: async () => ({})} : ok(plans[id]);
  };
  const facts = input.no_facts ? null : {forced_ids: ["B"], forced: 1, moved: 1, rearranged: 0, lifts_before: 12, lifts_after: 12, plan_date: "1 Nov 2026", caveat: "Pointwise forced means the old slot is forbidden.", frames: 2, frames_passed: true, repaired: false};
  const d = {facts, rule: {text: "The stored rule", stand_in_note: null}, live_available: true, live: {sentence: "Say", rule: "The live rule", reader: "r", label: "Recorded answer", binding: {}}, rule_counts: {active: 1, off: 0}, incumbents: []};
  const labels = [];
  // The server's replan gate, as app/v6.py answers it: a plan under the rule only for a confirmation token or the recorded answer chosen by name.
  const asked = [], held = {status: "before_only", why: ["No planner confirmation for this replan. Confirm the reading first."], before: gate.before, envelope: {}};
  const answerFor = (name) => {
    const q = new URLSearchParams(name.split("?")[1] || "");
    if (q.get("recorded") === "1") return {gate, incumbents: [], reading_gate: {shown: true, fallback: "recorded", label: "Recorded answer"}};
    if (q.get("token")) return {gate, incumbents: [], reading_gate: {shown: true, live: false}};
    return {gate: held, incumbents: [], reading_gate: {shown: false, needs: "confirmation", why: held.why[0]}};
  };
  if (!input.unconfirmed && !input.video) global.StageV6.confirmation = {token: "t"};
  const ctx = {esc: String, api: async name => (name.startsWith("replan/") ? job : name.startsWith("replan") ? (asked.push(name), answerFor(name)) : job),
    waitAt: () => false, release() {}, label: t => labels.push(t), interactive: !input.video};
  const host = new El();
  const view = def.mount(host, d, ctx);
  await view.preload();
  const stored = {labels: host.querySelector(".v6r-labels").innerHTML, split: host.querySelector(".v6r-split .v6r-forced").textContent, big: host.querySelector(".v6r-big").innerHTML,
    asked: [...new Set(asked)], go: host.querySelector(".v6r-go").innerHTML};
  if (input.stored_only) return stored;
  await host.querySelector(".v6r-go").on.click({target: {closest: () => ({dataset: {live: "1"}})}});
  await settle(60);
  return {labels: host.querySelector(".v6r-labels").innerHTML, note: host.querySelector(".v6r-note").textContent, rule: host.querySelector(".v6r-rule").querySelector("b").textContent,
    big: host.querySelector(".v6r-big").innerHTML, trail: host.querySelector(".v6r-trail").innerHTML, total: host.querySelector(".v6r-lift i").textContent, mode: view.gap ? "gap" : "ok"};
}

async function xray() {
  let mounted = null;
  if (input.player) global.StowLMXray = {mount: () => {
    if (input.player === "throw") throw new Error("refused");
    if (input.player === "reject") return Promise.reject(new Error("refused"));
    if (input.player === "nothing") return null;
    mounted = {at: [], seek(step) { this.at.push(step); }};
    return mounted;
  }};
  const trace = {steps: [{top_k: [{blocked: false}]}, {top_k: [{blocked: true}]}, {top_k: [{blocked: false}]}]};
  const data = {film: true, real: true, trace, blocked: 1, tokens: 3, source_label: "Recorded answer", player_css: "/x.css", player_js: "/x.js", first_block: null};
  global.fetch = async url => ok(url.endsWith("state") ? {synthetic: "", stand_ins: {}, features: {xray: {available: true}}} : data);
  require(path.join(STATIC, "stage-v6.js"));
  const V = global.StageV6;
  require(path.join(STATIC, "stage-v6xray.js"));
  await V.boot(true);
  const prepared = await V.prepare("xray");
  await settle();
  const after = await V.prepare("xray"); // the gap persists: a second look gives the same answer
  const seek = await V.seek("xray", 0.1);
  return {prepared, after, seek, drew: mounted ? mounted.at.length : 0};
}

({replan, xray})[input.chapter]().then(out => { process.stdout.write(JSON.stringify(out)); process.exit(0); }, e => { console.error(e); process.exit(1); });

/* Offline DOM contract harness. Deliberately no npm packages or browser download. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');

function reseal(trace) {
  const payload = JSON.parse(JSON.stringify(trace));
  delete payload.payload_json;
  delete payload.stage_record.output_sha256;
  trace.payload_json = JSON.stringify(payload);
  trace.stage_record.output_sha256 = crypto.createHash('sha256').update(trace.payload_json).digest('hex');
  return trace;
}

class EventTarget {
  constructor() { this.events = new Map(); }
  addEventListener(name, callback) {
    if (!this.events.has(name)) this.events.set(name, new Set());
    this.events.get(name).add(callback);
  }
  removeEventListener(name, callback) { this.events.get(name)?.delete(callback); }
  dispatch(name, event = {}) {
    event.target = event.target || this;
    event.preventDefault = () => { event.prevented = true; };
    for (const callback of this.events.get(name) || []) callback(event);
    return event;
  }
}
class Element extends EventTarget {
  constructor(tag, doc) {
    super(); this.tagName = tag.toUpperCase(); this.ownerDocument = doc;
    this.children = []; this.attributes = {}; this.style = {}; this.className = '';
    this.clientWidth = 1920; this.clientHeight = 1080; this.scrollHeight = 0;
    this.value = ''; this.disabled = false; this._text = '';
    this.classList = {
      add: value => { this.className = [...new Set([...this.classes(), value])].join(' '); },
      remove: value => { this.className = this.classes().filter(x => x !== value).join(' '); },
      contains: value => this.classes().includes(value)
    };
  }
  classes() { return this.className.split(/\s+/).filter(Boolean); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(x => x.textContent).join(''); }
  appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
  replaceChildren(...children) { this.children = []; this._text = ''; children.forEach(c => this.appendChild(c)); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
}
const doc = {createElement: tag => new Element(tag, doc)};
function all(root, cls) {
  return [root, ...root.children.flatMap(node => all(node, ''))].filter(node => !cls || node.classList.contains(cls));
}
function one(root, cls) { const matches = all(root, cls); assert(matches.length, cls + ' exists'); return matches[0]; }
let serial = 0;
const timers = new Map();
const window = new EventTarget();
Object.assign(window, {
  setTimeout: (callback, delay) => { const id = ++serial; timers.set(id, {callback, delay}); return id; },
  clearTimeout: id => timers.delete(id)
});
function tick() {
  assert.equal(timers.size, 1, 'one playback timer');
  const [id, timer] = [...timers][0]; timers.delete(id); timer.callback();
}
window.window = window;
const sandbox = vm.createContext(window);
vm.runInContext(fs.readFileSync(path.join(__dirname, 'player.js'), 'utf8'), sandbox);
const trace = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/fake-illegal-bay.json'), 'utf8'));
const root = new Element('main', doc);
root.clientWidth = 960; root.clientHeight = 540;
const player = window.StowLMXray.mount(root, trace, {initialStep: 0});
assert.equal(window.StowLMXray.format, 'xray.trace/1');
assert.equal(player.getState().step, 0);
assert.equal(one(root, 'xray-source').textContent, 'FAKE · SCRIPTED MODEL');
assert.match(one(root, 'xray-footer').textContent, /FAKE fixture/);
assert(one(root, 'xray-sentence').textContent.includes(trace.sentence));
assert.match(one(root, 'xray-frame').style.transform, /scale\(0\.5\)/);
assert.match(one(root, 'xray-probability-note').textContent, /before the mask.*Top-k may omit/);

const illegalIndex = trace.steps.findIndex(row => row.top_k.some(token => token.reason === 'bay_not_on_ship'));
assert(illegalIndex >= 0, 'fixture contains a blocked illegal bay');
player.seek(illegalIndex + 1);
const illegal = all(root, 'is-blocked').find(node => node.textContent.includes('bay not on this ship'));
assert(illegal, 'illegal bay is blocked with a readable reason');
assert(one(illegal, 'xray-wish-token').textContent.includes('99'), 'blocked wish shows illegal bay 99');
assert.equal(one(root, 'xray-json').textContent, trace.steps[illegalIndex].json_so_far);
assert.equal(one(root, 'xray-chosen-token').textContent, JSON.stringify(trace.steps[illegalIndex].chosen.token));
assert.equal(all(root, 'is-chosen').length, 1, 'one chosen token highlighted');
const visibleWishes = all(root, 'xray-wish-token').map(node => node.textContent);
const expectedWishes = trace.steps[illegalIndex].top_k.map(token => JSON.stringify(token.token));
assert.deepEqual(visibleWishes.slice(0, expectedWishes.length), expectedWishes, 'preserves raw rank');
const css = fs.readFileSync(path.join(__dirname, 'player.css'), 'utf8');
assert.match(css, /\.xray-wish\.is-blocked \.xray-wish-token\s*\{[^}]*text-decoration:\s*line-through/);

const frame = one(root, 'xray-frame');
let key = frame.dispatch('keydown', {key: 'ArrowLeft'});
assert(key.prevented); assert.equal(player.getState().step, illegalIndex);
frame.dispatch('keydown', {key: 'ArrowRight'});
assert.equal(player.getState().step, illegalIndex + 1);
frame.dispatch('keydown', {key: 'Home'});
assert.equal(player.getState().step, 0);
frame.dispatch('keydown', {key: 'ArrowRight', target: one(root, 'xray-scrub')});
assert.equal(player.getState().step, 0, 'range inputs keep native keyboard behavior');
frame.dispatch('keydown', {key: ' '});
assert(player.getState().playing); assert.equal([...timers.values()][0].delay, 500);
tick(); assert.equal(player.getState().step, 1);
player.setSpeed(8);
assert.equal(player.getState().speed, 8); assert.equal([...timers.values()][0].delay, 125);
tick(); assert.equal(player.getState().step, Math.min(2, trace.steps.length));
frame.dispatch('keydown', {key: ' '});
assert(!player.getState().playing); assert.equal(timers.size, 0);
const select = one(root, 'xray-speed').children.find(node => node.tagName === 'SELECT');
select.value = '4'; select.dispatch('change'); assert.equal(player.getState().speed, 4);
const scrub = one(root, 'xray-scrub'); scrub.value = '1'; scrub.dispatch('input');
assert.equal(player.getState().step, 1);
one(root, 'xray-block-button').dispatch('click');
assert(trace.steps[player.getState().step - 1].top_k.some(token => token.blocked));
frame.dispatch('keydown', {key: 'End'});
assert.equal(player.getState().step, trace.steps.length);
assert.equal(one(root, 'xray-json').textContent, trace.output);
assert(all(root, 'xray-transport')[0].children.find(node => node.textContent === 'Step →').disabled);
player.play(); assert.equal(player.getState().step, 0, 'play at end replays');
for (let i = 0; i < trace.steps.length; i++) tick();
assert.equal(player.getState().step, trace.steps.length);
assert(!player.getState().playing); assert.equal(timers.size, 0);
player.seek(-100); assert.equal(player.getState().step, 0);
player.seek(1e6); assert.equal(player.getState().step, trace.steps.length);
player.play(); player.destroy();
assert.equal(timers.size, 0); assert.equal(root.children.length, 0);
assert(!root.classList.contains('xray-player-root'));
assert.equal([...window.events.values()].reduce((sum, rows) => sum + rows.size, 0), 0, 'resize listener cleaned up');

const outside = JSON.parse(JSON.stringify(trace));
const relabelled = JSON.parse(JSON.stringify(trace));
relabelled.source = {kind: 'recorded', label: 'Recorded answer', synthetic: true};
assert.throws(() => window.StowLMXray.mount(root, relabelled), /provenance or payload hash/);
relabelled.stage_record.mode = 'recorded';
assert.throws(() => window.StowLMXray.mount(root, reseal(relabelled)), /provenance or payload hash/);
const edited = JSON.parse(JSON.stringify(trace));
edited.sentence += ' altered';
assert.throws(() => window.StowLMXray.mount(root, edited), /payload hash/);
const editedStage = JSON.parse(JSON.stringify(trace));
editedStage.stage_record.wall_s += 1;
assert.throws(() => window.StowLMXray.mount(root, editedStage), /payload hash/);
const missingHash = JSON.parse(JSON.stringify(trace));
delete missingHash.payload_json;
assert.throws(() => window.StowLMXray.mount(root, missingHash), /payload hash/);
assert.equal(root.children.length, 0, 'invalid traces never render');
outside.steps = [outside.steps[illegalIndex]];
outside.steps[0].top_k = outside.steps[0].top_k.filter(token => token.token_id !== outside.steps[0].chosen.token_id);
outside.steps[0].top_k[0].token = '<img src=x onerror=alert(1)>';
outside.steps[0].top_k.push(
  {token_id: -100, token: '9', probability: 0.001, blocked: true, reason: 'tier_over_cap'},
  {token_id: -101, token: 'banana', probability: 0.0001, blocked: true, reason: 'not_in_grammar'}
);
const replay = window.StowLMXray.mount(root, reseal(outside), {fit: false});
assert.equal(one(root, 'xray-source').textContent, 'FAKE · SCRIPTED MODEL');
assert(one(root, 'xray-frame').classList.contains('xray-fluid'));
assert.match(one(root, 'is-chosen').textContent, /outside raw top-k/);
assert(root.textContent.includes('BLOCKED · tier over the cap'));
assert(root.textContent.includes('BLOCKED · not in grammar'));
assert(all(root, 'xray-wish-token').some(node => node.textContent.includes('<img')));
assert.equal(all(root, '').filter(node => node.tagName === 'IMG').length, 0, 'token strings are text, never markup');
replay.destroy();
// Synthetic metadata only: exercise valid recorded provenance, not real inference.
const recorded = JSON.parse(JSON.stringify(trace));
recorded.source = {kind: 'recorded', label: 'Recorded answer', synthetic: true};
Object.assign(recorded.stage_record, {mode: 'recorded', model: 'muse-glimmer-30b',
  notes: 'Observed local inference', adapter_sha256: 'a'.repeat(64), prompt_sha256: 'b'.repeat(64)});
const recordedPlayer = window.StowLMXray.mount(root, reseal(recorded));
assert.equal(one(root, 'xray-source').textContent, 'RECORDED ANSWER');
recordedPlayer.destroy();
const wrongModel = JSON.parse(JSON.stringify(recorded));
wrongModel.stage_record.model = 'FAKE-scripted-logits';
assert.throws(() => window.StowLMXray.mount(root, reseal(wrongModel)), /provenance/);
const missingAdapter = JSON.parse(JSON.stringify(recorded));
missingAdapter.stage_record.adapter_sha256 = null;
assert.throws(() => window.StowLMXray.mount(root, reseal(missingAdapter)), /provenance/);
const unicode = JSON.parse(JSON.stringify(trace));
unicode.sentence = 'ท่าเรือ 🚢';
unicode.numeric_probe = [0, 1e-9, 1e20];
reseal(unicode);
unicode.payload_json = unicode.payload_json.replace('"numeric_probe":[0,', '"numeric_probe":[0.0,');
unicode.stage_record.output_sha256 = crypto.createHash('sha256').update(unicode.payload_json).digest('hex');
const unicodePlayer = window.StowLMXray.mount(root, unicode);
unicode.sentence = 'changed after mounting';
unicodePlayer.seek(0);
assert(one(root, 'xray-sentence').textContent.includes('ท่าเรือ 🚢'));
unicodePlayer.destroy();
const empty = JSON.parse(JSON.stringify(trace)); empty.steps = [];
const emptyPlayer = window.StowLMXray.mount(root, reseal(empty), {autoplay: true});
assert.equal(emptyPlayer.getState().step, 0); assert.equal(timers.size, 0);
emptyPlayer.destroy();
assert.throws(() => window.StowLMXray.mount(root, {}), /xray.trace/);
doc.getElementById = id => { assert.equal(id, 'xray'); return root; };
sandbox.document = doc;
const demo = fs.readFileSync(path.join(__dirname, 'demo.html'), 'utf8');
for (const script of demo.matchAll(/<script(?:\s+src="([^"]+)")?\s*>([\s\S]*?)<\/script>/g)) {
  const code = script[1] ? fs.readFileSync(path.join(__dirname, script[1]), 'utf8') : script[2];
  vm.runInContext(code, sandbox);
}
assert.equal(window.xrayPlayer.getState().step, illegalIndex + 1, 'demo opens on the illegal bay shot');
assert(one(root, 'is-override').textContent.includes('bay not on this ship'));
assert.equal(one(root, 'xray-source').textContent, 'FAKE · SCRIPTED MODEL');
window.xrayPlayer.destroy();
console.log('Player DOM checks passed: fixture, blocked bay, ranking, chosen, source labels, text safety, keyboard, scrub, autoplay, speed, fit, teardown.');

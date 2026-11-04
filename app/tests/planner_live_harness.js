/* Shared live planner harness. No browser, dependencies, network or model calls.
 * Intake/instruct cases are isolated in runIntake/runInstruct; shell owners may
 * add other case functions without changing those entry points. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const decode = s => String(s).replace(/&(?:amp|lt|gt|quot|#39);/g, x => ({'&amp;':'&','&lt;':'<','&gt;':'>','&quot;':'"','&#39;':"'"}[x]));
const escape = s => String(s == null ? '' : s).replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const dataKey = s => s.replace(/-([a-z])/g, (_, x) => x.toUpperCase());

class Element {
  constructor(tag = 'div', attrs = {}) {
    this.tagName = tag.toUpperCase(); this.attrs = {...attrs}; this.dataset = {};
    for (const [k, v] of Object.entries(attrs)) if (k.startsWith('data-')) this.dataset[dataKey(k.slice(5))] = v;
    this.children = []; this.parentNode = null; this.listeners = {};
    this.value = attrs.value || ''; this.hidden = 'hidden' in attrs; this.disabled = 'disabled' in attrs;
    this.checked = 'checked' in attrs; this.open = 'open' in attrs; this.files = []; this.style = {};
    this.className = attrs.class || ''; this.id = attrs.id || '';
    this.classList = {add: (...xs) => {this.className = [...new Set(this.className.split(/\s+/).concat(xs))].join(' ');},
      remove: (...xs) => {this.className = this.className.split(/\s+/).filter(x => !xs.includes(x)).join(' ');},
      toggle: (x, on) => {const active = on == null ? !this.className.split(/\s+/).includes(x) : on; this.classList[active ? 'add' : 'remove'](x);},
      contains: x => this.className.split(/\s+/).includes(x)};
  }
  set innerHTML(html) {
    this.children = []; const stack = [this];
    for (const token of String(html).match(/<!--[\s\S]*?-->|<[^>]*>|[^<]+/g) || []) {
      if (token.startsWith('<!--')) continue;
      if (token.startsWith('</')) { if (stack.length > 1) stack.pop(); continue; }
      if (token.startsWith('<')) {
        const match = /^<([\w-]+)([\s\S]*?)\/?\s*>$/.exec(token); if (!match) continue;
        const attrs = {}; for (const m of match[2].matchAll(/([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s]+)))?/g)) attrs[m[1]] = decode(m[2] ?? m[3] ?? m[4] ?? '');
        const el = new Element(match[1], attrs); stack.at(-1).appendChild(el);
        if (!/^(?:input|br|hr|img|meta|link|source|wbr)$/i.test(match[1]) && !token.endsWith('/>')) stack.push(el);
      } else stack.at(-1).children.push(decode(token));
    }
    for (const el of this.querySelectorAll('textarea')) el.value = el.textContent;
    for (const el of this.querySelectorAll('select')) {const opts = el.querySelectorAll('option'); el.value = (opts.find(o => 'selected' in o.attrs) || opts[0] || {}).value || '';}
  }
  get innerHTML() {return this.children.map(c => typeof c === 'string' ? escape(c) : c.outerHTML).join('');}
  get outerHTML() {
    const attrs = {...this.attrs}; if (this.className) attrs.class = this.className;
    for (const [k, v] of Object.entries(this.dataset)) attrs['data-' + k.replace(/[A-Z]/g, x => '-' + x.toLowerCase())] = v;
    if (this.hidden) attrs.hidden = ''; else delete attrs.hidden;
    if (this.disabled) attrs.disabled = ''; else delete attrs.disabled;
    if (this.tagName === 'DETAILS') {if (this.open) attrs.open = ''; else delete attrs.open;}
    return `<${this.tagName.toLowerCase()}${Object.entries(attrs).map(([k,v]) => ` ${k}="${escape(v)}"`).join('')}>${this.innerHTML}</${this.tagName.toLowerCase()}>`;
  }
  set textContent(text) {this.children = [String(text)];}
  get textContent() {return this.children.map(c => typeof c === 'string' ? c : c.textContent).join('');}
  get firstElementChild() {return this.children.find(c => c instanceof Element) || null;}
  get parentElement() {return this.parentNode;}
  appendChild(el) {el.parentNode = this; this.children.push(el); return el;}
  append(...els) {for (const el of els) typeof el === 'string' ? this.children.push(el) : this.appendChild(el);}
  replaceChildren(...els) {this.children = []; this.append(...els);}
  remove() {if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(c => c !== this);}
  setAttribute(k, v) {this.attrs[k] = String(v); if (k === 'class') this.className = String(v); if (k.startsWith('data-')) this.dataset[dataKey(k.slice(5))] = String(v);}
  getAttribute(k) {return this.attrs[k] ?? null;}
  removeAttribute(k) {delete this.attrs[k];}
  matches(selector) {
    if (selector.includes(',')) return selector.split(',').some(s => this.matches(s.trim()));
    const attrs = [...selector.matchAll(/\[([^\]=]+)(?:=["']?([^\]"']*)["']?)?\]/g)];
    const base = selector.replace(/\[[^\]]*\]/g, '');
    const tag = /^([\w-]+)/.exec(base); if (tag && this.tagName.toLowerCase() !== tag[1]) return false;
    const id = /#([\w-]+)/.exec(base); if (id && this.id !== id[1]) return false;
    for (const c of base.matchAll(/\.([\w-]+)/g)) if (!this.className.split(/\s+/).includes(c[1])) return false;
    for (const [,k,v] of attrs) {const actual = k.startsWith('data-') ? this.dataset[dataKey(k.slice(5))] : this.attrs[k]; if (actual == null || (v !== undefined && actual !== v)) return false;}
    return true;
  }
  querySelectorAll(selector) {
    if (selector.includes(',')) return [...new Set(selector.split(',').flatMap(s => this.querySelectorAll(s.trim())))];
    const result = [], parts = selector.trim().split(/\s+(?![^\[]*\])/);
    const visit = node => {for (const el of node.children) if (el instanceof Element) {
      if (el.matches(parts.at(-1))) {let ancestor = el.parentNode, i = parts.length - 2; while (i >= 0 && ancestor) {if (ancestor.matches(parts[i])) i--; ancestor = ancestor.parentNode;} if (i < 0) result.push(el);}
      visit(el);
    }}; visit(this); return result;
  }
  querySelector(selector) {return this.querySelectorAll(selector)[0] || null;}
  closest(selector) {return this.matches(selector) ? this : this.parentNode?.closest(selector) || null;}
  contains(el) {return el === this || this.children.some(c => c instanceof Element && c.contains(el));}
  addEventListener(type, fn) {(this.listeners[type] ||= []).push(fn);}
  removeEventListener(type, fn) {this.listeners[type] = (this.listeners[type] || []).filter(f => f !== fn);}
  async fire(type) {
    if (type === 'click' && this.disabled) return;
    const event = {type, target:this, currentTarget:this, preventDefault(){}, stopPropagation(){this.stopped = true;}};
    for (let node = this; node; node = node.parentNode) {event.currentTarget = node; if (node['on' + type]) await node['on' + type](event); for (const fn of node.listeners[type] || []) await fn(event); if (event.stopped) break;}
  }
  click() {return this.fire('click');}
  focus() {} scrollIntoView() {} pause() {} load() {}
}

function createEnvironment(input) {
  const definitions = {}, posts = [], gets = [], events = [], statuses = [], toasts = [], sheets = [], timers = new Map();
  const panel = new Element(), handlers = new Map(); let timerId = 0;
  const document = {createElement: tag => new Element(tag), body:new Element('body'), addEventListener(){}, querySelector:s => panel.querySelector(s)};
  const fakeFile = file => ({name:file.name || 'sample.wav', type:file.type || 'audio/wav', size:file.size || 48,
    arrayBuffer:async () => Uint8Array.from(file.bytes || [82,73,70,70]).buffer});
  const ctx = {live:true, call:input.view || null, state:{}, esc:escape, chip:(x) => x,
    callId(){return this.call?.id || null;}, on(name, fn){if (!handlers.has(name)) handlers.set(name, []); handlers.get(name).push(fn); return () => {};},
    emit(name, value){events.push({name,value}); for (const fn of handlers.get(name) || []) fn(value);},
    status:text => statuses.push(text), done:() => events.push({name:'step:done'}), toast:text => toasts.push(text),
    sheet:(node,title) => sheets.push({html:node?.innerHTML || '', title}), log(){}, wait:async () => {},
    loadlist:{setRows(rows,meta){ctx.list = {rows,meta};},show(){}},
    fx:{get:async url => {
      gets.push(url);
      if (url !== 'status') throw Error(`Unexpected GET ${url}`);
      if (env.launchStatus?.error) throw Error(env.launchStatus.error);
      return env.launchStatus;
    }, post:async (url, body) => {
      posts.push({path:url,body}); const response = env.responses.shift();
      if (response?.error) throw Error(response.error);
      if (response) {ctx.call = response; return response;} return ctx.call;
    }}};
  const window = {Planner:{stage:def => {definitions[def.id] = def;}}};
  class FileReader {readAsDataURL(file) {file.arrayBuffer().then(b => {this.result = 'data:' + file.type + ';base64,' + Buffer.from(b).toString('base64'); this.onload?.({target:this});});}}
  const env = {panel,ctx,definitions,posts,gets,events,statuses,toasts,sheets,timers,responses:[],fakeFile,keyPanels:0,
    launchStatus:input.status || {key:{present:true,paid_calls_accepted:true,cap_usd:2,spent_usd:0}}};
  if (!input.no_key_panel) ctx.openKeyPanel = async () => {
    env.keyPanels++;
    if (input.key_panel_status) {env.launchStatus = input.key_panel_status; ctx.emit('status:update',env.launchStatus);}
  };
  const navigator = {};
  if (input.browser_audio) {
    env.audioState = {stopped:0,closed:0};
    navigator.mediaDevices = {getUserMedia:async () => ({getTracks:() => [{stop(){env.audioState.stopped++;}}]})};
    window.AudioContext = class {
      constructor() {this.sampleRate = input.source_rate || 48000; this.destination = {};}
      async resume() {} async close() {env.audioState.closed++;}
      createMediaStreamSource() {return {connect(){},disconnect(){}};}
      createScriptProcessor() {env.audioNode = {connect(){},disconnect(){}}; return env.audioNode;}
    };
  }
  env.sandbox = vm.createContext({window,document,console,FileReader,Blob,ArrayBuffer,DataView,Float32Array,Uint8Array,Int16Array,TextEncoder,Math,Date,
    navigator, URL:{createObjectURL:() => 'blob:synthetic',revokeObjectURL(){}},
    btoa:s => Buffer.from(s,'binary').toString('base64'), atob:s => Buffer.from(s,'base64').toString('binary'),
    setTimeout:fn => {timers.set(++timerId,fn); return timerId;},clearTimeout:id => timers.delete(id),
    setInterval:fn => {timers.set(++timerId,fn); return timerId;},clearInterval:id => timers.delete(id)});
  return env;
}
async function flush() {for (let i = 0; i < 16; i++) await Promise.resolve();}
async function runModule(input, name) {
  const env = createEnvironment(input), {ctx,panel} = env;
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','static',`planner-live-${name}.js`),'utf8'),env.sandbox);
  const def = env.definitions[name]; if (!def) throw Error(`No ${name} stage`);
  const view = def.mount(panel,{},ctx) || {}; await view.enter?.(); await flush();
  for (const step of input.steps || []) {
    if ('response' in step) env.responses.push(step.response);
    if ('call' in step) {ctx.call = step.call; ctx.emit('call:update',step.call);}
    if ('call_only' in step) ctx.call = step.call_only;
    if (step.emit) ctx.emit(step.emit.name,step.emit.value);
    if ('status' in step) {env.launchStatus = step.status; ctx.emit('status:update',step.status);}
    if ('status_only' in step) env.launchStatus = step.status_only;
    if (step.selector) {
      const el = panel.querySelector(step.selector); if (!el) throw Error(`Missing selector ${step.selector}`);
      if ('value' in step) el.value = step.value;
      if ('checked' in step) el.checked = step.checked;
      if ('open' in step) el.open = step.open;
      if (step.files) el.files = step.files.map(env.fakeFile);
      await el.fire(step.event || (step.action ? 'click' : 'input'));
    }
    if (step.lifecycle) await view[step.lifecycle]?.();
    if (step.audio_samples) {
      if (!env.audioNode?.onaudioprocess) throw Error('No active audio processor');
      const channel = new Float32Array(step.audio_samples); channel.fill(0.25);
      env.audioNode.onaudioprocess({inputBuffer:{getChannelData:() => channel}});
    }
    if (step.tick) for (const fn of [...env.timers.values()]) fn();
    await flush();
  }
  const out = {html:panel.innerHTML,text:panel.textContent,posts:env.posts,gets:env.gets,keyPanels:env.keyPanels,events:env.events,statuses:env.statuses,toasts:env.toasts,sheets:env.sheets,list:ctx.list,
    audioState:env.audioState,definition:{id:def.id,mode:def.mode,order:def.order,features:def.features},
    controls:panel.querySelectorAll('button, input, select, textarea').map(el => ({tag:el.tagName,action:el.dataset.action,field:el.dataset.liveField,value:el.value,disabled:el.disabled,hidden:el.hidden}))};
  await view.leave?.(); return out;
}

// fa-inputs: intake cases.
async function runIntake(input) {return runModule(input,'intake');}
// fa-inputs: instruction cases.
async function runInstruct(input) {return runModule(input,'instruct');}

// fa-ui: shell cases (planner.js in demo or live mode, every module planner.html loads, recorded requests).
// Additive patches to Element live only inside runShell's sandbox setup; the intake/instruct cases above are unchanged.
async function runShell(input) {
  const staticDir = path.join(__dirname, '..', 'static');
  const page = fs.readFileSync(path.join(staticDir, 'planner.html'), 'utf8');
  const proto = Element.prototype;
  const withStyle = el => { if (el && el.style && !el.style.setProperty) { const st = el.style; st.setProperty = (k, v) => { st[k] = v; }; st.removeProperty = k => { delete st[k]; }; } return el; };
  if (!proto.__shellPatched) {
    proto.__shellPatched = true;
    const append = proto.appendChild;
    proto.appendChild = function (el) { if (el instanceof Element) { el.remove(); withStyle(el); } return append.call(this, el); };
    proto.insertBefore = function (el, ref) { if (el instanceof Element) { el.remove(); el.parentNode = this; withStyle(el); } const i = this.children.indexOf(ref); this.children.splice(i < 0 ? this.children.length : i, 0, el); return el; };
    proto.insertAdjacentHTML = function (where, html) { const t = new Element('div'); t.innerHTML = html; const kids = t.children; for (const k of kids) if (k instanceof Element) k.parentNode = this; if (where === 'afterbegin') this.children.unshift(...kids); else this.children.push(...kids); };
    proto.getBoundingClientRect = function () { return {left: 0, top: 0, right: 100, bottom: 20, width: 100, height: 20}; };
    proto.dispatchEvent = function (event) { this.fire(event.type); return true; };
    proto.scrollTo = function () {};
    Object.defineProperty(proto, 'isConnected', {get() { let n = this; while (n.parentNode) n = n.parentNode; return n.tagName === 'HTML'; }});
    Object.defineProperty(proto, 'offsetWidth', {get() { return 100; }});
  }
  const root = new Element('html'); withStyle(root);
  root.innerHTML = page.replace(/^<!doctype html>/i, '').replace(/<\/?html[^>]*>/g, '');
  const walk = n => { withStyle(n); for (const c of n.children) if (c instanceof Element) walk(c); }; walk(root);
  const byId = id => root.querySelectorAll(`#${id}`)[0] || null;
  const scripts = [...page.matchAll(/<script src="\/([^"]+)"/g)].map(m => m[1]);
  // Virtual clock: timers run in due order as the case advances time.
  let now = 0, seq = 0; const timers = new Map();
  const later = (fn, ms) => { const id = ++seq; timers.set(id, {fn, due: now + Math.max(0, +ms || 0)}); return id; };
  const requests = [], errors = [], routes = input.routes || {}, storage = new Map(Object.entries(input.local_storage || {}));
  const url = new URL(input.url || '/planner?live=1', 'http://127.0.0.1:8795');
  const answer = (status, body) => ({ok: status >= 200 && status < 300, status, json: async () => JSON.parse(JSON.stringify(body))});
  const fetch = async (target, init = {}) => {
    const method = (init.method || 'GET').toUpperCase(), u = new URL(String(target), url.origin), key = `${method} ${u.pathname}`;
    requests.push({method, path: u.pathname + u.search, body: init.body ? JSON.parse(init.body) : null});
    const hit = routes[key];
    if (hit === undefined) return answer(404, {error: `not in this harness case: ${key}`});
    const row = Array.isArray(hit) ? (hit.length > 1 ? hit.shift() : hit[0]) : hit;
    // {"__delay_ms": n, "body": ...} answers after n ms of virtual time (late responses, code-2 P1-2).
    if (row && row.__delay_ms) { await new Promise(res => later(res, row.__delay_ms)); return answer(row.__status || 200, row.body); }
    return row && row.__status ? answer(row.__status, row.body) : answer(200, row);
  };
  const document = {
    readyState: 'complete', documentElement: root, body: root.querySelector('body'), activeElement: null,
    getElementById: byId, querySelector: s => root.querySelector(s), querySelectorAll: s => root.querySelectorAll(s),
    createElement: tag => withStyle(new Element(tag)), createElementNS: (_ns, tag) => withStyle(new Element(tag)),
    addEventListener() {}, removeEventListener() {}, elementFromPoint: () => null,
  };
  const win = {
    document, location: {search: url.search, pathname: url.pathname, href: url.href, origin: url.origin},
    history: {state: null, replaceState(_s, _t, next) { const n = new URL(next, url.origin); win.location.search = n.search; win.location.pathname = n.pathname; }},
    localStorage: {getItem: k => (storage.has(k) ? storage.get(k) : null), setItem: (k, v) => storage.set(k, String(v)), removeItem: k => storage.delete(k)},
    innerWidth: 1600, innerHeight: 1000, devicePixelRatio: 1, addEventListener() {}, removeEventListener() {},
    getComputedStyle: () => ({overflowY: 'visible', getPropertyValue: () => ''}), matchMedia: () => ({matches: false, addEventListener() {}}),
    requestAnimationFrame: fn => later(() => fn(now), 16), cancelAnimationFrame: id => timers.delete(id),
    setTimeout: later, clearTimeout: id => timers.delete(id), setInterval: (fn, ms) => { const id = ++seq; const tick = () => { fn(); if (timers.has(id)) timers.set(id, {fn: tick, due: now + Math.max(1, +ms || 1)}); }; timers.set(id, {fn: tick, due: now + Math.max(1, +ms || 1)}); return id; },
    clearInterval: id => timers.delete(id), performance: {now: () => now}, fetch, console: {log() {}, warn() {}, error: (...a) => errors.push(a.map(String).join(' '))},
    URL, URLSearchParams, Element, SVGElement: Element, PointerEvent: class { constructor(type) { this.type = type; } }, MouseEvent: class { constructor(type) { this.type = type; } },
    CSS: {escape: s => String(s)}, Blob, FileReader: class {}, navigator: {}, Math, Date, JSON, Promise, Array, Object, Number, String, Set, Map,
  };
  win.window = win; win.globalThis = win;
  const sandbox = vm.createContext(win);
  for (const name of scripts) {
    if ((input.skip_scripts || []).includes(name)) continue;
    const file = path.join(staticDir, name);
    if (!fs.existsSync(file)) { errors.push(`missing script ${name}`); continue; }
    try { vm.runInContext(fs.readFileSync(file, 'utf8'), sandbox, {filename: name}); } catch (e) { errors.push(`${name}: ${e.message}`); }
  }
  // input.fake_bay: a stand-in BayPlan that records what the shell asks it to draw (the real one is slow in this DOM).
  const bayLog = [];
  if (input.fake_bay) {
    const fake = {setPlan: p => bayLog.push({kind: 'setPlan', slots: (p && p.slots || []).length, plan_id: p && p.plan_id}),
      preGenerate: slots => bayLog.push({kind: 'preGenerate', slots: (slots || []).length}), showBays() {}, focusBay() {}, closeBay() {}, openBay() {},
      highlight() {}, play: async () => ({completed: 0, total: 0, cancelled: false}), on() {}, screenPoint: () => null};
    sandbox.BayPlan = {create: () => fake};
  }
  let bayCreates = 0;
  if (sandbox.BayPlan && sandbox.BayPlan.create) { const create = sandbox.BayPlan.create; sandbox.BayPlan.create = (...a) => { bayCreates++; return create(...a); }; }
  const settle = () => new Promise(r => setImmediate(r));
  async function advance(ms) {
    const end = now + ms;
    for (let guard = 0; guard < 20000; guard++) {
      await settle(); await settle();
      let next = null; for (const [id, t] of timers) if (t.due <= end && (!next || t.due < next[1].due)) next = [id, t];
      if (!next) break;
      timers.delete(next[0]); now = next[1].due;
      try { await next[1].fn(now); } catch (e) { errors.push(`timer: ${e.message}`); }
    }
    now = end; await settle();
  }
  await advance(input.boot_ms || 3000);
  for (const step of input.steps || []) {
    if (step.routes) Object.assign(routes, step.routes);
    for (const [sel, value] of Object.entries(step.set || {})) { const el = root.querySelector(sel); if (!el) throw Error(`Missing selector ${sel}`); el.value = value; }
    for (const sel of step.check || []) { const el = root.querySelector(sel); if (!el) throw Error(`Missing selector ${sel}`); el.checked = true; }
    // A handler may return a promise that settles much later (openKeyPanel resolves on close): don't wait on it.
    if (step.click) { const el = root.querySelector(step.click); if (!el) throw Error(`Missing selector ${step.click}`); el.click().catch(e => errors.push(`click: ${e.message}`)); }
    if (step.call) await sandbox.Planner[step.call](...(step.args || []));
    if (step.emit) { const c = sandbox.Planner.ctx(step.emit.stage || 'plan'); c.emit(step.emit.name, step.emit.value); }
    await advance(step.advance_ms || 1500);
  }
  const snap = id => { const el = byId(id); return el ? {html: el.innerHTML, text: el.textContent, hidden: !!el.hidden, disabled: !!el.disabled, state: el.dataset.state || null, cls: el.className} : null; };
  const ids = ['pl-call-title', 'pl-call-sub', 'pl-demo-tag', 'pl-sections', 'pl-inputs-sum', 'pl-legend', 'pl-bay', 'pl-hero', 'pl-hero-chips', 'pl-hero-why', 'pl-generate', 'pl-result', 'pl-tabs', 'pl-panels', 'pl-review-lock', 'pl-meters', 'pl-scrub', 'pl-header'];
  const sections = root.querySelectorAll('#pl-sections .pl-sec').map(s => ({stage: s.dataset.stage, state: s.dataset.state, summary: (s.querySelector('.pl-sec-sum') || {textContent: ''}).textContent, open: s.classList.contains('open'), text: s.textContent}));
  const P = sandbox.Planner || {};
  const body = root.querySelector('body');
  return {requests, errors, elements: Object.fromEntries(ids.map(id => [id, snap(id)])), sections, bayCreates,
    body: body.textContent.replace(/\s+/g, ' '), bodyHtml: body.innerHTML,
    meters: root.querySelectorAll('#pl-meters .pl-meter').map(m => ({key: m.dataset.m, state: m.dataset.state, value: (m.querySelector('i') || {textContent: ''}).textContent, limit: (m.querySelector('.pl-meter-limit') || {textContent: ''}).textContent})),
    bayLog, planSlots: P.ctx && P.ctx('plan') && P.ctx('plan').plan ? (P.ctx('plan').plan.slots || []).length : null,
    steps: P.steps || [], generated: !!P.generated, live: !!P.live, storage: Object.fromEntries(storage), search: win.location.search,
    liveCss: root.querySelectorAll('link[data-live-css]').map(l => l.media || l.attrs.media || null),
    tabs: root.querySelectorAll('#pl-tabs .pl-tab').map(t => ({tab: t.dataset.tab, disabled: !!t.disabled, text: t.textContent}))};
}

module.exports = {Element,createEnvironment,runModule,runIntake,runInstruct,runShell};
if (require.main === module) {
  const input = JSON.parse(fs.readFileSync(0,'utf8'));
  const run = input.area === 'intake' ? runIntake : input.area === 'instruct' ? runInstruct : input.area === 'shell' ? runShell : null;
  if (!run) throw Error('Expected area intake, instruct or shell');
  run(input).then(out => process.stdout.write(JSON.stringify(out))).catch(e => {console.error(e);process.exitCode = 1;});
}

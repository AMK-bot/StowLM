"""Studio's paper/radio workflow against real v6 fixtures, without a browser."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import stage, v6

STATIC = Path(v6.__file__).with_name("static")
NODE = shutil.which("node")
STEPS = [("intake", 10, "Cargo papers", "papers", ["intake"]),
         ("instruct", 20, "Instructions", "radio", ["voice", "reading", "xray"])]


@pytest.fixture(scope="module")
def payloads():
    # These are the server's real builders: no hand-maintained sample payload.
    return {name: getattr(v6, name)() for name in ("intake", "voice", "reading", "xray")}


def require_recorded_radio(voice):
    """A missing URL alone is a regression, not evidence of withheld audio."""
    path = v6._radio_take()[0]
    stock_roots = (v6.ROOT / "experiments/mvp/pipeline/radio/audio", v6.VOICE / "audio")
    stock_take = not voice.get("own_recording", False) and any(
        path.is_relative_to(root) for root in stock_roots
    )
    if stock_take and not path.is_file() and voice.get("audio_note") == v6.AUDIO_ABSENT:
        pytest.skip(
            f"{path.relative_to(v6.ROOT)}: stock TTS audio deliberately withheld; "
            "no redistribution licence; recorded-audio waveform/autoplay timing requires this take"
        )


@pytest.fixture(scope="module")
def evidence():
    return json.loads((STATIC / "studio-data/evidence.json").read_text())


@pytest.fixture(scope="module")
def geometry():
    instance = json.loads((v6.ROOT / v6.hero()["instance"]).read_text())
    return stage.build_geometry(instance, [])


def node_run(script, *args, stdin=None):
    assert NODE, "node is required to verify Studio modules"
    result = subprocess.run([NODE, "-e", script, *map(str, args)], input=stdin,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize("identity,order,title,icon,features", STEPS)
def test_registration_and_syntax(identity, order, title, icon, features):
    path = STATIC / f"studio-{identity}.js"
    subprocess.run([NODE, "--check", str(path)], check=True, capture_output=True, text=True)
    registered = json.loads(node_run("""
      const fs = require('fs'), vm = require('vm'), steps = [];
      const context = {window: {Studio: {step: step => steps.push(step)}}};
      vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
      process.stdout.write(JSON.stringify(steps.map(s => ({id:s.id, order:s.order,
        title:s.title, icon:s.icon, features:s.features, ship:s.ship, mount:typeof s.mount}))));
    """, path))
    assert registered == [dict(id=identity, order=order, title=title, icon=icon,
                              features=features, ship=True, mount="function")]


@pytest.mark.parametrize("identity,order,title,icon,features", STEPS)
def test_local_assets_and_token_scoped_styles(identity, order, title, icon, features):
    js = (STATIC / f"studio-{identity}.js").read_text()
    css = (STATIC / f"studio-{identity}.css").read_text()
    assert not re.search(r"https?://|//(?:cdn|fonts)\.|@import", js + css)
    assert f"st-{identity}-" in css
    # All module-owned selectors stay inside their owner's namespace. Shared
    # classes may qualify a scoped selector, and keyframe percentages aren't selectors.
    for selector in re.findall(r"([^{}]+)\{", css):
        selector = re.sub(r"/\*.*?\*/", "", selector, flags=re.S).strip()
        if not selector or selector.startswith("@") or re.fullmatch(r"[\d%,\s]+|from|to", selector):
            continue
        for part in selector.split(","):
            assert f".st-{identity}-" in part, part
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(", css)
    assert not re.search(r"['\"]#[0-9a-fA-F]{3,8}['\"]", js), "Canvas colours must come from tokens"
    # v3 allows actor dots and a light-on-dark night-viewport label; all
    # other styling continues to come from the shared components.
    banned = {"color", "background", "background-color", "border", "border-color",
              "border-radius", "outline", "box-shadow", "font", "font-family",
              "font-size", "font-weight", "letter-spacing", "text-transform"}
    for selector, block in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        declarations = re.findall(r"(?:^|;)\s*([\w-]+)\s*:", block)
        allowed = set()
        if "actor-dot" in selector or "voice-dot" in selector or "reader-dot" in selector:
            allowed = {"background", "border-radius"}
        elif ".st-chip.cloud::before" in selector:
            allowed = {"background"}
        elif ".st-instruct-ship-label" in selector:
            allowed = {"background", "color"}
        assert not (banned - allowed).intersection(declarations), selector
        for prop in allowed - {"border-radius"}:
            for value in re.findall(rf"(?:^|;)\s*{prop}\s*:\s*([^;]+)", block):
                assert "var(--st-" in value, "Use tokens for v3 colours"
    for shared in ("st-card", "st-btn", "st-disclosure", "st-meta"):
        assert shared in js
    assert "st-h" not in js and "st-sub" not in js


def test_real_payloads_have_the_displayed_evidence(payloads):
    d = payloads["intake"]
    assert d["mode"] == "recorded" and d["label"] == "Recorded answer"
    assert {doc["kind"] for doc in d["documents"]} >= {"photo", "email", "sheet"}
    assert all(doc["label"].startswith("Synthetic document") for doc in d["documents"])
    assert d["document_count"] == len(d["documents"])
    for row in d["rows"]:
        for column in d["columns"]:
            assert column in row["before"] and column in row["after"]
    for question in d["questions"]:
        assert question["text"] and question["id"] and "row" in question and "column" in question
        assert len([option for option in question["options"] if option["recorded"]]) == 1
        assert next(o["label"] for o in question["options"] if o["recorded"]) == question["after"]
    assert d["summary"]["table"]["scope"] and d["summary"]["table"]["rows"]
    voice, reading, xray = (payloads[name] for name in ("voice", "reading", "xray"))
    assert voice["transcript"] and voice["heard_by"] and "simulated radio" in voice["label"]
    assert reading["answer"]["constraint"] and reading["reader"]["label"]
    assert reading["evidence"]["adapter_sha256"] and reading["seconds"] > 0
    assert reading["seconds_note"] and set(reading["binding"]) == {
        "sentence_sha256", "rule_sha256", "instance_sha256"}
    assert xray["player_js"] == "/api/v6/asset/xray-player.js"
    assert xray["player_css"] == "/api/v6/asset/xray-player.css"
    assert xray["trace"]["steps"] and xray["source_label"]


def test_recorded_radio_has_the_displayed_waveform(payloads):
    voice = payloads["voice"]
    require_recorded_radio(voice)
    assert voice["audio"].startswith("/api/v6/asset/")
    assert voice["peaks"] and all(0 <= peak <= 1 for peak in voice["peaks"])


# Minimal DOM/event/audio surface for the module contract; no rendering engine.
# Real payloads flow through all buttons, including the seven grouped reviews.
FAKE_DOM = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const decode = s => s.replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&amp;/g,'&');
class Element {
  constructor(tag='div') { this.tagName=tag.toUpperCase(); this.children=[]; this.attributes={}; this.dataset={}; this.parentNode=null; this.style={setProperty(k,v){this[k]=v}}; this.listeners={}; this.hidden=false; this.disabled=false; this._text='';
    this.classList={ add:(...xs)=>{this.className=[...new Set([...this.className.split(/\s+/),...xs])].filter(Boolean).join(' ')},remove:(...xs)=>{this.className=this.className.split(/\s+/).filter(x=>!xs.includes(x)).join(' ')},contains:x=>this.className.split(/\s+/).includes(x),toggle:(x,on)=>{on=on===undefined?!this.classList.contains(x):on;on?this.classList.add(x):this.classList.remove(x);return on}};
  }
  get className(){return this.attributes.class||''} set className(v){this.attributes.class=v}
  get ownerDocument(){return document}
  get clientWidth(){return this.getBoundingClientRect().width} get clientHeight(){return this.getBoundingClientRect().height}
  getContext(){if(!this.context)this.context={canvas:this,paint:[],setTransform(){},clearRect(){this.paint=[];this.paths=0},beginPath(){},moveTo(){this.paths=(this.paths||0)+1},lineTo(){},closePath(){},fill(){this.paint.push({fill:this.fillStyle,alpha:this.globalAlpha})},stroke(){},fillText(){},measureText(s){return {width:s.length*7}}};return this.context}
  setAttribute(k,v){this.attributes[k]=String(v);if(k==='hidden')this.hidden=true;if(k==='disabled')this.disabled=true;if(k.startsWith('data-'))this.dataset[k.slice(5).replace(/-([a-z])/g,(_,x)=>x.toUpperCase())]=String(v)}
  getAttribute(k){return this.attributes[k]??null} removeAttribute(k){delete this.attributes[k];if(k==='hidden')this.hidden=false;if(k==='disabled')this.disabled=false}
  appendChild(e){if(e.parentNode)e.remove();this.children.push(e);e.parentNode=this;return e} append(...els){els.forEach(e=>this.appendChild(typeof e==='string'?Object.assign(new Element('#text'),{_text:e}):e))}
  replaceChildren(...els){this.children.forEach(e=>e.parentNode=null);this.children=[];this._text='';this.append(...els)} remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(x=>x!==this);this.parentNode=null}
  get firstElementChild(){return this.children.find(e=>e.tagName!=='#TEXT')||null}
  get textContent(){return this._text+this.children.map(c=>c.textContent).join('')} set textContent(v){this.replaceChildren();this._text=String(v)}
  get innerHTML(){return this._html||''} set innerHTML(html){this.replaceChildren();this._html=html;const stack=[this],re=/<!--[\s\S]*?-->|<\/?([a-zA-Z][\w-]*)([^>]*?)\/?>|([^<]+)/g;let m;while((m=re.exec(html))){if(m[3]){stack[stack.length-1].appendChild(Object.assign(new Element('#text'),{_text:decode(m[3])}));continue}if(!m[1])continue;if(m[0][1]==='/'){if(stack.length>1)stack.pop();continue}const e=new Element(m[1]);const attrs=/([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;let a;while((a=attrs.exec(m[2])))e.setAttribute(a[1],decode(a[2]??a[3]??a[4]??''));stack[stack.length-1].appendChild(e);if(!/^(img|input|br|hr|meta|link|source)$/i.test(m[1]))stack.push(e)}}
  matches(selector){const parts=selector.trim().split(/\s+(?![^[]*\])/);const simple=(e,s)=>{const attrs=[...s.matchAll(/\[([\w-]+)(?:=["']?([^\]"']*)["']?)?\]/g)];if(attrs.some(m=>e.getAttribute(m[1])===null||(m[2]!==undefined&&e.getAttribute(m[1])!==m[2])))return false;s=s.replace(/\[[^\]]*\]/g,'');const tag=s.match(/^[\w-]+/);if(tag&&e.tagName!==tag[0].toUpperCase())return false;return [...s.matchAll(/\.([\w-]+)/g)].every(m=>e.classList.contains(m[1]))&&[...s.matchAll(/#([\w-]+)/g)].every(m=>e.getAttribute('id')===m[1])};let el=this;if(!simple(el,parts.pop()))return false;while(parts.length){let p=parts.pop();do{el=el.parentNode}while(el&&!simple(el,p));if(!el)return false}return true}
  querySelectorAll(sel){let out=[];for(const c of this.children){if(sel.split(',').some(s=>c.matches(s)))out.push(c);out.push(...c.querySelectorAll(sel))}return out}
  querySelector(sel){return this.querySelectorAll(sel)[0]||null} closest(sel){for(let e=this;e;e=e.parentNode)if(e.matches(sel))return e;return null}
  addEventListener(k,f){(this.listeners[k]??=[]).push(f)} removeEventListener(k,f){this.listeners[k]=(this.listeners[k]||[]).filter(x=>x!==f)}
  async click(){if(this.disabled)return;const e={target:this,currentTarget:this,preventDefault(){},stopPropagation(){}};for(let node=this;node;node=node.parentNode){e.currentTarget=node;if(node.onclick)await node.onclick(e);for(const f of node.listeners.click||[])await f(e)}}
  scrollIntoView(){} getBoundingClientRect(){return this.rect||{width:800,height:600,top:0,left:0,right:800,bottom:600}} focus(){}
}
const document={createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),head:new Element('head'),body:new Element('body'),getElementById(id){return this.body.querySelector('#'+id)},querySelector(sel){return this.head.querySelector(sel)||this.body.querySelector(sel)},querySelectorAll(sel){return [...this.head.querySelectorAll(sel),...this.body.querySelectorAll(sel)]}};
class Audio {constructor(src){this.src=src;this.currentTime=0;this.duration=3;this.paused=true;this.listeners={}}addEventListener(k,f){(this.listeners[k]??=[]).push(f)}removeEventListener(){}pause(){this.paused=true}load(){}async play(){this.paused=false;queueMicrotask(()=>{this.currentTime=this.duration;this.paused=true;for(const f of this.listeners.ended||[])f();if(this.onended)this.onended()})}}
let clock=0;const steps=[];const playerCalls=[];
const frameQueue=new Map();let nextFrame=1;
const raf=callback=>{const id=nextFrame++;frameQueue.set(id,callback);return id};
const flushFrames=()=>{const pending=[...frameQueue.values()];frameQueue.clear();pending.forEach(callback=>callback(clock))};
let theme='light';
const computedStyle=()=>({display:'block',visibility:'visible',backgroundColor:'rgb(11, 18, 32)',
  getPropertyValue:name=>({'--st-red':theme==='light'?'#d93025':'#ff453a',
    '--st-orange':theme==='light'?'#e8890c':'#ff9f0a','--st-white':'#ffffff',
    '--st-text':theme==='light'?'#111418':'#ececec'}[name]||'')});
class ResizeObserver {constructor(callback){this.callback=callback}observe(){}disconnect(){}}
const window={devicePixelRatio:1,getComputedStyle:computedStyle,Studio:{step:s=>steps.push(s)},addEventListener(){},removeEventListener(){},setTimeout,clearTimeout};
const sandbox={window,document,Audio,console,performance:{now:()=>clock+=250},requestAnimationFrame:raf,cancelAnimationFrame:id=>frameQueue.delete(id),ResizeObserver,getComputedStyle:computedStyle,setTimeout,clearTimeout,queueMicrotask,Promise,URL,addEventListener(){},removeEventListener(){}};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), sandbox);
const realPlayer = window.StowLMXray;
window.StowLMXray = {mount: (root, trace, options) => {
  playerCalls.push(trace);
  return realPlayer.mount(root, trace, options);
}};
for(const name of ['intake','instruct'])vm.runInContext(fs.readFileSync(process.argv[1]+'/studio-'+name+'.js','utf8'),sandbox);
async function main() {
  const payloads = JSON.parse(fs.readFileSync(0, 'utf8'));
  theme=payloads.theme||'light';
  const stageMode = process.argv[2];
  const shipWrap=new Element('div');shipWrap.setAttribute('id','ship-wrap');
  const shipCanvas=new Element('canvas');shipCanvas.setAttribute('id','ship-canvas');
  shipWrap.appendChild(shipCanvas);document.body.appendChild(shipWrap);
  for (const step of steps) {
    if(step.id!==payloads.workflow)continue;
    const panel = new Element('section');
    document.body.appendChild(panel);
    const events = [], logs = [], posts = [], clicks = [], morphs = [], canvases = [];
    let done = 0, waited = 0, center = null, centersAfterConfirm = 0;
    const project = (x,y,z) => [x*2+200,300-y-z];
    const scene = {slotList: () => payloads.geometry.slots, project,
      shipExtOfSlot(id) {
        const slot=payloads.geometry.slots.find(slot=>String(slot.id)===String(id));
        return slot?{x0:slot.x_m-3.048,x1:slot.x_m+3.048,y0:slot.y_m-1.219,
          y1:slot.y_m+1.219,z0:slot.z_m,z1:slot.z_m+2.591}:null;
      },
      kit:{poly(ctx,points){ctx.beginPath();points.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.closePath()},
        faces(e){return {top:[project(e.x0,e.y0,e.z1),project(e.x1,e.y0,e.z1),project(e.x1,e.y1,e.z1),project(e.x0,e.y1,e.z1)],
          front:[project(e.x0,e.y0,e.z0),project(e.x1,e.y0,e.z0),project(e.x1,e.y0,e.z1),project(e.x0,e.y0,e.z1)],
          end:[project(e.x1,e.y0,e.z0),project(e.x1,e.y1,e.z0),project(e.x1,e.y1,e.z1),project(e.x1,e.y0,e.z1)]}}}};
    if (stageMode === 'geometry') scene.setMorph = frame => morphs.push(frame);
    const shipState = {ship: new Map(), buffer: [], coversOff: new Set()};
    const ship = {scene, S: {state: shipState}, B: {geometry: payloads.geometry},
      setPlaying() {}, redraw() {}};
    window.__stage = stageMode === 'absent' ? null : ship;
    const ctx = {
      state: {}, stage: window.__stage, demo: true,
      esc: s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
      chip: (s, k) => `<span class="st-chip ${k}">${s}</span>`,
      asset: name => '/api/v6/asset/' + name, api: async n => payloads[n],
      data: async name => { assert.equal(name, 'evidence'); return payloads.evidence; },
      center: n => { center = n; if (n) canvases.push(n); if (posts.length) centersAfterConfirm++; },
      log: row => logs.push(row), done: () => done++,
      emit: (...args) => events.push(args), on() {}, toast() {},
      wait: async ms => { waited += ms; clock += ms; },
      click: async el => {
        assert(el && typeof el !== 'string', 'autoplay must target a mounted element');
        assert(!el.hidden && !el.disabled, 'autoplay clicked unavailable button');
        clicks.push(el);
        if (step.id === 'intake' && el.getAttribute('data-option') !== null) {
          const alternate = el.parentNode.querySelector('.ghost');
          if (alternate) {
            await alternate.click();
            assert.equal(done, 0, 'unrecorded answer completed the load list');
          }
        }
        await el.click();
      },
      post: async (name, body) => {
        if (stageMode === 'geometry')
          shipCanvas.rect={width:0,height:0,top:0,left:0,right:0,bottom:0};
        posts.push({name, body});
        return payloads.confirm;
      },
    };
    const view = step.mount(panel, payloads, ctx);
    for (const method of ['enter', 'leave', 'start', 'autoplay'])
      assert.equal(typeof view[method], 'function', step.id + '.' + method);
    assert.equal(panel.querySelector('h1,h2'), null, 'step headings belong to the shell');
    const details = panel.querySelector('details.st-disclosure');
    assert(details && details.querySelector('summary').textContent === 'Details');
    await view.enter();
    // Shell starts the turn once; a repeated start while in flight must be harmless.
    await Promise.all([view.start(), view.start()]);
    if (step.id === 'intake') {
      assert.equal(logs.filter(row => row.who === 'Muse Spark').length, 1);
      const first = panel.querySelector('.st-intake-import');
      assert.equal(first,null,'start() replaces the duplicate import control');
    } else {
      assert.equal(logs.length, 0, 'radio start must wait for the planner to play');
      assert.equal(posts.length, 0);
      if (payloads.voice.role !== 'hero') {
        const radio = panel.querySelector('.st-instruct-radio');
        assert(radio.textContent.includes('Separate demos'));
        assert(radio.textContent.includes(payloads.voice.note));
      }
      const adapter = payloads.reading.evidence.adapter_sha256.slice(0, 12);
      assert(details.textContent.includes(adapter), 'adapter belongs inside Details');
    }
    await view.autoplay(ctx);
    await new Promise(resolve => setImmediate(resolve));
    flushFrames();
    // Confirmation can arrive while the user has closed the ship canvas.
    // A later shell resize/reopen must place the marker using fresh projection.
    shipCanvas.rect=null;
    flushFrames();
    assert.equal(done, 1, step.id + ' must complete once');
    assert(waited >= 15000 && waited <= 25000, 'autoplay outside the requested take length');
    assert(logs.length > 0, 'No model feed lines');
    assert(!panel.textContent.includes('undefined'), 'Missing payload value rendered');
    if (step.id === 'intake') {
      assert(events.some(e => e[0] === 'loadlist:ready'));
      assert.deepEqual(ctx.state.loadlist, payloads.intake.rows.map(row => row.after));
      const ready = panel.querySelector('.st-intake-ready');
      assert(ready && ready.classList.contains('st-card') && !ready.hidden);
      assert(ready.textContent.includes(`Demo load list ready · ${payloads.intake.rows.length} boxes (separate demo)`));
      assert(panel.querySelector('.st-intake-provenance').textContent.includes(
        'Separate demos: 12-box demo voyage; the plan is the 87-box hero call'));
      const figures = panel.querySelector('.st-intake-figures');
      assert(figures && !figures.closest('details'), 'narrated intake workload must be on the turn face');
      const stopping = payloads.intake.summary.table.rows.find(row => row[0] === 'Boxes stopping');
      for (const value of stopping) assert(figures.textContent.includes(value));
      assert.equal(logs.filter(row => row.who === 'Muse Spark').length, 1);
      assert(!clicks.some(el => el.classList.contains('st-intake-import')));
      for (const [y, row] of payloads.intake.rows.entries())
        for (const column of payloads.intake.columns)
          {
            const cell=panel.querySelector(`[data-row="${y}"] [data-column="${column}"]`);
            assert.equal(cell.textContent,row.after[column]);
            assert(cell.title.includes(row.after[column]), 'truncated cells need their full value');
          }
      const papers=canvases.find(node=>node.classList.contains('st-intake-desk'));
      assert(papers.querySelector('.st-intake-paper-photo .st-chip').textContent==='Synthetic · generated picture');
    } else {
      assert.equal(posts.length, 1);
      assert.equal(posts[0].name, 'confirm');
      assert.deepEqual(posts[0].body, payloads.reading.binding);
      assert.equal(ctx.state.confirm.token, payloads.confirm.token);
      assert.deepEqual(playerCalls.slice(-1), [payloads.xray.trace]);
      const readerCard = payloads.evidence.cards.find(card => card.id === 'reader');
      for(const text of [payloads.voice.label,payloads.reading.reader.label]) {
        assert(panel.querySelectorAll('.st-meta').some(node=>node.textContent.includes(text)), 'long provenance belongs in a meta line');
        assert(!panel.querySelectorAll('.st-chip').some(node=>node.textContent.includes(text)), 'long provenance must not wrap inside a pill');
      }
      const ruleCard = panel.querySelector('.st-instruct-rule');
      const readerEvidence = ruleCard.querySelector('.st-instruct-reader-evidence');
      assert(readerEvidence && !readerEvidence.hidden && !readerEvidence.closest('details'));
      for (const row of readerCard.series.slice(0, 2))
        assert(ruleCard.textContent.includes(`${row.value} of ${row.n}`), 'locked-test counts missing');
      assert(ruleCard.textContent.includes(readerCard.does_not), 'stricter gates must accompany reader counts');
      const xrayCanvas = canvases.find(node => node.classList.contains('st-instruct-inside'));
      const maskCard = payloads.evidence.cards.find(card => card.id === 'mask');
      for (const row of maskCard.series)
        assert(xrayCanvas.textContent.includes(`${row.value} of ${row.n}`), 'mask-study count missing');
      assert(xrayCanvas.textContent.includes(maskCard.label));
      assert(xrayCanvas.textContent.includes(maskCard.show));
      assert(xrayCanvas.textContent.includes(maskCard.does_not));
      assert(xrayCanvas.textContent.includes(
        `In this recorded reading the mask overrode the model's first choice on ${payloads.xray.blocked} of ${payloads.xray.tokens} next words`));
      assert(xrayCanvas.querySelector('.xray-panel').classList.contains('st-card'));
      assert(xrayCanvas.querySelector('.xray-play').classList.contains('st-btn'));
      assert.equal(xrayCanvas.querySelector('.xray-block-button'),null,'unused mask control must be removed');
      for(const name of ['voice','reader'])
        assert(panel.querySelector(`.st-instruct-${name}-dot`), 'missing actor dot');
      for (const node of xrayCanvas.querySelectorAll(
        '.xray-count,.xray-reason,.xray-decision,.xray-legend-blocked,.xray-empty,.xray-block-button'))
        if (!node.hidden) assert(!/\bblocked\b/i.test(node.textContent), 'legacy mask claim rendered');
      for (const node of xrayCanvas.querySelectorAll('.xray-reason'))
        assert(!/overrode/i.test(node.textContent), 'a forbidden alternative is not an overridden first choice');
      assert.equal(document.head.querySelector('link'), null, 'legacy player theme must not override v2');
      assert(events.some(e => e[0] === 'rule:confirmed' && e[1].bay===payloads.reading.answer.constraint.bay), 'shell needs the confirmed bay');
      assert.equal(centersAfterConfirm, 0, 'confirmation must leave canvas ownership to shell');
      const bay = payloads.reading.answer.constraint.bay;
      if (stageMode === 'geometry') {
        const marked = morphs.filter(Boolean);
        assert(marked.length, 'confirmation must mark ship geometry');
        assert.equal(marked[0].marks.length,0,'legacy glow must not compound the projected 45% tint');
        for (const mark of marked[0].marks) {
          const slot = payloads.geometry.slots.find(slot => String(slot.id) === String(mark.slot));
          assert(slot && Number(slot.bay) === bay, 'marked a bay other than the confirmed one');
        }
        const tint=document.querySelector('.st-instruct-ship-tint');
        assert(tint && tint.dataset.bay===String(bay));
        const baySlots=payloads.geometry.slots.filter(slot=>Number(slot.bay)===bay);
        assert.equal(Number(tint.dataset.slotCount),baySlots.length);
        assert.equal(tint.getContext('2d').paths,baySlots.length*3,'paint all three visible faces of every bay slot');
        assert(tint.getContext('2d').paint.some(fill=>fill.alpha===0.45), 'bay tint must be painted at 45% opacity');
        const label=document.querySelector('.st-instruct-ship-label');
        assert(label && label.textContent===`Bay ${bay} · closed`);
        assert(label.style.left && label.style.top, 'label must be pinned to a projected point');
        assert.equal(label.style['--st-instruct-ship-label-bg'],'rgb(11, 18, 32)', 'label follows the night viewport in both themes');
      } else {
        // Semantic strike-through makes the fallback readable without module-specific colours.
        const struck = panel.querySelector('s,del');
        assert(struck && Number(struck.textContent) === bay, 'fallback must strike the confirmed bay');
      }
    }
    const completedLogs = logs.length;
    await view.start();
    assert.equal(logs.length, completedLogs, 'start after completion must not repeat work');
    await view.leave();
    if (step.id === 'instruct' && stageMode === 'geometry')
      assert.equal(morphs[morphs.length - 1], null, 'release the bay marker before the next step');
    assert.equal(document.querySelector('.st-instruct-ship-marker'),null,'release the canvas overlay before the next step');
    console.log(JSON.stringify({id: step.id, done, waited, logs: logs.map(row => row.who),
      events: events.map(e => e[0])}));
  }
}
main().catch(e=>{console.error(e);process.exitCode=1});
"""


@pytest.mark.parametrize("stage_mode", ["geometry", "fallback", "absent"])
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("workflow", ["intake", "instruct"], ids=["cargo", "recorded-radio"])
def test_real_payload_mount_and_hands_free_workflow(payloads, geometry, evidence, stage_mode, theme, workflow):
    if workflow == "instruct":
        require_recorded_radio(payloads["voice"])
    data = dict(payloads, geometry=geometry, evidence=evidence, theme=theme, workflow=workflow)
    # Exercise the real confirmation gate in an isolated in-memory registry.
    data["confirm"] = v6.Confirmations().confirm(data["reading"]["binding"])
    output = node_run(FAKE_DOM, STATIC, stage_mode, v6.ASSETS["xray-player.js"][0], stdin=json.dumps(data))
    results = [json.loads(line) for line in output.splitlines()]
    assert [result["id"] for result in results] == [workflow]
    assert results[0]["events"] == (["loadlist:ready"] if workflow == "intake" else
                                     ["rule:read", "rule:confirmed"])
    assert results[0]["logs"] == (["Muse Spark", "Planner"] if workflow == "intake" else
                                   ["Muse Voice Transcribe", "StowLM Reader", "StowLM Reader", "Planner"])


def test_illustrative_radio_is_labelled_separate(payloads, geometry, evidence):
    require_recorded_radio(payloads["voice"])
    data = dict(payloads, geometry=geometry, evidence=evidence, workflow="instruct")
    data["voice"] = dict(payloads["voice"], role="illustrative",
                         note=v6.hero()["stand_ins"]["radio"])
    data["confirm"] = v6.Confirmations().confirm(data["reading"]["binding"])
    node_run(FAKE_DOM, STATIC, "geometry", v6.ASSETS["xray-player.js"][0], stdin=json.dumps(data))


def test_illustrative_radio_label_without_recorded_audio(payloads, evidence):
    """Synthetic label-only fixture; makes no claim about recorded playback."""
    data = dict(payloads, evidence=evidence)
    data["voice"] = dict(payloads["voice"], role="illustrative",
                         note="Synthetic fixture: separate radio demonstration",
                         audio=None, seconds=None, peaks=[], audio_note=v6.AUDIO_ABSENT)
    script = FAKE_DOM.split("async function main() {")[0] + r"""
    async function main() {
      const data=JSON.parse(fs.readFileSync(0,'utf8')),panel=new Element('section');
      const ctx={esc:s=>String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'),
        data:async()=>data.evidence,chip:(text,kind)=>`<span class="st-chip ${kind}">${text}</span>`};
      const view=steps.find(step=>step.id==='instruct').mount(panel,data,ctx);
      await view.enter();await view.start();
      const radio=panel.querySelector('.st-instruct-radio');
      assert(!radio.hidden);
      assert(radio.textContent.includes('Separate demos'));
      assert(radio.textContent.includes(data.voice.note));
      assert(radio.textContent.includes(data.voice.audio_note));
      assert(panel.querySelector('.st-instruct-transcript').textContent.trim());
      assert(panel.querySelector('.st-instruct-actor-meta').textContent.includes(data.voice.heard_by));
      assert.equal(panel.querySelectorAll('.st-instruct-wave progress').length,0);
      await view.leave();
    }
    main().catch(error=>{console.error(error);process.exitCode=1});
    """
    node_run(script, STATIC, "absent", v6.ASSETS["xray-player.js"][0], stdin=json.dumps(data))

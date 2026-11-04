"""Planner Cargo/Instructions: real recordings, fake 2D UI, no browser."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import stage, v6

STATIC = Path(v6.__file__).with_name("static")
NODE = shutil.which("node")


def node_run(script, *args, stdin=None):
    assert NODE, "node is required"
    result = subprocess.run([NODE, "-e", script, *map(str, args)], input=stdin,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def payloads():
    return {name: getattr(v6, name)() for name in ("intake", "voice", "reading", "xray")}


def require_recorded_radio(voice):
    """Only the deliberately withheld configured take may skip playback checks."""
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
def planner_data():
    """Use generated data as soon as pl-data lands; derive fixtures meanwhile."""
    root = STATIC / "planner-data"
    if all((root / name).is_file() for name in ("ship.json", "boxes.json")):
        return {name: json.loads((root / f"{name}.json").read_text()) for name in ("ship", "boxes")}
    instance = json.loads((v6.ROOT / v6.hero()["instance"]).read_text())
    geometry = stage.build_geometry(instance, [])
    bays = sorted({int(slot["bay"]) for slot in geometry["slots"]})
    return {"ship": {"ship": {"words": instance["vessel"]["name"],
                              "teu": instance["vessel"]["nominal_teu"],
                              "synthetic_label": "Synthetic ship"},
                      "bays": [{"bay": bay, "label": f"Bay {bay}", "position": i,
                                "length_ft": 20,
                                "rows": sorted({s["row"] for s in geometry["slots"] if s["bay"] == bay}),
                                "deck_tiers": sorted({s["tier"] for s in geometry["slots"] if s["bay"] == bay and s["on_deck"]}),
                                "hold_tiers": sorted({s["tier"] for s in geometry["slots"] if s["bay"] == bay and not s["on_deck"]}),
                                "hatch": True} for i, bay in enumerate(bays)],
                      "row_order": "port_to_starboard", "limits": {}, "source": {"path": v6.hero()["instance"]}},
            "boxes": {"boxes": {}, "pods": [], "source": {"path": v6.hero()["instance"]}}}


@pytest.mark.parametrize("identity,order,features", [("intake", 10, ["intake"]),
                                                    ("instruct", 20, ["voice", "reading", "xray"])])
def test_registration_syntax_and_2d_only(identity, order, features):
    path = STATIC / f"planner-{identity}.js"
    subprocess.run([NODE, "--check", str(path)], check=True, capture_output=True, text=True)
    result = json.loads(node_run("""
      const fs=require('fs'),vm=require('vm'),stages=[];
      vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{window:{Planner:{stage:s=>stages.push(s)}}});
      process.stdout.write(JSON.stringify(stages.map(s=>({id:s.id,order:s.order,features:s.features,mount:typeof s.mount}))));
    """, path))
    assert result == [dict(id=identity, order=order, features=features, mount="function")]
    source = path.read_text()
    assert not re.search(r"\bStudio\b|__stage|ctx\.center|ctx\.stage|ship-canvas|setMorph", source)
    assert not re.search(r"https?://|cdn\.", source)
    css = (STATIC / f"planner-{identity}.css").read_text()
    assert f".pl-{identity}-" in css and "var(--pl-" in css
    assert not re.search(r"https?://|@import|rgba\(255,\s*255,\s*255", css)
    for selector in re.findall(r"([^{}]+)\{", css):
        selector = re.sub(r"/\*.*?\*/", "", selector, flags=re.S).strip()
        if not selector or selector.startswith("@") or re.fullmatch(r"[\d%,\s]+|from|to", selector):
            continue
        for part in selector.split(","):
            assert f".pl-{identity}-" in part, part


def test_ship_data_supports_the_confirmed_bay(payloads, planner_data):
    ship = planner_data["ship"]
    assert ship["row_order"] == "port_to_starboard"
    assert payloads["reading"]["answer"]["constraint"]["bay"] in [row["bay"] for row in ship["bays"]]
    assert ship["source"] and planner_data["boxes"]["source"]


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
let clock=0;const stages=[], playerCalls=[];
const window={Planner:{stage:s=>stages.push(s)},addEventListener(){},removeEventListener(){},setTimeout,clearTimeout};
const sandbox={window,document,Audio,console,performance:{now:()=>clock+=250},
  requestAnimationFrame:()=>1,cancelAnimationFrame(){},setTimeout,clearTimeout,queueMicrotask,Promise,URL};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),sandbox);
vm.runInContext(fs.readFileSync(process.argv[1]+'/planner-loadlist.js','utf8'),sandbox);
const actualPlayer=window.StowLMXray;
window.StowLMXray={mount:(host,trace,options)=>{playerCalls.push(trace);return actualPlayer.mount(host,trace,options)}};
for(const name of ['intake','instruct'])
  vm.runInContext(fs.readFileSync(process.argv[1]+'/planner-'+name+'.js','utf8'),sandbox);
const visible=node=>{for(let el=node;el;el=el.parentNode)if(el.hidden)return false;return true};
const visibleText=node=>!visible(node)?'':node._text+node.children.map(visibleText).join('');
async function main() {
  const payloads=JSON.parse(fs.readFileSync(0,'utf8'));
  const state={},bayCalls=[];
  const validBays=new Set(payloads.planner.ship.bays.map(row=>Number(row.bay)));
  window.BayPlan={create:(host,options)=>({
    closeBay(bay,label){assert(validBays.has(Number(bay)));bayCalls.push(['closeBay',bay,label])},
    focusBay(bay){assert(validBays.has(Number(bay)));bayCalls.push(['focusBay',bay])},
    showBays(){},highlight(){},on(){},setPlan(){},
  })};
  const bay=window.BayPlan.create(new Element('svg'),{ship:payloads.planner.ship,
    boxes:payloads.planner.boxes.boxes,pods:payloads.planner.boxes.pods});
  for(const stage of stages) {
    if(stage.id!==payloads.workflow)continue;
    const panel=new Element('section'),sheetHost=new Element('section'),loadHost=new Element('section');
    document.body.append(panel,sheetHost,loadHost);
    const actualList=window.PlannerLoadList.create(loadHost);
    actualList.setData(payloads.planner.boxes);
    const logs=[],events=[],posts=[],sheets=[],rowUpdates=[],actions=[],order=[];
    let done=0,waited=0,rows=[];
    const beforeBayCalls=bayCalls.length;
    const ctx={state,bay,demo:true,
      esc:s=>String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'),
      chip:(text,kind)=>`<span class="pl-chip ${kind||''}">${text}</span>`,
      asset:name=>'/api/v6/asset/'+name,
      api:async name=>payloads[name],
      data:async name=>name==='evidence'?payloads.evidence:payloads.planner[name],
      post:async(name,body)=>{posts.push({name,body});order.push('post');return payloads.confirm},
      log:entry=>logs.push(entry),done:()=>{done++;order.push('done')},
      emit:(name,value)=>events.push([name,value]),on(){},next(){},toast(){},meters(){},
      loadlist:{setRows(value,meta){assert(Array.isArray(value));rows=value;actualList.setRows(value,meta);
        rowUpdates.push(JSON.parse(JSON.stringify(value)));},mark(){},focus(){}},
      sheet:node=>{sheetHost.replaceChildren();if(node){sheets.push(node);sheetHost.appendChild(node)}},
      wait:async ms=>{clock+=ms;waited+=ms},
      click:async node=>{
        assert(node && typeof node!=='string');assert(visible(node)&&!node.disabled,'autoplay clicked unavailable action');
        actions.push(node.dataset.action||node.textContent);
        if(stage.id==='instruct'&&node.dataset.action==='confirm') {
          const reader=payloads.evidence.cards.find(card=>card.id==='reader');
          assert(visibleText(panel).includes(reader.does_not),'show the reader gate before the planner confirms');
        }
        if(stage.id==='intake'&&node.dataset.action==='answer') {
          const alternate=node.parentNode.querySelectorAll('[data-option]').find(el=>el!==node);
          if(alternate){await alternate.click();assert.equal(done,0,'unrecorded answer completed intake')}
        }
        await node.click();
      },
    };
    Object.defineProperties(ctx,{stage:{get(){throw new Error('No 3D stage in Planner')}},
      center:{get(){throw new Error('Planner uses sheets')}}});
    const view=stage.mount(panel,payloads,ctx);
    for(const name of ['enter','leave','start','autoplay'])assert.equal(typeof view[name],'function');
    await view.enter();
    if(stage.id==='intake') {
      const importButton=panel.querySelector('[data-action="import"]');
      assert(visible(importButton));assert.equal(importButton.textContent,'Import cargo papers');
      assert.notEqual(importButton.getAttribute('data-primary'),null,'shell must recognize the module primary action');
      assert(!panel.querySelector('.pl-intake-measured table'),'comparison table belongs in Details');
      assert(!visibleText(panel).includes(payloads.intake.summary.scope),'comparison notes belong in Details');
      const comparison=panel.querySelector('.pl-intake-comparison-summary');
      assert(comparison&&visible(comparison),'keep one comparison summary line on Cargo');
      for(const value of payloads.intake.summary.table.rows[0])assert(comparison.textContent.includes(String(value)));
      const inspect=panel.querySelector('[data-action="documents"]');
      await inspect.click();assert(sheetHost.firstElementChild,'documents must be accessible before import');
      await sheetHost.querySelector('[data-action="close"]').click();
    } else {
      assert(visible(panel.querySelector('[aria-label="Type an instruction"]')));
    }
    await Promise.all([view.start(),view.start()]);
    if(stage.id==='intake') {
      assert.equal(logs.filter(entry=>entry.who==='Muse Spark').length,1);
      assert(rowUpdates.some(value=>value.length),'start must fill the left load list');
      assert(!panel.querySelector('[data-action="import"]')||!visible(panel.querySelector('[data-action="import"]')));
    } else {
      assert.equal(logs.length,0,'radio waits for the planner to play');
      assert.equal(posts.length,0);
      assert(visible(panel.querySelector('[data-action="play"]')));
    }
    await view.autoplay(ctx);
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(done,1,'stage must complete once after its workflow');
    assert(waited>=15000&&waited<=25000,'autoplay take outside requested duration');
    assert(!panel.textContent.includes('undefined'));
    if(stage.id==='intake') {
      assert.equal(rows.length,payloads.intake.rows.length);
      const shownRows=loadHost.querySelectorAll('tr[data-box]');
      assert.equal(shownRows.length,rows.length);
      assert(shownRows.every(node=>/sample/i.test(node.querySelector('.pl-st-dot').getAttribute('title'))),'each actual load-list row needs its sample provenance label');
      assert(/Separate demos/.test(loadHost.textContent),'the separate voyage scope must remain on the left-list face');
      assert(rowUpdates.some(value=>value.some(row=>row.review_status==='question')),'uncertain rows need review status');
      assert(panel.textContent.includes('Separate demos: 12-box demo voyage; the plan is the 87-box hero call'));
      assert(!panel.textContent.includes(payloads.intake.list_scope),'show the separate-voyage scope once');
      assert(panel.textContent.includes(`Demo load list ready · ${rows.length} boxes (separate demo)`));
      const toggle=panel.querySelector('[data-action="loadlist"]'),table=panel.querySelector('#pl-intake-sample-table');
      assert(visible(toggle));assert(!visible(table));
      await toggle.click();assert(visible(table));assert.equal(toggle.getAttribute('aria-expanded'),'true');
      const inlineRows=table.querySelectorAll('tr[data-load-row]');
      assert.equal(inlineRows.length,rows.length);
      assert(inlineRows.every(node=>node.textContent.includes('sample')&&node.textContent.includes('ready')));
      for(const row of rows) {
        const shown=inlineRows.find(node=>node.dataset.loadRow===row.box_id);
        assert(shown&&shown.textContent.includes(row.pod));
        assert(shown.querySelectorAll('td').some(cell=>(cell.getAttribute('title')||'').includes(row.iso)));
      }
      await toggle.click();assert(!visible(table));assert.equal(toggle.textContent,'View load list');
      assert(!loadHost.textContent.includes('Separate sample voyage ·'),'demo-list note must be one sentence');
      assert.equal(bayCalls.length,beforeBayCalls,'sample intake must never mutate the hero bay plan');
      assert(!actions.includes('import'),'autoplay must not repeat start');
      for(const [index,row] of rows.entries()) {
        const source=payloads.intake.rows[index];
        assert.equal(row.id,source.id);assert.equal(row.box_id,source.id);
        assert.deepEqual(row.after,source.after);assert.equal(row.review_status,'ready');assert(/sample/i.test(row.status));
        assert.equal(row.separate_demo,true);assert(/separate/i.test(row.label));
        assert.equal(row.dg_class,null,'UN numbers are not IMDG classes');
        assert.equal(row.weight_t,Number(source.after.mass.replace(/,/g,''))/1000);
      }
      assert.deepEqual(state.loadlist,rows);
      assert(events.some(([name])=>name==='loadlist:ready'));
      const docSheet=sheets.find(node=>node.classList.contains('pl-intake-sheet'));
      assert(docSheet&&docSheet.querySelector('img').getAttribute('src').startsWith('/api/v6/asset/'));
      assert(docSheet.textContent.includes('Synthetic · generated picture'));
      assert(!panel.querySelector('.pl-intake-measured table'),'comparison must stay out of the narrow Cargo section');
      assert.equal(logs.filter(entry=>entry.who==='Muse Spark').length,1);
    } else {
      assert.equal(posts.length,1);assert.equal(posts[0].name,'confirm');
      assert.deepEqual(posts[0].body,payloads.reading.binding);
      assert.deepEqual(state.confirm,payloads.confirm);
      const closed=payloads.reading.answer.constraint.bay;
      assert.deepEqual(bayCalls.slice(beforeBayCalls),[['closeBay',closed,`Bay ${closed} · closed`],['focusBay',closed]]);
      assert(events.some(([name,value])=>name==='rule:confirmed'&&value.bay===closed&&value.token===payloads.confirm.token));
      const inside=sheets.find(node=>node.classList.contains('pl-instruct-inside'));
      assert(inside,'Look inside must open a sheet');
      assert.deepEqual(playerCalls.slice(-1),[payloads.xray.trace]);
      for(const id of ['reader','mask']) {
        const evidence=payloads.evidence.cards.find(card=>card.id===id);
        const shown=inside.querySelector(`.pl-instruct-${id}-evidence`);
        assert(shown,'missing '+id+' evidence');
        for(const row of (id==='reader'?evidence.series.slice(0,2):evidence.series))
          assert(shown.textContent.includes(`${row.value} of ${row.n}`));
        assert(shown.textContent.includes(evidence.label));
        assert(shown.textContent.includes(evidence.does_not));
      }
      const reader=payloads.evidence.cards.find(card=>card.id==='reader');
      assert(!visibleText(panel).includes(reader.does_not),'reader gate belongs in Details once confirmed');
      assert(!visibleText(panel).includes(payloads.reading.scope_line),'long scope notes belong in Details once confirmed');
      assert(inside.textContent.includes(`the mask overrode the model's first choice on ${payloads.xray.blocked} of ${payloads.xray.tokens} next words`));
      assert.equal(document.head.querySelector('link'),null,'do not inject the legacy player theme');
      assert.equal(inside.querySelector('.xray-block-button'),null,'remove the misleading mask jump action');
      for(const node of inside.querySelectorAll('.xray-count,.xray-reason,.xray-decision,.xray-legend-blocked'))
        assert(!/\bblocked\b/i.test(node.textContent));
      for(const node of inside.querySelectorAll('.xray-reason'))
        assert(!/overrode/i.test(node.textContent),'forbidden alternatives are not first-choice overrides');
      if(payloads.voice.role!=='hero') {
        assert(panel.querySelector('.pl-instruct-radio').textContent.includes('Separate demos'));
        assert(panel.querySelector('.pl-instruct-radio').textContent.includes(payloads.voice.note));
      }
      assert.equal(order[order.length-1],'done');
    }
    // Hashes and internals are one click away in a separate sheet.
    const detailsAction=panel.querySelector('[data-action="details"]');
    assert(detailsAction&&visible(detailsAction),'Details must remain reachable');
    await detailsAction.click();
    await new Promise(resolve=>setImmediate(resolve));
    assert(sheetHost.firstElementChild,'Details must open a sheet');
    assert(sheetHost.textContent.includes(stage.id==='intake'?payloads.intake.model:payloads.reading.evidence.adapter_sha256));
    if(stage.id==='intake') {
      const measured=sheetHost.querySelector('.pl-intake-measured');
      assert(measured&&measured.querySelector('table'),'the full comparison table must remain accessible in Details');
      for(const row of payloads.intake.summary.table.rows)
        for(const value of row)assert(measured.textContent.includes(String(value)));
      assert(measured.textContent.includes(payloads.intake.summary.scope));
      assert(measured.textContent.includes(payloads.intake.summary.table.scope));
    } else {
      const reader=payloads.evidence.cards.find(card=>card.id==='reader');
      assert(sheetHost.textContent.includes(reader.does_not),'reader gate remains accessible in Details');
      assert(sheetHost.textContent.includes(payloads.reading.scope_line));
    }
    const close=sheetHost.querySelector('[data-action="close"],[data-action="close-details"]');
    assert(close);await close.click();assert.equal(sheetHost.firstElementChild,null);
    const completeLogs=logs.length;
    await view.start();assert.equal(logs.length,completeLogs);
    await view.leave();assert.equal(sheetHost.firstElementChild,null);
    console.log(JSON.stringify({id:stage.id,done,waited,logs:logs.map(entry=>entry.who),events:events.map(([name])=>name)}));
  }
}
main().catch(error=>{console.error(error);process.exitCode=1});
"""


@pytest.mark.parametrize("workflow", ["intake", "instruct"], ids=["cargo", "recorded-radio"])
@pytest.mark.parametrize("separate_radio", [False, True])
def test_real_payload_workflow(payloads, planner_data, separate_radio, workflow):
    if workflow == "instruct":
        require_recorded_radio(payloads["voice"])
    data = dict(payloads, planner=planner_data,
                evidence=json.loads((STATIC / "studio-data/evidence.json").read_text()),
                workflow=workflow)
    if separate_radio:
        data["voice"] = dict(payloads["voice"], role="illustrative", note=v6.hero()["stand_ins"]["radio"])
    data["confirm"] = v6.Confirmations().confirm(data["reading"]["binding"])
    result = [json.loads(line) for line in node_run(
        FAKE_DOM, STATIC, v6.ASSETS["xray-player.js"][0], stdin=json.dumps(data)).splitlines()]
    assert [row["id"] for row in result] == [workflow]
    assert result[0]["events"] == (["loadlist:ready"] if workflow == "intake" else
                                    ["rule:read", "rule:confirmed"])
    assert result[0]["logs"] == (["Muse Spark", "Planner"] if workflow == "intake" else
                                  ["Muse Voice Transcribe", "StowLM Reader", "StowLM Reader", "Planner"])


def test_illustrative_radio_label_without_recorded_audio(payloads):
    """Synthetic label-only fixture; makes no claim about recorded playback."""
    data = dict(payloads, evidence=json.loads((STATIC / "studio-data/evidence.json").read_text()))
    data["voice"] = dict(payloads["voice"], role="illustrative",
                         note="Synthetic fixture: separate radio demonstration",
                         audio=None, seconds=None, peaks=[], audio_note=v6.AUDIO_ABSENT)
    script = FAKE_DOM.split("async function main() {")[0] + r"""
    async function main() {
      const data=JSON.parse(fs.readFileSync(0,'utf8')),panel=new Element('section');
      const esc=s=>String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
      const view=stages.find(stage=>stage.id==='instruct').mount(panel,data,{state:{},esc,data:async()=>data.evidence});
      await view.enter();await view.start();
      const radio=panel.querySelector('.pl-instruct-radio');
      assert(!radio.hidden);
      assert(radio.textContent.includes('Separate demos'));
      assert(radio.textContent.includes(data.voice.note));
      assert(radio.textContent.includes(data.voice.audio_note));
      assert(panel.querySelector('.pl-instruct-transcript').textContent.trim());
      assert(panel.querySelector('.pl-instruct-actor').textContent.includes(data.voice.heard_by));
      assert.equal(panel.querySelectorAll('.pl-instruct-wave i').length,0);
      await view.leave();
    }
    main().catch(error=>{console.error(error);process.exitCode=1});
    """
    node_run(script, STATIC, v6.ASSETS["xray-player.js"][0], stdin=json.dumps(data))


TYPED_INSTRUCTIONS = FAKE_DOM.split("async function main() {")[0] + r"""
async function main() {
  const data=JSON.parse(fs.readFileSync(0,'utf8'));
  const stage=stages.find(stage=>stage.id==='instruct');
  const settle=()=>new Promise(resolve=>setImmediate(resolve));
  const key=value=>value.trim().replace(/\s+/g,' ');
  const expected=new Set([data.reading.sentence]);
  if(data.voice.role==='hero'&&data.reading.heard.binds)expected.add(data.reading.heard.transcript);
  for(const row of data.reading.prepared.rows)
    if(row.as_expected===true&&!row.refused&&row.read===data.reading.rule)expected.add(row.sentence);
  function mount() {
    const panel=new Element('section'),events=[],posts=[],calls=[],logs=[],state={};
    let done=0,pendingPost=null,pendingWait=null,holdPost=false,holdWait=false;
    const ctx={state,demo:true,esc:s=>String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'),
      data:async()=>data.evidence,post:async(name,body)=>{posts.push({name,body});return holdPost?new Promise(resolve=>pendingPost=resolve):data.confirm},
      log:entry=>logs.push(entry),done:()=>done++,emit:(name,value)=>events.push([name,value]),sheet(){},toast(){},
      wait:async()=>{if(holdWait){holdWait=false;await new Promise(resolve=>pendingWait=resolve)}},
      bay:{closeBay:(...args)=>calls.push(['closeBay',...args]),focusBay:(...args)=>calls.push(['focusBay',...args])}};
    const view=stage.mount(panel,data,ctx),input=panel.querySelector('.pl-instruct-input');
    const type=async sentence=>{input.value=sentence;for(const callback of input.listeners.input||[])callback({target:input});await settle()};
    const click=async action=>{const button=panel.querySelector(`[data-action="${action}"]`);assert(visible(button)&&!button.disabled,action+' unavailable');await button.click();await settle()};
    return {panel,input,view,ctx,events,posts,calls,logs,type,click,
      get done(){return done},holdPost(){holdPost=true},holdWait(){holdWait=true},
      releasePost(){assert(pendingPost);pendingPost(data.confirm)},releaseWait(){assert(pendingWait);pendingWait()}};
  }
  for(const sentence of expected) {
    const app=mount(),suggestions=app.panel.querySelectorAll('[data-suggestion]');
    assert.deepEqual(new Set(suggestions.map(button=>button.textContent)),expected,'only exact successful recorded wordings');
    // These controls work before shell start/enter and after a collapsed section.
    await app.view.leave();
    const suggestion=suggestions.find(button=>button.textContent===sentence);
    await suggestion.click();await settle();
    assert.equal(app.input.value,sentence);assert.equal(suggestion.getAttribute('aria-pressed'),'true');
    await app.view.start();assert.equal(app.input.value,sentence,'radio arrival must preserve typed selection');
    await app.click('read');
    assert.equal(app.panel.querySelector('.pl-instruct-selected-sentence').textContent,sentence);
    assert(app.panel.querySelector('.pl-instruct-rule').textContent.includes('Recorded answer'));
    assert(app.panel.querySelector('.pl-instruct-confirmation-scope').textContent.includes(data.reading.sentence));
    const read=app.events.find(([name])=>name==='rule:read')[1];
    assert.equal(read.selected_sentence,sentence);assert.equal(read.sentence,data.reading.sentence);
    assert.deepEqual(read.binding,data.reading.binding);
    await app.click('confirm');
    assert.deepEqual(app.posts,[{name:'confirm',body:data.reading.binding}]);
    assert.deepEqual(app.ctx.state.confirm,data.confirm);assert.equal(app.done,1);
    const bay=data.reading.answer.constraint.bay;
    assert.deepEqual(app.calls,[['closeBay',bay,`Bay ${bay} · closed`],['focusBay',bay]]);
    await app.type('Put every box in bay 999.');
    assert.equal(app.ctx.state.confirm,undefined,'editing invalidates prior confirmation');
    assert(!visible(app.panel.querySelector('.pl-instruct-rule')));
    assert.equal(app.panel.querySelector('.pl-instruct-input-status').textContent,'No recorded reading for this sentence in the demo');
    assert(!visible(app.panel.querySelector('[data-action="read"]')));
    assert(app.events.some(([name,value])=>name==='rule:changed'&&!value.known));
    assert.equal(app.posts.length,1,'unknown sentences must not submit a confirmation');
  }
  const app=mount();
  for(const row of data.reading.prepared.rows.filter(row=>!row.as_expected||row.refused||row.read!==data.reading.rule)) {
    if(expected.has(row.sentence))continue;
    await app.type(row.sentence);
    assert.equal(app.panel.querySelector('.pl-instruct-input-status').textContent,'No recorded reading for this sentence in the demo');
    assert(!visible(app.panel.querySelector('[data-action="read"]')));
  }
  await app.type('  '+data.reading.sentence.replace(/ /g,'  ')+'  ');
  assert(visible(app.panel.querySelector('[data-action="read"]')),'typing accepts harmless whitespace');
  app.holdWait();await app.click('read');
  await app.type('Unrecorded edit while the reader is working.');
  app.releaseWait();await settle();
  assert(!visible(app.panel.querySelector('.pl-instruct-rule')),'a stale reading must stay hidden');
  assert(!app.events.some(([name])=>name==='rule:read'));
  await app.type(data.reading.sentence);await app.click('read');
  app.holdPost();await app.click('confirm');
  await app.type('Unrecorded edit while confirmation is pending.');
  app.releasePost();await settle();
  assert.equal(app.done,0);assert.equal(app.calls.length,0);assert.equal(app.ctx.state.confirm,undefined);
  assert(!app.events.some(([name])=>name==='rule:confirmed'),'a stale confirmation must not mark the ship');
  console.log(JSON.stringify({recorded:expected.size,done:app.done}));
}
main().catch(error=>{console.error(error);process.exitCode=1});
"""


def test_typed_recordings_and_pending_edits(payloads):
    data = dict(payloads, evidence=json.loads((STATIC / "studio-data/evidence.json").read_text()))
    data["confirm"] = v6.Confirmations().confirm(data["reading"]["binding"])
    result = json.loads(node_run(TYPED_INSTRUCTIONS, STATIC, v6.ASSETS["xray-player.js"][0],
                                 stdin=json.dumps(data)))
    assert result["recorded"] >= 3
    assert result["done"] == 0

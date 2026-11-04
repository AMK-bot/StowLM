"""Studio Plan and Stress modules: real data contracts and browser-free behavior checks."""
from __future__ import annotations

import json
from copy import deepcopy
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import v6
from app.flow import DemoService
from app.inputs import load_inputs

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app/static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


def node(code: str, data=None):
    result = subprocess.run(
        [NODE, "-e", code], input=json.dumps(data), text=True,
        capture_output=True, check=False, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def definition(module: str, expression: str):
    return node(
        "global.window = globalThis; let def; "
        "window.Studio = {step(value) { def = value; }}; "
        f"require({json.dumps(str(STATIC / ('studio-' + module + '.js')))}); "
        f"console.log(JSON.stringify({expression}));"
    )


@pytest.fixture(scope="module")
def payloads():
    """Use the real builders and an existing successful regate, without launching workers.

    The persisted regate is only the launch-check input; Hero._check still verifies
    the registered plan identity and instruction before the replan builder runs.
    """
    from app import v6_sandbox as bench
    from app.planner_data import persisted_regate

    # Preflight the precise recorded dependency before registration can warm
    # shared state. Public prepared JSON is opened normally, never skip-guarded.
    prepared = json.loads((STATIC / "planner-data/plan-before.json").read_text())
    regate_paths = [ROOT / row["path"] for row in prepared["meters"]["source"]["files"]
                    if Path(row["path"]).parent == Path("experiments/mvp/ui/cache/regate")]
    assert regate_paths, "prepared plan must name its persisted regate"
    missing = [str(path.relative_to(ROOT)) for path in regate_paths if not path.is_file()]
    if missing:
        pytest.skip("Recorded Studio plan check requires " + ", ".join(missing)
                    + "; persisted hero regate caches are deliberately not shipped")
    config = v6.hero()
    recorded, _ = persisted_regate(config)  # existing malformed/stale evidence fails normally
    original_sandbox = bench._SANDBOX
    original_state = deepcopy(bench._STATE)
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(bench, "_SANDBOX", None)
            patch.setattr(bench, "_STATE", {"failed": None, "baseline": None})
            patch.setattr(bench.Sandbox, "warm", lambda self: None)
            patch.setattr(v6, "HERO", v6.Hero())
            try:
                primary = load_inputs(instance_path=config["instance"], plan_path=config["before_plan"])
                others = []
                v6.register_hero(primary, others)
                service = DemoService(primary.instance, primary.plan, primary.metadata, others, discover=False)
                patch.setattr(v6, "regate_hero", lambda: recorded)
                gate = v6.HERO._check(service)
                assert gate["status"] == "shown", gate
                patch.setattr(v6.HERO, "gate", lambda _: gate)
                replan = v6.feature(lambda: v6.replan(service, recorded=True))
                sandbox = v6.feature(v6.sandbox)
                assert replan["available"] and sandbox["available"]
                from app.stage import build_stage_bundle
                bundles = {name: build_stage_bundle(service.replay(plan_id), plan_id=plan_id)
                           for name, plan_id in v6.plan_ids(service).items() if plan_id}
                # The fixture's recorded baseline supplies gauges during the morph.
                for value in bundles.values():
                    value["physics"] = sandbox["baseline"]
            finally:
                owned_sandbox = bench._SANDBOX
                if owned_sandbox is not None and owned_sandbox is not original_sandbox:
                    owned_sandbox.close()
                patch.setattr(bench, "_SANDBOX", None)
        # Detached payloads need no retained global registration or workers.
        yield {"plan": replan, "stress": sandbox, "bundles": bundles}
    finally:
        # MonkeyPatch restores globals on setup errors too; never close a
        # pre-existing sandbox belonging to another test or launch.
        bench._SANDBOX = original_sandbox
        bench._STATE.clear()
        bench._STATE.update(original_state)


@needs_node
@pytest.mark.parametrize("module,order,feature", [("plan", 30, "replan"), ("stress", 40, "sandbox")])
def test_registration_and_syntax(module, order, feature):
    subprocess.run([NODE, "--check", str(STATIC / f"studio-{module}.js")], check=True, capture_output=True)
    registered = definition(module, "({id:def.id,order:def.order,title:def.title,icon:def.icon,features:def.features,ship:def.ship,mount:typeof def.mount})")
    assert registered == {"id": module, "order": order, "title": "Plan" if module == "plan" else "Stress test",
                          "icon": module, "features": [feature], "ship": True, "mount": "function"}


def assert_path(value, path: str):
    """Check every declared data read, including each member of wildcard arrays."""
    head, _, tail = path.partition(".")
    array = head.endswith("[]")
    key = head[:-2] if array else head
    assert key in value, f"Payload has no field {path}"
    child = value[key]
    if child is None:  # the API deliberately makes some complete sections nullable
        return
    for item in child if array else [child]:
        if tail:
            assert_path(item, tail)


@needs_node
@pytest.mark.parametrize("module", ["plan", "stress"])
def test_payload_fields_are_real(module, payloads):
    fields = definition(module, "def.payloadFields")
    assert fields and len(fields) >= 10
    for path in fields:
        assert_path(payloads[module], path)


@pytest.mark.parametrize("module", ["plan", "stress"])
def test_assets_stay_local_and_css_uses_the_studio_contract(module):
    js = (STATIC / f"studio-{module}.js").read_text()
    css = (STATIC / f"studio-{module}.css").read_text()
    assert not re.search(r"https?://|//(?:cdn|fonts)\.|@import|url\(", js + css)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", js + css)
    # Shared classes own surfaces and type; v3 permits actor colours on dots only.
    appearance = r"(?<![\w-])(?:color|background(?:-color)?|font(?:-[\w-]+)?|border(?:-[\w-]+)?|box-shadow|letter-spacing|text-transform)\s*:"
    for selector, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if not re.search(appearance, declarations):
            continue
        assert any(dot in selector for dot in (".st-plan-engine-dot", ".st-plan-check::before", ".st-stress-checker::before"))
        assert not re.search(r"(?<![\w-])(?:color|font(?:-[\w-]+)?|box-shadow|letter-spacing|text-transform)\s*:", declarations)
        for value in re.findall(r"background(?:-color)?\s*:\s*([^;]+)", declarations):
            assert value.strip() in {"var(--st-c-engine)", "var(--st-c-checker)"}
    classes = re.findall(r"(?<![\w-])\.([a-zA-Z_][\w-]*)", css)
    shared = {"st-card", "st-card-title", "st-card-sub", "st-btn", "st-chip", "st-kv", "st-meta", "st-callout", "st-disclosure", "st-grid2", "st-big", "st-mono", "primary", "secondary", "ghost", "danger", "ok", "warn", "bad"}
    assert classes and all(name.startswith(f"st-{module}-") or name in shared for name in classes), classes
    assert not re.search(r"<h[12]\b", js)
    assert "FAST SCREEN" not in js


def test_plan_layout_fits_the_thread_and_wraps_long_provenance():
    js = (STATIC / "studio-plan.js").read_text()
    css = (STATIC / "studio-plan.css").read_text()
    assert re.search(r"\.st-plan-rules\s*\{[^}]*grid-template-columns:\s*(?:1fr|minmax\(0,\s*1fr\))", css)
    assert "repeat(2" not in css
    assert "overflow-wrap: anywhere" in css
    assert 'st-meta st-plan-provenance' in js
    assert "ctx.chip(stored" not in js
    assert "data-g=" not in js


def test_actor_colours_are_token_backed_dots():
    plan_js = (STATIC / "studio-plan.js").read_text()
    plan_css = (STATIC / "studio-plan.css").read_text()
    stress_css = (STATIC / "studio-stress.css").read_text()
    assert "st-plan-engine-dot" in plan_js
    assert "var(--st-c-engine)" in plan_css and "var(--st-c-checker)" in plan_css
    assert ".st-stress-checker::before" in stress_css and "var(--st-c-checker)" in stress_css
    assert "ctx.chip('Engine ' + engine + ' · search', 'device')" not in plan_js


@needs_node
def test_replan_helpers_keep_confirmations_and_land_every_box():
    result = definition("plan", """(() => {
      const L = def.logic;
      const before = {initial:{ship:[{box:'A',slot:'0010102'},{box:'B',slot:'0030102'}]},steps:[]};
      const after = {initial:before.initial,steps:[{box:'A',src:'0010102',dst:'0030102'}, {box:'B',src:'0030102',dst:'0050102'}]};
      const diff = L.morphOrder(before,after,['A']);
      return {query:L.replanQuery({token:'a b',reading:'read/1'}),recorded:L.replanQuery(null),
        order:diff.order.map(x=>x.box),end:L.morphAt(diff.order,8),
        changed:[...L.finalSlots(after)].map(([box,row])=>[box,row.slot]),
        missing:L.morphOrder(before,{initial:{ship:[{box:'NEW',slot:'0070102'}]},steps:[]},[])};
    })()""")
    assert result["query"] == "replan?token=a+b&reading=read%2F1"
    assert result["recorded"] == "replan?recorded=1"
    assert result["order"] == ["B", "A"]
    assert result["end"] == {"started": 2, "landed": 2, "flyers": []}
    assert result["changed"] == [["A", "0030102"], ["B", "0050102"]]
    assert result["missing"] is None


@needs_node
def test_drag_targets_respect_length_and_fast_frames_clear_stale_passes():
    result = definition("stress", """(() => {
      const L=def.logic, entries=[['0010102','A'],['0030102','B']];
      const cells=['0010102','0010104','0030102','0030104','0050102'];
      return {overlap:L.sameStack('0020104','0030102'),row:L.sameStack('0010102','0010204'),
        targets:L.candidates(cells,entries,{A:{length_ft:20},B:{length_ft:20}},'A'),
        long:L.candidates(cells,entries,{A:{length_ft:40},B:{length_ft:20}},'A'),
        frame:L.mergeFrame({gm_m:2,stability_passed:true,strength_passed:true,sf_n:[3]}, {gm_m:null,sf_n:[],stability_passed:false})};
    })()""")
    assert result["overlap"] is True and result["row"] is False
    assert {"slot": "0030102", "swap": "B"} in result["targets"]
    assert {"slot": "0030104", "swap": None} in result["targets"]
    assert not any(t["swap"] for t in result["long"])
    assert result["frame"] == {"gm_m": 2, "stability_passed": False, "strength_passed": None, "sf_n": [3]}


HARNESS = r"""
const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0,'utf8'));
const defs = {}, reads = [], calls = [], events = [], logs = [], requests = [], clicks = [], drags = [], snapshots = [], doneAt = [];
const speed=input.speed || 1;
let clock = 0, seq = 0, done = 0, raf = new Map();
global.window = globalThis;
global.performance = {now:()=>clock};
global.requestAnimationFrame = fn => {const id=++seq;raf.set(id,fn);return id;};
global.cancelAnimationFrame = id => raf.delete(id);
global.addEventListener = ()=>{};global.removeEventListener = ()=>{};
function pump(ms) {
  const end=clock+ms;
  while(clock<end) {
    clock=Math.min(end,clock+50);
    const work=[...raf.values()];raf.clear();work.forEach(fn=>fn(clock));
  }
}
async function advanceWall(ms) {
  for(let elapsed=0;elapsed<ms;elapsed+=50) {pump(Math.min(50,ms-elapsed));await Promise.resolve();}
}
async function withFrames(promise) {
  let settled=false, error;
  Promise.resolve(promise).then(()=>settled=true,e=>{settled=true;error=e;});
  for(let i=0;!settled && i<6000;i++) {pump(50);await Promise.resolve();}
  if(!settled) throw Error('View did not settle while RAF advanced independently of scaled waits');
  if(error) throw error;
}
const gfx = new Proxy({}, {get:(o,k)=>o[k] || (()=>{}),set:(o,k,v)=>(o[k]=v,true)});
class El {
  constructor(tag='div',attrs='') {
    this.tagName=tag;this.attrs=attrs;this.dataset={};this.style={};this.on={};this.cache=new Map();this.kids=[];
    this.hidden=/\bhidden\b/.test(attrs);this.disabled=false;this.className=(attrs.match(/class="([^"]*)"/)||['',''])[1];this._text='';this.html='';
    for(const m of attrs.matchAll(/data-([\w-]+)="([^"]*)"/g)) this.dataset[m[1].replace(/-([a-z])/g,(_,x)=>x.toUpperCase())]=m[2];
    this.classList={add(){},remove(){},toggle(){}};this.clientWidth=1000;this.clientHeight=650;
  }
  set innerHTML(value){this.html=value;this._text='';this.cache.clear();if(this.className.includes('st-stress-verdict')) snapshots.push(value);}
  get innerHTML(){return this.html;}
  set textContent(value){this._text=String(value);this.html='';}
  get textContent(){return this._text || this.html.replace(/<[^>]*>/g,'');}
  matches(selector,attrs) {
    const last=selector.split(' ').slice(-1)[0];
    if(last.startsWith('.')) return (attrs.match(/class="([^"]*)"/)||['',''])[1].split(' ').includes(last.slice(1));
    if(last.startsWith('[')) {const m=/^\[([^=\]]+)(?:="([^"]*)")?\]$/.exec(last);return m && (m[2]===undefined ? attrs.includes(m[1]) : attrs.includes(m[1]+'="'+m[2]+'"'));}
    return false;
  }
  querySelectorAll(selector) {
    if(this.cache.has(selector)) return this.cache.get(selector);
    const found=[];
    for(const m of this.html.matchAll(/<([a-z][\w-]*)([^>]*)>/g)) if(this.matches(selector,m[2])) found.push(new El(m[1],m[2]));
    this.cache.set(selector,found);return found;
  }
  querySelector(selector) {
    const items=this.querySelectorAll(selector);
    if(!items.length) items.push(new El());return items[0];
  }
  addEventListener(name,fn){this.on[name]=fn;}
  appendChild(child){this.kids.push(child);child.parentNode=this;return child;}
  remove(){this.removed=true;}
  setAttribute(){}setPointerCapture(){throw Error('NotFoundError: synthetic pointer has no native capture');}getContext(){return gfx;}
  getBoundingClientRect(){return {left:20,top:50,width:1000,height:650};}
}
const shipCanvas=new El('canvas'), body=new El('body');
global.document={body,createElement:tag=>new El(tag),getElementById:id=>id==='ship-canvas'?shipCanvas:null};
global.getComputedStyle=()=>({getPropertyValue:()=> 'green'});
function strict(value,path) {
  if(!value || typeof value!=='object') return value;
  return new Proxy(value,{get(object,key,receiver){
    if(typeof key==='symbol'||key==='then'||key==='toJSON') return Reflect.get(object,key,receiver);
    if(!(key in object) && !(Array.isArray(object) && /^\d+$/.test(key))) throw Error('Missing real payload field '+path+'.'+key);
    if(Object.hasOwn(object,key)) reads.push(path+'.'+key);
    return strict(Reflect.get(object,key,receiver),path+'.'+key);
  }});
}
const finalShip=b=> {
 const ship=new Map((b.initial.ship||[]).map(r=>[r.slot,r.box]));
 for(const s of b.steps) {
   if(!s.box) continue;
   for(const [slot,id] of ship) if(id===s.box) ship.delete(slot);
   if(/^\d{7}$/.test(String(s.dst))) ship.set(s.dst,s.box);
 }
 return ship;
};
const ext=slot=>{const b=+slot.slice(0,3),r=+slot.slice(3,5),t=+slot.slice(5,7);return {x0:b*10,x1:b*10+8,y0:r,y1:r+1,z0:t,z1:t+1,deck:t>=80};};
let hitBox=null;
const scene={pixelScale:1,project:(x,y,z)=>[x*2+z*.1,y*60-z*5+500],unproject:(x,y)=>({x:x/2,z:(500-y)/5}),shipExtOfSlot:(slot)=>ext(slot),
  hit:(x,y)=>{
    if(hitBox) return hitBox;
    let best=null,distance=1;
    for(const [slot,id] of stage.S.state.ship) {
      const e=ext(slot),p=scene.project((e.x0+e.x1)/2,(e.y0+e.y1)/2,(e.z0+e.z1)/2),d=Math.hypot(x-p[0],y-p[1]);
      if(d<distance) {best=id;distance=d;}
    }
    return best;
  },kit:{faces:()=>({front:[],end:[],top:[]}),poly(){},containerBox(){}},
  setMorph(value){calls.push('morph');this.morph=value;}};
const stage={scene,S:{k:0,state:{}},B:null,
 load(b,k){this.B=b;this.total=b.steps.length;this.S.k=k==='end'?this.total:k;const ship=finalShip(b);this.S.state={ship,loc:new Map([...ship].map(([slot,box])=>[box,{w:'ship',k:slot}]))};calls.push('load');},
 setPlaying(){calls.push('playing');},jumpTo(k){this.S.k=k;},peek(k){calls.push('peek');},
 stateAt(){return this.S.state;},setGaugeFrame(f){this.gauge=f;calls.push('gauge');},redraw(){calls.push('redraw');},selectBox(){},seek(){}
};
stage.load(input.bundles.before,'end');global.__stage=stage;
global.Studio={step:d=>defs[d.id]=d};
require(input.static+'/studio-plan.js');require(input.static+'/studio-stress.js');
const ok=value=>({ok:true,json:async()=>value});
global.fetch=async url=>{
 requests.push(url);
 if(url.startsWith('/api/stage?')) {const id=new URLSearchParams(url.split('?')[1]).get('plan_id');return ok(Object.values(input.bundles).find(b=>b.meta.plan_id===id));}
 if(url.startsWith('/api/v6/try/')) return ok({status:'done',recorded:false,fast:input.stress.cases[0].fast,full:input.stress.cases[0].verdict});
 throw Error('Unexpected URL '+url);
};
const ctx={stage,state:{confirm:{token:'confirmed-token'}},esc:String,
 api:async name=>name.startsWith('replan')?strict(input.plan,'replan'):strict(input.stress,'sandbox'),
 post:async(name,body)=>{requests.push('/api/v6/'+name);return {job:'free-drag'};},
 center(){},chip:(t,k)=>'<span class="st-chip '+k+'">'+t+'</span>',
 log:line=>logs.push(line),emit:(name,value)=>events.push({name,value}),done:()=>{done++;doneAt.push(events.filter(e=>e.name==='stress:verdict').length);},
 wait:async ms=>{pump(ms/speed);await Promise.resolve();},
 click:async el=>{await advanceWall(450/speed);clicks.push(el.attrs);if(el.on.click)el.on.click({target:el});else if(panel.on.click)panel.on.click({target:{closest:s=>s==='[data-stress-case]'?el:null}});await advanceWall(100/speed);},
 drag:async(from,to,opts)=>{
   drags.push({from,to,ms:opts&&opts.ms});
   const overlay=body.kids[body.kids.length-1];
   const event=(x,y)=>({button:0,pointerId:1,clientX:x,clientY:y,preventDefault(){},stopPropagation(){}});
   overlay.on.pointerdown(event(from.x,from.y));
   for(let i=1;i<=24;i++) {
     await advanceWall((opts&&opts.ms||1200)/24/Math.min(speed,2));
     overlay.on.pointermove(event(from.x+(to.x-from.x)*i/24,from.y+(to.y-from.y)*i/24));
   }
   overlay.on.pointerup(event(to.x,to.y));
   await advanceWall(160/speed);return true;
 }
};
let panel;
(async()=>{
 panel=new El();const plan=defs.plan.mount(panel,{replan:strict(input.plan,'replan')},ctx);
 const lifecycle=[typeof plan.enter,typeof plan.leave,typeof plan.start,typeof plan.autoplay];
 await plan.enter();const eventsBeforeStart=events.length;
 await withFrames(Promise.all([plan.start(),plan.autoplay(ctx)]));
 const planMorphs=calls.filter(c=>c==='morph').length;
 const landing={counter:panel.querySelector('.st-plan-counter').textContent,check:panel.querySelector('.st-plan-check').textContent,
  heel:panel.querySelector('.st-plan-heel').textContent,flyers:scene.morph.flyers.length,ship:[...scene.morph.state.ship],rule:panel.querySelector('.st-plan-rule-text').textContent};
 plan.leave();const cleared=scene.morph===null, replayHidden=panel.querySelector('.st-plan-replay').hidden;
 panel=new El();const stress=defs.stress.mount(panel,{sandbox:strict(input.stress,'sandbox')},ctx);
 lifecycle.push(typeof stress.enter,typeof stress.leave,typeof stress.start,typeof stress.autoplay);
 await stress.enter();await stress.start();
 const stressStartEvents=events.length;
 const playback=stress.autoplay(ctx);
 if(input.cancel) {
   await advanceWall(2000);stress.leave();await withFrames(playback);
   console.log(JSON.stringify({eventsBeforeStart,stressStartEvents,lifecycle,events,done,clicks,drags,doneAt,restored:stage.B===input.bundles.after,raf:raf.size}));
   return;
 }
 await withFrames(playback);
 const verdict=panel.querySelector('.st-stress-verdict').innerHTML;
 panel.on.click({target:{closest:selector=>selector==='.st-stress-undo'?{}:null}});
 const undoCleared=panel.querySelector('.st-stress-verdict').hidden && panel.querySelector('.st-stress-rules').hidden;
 const liveCase=input.stress.cases.find(c=>c.kind==='legal');hitBox=liveCase.box;
 const over=body.kids[body.kids.length-1];
 const slots=defs.stress.logic.candidates(stage.B.geometry.slots,[...stage.S.state.ship],stage.B.boxes,hitBox);
 const target=slots.find(c=>!input.stress.cases.some(k=>k.box===hitBox&&k.slot===c.slot));
 const e=ext(target.slot),p=scene.project((e.x0+e.x1)/2,(e.y0+e.y1)/2,e.z1);
 const evt=(x,y)=>({button:0,pointerId:1,clientX:x+20,clientY:y+50,preventDefault(){},stopPropagation(){}});
 over.on.pointerdown(evt(100,100));over.on.pointermove(evt(p[0],p[1]));over.on.pointerup(evt(p[0],p[1]));
 await ctx.wait(1000);await ctx.wait(1500);await ctx.wait(1500);
 stress.leave();
 console.log(JSON.stringify({lifecycle,eventsBeforeStart,stressStartEvents,planMorphs,clicks,drags,doneAt,snapshots,landing,cleared,replayHidden,verdict,undoCleared,actionsHidden:panel.querySelector('.st-stress-cases').hidden,events,logs,done,reads:reads.length,calls,requests,
   restored:stage.B===input.bundles.after,overlayRemoved:over.removed,raf:raf.size}));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""


@needs_node
@pytest.mark.parametrize("speed", [1, 4])
def test_modules_land_replay_verdicts_drag_and_cleanup_without_a_browser(payloads, speed):
    out = node(HARNESS, {**payloads, "static": str(STATIC), "speed": speed})
    assert out["lifecycle"] == ["function"] * 8
    assert out["eventsBeforeStart"] == 0 and out["stressStartEvents"] == 1
    assert 160 < out["planMorphs"] < 170  # start + autoplay must play the first action once
    assert not any("st-plan-replay" in click for click in out["clicks"])
    assert out["clicks"] == []
    assert len(out["drags"]) == 2
    assert all(drag["ms"] == 1200 for drag in out["drags"])
    assert all(drag["from"] != drag["to"] for drag in out["drags"])
    prepared = [next(case for case in payloads["stress"]["cases"] if case["kind"] == kind) for kind in ("securing", "legal")]
    for drag, case in zip(out["drags"], prepared):
        slot = case["slot"]
        bay, row, tier = int(slot[:3]), int(slot[3:5]), int(slot[5:])
        # Same fixture geometry/projection, including canvas screen offset (20,50).
        assert drag["to"] == pytest.approx({"x": (bay * 10 + 4) * 2 + (tier + 1) * .1 + 20,
                                                 "y": (row + .5) * 60 - (tier + 1) * 5 + 550})
    assert out["landing"]["counter"].endswith(f"of {len(payloads['bundles']['after']['steps'])} moves")
    assert out["landing"]["check"] == f"Full checker: passed · {payloads['plan']['rule_counts']['active']} active rules"
    assert out["landing"]["heel"].startswith("Peak heel ")
    assert out["landing"]["flyers"] == 0 and out["cleared"]
    assert {event["name"] for event in out["events"]} == {"plan:replanned", "stress:verdict"}
    verdicts = [event["value"]["verdict"]["legal"] for event in out["events"] if event["name"] == "stress:verdict"]
    assert verdicts[:2] == [False, True]
    assert "Recorded verdict" in out["verdict"] and "Passes as edited" in out["verdict"]
    assert 'class="st-callout st-stress-checker"' in out["verdict"]
    assert out["undoCleared"]
    assert out["replayHidden"] and out["actionsHidden"]
    assert any("Fast screen" in card for card in out["snapshots"])
    assert any("Lashing" in card and "Full checker" in card for card in out["snapshots"])
    assert out["done"] == 2
    assert out["doneAt"] == [0, 2]  # the next pill waits for both complete stress verdicts
    assert {line["who"] for line in out["logs"]} == {"Engine 0.13.0", "Full checker"}
    assert out["reads"] > 100
    assert "/api/v6/try" in out["requests"]
    assert all(request.startswith("/") for request in out["requests"])
    assert out["restored"] and out["overlayRemoved"] and out["raf"] == 0


@needs_node
def test_leaving_stress_resolves_pending_autoplay_without_starting_the_next_move(payloads):
    out = node(HARNESS, {**payloads, "static": str(STATIC), "speed": 4, "cancel": True})
    assert [event["name"] for event in out["events"]] == ["plan:replanned"]
    assert out["done"] == 1
    assert out["clicks"] == [] and len(out["drags"]) == 1
    assert out["restored"] and out["raf"] == 0

"""Planner Plan/Verify: real fixture contracts and browser-free workflow tests."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.tests.test_studio_plan import payloads as studio_payloads
from app.tests.test_studio_plan import assert_path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app/static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


def node(code, data=None):
    run = subprocess.run([NODE, "-e", code], input=json.dumps(data), text=True,
                         capture_output=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip(), "Node workflow exited without settling"
    return json.loads(run.stdout)


def definition(name, expression):
    return node("global.window=globalThis; let def; window.Planner={stage(d){def=d;}};"
                f"require({json.dumps(str(STATIC / ('planner-' + name + '.js')))});"
                f"console.log(JSON.stringify({expression}));")


@needs_node
@pytest.mark.parametrize("name,order,feature", [("plan", 30, "replan"), ("stress", 40, "sandbox")])
def test_registration_and_syntax(name, order, feature):
    subprocess.run([NODE, "--check", str(STATIC / f"planner-{name}.js")], capture_output=True, check=True)
    assert definition(name, "({id:def.id,order:def.order,features:def.features,mount:typeof def.mount})") == {
        "id": name, "order": order, "features": [feature], "mount": "function"}


@pytest.mark.parametrize("name", ["plan", "stress"])
def test_local_two_dimensional_modules_and_scoped_styles(name):
    js = (STATIC / f"planner-{name}.js").read_text()
    css = (STATIC / f"planner-{name}.css").read_text()
    assert "__stage" not in js and "StageStory" not in js and "scene.project" not in js
    assert not re.search(r"https?://|@import|fonts\.", js + css)
    assert "var(--pl-" in css
    classes = re.findall(r"(?<![\w-])\.([a-zA-Z_][\w-]*)", css)
    assert classes and all(c.startswith(f"pl-{name}-") for c in classes), classes


@needs_node
@pytest.mark.parametrize("name,feature", [("plan", "plan"), ("stress", "stress")])
def test_module_field_contracts_read_real_data(name, feature, planner_payloads):
    fields = definition(name, "({api:def.payloadFields,data:def.dataFields})")
    assert fields["api"] and fields["data"]
    for path in fields["api"]:
        assert_path(planner_payloads["api"][feature], path)
    for document, paths in fields["data"].items():
        for path in paths:
            assert_path(planner_payloads["docs"][document], path)


@needs_node
def test_native_plan_identity_and_literal_checker_helpers():
    out = definition("plan", """(() => {
      const L=def.logic, moves=[{i:0,box_id:'A'},{i:1,box_id:'B'}];
      return {query:L.replanQuery({token:'a b',reading:'r/1'}),
        indices:moves.map((m,i)=>L.playbackIndex(m,i+1,moves)),
        matches:L.planMatches({plan_id:'native'},'plan-2',{meta:{plan_id:'plan-2',plan_ref:'native'}}),
        wrong:L.planMatches({plan_id:'other'},'plan-2',{meta:{plan_id:'plan-2',plan_ref:'native'}}),
        pass:L.familyPassed({status:'pass',rules:[{status:'pass',violations:0}]}),
        skipped:L.familyPassed({status:'n/a',rules:[{status:'n/a',violations:0}]}),
        frame:L.frameAt([{step:0,gm_m:2},{step:3,gm_m:1.8}],2)};
    })()""")
    assert out == {"query": "replan?token=a+b&reading=r%2F1", "indices": [1, 2], "matches": True,
                   "wrong": False, "pass": True, "skipped": False, "frame": {"step": 0, "gm_m": 2}}


@needs_node
@pytest.mark.parametrize("name", ["plan", "stress"])
def test_meter_updates_use_shell_objects_and_never_make_unknown_flags_green(name):
    out = definition(name, """def.logic.meterUpdate({gm_m:2,gm_min_m:.15,heel_deg:1,trim_m:-.1,draught_mean_m:6,
        max_draught_m:7,max_sf_ratio:.4,max_bm_ratio:.5,stability_passed:null,strength_passed:null},
        {gm_min_m:.3,heel_max_deg:2,draught_m:7,trim_min_m:-.2,trim_max_m:.2},4,145)""")
    if name == "plan":
        assert out["moves"] == {"at": 4, "total": 145}
    else:
        assert set(out) == {"stability", "list", "draught", "hull"}
    assert out["stability"]["value"] == "GM 2.00 m"
    assert out["stability"]["limit"] == "minimum 0.30 m"
    assert out["hull"]["value"] == "50 %"
    assert all(out[key]["state"] == "info" for key in ("stability", "list", "draught", "hull"))


@needs_node
def test_prepared_swaps_preserve_every_container_and_match_exact_coordinates(planner_payloads):
    code = """const fs=require('node:fs'),input=JSON.parse(fs.readFileSync(0,'utf8'));global.window=globalThis;let def;
      global.Planner={stage:d=>def=d};require(input.static+'/planner-stress.js');const L=def.logic;
      const c=input.docs.stress.cases.find(c=>c.full.legal),plan=input.docs.stress;
      const result=L.movedPlan(plan,{box_id:c.box_id,from:c.from,to:c.to},c.full.gauges.swapped_box);
      console.log(JSON.stringify({id:L.matchCase(input.docs.stress.cases,{box_id:c.box_id,from:c.from,to:c.to}).id,
        mismatch:!!L.matchCase(input.docs.stress.cases,{box_id:c.box_id,from:c.to,to:c.from}),
        count:result.slots.length,unique:new Set(result.slots.map(s=>s.box_id)).size,
        destination:result.slots.find(s=>s.box_id===c.box_id),swapped:result.slots.find(s=>s.box_id===c.full.gauges.swapped_box)}));"""
    out = node(code, planner_payloads)
    legal = next(c for c in planner_payloads["docs"]["stress"]["cases"] if c["full"]["legal"])
    assert out["id"] == legal["id"] and not out["mismatch"]
    assert out["count"] == out["unique"] == len(planner_payloads["docs"]["stress"]["slots"])
    assert {k: out["destination"][k] for k in ("bay", "row", "tier")} == {k: legal["to"][k] for k in ("bay", "row", "tier")}
    assert {k: out["swapped"][k] for k in ("bay", "row", "tier")} == {k: legal["from"][k] for k in ("bay", "row", "tier")}


def final_slots(bundle):
    result = {row["box"]: row["slot"] for row in bundle["initial"]["ship"]}
    for move in bundle["steps"]:
        if not move.get("box"):
            continue
        if re.fullmatch(r"\d{7}", str(move.get("dst"))):
            result[move["box"]] = move["dst"]
        elif re.fullmatch(r"\d{7}", str(move.get("src"))):
            result.pop(move["box"], None)
    return result


def slot(value):
    return {"bay": int(value[:3]), "row": int(value[3:5]), "tier": int(value[5:]), "deck": int(value[5:]) >= 80}


@pytest.fixture(scope="module")
def planner_payloads(studio_payloads):
    """Prefer generated files; until they land, use the same genuine v6 inputs."""
    names = ("plan-before", "plan-after", "moves", "stress", "ship", "boxes")
    paths = {name: STATIC / "planner-data" / f"{name}.json" for name in names}
    if all(path.is_file() for path in paths.values()):
        docs = {name: json.loads(path.read_text()) for name, path in paths.items()}
        generated = True
    else:
        before, after, sandbox = (final_slots(studio_payloads["bundles"][name]) for name in ("before", "after", "sandbox"))
        source = {"kind": "test_projection_of_real_v6_payloads", "paths": ["app/v6.py"], "sha256": "0" * 64}
        docs = {}
        for name, slots in (("before", before), ("after", after)):
            docs["plan-" + name] = {"plan_id": studio_payloads["plan"]["gate"][name]["plan_id"],
                "label": "Stored plan", "slots": [{**slot(pos), "box_id": box} for box, pos in slots.items()],
                "counts": {"boxes": len(slots)}, "source": source}
        forced = studio_payloads["plan"]["facts"]["forced_ids"]
        docs["moves"] = {"moves": [{"i": i + 1, "kind": "shift", "box_id": box,
            "from": slot(before[box]), "to": slot(pos), "forced": box in forced}
            for i, (box, pos) in enumerate((b, p) for b, p in after.items() if before.get(b) != p)], "source": source}
        cases = studio_payloads["stress"]["cases"]
        sandbox_plan = {"plan_id": studio_payloads["plan"]["plans"]["sandbox"], "label": studio_payloads["stress"]["stored_label"],
            "slots": [{**slot(pos), "box_id": box} for box, pos in sandbox.items()], "counts": {"boxes": len(sandbox)}, "source": source}
        docs["ship"] = {"limits": {}, "source": source}
        docs["boxes"] = {"boxes": {}, "pods": [], "source": source}
        docs["stress"] = {"plan_id": sandbox_plan["plan_id"], "slots": sandbox_plan["slots"], "cases": [{"id": case["kind"], "name": case["name"], "box_id": case["box"],
            "from": slot(sandbox[case["box"]]), "to": slot(case["slot"]), "fast": case["fast"], "full": case["verdict"],
            "rule": case["verdict"]["outcome"], "label": "Recorded verdict"} for case in cases],
            "plan": sandbox_plan, "label": "Recorded verdict", "running_label": studio_payloads["stress"]["running_label"],
            "challenge_note": studio_payloads["stress"]["challenge_note"], "challenge_tag": studio_payloads["stress"]["challenge_tag"],
            "derived": studio_payloads["stress"]["derived"], "stored_label": studio_payloads["stress"]["stored_label"], "source": source}
        generated = False
    return {"docs": docs, "api": studio_payloads, "generated": generated, "static": str(STATIC)}


HARNESS = r"""
const fs=require('node:fs'), input=JSON.parse(fs.readFileSync(0,'utf8'));
global.window=globalThis;let defs={};global.Planner={stage:d=>defs[d.id]=d};
require(input.static+'/planner-plan.js');require(input.static+'/planner-stress.js');
class El {
 constructor(tag='div',attrs=''){this.tagName=tag;this.attrs=attrs;this.on={};this.style={};this.cache=new Map();this.html='';this._text='';this.hidden=/\bhidden\b/.test(attrs);this.disabled=false;this.dataset={};this.children=[];this.className='';
 for(const m of attrs.matchAll(/data-([\w-]+)="([^"]*)"/g)) this.dataset[m[1].replace(/-([a-z])/g,(_,x)=>x.toUpperCase())]=m[2];
 this.classList={add(){},remove(){},toggle(){}};
 }
 set innerHTML(v){this.html=v;this._text='';this.cache.clear();}get innerHTML(){return this.html;}
 set textContent(v){this._text=String(v);this.html='';}get textContent(){return this._text||this.html.replace(/<[^>]*>/g,'');}
 querySelectorAll(s){if(this.cache.has(s))return this.cache.get(s);const items=[];for(const m of this.html.matchAll(/<([a-z][\w-]*)([^>]*)>/g)){
 let yes=false;if(s.startsWith('.'))yes=(m[2].match(/class="([^"]*)"/)||['',''])[1].split(' ').includes(s.slice(1));
 else if(s.startsWith('[')){const a=/^\[([^=\]]+)(?:="([^"]*)")?\]$/.exec(s);yes=a&&(a[2]===undefined?m[2].includes(a[1]):m[2].includes(a[1]+'="'+a[2]+'"'));}
 if(yes)items.push(new El(m[1],m[2]));}
 for(const child of this.children){if(s.startsWith('.')&&child.className.split(' ').includes(s.slice(1)))items.push(child);items.push(...child.querySelectorAll(s));}
 this.cache.set(s,items);return items;}
 querySelector(s){const all=this.querySelectorAll(s);if(!all.length)all.push(new El());return all[0];}
 addEventListener(t,f){this.on[t]=f;}setAttribute(){}removeAttribute(){}appendChild(c){if(c.parentNode)c.remove();this.children.push(c);c.parentNode=this;return c;}append(...c){for(const child of c)this.appendChild(child);}prepend(...c){for(const child of c.reverse()){if(child.parentNode)child.remove();this.children.unshift(child);child.parentNode=this;}}replaceChildren(...c){this.children=c;}remove(){if(this.parentNode){const p=this.parentNode;p.children=p.children.filter(child=>child!==this);this.parentNode=null;}}
}
const resultHost=new El();resultHost.hidden=true;
global.document={createElement:t=>new El(t),getElementById:id=>id==='pl-result'?resultHost:null};
const calls=[],events=[],logs=[],meters=[],drags=[],doneAt=[],dataReads=[];let done=0,drop=null,plan=null,heldWait=null,resultNode=null,clock=0,pendingPlanFinish=null;
global.performance={now:()=>clock};
const timers=new Map();let timerId=0;
global.setTimeout=(fn,ms)=>{const id=++timerId;timers.set(id,{fn,at:clock+ms});return id;};
global.clearTimeout=id=>timers.delete(id);
function tick(ms){clock+=ms;for(const [id,t] of [...timers])if(t.at<=clock){timers.delete(id);t.fn();}}
const key=s=>typeof s==='string'?s:s?[s.bay,s.row,s.tier].join('|'):'';
const points=new Map();
const bay={
 setPlan:p=>{plan=p;calls.push({fn:'setPlan',id:p.plan_id,slots:p.slots.length});if(pendingPlanFinish){const finish=pendingPlanFinish;pendingPlanFinish=null;finish({cancelled:true,completed:104,total:input.docs.moves.moves.length});}},
 showBays:b=>calls.push({fn:'showBays',bays:b}),focusBay:b=>calls.push({fn:'focusBay',bay:b}),closeBay:bay=>calls.push({fn:'closeBay',bay}),openBay:bay=>calls.push({fn:'openBay',bay}),
 highlight:(ids,kind)=>calls.push({fn:'highlight',ids,kind}),clearMarks:()=>calls.push({fn:'clearMarks'}),
 markViolation:(slot,text)=>calls.push({fn:'markViolation',slot,text}),
 enableDrag:(fn,opts)=>{drop=fn;calls.push({fn:'enableDrag',enabled:typeof fn==='function',opts});},
 updateBoxes:overrides=>calls.push({fn:'updateBoxes',overrides}),resetBoxes:()=>calls.push({fn:'resetBoxes'}),
 screenPoint:slot=>{const p={x:200+(slot.bay||0)*25+(slot.row||0)*2,y:500-(slot.tier||0)*3};points.set(key(p),slot);points.set(p.x+','+p.y,slot);return p;},
 async play(moves,opts){calls.push({fn:'play',moves:moves.length,speed:opts&&opts.speed,swap_with:moves[0]&&moves[0].swap_with});
   for(let i=0;i<moves.length;i++){
     tick(320/(opts&&opts.speed||1));
     if(opts&&opts.onMove)opts.onMove(moves[i],i+1,moves.length);
     if((input.earlyLeave||input.deadline)&&moves.length>1&&i===103) return await new Promise(resolve=>{pendingPlanFinish=resolve;});
     await Promise.resolve();
   }
   return {completed:moves.length,total:moves.length,cancelled:false};
 }
};
global.fetch=async url=>{calls.push({fn:'fetch',url});const id=new URLSearchParams(url.split('?')[1]).get('plan_id');const b=Object.values(input.api.bundles).find(b=>b.meta.plan_id===id);return {ok:!!b,json:async()=>b};};
let panel;
const ctx={bay,state:{confirm:{token:'confirmed-test-token'}},demo:true,esc:s=>String(s==null?'':s),
 data:async name=>{dataReads.push(name);return input.docs[name.replace(/\.json$/,'')];},
 api:async name=>name.startsWith('replan')?input.api.plan:input.api.stress,
 post:async()=>{throw Error('Prepared workflows must use their recorded verdicts');},
 loadlist:{setRows(){},mark:(ids,kind)=>calls.push({fn:'loadlist.mark',ids,kind}),focus(){}},sheet(){},slot:name=>name==='result'?resultHost:null,result:node=>{resultNode=node;resultHost.appendChild(node);resultHost.hidden=false;},
 meters:update=>meters.push(update),log:line=>logs.push(line),emit:(name,value)=>events.push({name,value,time:clock}),done:()=>{done++;doneAt.push(events.filter(e=>e.name==='stress:verdict').length);},
 chip:(t,k)=>'<span>'+t+'</span>',toast(){},on(){},
 wait:async ms=>{if(input.cancel&&ms>=300&&drop){await new Promise(r=>{heldWait=r;});}else {tick(ms);await Promise.resolve();}},
 click:async target=>{if(target.on.click)target.on.click({target});else if(panel.on.click)panel.on.click({target:{closest:s=>s.includes('case')?target:null}});await Promise.resolve();},
 drag:async(from,to,opts)=>{drags.push({from,to,opts});tick((opts&&opts.ms||1200)+1300);if(!drop)throw Error('No enabled bay drop handler');const a=points.get(from.x+','+from.y),b=points.get(to.x+','+to.y);const c=input.docs.stress.cases.find(c=>key(c.from)===key(a)&&key(c.to)===key(b));if(!c)throw Error('Drag did not target a prepared pair');const result=drop({box_id:c.box_id,from:a,to:b,swap_with:c.full.gauges.swapped_box||undefined});await Promise.resolve();return true;}
};
async function settle(p){let ended=false,error;Promise.resolve(p).then(()=>ended=true,e=>{error=e;ended=true;});for(let n=0;!ended&&n<10000;n++){if(input.deadline)tick(20);await Promise.resolve();}if(!ended)throw Error('Workflow did not settle');if(error)throw error;}
(async()=>{
 panel=new El();const p=defs.plan.mount(panel,{replan:input.api.plan},ctx);const lifecycle=[typeof p.enter,typeof p.leave,typeof p.start,typeof p.autoplay];
 await p.enter();
 if(input.leaveBeforeStart){p.leave();console.log(JSON.stringify({calls,done,meters,plan}));return;}
 if(input.earlyLeave||input.deadline){
   const generating=p.start();for(let n=0;!pendingPlanFinish&&n<1000;n++)await Promise.resolve();
   if(input.earlyLeave)p.leave();await settle(generating);
   console.log(JSON.stringify({calls,done,meters,plan,events,resultMounted:resultHost.children.includes(p.result),timers:timers.size}));return;
 }
 const planStart=clock;await settle(Promise.all([p.start(),p.autoplay(ctx)]));
 const result=p.result||resultNode||resultHost.children[0]||panel;
 const planHtml=panel.innerHTML,resultHtml=result.innerHTML,planCounter=result.querySelector('.pl-plan-counter').textContent,
 planCheck=result.querySelector('.pl-plan-check').textContent,planRules=panel.querySelector('.pl-plan-rules-count').textContent,planMeters=meters.slice(),planElapsed=clock-planStart;p.leave();
 if(input.regenerate){
   global.Planner.go=async id=>{calls.push({fn:'go',id});await p.enter();};
   await settle(result.querySelector('.pl-plan-replay').on.click({target:result.querySelector('.pl-plan-replay')}));
   console.log(JSON.stringify({calls,done,meters,plan,events}));return;
 }
 panel=new El();const s=defs.stress.mount(panel,{sandbox:input.api.stress},ctx);lifecycle.push(typeof s.enter,typeof s.leave,typeof s.start,typeof s.autoplay);
 const stressStart=clock;await s.enter();await s.start();const meterStart=meters.length;
 const baselineLabel=resultHost.children.find(child=>child.className.includes('pl-stress-baseline'));
 const baselineVisible=!!baselineLabel&&!baselineLabel.hidden&&!resultHost.hidden;
 const baselineText=baselineLabel&&baselineLabel.textContent;
 const generatedHiddenDuringStress=result.hidden;
 const playback=s.autoplay(ctx);
 if(input.cancel){for(let n=0;!heldWait&&n<1000;n++)await Promise.resolve();s.leave();if(heldWait)heldWait();await settle(playback);}
 else {await settle(playback);s.leave();}
 console.log(JSON.stringify({lifecycle,calls,events,logs,meters,stressMeters:meters.slice(meterStart),planMeters,planCounter,planCheck,planRules,planElapsed,stressElapsed:clock-stressStart,stressStart,drags,done,doneAt,dataReads,planHtml,resultHtml,resultMounted:resultHost.children.includes(p.result),baselineVisible,baselineText,generatedHiddenDuringStress,generatedRestored:!result.hidden,baselineRemoved:!baselineLabel||baselineLabel.hidden||!resultHost.children.includes(baselineLabel),stressHtml:panel.innerHTML,dragDisabled:drop===null||drop===undefined,timers:timers.size}));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""


@needs_node
def test_plan_and_recorded_drag_workflow(planner_payloads):
    out = node(HARNESS, planner_payloads)
    assert out["lifecycle"] == ["function"] * 8
    plays = [c for c in out["calls"] if c["fn"] == "play"]
    assert len(plays) == 3
    assert plays[0]["moves"] == len(planner_payloads["docs"]["moves"]["moves"])
    assert [p["moves"] for p in plays[1:]] == [1, 1]
    first_plan = next(c for c in out["calls"] if c["fn"] == "setPlan")
    assert first_plan["id"] == planner_payloads["docs"]["plan-before"]["plan_id"]
    assert {c["kind"] for c in out["calls"] if c["fn"] == "highlight"} >= {"forced", "moved"}
    assert out["meters"]
    total = len(planner_payloads["docs"]["moves"]["moves"])
    assert out["planCounter"] == f"Move {total} of {total}"
    assert out["planCheck"] == "Full checker: passed"
    assert str(planner_payloads["api"]["plan"]["rule_counts"]["active"]) in out["planRules"]
    assert out["planMeters"][-1]["moves"] == {"at": total, "total": total}
    assert {"stability", "list", "draught", "hull", "rules"} <= out["planMeters"][-1].keys()
    assert all("value" in out["planMeters"][-1][key] for key in ("stability", "list", "draught", "hull", "rules"))
    assert len(out["drags"]) == 2
    assert out["resultMounted"]
    assert out["baselineVisible"]
    assert out["baselineText"] == "Stress-test baseline (separate stored plan)"
    assert out["generatedHiddenDuringStress"] and out["generatedRestored"] and out["baselineRemoved"]
    assert "pl-plan-families" in out["planHtml"] and "pl-plan-counter" not in out["planHtml"]
    assert "pl-plan-counter" in out["resultHtml"] and "Regenerate" in out["resultHtml"]
    assert out["resultHtml"].count("</span> · <span") == 2
    assert out["planElapsed"] <= 11000
    assert out["stressElapsed"] <= 12000
    assert all(set(update) == {"stability", "list", "draught", "hull"} for update in out["stressMeters"])
    assert any(c["fn"] == "enableDrag" and c["opts"] == {"allowSwap": True} for c in out["calls"])
    assert any(c["fn"] == "updateBoxes" and c["overrides"] == planner_payloads["docs"]["stress"]["cargo_overrides"] for c in out["calls"])
    assert any(c["fn"] == "resetBoxes" for c in out["calls"])
    passing = next(c for c in planner_payloads["docs"]["stress"]["cases"] if c["full"]["legal"])
    assert plays[-1]["swap_with"] == passing["full"]["gauges"]["swapped_box"]
    assert out["done"] == 2 and out["doneAt"] == [0, 2]
    first = next(e for e in out["events"] if e["name"] == "stress:verdict")
    assert first["time"] - out["stressStart"] <= 6000
    assert [e["value"]["verdict"]["legal"] for e in out["events"] if e["name"] == "stress:verdict"] == [False, True]
    assert any(c["fn"] == "markViolation" and c["text"] for c in out["calls"])
    assert out["dragDisabled"]
    assert [call for call in out["calls"] if call["fn"] == "setPlan"][-1]["id"] == planner_payloads["docs"]["plan-after"]["plan_id"]
    if planner_payloads["generated"]:
        expected = {row["constraint"]["bay"] for row in planner_payloads["docs"]["plan-after"]["rule"]["canonical_rules"]
                    if row["constraint"]["type"] == "close_bay"}
        assert {call["bay"] for call in out["calls"] if call["fn"] == "openBay"} == expected
        assert {call["bay"] for call in out["calls"] if call["fn"] == "closeBay"} == expected


@needs_node
def test_result_strip_is_two_rows_with_explicit_summary_separators(planner_payloads):
    out = node(HARNESS, planner_payloads)

    class TopLevel(HTMLParser):
        def __init__(self):
            super().__init__()
            self.depth = 0
            self.classes = []

        def handle_starttag(self, tag, attrs):
            if self.depth == 0:
                self.classes.append(dict(attrs).get("class", ""))
            self.depth += 1

        def handle_endtag(self, tag):
            self.depth -= 1

    parser = TopLevel()
    parser.feed(out["resultHtml"])
    assert parser.classes == ["pl-plan-result-summary", "pl-plan-provenance pl-plan-meta"]
    assert out["resultHtml"].count("</span> · <span") == 2
    css = (STATIC / "planner-plan.css").read_text()
    summary = re.search(r"\.pl-plan-result-summary\s*\{([^}]*)\}", css).group(1)
    assert "flex-wrap: nowrap" in summary and "white-space: nowrap" in summary


@needs_node
def test_leave_cancels_pending_verdict_and_autoplay(planner_payloads):
    out = node(HARNESS, {**planner_payloads, "cancel": True})
    assert out["done"] == 1
    assert len(out["drags"]) <= 1
    assert out["dragDisabled"]


@needs_node
@pytest.mark.parametrize("mode", ["earlyLeave", "deadline"])
def test_interrupted_plan_lands_and_publishes_final_meters(planner_payloads, mode):
    out = node(HARNESS, {**planner_payloads, mode: True})
    total = len(planner_payloads["docs"]["moves"]["moves"])
    assert out["plan"]["plan_id"] == planner_payloads["docs"]["plan-after"]["plan_id"]
    assert len(out["plan"]["slots"]) == len(planner_payloads["docs"]["plan-after"]["slots"])
    assert out["meters"][-1]["moves"] == {"at": total, "total": total}
    assert out["meters"][-1]["rules"]["state"] == "ok"
    assert "Pending" not in out["meters"][-1]["rules"]["value"]
    assert out["done"] == 1 and out["timers"] == 0
    assert [e["name"] for e in out["events"]] == ["plan:replanned"]


@needs_node
def test_leaving_checks_before_generate_does_not_generate_a_plan(planner_payloads):
    out = node(HARNESS, {**planner_payloads, "leaveBeforeStart": True})
    assert out["done"] == 0
    assert not any(c["fn"] == "play" for c in out["calls"])


@needs_node
def test_regenerate_reactivates_checks_after_another_tab(planner_payloads):
    out = node(HARNESS, {**planner_payloads, "regenerate": True})
    assert {"fn": "go", "id": "plan"} in out["calls"]
    assert len([call for call in out["calls"] if call["fn"] == "play"]) == 2
    assert out["done"] == 1
    assert out["plan"]["plan_id"] == planner_payloads["docs"]["plan-after"]["plan_id"]


def test_generated_files_match_required_slot_schema_when_available(planner_payloads):
    docs = planner_payloads["docs"]
    for name in ("plan-before", "plan-after"):
        assert docs[name]["label"] and docs[name]["source"]
        assert all({"bay", "row", "tier", "box_id", "deck"} <= row.keys() for row in docs[name]["slots"])
    assert all({"id", "box_id", "from", "to", "fast", "full", "rule", "label"} <= case.keys() for case in docs["stress"]["cases"])


def test_recorded_passing_case_requires_an_occupied_slot_swap(planner_payloads):
    stress = planner_payloads["docs"]["stress"]
    passing = next(case for case in stress["cases"] if case["full"]["legal"])
    destination = next(row for row in stress["slots"] if all(row[key] == passing["to"][key] for key in ("bay", "row", "tier")))
    assert destination["box_id"] == passing["full"]["gauges"]["swapped_box"]
    assert destination["box_id"] != passing["box_id"]


@needs_node
def test_actual_bayplan_accepts_and_animates_the_recorded_swap(planner_payloads):
    out = node(r"""
      const fs=require('node:fs'),path=require('node:path'),input=JSON.parse(fs.readFileSync(0,'utf8'));
      const root=path.resolve(input.static,'../..');
      const {host}=require(path.join(root,'app/tests/fixtures/planner/tiny-dom.cjs')).install();
      require(input.static+'/planner-bayplan.js');
      const bp=BayPlan.create(host,{ship:input.docs.ship,boxes:input.docs.boxes.boxes,pods:input.docs.boxes.pods});
      const stress=input.docs.stress,c=stress.cases.find(c=>c.full.legal),drops=[];
      bp.updateBoxes(stress.cargo_overrides);bp.setPlan(stress);bp.showBays([c.from.bay,c.to.bay]);
      bp.enableDrag(d=>drops.push(d),{allowSwap:true});
      const box=host.querySelectorAll('[data-box-id="'+c.box_id+'"]').at(-1),svg=host.querySelector('svg');
      if(!box)throw Error('Prepared source box is not drawn');
      const a=bp.screenPoint(c.from),b=bp.screenPoint(c.to);
      box.dispatchEvent({type:'pointerdown',pointerId:1,button:0,clientX:a.x,clientY:a.y});
      svg.dispatchEvent({type:'pointermove',pointerId:1,buttons:1,clientX:b.x,clientY:b.y});
      svg.dispatchEvent({type:'pointerup',pointerId:1,button:0,clientX:b.x,clientY:b.y});
      if(drops.length!==1)throw Error('Occupied prepared target did not produce a real drop');
      (async()=>{
        const d=drops[0];await bp.play([{...d,kind:'shift'}],{speed:100000});
        const final=bp.getPlan();bp.resetBoxes();
        console.log(JSON.stringify({drop:d,count:final.slots.length,
          moved:final.slots.find(s=>s.box_id===c.box_id),swapped:final.slots.find(s=>s.box_id===c.full.gauges.swapped_box)}));
      })().catch(e=>{console.error(e);process.exitCode=1;});
    """, planner_payloads)
    passing = next(c for c in planner_payloads["docs"]["stress"]["cases"] if c["full"]["legal"])
    assert out["drop"]["swap_with"] == passing["full"]["gauges"]["swapped_box"]
    assert out["count"] == len(planner_payloads["docs"]["stress"]["slots"])
    for key in ("bay", "row", "tier"):
        assert out["moved"][key] == passing["to"][key]
        assert out["swapped"][key] == passing["from"][key]

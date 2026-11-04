"""Planner Copilot and Handover: payloads, 2D bay inspection, sheets and live-check plumbing. No browser."""
from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import v6

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app/static"
NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture(scope="module")
def payloads():
    result = {"copilot": v6.feature(v6.copilot), "package": v6.feature(v6.package)}
    assert all(data["available"] for data in result.values()), result
    return result


@pytest.fixture(scope="module")
def planner_data():
    names = ("ship", "boxes", "plan-before", "plan-after", "moves", "stress")
    return {name: json.loads((STATIC / "planner-data" / f"{name}.json").read_text())
            for name in names if (STATIC / "planner-data" / f"{name}.json").is_file()}


def node(code, data=None):
    result = subprocess.run(["node", "-e", code, str(STATIC)], input=json.dumps(data),
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def workflow(payloads, planner_data, name, **options):
    return node(HARNESS, {"name": name, "payloads": payloads, "planner_data": planner_data, **options})


@NODE
def test_registration_without_dom():
    code = (
        "const defs=[]; global.window={Planner:{stage:d=>defs.push(d)}};"
        "for(const n of ['copilot','handover'])require(process.argv[1]+'/planner-'+n+'.js');"
        "console.log(JSON.stringify(defs.map(d=>({id:d.id,order:d.order,title:d.title,"
        "features:d.features,mount:typeof d.mount}))));"
    )
    assert node(code) == [
        dict(id="copilot", order=50, title="Copilot", features=["copilot"], mount="function"),
        dict(id="handover", order=60, title="Handover", features=["package"], mount="function"),
    ]


@pytest.mark.parametrize("name", ["copilot", "handover"])
def test_modules_are_isolated_local_and_two_dimensional(name):
    js = (STATIC / f"planner-{name}.js").read_text()
    css = (STATIC / f"planner-{name}.css").read_text()
    assert not re.search(r"https?://|@import\b|\b(?:WebSocket|EventSource)\s*\(", js + css)
    assert not re.search(r"window\.__stage|ctx\.stage\b|ctx\.center\b|\bStudio\b|\bTHREE\b", js)
    classes = set(re.findall(r"\.([A-Za-z_][\w-]*)", " ".join(re.findall(r"([^{}]+)\{", css))))
    assert all(c.startswith(f"pl-{name}-") or c in {"primary", "secondary", "ghost", "ok", "warn", "bad", "fail"}
               for c in classes), classes
    assert "var(--pl-" in css
    assert not re.search(r"gradient\(|box-shadow\s*:.*(?:glow|0 0)", css)


@NODE
def test_copilot_claims_question_tools_and_bay_inspection(payloads, planner_data):
    out = workflow(payloads, planner_data, "copilot", inspect_bays=True)
    proof, take = payloads["copilot"]["proved"], payloads["copilot"]["delivered"]
    assert out["question"] == "\n".join(proof["sentences"])
    assert out["doneBeforeStart"] == 0 and out["doneAfterStart"] == 1 and out["done"] == 1
    assert "Software · proved by arithmetic" in out["html"]
    assert f'{proof["equation"]}, {proof["shortfall"]} short' in out["html"]
    context = "In a recorded take on a stricter request, the copilot negotiated two checked alternatives"
    assert context in out["html"]
    assert f'Recorded answer · real tools · {take["scripted_replies_said"]} scripted planner replies; reader here is cloud Spark' in out["html"]
    assert take["request_equation"] in out["html"]
    assert all(s in out["html"] for s in take["request"])
    assert all(card["sentence"] in out["html"] for card in take["cards"])
    assert "Full checker ✓" in out["html"] and "What-if · not applied" in out["html"]
    assert "Scripted planner confirmation · recorded take" in out["html"]
    assert "Details" in out["html"] and "Spark's raw text gets strict flags" in out["html"]
    tools = [step for step in take["steps"] if step["kind"] == "tool"]
    assert len([log for log in out["logs"] if log["label"] == take["label"]]) == len(tools)
    assert not out["calls"], "The tools are recorded, no fresh model/tool execution is claimed"
    expected = sorted({int(bay) for card in take["cards"] for bay in re.findall(r"\bbay\s+0*(\d+)\b", card["sentence"], re.I)})
    assert out["bayCalls"] == [["showBays", expected]]
    assert not out["sheetCalls"], "Copilot stays beside the 2D bay plan"


@NODE
def test_copilot_ask_button_and_focus_fallback(payloads, planner_data):
    out = workflow(payloads, planner_data, "copilot", manual_start=True, inspect_bays=True, focus_only=True)
    assert out["done"] == 1
    assert out["bayCalls"] and all(call[0] == "focusBay" for call in out["bayCalls"])


@NODE
def test_shell_types_and_clicks_the_single_module_question_and_ask(payloads, planner_data):
    out = workflow(payloads, planner_data, "copilot", shell_ask=True)
    question = "\n".join(payloads["copilot"]["proved"]["sentences"])
    assert out["exposedControls"] == {
        "question": question, "questionInput": True, "askButton": True,
        "controls": True, "ownsAction": True, "globalQuestion": question,
    }
    assert out["questionBoxes"] == 1 and out["askButtons"] == 1
    assert out["doneBeforeClick"] == 0 and out["done"] == 1
    assert len(out["clicks"]) == 1, "Autoplay must not ask again after the shell clicked Ask"


@NODE
def test_handover_sheet_and_live_workflow(payloads, planner_data):
    out = workflow(payloads, planner_data, "handover", reopen_sheet=True)
    assert out["doneBeforeStart"] == out["doneAfterStart"] == 0
    assert out["done"] == 1 and out["callsAfterStart"] == 0
    assert out["sheetCalls"][:3] == ["open", "close", "open"]
    assert not out["afterLeave"]
    assert [call["body"] for call in out["calls"] if call.get("name") == "verify"] == [{"tamper": False}, {"tamper": True}]
    assert {call["url"] for call in out["calls"] if "url" in call} == {"/api/v6/verify/clean", "/api/v6/verify/bad"}
    assert all(call["options"]["cache"] == "no-store" for call in out["calls"] if "url" in call)
    assert out["trail"].index("package:ready") < out["trail"].index("post:verify") < out["trail"].index("post:tamper") < out["trail"].index("done")
    for title in ("Bay plan", "Crane sequence", "Stability summary", "Note to the master"):
        assert title in out["html"]
    assert payloads["package"]["signature_note"] in out["html"]
    assert "Live verify, ran here" in out["html"]
    assert "plan.json: sha256 mismatch" in out["html"]
    assert "Current app: " + payloads["package"]["receipt"]["build_line"] in out["html"]
    assert "Stored package: built on a frozen pipeline." in out["html"]
    assert not out["bayCalls"], "Document handover does not change the bay plan"
    assert all(len(state) <= 1 for state in out["actionStates"])
    assert out["primaryAfterStart"] == ["Verify receipt"]
    assert not out["primaryAtEnd"]


@NODE
@pytest.mark.parametrize("scenario", [
    {"wrong_digest": True}, {"network_failure": True}, {"tamper_passes": True}, {"tamper_stopped": True},
])
def test_failed_receipt_demonstration_never_completes(payloads, planner_data, scenario):
    out = workflow(payloads, planner_data, "handover", **scenario)
    assert out["done"] == 0


@NODE
def test_build_button_opens_documents_without_finishing_verification(payloads, planner_data):
    out = workflow(payloads, planner_data, "handover", manual_start=True, start_only=True)
    assert out["sheetCalls"][0] == "open" and out["done"] == 0 and not out["calls"]


@NODE
def test_export_tab_reentry_waits_for_explicit_document_action(payloads, planner_data):
    out = workflow(payloads, planner_data, "handover", start_only=True, passive_reentry=True)
    assert out["sheetCalls"].count("open") == 1, "Changing review tabs must not reopen documents"
    assert not out["beforeLeave"] and not out["calls"] and out["done"] == 0


@NODE
def test_package_and_current_app_build_hashes_stay_distinct(payloads, planner_data):
    data = copy.deepcopy(payloads)
    receipt = data["package"]["receipt"]
    receipt["pipeline_sha256"], receipt["build_line"] = "f" * 64, "pipeline " + "f" * 12
    out = workflow(data, planner_data, "handover", start_only=True)
    assert f'Stored package pipeline sha</dt><dd class="pl-handover-mono">{receipt["reader"]["pipeline"]}' in out["html"]
    assert f'Current app: {receipt["build_line"]}' in out["html"]


def test_generated_planner_bays_match_referenced_alternatives(payloads, planner_data):
    if "ship" not in planner_data:
        pytest.skip("Planner data generator has not written ship.json yet; runtime uses fixture BayPlan")
    assert planner_data["ship"]["row_order"] == "port_to_starboard"
    bays = {int(bay["bay"]) for bay in planner_data["ship"]["bays"]}
    references = {int(bay) for card in payloads["copilot"]["delivered"]["cards"]
                  for bay in re.findall(r"\bbay\s+0*(\d+)\b", card["sentence"], re.I)}
    assert references <= bays
    assert planner_data["ship"]["source"]


HARNESS = r"""

const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const reads = new Set();
function guard(value, path = 'data') {
  if (!value || typeof value !== 'object') return value;
  return new Proxy(value, {get(target, key) {
    if (typeof key === 'symbol' || key === 'then' || key === 'toJSON' || key in Object.prototype || Array.isArray(target) && key in Array.prototype)
      return Reflect.get(target, key);
    if (!(key in target)) throw Error('Missing payload field ' + path + '.' + key);
    reads.add(path + '.' + key);
    return guard(target[key], path + '.' + key);
  }});
}
const elements = [];
const htmlHistory = [];
class El {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.dataset = {}; this.style = {setProperty(){}};
    this.hidden = false; this.disabled = false; this.value = ''; this.textContent = '';
    this.className = ''; this.children = []; this.listeners = {}; this.found = new Map();
    this.scrollHeight = 100; this.scrollTop = 0; this.attributes = {}; this.html = '';
    const names = () => new Set(this.className.split(/\s+/).filter(Boolean));
    this.classList = {
      add:(...values)=>{const n=names();values.forEach(v=>n.add(v));this.className=[...n].join(' ');},
      remove:(...values)=>{const n=names();values.forEach(v=>n.delete(v));this.className=[...n].join(' ');},
      toggle:(value, force)=>{const n=names(), add=force===undefined?!n.has(value):force;
        add?n.add(value):n.delete(value);this.className=[...n].join(' ');return add;},
      contains:value=>names().has(value),
    };
    elements.push(this);
  }
  set innerHTML(s) {this.html = String(s); htmlHistory.push(this.html); this.found.clear();}
  get innerHTML() {return this.html;}
  querySelector(s) {
    if (!this.found.has(s)) {
      const node = new El();
      if (s.startsWith('.')) {
        const name=s.slice(1);
        for(const match of this.html.matchAll(/<(\w+)\b([^>]*)>/g)) {
          const classes=(match[2].match(/class="([^"]*)"/)||[])[1]||'';
          if (!classes.split(/\s+/).includes(name)) continue;
          node.tagName=match[1].toUpperCase();node.className=classes;
          node.hidden=/\bhidden\b/.test(match[2]);node.disabled=/\bdisabled\b/.test(match[2]);
          node.textContent=this.html.slice(match.index+match[0].length).split('</'+match[1]+'>')[0].replace(/<[^>]+>/g,'');
          break;
        }
      }
      node.parentNode=this;this.found.set(s,node);
    }
    return this.found.get(s);
  }
  querySelectorAll(s) {
    if (s === '.pl-handover-document') {
      if (!this.cards) this.cards = Array.from({length:4}, () => new El('article'));
      return this.cards;
    }
    return [];
  }
  appendChild(el) {this.children.push(el); el.parentNode = this; return el;}
  append(...els) {els.forEach(el => this.appendChild(el));}
  replaceChildren(...els) {this.children = []; this.append(...els);}
  addEventListener(name, fn) {(this.listeners[name] ||= []).push(fn);}
  dispatchEvent(event) {(this.listeners[event.type] || []).forEach(fn=>fn(event)); return true;}
  removeEventListener() {}
  setAttribute(k,v) {this.attributes[k]=v;}
  getAttribute(k) {return this.attributes[k];}
  removeAttribute(k) {delete this.attributes[k];}
  insertAdjacentHTML(_where, text) {this.html += text; htmlHistory.push(this.html);}
  focus() {} remove() {} scrollIntoView() {} scrollTo() {}
  getBoundingClientRect() {return {width:1000,height:650,top:0,left:0};}
  async click() {
    if (this.disabled) return;
    for (const fn of this.listeners.click || []) await fn({target:this, preventDefault(){}});
    if (this.tagName === 'BUTTON' && this.type === 'submit') {
      for (let parent = this.parentNode; parent; parent = parent.parentNode) {
        if (parent.tagName !== 'FORM') continue;
        for (const fn of parent.listeners.submit || []) await fn({target:parent, preventDefault(){}});
        break;
      }
    }
  }
}
const realTimer = global.setTimeout;
global.setTimeout = (fn, ms) => realTimer(fn, Math.min(ms || 0, 1));
global.requestAnimationFrame = fn => realTimer(() => fn(performance.now()), 1);
global.cancelAnimationFrame = clearTimeout;
global.document = {createElement: tag => new El(tag), createTextNode: text => ({textContent:text}),
  body:new El('body'), querySelector: () => null, addEventListener(){}};
let definition;

const bayCalls = [], sheetCalls = [];
const ship = input.planner_data.ship || {bays:[]};
const validBay = value => {if (ship.bays.length && !ship.bays.some(b => Number(b.bay) === Number(value))) throw Error('Unknown bay '+value);};
const bay = {showBays:values=>{values.forEach(validBay);bayCalls.push(['showBays',values]);}, focusBay:value=>{validBay(value);bayCalls.push(['focusBay',value]);}};
if (input.focus_only) delete bay.showBays;
global.window = {Planner:{stage:d => definition=d}, BayPlan:{create:()=>bay}, matchMedia:()=>({matches:false})};

const calls = [], logs = [], events = [], waits = [], trail = [], clicks = [], actionStates = [];
function actionState() {
  return elements.filter(e=>e.tagName==='BUTTON' && !e.hidden && !e.disabled && e.classList.contains('primary'))
    .map(e=>e.textContent);
}
let done = 0, center = null;
const clone = x => JSON.parse(JSON.stringify(x));
function result(tamper) {
  const job = clone(input.payloads.package.verification[tamper ? 'tamper' : 'verify']);
  if (tamper && input.tamper_passes) {
    job.passed = true;
    job.checks.forEach(row => {row.state = 'passed'; row.errors = [];});
  }
  if (tamper && input.tamper_stopped) job.status = 'stopped';
  return {...job, id:tamper?'bad':'clean', live:true,
    package_sha256:tamper?null:input.wrong_digest?'different-package':input.payloads.package.package_sha256};
}
global.fetch = async (url, options) => {
  calls.push({url, options});
  trail.push(url);
  if (!/^\/api\/v6\/verify\/(clean|bad)$/.test(url)) throw Error('Unexpected URL ' + url);
  if (input.network_failure) throw Error('Connection unavailable');
  return {ok:true, json:async()=>guard(result(url.endsWith('/bad')), 'job')};
};
const ctx = {
  esc:s=>String(s ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  chip:(s,k)=>'<span class="st-chip '+k+'">'+String(s)+'</span>',
  api:async name=>{throw Error('Unexpected cached API read '+name);},
  post:async (name, body)=>{
    calls.push({name,body}); if (name!=='verify') throw Error('Unexpected POST '+name);
    trail.push(body.tamper ? 'post:tamper' : 'post:verify');
    return guard({...result(body.tamper),status:'running',passed:null},'job');
  },
  asset:name=>'/api/v6/asset/'+name, state:{}, demo:false,
  sheet:node=>{center=node;sheetCalls.push(node?'open':'close');}, bay, log:x=>logs.push(x), emit:(...x)=>{events.push(x);trail.push(x[0]);},
  done:()=>{done++;trail.push('done');}, toast(){}, wait:async ms=>{waits.push(ms);},
  click:async el=>{if (typeof el === 'string') throw Error('Autoplay should resolve selector against its host');
    clicks.push(el.className+' '+el.textContent);
    actionStates.push(actionState());
    await el.click(); await new Promise(r=>realTimer(r, 25));},
};
require(process.argv[1] + '/planner-' + input.name + '.js');
(async()=>{
 const panel = new El();
 const view = definition.mount(panel, guard(input.payloads), ctx);
 for(const method of ['enter','leave','start','autoplay']) if(typeof view[method]!=='function') throw Error('Missing '+method);
 await view.enter();
 const doneBeforeStart = done;
 
 let doneBeforeClick = done;
 if (input.shell_ask) {
   if (!view.questionInput || !view.askButton) throw Error('Missing module-owned Copilot controls');
   view.questionInput.value = '';
   for (const letter of view.question) view.questionInput.value += letter;
   view.questionInput.dispatchEvent({type:'input', target:view.questionInput});
   doneBeforeClick = done;
   await ctx.click(view.askButton);
 } else if (input.manual_start) {
   const first = input.name === 'copilot' ? elements.find(e=>e.tagName==='BUTTON' && e.textContent==='Ask') : panel.querySelector('.pl-handover-build');
   if (!first) throw Error('Missing primary action');
   await ctx.click(first);
 } else await view.start();
 await new Promise(r=>realTimer(r, 40));

 const doneAfterStart = done;
 const callsAfterStart = calls.length;
 const primaryAfterStart = actionState();
 if (input.passive_reentry) {
   await ctx.click(center.querySelector('.pl-handover-close'));
   await view.leave();
   await view.enter();
 }
 
 if (input.reopen_sheet) {
   const doc = center;
   await ctx.click(doc.querySelector('.pl-handover-close'));
   if (center !== null) throw Error('Sheet did not close');
   await ctx.click(panel.querySelector('.pl-handover-build'));
 }
 if (!input.start_only) await view.autoplay(ctx);
 if (input.inspect_bays) {
   for (const button of elements.filter(e=>e.tagName==='BUTTON' && e.textContent==='Show referenced bay')) await ctx.click(button);
 }

 await new Promise(r=>realTimer(r, 40));
 const beforeLeave = center !== null;
 await view.leave();
 const visibleText = el => el.attributes['aria-hidden'] === 'true' ? '' : el.textContent + el.children.map(visibleText).join('');
 console.log(JSON.stringify({reads:[...reads], calls, logs, events, done, waits, beforeLeave, afterLeave:center !== null,
   doneBeforeStart, doneAfterStart, callsAfterStart, trail, clicks, actionStates, primaryAfterStart,
   primaryAtEnd:actionState(), question:elements.find(e=>e.tagName==='TEXTAREA')?.value, bayCalls, sheetCalls,
   doneBeforeClick,
   questionBoxes:elements.filter(e=>e.tagName==='TEXTAREA').length,
   askButtons:elements.filter(e=>e.className.split(/\s+/).includes('pl-copilot-ask')).length,
   exposedControls:input.name==='copilot'?{
     question:view.question, questionInput:view.questionInput===elements.find(e=>e.tagName==='TEXTAREA'),
     askButton:!!view.askButton && view.askButton.classList.contains('pl-copilot-ask'),
     controls:!!view.controls && view.controls.question===view.questionInput && view.controls.ask===view.askButton,
     ownsAction:view.ownsAction, globalQuestion:window.Planner.copilotQuestion,
   }:null,
   messages:elements.filter(e=>e.className.split(/\s+/).includes('pl-copilot-message'))
     .map(e=>({speaker:e.children.length>1?visibleText(e.children[0]):'', text:e.children[e.children.length-1].textContent})),
   htmlHistory,
   html:elements.map(e=>e.innerHTML+' '+e.textContent).join('\n')}));
})().catch(error=>{console.error(error);process.exitCode=1;});

"""

"""Studio Copilot/Handover contract checks, using real payloads and Node, without a browser."""
from __future__ import annotations

import json
import copy
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app import v6

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app/static"
NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture(scope="module")
def payloads():
    data = {"copilot": v6.feature(v6.copilot), "package": v6.feature(v6.package)}
    assert all(d["available"] for d in data.values()), data
    return data


def run_node(code, data=None):
    result = subprocess.run(
        ["node", "-e", code, str(STATIC)], input=json.dumps(data),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@NODE
def test_modules_register_without_a_dom():
    result = run_node("""
      const defs = [];
      global.window = {Studio: {step: d => defs.push(d)}};
      for (const name of ['copilot', 'handover']) require(process.argv[1] + '/studio-' + name + '.js');
      console.log(JSON.stringify(defs.map(d => ({id:d.id, order:d.order, title:d.title,
        icon:d.icon, features:d.features, ship:d.ship, mount:typeof d.mount}))));
    """)
    assert result == [
        dict(id="copilot", order=50, title="Copilot", icon="copilot", features=["copilot"], ship=True, mount="function"),
        dict(id="handover", order=60, title="Handover", icon="handover", features=["package"], ship=False, mount="function"),
    ]


@pytest.mark.parametrize("name", ["copilot", "handover"])
def test_css_stays_scoped_and_leaves_visuals_to_v2_shell(name):
    css = (STATIC / f"studio-{name}.css").read_text()
    assert f".st-{name}-" in css
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(", css)
    assert not re.search(
        r"(?:^|[;{])\s*(?:color|background(?:-[\w-]+)?|border(?:-[\w-]+)?|font(?:-[\w-]+)?|"
        r"letter-spacing|text-transform|box-shadow|outline(?:-[\w-]+)?)\s*:", css
    ), "Module CSS owns layout; shared shell classes own visual styling"
    variables = set(re.findall(r"var\((--[\w-]+)", css))
    assert variables <= {f"--st-{token}" for token in (
        "bg", "surface", "surface-2", "frame", "side", "line", "line-strong", "text", "text-2", "muted",
        "white", "green", "orange", "red", "blue", "radius", "font", "mono"
    )}
    # Every class selector belongs to this module or is a shared contract class.
    selectors = " ".join(re.findall(r"([^{}]+)\{", css))
    classes = set(re.findall(r"\.([A-Za-z_][\w-]*)", selectors))
    assert all(c.startswith(f"st-{name}-") or c in {
        "st-card", "st-card-title", "st-card-sub", "st-btn", "primary", "secondary", "ghost", "danger", "st-chip",
        "recorded", "stored", "live", "projection", "synthetic", "cloud", "device", "pass", "fail", "warn",
        "st-kv", "st-mono", "st-meta", "st-disclosure", "st-table", "st-grid2", "st-big", "st-callout", "st-tile", "sm", "ok", "bad", "big",
    } for c in classes), classes


@pytest.mark.parametrize("name", ["copilot", "handover"])
def test_modules_are_local_only(name):
    source = "\n".join((STATIC / f"studio-{name}.{ext}").read_text() for ext in ("js", "css"))
    assert not re.search(r"https?://|@import\b|fonts\.google|\b(?:WebSocket|EventSource)\s*\(", source)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(", source), "Module colours come from theme tokens"
    assert not re.search(r"<h[12]\b|<textarea\b|<form\b|el\(['\"](?:h[12]|textarea|form)['\"]", source)


# This small DOM substitute exercises event handlers and autoplay, not layout. Every
# payload object is guarded, so even an optional read of an invented field fails.
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
    if (s === '.st-handover-document') {
      if (!this.cards) this.cards = Array.from({length:4}, () => new El('article'));
      return this.cards;
    }
    return [];
  }
  appendChild(el) {this.children.push(el); el.parentNode = this; return el;}
  append(...els) {els.forEach(el => this.appendChild(el));}
  replaceChildren(...els) {this.children = []; this.append(...els);}
  addEventListener(name, fn) {(this.listeners[name] ||= []).push(fn);}
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
global.window = {Studio:{step:d => definition=d}, matchMedia:()=>({matches:false})};
const calls = [], logs = [], events = [], waits = [], trail = [], clicks = [], actionStates = [];
function actionState() {
  return elements.filter(e=>e.tagName==='BUTTON' && !e.hidden && !e.disabled && e.classList.contains('primary'))
    .map(e=>e.textContent);
}
let done = 0, center = null;
const clone = x => JSON.parse(JSON.stringify(x));
const jobs = {};
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
  asset:name=>'/api/v6/asset/'+name, stage:{}, state:{}, demo:false,
  center:node=>{center=node;}, log:x=>logs.push(x), emit:(...x)=>{events.push(x);trail.push(x[0]);},
  done:()=>{done++;trail.push('done');}, toast(){}, wait:async ms=>{waits.push(ms);},
  click:async el=>{if (typeof el === 'string') throw Error('Autoplay should resolve selector against its host');
    clicks.push(el.className+' '+el.textContent);
    actionStates.push(actionState());
    await el.click(); await new Promise(r=>realTimer(r, 25));},
};
require(process.argv[1] + '/studio-' + input.name + '.js');
(async()=>{
 const panel = new El();
 const view = definition.mount(panel, guard(input.payloads), ctx);
 for(const method of ['enter','leave','start','autoplay']) if(typeof view[method]!=='function') throw Error('Missing '+method);
 await view.enter();
 const doneBeforeStart = done;
 await view.start(); await new Promise(r=>realTimer(r, 40));
 const doneAfterStart = done;
 const callsAfterStart = calls.length;
 const primaryAfterStart = actionState();
 if (!input.start_only) await view.autoplay(ctx);
 await new Promise(r=>realTimer(r, 40));
 const beforeLeave = center !== null;
 await view.leave();
 const visibleText = el => el.attributes['aria-hidden'] === 'true' ? '' : el.textContent + el.children.map(visibleText).join('');
 console.log(JSON.stringify({reads:[...reads], calls, logs, events, done, waits, beforeLeave, afterLeave:center !== null,
   doneBeforeStart, doneAfterStart, callsAfterStart, trail, clicks, actionStates, primaryAfterStart,
   primaryAtEnd:actionState(), question:window.Studio.copilotQuestion,
   messages:elements.filter(e=>e.className.split(/\s+/).includes('st-copilot-message'))
     .map(e=>({speaker:e.children.length>1?visibleText(e.children[0]):'', text:e.children[e.children.length-1].textContent})),
   htmlHistory,
   html:elements.map(e=>e.innerHTML+' '+e.textContent).join('\n')}));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""


@NODE
def test_copilot_real_fields_and_recorded_tools(payloads):
    out = run_node(HARNESS, {"name": "copilot", "payloads": payloads})
    assert out["done"] >= 1
    assert payloads["copilot"]["proved"]["equation"] in out["html"]
    assert "Stored refusal, proved by arithmetic" in out["html"]
    assert "Spark's raw text vs what software let through" in out["html"] or "Spark’s raw text vs what software let through" in out["html"]
    for card in payloads["copilot"]["delivered"]["cards"]:
        assert card["sentence"] in out["html"]
    assert "Full checker ✓" in out["html"]
    tools = [s for s in payloads["copilot"]["delivered"]["steps"] if s["kind"] == "tool"]
    assert all(any(step["name"] in log["text"] for log in out["logs"]) for step in tools)
    assert not out["calls"], "Copilot replays recorded tools, it never runs a new model request"
    assert out["question"] == "\n".join(payloads["copilot"]["proved"]["sentences"])
    assert out["doneBeforeStart"] == 0
    assert out["doneAfterStart"] == 1
    assert not out["clicks"], "The shell already sent the question and called start()"
    assert out["messages"][0] == {
        "speaker": "Software · proved by arithmetic",
        "text": "No, software proved this request impossible before planning.",
    }
    proof = payloads["copilot"]["proved"]
    take = payloads["copilot"]["delivered"]
    equation = f'{proof["equation"]}, {proof["shortfall"]} short'
    assert equation in out["html"]
    provenance = (
        f'Recorded answer · real tools · {take["scripted_replies_said"]} scripted planner replies; '
        'reader here is cloud Spark'
    )
    assert provenance in out["html"]
    context = "In a recorded take on a stricter request, the copilot negotiated two checked alternatives"
    assert context in out["html"]
    assert out["html"].index(equation) < out["html"].index(context) < out["html"].index(take["request_equation"])
    assert all(request in out["html"] for request in take["request"])
    assert "Spark's raw text gets strict flags, and software holds back every flagged line." in out["html"]
    assert "Scripted planner confirmation · recorded take" in out["html"]
    source = (STATIC / "studio-copilot.js").read_text()
    assert "chip('Muse Spark · cloud'" not in source
    assert "chip('Recorded answer'," not in source


@NODE
def test_handover_real_fields_and_live_verify(payloads):
    out = run_node(HARNESS, {"name": "handover", "payloads": payloads})
    assert out["beforeLeave"]
    assert not out["afterLeave"]
    assert out["done"] >= 1
    assert out["doneAfterStart"] == 0
    assert out["callsAfterStart"] == 0
    assert any(event[0] == "package:ready" for event in out["events"])
    posts = [call["body"] for call in out["calls"] if call.get("name") == "verify"]
    assert posts == [{"tamper": False}, {"tamper": True}]
    assert {call["url"] for call in out["calls"] if "url" in call} == {
        "/api/v6/verify/clean", "/api/v6/verify/bad"
    }
    assert payloads["package"]["signature_note"] in out["html"]
    assert "plan.json: sha256 mismatch" in out["html"]
    assert "Live verify, ran here" in out["html"]
    assert out["trail"].index('package:ready') < out["trail"].index('post:verify') < out["trail"].index('post:tamper') < out["trail"].index('done')
    assert len(out["clicks"]) == 2, "Autoplay continues with Verify and Tamper after start() builds"
    assert all(len(state) <= 1 for state in out["actionStates"])
    assert out["primaryAfterStart"] == ["Verify receipt"]
    assert not out["primaryAtEnd"], "Only the shell's next suggestion should remain primary"
    assert "Stored package: built on a frozen pipeline." in out["html"]
    assert f'Current app: {payloads["package"]["receipt"]["build_line"]}' in out["html"]
    assert "Stored package pipeline sha" in out["html"]
    assert "Current app pipeline sha" in out["html"]
    assert re.search(r'<p class="st-meta st-handover-plan-label">', out["html"])
    assert "Crane sequence, scroll horizontally for all columns" in out["html"]


@NODE
@pytest.mark.parametrize("scenario, message", [
    ({"wrong_digest": True}, "Verification ran on a different package"),
    ({"network_failure": True}, "Connection unavailable"),
])
def test_handover_never_passes_a_mismatched_or_unfinished_run(payloads, scenario, message):
    out = run_node(HARNESS, {"name": "handover", "payloads": payloads, **scenario})
    assert message in out["html"]
    assert not any("Verified in" in html or "all checks passed" in html for html in out["htmlHistory"])
    assert [call["body"] for call in out["calls"] if call.get("name") == "verify"] == [{"tamper": False}]
    assert out["done"] == 0


@NODE
def test_building_handover_does_not_complete_receipt_workflow(payloads):
    out = run_node(HARNESS, {"name": "handover", "payloads": payloads, "start_only": True})
    assert out["done"] == 0
    assert not out["calls"]
    assert any(event[0] == "package:ready" for event in out["events"])


@NODE
@pytest.mark.parametrize("scenario", [{"tamper_passes": True}, {"tamper_stopped": True}])
def test_tamper_must_finish_with_an_actual_checker_failure(payloads, scenario):
    out = run_node(HARNESS, {"name": "handover", "payloads": payloads, **scenario})
    assert out["done"] == 0


@NODE
def test_receipt_internals_are_inside_details(payloads):
    out = run_node(HARNESS, {"name": "handover", "payloads": payloads, "start_only": True})
    receipt = payloads["package"]["receipt"]
    needles = [receipt["plan_sha256"][:8], receipt["reader"]["adapter"][:8]]

    class DetailsCheck(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.found = set()

        def check(self, text):
            for needle in needles:
                if needle in text:
                    assert any(tag == "details" and disclosure for tag, disclosure in self.stack), text
                    self.found.add(needle)

        def handle_starttag(self, tag, attrs):
            if tag not in {"br", "hr", "img", "input", "meta", "link"}:
                self.stack.append((tag, "st-disclosure" in dict(attrs).get("class", "").split()))
            self.check(" ".join(value or "" for _, value in attrs))

        def handle_endtag(self, tag):
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    del self.stack[i:]
                    break

        def handle_data(self, text):
            self.check(text)

    check = DetailsCheck()
    for html in out["htmlHistory"]:
        check.feed(html)
    assert set(needles) <= check.found
    assert "Details" in out["html"]


@NODE
def test_stored_package_provenance_is_separate_from_current_app(payloads):
    data = copy.deepcopy(payloads)
    receipt = data["package"]["receipt"]
    receipt["pipeline_sha256"] = "f" * 64
    receipt["build_line"] = "pipeline " + "f" * 12
    out = run_node(HARNESS, {"name": "handover", "payloads": data, "start_only": True})
    assert f'Stored package pipeline sha</dt><dd class="st-mono">{receipt["reader"]["pipeline"]}' in out["html"]
    assert f'Current app pipeline sha</dt><dd class="st-mono" title="{"f" * 64}">{"f" * 12}' in out["html"]
    assert f'Current app: {receipt["build_line"]}' in out["html"]

"""Planner reports: sourced results, working controls, no browser or 3D runtime."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / 'app/static'


def node(script, payload=None):
    result = subprocess.run(['node', '-e', script], cwd=ROOT, input=json.dumps(payload),
                            text=True, capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout)


DOM = r"""
const walk = root => [root, ...root.children.flatMap(walk)];
const decode = text => text.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&');
const event = (type, target, extra={}) => ({type, target, bubbles:true, ...extra,
  preventDefault() {this.defaultPrevented=true;}, stopPropagation() {this.stopped=true;}});
class Element {
  constructor(tag) {
    if (tag === 'canvas') throw Error('Reports must not create a render canvas');
    this.tagName=tag.toLowerCase(); this.children=[]; this.dataset={}; this.style={};
    this.attributes={}; this.listeners={}; this.className=''; this._text='';
    this.classList={
      contains: item => this.className.split(/\s+/).includes(item),
      add: (...items) => {this.className=[...new Set(this.className.split(/\s+/).filter(Boolean).concat(items))].join(' ');},
      remove: (...items) => {this.className=this.className.split(/\s+/).filter(item=>!items.includes(item)).join(' ');},
      toggle: (item, force) => {const add=force===undefined?!this.classList.contains(item):force; this.classList[add?'add':'remove'](item); return add;}
    };
  }
  append(...nodes) {for (const item of nodes) {let node=item; if(typeof item==='string'){node=new Element('#text');node._text=item;} node.parentNode=this;this.children.push(node);}}
  appendChild(node) {this.append(node);return node;}
  replaceChildren(...nodes) {this.children=[];this._text='';this.append(...nodes);}
  set textContent(value) {this._text=String(value);this.children=[];}
  get textContent() {return this._text+this.children.map(node=>node.textContent).join('');}
  set innerHTML(html) {
    this.children=[];this._text=''; const stack=[this];
    for(const token of html.match(/<[^>]+>|[^<]+/g)||[]) {
      if(token.startsWith('</')) {if(stack.length>1)stack.pop();}
      else if(token.startsWith('<')) {
        const match=token.match(/^<([\w-]+)/);if(!match)continue;
        const el=new Element(match[1]);
        for(const attr of token.slice(match[0].length).matchAll(/([\w:-]+)(?:="([^"]*)"|='([^']*)'|=([^\s>]+))?/g))el.setAttribute(attr[1],decode(attr[2]??attr[3]??attr[4]??''));
        stack.at(-1).append(el);
        if(!/\/>$/.test(token)&&!['input','br','hr','img','meta','link'].includes(el.tagName))stack.push(el);
      } else {const el=new Element('#text');el._text=decode(token);stack.at(-1).append(el);}
    }
  }
  get firstElementChild() {return this.children.find(el=>el.tagName!=='#text');}
  setAttribute(key,value) {
    this.attributes[key]=String(value);
    if(key==='class')this.className=String(value);
    else if(key.startsWith('data-'))this.dataset[key.slice(5).replace(/-([a-z])/g,(_,s)=>s.toUpperCase())]=String(value);
    else if(['id','type','min','max','step','value'].includes(key))this[key]=String(value);
  }
  getAttribute(key) {return this.attributes[key]??null;}
  addEventListener(type,fn) {(this.listeners[type]||=[]).push(fn);}
  removeEventListener(type,fn) {this.listeners[type]=(this.listeners[type]||[]).filter(item=>item!==fn);}
  dispatchEvent(evt) {
    if(!evt.target)Object.defineProperty(evt,'target',{value:this,configurable:true});
    for(const fn of this.listeners[evt.type]||[])fn(evt);
    if(evt.bubbles&&!evt.stopped&&this.parentNode)this.parentNode.dispatchEvent(evt);
    return !evt.defaultPrevented;
  }
  click() {this.dispatchEvent(event('click',this));}
  matches(selector) {
    if(selector.startsWith('.'))return this.classList.contains(selector.slice(1));
    const attr=selector.match(/^\[([^=\]]+)(?:="?([^"\]]+)"?)?\]$/);
    if(attr)return attr[2]===undefined?this.getAttribute(attr[1])!==null:this.getAttribute(attr[1])===attr[2];
    return this.tagName===selector.toLowerCase();
  }
  querySelectorAll(selector) {return walk(this).slice(1).filter(el=>selector.split(',').some(part=>el.matches(part.trim())));}
  querySelector(selector) {return this.querySelectorAll(selector)[0]||null;}
  closest(selector) {for(let el=this;el;el=el.parentNode)if(el.matches(selector))return el;return null;}
  getBoundingClientRect() {return {left:0,top:0,width:400,height:32,right:400,bottom:32};}
  scrollIntoView() {}
  focus() {this.dispatchEvent(event('focus',this));}
}
const steps=[];
const forbidden=new Proxy({}, {get(){throw Error('Reports must not touch BayPlan');}});
global.BayPlan=forbidden;
global.document={createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag)};
global.window={Planner:{stage:stage=>steps.push(stage)},BayPlan:forbidden,addEventListener(){},removeEventListener(){}};
require('./app/static/planner-evidence.js');require('./app/static/planner-impact.js');
"""


class PlannerEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = {name: json.loads((STATIC / f'studio-data/{name}.json').read_text())
                    for name in ('evidence', 'impact')}

    def test_stage_registration_and_syntax(self):
        registered = node(DOM + "process.stdout.write(JSON.stringify(steps.map(s=>({id:s.id,order:s.order,features:s.features,ship:s.ship,mount:typeof s.mount}))));")
        self.assertEqual(registered, [
            {'id':'evidence','order':70,'features':[],'ship':False,'mount':'function'},
            {'id':'impact','order':80,'features':[],'ship':False,'mount':'function'},
        ])
        for name in ('evidence','impact'):
            result = subprocess.run(['node','--check',str(STATIC / f'planner-{name}.js')],
                                    capture_output=True,text=True,timeout=30,check=False)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_projection_port_matches_current_python_calculator(self):
        path = ROOT / 'experiments/impact-estimate/impact_v6.py'
        spec = importlib.util.spec_from_file_location('planner_impact_reference',path)
        reference = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reference)
        expected = reference.calculate()['pilot']
        actual = node("""
          const fs=require('fs');const data=JSON.parse(fs.readFileSync(0,'utf8'));
          const {project}=require('./app/static/planner-impact.js');
          process.stdout.write(JSON.stringify(Object.fromEntries(Object.entries(data.pilot).map(([name,row])=>[name,project(row.inputs)]))));
        """,self.data['impact'])
        for case, result in actual.items():
            self.assertEqual(result['annual_calls'],expected[case]['annual_calls'])
            for period in ('per_call','annual'):
                self.assertEqual(set(result[period]),set(expected[case][period]))
                for key, value in result[period].items():
                    with self.subTest(case=case,period=period,key=key):
                        self.assertAlmostEqual(value,expected[case][period][key],places=8)
                        self.assertEqual(round(value*100),round(expected[case][period][key]*100))

    def test_reports_real_data_lifecycle_drawers_and_autoplay(self):
        actual = node(DOM + r"""
          const fs=require('fs');const fixtures=JSON.parse(fs.readFileSync(0,'utf8'));
          (async()=>{
            const records=[];
            for(const stage of steps) {
              const panel=new Element('section');let sheet=null;let inAutoplay=false;
              const calls={load:0,done:0,log:0,wait:0,drag:0};const clicks=[],closedAtWait=[];
              const ctx={
                data:async name=>{calls.load++;if(!fixtures[name])throw Error('Unexpected data '+name);return fixtures[name];},
                sheet:node=>{sheet=node;},done:()=>calls.done++,log:()=>calls.log++,
                wait:async ms=>{calls.wait+=ms;if(inAutoplay)closedAtWait.push(sheet===null);},
                click:async el=>{if(!el)throw Error('Missing autoplay target');clicks.push(el.tagName);el.click();},
                drag:async()=>{calls.drag++;},
                get bay(){throw Error('Reports must not use ctx.bay');},
                api(){throw Error('Report unexpectedly used v6 API');},post(){throw Error('Report unexpectedly posted');}
              };
              const view=stage.mount(panel,{},ctx);
              if(typeof view.start!=='function')throw Error('Missing start');
              await Promise.all([view.enter(),view.start(),view.start()]);await view.enter();
              const record={id:stage.id,load:calls.load,done:calls.done,log:calls.log,scope:panel.textContent.includes(fixtures[stage.id].scope),rawPaths:panel.textContent.includes('experiments/')};
              if(stage.id==='evidence') {
                record.labels=Object.fromEntries(['original','extension','baseline','partial_run_short'].map(key=>[key,panel.textContent.includes(fixtures.evidence.labels[key])]));
                record.cards=panel.querySelectorAll('[data-pl-evidence-card]').length;
                record.svgCount=panel.querySelectorAll('svg').length;
                panel.querySelector('[data-pl-evidence-details="voyages"]').click();
                record.drawerSource=sheet.textContent.includes('experiments/');record.drawerN=sheet.textContent.includes('24');
                record.drawerInterval=sheet.textContent.includes('95%')||sheet.textContent.includes('95 %');
                const hover=[];
                for(const card of panel.querySelectorAll('[data-pl-evidence-card]')){
                  const dispatch=card.dispatchEvent.bind(card);
                  card.dispatchEvent=evt=>{if(evt.type==='mouseenter')hover.push(card.dataset.plEvidenceCard);return dispatch(evt);};
                }
                inAutoplay=true;await view.autoplay(ctx);inAutoplay=false;
                record.hover=hover;record.clicks=clicks;
              } else {
                const annual=()=>walk(panel).find(el=>el.dataset.field==='annual_calls');
                const hero=()=>panel.querySelector('.pl-impact-amount');
                record.initialAmount=hero().textContent;
                record.sliders=panel.querySelectorAll('input').length;
                record.inert=panel.querySelectorAll('input').some(el=>el.disabled||Number(el.min)===Number(el.max));
                record.fixed=walk(panel).find(el=>el.dataset.fixedField==='move_charge_usd').textContent;
                annual().value=300;annual().dispatchEvent(event('input',annual()));
                record.customAmount=hero().textContent;await view.start();record.preserved=Number(annual().value);
                record.projection=panel.textContent.includes('Projection, not a result');
                record.revenue=panel.querySelector('.pl-impact-global-check').textContent;
                panel.querySelector('[data-pl-impact-details]').click();
                record.drawerSource=sheet.textContent.includes('experiments/impact-estimate/');
                sheet.querySelector('[data-pl-impact-close]').click();record.drawerClosed=sheet===null;
                inAutoplay=true;await view.autoplay(ctx);inAutoplay=false;
                record.finalAmount=hero().textContent;record.drag=calls.drag;
              }
              record.closedAtWait=closedAtWait;record.duration=calls.wait;
              view.leave();record.sheetClosedOnLeave=sheet===null;
              await view.enter();await view.start();record.loadsAfterRevisit=calls.load;record.doneAfterRevisit=calls.done;
              records.push(record);
            }
            process.stdout.write(JSON.stringify(records));
          })().catch(error=>{process.stderr.write(error.stack);process.exitCode=1;});
        """,self.data)
        evidence,impact=actual
        for record in actual:
            self.assertEqual((record['load'],record['done'],record['log']),(1,1,1))
            self.assertEqual((record['loadsAfterRevisit'],record['doneAfterRevisit']),(1,1))
            self.assertTrue(record['scope'])
            self.assertFalse(record['rawPaths'])
            self.assertTrue(record['drawerSource'])
            self.assertTrue(record['closedAtWait'])
            self.assertTrue(all(record['closedAtWait']))
            self.assertTrue(record['sheetClosedOnLeave'])
            self.assertTrue(12000<=record['duration']<=20000)
        self.assertTrue(all(evidence['labels'].values()))
        self.assertEqual(evidence['cards'],5)
        self.assertGreaterEqual(evidence['svgCount'],5)
        self.assertTrue(evidence['drawerN'])
        self.assertTrue(evidence['drawerInterval'])
        self.assertEqual(evidence['hover'],['voyages','reader'])
        self.assertEqual(evidence['clicks'],['button'])
        self.assertEqual(impact['sliders'],12)
        self.assertFalse(impact['inert'])
        self.assertIn('$20.86',impact['fixed'])
        self.assertEqual(impact['initialAmount'],'$1,656')
        self.assertEqual(impact['customAmount'],'$9,552')
        self.assertEqual(impact['preserved'],300)
        self.assertEqual(impact['finalAmount'],'$34,135')
        self.assertTrue(impact['projection'])
        self.assertTrue(impact['drawerClosed'])
        self.assertEqual(impact['drag'],1)
        self.assertIn('terminal revenue',impact['revenue'])

    def test_modules_are_local_scoped_and_independent_of_renderers(self):
        for name in ('evidence','impact'):
            js=(STATIC / f'planner-{name}.js').read_text()
            css=(STATIC / f'planner-{name}.css').read_text()
            self.assertNotRegex(js+css,r'https?://|(?<!:)//[\w.-]+\.[a-z]{2,}')
            self.assertNotRegex(js,r'__stage|Studio\.step|ctx\.center\(|BayPlan\.')
            for selector in re.findall(r'\.([a-zA-Z][\w-]*)',css):
                self.assertTrue(selector.startswith(f'pl-{name}-'),selector)
            self.assertNotIn('rgba(255,255,255',css.replace(' ',''))
            self.assertNotRegex(css,r'(?:linear|radial|conic)-gradient\(')
            for phrase in ('solves real ships','industry level','production ready','beats Navis','62 rules'):
                self.assertNotIn(phrase.lower(),js.lower())


if __name__=='__main__':
    unittest.main()

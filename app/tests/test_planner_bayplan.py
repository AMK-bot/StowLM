"""Browser-free BayPlan contract, geometry, interaction and sequential replay checks.

The tiny SVG DOM is intentionally local: no browser or third-party JavaScript DOM is
needed. Rendering tests also leave leader-reviewable SVGs in .suite-tmp/bayplan/.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "app/tests/fixtures/planner"
REAL = ROOT / "app/static/planner-data"
NODE = shutil.which("node")
REQUIRED = ("ship", "boxes", "plan-before", "plan-after", "moves", "stress")
DATASETS = [("fixture", FIXTURE)]
if all((REAL / f"{name}.json").exists() for name in REQUIRED):
    DATASETS.append(("real", REAL))

PRELUDE = r"""
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = process.argv[1], dataDir = process.argv[2];
const {host,document} = require(path.join(root,'app/tests/fixtures/planner/tiny-dom.cjs')).install();
require(path.join(root,'app/static/planner-bayplan.js'));
const read = name => JSON.parse(fs.readFileSync(path.join(dataDir,name+'.json'),'utf8'));
const ship = read('ship'), cargo = read('boxes'), before = read('plan-before'), after = read('plan-after'), moves = read('moves');
const bp = BayPlan.create(host,{ship,boxes:cargo.boxes,pods:cargo.pods});
const sameSlot = (a,b) => a && b && ['bay','row','tier'].every(k=>+a[k]===+b[k]);
const rectAt = slot => bp.geometry().slots.find(r=>sameSlot(r.slot,slot));
const allNodes = () => host.querySelectorAll('*');
const output = value => process.stdout.write(JSON.stringify(value)+'\n');
const dragBox=(instance,container,id,to,pointerId=31)=>{
  const svg=container.tagName.toLowerCase()==='svg'?container:container.querySelector('svg');
  const source=instance.screenPoint(id),target=instance.screenPoint(to);
  const sections=container.querySelectorAll('.bp-section');
  const candidates=sections.flatMap(section=>section.querySelectorAll('[data-box-id="'+id+'"]'));
  const box=candidates.at(-1);assert(box,'visible draggable section box '+id);
  box.dispatchEvent({type:'pointerdown',pointerId,button:0,isPrimary:true,clientX:source.x,clientY:source.y});
  svg.dispatchEvent({type:'pointermove',pointerId,buttons:1,clientX:target.x,clientY:target.y});
  svg.dispatchEvent({type:'pointerup',pointerId,button:0,clientX:target.x,clientY:target.y});
};
const saveSample=(instance,name)=>{const file=path.join(root,'.suite-tmp/bayplan',name+'.svg');fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,instance.toSVG());return file;};

"""


def run_node(body: str, dataset: Path = FIXTURE) -> dict:
    assert NODE, "node must be installed to verify the browser-free SVG component"
    script = PRELUDE + "\n(async () => {\n" + body + "\n})().catch(error=>{console.error(error.stack);process.exitCode=1;});"
    result = subprocess.run([NODE, "-e", script, str(ROOT), str(dataset)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_javascript_syntax():
    result = subprocess.run([NODE, "--check", str(ROOT / "app/static/planner-bayplan.js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name,dataset", DATASETS, ids=[name for name, _ in DATASETS])
def test_render_contract_and_review_samples(name, dataset):
    result = run_node(r"""
      bp.setPlan(before);
      const oddBays = ship.bays.filter(b=>+b.bay%2===1).map(b=>b.bay);
      const selected = oddBays.slice(0,2);
      bp.showBays(selected);
      const g = bp.geometry();
      assert.equal(g.profile.length,ship.bays.length,'profile includes every supplied bay');
      assert.equal(g.sections.length,selected.length,'two selected bay sections');
      assert.deepEqual(g.profile.map(b=>+b.bay),ship.bays.map(b=>+b.bay),'payload bay order');
      assert(g.slots.every(r=>Number.isFinite(r.cx)&&Number.isFinite(r.cy)&&r.width>0&&r.height>0));
      assert(g.slots.every(r=>r.x>=0&&r.y>=0&&r.x+r.width<=g.width&&r.y+r.height<=g.height));
      const outputDir=path.join(root,'.suite-tmp/bayplan'); fs.mkdirSync(outputDir,{recursive:true});
      const prefix=path.basename(dataDir)==='planner'?'fixture':'real';
      const save = label => {const file=path.join(outputDir,prefix+'-'+label+'.svg'); fs.writeFileSync(file,bp.toSVG()); return file;};
      const files=[save('before')];
      bp.setPlan(after); files.push(save('after'));
      const legendHost=document.createElement('div'); document.body.appendChild(legendHost);
      BayPlan.legend(legendHost,{pods:cargo.pods});
      const legend=legendHost.querySelector('svg');
      if(legend){ const file=path.join(outputDir,prefix+'-legend.svg');fs.writeFileSync(file,legend.outerHTML);files.push(file); }
      output({files,profile:g.profile.length,sections:g.sections.length,legend:legendHost.textContent});
    """, dataset)
    assert result["sections"] == 2
    assert "20" in result["legend"] and "40" in result["legend"]
    for filename in result["files"]:
        svg = ET.parse(filename).getroot()
        assert svg.tag.endswith("svg")
        assert svg.attrib.get("viewBox")


def test_slot_order_normalization_and_hatch_geometry():
    result = run_node(r"""
      const varied=JSON.parse(JSON.stringify(ship));
      varied.bays.forEach((b,i)=>{b.position=100-i;b.bay=String(b.bay);b.rows=b.rows.map(String);b.deck_tiers=[82,84];b.hold_tiers=[2,4];});
      const other=BayPlan.create(document.createElement('div'),{ship:varied,boxes:cargo.boxes,pods:cargo.pods});
      other.setPlan(before);other.showBays(['1','3']);
      const g=other.geometry();
      assert.deepEqual(g.profile.map(b=>+b.bay),[1,2,3,5]);
      const at=(b,r,t)=>g.slots.find(v=>sameSlot(v.slot,{bay:b,row:r,tier:t}));
      assert(at(1,4,84).cy<at(1,4,82).cy,'higher deck tier appears above lower');
      assert(at(1,4,4).cy<at(1,4,2).cy,'higher hold tier appears above lower');
      const section=g.sections.find(b=>+b.bay===1);
      assert(at(1,4,82).cy<section.hatchY&&at(1,4,4).cy>section.hatchY);
      assert(at(1,4,82).cx<at(1,2,82).cx&&at(1,0,82).cx<at(1,1,82).cx,'payload port to starboard row order');
      assert(other.screenPoint({bay:'1',row:'4',tier:'82'}));
      output({slots:g.slots.length});other.destroy();
    """)
    assert result["slots"] == 40


def test_snapping_occupancy_closed_bays_and_40ft_footprint():
    result = run_node(r"""
      bp.setPlan(before);bp.showBays([1,3]);
      const free=rectAt({bay:1,row:2,tier:82}),occupied=rectAt({bay:3,row:1,tier:84});
      assert(sameSlot(bp.snap({x:free.cx,y:free.cy},'TEST0000001'),free.slot));
      assert.equal(bp.snap({x:occupied.cx,y:occupied.cy},'TEST0000001'),null,'occupied destinations refused');
      const span=rectAt({bay:1,row:0,tier:2});
      assert.equal(bp.snap({x:span.cx,y:span.cy},'TEST0000001'),null,'40ft even bay blocks adjacent odd bay');
      assert.equal(bp.snap({x:-100,y:-100},'TEST0000001'),null,'outside grid does not snap');
      bp.closeBay(1,'Bay 1 · closed');
      assert.equal(bp.snap({x:free.cx,y:free.cy},'TEST0000001'),null,'closed destinations refused');
      const svg=bp.toSVG();assert(svg.includes('Bay 1 · closed'));assert(svg.includes('<pattern'),'closed bay hatching');
      output({closed:true});
    """)
    assert result["closed"]


def test_highlights_tooltips_violation_marks_and_selection():
    result = run_node(r"""
      bp.setPlan(before);bp.showBays([1,3]);
      for(const kind of ['forced','moved','selected','violation'])bp.highlight(['TEST0000001'],kind);
      let svg=bp.toSVG();
      for(const kind of ['forced','moved','selected','violation'])assert(svg.includes('is-'+kind)||svg.includes('bp-'+kind),'highlight '+kind);
      const titles=host.querySelectorAll('title').map(n=>n.textContent).join('\n');
      for(const text of ['TEST0000002','40','26.1','MYPKG','3','TEST0000003','reefer'])assert(titles.toLowerCase().includes(text.toLowerCase()),'tooltip metadata '+text);
      bp.markViolation({bay:1,row:2,tier:82},'Over stack weight');
      assert(bp.toSVG().includes('Over stack weight'));bp.clearMarks();assert(!bp.toSVG().includes('Over stack weight'));
      let selected=null;bp.on('select',(id,slot)=>{selected={id,slot};});
      const box=host.querySelector('[data-box-id="TEST0000001"]')||host.querySelector('[data-box="TEST0000001"]');
      assert(box,'box has discoverable data identifier');box.dispatchEvent({type:'click'});
      assert.equal(selected.id,'TEST0000001');
      output({selected});
    """)
    assert result["selected"]["id"] == "TEST0000001"


@pytest.mark.parametrize("name,dataset", DATASETS, ids=[name for name, _ in DATASETS])
def test_play_commits_moves_in_order_without_mutating_inputs(name, dataset):
    result = run_node(r"""
      bp.setPlan({slots:moves.initial_slots || before.slots});bp.showBays([1,3]);
      const original=JSON.stringify(moves),callbacks=[];
      const result=await bp.play(moves,{speed:100000,onMove:(move,count,total)=>{
        const current=bp.getPlan().slots.find(s=>s.box_id===move.box_id);
        assert(move.to?sameSlot(current,move.to):!current,'callback observes committed move');
        callbacks.push({i:move.i,count,total});
      }});
      assert.deepEqual(callbacks,moves.moves.map((move,i)=>({i:move.i,count:i+1,total:moves.moves.length})));
      assert.equal(JSON.stringify(moves),original,'move input preserved');
      const normalize=plan=>plan.slots.map(s=>`${s.box_id}:${+s.bay}/${+s.row}/${+s.tier}`).sort();
      assert.deepEqual(normalize(bp.getPlan()),normalize(after));
      assert.equal(result.completed,moves.moves.length);assert.equal(result.cancelled,false);
      const present=new Set(after.slots.map(slot=>slot.box_id));
      const marked=kind=>[...new Set(host.querySelectorAll('.bp-'+kind).map(node=>node.closest('.bp-box')).filter(Boolean).map(node=>node.getAttribute('data-box-id')))].sort();
      const derived=force=>[...new Set(moves.moves.filter(move=>move.box_id&&(!force||move.forced)&&present.has(move.box_id)).map(move=>move.box_id))].sort();
      assert.deepEqual(marked('moved'),derived(false),'without caller highlights playback derives present moved markers');
      assert.deepEqual(marked('forced'),derived(true),'without caller highlights playback derives present forced markers');
      output({callbacks,result});
    """, dataset)
    assert len(result["callbacks"]) == result["result"]["total"]


def test_screen_points_account_for_letterboxing():
    result = run_node(r"""
      bp.setPlan(before);bp.showBays([1,3]);
      const svg=host.querySelector('svg');
      svg.rect={left:10,top:20,x:10,y:20,width:1000,height:520,right:1010,bottom:540};
      const cell=rectAt({bay:1,row:4,tier:82}),point=bp.screenPoint('TEST0000001');
      assert(Math.abs(point.x-(cell.cx+130))<0.01,'horizontal SVG letterbox inset');
      assert(Math.abs(point.y-(cell.cy+20))<0.01,'screen top offset');
      output({point});
    """)
    assert result["point"]["x"] > 130


def test_pointer_drag_reports_drop_and_escape_cancels():
    result = run_node(r"""
      bp.setPlan(before);bp.showBays([1,3]);
      const drops=[];bp.enableDrag(drop=>drops.push(drop));
      const svg=host.querySelector('svg');
      const source={bay:1,row:4,tier:82},target={bay:1,row:2,tier:82};
      const pick=()=>{
        const candidates=[...host.querySelectorAll('[data-box-id="TEST0000001"]'),...host.querySelectorAll('[data-box="TEST0000001"]')];
        return candidates.at(-1);
      };
      let pointer=0;
      function start(){
        const box=pick();assert(box);const p=bp.screenPoint(source);
        box.dispatchEvent({type:'pointerdown',pointerId:++pointer,button:0,isPrimary:true,clientX:p.x,clientY:p.y});
      }
      function moveAndDrop(slot){
        const p=bp.screenPoint(slot);
        svg.dispatchEvent({type:'pointermove',pointerId:pointer,buttons:1,clientX:p.x,clientY:p.y});
        svg.dispatchEvent({type:'pointerup',pointerId:pointer,button:0,clientX:p.x,clientY:p.y});
      }
      start();moveAndDrop(target);
      assert.equal(drops.length,1,'one pointer gesture produces one drop');
      assert.equal(drops[0].box_id,'TEST0000001');assert(sameSlot(drops[0].from,source));assert(sameSlot(drops[0].to,target));
      bp.setPlan(before);start();moveAndDrop({bay:3,row:1,tier:84});
      assert.equal(drops.length,1,'occupied pointer destination refused');
      bp.setPlan(before);start();document.dispatchEvent({type:'keydown',key:'Escape'});moveAndDrop(target);
      assert.equal(drops.length,1,'Escape cancels active pointer gesture');
      bp.setPlan(before);bp.closeBay(3,'Bay 3 closed');start();moveAndDrop({bay:3,row:3,tier:82});
      assert.equal(drops.length,1,'closed pointer destination refused');
      output({drops});
    """)
    assert len(result["drops"]) == 1


def test_new_play_and_stop_cancel_without_committing_stale_moves():
    result = run_node(r"""
      bp.setPlan(before);bp.showBays([1,3]);
      let staleCallbacks=0;
      const stale=bp.play(moves,{speed:1,onMove:()=>staleCallbacks++});
      const current=bp.play(moves.moves,{speed:100000});
      const [first,second]=await Promise.all([stale,current]);
      assert.equal(first.cancelled,true);assert.equal(first.completed,0);assert.equal(staleCallbacks,0);
      assert.equal(second.cancelled,false);assert.equal(second.completed,3);
      bp.setPlan(before);
      const interrupted=bp.play(moves,{speed:1,onMove:()=>staleCallbacks++});bp.stop();
      const stopped=await interrupted;assert.equal(stopped.cancelled,true);assert.equal(stopped.completed,0);
      assert.equal(staleCallbacks,0);assert.deepEqual(bp.getPlan().slots,before.slots);
      output({first,second,stopped});
    """)
    assert result["first"]["cancelled"] and result["stopped"]["cancelled"]


def test_40ft_snaps_to_paired_alias_and_respects_both_physical_bays():
    result = run_node(r"""
      bp.setPlan(before);bp.showBays([1,3]);
      const free=rectAt({bay:1,row:2,tier:82});
      assert(sameSlot(bp.snap({x:free.cx,y:free.cy},'TEST0000002'),{bay:2,row:2,tier:82}),'40ft drag maps odd section to even assignment');
      const overlap=rectAt({bay:1,row:4,tier:82});
      assert.equal(bp.snap({x:overlap.cx,y:overlap.cy},'TEST0000002'),null,'20ft in either half blocks forty');
      bp.closeBay(3,'Closed paired bay');
      assert.equal(bp.snap({x:free.cx,y:free.cy},'TEST0000002'),null,'closed sibling blocks paired forty destination');
      output({paired:true});
    """)
    assert result["paired"]


def test_sparse_cells_size_compatibility_and_reefer_plugs():
    result = run_node(r"""
      const sparse=JSON.parse(JSON.stringify(ship));
      sparse.bays.forEach(b=>{b.cells=[];});
      sparse.bays.find(b=>b.bay===1).cells=[{row:4,tier:82,accepts:[20],reefer:false},{row:2,tier:82,accepts:[40],reefer:true}];
      sparse.bays.find(b=>b.bay===2).cells=[{row:2,tier:82,accepts:[40],reefer:true}];
      sparse.bays.find(b=>b.bay===3).cells=[{row:2,tier:82,accepts:[40],reefer:false}];
      const customBoxes={...cargo.boxes,REEFER40:{...cargo.boxes.TEST0000002,reefer:true}};
      const custom=BayPlan.create(document.createElement('div'),{ship:sparse,boxes:customBoxes,pods:cargo.pods});
      custom.setPlan({slots:[]});custom.showBays([1,3]);
      const g=custom.geometry();assert.equal(g.slots.length,3,'only physical cells in sparse geometry');
      const normal=g.slots.find(r=>sameSlot(r.slot,{bay:1,row:4,tier:82}));
      const paired=g.slots.find(r=>sameSlot(r.slot,{bay:1,row:2,tier:82}));
      assert(sameSlot(custom.snap({x:normal.cx,y:normal.cy},'TEST0000001'),normal.slot));
      assert.equal(custom.snap({x:paired.cx,y:paired.cy},'TEST0000001'),null,'cell accepts array rejects incompatible size');
      assert.equal(custom.snap({x:normal.cx,y:normal.cy},'TEST0000003'),null,'reefer requires plug');
      assert(sameSlot(custom.snap({x:paired.cx,y:paired.cy},'TEST0000002'),{bay:2,row:2,tier:82}));
      assert.equal(custom.snap({x:paired.cx,y:paired.cy},'REEFER40'),null,'paired reefer needs both physical cells');
      output({slots:g.slots.length});
    """)
    assert result["slots"] == 3


def test_40ft_alias_violation_outline_and_default_pod_legend():
    result = run_node(r"""
      bp.setPlan(before);bp.showBays([1,3]);
      bp.markViolation({bay:1,row:0,tier:2},'Paired cell rule failed');
      const section=host.querySelectorAll('.bp-section').find(g=>+g.getAttribute('data-bay')===1);
      const alias=section.querySelector('[data-box-id="TEST0000002"]');
      assert(alias.querySelector('.bp-violation'),'physical odd slot mark outlines forty foot alias');
      assert(alias.textContent.includes('Paired cell rule failed'),'physical slot rule named in alias tooltip');
      const legendHost=document.createElement('div');BayPlan.legend(legendHost);
      for(const pod of cargo.pods)assert(legendHost.textContent.includes(pod.code),'default legend includes '+pod.code);
      output({legend:legendHost.textContent});
    """)
    assert "Reefer" in result["legend"]


def test_export_outline_and_closed_bay_review_sample():
    result = run_node(r"""
      bp.setPlan(before);bp.showBays([1,3]);
      bp.highlight(['TEST0000001'],'selected');bp.highlight(['TEST0000002'],'forced');
      bp.highlight(['TEST0000003'],'moved');bp.markViolation({bay:3,row:1,tier:84},'Prepared drag breaks the rule');
      bp.closeBay(5,'Bay 5 · closed');
      const file=path.join(root,'.suite-tmp/bayplan/fixture-states.svg');fs.mkdirSync(path.dirname(file),{recursive:true});
      fs.writeFileSync(file,bp.toSVG());output({file});
    """)
    assert ET.parse(result["file"]).getroot().tag.endswith("svg")


@pytest.mark.parametrize("name,dataset", DATASETS, ids=[name for name, _ in DATASETS])
def test_null_box_hatch_events_have_an_indicator_without_phantom_cargo(name, dataset):
    result = run_node(r"""
      const queue=[];global.requestAnimationFrame=fn=>{queue.push(fn);return queue.length;};
      const find=kind=>moves.moves.find(m=>m.kind===kind)||{i:0,kind,cover_id:'HC-002',box_id:null,from:null,to:null,forced:false};
      const sequence=[find('hatch_cover_off'),find('hatch_cover_on')],callbacks=[];
      bp.setPlan(before);const original=bp.getPlan();
      const playback=bp.play(sequence,{onMove:(move,count,total)=>callbacks.push({kind:move.kind,count,total})});
      for(let i=0;i<sequence.length;i++){
        assert(queue.length,'hatch operations retain paced playback');queue.shift()(i*1000);
        assert.equal(host.querySelectorAll('.bp-moving-box').length,0,'hatch operation is not a cargo box');
        const indicator=host.querySelector('[role="status"]');assert(indicator,'non-cargo operation is named');
        assert(indicator.textContent.toLowerCase().includes(sequence[i].kind.replace(/_/g,' ')));
        assert(indicator.textContent.includes(sequence[i].cover_id),'indicator identifies the cover');
        assert(host.querySelectorAll('.bp-box').every(box=>box.getAttribute('data-box-id')&&box.getAttribute('data-box-id')!=='null'));
        assert(queue.length);queue.shift()(i*1000+400);await Promise.resolve();
      }
      const result=await playback;assert.equal(result.completed,2);assert.equal(result.cancelled,false);
      assert.deepEqual(callbacks,sequence.map((move,i)=>({kind:move.kind,count:i+1,total:2})));
      assert.deepEqual(bp.getPlan(),original,'hatch work leaves cargo assignments unchanged');
      output({callbacks});
    """, dataset)
    assert [item["count"] for item in result["callbacks"]] == [1, 2]


def test_play_renders_box_between_its_source_and_destination():
    result = run_node(r"""
      const queue=[];global.requestAnimationFrame=fn=>{queue.push(fn);return queue.length;};
      bp.setPlan(before);bp.showBays([1,3]);
      const move=moves.moves.find(m=>m.from&&m.to),source=rectAt(move.from),target=rectAt(move.to);
      const playback=bp.play([move]);queue.shift()(0);assert(queue.length);queue.shift()(160);
      const moving=host.querySelector('.bp-moving-box');assert(moving,'in-flight cargo is visible');
      const fill=moving.querySelector('.bp-box-fill');
      const cx=+fill.getAttribute('x')+(+fill.getAttribute('width'))/2,cy=+fill.getAttribute('y')+(+fill.getAttribute('height'))/2;
      assert(Math.abs(cx-(source.cx+target.cx)/2)<0.01,'half-time horizontal midpoint');
      assert(Math.abs(cy-(source.cy+target.cy)/2)<0.01,'half-time vertical midpoint');
      assert(sameSlot(bp.getPlan().slots.find(s=>s.box_id===move.box_id),move.from),'cargo commits when slide finishes');
      queue.shift()(320);const result=await playback;assert.equal(result.completed,1);
      assert(sameSlot(bp.getPlan().slots.find(s=>s.box_id===move.box_id),move.to));
      output({cx,cy});
    """)
    assert result["cx"] > 0


def test_svg_host_lifecycle_keeps_caller_classes_and_removes_old_listeners():
    result = run_node(r"""
      const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('class','planner-sheet is-big');
      document.body.appendChild(svg);let callerMoves=0;svg.addEventListener('pointermove',()=>callerMoves++);
      const docListeners=()=> (document.listeners.keydown||[]).length,baseline=docListeners();
      let oldDrops=0,newDrops=0;
      const first=BayPlan.create(svg,{ship,boxes:cargo.boxes,pods:cargo.pods});first.setPlan(before);first.showBays([1,3]);first.enableDrag(()=>oldDrops++);
      assert(svg.classList.contains('planner-sheet')&&svg.classList.contains('is-big'));
      const oldBox=svg.querySelectorAll('[data-box-id="TEST0000001"]').at(-1),start=first.screenPoint('TEST0000001');
      oldBox.dispatchEvent({type:'pointerdown',pointerId:10,button:0,clientX:start.x,clientY:start.y});
      first.destroy();assert.equal(docListeners(),baseline,'document listeners released');
      assert.equal(Object.values(svg.listeners).flat().length,1,'only caller root listener remains');
      assert(svg.classList.contains('planner-sheet')&&svg.classList.contains('is-big'),'caller classes survive destroy');
      const second=BayPlan.create(svg,{ship,boxes:cargo.boxes,pods:cargo.pods});second.setPlan(before);second.showBays([1,3]);second.enableDrag(()=>newDrops++);
      const box=svg.querySelectorAll('[data-box-id="TEST0000001"]').at(-1),from=second.screenPoint('TEST0000001'),to=second.screenPoint({bay:1,row:2,tier:82});
      box.dispatchEvent({type:'pointerdown',pointerId:11,button:0,clientX:from.x,clientY:from.y});
      svg.dispatchEvent({type:'pointermove',pointerId:11,buttons:1,clientX:to.x,clientY:to.y});
      svg.dispatchEvent({type:'pointerup',pointerId:11,clientX:to.x,clientY:to.y});
      assert.equal(oldDrops,0);assert.equal(newDrops,1);assert.equal(callerMoves,1);
      second.destroy();assert.equal(docListeners(),baseline);assert.equal(Object.values(svg.listeners).flat().length,1);
      assert(svg.classList.contains('planner-sheet')&&svg.classList.contains('is-big'));
      output({oldDrops,newDrops,callerMoves});
    """)
    assert result == {"oldDrops": 0, "newDrops": 1, "callerMoves": 1}


def test_swap_is_opt_in_and_pointer_proposes_compatible_counterpart():
    result = run_node(r"""
      const fixture=read('swap'),drops=[];bp.setPlan({slots:fixture.slots});bp.showBays([1,3]);
      bp.enableDrag(drop=>drops.push(drop));dragBox(bp,host,fixture.move.box_id,fixture.move.to);
      assert.equal(drops.length,0,'ordinary drag keeps occupied destinations unavailable');
      const cell=rectAt(fixture.move.to);assert.equal(bp.snap({x:cell.cx,y:cell.cy},fixture.move.box_id),null);
      bp.enableDrag(drop=>drops.push(drop),{allowSwap:true});dragBox(bp,host,fixture.move.box_id,fixture.move.to);
      assert.equal(drops.length,1);assert.equal(drops[0].swap_with,fixture.move.swap_with);
      assert(sameSlot(drops[0].from,fixture.move.from)&&sameSlot(drops[0].to,fixture.move.to));
      assert.deepEqual(bp.getPlan().slots,fixture.slots,'pointer drop proposes without changing plan');
      bp.closeBay(1,'Bay 1 · closed');dragBox(bp,host,fixture.move.box_id,fixture.move.to,32);
      assert.equal(drops.length,1,'closed swap source/destination refused');
      output({drop:drops[0]});
    """)
    assert result["drop"]["swap_with"] == "TEST0000005"


def test_swap_rejects_incompatible_reverse_alias_and_multiple_occupants():
    result = run_node(r"""
      const drops=[];bp.setPlan(before);bp.showBays([1,3]);bp.enableDrag(d=>drops.push(d),{allowSwap:true});
      dragBox(bp,host,'TEST0000001',{bay:1,row:0,tier:2});
      assert.equal(drops.length,0,'twenty cannot replace even-bay forty counterpart');
      const customBoxes={...cargo.boxes,TEST0000006:{...cargo.boxes.TEST0000001}};
      const customHost=document.createElement('div');document.body.appendChild(customHost);
      const custom=BayPlan.create(customHost,{ship,boxes:customBoxes,pods:cargo.pods});
      custom.setPlan({slots:[...before.slots,{bay:1,row:2,tier:82,deck:true,box_id:'TEST0000005'},{bay:3,row:2,tier:82,deck:true,box_id:'TEST0000006'}]});
      custom.showBays([1,3]);custom.enableDrag(d=>drops.push(d),{allowSwap:true});
      dragBox(custom,customHost,'TEST0000002',{bay:1,row:2,tier:82});
      assert.equal(drops.length,0,'forty spanning two different twenties is not a two-box swap');
      const sparse=JSON.parse(JSON.stringify(ship));
      sparse.bays.find(b=>b.bay===1).cells=[{row:4,tier:82,accepts:[20],reefer:false}];
      sparse.bays.find(b=>b.bay===3).cells=[{row:1,tier:84,accepts:[20],reefer:true}];
      const reverseHost=document.createElement('div');document.body.appendChild(reverseHost);
      const reverse=BayPlan.create(reverseHost,{ship:sparse,boxes:cargo.boxes,pods:cargo.pods});
      reverse.setPlan({slots:before.slots.filter(s=>['TEST0000001','TEST0000003'].includes(s.box_id))});reverse.showBays([1,3]);reverse.enableDrag(d=>drops.push(d),{allowSwap:true});
      dragBox(reverse,reverseHost,'TEST0000001',{bay:3,row:1,tier:84});
      assert.equal(drops.length,0,'reverse reefer placement must have plug');
      output({drops:drops.length});
    """)
    assert result["drops"] == 0


def test_swap_slides_both_boxes_and_commits_together():
    result = run_node(r"""
      const fixture=read('swap'),queue=[];global.requestAnimationFrame=fn=>{queue.push(fn);return queue.length;};
      bp.setPlan({slots:fixture.slots});bp.showBays([1,3]);const original=JSON.stringify(fixture),callbacks=[];
      const source=rectAt(fixture.move.from),target=rectAt(fixture.move.to);
      const playback=bp.play([fixture.move],{onMove:(move,count,total)=>{callbacks.push({count,total});
        assert(sameSlot(bp.getPlan().slots.find(s=>s.box_id===move.box_id),move.to));
        assert(sameSlot(bp.getPlan().slots.find(s=>s.box_id===move.swap_with),move.from));
      }});
      queue.shift()(0);queue.shift()(160);
      const ghosts=host.querySelectorAll('.bp-moving-box');assert.equal(ghosts.length,2,'both swap participants are in flight');
      assert.deepEqual(ghosts.map(g=>g.getAttribute('data-box-id')).sort(),[fixture.move.box_id,fixture.move.swap_with].sort());
      for(const ghost of ghosts){const r=ghost.querySelector('.bp-box-fill');assert(Math.abs(+r.getAttribute('x')+(+r.getAttribute('width'))/2-(source.cx+target.cx)/2)<0.01);}
      assert.deepEqual(bp.getPlan().slots,fixture.slots,'both original assignments remain until animation completes');
      const sample=saveSample(bp,'fixture-swap');
      queue.shift()(320);const result=await playback;assert.equal(result.completed,1);assert.equal(result.cancelled,false);
      assert.deepEqual(callbacks,[{count:1,total:1}]);assert.equal(JSON.stringify(fixture),original,'proposal input remains unchanged');
      output({sample});
    """)
    assert Path(result["sample"]).exists()


def test_cargo_overrides_normalize_display_and_reset_defensive_originals():
    result = run_node(r"""
      bp.setPlan(before);bp.showBays([1,3]);const original=JSON.stringify(cargo),overrides=read('overrides'),saved=JSON.stringify(overrides);
      bp.updateBoxes(overrides);assert.equal(JSON.stringify(cargo),original);assert.equal(JSON.stringify(overrides),saved);
      const section=()=>host.querySelectorAll('.bp-section').find(s=>+s.getAttribute('data-bay')===1);
      const box=()=>section().querySelector('[data-box-id="TEST0000001"]');
      let node=box();assert(node.textContent.includes('32.7'),'kilograms normalized to tonnes');assert(node.querySelector('.bp-dg'));assert.equal(node.querySelector('.bp-dg-class').textContent,'6.1');assert(node.querySelector('.bp-reefer'));
      overrides.TEST0000001.dg.imdg_class='9';bp.updateBoxes({TEST0000001:{pod:'MYPKG'}});
      node=box();assert.equal(node.querySelector('.bp-dg-class').textContent,'6.1','override objects defensively copied');assert(node.textContent.includes('32.7'));
      const sample=saveSample(bp,'fixture-overrides');
      bp.updateBoxes({TEST0000001:{dg:{imdg_class:'5.1'},dg_class:'3'}});assert.equal(box().querySelector('.bp-dg-class').textContent,'3','flat display class wins');
      bp.updateBoxes({TEST0000001:{dg:null}});assert.equal(box().querySelector('.bp-dg'),null,'explicit null DG clears symbol');
      cargo.boxes.TEST0000001.weight_t=99;bp.resetBoxes();node=box();assert(node.textContent.includes('18.4'),'reset restores original create snapshot');assert.equal(node.querySelector('.bp-dg'),null);assert.equal(node.querySelector('.bp-reefer'),null);
      output({sample});
    """)
    assert Path(result["sample"]).exists()


@pytest.mark.skipif(not all((REAL / f"{name}.json").exists() for name in REQUIRED), reason="real planner data has not been generated")
def test_real_prepared_passing_stress_swap_and_nested_dg_override():
    result = run_node(r"""
      const stress=read('stress'),prepared=stress.cases.find(c=>c.full&&c.full.legal&&c.full.gauges.swapped_box);assert(prepared);
      bp.setPlan({slots:stress.slots});bp.showBays([prepared.from.bay-1,prepared.from.bay+1]);bp.updateBoxes(stress.cargo_overrides);
      let drop=null;bp.enableDrag(d=>{drop=d;},{allowSwap:true});dragBox(bp,host,prepared.box_id,prepared.to);
      assert(drop,'prepared passing stress edit is reachable via direct manipulation');assert.equal(drop.swap_with,prepared.full.gauges.swapped_box);
      const count=bp.getPlan().slots.length,result=await bp.play([drop],{speed:100000});assert.equal(result.completed,1);assert.equal(bp.getPlan().slots.length,count);
      assert(sameSlot(bp.getPlan().slots.find(s=>s.box_id===drop.box_id),prepared.to));assert(sameSlot(bp.getPlan().slots.find(s=>s.box_id===drop.swap_with),prepared.from));
      const overrideId=Object.keys(stress.cargo_overrides)[0],overrideSlot=stress.slots.find(s=>s.box_id===overrideId);bp.focusBay(overrideSlot.bay);
      const node=host.querySelectorAll('.bp-section')[0].querySelector('[data-box-id="'+overrideId+'"]');assert.equal(node.querySelector('.bp-dg-class').textContent,'6.1');
      output({drop,sample:saveSample(bp,'real-stress-swap')});
    """, REAL)
    assert result["drop"]["swap_with"]


def test_play_rejects_stale_or_closed_swap_without_changing_plan():
    result = run_node(r"""
      const fixture=read('swap');bp.setPlan({slots:fixture.slots});bp.showBays([1,3]);const original=bp.getPlan();
      await assert.rejects(bp.play([{...fixture.move,swap_with:'TEST0000003'}],{speed:100000}));
      assert.deepEqual(bp.getPlan(),original,'stale counterpart does not partially commit');
      bp.closeBay(1,'Closed while proposal was pending');
      await assert.rejects(bp.play([fixture.move],{speed:100000}));assert.deepEqual(bp.getPlan(),original);
      output({preserved:true});
    """)
    assert result["preserved"]


def test_selected_profile_has_thin_outline_and_closed_label_is_visible():
    result = run_node(r"""
      bp.setPlan(before);bp.showBays([1,3]);bp.closeBay(5,'Bay 5 · closed');
      const selected=host.querySelectorAll('.bp-profile-bay').find(g=>+g.getAttribute('data-bay')===1);
      const frame=selected.querySelector('.bp-profile-frame');assert(frame,'selected profile has a dedicated outline');
      assert.equal(frame.getAttribute('fill'),'none');assert.equal(frame.getAttribute('stroke'),'#176698');assert.equal(+frame.getAttribute('stroke-width'),1.25);
      assert.equal(selected.querySelectorAll('rect')[0].getAttribute('fill'),'#fff','profile background remains white');
      const closed=host.querySelectorAll('.bp-profile-bay').find(g=>+g.getAttribute('data-bay')===5);
      const label=closed.querySelector('.bp-closed-label');assert(label&&label.tagName==='text');assert(label.textContent.toLowerCase().includes('closed'));
      assert(host.querySelectorAll('.bp-section-title').every(t=>t.textContent.includes('aft view')),'port to starboard row order states aft viewpoint');
      output({sample:saveSample(bp,'fixture-polish')});
    """)
    assert Path(result["sample"]).exists()


def test_45ft_size_stays_distinct_in_short_legible_cells():
    result = run_node(r"""
      const compact=JSON.parse(JSON.stringify(ship));compact.bays.forEach(b=>{b.deck_tiers=[82,84,86,88,90];b.hold_tiers=[2,4,6,8,10];});
      const customBoxes={...cargo.boxes,TEST45:{...cargo.boxes.TEST0000002,size_ft:45}};
      const compactHost=document.createElement('div');document.body.appendChild(compactHost);
      const custom=BayPlan.create(compactHost,{ship:compact,boxes:customBoxes,pods:cargo.pods});
      custom.setPlan({slots:[{box_id:'TEST45',bay:2,row:0,tier:82,deck:true}]});custom.showBays([1,3]);
      const section=compactHost.querySelectorAll('.bp-section')[0],node=section.querySelector('[data-box-id="TEST45"]');
      const size=node.querySelector('.bp-size-label');assert(size,'short cells retain length mark');assert.equal(size.textContent,'45′');
      assert.equal(node.querySelector('.bp-weight-label'),null,'compact cell uses space for size');
      output({sample:saveSample(custom,'fixture-45-short')});
    """)
    assert Path(result["sample"]).exists()


@pytest.mark.skipif(not all((REAL / f"{name}.json").exists() for name in REQUIRED), reason="real planner data has not been generated")
def test_real_polish_sample_includes_45ft_box_and_closed_bay():
    result = run_node(r"""
      bp.setPlan(after);bp.showBays([2,10]);bp.closeBay(9,'Bay 9 · closed');
      const fortyFive=Object.keys(cargo.boxes).filter(id=>+cargo.boxes[id].size_ft===45);
      const visible=host.querySelectorAll('.bp-section').flatMap(g=>g.querySelectorAll('.bp-box')).filter(g=>fortyFive.includes(g.getAttribute('data-box-id')));
      assert(visible.length,'real fixture includes a visible forty-five foot box');
      assert(visible.every(g=>g.querySelector('.bp-size-label')&&g.querySelector('.bp-size-label').textContent==='45′'));
      output({sample:saveSample(bp,'real-polish')});
    """, REAL)
    assert Path(result["sample"]).exists()


@pytest.mark.parametrize("name,dataset", DATASETS, ids=[name for name, _ in DATASETS])
def test_pre_generate_profile_shows_only_initial_onboard_cargo(name, dataset):
    result = run_node(r"""
      bp.setPlan(before);const normal=bp.geometry();
      const initial=Array.isArray(moves.initial_slots)?moves.initial_slots:before.slots.slice(0,2);
      const payload={...moves,initial_slots:JSON.parse(JSON.stringify(initial))},original=JSON.stringify(payload);
      bp.preGenerate(payload);bp.highlight(initial.map(s=>s.box_id),'forced');
      const g=bp.geometry();assert.equal(g.sections.length,0,'pre-generate has no expanded bay sections');
      assert.equal(host.querySelectorAll('.bp-section').length,0);assert.equal(g.profile.length,ship.bays.length);
      assert(g.profile[0].height>normal.profile[0].height*2,'pre-generate profile occupies centre');
      const expectedSlots=ship.bays.reduce((n,b)=>n+(b.cells?b.cells.length:b.rows.length*(b.deck_tiers.length+b.hold_tiers.length)),0);
      assert.equal(host.querySelectorAll('.bp-slot').length,expectedSlots,'all empty physical slots remain outlined');
      const visible=[...new Set(host.querySelectorAll('.bp-box').map(g=>g.getAttribute('data-box-id')))].sort();
      assert.deepEqual(visible,[...new Set(initial.map(s=>s.box_id))].sort(),'only containers onboard initially are filled');
      assert.deepEqual(bp.getPlan().slots,initial);assert.equal(JSON.stringify(payload),original,'input payload untouched');
      const sample=saveSample(bp,(path.basename(dataDir)==='planner'?'fixture':'real')+'-pre-generate');
      bp.preGenerate(initial);assert.deepEqual(bp.getPlan().slots,initial,'array shorthand has same initial state');
      bp.setPlan(after);assert(bp.geometry().sections.length>0,'a generated plan restores sections');
      output({sample,initial:initial.length});
    """, dataset)
    assert Path(result["sample"]).exists()


def test_focus_changes_counts_unique_present_boxes_and_preserves_payload_ties():
    result = run_node(r"""
      const plan={slots:[...before.slots,{bay:3,row:3,tier:82,deck:true,box_id:'TEST0000005'}]};
      bp.setPlan(plan);bp.showBays([2]);
      bp.highlight(['TEST0000001','TEST0000003','TEST0000004','MISSING'],'forced');
      bp.highlight(['TEST0000001','TEST0000005','TEST0000004','MISSING'],'moved');
      assert.deepEqual(bp.geometry().sections.map(s=>s.bay),[3],'two unique present changes beat one box in both highlight kinds');
      bp.closeBay(3,'Bay 3 · closed');bp.focusChanges();
      assert.deepEqual(bp.geometry().sections.map(s=>s.bay),[1],'closed winner excluded; payload order breaks remaining tie');
      bp.highlight(['TEST0000002'],'forced');bp.highlight(['TEST0000001'],'moved');
      assert.deepEqual(bp.geometry().sections.map(s=>s.bay),[1],'payload first bay wins equal counts');
      bp.highlight(['MISSING'],'forced');bp.highlight([],'moved');bp.focusChanges();
      assert.deepEqual(bp.geometry().sections.map(s=>s.bay),[1],'no current changes preserve manual selection');
      output({bay:bp.geometry().sections[0].bay});
    """)
    assert result["bay"] == 1


def test_hover_weight_uses_exactly_one_decimal():
    result = run_node(r"""
      bp.setPlan(after);bp.showBays([1,3]);
      const box=()=>host.querySelectorAll('.bp-section').find(s=>+s.getAttribute('data-bay')===3).querySelector('[data-box-id="TEST0000005"]');
      assert(box().querySelector('title').textContent.includes('21.0 t'),'whole tonne weight retains decimal');
      bp.updateBoxes({TEST0000005:{weight_t:27.75}});
      assert(box().querySelector('title').textContent.includes('27.8 t'),'hover weight rounds consistently to one decimal');
      const p=bp.screenPoint('TEST0000005');box().dispatchEvent({type:'pointerenter',clientX:p.x,clientY:p.y});
      assert(host.querySelector('[role="tooltip"]').textContent.includes('27.8 t'),'visible hover matches SVG title');
      output({weight:'27.8 t'});
    """)
    assert result["weight"] == "27.8 t"


def test_load_arrives_lightly_from_above_its_destination():
    result = run_node(r"""
      const queue=[];global.requestAnimationFrame=fn=>{queue.push(fn);return queue.length;};
      bp.setPlan(before);bp.showBays([1,3]);
      const move=moves.moves.find(m=>!m.from&&m.to),target=rectAt(move.to),centres=[];
      const playback=bp.play([move],{speed:1});
      const ghostCentre=()=>{const g=host.querySelector('.bp-moving-box'),r=g&&g.querySelector('.bp-box-fill');assert(r);return{x:+r.getAttribute('x')+(+r.getAttribute('width'))/2,y:+r.getAttribute('y')+(+r.getAttribute('height'))/2};};
      queue.shift()(0);centres.push(ghostCentre());assert(Math.abs(centres[0].x-target.cx)<0.01,'arrival aligns with destination column');
      assert(centres[0].y<target.cy&&target.cy-centres[0].y<=80,'short drop above destination');
      queue.shift()(160);centres.push(ghostCentre());assert(centres[1].y>centres[0].y&&centres[1].y<target.cy);
      const sample=saveSample(bp,'playback-mid');queue.shift()(320);const result=await playback;assert.equal(result.completed,1);
      assert(sameSlot(bp.getPlan().slots.find(s=>s.box_id===move.box_id),move.to));output({sample,centres});
    """)
    assert Path(result["sample"]).exists()


@pytest.mark.skipif(not all((REAL / f"{name}.json").exists() for name in REQUIRED), reason="real planner data has not been generated")
def test_real_generated_plan_automatically_focuses_the_most_changes():
    result = run_node(r"""
      bp.preGenerate(moves);bp.closeBay(9,'Bay 9 · closed');bp.setPlan(after);
      const old=new Map(before.slots.map(s=>[s.box_id,s]));
      const moved=after.slots.filter(s=>!sameSlot(s,old.get(s.box_id))).map(s=>s.box_id);
      const forced=moves.moves.filter(m=>m.forced&&m.box_id).map(m=>m.box_id);
      bp.highlight(forced,'forced');bp.highlight(moved,'moved');
      const changed=new Set([...forced,...moved]),counts=new Map();
      for(const s of after.slots)if(changed.has(s.box_id)&&+s.bay!==9)counts.set(+s.bay,(counts.get(+s.bay)||0)+1);
      const winner=ship.bays.filter(b=>counts.has(+b.bay)).sort((a,b)=>counts.get(+b.bay)-counts.get(+a.bay))[0];
      assert.equal(bp.geometry().sections.length,1);assert.equal(bp.geometry().sections[0].bay,+winner.bay);
      bp.focusChanges();assert.equal(bp.geometry().sections[0].bay,+winner.bay);
      output({bay:+winner.bay,sample:saveSample(bp,'real-generated-focus')});
    """, REAL)
    assert Path(result["sample"]).exists()


@pytest.mark.skipif(not all((REAL / f"{name}.json").exists() for name in REQUIRED), reason="real planner data has not been generated")
def test_real_default_playback_finishes_in_ten_seconds_with_every_move_callback():
    result = run_node(r"""
      const queue=[];global.requestAnimationFrame=fn=>{queue.push(fn);return queue.length;};
      bp.preGenerate(moves);const callbacks=[],original=JSON.stringify(moves);let done=false,clock=0;
      const playback=bp.play(moves,{onMove:(move,count,total)=>{callbacks.push({i:move.i,count,total});}}).then(result=>{done=true;return result;});
      while(!done){assert(clock<12000,'default timeline exceeds ten-second budget');if(queue.length){clock+=16;queue.shift()(clock);}await Promise.resolve();}
      const result=await playback;assert.equal(result.completed,moves.moves.length);assert.equal(result.cancelled,false);
      assert.deepEqual(callbacks,moves.moves.map((move,i)=>({i:move.i,count:i+1,total:moves.moves.length})));
      assert(clock>=9950&&clock<=10064,'entire exported sequence has one shared ten-second timeline: '+clock);
      const normalize=plan=>plan.slots.map(s=>`${s.box_id}:${+s.bay}/${+s.row}/${+s.tier}`).sort();assert.deepEqual(normalize(bp.getPlan()),normalize(after));
      assert.equal(JSON.stringify(moves),original);assert.equal(bp.geometry().sections.length,1,'completed generation focuses changed bay');
      output({clock,moves:callbacks.length});
    """, REAL)
    assert result["moves"] == len(json.loads((REAL / "moves.json").read_text())["moves"])


def test_long_play_ghost_lands_on_current_section_after_mode_and_bay_changes():
    result = run_node(r"""
      const queue=[];global.requestAnimationFrame=fn=>{queue.push(fn);return queue.length;};
      bp.preGenerate([]);
      const sequence=Array.from({length:40},(_,i)=>({i,kind:'load',box_id:i%2?'TEST0000003':'TEST0000001',from:null,to:i%2?{bay:3,row:1,tier:84,deck:true}:{bay:1,row:4,tier:82,deck:true},forced:false}));
      const playback=bp.play(sequence,{durationMs:0});assert.equal(queue.length,1);queue.shift()(0);
      for(let i=0;i<sequence.length;i++){
        const target=rectAt(sequence[i].to);assert(target,'current active section destination is visible');
        assert.deepEqual(bp.geometry().sections.map(s=>s.bay),[sequence[i].to.bay]);
        const ghost=host.querySelector('.bp-moving-box');assert.equal(ghost.getAttribute('data-box-id'),sequence[i].box_id);
        const fill=ghost.querySelector('.bp-box-fill'),cx=+fill.getAttribute('x')+(+fill.getAttribute('width'))/2,cy=+fill.getAttribute('y')+(+fill.getAttribute('height'))/2;
        assert(Math.abs(cx-target.cx)<0.01&&Math.abs(cy-target.cy)<0.01,'ghost lands at freshly rendered section, move '+i);
        await Promise.resolve();
      }
      const result=await playback;assert.equal(result.completed,sequence.length);assert.equal(result.cancelled,false);output({completed:result.completed});
    """)
    assert result["completed"] == 40


def test_pre_generate_is_compact_and_clears_stale_results_but_keeps_closures():
    result = run_node(r"""
      bp.setPlan(before);bp.closeBay(5,'Bay 5 · closed');
      for(const kind of ['selected','forced','moved','violation'])bp.highlight(['TEST0000001'],kind);
      bp.markViolation({bay:1,row:4,tier:82},'Stale checker message');assert(host.querySelectorAll('.bp-state').length);
      bp.preGenerate(before.slots);
      assert.equal(bp.geometry().height,360,'compact pre-generate canvas has no unused section space');
      assert.equal(host.querySelector('svg').getAttribute('viewBox'),'0 0 760 360');
      assert(host.textContent.includes('On board before planning'));
      assert.equal(host.querySelectorAll('.bp-state').length,0,'old result highlight states cleared');
      assert(!host.textContent.includes('Stale checker message'),'old checker marks cleared');
      const closed=host.querySelectorAll('.bp-profile-bay').find(g=>+g.getAttribute('data-bay')===5);
      assert(closed.querySelector('.bp-closed'),'confirmed closure persists');assert(closed.textContent.includes('Bay 5 · closed'));
      bp.setPlan(before);assert.equal(host.querySelectorAll('.bp-state').length,0,'stale states do not return when plan renders');
      output({height:360});
    """)
    assert result["height"] == 360


def test_empty_invalid_focus_requests_choose_changes_but_explicit_bay_wins():
    result = run_node(r"""
      bp.setPlan({slots:[...before.slots,{box_id:'TEST0000005',bay:3,row:3,tier:82,deck:true}]});
      bp.highlight(['TEST0000003','TEST0000005'],'moved');
      for(const value of [undefined,null,'','  ','not-a-bay',999]){
        bp.showBays([1]);bp.focusBay(value);assert.deepEqual(bp.geometry().sections.map(s=>s.bay),[3],'automatic fallback for '+String(value));
      }
      bp.focusBay(2);assert.deepEqual(bp.geometry().sections.map(s=>s.bay),[2],'valid explicit selection is preserved');
      bp.showBays([]);assert.deepEqual(bp.geometry().sections.map(s=>s.bay),[],'empty section list remains an explicit collapse');
      bp.focusBay();assert.deepEqual(bp.geometry().sections.map(s=>s.bay),[3],'no-argument focus restores automatic choice after collapse');
      output({bay:bp.geometry().sections[0].bay});
    """)
    assert result["bay"] == 3


def test_short_play_automatically_focuses_changes_with_explicit_completion_override():
    result = run_node(r"""
      const move=moves.moves.find(m=>m.from&&m.to);bp.setPlan(before);bp.showBays([3]);
      await bp.play([move],{speed:100000});assert.deepEqual(bp.geometry().sections.map(s=>s.bay),[1],'short successful playback also focuses changed cargo');
      bp.setPlan(before);bp.showBays([3]);await bp.play([move],{speed:100000,focusBay:5});
      assert.deepEqual(bp.geometry().sections.map(s=>s.bay),[5],'explicit completion bay overrides automatic focus');
      output({bay:bp.geometry().sections[0].bay});
    """)
    assert result["bay"] == 5


@pytest.mark.skipif(not all((REAL / f"{name}.json").exists() for name in REQUIRED), reason="real planner data has not been generated")
def test_actual_plan_module_sequence_recovers_single_automatic_focus():
    result = run_node(r"""
      let definition;const previous=window.Planner;window.Planner={stage:value=>{definition=value;}};
      require(path.join(root,'app/static/planner-plan.js'));window.Planner=previous;assert.equal(definition.id,'plan');const logic=definition.logic;
      const changed=logic.changedBoxes(before,after),forced=[...new Set(moves.moves.filter(m=>m.forced&&m.box_id).map(m=>m.box_id))];
      const highlight=()=>{bp.highlight(forced,'forced');bp.highlight(changed.filter(id=>!forced.includes(id)),'moved');};
      const action=logic.actionBays(after.slots,changed,forced);assert(action.length);
      bp.setPlan({...before,slots:moves.initial_slots});highlight();bp.showBays(action);
      const changedSet=new Set([...changed,...forced]),counts=new Map();
      for(const slot of after.slots)if(changedSet.has(slot.box_id))counts.set(+slot.bay,(counts.get(+slot.bay)||0)+1);
      const best=ship.bays.filter(b=>counts.has(+b.bay)).sort((a,b)=>counts.get(+b.bay)-counts.get(+a.bay))[0];
      const callbacks=[];const result=await bp.play(moves.moves,{speed:logic.playbackSpeed(moves.moves.length),onMove:(move,count)=>callbacks.push({i:move.i,count})});
      assert.equal(result.completed,moves.moves.length);assert.deepEqual(callbacks,moves.moves.map((move,i)=>({i:move.i,count:i+1})));
      assert.deepEqual(bp.geometry().sections.map(s=>s.bay),[+best.bay],'play completion preserves caller replan change meaning before finish refresh');
      const present=new Set(after.slots.map(slot=>slot.box_id));
      const marked=kind=>[...new Set(host.querySelectorAll('.bp-'+kind).map(node=>node.closest('.bp-box')).filter(Boolean).map(node=>node.getAttribute('data-box-id')))].sort();
      assert.deepEqual(marked('forced'),[...new Set(forced)].filter(id=>present.has(id)).sort(),'caller forced snapshot restored after playback');
      assert.deepEqual(marked('moved'),changed.filter(id=>!forced.includes(id)&&present.has(id)).sort(),'caller moved snapshot excludes untouched loads and forced boxes');
      bp.setPlan(after);highlight();bp.showBays(logic.actionBays(after.slots,changed,forced));
      const shellSelection=bp.geometry().sections.map(s=>s.bay);bp.focusBay();
      assert.deepEqual(bp.geometry().sections.map(s=>s.bay),[+best.bay],'no-argument focus recovers automatic winner after shell actionBays');
      output({action,shellSelection,automatic:+best.bay,sample:saveSample(bp,'real-shell-focus')});
    """, REAL)
    assert result["automatic"] in result["action"]


@pytest.mark.skipif(not all((REAL / f"{name}.json").exists() for name in REQUIRED), reason="real planner data has not been generated")
def test_every_real_box_weight_is_rounded_in_cells_titles_accessibility_and_hover():
    result = run_node(r"""
      const stress=read('stress'),id='QZXU0000478',raw=String(cargo.boxes[id].weight_t);assert(raw.includes('11.1414'),'regression source retains full precision');
      let labels=0,titles=0;
      for(const plan of [after,{slots:stress.slots}]){
        bp.setPlan(plan);bp.showBays(ship.bays.map(b=>b.bay));
        for(const node of host.querySelectorAll('.bp-box')){
          const data=cargo.boxes[node.getAttribute('data-box-id')];if(!data||data.weight_t==null)continue;
          const expected=Number(data.weight_t).toFixed(1);
          assert(node.querySelector('title').textContent.includes(expected+' t'),'rounded SVG title');titles++;
          assert(node.getAttribute('aria-label').includes(expected+' t'),'rounded accessible label');
          for(const text of node.querySelectorAll('.bp-weight-label')){assert.equal(text.textContent,expected+'t','rounded visible cell weight');labels++;}
        }
        const slot=plan.slots.find(s=>s.box_id===id);bp.focusBay(slot.bay);
        const node=host.querySelector('.bp-section').querySelector('[data-box-id="'+id+'"]');assert(node);
        const p=bp.screenPoint(id);node.dispatchEvent({type:'pointerenter',clientX:p.x,clientY:p.y});
        const tooltip=host.querySelector('[role="tooltip"]');assert(tooltip.textContent.includes('11.1 t'));assert(!tooltip.textContent.includes(raw),'hover does not leak raw precision');
        assert(!bp.toSVG().includes(raw),'no visible or accessible SVG surface contains raw precision');
        if(plan===after)saveSample(bp,'real-shell-hover');
      }
      assert(labels>0&&titles>0);output({labels,titles,hover:'11.1 t'});
    """, REAL)
    assert result["labels"] > 0 and result["titles"] > 0


@pytest.mark.skipif(not all((REAL / f"{name}.json").exists() for name in REQUIRED), reason="real planner data has not been generated")
def test_real_short_load_focuses_away_from_prior_bay10_and_honors_explicit_override():
    result = run_node(r"""
      const load=moves.moves.find(move=>move.box_id&&move.to&&+move.to.bay===22);assert(load);
      bp.setPlan({slots:[]});bp.focusBay(10);assert.deepEqual(bp.geometry().sections.map(s=>s.bay),[10]);
      await bp.play([load],{speed:100000});assert.deepEqual(bp.geometry().sections.map(s=>s.bay),[22],'short load focuses its generated bay from previous bay10');
      bp.focusBay(10);assert.deepEqual(bp.geometry().sections.map(s=>s.bay),[10],'explicit valid bay10 selection is honored');
      bp.focusBay();assert.deepEqual(bp.geometry().sections.map(s=>s.bay),[22],'no-argument focus returns to changed bay22');
      bp.setPlan({slots:[]});await bp.play([load],{speed:100000,focusBay:10});
      assert.deepEqual(bp.geometry().sections.map(s=>s.bay),[10],'explicit completion override can choose bay10');
      output({bay:10});
    """, REAL)
    assert result["bay"] == 10

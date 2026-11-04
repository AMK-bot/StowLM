/* Playback is presentation only. All states and move counts come from the Python
 * move engine. BAPLIE row/tier conventions: docs/architecture.md, Conventions. */
"use strict";
const $ = id => document.getElementById(id);
const ports = {THLCH:"Laem Chabang-like (synthetic)",THBKK:"Bangkok-like (synthetic)",THSGZ:"Songkhla-like (synthetic)",SGSIN:"Singapore-like (synthetic)",MYPKG:"Port Klang-like (synthetic)",VNSGN:"Ho Chi Minh City-like (synthetic)",VNCMT:"Cai Mep-like (synthetic)",HKHKG:"Hong Kong-like (synthetic)",IDJKT:"Jakarta-like (synthetic)"};
const portProfiles={laem_chabang_like:ports.THLCH,singapore_like:ports.SGSIN,port_klang_like:ports.MYPKG,cat_lai_like:ports.VNSGN,hong_kong_like:ports.HKHKG,us_west_coast:"US West Coast-like (synthetic)",small_feeder:"Small feeder port (synthetic)"};
function syntheticPort(port){return portProfiles[port.archetype||port.id]||ports[port.locode]||`${port.name||port.archetype||port.id||"Port"} (synthetic)`;}
function modelIdentity(model){
  if(model?.verified!==true)return "Imported (unverified source)";
  if(model.planner_kind==="stowlm_adapter"||/^StowLM/.test(model.label||"")){
    const tainted=model.label==='StowLM (diagnostic adapter, not evidence)'||model.adapter_clean===false||["pipeline_test","diagnostic"].includes(model.adapter_data_mode)||Object.keys(model.adapter_stubs||{}).length>0;
    if(tainted)return "StowLM (diagnostic adapter, not evidence)";
    if(model.adapter_clean!==true)return "Imported (unverified source)";
  }
  return model.label;
}
function plannerLabel(meta){if(meta.model_run)return modelIdentity(meta.model_run);if(/stowlm|glimmer/i.test(meta.planner||""))return "Imported (unverified source)";if(meta.planner==="solver.exact"&&String(meta.optimality_status).toUpperCase()==="FEASIBLE")return "Exact (not proven optimal)";return meta.label||meta.planner||"Deterministic demo";}
function repairLabel(meta){return meta.repaired===true?`REPAIRED · ${meta.repairing_solver||"repairing solver unreported"}${meta.repair_status?` · ${meta.repair_status}`:""}${meta.repair_note?` · ${meta.repair_note}`:""}`:"";}
const recordedCount=value=>Number.isInteger(value)&&value>=0?String(value):'not recorded';
const recordedRate=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0&&value<=1?`${fmt(value*100)}%`:'not recorded';
const recordedFlag=value=>value===true?'yes':value===false?'no':'not recorded';
function traceTopFive(top){
  if(!Array.isArray(top)||!top.length)return '<p>Top-5 before masking: not recorded</p>';
  return `<p>Top-5 before masking</p><ol>${top.slice(0,5).map(action=>`<li>${action.legal===false?`<s>${escapeHTML(action.text||'[empty token]')}</s> · masked`:escapeHTML(action.text||'[empty token]')} <small>log p ${escapeHTML(action.logprob??'not recorded')}</small></li>`).join('')}</ol>`;
}
function traceStep(step){
  if(step.event==='dead_end'){
    const rules=[...new Set([...(Array.isArray(step.dead_end?.rule_ids)?step.dead_end.rule_ids:[]),...(Array.isArray(step.rule_ids)?step.rule_ids:[]),...Object.keys(step.dead_ends_by_rule||{})])];
    return `<details class="decode-step dead-state"><summary>Step ${escapeHTML(step.step)} · dead end · depth ${escapeHTML(step.depth??step.dead_end?.depth??'not recorded')}</summary><p>rules active at the dead state: ${rules.length?rules.map(escapeHTML).join(', '):'not recorded'} · unattributed: ${rules.length?0:1}</p>${step.dead_end?.reason?`<p>${escapeHTML(step.dead_end.reason)}</p>`:''}<p>Screen diagnostics (recorded probes, not rejected-action counts):</p><pre>${escapeHTML(step.screens==null?'not recorded':JSON.stringify(step.screens,null,2))}</pre></details>`;
  }
  const counts=Number.isInteger(step.n_actions)?`${recordedCount(step.n_legal)} / ${step.n_actions} ${String(step.n_actions_scope||'').startsWith('physically possible')?'legal / physically possible actions':'legal actions · denominator scope unreported'}`:'action counts: not recorded';
  const chosen=step.chosen?.text??(step.chosen?.action_id!=null?JSON.stringify(step.chosen.action_id):'not recorded');
  const decisions=Array.isArray(step.decisions)?step.decisions.filter(d=>['box','bay','row','tier'].includes(d.field)):[];
  const preferences=decisions.length?decisions.map(d=>`<section class="decode-decision"><strong>${escapeHTML(d.field)} token · position ${escapeHTML(d.position??'not recorded')}</strong><p>Legal actions after prefix: ${recordedCount(d.n_legal)} · allowed tokens: ${recordedCount(d.n_allowed)} · overridden: ${recordedFlag(d.overridden)}</p>${traceTopFive(d.top_k)}</section>`).join(''):`<p>Decision tokens: not recorded</p><p>legacy token position: ${escapeHTML(step.top_k_field??'not recorded')}</p>${traceTopFive(step.top_k)}`;
  return `<details class="decode-step"><summary>Step ${escapeHTML(step.step)} · ${escapeHTML(counts)} · chosen ${escapeHTML(chosen)}</summary><p>format_override: ${recordedFlag(step.format_override)} (opcode only)</p>${preferences}</details>`;
}
function renderModelRun(){
  const model=data.metadata?.model_run;$("model-run-state").textContent=model?modelIdentity(model):"no Glimmer run imported yet";
  $("decode-trace-caption").textContent='Decode trace · selected sample, replayed';
  const stats=model?.mask_override,rates=model?.run_rates,dead=model?.dead_ends,outcome=model?.outcome;
  for(const id of ['mask-override','format-override','run-reliability','model-outcome','dead-end-summary'])$(id).hidden=!model;
  const decisionRecorded=stats?.available===true&&stats.decision_scope==='box/bay/row/tier';
  $("mask-override").textContent=`Mask override rate · all samples${stats?.samples?` (${stats.samples} samples)`:''}: ${decisionRecorded?`${recordedRate(stats.decision_rate)} of box/slot positions (${recordedCount(stats.decision_positions)} positions; first token of box, bay, row, tier)`:'unavailable (not recorded with box/slot scope; selected-sample replay is insufficient)'}. All decoded tokens (separate): ${recordedRate(stats?.token_rate)} (${recordedCount(stats?.token_positions)} positions).`;
  $("format-override").textContent=`format_override · opcode only · all samples: ${recordedRate(stats?.format_rate)} (${recordedCount(stats?.format_positions)} positions). Excluded from the box/slot override rate.`;
  $("run-reliability").textContent=`Fallback/repair rate · all run instances: fallback ${recordedRate(rates?.fallback_rate)} · repair ${recordedRate(rates?.repair_rate)} (${recordedCount(rates?.instances)} instances).${rates?.definition?` ${rates.definition}`:''}`;
  $("model-outcome").textContent=`Recorded draft outcome: ${outcome?.label||'not recorded'}${outcome?.delivered_after_time_limit===true?' · plan delivered after the time limit':''} · current checker verdict shown separately`;
  const attribution=dead?.collected===true?Object.entries(dead.by_rule||{}).map(([rule,count])=>`${rule}: ${count}`).join(', ')||'none recorded':'not recorded';
  $("dead-end-summary").textContent=`Run dead ends: ${recordedCount(dead?.dead_end_samples)} dead-ended samples · rules active at the dead state: ${attribution} · Unattributed: ${recordedCount(dead?.unattributed)}. One sample may list several active rules.`;
  const trace=Array.isArray(model?.trace)?model.trace:[];$("decode-trace-panel").hidden=trace.length===0;
  $("decode-trace").innerHTML=trace.map(traceStep).join('');
}
function renderExplanation(explanation){
  $("replay-explanation").hidden=!explanation?.text;
  $("explanation-label").textContent=explanation?.source==='spark'?"Explained by Muse Spark; the checker's verdict is authoritative":"Deterministic template explanation";
  $("explanation-date").textContent=explanation?.source==='spark'?`${explanation.model} · ${explanation.generated_at} · Validator: accepted`:'';
  $("explanation-text").textContent=explanation?.text||'';
}
function crossingGeometry(){
  const frame=data.frames[index],reported=frame.highlight?.crane_height_crossing;
  if(reported)return {stacks:new Set(reported.blocking_stack_ids||[]),source:"Move-engine crossing data"};
  const move=frame.move,H=data.instance.port.max_crane_height_tiers,side=data.instance.port.berthing_side,stacks=new Set();
  if(!move||!Number.isFinite(H)||!side)return {stacks,source:"Geometry display only · crossing data not reported by move engine"};
  // Presentation geometry only: this never determines feasibility or approval.
  const before=data.frames[Math.max(0,index-1)].state,rank=row=>Number(row)%2?-Number(row):Number(row);
  const shipEnds=[move.src,move.dst].filter(value=>/^\d{7}$/.test(value||""));
  if(shipEnds.length){const bay=shipEnds[0].slice(0,3),endRows=shipEnds.map(slot=>rank(slot.slice(3,5)));if(shipEnds.length===1)endRows.push(side==="starboard"?-Infinity:Infinity);const lo=Math.min(...endRows),hi=Math.max(...endRows);for(const p of before.ship.placements){const row=rank(p.slot.slice(3,5)),tier=Number(p.slot.slice(5));if(p.slot.slice(0,3)===bay&&row>lo&&row<hi&&tier>=80&&(tier-80)/2>=H)stacks.add(p.slot.slice(0,5));}}
  const yardEnds=[move.src,move.dst].filter(value=>/^.+-\d+-\d+-\d+$/.test(value||""));
  for(const address of yardEnds){
    const [blockId,bay,row]=address.split("-"),block=data.instance.port.yard.blocks.find(b=>b.id===blockId);
    if(!block?.truck_lane_side)continue;
    const other=yardEnds.find(value=>value!==address&&value.split("-").slice(0,2).join("-")===`${blockId}-${bay}`);
    const destination=other?Number(other.split("-")[2]):block.truck_lane_side==="low"?0:Infinity;
    const lo=Math.min(Number(row),destination),hi=Math.max(Number(row),destination);
    for(const stack of before.yard.stacks){const [candidateBlock,candidateBay,candidateRow]=stack.key.split("-");if(candidateBlock===blockId&&candidateBay===bay&&Number(candidateRow)>lo&&Number(candidateRow)<hi&&stack.boxes.length>=H)stacks.add(stack.key);}
  }
  return {stacks,source:"Instance geometry only · H-high stacks on lift path · not a move-engine crossing verdict"};
}
const colors = ["#68b6b0","#96a9d8","#dfbc79","#ba9bc9","#8bb892","#d69b91","#93becb"];
let data, index = 0, timer = null, boxes = new Map(), podColors = new Map();
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt = value => Number(value || 0).toLocaleString(undefined,{maximumFractionDigits:2});
function fail(error) { $("error").hidden = false; $("error").textContent = `Unable to complete request: ${error.message}`; }
function selectedState() { return data.frames[index].state; }
function activeId() { const f=data.frames[index]; return f.highlight?.container_id || f.move?.container_id; }
function containerMarkup(id) {
  const box = boxes.get(id); if (!box) return "";
  const active = id === activeId();
  const blockers = data.frames[index].highlight?.blockers || [];
  const blocked = !active && blockers.includes(id);
  const markers = [box.dg ? "◆" : "",box.reefer ? "❄" : "",box.oog ? "↗" : "",box.special_stowage?.length ? "★" : "",box.direct_delivery ? "⇄" : ""].join("");
  const tooltip = [box.id,`${box.length_ft} ft · ${box.iso_type}`,`POD ${box.pod} · ${ports[box.pod] || box.pod}`,`Gross mass: ${fmt(box.gross_mass_kg)} kg`,box.dg ? `DG: UN ${box.dg.un_number}, class ${box.dg.imdg_class}` : null,box.reefer ? `Reefer: ${box.reefer_setpoint_c ?? "unspecified"} °C` : null,box.oog ? `OOG: ${Object.entries(box.oog).filter(([,v])=>v>0).map(([k,v])=>`${k}: ${v} m`).join(", ")}` : null,...(box.special_stowage||[])].filter(Boolean).join("\n");
  const description=box.direct_delivery?`${tooltip}\nDirect delivery · truck ↔ ship · no yard storage`:tooltip;
  return `<div tabindex="0" class="container${active?" active":""}${blocked?" blocker":""}${box.direct_delivery?" direct-delivery":""}" style="--pod-color:${podColors.get(box.pod)}" title="${escapeHTML(description)}" aria-label="${escapeHTML(description)}"><span>${escapeHTML(box.id.slice(-7))}</span><span class="markers">${markers}</span></div>`;
}
function renderYard() {
  const state=selectedState(),blockId=$("yard-block").value;
  const block=data.instance.port.yard.blocks.find(b=>b.id===blockId);
  const stacks=state.yard.stacks.filter(s=>s.key.split("-")[0]===blockId);
  const grouped=new Map(),crossing=crossingGeometry();
  for(const stack of stacks){const bay=stack.key.split("-")[1];if(!grouped.has(bay))grouped.set(bay,[]);grouped.get(bay).push(stack);}
  $("yard").innerHTML=`${block?.truck_lane_side?`<div class="truck-lane">TRUCK LANE · ${escapeHTML(block.truck_lane_side.toUpperCase())} ROW SIDE</div>`:""}${Number.isFinite(data.instance.port.max_crane_height_tiers)?`<div class="yard-height-line">H = ${data.instance.port.max_crane_height_tiers} tiers</div>`:""}`+[...grouped].sort(([a],[b])=>a.localeCompare(b)).map(([bay,items])=>`<div class="yard-bay"><div class="yard-bay-label">BAY ${escapeHTML(bay)}</div>${items.sort((a,b)=>block?.truck_lane_side==="high"?b.key.localeCompare(a.key):a.key.localeCompare(b.key)).map(stack=>`<div class="stack-wrapper${crossing.stacks.has(stack.key)?" yard-crossing":""}" title="${crossing.stacks.has(stack.key)?"H-high stack on lift path · geometry display":""}"><div class="yard-stack">${[...stack.boxes].reverse().map(containerMarkup).join("")}</div><div class="stack-address">${escapeHTML(stack.key)}</div></div>`).join("")}</div>`).join("");
  $("yard-count").textContent=`${state.yard.stacks.reduce((n,s)=>n+s.boxes.length,0)} boxes`;
  const buffered=state.yard.quay_buffer||[];
  $("quay-buffer-label").textContent=`QUAY BUFFER · ${buffered.length} ${buffered.length===1?"box":"boxes"}`;
  $("quay-buffer").innerHTML=buffered.map(containerMarkup).join("")||'<span class="empty-buffer">No containers temporarily ashore</span>';
  const aboard=new Set(state.ship.placements.map(p=>p.container_id)),bufferedIds=new Set(buffered);
  const directExports=data.instance.load_list.filter(id=>boxes.get(id)?.direct_delivery===true);
  const waiting=directExports.filter(id=>!aboard.has(id)&&!bufferedIds.has(id));
  $("direct-delivery-area").hidden=directExports.length===0;
  $("direct-delivery-label").textContent=`SHIP-SIDE TRUCKS · ${waiting.length} ${waiting.length===1?"export waiting":"exports waiting"} · direct delivery`;
  $("direct-delivery").innerHTML=waiting.map(containerMarkup).join("")||'<span class="empty-buffer">No direct-delivery exports waiting</span>';
  const lane=block?.truck_lane_side;
  $("yard-meta").textContent=block ? `BLOCK ${block.id} · ${block.n_bays} bays × ${block.n_rows} rows · ${block.max_tiers} high · ${lane?`TRUCK LANE · ${lane.toUpperCase()} ROW SIDE`:"TRUCK LANE SIDE UNAVAILABLE"}` : "";
}
function renderBay() {
  const state=selectedState(),bay=$("ship-bay").value;
  const cells=data.instance.vessel.cells.filter(c=>c.id.slice(0,3)===bay);
  const rows=[...new Set(cells.map(c=>c.id.slice(3,5)))].sort((a,b)=>{const rank=x=>Number(x)%2 ? -Number(x) : Number(x);return rank(a)-rank(b);});
  if(data.instance.port.berthing_side==="port")rows.reverse();
  $("row-orientation").textContent=data.instance.port.berthing_side?`QUAY · ${data.instance.port.berthing_side.toUpperCase()} ← ROWS → OFFSHORE`:"ROWS · quay side unavailable";
  const crossing=crossingGeometry();$("crossing-source").textContent=crossing.source;
  const tiers=[...new Set(cells.map(c=>c.id.slice(5,7)))].sort((a,b)=>Number(b)-Number(a));
  const available=new Set(cells.map(c=>c.id));
  const placements=new Map();
  for(const p of state.ship.placements){const pbay=Number(p.slot.slice(0,3)),tail=p.slot.slice(3);if(pbay%2===0){placements.set(String(pbay-1).padStart(3,"0")+tail,p.container_id);placements.set(String(pbay+1).padStart(3,"0")+tail,p.container_id);}else placements.set(p.slot,p.container_id);}
  const covers=(data.instance.vessel.hatch_covers||[]).filter(c=>c.bays.includes(Number(bay)) || [...c.hold_stack_ids,...c.deck_stack_ids].some(id=>id.slice(0,3)===bay));
  const openCovers=covers.filter(c=>Object.hasOwn(state.ship.covers_off,c.id));
  let html="",hatchDrawn=false,heightDrawn=false;const H=data.instance.port.max_crane_height_tiers;
  const heightLine=`<span></span><div class="crane-height-line" style="grid-column:span ${Math.max(rows.length,1)}">H = ${escapeHTML(H)} tiers</div>`;
  const hatch=`<span class="tier-label"></span><div class="hatch-line${openCovers.length?" open":""}" style="grid-column:span ${Math.max(rows.length,1)}">HATCH COVER${openCovers.length?" · OPEN":""}</div>`;
  for(const tier of tiers){if(Number.isFinite(H)&&!heightDrawn&&(Number(tier)<80||(Number(tier)-80)/2<=H)){html+=heightLine;heightDrawn=true;}if(Number(tier)<80&&!hatchDrawn){html+=hatch;hatchDrawn=true;}html+=`<div class="tier-label">${tier}</div>`+rows.map(row=>{const slot=bay+row+tier;return available.has(slot)?`<div class="bay-cell${crossing.stacks.has(bay+row)?" crane-crossing":""}" title="Cell ${slot}${crossing.stacks.has(bay+row)?" · H-high crossing stack":""}">${placements.has(slot)?containerMarkup(placements.get(slot)):""}</div>`:"<div></div>";}).join("");}
  if(!heightDrawn&&Number.isFinite(H))html=heightLine+html;
  if(!hatchDrawn)html+=hatch;
  html+='<div class="tier-label">TIER</div>'+rows.map(r=>`<div class="row-label">${r}</div>`).join("");
  $("bay").innerHTML=`<div class="quay-edge">QUAY · ${escapeHTML(data.instance.port.berthing_side?.toUpperCase()||"UNAVAILABLE")}</div><div class="bay-grid" style="grid-template-columns:30px repeat(${Math.max(rows.length,1)},minmax(50px,1fr))">${html}</div>`;
  $("ship-count").textContent=`${state.ship.placements.length} aboard`;
  $("cover-status").textContent=openCovers.length?`${openCovers.length} BAY COVER(S) OFF`:covers.length?"BAY HATCH COVERS CLOSED":"NO HATCH COVER IN THIS BAY";
}
function metricCounters(count){
  return {loads:count.loads,discharges:count.discharges,relocations:count.relocations,restows:count.restow_lifts,hatches:count.hatch_cover_lifts,"gate-moves":count.gate_moves,"total-lifts":count.total_lifts_now,future:(count.future||[]).reduce((n,f)=>n+f.restow_lifts,0),"future-hatches":(count.future||[]).reduce((n,f)=>n+f.hatch_cover_lifts,0),objective:count.weighted_nonproductive};
}
function render() {
  if(!data.frames?.length){renderModelRun();return;}
  const frame=data.frames[index],count=frame.counts || frame.count || {},move=frame.move;
  const counters=metricCounters(count),last=data.frames[data.frames.length-1];
  const totals=metricCounters(last.counts||last.count||{}),planned=data.plan.moves.length;
  const complete=data.frames.length-1===planned&&data.metadata?.physical_replay_legal===true;
  for(const [key,value] of Object.entries(counters))$(key).textContent=fmt(value);
  for(const [key,value] of Object.entries(totals))$(key+'-plan-total').textContent=`${complete?'plan total':'executed-prefix total'}: ${fmt(value)}`;
  $("metrics-progress").textContent=`So far (move ${index} / ${planned})`;
  $("metrics-totals-note").textContent=complete?
    'Plan totals cover the complete supplied sequence; checker approval is separate. Later-port counts and D11 include projections.':
    `Plan totals unavailable: replay stopped after ${data.frames.length-1} / ${planned} moves. Totals show only the executed prefix, including its projections.`;
  $("step-label").textContent=index ? `MOVE ${String(index).padStart(2,"0")}`:"INITIAL STATE";
  const names={load:"Load container",relocate:"Yard rehandle",discharge:"Discharge container",restow_shift:"Shift container",restow_off:"Restow to quay",restow_on:"Restow aboard",hatch_cover_off:"Open hatch cover",hatch_cover_on:"Close hatch cover",gate_in:"Gate arrival",gate_out:"Gate departure"};
  $("move-label").textContent=move ? `${names[move.kind]||move.kind} · ${move.container_id||move.cover_id||""}${/crane.?height/i.test(String(move.reason||move.shift_reason||data.frames[index].highlight?.reason||""))?" · crane-height shift":""}`:"Ready to replay";
  const endpoint=value=>value==="truck"?"Ship-side truck":value||"Quay";
  $("move-route").textContent=move ? `${endpoint(move.src)} → ${endpoint(move.dst)}`:"Move engine states · no rules run in the browser";
  $("current-step").textContent=index;$("scrub").value=index;
  $("first").disabled=$("previous").disabled=index===0;$("next").disabled=$("last").disabled=index===data.frames.length-1;
  renderYard();renderBay();
  if(workspaceView==="checker")renderChecker();
}
function setIndex(next,follow=true) {
  if(!data.frames?.length){index=0;render();return;}
  index=Math.max(0,Math.min(data.frames.length-1,Math.trunc(Number(next))||0));
  if(follow){const move=data.frames[index].move;
    if(move?.src?.includes("-")) {const block=move.src.split("-")[0];if([...$("yard-block").options].some(o=>o.value===block))$("yard-block").value=block;}
    const slot=move?.dst?.match(/^\d{7}$/)?move.dst:move?.src?.match(/^\d{7}$/)?move.src:null;
    if(slot){let bay=Number(slot.slice(0,3));if(bay%2===0)bay--;const value=String(bay).padStart(3,"0");if([...$("ship-bay").options].some(o=>o.value===value))$("ship-bay").value=value;}}
  render();
}
function pause(){clearInterval(timer);timer=null;$("play").innerHTML="▶ <span>Play sequence</span>";$("play").setAttribute("aria-label","Play");}
function play(){if(!data.frames?.length)return;if(timer){pause();return;}if(index===data.frames.length-1)setIndex(0);$("play").innerHTML="Ⅱ <span>Pause sequence</span>";$("play").setAttribute("aria-label","Pause");timer=setInterval(()=>{setIndex(index+1);if(index===data.frames.length-1)pause();},Number($("speed").value));}
async function load(url="/api/demo") {
  pause();const response=await fetch(url);if(!response.ok)throw new Error(`${response.status} ${await response.text()}`);
  acceptReplay(await response.json());
}
function acceptReplay(payload) {
  pause();data=payload;
  renderExplanation(data.explanation);
  if(!data.frames?.length){
    if(data.plan===null&&data.metadata?.model_run&&data.planner_result){acceptFailedModelRun();return;}
    throw new Error("Replay contains no frames");
  }
  $("operations-tab").disabled=$("checker-tab").disabled=false;
  $("no-plan-state").hidden=true;
  for(const id of ['metrics','metrics-progress','metrics-totals-note','playback-panel','crane-height','berthing-side'])$(id).hidden=false;
  $("error").hidden=true;boxes=new Map(data.instance.containers.map(b=>[b.id,b]));
  const pods=[...new Set(data.instance.containers.map(b=>b.pod))].sort();podColors=new Map(pods.map((p,i)=>[p,colors[i%colors.length]]));
  $("legend").innerHTML=pods.map(p=>`<span class="legend-item"><i class="legend-dot" style="background:${podColors.get(p)}"></i>${escapeHTML(p)} <span>${escapeHTML(ports[p]||"")}</span></span>`).join("");
  $("yard-block").innerHTML=data.instance.port.yard.blocks.map(b=>`<option value="${escapeHTML(b.id)}">Block ${escapeHTML(b.id)}</option>`).join("");
  $("ship-bay").innerHTML=[...new Set(data.instance.vessel.cells.map(c=>c.id.slice(0,3)))].sort().map(b=>`<option>${b}</option>`).join("");
  const vesselLabel=data.metadata?.vessel_label||data.instance.vessel.name||data.instance.vessel.id;$("vessel-name").textContent=/^Synthetic\b/i.test(vesselLabel)?vesselLabel:`Synthetic ${vesselLabel}`;
  const port=data.instance.port;$("terminal-label").textContent=syntheticPort(port);renderModelRun();
  $("illustrative-note").textContent=data.metadata?.storyboard_selection?.label||"1 instance, illustrative";
  $("crane-height").textContent=Number.isFinite(port.max_crane_height_tiers)?`CRANE HEIGHT · H = ${fmt(port.max_crane_height_tiers)} tiers · ${((port.archetype||port.id)==="laem_chabang_like")?"expert Q3":"synthetic"}`:`CRANE HEIGHT · H unavailable${Number.isFinite(port.quay_crane?.max_on_deck_tiers)?` · legacy quay limit ${fmt(port.quay_crane.max_on_deck_tiers)} tiers`:""}`;
  $("berthing-side").textContent=port.berthing_side?`QUAY SIDE · ${port.berthing_side.toUpperCase()}`:"QUAY SIDE · unavailable";
  const preferences=unboundReport(data.checker)?{}:data.checker?.report?.parts?.rules?.metrics||{};
  const killed=preferences.killed_slots,heavy=preferences.heavy_on_deck_count;
  $("replay-preferences").hidden=!Number.isFinite(killed)&&!Number.isFinite(heavy);
  $("preference-killed-slots").textContent=Number.isFinite(killed)?fmt(killed):"Not reported";
  $("preference-heavy-on-deck").textContent=Number.isFinite(heavy)?fmt(heavy):"Not reported";
  $("size-label").textContent=`${data.instance.size_class.toUpperCase()} / STAGE ${data.instance.stage}`;$("cargo-label").textContent=`${data.instance.load_list.length} to load`;$("seed-label").textContent=data.instance.seed;
  $("planner-name").textContent=plannerLabel(data.metadata||{});
  const repaired=repairLabel(data.metadata||{});$("repair-warning").hidden=!repaired;$("repair-warning").textContent=repaired;
  const placeholder=data.metadata?.planner==="ui-placeholder";
  const livePlanner = data.metadata?.source_kind==="Live run_planner";
  $("planner-tag").textContent=placeholder?"PLACEHOLDER":livePlanner?"LIVE / LOCAL":"IMPORTED";
  $("planner-footer").textContent=placeholder?"Placeholder planner":livePlanner?"Local planner":"Imported MovePlan";
  const shutouts=data.metadata?.shut_out;
  $("shutouts").textContent=Array.isArray(shutouts)?`${shutouts.length} shut out at plan end`:"Shutouts pending";
  $("shutouts").title=Array.isArray(shutouts)&&shutouts.length?shutouts.join(", "):"Load-list containers absent from the final ship state";
  const status=data.metadata?.checker_status;
  const unbound=unboundReport(data.checker),invalid=notValidated(data.checker,data.metadata),approved=!unbound&&!invalid&&data.metadata?.departure_approved===true;
  $("verification").textContent=`${unbound?"Unbound report · NOT VALIDATED":invalid?"NOT VALIDATED":status==="passed"?"Full checker passed":status==="failed"?"Full checker failed":"Full checker unavailable"} · ${approved?"departure approved":"departure not approved"}`;
  $("verification").style.color=approved?"var(--green)":"#a34d3c";
  $("checker-footer").textContent=approved?"Departure approved":"Not approved for departure";
  const provenance=data.metadata?.provenance||{},tainted=data.metadata?.stub_tainted===true||Object.keys(provenance.stubs||{}).length>0;
  $("provenance-warning").hidden=!tainted;
  $("provenance-warning").textContent=tainted?"STUB-TAINTED ARTIFACT · departure not approved · inspect provenance below":"";
  $("provenance-details").textContent=JSON.stringify(provenance,null,2);
  const blockers=data.metadata?.shipping_blockers||[];
  $("shipping-blockers").hidden=!blockers.length;
  $("shipping-blockers").textContent=blockers.join(" · ");
  if(data.metadata?.physical_replay_legal===false)$("verification").textContent=`${unbound?"Unbound report · ":""}${invalid?'NOT VALIDATED · ':''}Illegal move · showing reached state · departure not approved`;
  if(data.metadata?.planner_status && data.metadata.planner_status!=="ok")$("planner-tag").textContent=`${data.metadata.planner_status.toUpperCase()} / PARTIAL PLAN`;
  $("scrub").max=data.frames.length-1;$("total-steps").textContent=data.frames.length-1;
  if(!$("instance-select").dataset.populated)$("instance-select").innerHTML=`<option>${escapeHTML(data.instance.id)}</option>`;
  setIndex(Number(new URLSearchParams(location.search).get("frame"))||0);
  setView(workspaceView);
  $("checker-tab-count").textContent=data.checker?.violations?.length?`(${data.checker.violations.length})`:"";
}

function acceptFailedModelRun(){
  index=0;renderModelRun();$("error").hidden=true;
  $("no-plan-state").hidden=false;
  const outcome=data.metadata.model_run?.outcome?.label;
  const message=(data.planner_result.message||'Planner produced no replayable plan').trimEnd();
  const sentence=/[.!?]$/.test(message)?message:message+'.';
  $("no-plan-state").textContent=`No final plan · ${outcome==='mask time limit'?outcome:data.planner_result.status}: ${sentence} Decode trace is the selected draft sample; no cargo replay or checker approval is available.`;
  $("planner-name").textContent=plannerLabel(data.metadata);
  $("planner-tag").textContent='IMPORTED / NO PLAN';$("planner-footer").textContent='Imported model run · no final plan';
  $("verification").textContent='NOT VALIDATED · No final plan · departure not approved';$("verification").style.color='#a34d3c';
  $("checker-footer").textContent='No plan to check';$("shutouts").textContent='';
  const repair=repairLabel(data.metadata);$("repair-warning").hidden=!repair;$("repair-warning").textContent=repair;
  const stubs=data.metadata.provenance?.stubs||{};$("provenance-warning").hidden=!Object.keys(stubs).length;
  $("provenance-warning").textContent='Diagnostic model artifact · departure not approved';
  $("provenance-details").textContent=JSON.stringify(data.metadata.provenance||{},null,2);
  $("shipping-blockers").hidden=false;$("shipping-blockers").textContent=(data.metadata.shipping_blockers||['No final plan']).join(' · ');
  $("terminal-label").textContent=syntheticPort(data.instance.port);
  $("size-label").textContent=data.instance.size_class.toUpperCase();$("cargo-label").textContent=`${data.instance.load_list.length} to load`;$("seed-label").textContent=data.instance.seed;
  $("illustrative-note").textContent='1 instance, illustrative · diagnostic model run';
  if(!$("instance-select").dataset.populated)$("instance-select").innerHTML=`<option>${escapeHTML(data.instance.id)}</option>`;
  for(const id of ['metrics','metrics-progress','metrics-totals-note','playback-panel','replay-preferences','crane-height','berthing-side'])$(id).hidden=true;
  $("operations-tab").disabled=$("checker-tab").disabled=true;setView('diagnostic');
}
$("first").onclick=()=>{pause();setIndex(0);};$("last").onclick=()=>{pause();setIndex(data.frames.length-1);};$("previous").onclick=()=>{pause();setIndex(index-1);};$("next").onclick=()=>{pause();setIndex(index+1);};$("play").onclick=play;
$("scrub").oninput=event=>{pause();setIndex(Number(event.target.value));};$("yard-block").onchange=renderYard;$("ship-bay").onchange=renderBay;
$("speed").onchange=()=>{if(timer){pause();play();}};
document.addEventListener("keydown",event=>{if(["INPUT","SELECT","BUTTON"].includes(event.target.tagName)||!data)return;if(event.code==="Space"){event.preventDefault();play();}if(event.key==="ArrowRight"){event.preventDefault();pause();setIndex(index+1);}if(event.key==="ArrowLeft"){event.preventDefault();pause();setIndex(index-1);}});
load().then(initFlow).catch(fail);

/* These panels display the Python checker's departure verdict. SVG coordinates,
 * unit conversions and labels are presentation; no pass/fail rules live here. */
let checkerPanel = new URLSearchParams(location.search).get("panel") || "stability";
let workspaceView = new URLSearchParams(location.search).get("view") === "checker" ? "checker" : new URLSearchParams(location.search).get("view") === "compare" ? "compare" : "operations";
const numeric = value => typeof value === "number" && Number.isFinite(value);
const display = (value,unit="") => numeric(value) ? `${fmt(value)}${unit ? ` ${unit}` : ""}` : "—";
const criterionDisplay = (value,unit="") => numeric(value) ? `${value.toLocaleString(undefined,{maximumFractionDigits:4})}${unit ? ` ${unit}` : ""}` : "—";
const badge = (text,kind="") => `<span class="check-badge ${kind}">${escapeHTML(text)}</span>`;
function checkerState() { return data?.checker; }
function unboundReport(checker) { return Boolean(checker)&&(checker.bound!==true||checker.binding_status!=="bound"); }
function notValidated(checker,metadata={}){
  const report=checker?.report;
  return metadata?.validation_status==='NOT VALIDATED'||checker?.validation_status==='NOT VALIDATED'||metadata?.stub_tainted===true||
    report?.validated===false||report?.not_validated===true||
    Object.keys(report?.provenance?.stubs||{}).length>0||Object.keys(metadata?.provenance?.stubs||{}).length>0||
    Object.values(report?.parts||{}).some(part=>part.ran===false||part.error||part.metrics?.validated===0||part.metrics?.not_validated===1);
}
function setView(view) {
  if(data?.plan===null&&!data.frames?.length)view='diagnostic';
  workspaceView=view;document.body.classList.toggle("checker-mode",view==="checker");$("operations-view").hidden=view!=="operations";$("checker-view").hidden=view!=="checker";$("compare-view").hidden=view!=="compare";document.body.classList.toggle("compare-mode",view==="compare");
  for(const name of ["operations","checker","compare"]){$(name+"-tab").classList.toggle("selected",view===name);$(name+"-tab").setAttribute("aria-pressed",String(view===name));}
  if(view==="checker")renderChecker();
}
function chartSVG(points,series,{xLabel,yLabel,height=188}={}) {
  const good=points.filter(p=>numeric(p.x));
  const values=good.flatMap(p=>series.map(s=>p[s.key]).filter(numeric));
  if(!good.length||!values.length)return '<div class="chart-empty">No computed curve available. See the checker diagnostics.</div>';
  const width=670,left=66,right=18,top=16,bottom=height-36;
  const xmin=Math.min(...good.map(p=>p.x)),xmax=Math.max(...good.map(p=>p.x));
  let ymin=Math.min(0,...values),ymax=Math.max(0,...values);const pad=(ymax-ymin||1)*.08;ymin-=pad;ymax+=pad;
  const xx=x=>left+(x-xmin)/(xmax-xmin||1)*(width-left-right), yy=y=>bottom-(y-ymin)/(ymax-ymin)*(bottom-top);
  let content='';
  for(let i=0;i<=4;i++){const y=ymin+(ymax-ymin)*i/4;content+=`<line class="chart-grid" x1="${left}" x2="${width-right}" y1="${yy(y)}" y2="${yy(y)}"/><text x="${left-9}" y="${yy(y)+4}" text-anchor="end">${escapeHTML(fmt(y))}</text>`;const x=xmin+(xmax-xmin)*i/4;content+=`<text x="${xx(x)}" y="${bottom+17}" text-anchor="middle">${escapeHTML(fmt(x))}</text>`;}
  content+=`<line class="chart-zero" x1="${left}" x2="${width-right}" y1="${yy(0)}" y2="${yy(0)}"/>`;
  for(const s of series){const line=good.filter(p=>numeric(p[s.key])).map(p=>`${xx(p.x)},${yy(p[s.key])}`).join(' ');content+=`<polyline fill="none" stroke="${s.color}" stroke-width="${s.dashed?1.6:2.5}" ${s.dashed?'stroke-dasharray="6 4"':''} points="${line}"/>`;}
  return `<div class="chart"><div class="chart-axis">${escapeHTML(yLabel||'')}</div><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHTML(`${yLabel} against ${xLabel}`)}">${content}<text class="chart-x-label" x="${(width+left)/2}" y="${height-1}" text-anchor="middle">${escapeHTML(xLabel||'')}</text></svg></div>`;
}
function violationList(items,empty="No violations reported.") {
  if(!items?.length)return `<div class="empty-diagnostic">${escapeHTML(empty)}</div>`;
  return items.map(v=>`<article class="violation-row"><div>${badge(v.severity||"diagnostic",v.severity==="hard"?"fail":"warn")}<code>${escapeHTML(v.rule_id)}</code>${v.inherited?badge("INHERITED"):""}</div><p>${escapeHTML(v.message)}</p><div class="violation-detail">${[...(v.containers||[]),...(v.slots||[]),...(v.yard_addresses||[]),v.move_index!==null&&v.move_index!==undefined?`Move ${v.move_index+1}`:null,numeric(v.measured)?`Measured ${display(v.measured,v.unit)} · limit ${display(v.limit,v.unit)}`:null,numeric(v.margin)?`Raw clearance ${String(v.margin)} ${v.unit||""}`:null].filter(Boolean).map(escapeHTML).join(" · ")}</div></article>`).join('');
}
function partError(part) { return part?.error ? `<p class="part-error">${escapeHTML(part.error)}</p>` : ""; }
function metricTile(label,value,unit="",note="") {return `<div class="condition-metric"><span>${escapeHTML(label)}</span><strong>${display(value,unit)}</strong>${note?`<small>${escapeHTML(note)}</small>`:""}</div>`;}
function checkerNotes(notes) {
  return notes?.length?`<aside class="checker-notes" aria-label="Checker notes"><strong>Checker notes</strong>${[...new Set(notes)].map(note=>`<p class="checker-note">${escapeHTML(note)}</p>`).join('')}</aside>`:'';
}
function checkerConditions(checker) {
  const unavailable={ran:false,error:"No checked condition evidence available",metrics:{}};
  const conditions=checker.conditions?.length?[...checker.conditions]:[
    {id:"departure",phase:"departure",stability:checker.stability,strength:checker.strength},
  ];
  for(const phase of ["departure","arrival"]){
    if(!conditions.some(condition=>condition.phase===phase)){
      const missing={...unavailable,error:`No checked ${phase} condition evidence available`};
      conditions.push({id:`${phase}-unavailable`,phase,stability:missing,strength:missing});
    }
  }
  return conditions;
}
function endpointSummary(checker) {
  return `<aside class="endpoint-summary" aria-label="Checked endpoints">${checkerConditions(checker).map(condition=>`<div data-condition-summary="${escapeHTML(condition.id)}"><strong>${escapeHTML(condition.phase.toUpperCase())} · ${escapeHTML(condition.id)}</strong><span>${condition.failed_parts?.length?`Failed parts: ${escapeHTML(condition.failed_parts.join(', '))}`:condition.passed===true?'Condition checked':condition.passed===false?'Condition failed':'Condition evidence unavailable'}</span></div>`).join('')}</aside>`;
}
function renderConditions(checker,panel) {
  // Each endpoint uses only its own evidence. Legacy departure data cannot
  // supply an arrival curve, stations, criteria or verdict.
  const unavailable={ran:false,error:"No checked condition evidence available",metrics:{}};
  const conditions=checkerConditions(checker);
  const draw=panel==="stability"?renderStability:renderStrength;
  return conditions.map(condition=>`<section class="condition-section" data-condition-id="${escapeHTML(condition.id)}"><div class="condition-heading"><h2>${escapeHTML(condition.phase?.toUpperCase()||"CONDITION")} · ${escapeHTML(condition.id)}</h2>${condition.passed===false?badge("CONDITION FAILED","fail"):condition.passed===true?badge("CONDITION CHECKED"):badge("CONDITION EVIDENCE")}</div>${condition.failed_parts?.length?`<p class="failed-parts">Failed parts: ${escapeHTML(condition.failed_parts.join(', '))}</p>`:''}${checkerNotes(condition.notes)}${draw({[panel]:condition[panel]||unavailable,phase:condition.phase})}</section>`).join('');
}
function renderStability(checker) {
  const part=checker.stability||{},m=part.metrics||{};
  const chart=chartSVG((part.gz_curve||[]).map(p=>({x:p.heel_rad*180/Math.PI,gz:p.gz_m})),[{key:"gz",color:"#14796a"}],{xLabel:"Heel angle (°)",yLabel:"Righting lever GZ (m)",height:202});
  return `<div class="checker-grid"><article class="panel"><div class="panel-heading"><div><div class="eyebrow">STABILITY / RIGHTING ABILITY</div><h2>GZ curve ${badge(part.ran===false?"UNAVAILABLE":checker.phase?.toUpperCase()||"DEPARTURE")}</h2></div></div>${partError(part)}${chart}${part.curve_error?`<p class="part-error">${escapeHTML(part.curve_error)}</p>`:""}<div class="condition-metrics">${metricTile("CORRECTED GM",m.gm_m,"m","including free surface")}${metricTile("TRIM · AFT − FORWARD",m.trim_m,"m")}${metricTile("LIST",numeric(m.list_rad)?m.list_rad*180/Math.PI:null,"°")}${metricTile("DRAUGHT AFT",m.draught_aft_m,"m")}${metricTile("DRAUGHT FORWARD",m.draught_fwd_m,"m")}${metricTile("MEAN DRAUGHT",m.draught_mean_m,"m")}</div><div class="checker-footnote">GZ is the lever that restores a heeled ship. GM describes initial stability. Source: server-side stability checker / 2008 IS Code.</div></article><article class="panel"><div class="panel-heading"><div><div class="eyebrow">ACTUALLY APPLIED BY CHECKER</div><h2>${escapeHTML(part.criteria_set||"IS Code criteria unavailable")}</h2></div></div><div class="criteria-scroll"><table class="diagnostic-table"><thead><tr><th>Criterion / catalogue id</th><th>Value</th><th>Limit</th><th>Result</th></tr></thead><tbody>${(part.criteria||[]).map(c=>`<tr><td>${escapeHTML(c.label||c.key)}<code>${escapeHTML(c.rule_id)}</code></td><td>${criterionDisplay(c.measured,c.unit)}</td><td>${criterionDisplay(c.limit,c.unit)}</td><td>${badge(c.passed===true?"PASS":c.passed===false?"FAIL":"—",c.passed===true?"pass":c.passed===false?"fail":"")}</td></tr>`).join('')||'<tr><td colspan="4">Criteria could not be evaluated. See violations.</td></tr>'}</tbody></table></div><div class="checker-footnote">B 2.3 is selected for these feeders per D21. Applicable criteria and limits are returned by Python; the browser does not judge compliance.</div></article></div>`;
}
function renderStrength(checker) {
  const part=checker.strength||{},m=part.metrics||{},points=(part.stations||[]).map(s=>({x:s.x_m,sf:s.sf_n/1e6,bm:s.bm_nm/1e6,sfmin:s.min_shear_n/1e6,sfmax:s.max_shear_n/1e6,bmmin:s.min_bending_nm/1e6,bmmax:s.max_bending_nm/1e6}));
  const plot=(name,key,unit)=>`<article class="panel"><div class="panel-heading"><div><div class="eyebrow">SEAGOING / PERMISSIBLE ENVELOPE</div><h2>${name}</h2></div>${badge(`Peak ${display(m[`max_${key}_ratio`]*100,"%")}`)}</div>${partError(part)}${chartSVG(points,[{key:key+"min",color:"#c29a58",dashed:true},{key:key+"max",color:"#c29a58",dashed:true},{key,color:"#14796a"}],{xLabel:"Position forward of aft perpendicular (m)",yLabel:`${name} (${unit})`,height:252})}<div class="chart-legend"><span><i class="curve-key"></i>Computed ${escapeHTML(checker.phase||"departure")}</span><span><i class="envelope-key"></i>Permissible envelope</span></div><div class="checker-footnote">${key==="sf"?"Shear force is the accumulated imbalance between buoyancy and weight.":"Bending moment describes hull-girder bending; positive is hogging."} Source: server-side strength checker / IACS UR S11A.</div></article>`;
  return `<div class="checker-grid">${plot("Shear force","sf","MN")}${plot("Bending moment","bm","MN·m")}</div>`;
}
function renderDG(checker) {
  const dg=checker.dg||{},pairs=dg.pairs||[],areas=dg.areas||[],cargo=dg.cargo||[];
  return `<div class="checker-grid"><article class="panel"><div class="panel-heading"><div><div class="eyebrow">IMDG / SERVER CHECK</div><h2>Segregation pairs ${badge(`${pairs.length} pairs`)}</h2></div>${badge(dg.ran===false?"UNAVAILABLE":`${cargo.length} DG boxes`,dg.ran===false?"fail":"")}</div>${partError(dg)}<div class="diagnostic-scroll">${pairs.map(p=>`<div class="dg-pair"><strong>${(p.containers||[]).map(escapeHTML).join(" ↔ ")}${badge(p.status||"DIAGNOSTIC",p.status==="failed"?"fail":p.status==="unavailable"?"warn":"")}</strong><small>${(p.slots||[]).map(escapeHTML).join(" · ")}</small><div class="dg-requirements">${(p.general_table_requirements||[]).map(r=>`Class ${(r.classes||[]).map(escapeHTML).join(" ↔ ")}: general-table ${escapeHTML(r.value)}`).join(" · ")}${p.segregation_codes?.length?` · ${p.segregation_codes.map(escapeHTML).join(", ")}`:""}<small>${escapeHTML(p.requirement_note||"")}</small></div>${violationList(p.violations,p.status==="unavailable"?"DG checker unavailable; no verdict for this pair.":"No pair violation reported by checker.")}</div>`).join('')||'<div class="empty-diagnostic">No DG pair diagnostics reported.</div>'}${violationList((dg.violations||[]).filter(v=>(v.containers||[]).length<2),"No individual DG diagnostic records returned.")}</div><div class="checker-footnote">Pair diagnostics are supplied by the IMDG checker. Absence of a pair diagnostic is not a separate safety certificate.</div></article><article class="panel"><div class="panel-heading"><div><div class="eyebrow">DOCUMENT OF COMPLIANCE</div><h2>Authorised DG areas ${badge(`${areas.length} areas`)}</h2></div></div><div class="diagnostic-scroll">${areas.map(a=>`<div class="doc-area"><strong>${escapeHTML(a.id)}</strong><span>${escapeHTML(a.kind||"Area")} · ${a.stack_ids?.length||0} stacks</span><p>Classes: ${escapeHTML((a.classes_allowed||[]).join(", ")||"None recorded")}</p><small>${escapeHTML((a.packing_forms_allowed||[]).join(", "))}</small>${a.assumption?`<p class="doc-assumption">Synthetic premise: ${escapeHTML(a.assumption)}</p>`:""}</div>`).join('')||'<div class="empty-diagnostic">No DoC areas recorded. Missing authorisation is judged by the checker.</div>'}<table class="diagnostic-table"><thead><tr><th>DG container</th><th>Slot / class</th><th>DoC area</th></tr></thead><tbody>${cargo.map(c=>`<tr><td>${escapeHTML(c.container_id)}</td><td>${escapeHTML(c.slot)} / ${escapeHTML(c.class_number)}</td><td>${escapeHTML((c.doc_area_ids||[]).join(", ")||"Unrecorded")}</td></tr>`).join('')}</tbody></table></div><div class="checker-footnote">DoC records specify authorised spaces and classes; equipment and synthetic premises remain subject to the full checker.</div></article></div>`;
}
function renderPortStability(c){
  if(unboundReport(c)){$("port-stability").innerHTML="Unbound report · in-port evidence unavailable";return;}
  const part=c?.port_stability;if(part?.available!==true){$("port-stability").innerHTML="<strong>In-port stability</strong><p>not yet checked during cargo operations (D64 pending)</p>";return;}
  $("port-stability").innerHTML=`<strong>In-port stability ${badge(part.passed===true?"PASS":part.passed===false?"FAIL":"UNAVAILABLE",part.passed===true?"pass":part.passed===false?"fail":"")}</strong><p>Ballast changeover k = ${escapeHTML(part.ballast_changeover_k??"not reported")}</p><div class="per-lift-strip">${(part.per_lift||[]).map((lift,i)=>`<details><summary>Lift ${escapeHTML(Number.isInteger(lift.move_index)?lift.move_index+1:Number.isInteger(lift.index)?lift.index:i+1)} · ${badge(lift.passed===true?"PASS":lift.passed===false?"FAIL":"DIAGNOSTIC",lift.passed===false?"fail":"")}</summary><pre>${escapeHTML(JSON.stringify(lift,null,2))}</pre></details>`).join("")}</div>${violationList(part.violations,"No per-lift violation records returned.")}`;
}
function renderChecker() {
  renderPortStability(checkerState());
  const c=checkerState();if(!c){$("checker-content").innerHTML='<div class="empty-diagnostic">Departure check is unavailable for this replay.</div>';return;}
  if(unboundReport(c)){
    $("checker-verdict").textContent="Unbound report · NOT VALIDATED · departure not approved";$("checker-verdict").className="verdict-fail";
    $("checker-context").textContent="The report does not match this instance and plan. Recheck before inspecting evidence.";
    $("checker-version").textContent=`CHECKER ${c.version||"—"} · UNBOUND`;
    $("checker-tab-count").textContent=$("violation-count").textContent="";
    $("checker-content").innerHTML='<div class="empty-diagnostic">Unbound report · checker evidence is unavailable for this instance and plan.</div>';
    return;
  }
  const vs=c.violations||c.report?.violations||[];
  const invalid=notValidated(c,data.metadata),approved=!invalid&&data.metadata?.departure_approved===true;
  $("checker-verdict").textContent=`${invalid?'NOT VALIDATED':c.passed?"Full checker passed":"Full checker failed"} · ${approved?"departure approved":"departure not approved"}`;
  $("checker-verdict").className=approved?"verdict-pass":"verdict-fail";
  $("checker-version").textContent=`CHECKER ${c.version||"—"} · ${vs.length} DIAGNOSTICS`;
  $("checker-context").textContent=`Listed checker conditions · final plan and ballast at move ${c.evaluated_frame??data.frames.length-1} · playback now ${index}. Playback alone does not certify other conditions.`;
  $("checker-tab-count").textContent=vs.length?`(${vs.length})`:"";$("violation-count").textContent=vs.length?`(${vs.length})`:"";
  for(const button of document.querySelectorAll("[data-checker]")){const active=button.dataset.checker===checkerPanel;button.classList.toggle("selected",active);button.setAttribute("aria-pressed",String(active));}
  const content=["stability","strength"].includes(checkerPanel)?renderConditions(c,checkerPanel):checkerPanel==="dg"?renderDG(c):`<article class="panel all-violations"><div class="panel-heading"><div><div class="eyebrow">FULL CHECK / CATALOGUE IDS</div><h2>Endpoint diagnostics ${badge(`${vs.filter(v=>v.severity==='hard').length} hard`,"fail")}</h2></div></div><div class="all-violations-scroll">${violationList(vs,"No violation records returned. Consult the full checker verdict above.")}</div></article>`;
  $("checker-content").innerHTML=checkerNotes(c.notes)+(c.failed_parts?.length?`<p class="failed-parts">Failed checker parts: ${escapeHTML(c.failed_parts.join(', '))}</p>`:'')+(["stability","strength"].includes(checkerPanel)?'':endpointSummary(c))+content;
}
$("compare-tab").onclick=()=>{if(!comparison&&registeredPlans().length>=2)compareSaved();else setView("compare");};
$("operations-tab").onclick=()=>setView("operations");$("checker-tab").onclick=()=>setView("checker");
for(const button of document.querySelectorAll("[data-checker]"))button.onclick=()=>{checkerPanel=button.dataset.checker;renderChecker();};
setView(workspaceView);

/* Demo flow: identifiers come from the server's registered input catalogue.
 * Every replay, count and comparison diagnostic is computed in Python. */
let flowCatalog=null, comparison=null, solveBusy=false, pollingJob=null;
async function api(url,options) {
  const response=await fetch(url,options);
  const payload=await response.json();
  if(!response.ok)throw new Error(payload.error||`Request failed (${response.status})`);
  return payload;
}
function chosenInstance(){return flowCatalog?.instances.find(item=>item.id===$("instance-select").value);}
function registeredPlans(){return (flowCatalog?.plans||[]).filter(item=>item.instance_id===$("instance-select").value);}
function flowMessage(message,running=false){$("solve-progress").textContent=message;$("solve-progress").classList.toggle("running",running);}
function updateFlowControls(){
  const imported=$("planner-select").value==="imported",instance=chosenInstance();
  const conflicted=instance?.conflict===true,live=['tiny','small'].includes(instance?.size_class)&&instance?.live!==false&&!conflicted;
  $("import-controls").hidden=!imported;
  $("plan-compare").textContent=imported?"Compare saved plans →":"Plan & compare →";
  $("plan-compare").disabled=solveBusy||(imported?registeredPlans().length<2:!live);
  $("compare-imported").disabled=solveBusy||registeredPlans().length<2;
  $("instance-select").disabled=$("planner-select").disabled=solveBusy;
  if(conflicted)$("size-label").textContent=`${instance.size_class?.toUpperCase()||'—'} / CONFLICTED SOURCES`;
  if(!solveBusy)flowMessage(conflicted?(imported?"Conflicting instance contents · imported artifacts remain inspectable; comparisons require matching world identity.":"Conflicting instance contents · live planning disabled. Inspect imported artifacts; comparisons require matching world identity."):imported?"Imported output only · no live StowLM model call":instance?.live===false?"Live planning unavailable for this instance. Inspect imported output instead.":live?"Live solve for tiny / small instances · no network calls":"Live solve is limited to tiny / small. Use imported output for larger calls.");
}
function updatePlanChoices(){
  const plans=registeredPlans(),options=plans.map(item=>`<option value="${escapeHTML(item.id)}">${escapeHTML(item.label||item.planner||item.id)}</option>`).join('');
  $("left-plan").innerHTML=$("right-plan").innerHTML=options;
  const primary=plans.find(p=>p.planner==="solver.scalable")||plans[0];
  const baseline=plans.find(p=>p.planner==="baseline.twostep"&&p.id!==primary?.id)||plans.find(p=>p.id!==primary?.id);
  if(primary)$("left-plan").value=primary.id;if(baseline)$("right-plan").value=baseline.id;
  updateFlowControls();
}
async function refreshCatalog(){flowCatalog=await api('/api/catalog');return flowCatalog;}
async function initFlow(){
  try{
    await refreshCatalog();const picker=$("instance-select");picker.dataset.populated="true";
    picker.innerHTML=flowCatalog.instances.map(item=>`<option value="${escapeHTML(item.id)}">${escapeHTML(item.label||item.id)}${item.conflict?' · CONFLICTED SOURCES':''}</option>`).join('');
    if(flowCatalog.instances.some(item=>item.id===data.instance.id))picker.value=data.instance.id;
    updatePlanChoices();
    const active=flowCatalog.plans.find(p=>p.id===flowCatalog.active_plan_id);
    if(active&&active.planner!=="ui-placeholder"&&!['solver.scalable','solver.exact','baseline.twostep'].includes(active.planner))$("planner-select").value='imported';
    updateFlowControls();
    picker.onchange=()=>{pause();$("operations-tab").disabled=$("checker-tab").disabled=true;comparison=null;updatePlanChoices();const item=chosenInstance();$("size-label").textContent=`${item?.size_class?.toUpperCase()||'—'} / ${item?.conflict?'CONFLICTED SOURCES':'SELECTED'}`;$("cargo-label").textContent='Ready to plan';$("seed-label").textContent='—';$("comparison-content").innerHTML='<div class="empty-comparison"><span class="eyebrow">NEW PORT CALL SELECTED</span><h2>Ready to compare this instance.</h2><p>Select Plan & compare to run both local planners on the same yard.</p></div>';setView('compare');};
    if(workspaceView==='compare'&&registeredPlans().length>=2)await showComparison($("left-plan").value,$("right-plan").value);
  }catch(error){flowMessage(`Planning catalogue unavailable: ${error.message}`);$("plan-compare").disabled=true;}
}
function comparisonSummary(side){
  const invalid=notValidated(null,side),passed=!invalid&&side.checker_passed===true,approved=!invalid&&side.shippable===true&&side.repaired!==true,shutouts=Array.isArray(side.shut_out)?side.shut_out.length:side.shut_out;
  const provenance=side.provenance||{},tainted=side.stub_tainted===true||Object.keys(provenance.stubs||{}).length>0;
  const scope=side.count_scope==='complete_plan'?'complete supplied-sequence totals':side.count_scope==='executed_prefix'?'executed-prefix totals':'replay totals · execution scope unavailable';
  const tally=numeric(side.executed_moves)&&numeric(side.planned_moves)?`${fmt(side.executed_moves)} of ${fmt(side.planned_moves)} planned moves executed`:'Executed/planned move count unavailable';
  return `<div class="comparison-status">${badge(approved?'DEPARTURE APPROVED':'NOT APPROVED',approved?'pass':'fail')} ${badge(invalid?'NOT VALIDATED':passed?'CHECKER PASSED':'CHECKER FAILED')} ${tainted?badge('STUB-TAINTED','fail'):''} ${side.repaired===true?badge(repairLabel(side),"warn"):""} ${escapeHTML(String(shutouts??'—'))} shut out · ${scope}<div class="comparison-count-scope">${escapeHTML(tally)}</div>${side.shipping_blockers?.length?`<div>${escapeHTML(side.shipping_blockers.join(' · '))}</div>`:''}<details class="comparison-provenance"><summary>Artifact provenance</summary><pre>${escapeHTML(JSON.stringify(provenance,null,2))}</pre></details></div>`;
}
function comparisonCard(side,sideName){
  return `<article class="panel comparison-card"><div class="panel-heading"><div><div class="eyebrow">${sideName==='left'?'PRIMARY PLAN':'COMPARISON PLAN'}</div><h2>${escapeHTML(plannerLabel(side))}</h2></div><button data-replay="${sideName}">Replay this plan →</button></div><dl>${comparison.rows.map(row=>`<div class="${row.key==='weighted_nonproductive'?'comparison-objective':''}"><dt>${escapeHTML(row.label)}</dt><dd>${fmt(row[sideName])}</dd></div>`).join('')}</dl>${comparisonSummary(side)}${side.explanation?`<details class="template-explanation"><summary>${side.explanation_source==='spark'?"Explained by Muse Spark; the checker's verdict is authoritative":"Deterministic template explanation"}</summary>${side.explanation_source==='spark'?`<small>${escapeHTML(side.explanation_model)} · ${escapeHTML(side.explanation_date)} · Validator: accepted</small>`:''}<p class="explanation-text">${escapeHTML(side.explanation)}</p></details>`:''}</article>`;
}
function evidenceRow(event,sideName){
  const label=comparison[sideName].label||comparison[sideName].planner||sideName;
  return `<div class="evidence-row"><span class="check-badge warn">${escapeHTML(event.kind)}</span><strong>${escapeHTML(event.container_id||event.cover_id||'Lift')}</strong><code>${escapeHTML(event.src||'quay')} → ${escapeHTML(event.dst||'quay')}</code><span class="evidence-side" title="${escapeHTML(event.message||'')}">Only in ${escapeHTML(label)}</span><button data-evidence-side="${sideName}" data-frame="${Number(event.frame_index)||0}">Show before lift →</button></div>`;
}
function renderComparison(){
  if(!comparison)return;
  const saved=comparison.saved_objective,repaired=comparison.left.repaired===true||comparison.right.repaired===true,comparable=comparison.comparable===true&&!repaired;
  const headline=comparable?(saved>0?'Lower D11 for the primary plan.':saved<0?'Compare the trade-offs.':'Same objective. Different sequences.'):'Diagnostic comparison · departure not approved';
  const delta=comparable?`D11 ${saved>0?'−':saved<0?'+':''}${fmt(Math.abs(saved))}`:"No approved winner";
  const events=[...(comparison.avoided_by_left||[]).map(event=>[event,'right']),...(comparison.avoided_by_right||[]).map(event=>[event,'left'])];
  $("comparison-content").innerHTML=`<div class="comparison-headline"><div><h2>${headline}</h2><p>${escapeHTML(repaired?"Repaired plan present: comparison is diagnostic; no approved winner.":comparison.notice||'Same instance · full-plan lift counts · D11 weighted objective')}</p></div><strong>${escapeHTML(delta)}</strong></div><div class="comparison-columns">${comparisonCard(comparison.left,'left')}${comparisonCard(comparison.right,'right')}</div><p class="projected-difference">Projected later-port lift difference (comparison − primary): ${numeric(comparison.projected_lift_difference)?fmt(comparison.projected_lift_difference):"Not reported"}</p><p class="comparison-note">two-step baseline = a simple two-step planner, modelled from one field expert's description (E10), not a measure of how good planners work; search plans come from search software, our emulation of published methods; Navis and human-planner performance aren't public</p><details class="comparison-evidence"><summary>${events.length} unmatched nonproductive lifts · inspect the actual moves</summary><div class="evidence-list">${events.map(([event,side])=>evidenceRow(event,side)).join('')||'<div class="empty-diagnostic">No unmatched nonproductive lift events.</div>'}</div><div class="checker-footnote">${escapeHTML(comparison.evidence_note||'Move evidence is matched by type and container. An unmatched lift does not establish why the plans differ.')}</div></details>`;
  for(const button of document.querySelectorAll('[data-replay]'))button.onclick=()=>replayComparison(button.dataset.replay).catch(fail);
  for(const button of document.querySelectorAll('[data-evidence-side]'))button.onclick=()=>replayComparison(button.dataset.evidenceSide,Number(button.dataset.frame)).catch(fail);
}
async function replayComparison(side,frame=0){
  const entry=comparison[side];const planId=entry.replay_id||entry.plan_id;
  const payload=await api(`/api/replay?plan_id=${encodeURIComponent(planId)}`);acceptReplay(payload);setIndex(frame);setView('operations');
  const upcoming=payload.plan.moves[frame];
  if(upcoming?.src?.includes('-')){const block=upcoming.src.split('-')[0];if([...$("yard-block").options].some(o=>o.value===block))$("yard-block").value=block;}
  const slot=[upcoming?.src,upcoming?.dst].find(value=>/^\d{7}$/.test(value||''));
  if(slot){let bay=Number(slot.slice(0,3));if(bay%2===0)bay--;const value=String(bay).padStart(3,'0');if([...$("ship-bay").options].some(o=>o.value===value))$("ship-bay").value=value;}
  render();document.querySelector?.('.yard .container.active')?.scrollIntoView?.({block:'nearest',inline:'nearest'});
  flowMessage(`Replaying ${entry.label||entry.planner} · compare tab keeps both final totals`);
}
async function showComparison(left,right){
  if(!left||!right||left===right)throw new Error('Select two different saved plans for the same instance');
  comparison=await api(`/api/comparison?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`);
  renderComparison();pause();setView('compare');$("error").hidden=true;
}
async function compareSaved(){try{await showComparison($("left-plan").value,$("right-plan").value);}catch(error){fail(error);}}
async function startPlanning(){
  if($("planner-select").value==='imported'){await compareSaved();return;}
  solveBusy=true;updateFlowControls();flowMessage('Starting local planners…',true);
  try{
    const job=await api('/api/solve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instance_id:$("instance-select").value,planner:$("planner-select").value,compare:true,seed:0,time_limit_s:15})});
    pollingJob=job.id;await pollPlanning(job);
  }catch(error){solveBusy=false;pollingJob=null;updateFlowControls();flowMessage(`Planning stopped: ${error.message}`);fail(error);}
}
async function pollPlanning(job){
  if(job.id!==pollingJob)return;
  flowMessage(`${job.stage||job.status} · ${fmt(job.elapsed_s)} s elapsed`,true);
  if(job.status==='error')throw new Error(job.error||'Local planning failed');
  if(job.status==='done'){
    const ids=job.plan_ids||[];solveBusy=false;pollingJob=null;await refreshCatalog();updatePlanChoices();
    if(ids.length<2)throw new Error(`Comparison needs two plans. ${(job.results||[]).map(item=>`${item.planner}: ${item.status}${item.message?` — ${item.message}`:''}`).join('; ')||'A planner returned no replayable output.'}`);
    await showComparison(ids[0],ids[1]);flowMessage(`Comparison ready · ${fmt(job.elapsed_s)} s · inspect either replay`);return;
  }
  setTimeout(()=>api(`/api/jobs/${encodeURIComponent(job.id)}`).then(pollPlanning).catch(error=>{solveBusy=false;pollingJob=null;updateFlowControls();flowMessage(`Planning stopped: ${error.message}`);fail(error);}),700);
}
$("planner-select").onchange=updateFlowControls;
$("plan-compare").onclick=startPlanning;$("compare-imported").onclick=compareSaved;

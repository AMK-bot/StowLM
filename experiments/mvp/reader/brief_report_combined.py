"""Append the separately labelled D222 cohort; keep D220's fixed cohort intact."""
from __future__ import annotations
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import re
from statistics import mean

from experiments.mvp.reader import brief_report as original
from experiments.mvp.reader.run_brief import digest, write, ROOT, HERE

FIRST=HERE/'runs/brief'
SECOND=HERE/'runs/brief2'
CONTROL=HERE/'runs/brief-followup'


def read_group(run):
    cohort=json.loads((run/'cohort.json').read_text())
    prereg=json.loads((run/'preregister.json').read_text())
    if digest(run/'cohort.json')!=prereg['cohort_sha256']: raise ValueError('Cohort hash mismatch')
    implementation=run/'implementation.json'
    if implementation.exists():
        for name,sha in json.loads(implementation.read_text())['sources'].items():
            if digest(ROOT/name)!=sha: raise ValueError('Frozen implementation changed: '+name)
    rows,plans,parsers=[],[],[]
    for case in cohort['cases']:
        for folder,records in (('audits',rows),('plans',plans),('parser',parsers)):
            path=run/folder/f"{case['case_id']}.json"
            if not path.exists():continue
            row=json.loads(path.read_text())
            if row['case_id']!=case['case_id']: raise ValueError('Checkpoint case mismatch')
            if folder=='audits':
                for linked,key in (('plans','plan_sha256'),('parser','parser_sha256')):
                    if row.get('audit_completed') and key not in row:raise ValueError('Audit linkage missing')
                    if key in row and digest(run/linked/path.name)!=row[key]:raise ValueError('Audit artifact changed')
            records.append(row)
    assessed=[r for r in rows if r.get('audit_completed')]
    baseline=[r for r in rows if r.get('baseline_verified')]
    returned=[r for r in assessed if r.get('plan_present') and 'instruction_violations' in r]
    compliant=[r for r in assessed if r.get('compliant_pass')]
    timed=[r for r in plans if not r.get('timing_unknown')]
    cost=sum((Decimal(s.get('parser',{}).get('cost_usd','0')) for p in parsers
              for s in (p.get('brief') or {}).get('sentences',[])),Decimal(0))
    return dict(cohort=cohort,rows=rows,plans=plans,parsers=parsers,assessed=assessed,baseline=baseline,
                returned=returned,compliant=compliant,timed=timed,cost_usd=str(cost))


def metrics(group):
    return dict(audit_attempts=len(group['rows']),completed_audits=len(group['assessed']),
        planned_maps=len(group['plans']),returned_plans=len(group['returned']),
        baseline_checked=len(group['baseline']),baseline_broken=sum(bool(r['baseline_breaks_instruction']) for r in group['baseline']),
        reader_broken=sum(bool(r['instruction_violations']) for r in group['returned']),
        checked_passes=sum(bool(r.get('passed')) for r in group['assessed']),compliant=len(group['compliant']),
        mean_move_delta=mean(r['move_delta'] for r in group['compliant']) if group['compliant'] else None,
        mean_pipeline_wall_s=mean(r['pipeline_wall_s'] for r in group['timed']) if group['timed'] else None,
        timing_unknown=sum(bool(r.get('timing_unknown')) for r in group['plans']),cost_usd=group['cost_usd'])


def build():
    CONTROL.mkdir(parents=True,exist_ok=True)
    first=read_group(FIRST)
    # Render original report into a snapshot, not over the combined output.
    temp=CONTROL/'first-report';temp.mkdir(exist_ok=True)
    previous=original.HERE
    try:
        original.HERE=temp
        original.build()
    finally:original.HERE=previous
    text=(temp/'BRIEF.md').read_text()
    unknown=[r['case_id'] for r in first['plans'] if r.get('timing_unknown')]
    if unknown:
        timed=first['timed']
        correction=(f'Mean pipeline wall time: {mean(r["pipeline_wall_s"] for r in timed):.1f} s on {len(timed)} known timings; '
                    f'{len(unknown)} interrupted attempts have unknown planner wall time and are excluded from this mean; '
                    'excludes the independent fresh audit.')
        text=re.sub(r'Mean pipeline wall time: .*?; excludes the independent fresh audit\.',correction,text)
        text+='\n## First-cohort execution interruption\n\n'
        text+=('The original foreground controller stopped between turns. '+', '.join(unknown)+
               ' had admitted claims but no saved result. Their claims remain, they were not rerun, and they count as failures. '
               'Original instructions, cohort/preregistration hashes, parses, solver/search settings and completed plans were preserved. '
               'This is an execution limitation in addition to the 14 statically impossible instructions; it is not evidence of instruction infeasibility. '
               'Recovery provenance is `runs/brief-followup/interruption.json`. A detached controller continues the other maps.\n')
    output={'first':metrics(first)}
    if (SECOND/'cohort.json').exists():
        second=read_group(SECOND)
        if second['cohort']['first_cohort_sha256']!=digest(FIRST/'cohort.json'): raise ValueError('Second cohort has another first cohort')
        if [c['input_sha256'] for c in first['cohort']['cases']]!=[c['input_sha256'] for c in second['cohort']['cases']]:raise ValueError('Second cohort changed maps/order')
        output['second']=metrics(second)
        m=output['second'];cohort=second['cohort'];counts=Counter(c['type'] for c in cohort['cases'])
        unmatched=sum(c.get('instruction_selection',{}).get('vacuous',False) for c in cohort['cases'])
        none=sum(not c.get('sentence') for c in cohort['cases'])
        text+='\n# D222: second cohort selected by a declared static screen\n\n'
        text+=(f'Status: {"complete" if m["completed_audits"]==30 else "running / partial"}. Same 30 maps and order; seed {cohort["seed"]}. '
               'This is **selection by a stated necessary-condition screen**, not an unselected feasibility rate. The first cohort above remains the original draw; its 14/30 impossibility count is itself a finding. '
               'The complete candidate universe and draw were frozen in `runs/brief2/cohort.json` / `preregister.json` before paid reading or planning. '
               'No stored plan outcome entered selection.\n\n')
        universes=cohort['candidate_universe']
        text+=(f'{sum(u["candidate_count"] for u in universes):,} declared candidates, {sum(u["admitted_count"] for u in universes):,} not proved impossible. '
               f'The seeded draw contains {counts["close_bay"]} CloseBay, {counts["max_tiers"]} MaxTiers and {counts["dg_keep_away"]} DG instructions. '
               'Type balance is constrained by what survives the screen: all bay closures fail on these maps, so no CloseBay was substituted. '
               f'Selected static impossibilities: {sum(c["static_screen"]["proven_impossible"] for c in cohort["cases"])}/30. '
               f'Maps with no admissible instruction: {none}/30. Physical-cap/no-op fallbacks: {cohort["physical_cap_fallback_count"]}/30. '
               f'DG instructions with no matching active load or retained cargo: **{unmatched}** in this second cohort, versus **4** in the first. '
               'Passing a static screen still requires construction, finishing and a full checker; no feasibility claim follows from admission.\n\n')
        text+='| Cohort / arm | Instructions broken / assessed returned plans | Checked passes / all maps | Checked intended-compliant passes / all maps |\n| --- | ---: | ---: | ---: |\n'
        for label,g in (('First, without reader',first),('First, with Spark reader',first),('Second, without reader',second),('Second, with Spark reader',second)):
            if 'without' in label:
                broken=sum(r['baseline_breaks_instruction'] for r in g['baseline']);n=len(g['baseline']);passed=n;ok=n-broken
            else:
                broken=sum(bool(r['instruction_violations']) for r in g['returned']);n=len(g['returned']);passed=sum(bool(r.get('passed')) for r in g['assessed']);ok=len(g['compliant'])
            text+=f'| {label} | {broken}/{n} | {passed}/30 | {ok}/30 |\n'
        text+=(f'\nSecond-cohort saved planner outcomes: {m["planned_maps"]}/30; fresh audits completed without audit error: {m["completed_audits"]}/30. '
               f'Exact voyage parses: {sum(r.get("parse_exact",False) for r in second["assessed"])}/{m["completed_audits"]} audited. '
               f'Mean extra weighted nonproductive moves on {m["compliant"]} paired compliant plans: {m["mean_move_delta"] if m["mean_move_delta"] is not None else "pending"}. '
               f'Mean pipeline wall time: {round(m["mean_pipeline_wall_s"],1) if m["mean_pipeline_wall_s"] is not None else "pending"} seconds; independent audits excluded. '
               f'Measured second-cohort parsing spend: ${m["cost_usd"]}. No plan is repaired or retried. '
               'Spark is the reader in both rows; these are not final fine-tuned-reader results, and the checker does not detect a language misreading.\n\n')
        text+='| Map | Drawn instruction | Stored baseline breaks | Reader checked pass | Intended instruction obeyed | Δ moves | Pipeline seconds |\n| --- | --- | --- | --- | --- | ---: | ---: |\n'
        audit_by={r['case_id']:r for r in second['rows']}
        for case in cohort['cases']:
            row=audit_by.get(case['case_id'],{})
            text+=f'| {case["instance_id"]} | {case["sentence"]} | {row.get("baseline_breaks_instruction","pending")} | {row.get("passed","pending")} | {row.get("instruction_obeyed","no plan" if row else "pending")} | {row.get("move_delta","—")} | {round(row["pipeline_wall_s"],1) if "pipeline_wall_s" in row else "—"} |\n'
        text+='\nBoth cohorts share a single detached controller with at most two native-one map/audit workers and D206 admission at every new job. See `runs/brief-followup/controller.json`, `run.log` and terminal `completion.json`. Refusal explanations use separate proof evidence in `REFUSALS.md`; no checker report is fabricated for a preplanning refusal.\n'
    target=HERE/'BRIEF.md';tmp=target.with_suffix('.combined.tmp');tmp.write_text(text);tmp.replace(target)
    write(CONTROL/'summary.json',output)
    return output


if __name__=='__main__': print(json.dumps(build()))

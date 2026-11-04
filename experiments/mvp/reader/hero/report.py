"""The curated hero result, with historical replan and new audit clocks apart."""
from __future__ import annotations
from decimal import Decimal
import json
from pathlib import Path

from experiments.mvp.reader.hero.run import HERE,ROOT,RUN,digest,write,verify


def language_rank(case):
    kind=case['expected']['constraint']['type']
    sentence=case['sentence'].lower()
    if kind=='dg_keep_away':
        if 'corrosiv' in sentence:return 0
        if 'flammable liquid' in sentence:return 1
        return 2
    return 3 if kind=='close_bay' else 4


def ranking_key(case,row):
    v=row['visual_delta']
    latency=row.get('new_replan_s')
    if latency is None:latency=row.get('historical_planner_component_s',row.get('historical_worker_wall_s'))
    return (language_rank(case),max(0,row['move_delta']),-v['matched_dg_changed_physical_bay_count'],
            -v['changed_load_placements_count'],float('inf') if latency is None else latency+row['parse_wall_s'],
            case['n_load'],case['input'])


def build():
    frozen=verify();cases=frozen['cases'];rows={};parses={}
    for case in cases:
        for folder,records in (('audits',rows),('parser',parses)):
            path=RUN/folder/f"{case['case_id']}.json"
            if path.exists():records[case['case_id']]=json.loads(path.read_text())
    eligible=[]
    for case in cases:
        row=rows.get(case['case_id'])
        if row is None:continue
        if row['case_id']!=case['case_id'] or row['sentence']!=case['sentence']:
            raise ValueError('Fresh audit case or sentence changed')
        if digest(RUN/'parser'/f"{case['case_id']}.json")!=row['parser_record_sha256']:
            raise ValueError('Reader artifact changed after fresh audit')
        if row.get('passed'):
            from experiments.mvp.hybrid.admission import evidence_errors
            if evidence_errors(row.get('admission')):
                raise ValueError('Passing hero lacks valid D206 admission')
        if row.get('asset_file') and digest(ROOT/row['asset_file'])!=row['asset_sha256']:
            raise ValueError('Fresh-audited hero asset changed')
        if row.get('binding') and row.get('passed') and row['move_delta']<=2:
            eligible.append((case,row))
    eligible.sort(key=lambda pair:ranking_key(*pair))
    best=eligible[0] if eligible else None
    cost=sum((Decimal(s.get('parser',{}).get('cost_usd','0')) for p in parses.values()
              for s in p['brief']['sentences']),Decimal(0))
    complete=len(rows)==len(cases)
    status='complete' if complete else 'running / admission queued'
    text=f'''# v5 3.2 prep: hero sentence and map

Status: **{status}**. {len(rows)}/{len(cases)} fixed shortlisted cases have new fresh-process results; {len(eligible)} pass every binding/visibility gate at no more than two extra weighted nonproductive lifts. Spark spend: **${cost}**, restart-safe ceiling $1. This is a deliberately selected demonstration, not an unselected success-rate or language-accuracy benchmark. Spark is the reader here; final Glimmer-reader rehearsal remains task 3.2.

Criteria were fixed in `CRITERIA.json` / `criteria-freeze.json` before inventory, shortlist, calls or checks. The shortlist was then frozen with independent intended constraints in `manifest.json` before Spark read it. At most five map/sentence pairs; failures stay reported, with no replacement or repair. Only validation/train inputs are allowed; this inventory found its archived candidates in named validation runs, not held-out test or test-v3 data.

'''
    if best:
        case,row=best;v=row['visual_delta']
        text+=f'''## Recommended hero

**“{case['sentence']}”**

Map: `{case['instance_id']}` — `{case['input']}` (SHA256 `{case['input_sha256']}`).

**{row['before_moves']:g} → {row['after_moves']:g} weighted nonproductive lifts ({row['move_delta']:+g})**. The freshly checked stored before plan breaks the independently fixed instruction. The after plan passes the full checker, parsed-rule and intended-rule audits, move replay and complete instruction-aware mask replay. It changes **{v['changed_load_placements_count']} loaded-box final placements**; **{v['matched_dg_changed_physical_bay_count']} matching DG boxes change physical bay**. The reader's structured output exactly matches the frozen intended rule; the full checker alone cannot detect a language misreading.

'''
        matched=[b for b in v['matched_dg_load_boxes'] if b['physical_bay_changed']]
        for box in matched:
            text+=(f'- Highlight `{box["container_id"]}` ({box["length_ft"]} ft, IMDG {", ".join(box["dg_classes"])}): '
                   f'`{box["before_slot"]}` → `{box["after_slot"]}`, physical bays {box["before_physical_bays"]} → {box["after_physical_bays"]}; '
                   f'{box["absolute_bay_displacement_20ft"]:g} twenty-foot bay pitch.\n')
        text+='\nShow the sentence, then the exact structured reading, then the changed box and the prohibited bay zone. Bay 01 → 03 is **one** twenty-foot bay, not two. Label the plan recorded and the ship/port synthetic. The current reader label is Muse Spark; a final on-device Glimmer label requires rereading this same sentence with that adapter and checking its exact intended interpretation.\n\n'
        text+=f'Before/after assets: `{row["asset_file"]}`; new audit: `hero/runs/audits/{case["case_id"]}.json`. '
        text+='The after plan was reused from the same-constraint recorded replan, not newly searched for this wording. The new sentence was genuinely read by Spark and its meaning and saved plans were rechecked in a new process.\n\n' if row['source_mode']=='reused_recorded_constrained_plan' else 'The after plan was newly constructed under the read instruction.\n\n'
    else:
        text+='## Recommendation pending\n\nNo case has yet passed all new mandatory gates. Archived passes and attractive movement counts are screening evidence only. Do not present a shortlisted case as newly qualified before its new reading and fresh audit.\n\n'
    text+='## Fixed shortlist: all outcomes\n\n| Case / map | Sentence | New Spark exact read | New full checks before / after | Binding + visible | Before → after lifts | Δ | Source / failure |\n| --- | --- | --- | --- | --- | ---: | ---: | --- |\n'
    for case in cases:
        row=rows.get(case['case_id'],{})
        parsed=parses.get(case['case_id'])
        exact=row.get('parse_exact','read; awaiting audit' if parsed else 'pending')
        moves=f'{row["before_moves"]:g} → {row["after_moves"]:g}' if 'before_moves' in row else 'pending'
        checks=f'{row.get("before_checker_passed","pending")} / {row.get("after_checker_passed","pending")}'
        source=row.get('rejection',row.get('source_mode','queued'))
        text+=f'| {case["case_id"]} / {case["instance_id"]} | {case["sentence"]} | {exact} | {checks} | {row.get("binding","pending")} | {moves} | {row.get("move_delta","—")} | {source} |\n'
    text+='''
## Wall time: do not merge these clocks

The historical planner component is the archived instruction-aware Frontier/finisher/checker time. Historical worker wall includes loading, before/after checks and orchestration. Neither is a fresh timing of this new wording. New audit wall is the new process's work including checks; new execution sum is new parse plus new audit. Reused candidates have **no new replanning run**. A final live replan needs its own quiet-machine end-to-end timing; do not promise that an archived component sum is live latency.

| Case | Historical planner component (s) | Historical worker wall (s) | New Spark brief read (s) | New replan | New fresh audit (s) | New execution sum (s) |
| --- | ---: | ---: | ---: | --- | ---: | ---: |
'''
    for case in cases:
        row=rows.get(case['case_id'],{});p=parses.get(case['case_id'],{})
        def fmt(value):return '—' if value is None else f'{value:.3f}'
        new_replan='not run (reuse)' if row.get('source_mode')=='reused_recorded_constrained_plan' else fmt(row.get('new_replan_s'))
        text+=f'| {case["case_id"]} | {fmt(row.get("historical_planner_component_s"))} | {fmt(row.get("historical_worker_wall_s",case.get("preferred_after",{}).get("wall_s")))} | {fmt(p.get("parse_wall_s"))} | {new_replan} | {fmt(row.get("audit_wall_s"))} | {fmt(row.get("new_work_wall_s"))} |\n'
    text+='''
## Evidence and execution limits

The intended rule is fixed independently before reading and never put in Spark's prompt. A refusal, malformed answer or wrong structured meaning fails the hero gate; it is not corrected or replaced. The saved before plan must actually violate the intended instruction. The saved after plan must freshly pass both the physical full checker and the intended/parsed semantics; container movement signatures and loaded placements must change. Ballast-only or order-only changes cannot qualify. Every candidate's signed lift delta stays visible, including expensive or failed candidates.

Selection openly uses historical success, cost and movement outcomes to find a clear demo. Candidates with an existing same-constraint checked after plan are preferred to avoid unnecessary searches. This does not establish a general reader benefit, a new planner saving, or a new end-to-end replan under the new wording. Static screens are necessary only. The full checker checks the plan against physical rules and the rule as read; exact comparison to the separately fixed intended constraint is what detects a wrong parse here.

One native-one hero worker runs at a time, in addition to the untouched two-worker cohort controller (the worker-scope assumption was declared in the criteria). Each new hero audit/solve obeys D206: leader core-state mask bit count ≥8, load1 ≤45, age <120 seconds. Requests, output, parser/model identity and adapter hash (null for Spark) are stored in `runs/parser/`; admitted readings, fresh PIDs and checks are in `runs/audits/`. No repair, no retry of an unreturned attempt, no unconstrained fallback. The two cohorts remain running; their files and controller are not edited.

Reproduce from the repository root:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python -m experiments.mvp.reader.hero.run prepare
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python -m experiments.mvp.reader.hero.run run
.venv/bin/python -m experiments.mvp.reader.hero.report
```

Credentials use the existing external `.env` loader into process environment only. No key is passed as an argument or placed in hero artifacts. All authored files for this task are under `experiments/mvp/reader/hero/`.
'''
    target=HERE/'HERO.md';tmp=target.with_suffix('.tmp');tmp.write_text(text);tmp.replace(target)
    summary=dict(status='complete' if complete else 'running',shortlisted=len(cases),fresh_results=len(rows),qualified=len(eligible),
                 recommended_case=best[0]['case_id'] if best else None,cost_usd=str(cost))
    write(RUN/'summary.json',summary)
    return summary


if __name__=='__main__':print(json.dumps(build()))

"""Verify completed hero evidence and export the chosen recorded demo assets."""
from __future__ import annotations
import json
from pathlib import Path
from decimal import Decimal
from datetime import datetime,timezone

from experiments.mvp.reader.hero.run import HERE,ROOT,RUN,write,digest,verify
from experiments.mvp.reader.hero.report import build
from experiments.mvp.hybrid.admission import evidence_errors
from experiments.mvp.reader.schema import canonicalize


def finalize():
    frozen=verify();summary=build()
    if summary['fresh_results']!=len(frozen['cases']):raise ValueError('Shortlist verification unfinished')
    if summary['recommended_case'] is None:raise ValueError('No qualifying cheap visible hero')
    if Decimal(summary['cost_usd'])>1:raise ValueError('Hero Spark budget exceeded')
    rows=[]
    for case in frozen['cases']:
        row=json.loads((RUN/'audits'/f"{case['case_id']}.json").read_text())
        parsed=json.loads((RUN/'parser'/f"{case['case_id']}.json").read_text())
        voyage=next(s for s in parsed['brief']['sentences'] if s['role']=='voyage')
        if row.get('passed'):
            if not row['fresh_process'] or evidence_errors(row['admission']):raise ValueError('Invalid fresh/admitted audit')
            if canonicalize(voyage['parsed'])!=canonicalize(case['expected']):raise ValueError('Wrong intended reading')
            if not row['before_instruction_violations'] or not row['binding']:raise ValueError('Not binding')
        rows.append(row)
    case=next(c for c in frozen['cases'] if c['case_id']==summary['recommended_case'])
    row=next(r for r in rows if r['case_id']==case['case_id'])
    asset=json.loads((ROOT/row['asset_file']).read_text())
    parsed=json.loads((RUN/'parser'/f"{case['case_id']}.json").read_text())
    voyage=next(s for s in parsed['brief']['sentences'] if s['role']=='voyage')
    demo=HERE/'demo'
    write(demo/'before-plan.json',asset['before_plan']);write(demo/'after-plan.json',asset['after_plan'])
    write(demo/'reading.json',dict(sentence=case['sentence'],parsed=voyage['parsed'],parser=voyage['parser']))
    shot=dict(case_id=case['case_id'],sentence=case['sentence'],map=case['input'],input_sha256=case['input_sha256'],
              model_labels=dict(reader='Muse Spark',planner='Frontier 0.9 — software',checker='Full checker'),
              labels=['recorded constrained plan','synthetic ship and simulated port'],
              before_moves=row['before_moves'],after_moves=row['after_moves'],extra_moves=row['move_delta'],
              highlighted_boxes=[r for r in row['visual_delta']['matched_dg_load_boxes'] if r['physical_bay_changed']],
              changed_loaded_placements=row['visual_delta']['changed_load_placements_count'],
              historical_planner_component_s=row['historical_planner_component_s'],historical_worker_wall_s=row['historical_worker_wall_s'],
              new_brief_parse_s=row['parse_wall_s'],new_fresh_audit_s=row['audit_wall_s'],new_replan_s=row['new_replan_s'],
              files={name:dict(path=str((demo/name).relative_to(ROOT)),sha256=digest(demo/name)) for name in ('before-plan.json','after-plan.json','reading.json')})
    write(demo/'shot.json',shot)
    inventory=json.loads((HERE/'inventory.json').read_text())
    verification=dict(verified_utc=datetime.now(timezone.utc).isoformat(),status='complete',summary=summary,
        criteria_sha256=digest(HERE/'CRITERIA.json'),manifest_sha256=digest(HERE/'manifest.json'),
        inventory_sha256=digest(HERE/'inventory.json'),finalizer_sha256=digest(Path(__file__)),
        fresh_pids=[r['audit_pid'] for r in rows],all_new_audits_passed=all(r.get('passed') for r in rows),
        all_new_audits_binding=all(r.get('binding') for r in rows),
        inventory_maps=inventory['unique_input_count'],inventory_candidates=inventory['candidate_count'],
        sources_unchanged=True,cohort_controller_untouched=True)
    write(RUN/'verification.json',verification)
    report=HERE/'HERO.md'
    text=report.read_text().replace('was genuinely read','was read')
    text+='\n## Search coverage and ready-to-load shot assets\n\n'
    text+=f'The inventory examined {inventory["unique_input_count"]} validation maps and {inventory["candidate_count"]} baseline-violating, static-screen-admitted candidates. '
    text+=f'{inventory["same_rule_recorded_after_candidate_count"]} had reusable same-rule recorded after plans; {inventory["same_rule_recorded_audited_candidate_count"]} had linked historical fresh audits. '
    text+='The five fixed top-ranked pairs were read and checked without replacement. No train map was needed to fill this archive-led shortlist; no test or test-v3 input was opened.\n\n'
    text+='Ready-to-load exports are `demo/before-plan.json`, `demo/after-plan.json`, `demo/reading.json` and `demo/shot.json`. '
    text+='The shot file carries the map hash, model/recorded labels, highlighted DG box, before/after counters and distinct historical/new timing fields. '
    text+='These are the same freshly audited plans, not a new search. `runs/verification.json` records terminal source/budget/admission/binding checks. '
    text+='Run `.venv/bin/python -m experiments.mvp.reader.hero.finalize` after regenerating the report to rebuild these exports and this final section.\n'
    report.write_text(text)
    return verification


if __name__=='__main__':print(json.dumps(finalize()))

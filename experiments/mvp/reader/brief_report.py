"""Rebuild D220 BRIEF.md from frozen cohort, parse, plan and fresh audit rows."""
from __future__ import annotations
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
from statistics import mean
import hashlib
from experiments.mvp.reader.brief_ablation import summarize, report_text

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUN = HERE/'runs/brief'


def build():
    cohort = json.loads((RUN/'cohort.json').read_text())
    cases = cohort['cases']
    prereg = json.loads((RUN/'preregister.json').read_text())
    if hashlib.sha256((RUN/'cohort.json').read_bytes()).hexdigest() != prereg['cohort_sha256']:
        raise ValueError('Cohort preregistration hash mismatch')
    implementation = RUN/'implementation.json'
    if implementation.exists():
        frozen = json.loads(implementation.read_text())
        for name, sha in frozen['sources'].items():
            if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != sha:
                raise ValueError('Frozen source changed: '+name)
    rows, parsers, plans = [], [], []
    for case in cases:
        for folder, target in (('audits', rows), ('parser', parsers), ('plans', plans)):
            path = RUN/folder/f"{case['case_id']}.json"
            if path.exists():
                value = json.loads(path.read_text())
                if value['case_id'] != case['case_id']:
                    raise ValueError('Checkpoint case mismatch')
                if folder == 'audits':
                    if value.get('audit_completed') and any(k not in value for k in ('plan_sha256','parser_sha256')):
                        raise ValueError('Completed audit lacks linked artifact hashes')
                    for linked, key in (('plans','plan_sha256'), ('parser','parser_sha256')):
                        linked_path = RUN/linked/path.name
                        if key in value and hashlib.sha256(linked_path.read_bytes()).hexdigest() != value[key]:
                            raise ValueError('Fresh audit artifact hash mismatch')
                target.append(value)
    assessed = [r for r in rows if r.get('audit_completed')]
    baseline = [r for r in rows if r.get('baseline_verified')]
    present = [r for r in rows if r.get('plan_present')]
    evaluated = [r for r in present if 'instruction_violations' in r and r.get('audit_completed')]
    passes = [r for r in assessed if r.get('passed')]
    compliant = [r for r in assessed if r.get('compliant_pass')]
    paired = [r for r in compliant if r.get('baseline_verified')]
    binding_pairs = [r for r in paired if r.get('baseline_breaks_instruction')]
    sentence_rows = [s for r in parsers for s in r['brief']['sentences']]
    cost = sum((Decimal(s.get('parser',{}).get('cost_usd','0')) for s in sentence_rows), Decimal(0))
    impossible = [c for c in cases if c.get('static_screen',{}).get('proven_impossible')]
    no_dg = [c for c in cases if c.get('instruction_selection',{}).get('vacuous')]
    finished = len(assessed)==30 and len(plans)==30
    before_broken = sum(r.get('baseline_breaks_instruction',False) for r in baseline)
    after_broken = sum(bool(r['instruction_violations']) for r in evaluated)
    text = f'''# D220: a required reader before every brief plan

Status: {'complete' if finished else 'running / partial'}; validation only, first 30 small maps in ascending stored `val-0.9` result filename order (the 55-map validation cohort), with no success or violation filtering. Spark is the active reader; the Glimmer adapter is a strict, provenance-recording plug-in seam, unavailable until attached. These are **not fine-tuned-reader results**. D219's gates are unchanged.

| Arm | Instructions broken among returned plans | Full-checker / parsed-constraint passes | Checked intended-instruction passes |
| --- | ---: | ---: | ---: |
| Stored Frontier 0.9, without reader | {before_broken}/{len(baseline)} | {len(baseline)}/30 | {sum(not r.get('baseline_breaks_instruction',True) for r in baseline)}/30 |
| Same standing brief + one voyage instruction, Spark reader | {after_broken}/{len(evaluated)} | {len(passes)}/30 | {len(compliant)}/30 |

A missing plan is a failure and has no instruction-compliance success. The broken-instruction denominator counts returned plans; pass denominators remain all 30, including static impossibilities and failures. Fresh audit attempts: {len(rows)}/30; completed without audit error: {len(assessed)}/30. Returned plans with unassessed instruction compliance: {len(present)-len(evaluated)}. Exact voyage parses: {sum(r.get('parse_exact',False) for r in rows)}/{len(rows)} audited. Confirmed binding replans (also exact intended parse, violating baseline, changed container plan): {sum(r.get('reader_binding_credit',False) for r in assessed)}/30.

Mean move cost of obeying, on {len(paired)} paired checked compliant plans: {f'{mean(r["move_delta"] for r in paired):+.3f}' if paired else 'pending'} weighted nonproductive moves versus each stored unconstrained plan. This includes instructions already obeyed without the reader; it is not a claim that the reader saves moves. Mean pipeline wall time: {f'{mean(p["pipeline_wall_s"] for p in plans):.1f} s' if plans else 'pending'}, observed sum {sum(p.get('pipeline_wall_s',0) for p in plans):.1f} s; excludes the independent fresh audit. Spark measured spend: ${cost} (dedicated restart-safe allocation ≤ $2).

## Fixed cohort and static screens

`runs/brief/cohort.json` and `preregister.json` freeze the seed, map order, one sentence and independent expected constraint per map before paid parsing or planning. No map or instruction is replaced after a failed screen. Types cycle CloseBay, MaxTiers, DgKeepAway (10 each); values derive from ship geometry/cargo, with a recorded fixed class-3 assumption when a DG instruction has no matching cargo. Static-impossibility rows: {len(impossible)}/30. Vacuous DG restrictions: {len(no_dg)}/30 maps ({len(no_dg)}/10 DG cases); they remain in the headline denominator and are not evidence of meaningful DG restriction handling. The screen is a sufficient proof only, not a feasibility oracle.

'''
    text += '| Static-screen stratum | Maps | Stored baseline breaks | Reader checked compliant passes |\n| --- | ---: | ---: | ---: |\n'
    by_id = {r['case_id']:r for r in rows}
    for label, is_impossible in (('Proven impossible', True), ('No static impossibility proof', False)):
        group = [c for c in cases if c.get('static_screen',{}).get('proven_impossible',False) == is_impossible]
        audited = [by_id[c['case_id']] for c in group if c['case_id'] in by_id]
        text += f'| {label} | {len(group)} | {sum(r.get("baseline_verified",False) and r.get("baseline_breaks_instruction",False) for r in audited)}/{sum(r.get("baseline_verified",False) for r in audited)} assessed | {sum(r.get("audit_completed",False) and r.get("compliant_pass",False) for r in audited)}/{len(group)} |\n'
    if paired:
        text += f'\nOn the {len(paired)} compliant paired plans, stored/new mean move costs are {mean(r["baseline_moves"] for r in paired):.3f}/{mean(r["moves"] for r in paired):.3f}. '
    if binding_pairs:
        text += f'On the {len(binding_pairs)} compliant pairs whose baseline broke the instruction, mean additional cost is {mean(r["move_delta"] for r in binding_pairs):+.3f} moves. '
    text += '\n\n'
    text += '| Instruction family | Maps | Static impossible | Vacuous | Fresh compliant passes |\n| --- | ---: | ---: | ---: | ---: |\n'
    for kind in ('close_bay','max_tiers','dg_keep_away'):
        group = [c for c in cases if c.get('type') == kind]
        audited = [by_id[c['case_id']] for c in group if c['case_id'] in by_id]
        text += f'| {kind} | {len(group)} | {sum(c["static_screen"]["proven_impossible"] for c in group)} | {sum(c.get("instruction_selection",{}).get("vacuous",False) for c in group)} | {sum(r.get("audit_completed",False) and r.get("compliant_pass",False) for r in audited)}/{len(group)} |\n'
    text += '\nAll statically impossible rows remain below. In particular, this cohort supplies no successful CloseBay evidence when every CloseBay row is statically impossible.\n\n'
    for case in impossible:
        text += f'- {case["case_id"]} ({case["instance_id"]}): {json.dumps(case["static_screen"].get("reasons", []))}\n'
    text += '''
## Pipeline and semantics

The frozen historical `brief_planner.plan_brief(instance, VoyageBrief(standing, voyage), parser=...)` used by these cohorts always invokes the reader, including an empty brief. Each newline in the standing part is a separate sentence; voyage instructions are explicit sentence items. The common standing text is “One port per hatch.” Its accepted parse enables the port-blocked start without restricting other starts or imposing a cover allocation. Accepted voyage constraints intersect and bind construction, master search, scored candidates, final occupancy and finishing. Malformed or refused sentences are visible in per-sentence evidence and do not bind; no software guess or correction replaces their parse. The independent intended label never enters the planner.

Each accepted constraint retains the source sentence, parser/model and adapter SHA256 (null for Spark) in evidence. `pipeline.GlimmerParser(generate=callback, adapter_sha256=...)` takes the adapter's untouched JSON through the same strict schema/context validator. It has no Spark fallback. This report makes no claim that the standing preference needs language understanding or that a fine-tune beats Spark.

Every returned plan goes through Frontier 0.9 → finisher → full checker, then a fresh process checks the saved baseline and saved new plan, parsed semantics and independent intended semantics. Parsed-constraint necessary capacity/footprint screens and fixed retained conflicts may prove impossibility before search; these remain failures in the 30-map denominator. No repair or unconstrained fallback. New demo integration uses `public_brief.plan_brief` and its release gate; the legacy brief and single-sentence APIs are retained only as historical experiment entry points. These cohorts measure the frozen implementation, not the new gate.

## Standing-versus-empty ablation

'''
    ablation = summarize(cases, ROOT)
    (RUN/'ablation.json').write_text(json.dumps(ablation,indent=2)+'\n')
    text += report_text(ablation)+'\n'
    text += '''
## Per-map fresh audit

| Map | Instruction | Static impossible | Before breaks | With reader pass | Obeys instruction | Δ moves | Pipeline seconds |
| --- | --- | --- | --- | --- | --- | ---: | ---: |
'''
    by_id = {r['case_id']:r for r in rows}
    for case in cases:
        row = by_id.get(case['case_id'],{})
        text += f'| {case["instance_id"]} | {case["sentence"]} | {case.get("static_screen",{}).get("proven_impossible",False)} | {row.get("baseline_breaks_instruction","pending")} | {row.get("passed","pending")} | {row.get("instruction_obeyed","no plan" if row else "pending")} | {row.get("move_delta","—")} | {round(row["pipeline_wall_s"],1) if "pipeline_wall_s" in row else "—"} |\n'
    text += '''
## Reproduction and limits

From the repository root, with the existing offline environment:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python -m experiments.mvp.reader.brief_cohort
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python -m experiments.mvp.reader.run_brief prepare
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python -m experiments.mvp.reader.run_brief run
.venv/bin/python -m experiments.mvp.reader.brief_report
```

The running job refreshes this report through `watch_brief.py`; `runs/brief/completion.json` records terminal evidence verification. Checkpoints do not repeat completed or claimed planner attempts. Admission before each new solve/audit uses `experiments/fleet/core-state.json`: mask bit-count ≥ 8, load1 ≤ 45, age in [0,120) seconds. There are at most two subprocess workers, one native solver worker each; admission evidence and recorded PIDs accompany rows. Search budgets match each stored baseline, finishing is 300 seconds per attempt and construction 120 seconds. Timed search on a shared machine means this is instruction compliance versus stored plans, not a matched performance or causal move-saving experiment. Only validation inputs were used; no test or test-v3 inputs were opened. Credentials come from the external parent Projects `.env` into process environment only. No key is in arguments or output.
'''
    notes = Path(__file__).with_name('REVIEW-FIXES.md')
    if notes.exists():
        text += '\n' + notes.read_text()
    (HERE/'BRIEF.md').write_text(text)
    return dict(audits=len(rows), plans=len(plans), baseline_broken=before_broken, reader_broken=after_broken,
                passes=len(passes), compliant=len(compliant), cost_usd=str(cost))


if __name__ == '__main__':
    print(json.dumps(build()))

"""Build REFUSALS.md from actual paid outputs and software proof evidence."""
from __future__ import annotations
from decimal import Decimal
import argparse
import json
from pathlib import Path
import time

from experiments.mvp.reader.refusal_demo import RUN, HERE, ROOT, verify, explain
from experiments.mvp.reader.run_brief import write, digest


def build():
    manifest=verify()
    examples=[];parses=[]
    for case in manifest['cases']:
        for folder,records in (('examples',examples),('parses',parses)):
            path=RUN/folder/f"{case['case_id']}.json"
            if path.exists():records.append(json.loads(path.read_text()))
    cost=sum((Decimal(r.get('parser',{}).get('cost_usd','0')) for r in parses),Decimal(0))
    cost+=sum((Decimal(r.get('explainer',{}).get('cost_usd','0')) for r in examples),Decimal(0))
    audit_path=RUN/'witness-audit.json'
    audited=json.loads(audit_path.read_text()) if audit_path.exists() else None
    text=f'''# Task 1.4: refusals grounded in the screen's proof

Status: {sum(r.get('passed',False) for r in examples)}/4 curated live-Spark examples recorded; {'fresh alternative witness checked' if audited and audited.get('passed') else 'fresh alternative witness audit pending'}. These are demonstration cases and tests, not a refusal-accuracy benchmark or final fine-tuned-reader results. No new plan is attempted for a static impossibility and no checker report is fabricated for it.

The proof identifies every load container, its length, required cells, size-compatible footprints before/after the instruction, no-fit IDs and fixed retained conflicts. It gives total TEU/cell demand against open cells, with container-specific restrictions separate. Those are necessary-condition proofs: a passed screen is never called feasible.

Spark selects and orders exact cited fact sentences supplied by that proof. This is a deliberately constrained explanation contribution, **not free-form factual generation**: validated output cannot alter a number, invent a box, omit a required reason, invent a checker finding or claim an uncertified alternative works. Invalid model output is rejected; a software fallback is not labelled a Spark explanation. Proof, prompt, output and module SHA256, model, request ID, cost and cited fact IDs accompany each saved explanation. Paid refusal reading/explanation spend so far: ${cost}; the shared D222 allocation is $1.90, leaving the previous $0.082778 within an overall $2 ceiling.

## Recorded examples

'''
    for case in manifest['cases']:
        row=next((r for r in examples if r['case_id']==case['case_id']),None)
        text+=f'### {case["case_id"]}\n\nInput: “{case["sentence"]}”\n\n'
        if row is None:
            text+='Pending its admitted fresh witness audit; no stronger alternative claim is shown.\n\n';continue
        if not row.get('passed'):
            text+='Reader mismatch: no expected interpretation was substituted and no explanation is presented as successful.\n\n';continue
        text+='Spark, recorded and validated:\n\n> '+row['explanation']+'\n\n'
        proof=row['proof']
        if 'proof' in proof:
            p=proof['proof']
            text+=(f'Arithmetic: **{p["demand_cells"]} TEU/cells needed; {p["open_cells"]} globally open cells left**; '
                   f'{len(p["nofit_ids"])} load containers have zero size-compatible allowed footprints. '
                   'No checker was called to establish this impossibility.\n\n')
            text+='| Container | Length (ft) | Cells needed | Compatible footprints before | Allowed footprints left |\n| --- | ---: | ---: | ---: | ---: |\n'
            for box in p['load_boxes']:
                text+=f'| {box["container_id"]} | {box["length_ft"]} | {box["demand_cells"]} | {box["size_compatible_footprints"]} | {box["allowed_footprints"]} |\n'
            text+='\n'
            if not proof['alternatives']:
                text+='No same-family alternative in the declared search survives the screen. No replacement instruction was invented.\n\n'
            for alt in proof['alternatives']:
                certificate='fresh full-checked stored witness' if alt.get('fresh_audit',{}).get('passed') else alt['certificate']
                text+=f'- Proposed separately: “{alt["sentence"]}” ({certificate}; numeric distance {alt["numeric_distance"]}). '
                text+='Requires the planner’s explicit choice and is never applied automatically. '
                text+='It satisfies this particular freshly checked stored plan; no guarantee is made for a new plan.\n' if alt.get('fresh_audit',{}).get('passed') else 'A screen-only suggestion still needs planning and a full checker.\n'
            text+='\n'
        text+=f'Evidence: `runs/refusals/examples/{case["case_id"]}.json`.\n\n'
    text+='''## Nearest alternatives and limits

The finite search preserves the instruction family. CloseBay changes only to other real bays and ranks 20-foot bay distance; MaxTiers changes only the cap in the same bay/zone and ranks absolute cap change; DG preserves class/reference and ranks clearance-distance change (1–20). It retains the nearest screen-admitted candidate and, if different, the nearest candidate satisfied by a stored checked plan. This is the nearest alternative **within that declared family and witness**, not proof of a globally closest voyage change. A weaker cap is explicitly labelled as relaxing the requested instruction; it requires a new planner choice. It never relaxes production rules or mutates the impossible instruction.

A historical checker flag is not a fresh certificate. The alternative example gets a fresh full check of the saved witness in the same shared two-worker pool, and separately checks the alternative against that plan. The hash must match before the explainer can select the witnessed alternative. This witness check is not a checker report for the original impossible instruction.

Loosening examples name `imdg.segregation.7_2_4`, “General dangerous-goods segregation table”, from `src/stowage/rules/catalogue.toml`. An application must supply authoritative existing-rule context; if it does not, the public flow refuses the relaxation and asks which rule rather than inventing one. Unclear requests ask for class, bay and distance; no guessed constraint enters planning.

## What the demo needs for one shot

Use `impossible-bay-2` for shot 5b: frozen first-cohort `brief-07`, `random.small.40100016.0.5`, sentence **“Bay 2 is closed.”** Load the input path/hash in `runs/refusals/manifest.json`. Show the reader’s exact CloseBay reading first. Then show **23 needed / 0 left**, with the 13 affected box IDs in the expandable proof table, and the recorded Spark explanation with its cited facts. Do not put a checker tick or a new plan beside this proof. No same-family bay-closure alternative survives on that map.

Use `loosening-dg` for shot 5a: **“Ignore the dangerous-goods segregation rules.”** Show the reader’s refusal, the actual rule name and the recorded Spark reason. Use `unclear-distance` to demonstrate a clarification instead of a guess. The fourth example demonstrates a separately chosen alternative once its fresh witness audit is present.

`refusal_card.read_recorded_card(case_id, parser=...)` performs the reading live and releases the cached Spark card only when that reading agrees with the recorded interpretation on the frozen instance. Pass the final Glimmer adapter through the existing parser interface for an on-device label; Spark is the reader in the measurements here. Label explanations “Muse Spark — recorded, bound to the shown proof.” A mismatch or malformed reading returns no cached explanation: show the reading/error and ask for confirmation or make a new bound explanation. Cached explanation playback needs no API key or new solve; a new Spark explanation needs the credential loaded into process environment by the existing loader.

The API `refusal_flow.review_sentence(...)` / `review_answer(...)` is ready for demo integration. It returns per-sentence parser provenance, the proof, grounded explanation, binding status and whether planning is permitted. A refused or malformed voyage sentence does not bind and blocks planning until explicit correction or removal; an impossible hard instruction still binds and prevents a plan. `plan_allowed` is per-sentence: never combine approvals with OR. Submit the entire request through `public_brief.plan_brief` so one unresolved voyage sentence blocks the whole request. An existing displayed plan is the previous plan, not a plan satisfying the unresolved request. Standing port-blocking remains a preference. It never applies a proposed alternative. No `app/` or `src/` integration was changed here.

## Verification and reproduction

Offline tests cover slot arithmetic, 20/40/45-foot footprints, DG no-fit cases, retained conflicts, witness provenance, nearest-family selection, rule context, clarification, duplicate/malformed JSON, forged or changed facts, unsupported claims, mandatory citations, stale/mismatched witness hashes, budget guards and the live-card reading gate. No test or test-v3 inputs are used.

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python -m pytest experiments/mvp/reader/test_refusal_evidence.py experiments/mvp/reader/test_refusal_explainer.py experiments/mvp/reader/test_refusal_flow.py experiments/mvp/reader/test_refusal_card.py
.venv/bin/python -m experiments.mvp.reader.refusal_report
```

`refusal_demo prepare` freezes the curated requests before calls; `refusal_demo explain` reuses completed parse/explanation checkpoints and waits for the scheduled alternative witness. It does not repeat completed calls or run a solver. Spark errors are recorded without provider bodies or secrets. Admission for the single fresh witness audit follows D206. No repair.
'''
    target=HERE/'REFUSALS.md';tmp=target.with_suffix('.tmp');tmp.write_text(text);tmp.replace(target)
    summary=dict(examples=len(examples),passed=sum(r.get('passed',False) for r in examples),cost_usd=str(cost),
                 witness_audited=bool(audited and audited.get('passed')))
    write(RUN/'summary.json',summary)
    return summary


def watch():
    began=time.time()
    while time.time()-began<4*3600:
        build()
        if (RUN/'witness-audit.json').exists():
            try:
                explain()
                result=build()
                write(RUN/'completion.json',dict(status='complete' if result['passed']==4 else 'needs_review',summary=result))
                return
            except Exception as error:
                write(RUN/'completion.json',dict(status='needs_review',error_type=type(error).__name__,
                          evidence=getattr(error,'evidence',{})))
                return
        time.sleep(15)
    write(RUN/'completion.json',dict(status='incomplete',reason='Witness audit not complete within monitor window'))


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__);cli.add_argument('--watch',action='store_true');args=cli.parse_args()
    watch() if args.watch else print(json.dumps(build()))

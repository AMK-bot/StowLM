"""Fresh-process full checker, intrinsic instruction and mask audit; no planner."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from time import perf_counter

from stowage.actions import get_action_space
from stowage.checker import check
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.io import load_instance
from stowage.model import MovePlan
from stowage.moves import apply_plan

from experiments.mvp.reader.constraints import compile_one_port_per_hatch, wrap_engine
from experiments.mvp.reader.binding_claims import sentence_target_agreement
from experiments.mvp.reader.occupancy import apply_reader_constraint
from experiments.mvp.reader.pipeline import intrinsic_violations, plan_signature
from experiments.mvp.reader.schema import OnePortPerHatch, validate_answer

ROOT = Path(__file__).resolve().parents[3]


def audit(path):
    start = perf_counter()
    row = json.loads(Path(path).read_text())
    out = dict(case_id=row['case_id'], fresh_process=True, audit_pid=os.getpid(),
               passed=False, binding=False, repaired=False,
               plan_record_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())
    try:
        out.update(sentence_target_agreement(row))
        if not out['parse_agreement']:
            raise ValueError(out['sentence_target_failure'])
        instance = load_instance(ROOT/row['input'])
        answer = validate_answer(json.dumps(row['parsed']), row['context'])
        baseline = json.loads((ROOT/row['baseline_file']).read_text())
        before = MovePlan.model_validate(baseline['plan'])
        before_check = check(instance, before)
        before_errors = intrinsic_violations(instance, answer, before)
        out.update(unconstrained_passed=bool(before_check.passed),
                   unconstrained_moves=float(before_check.count.weighted_nonproductive),
                   unconstrained_violations=before_errors)
        if row.get('plan'):
            plan = MovePlan.model_validate(row['plan'])
            report = check(instance, plan)
            replay = apply_plan(instance, plan)
            if isinstance(answer, OnePortPerHatch):
                compiled = compile_one_port_per_hatch(instance, row['compilation']['cover_ports'])
            else:
                compiled = apply_reader_constraint(instance, answer)
            intrinsic = intrinsic_violations(instance, answer, plan)
            allocation = compiled.violations(plan)
            space = get_action_space('boxslot')
            engine = wrap_engine(make_feasibility_engine(instance, space,
                config=FeasibilityConfig(extra={'global_screen': 0})), compiled)
            legal = True
            action_count = 0
            for action in space.from_plan(instance, plan):
                if not engine.is_legal(action):
                    legal = False
                    break
                engine.push(action)
                action_count += 1
            positions = {p.container_id: p.slot for p in replay.final_state.ship.placements}
            before_positions = {p.container_id: p.slot for p in apply_plan(instance, before).final_state.ship.placements}
            changed = sum(positions.get(bid) != before_positions.get(bid) for bid in set(positions) | set(before_positions))
            all_loaded = set(instance.load_list) <= positions.keys()
            value = float(report.count.weighted_nonproductive)
            out.update(checker_passed=bool(report.passed), replay_legal=bool(replay.legal),
                       mask_legal=legal, mask_complete=legal and engine.is_complete(),
                       all_loaded=all_loaded, instruction_violations=intrinsic,
                       allocation_violations=allocation, constrained_moves=value,
                       stored_moves_match=value == row.get('constrained_moves'),
                       changed_final_placements=changed, plan_changed=plan_signature(plan) != plan_signature(before),
                       mask_actions_checked=action_count,
                       checker_violations=[v.rule_id for v in report.violations],
                       restows_now=report.count.restow_lifts, relocations=report.count.relocations,
                       hatches_now=report.count.hatch_cover_lifts,
                       restows_later=sum(f.restow_lifts for f in report.count.future),
                       hatches_later=sum(f.hatch_cover_lifts for f in report.count.future))
            out['passed'] = bool(report.passed and replay.legal and legal and engine.is_complete()
                                 and all_loaded and not intrinsic and not allocation and out['stored_moves_match'])
            out['sentence_compliance_verified'] = out['passed']
            out['binding'] = bool(out['parse_agreement'] and out['passed'] and before_check.passed
                                  and before_errors and out['plan_changed'])
        else:
            out['failure'] = row.get('error', 'No constrained plan passed the driver')
    except Exception as error:
        out.update(passed=False, error=f'{type(error).__name__}: {error}')
    out['wall_s'] = perf_counter()-start
    return out

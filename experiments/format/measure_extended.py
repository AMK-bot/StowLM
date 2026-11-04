"""Size four canonical KCS dense campaign cases without constructing mask tries.

The lowest manifested seed per vessel and generator is SHA256-verified. Actual
expanded plans are counted only if the existing sizing heuristic completes and
physical replay passes. No fabricated output estimate substitutes for a failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import time

from extended import ROOT, load_canonical_dense
from measure import sizing_plan, prompt_cost_split
from llm.stowlm.format import ActionCodec, load_tokenizer
from llm.stowlm.format_prompt import encode_prompt, instance_to_prompt, parse_prompt



def top_access_sizing_plan(instance):
    """All-20ft fallback: retrieve exposed targets before digging below them.

    This is a token-sizing heuristic, not an optimization or checker-feasibility
    claim. Only physical replay can establish the resulting lift legality.
    """
    from stowage.actions.expander import Expander
    from stowage.actions.samples import legal_slots
    from stowage.actions.space import BoxSlotSpace, Place
    from stowage.moves import apply_plan

    boxes = {box.id: box for box in instance.containers}
    if any(boxes[cid].length_ft != 20 for cid in instance.load_list):
        raise ValueError('Top-access fallback is defined only for all-20ft loads')
    pending = set(instance.load_list)
    ex = Expander(instance)
    actions = []
    while pending:
        # Zero blockers ranks every exposed pending box ahead of buried targets.
        def blockers(cid):
            where, key = ex.where[cid]
            if where != 'yard':
                raise ValueError(f'Pending target is not in yard: {cid}')
            return len(ex.yard[key])-1-ex.yard[key].index(cid)
        committed = False
        for cid in sorted(pending, key=lambda c: (blockers(c), c)):
            slots = sorted(legal_slots(instance, ex, cid),
                           key=lambda slot: (int(slot[-2:]) >= 80, int(slot[-2:]), slot))
            for slot in slots:
                if not ex.cells[slot].accepts_20:
                    continue
                action = Place(cid, slot)
                trial = ex.copy()
                try:
                    trial.step(action)
                except ValueError:
                    continue
                ex = trial
                actions.append(action)
                pending.remove(cid)
                committed = True
                break
            if committed:
                break
        if not committed:
            raise RuntimeError('Top-access sizing fallback has no expandable placement')
    plan = BoxSlotSpace().expand(instance, actions)
    replay = apply_plan(instance, plan)
    if not replay.legal:
        raise RuntimeError(f'Top-access sizing fallback failed physical replay: {replay.violations}')
    return actions, plan


def measure_case(name, generator, tokenizer):
    t0 = time.perf_counter()
    instance, metadata = load_canonical_dense(name, generator=generator)
    codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
    facts = parse_prompt(instance_to_prompt(instance))
    assert facts['load'] == [codec.box_index.index(cid) for cid in instance.load_list]
    prompt = encode_prompt(instance, tokenizer)
    no_layout = encode_prompt(instance, tokenizer, include_layout=False)
    boxes = {box.id: box for box in instance.containers}
    loads = len(instance.load_list)
    row = dict(
        **metadata, **prompt_cost_split(instance, tokenizer),
        vessel=name, generator=generator, instance_id=instance.id,
        physical_cells=len(instance.vessel.cells), load_boxes=loads,
        load_teu=sum(boxes[cid].length_ft/20 for cid in instance.load_list),
        yard_boxes=sum(len(stack.boxes) for stack in instance.yard.stacks),
        prompt_tokens=len(prompt), layout_tokens=len(prompt)-len(no_layout),
        prompt_without_layout_tokens=len(no_layout), prefill_seconds=len(prompt)/870,
        output_tokens=None, total_sft_tokens=None, expanded_lifts=None,
        estimated_plan_seconds=None, generation_rate_tokens_s=None,
        context_tokens=131072, context_fit=None,
        output_budget_tokens=loads*8*1.05+len(codec.end_plan_tokens), output_budget_fit=None,
        move_engine_legal=None, full_checker_certified=False,
        sizing_heuristic="short_box_first_static_assignment", original_sizing_error=None,
        instance_provenance=instance.provenance.model_dump(mode='json'),
    )
    try:
        try:
            actions, plan = sizing_plan(instance)
        except (RuntimeError, ValueError) as original_error:
            row['original_sizing_error'] = f'{type(original_error).__name__}: {original_error}'
            row['sizing_heuristic'] = 'top_access_first_all20_fallback'
            actions, plan = top_access_sizing_plan(instance)
        output = codec.encode_plan(actions)
        assert codec.decode_plan(output) == actions
    except (RuntimeError, ValueError) as error:
        row.update(sizing_status='failed', sizing_error=f'{type(error).__name__}: {error}')
    else:
        rate = 31 if len(output) <= 2000 else 29 if len(output) <= 5000 else 24.6
        row.update(sizing_status='complete', output_tokens=len(output),
                   total_sft_tokens=len(prompt)+len(output),
                   expanded_lifts=len(plan.moves), move_engine_legal=True,
                   generation_rate_tokens_s=rate,
                   estimated_plan_seconds=len(prompt)/870+len(output)/rate,
                   context_fit=len(prompt)+len(output)<=131072,
                   output_budget_fit=len(output)<=row['output_budget_tokens'])
    row['measurement_seconds'] = time.perf_counter()-t0
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=4, choices=range(1, 5))
    args = parser.parse_args()
    started = time.perf_counter()
    tokenizer = load_tokenizer(ROOT / 'models/glimmer-30b-4bit')
    cases = [(name, generator) for generator in ['random_yard', 'simulated_yard']
             for name in ['feeder_500', 'feeder_800']]
    rows = []
    for name, generator in cases[:args.limit]:
        row = measure_case(name, generator, tokenizer)
        rows.append(row)
        print(json.dumps(row), flush=True)
    result = dict(rows=rows, wall_seconds=time.perf_counter()-started,
                  peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  cpu_cores=1, gpu=False,
                  tokenizer_sha256=hashlib.sha256((ROOT/'models/glimmer-30b-4bit/tokenizer.json').read_bytes()).hexdigest(),
                  prompt_source_sha256=hashlib.sha256((ROOT/'src/llm/stowlm/format_prompt.py').read_bytes()).hexdigest())
    (ROOT/'experiments/format/extended_measurements.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__ == '__main__':
    main()

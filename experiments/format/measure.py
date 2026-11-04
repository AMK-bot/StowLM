"""CPU-only StowLM format sizing on persisted synthetic ladder instances.

A trie is a tree of token prefixes: sharing the common prefix of candidate actions
lets a decoder find permitted next tokens without scanning every action again.
See the implementation/source note in llm.stowlm.format. These plans measure the
wire format, not solver quality or full-checker feasibility.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import resource
import time

from stowage.actions.expander import Expander
from stowage.actions.samples import legal_slots
from stowage.actions.space import BoxSlotSpace, Place, Relocate
from stowage.io import load_instance
from stowage.moves import apply_plan
from stowage.conventions import cells_of_slot

ROOT = Path(__file__).resolve().parents[2]


def sizing_plan(instance):
    """Greedy short-box-first assignment, hold-before-deck expansion, full move replay.

    APPROXIMATION: this deterministic plan is for token sizing. It checks physical
    crane legality only, not DG, strength, stability, reefers or other hard rules;
    it must never be consumed as checker-approved training data.
    """
    boxes = {box.id: box for box in instance.containers}
    expander = Expander(instance)
    assigned = []
    # Assign 20-foot boxes before long boxes: a long box can rest on two 20s,
    # whereas the move engine forbids a 20 resting on a long box.
    for cid in sorted(instance.load_list, key=lambda c: (boxes[c].length_ft, c)):
        slots = sorted(legal_slots(instance, expander, cid),
                       key=lambda s: (int(s[-2:]) >= 80, int(s[-2:]), s))
        for slot in slots:
            cells = cells_of_slot(slot)
            if not all(getattr(expander.cells[c], f"accepts_{boxes[cid].length_ft}") for c in cells):
                continue
            for cell in cells:
                expander.occ[cell] = cid
            assigned.append(Place(cid, slot))
            break
        else:
            raise RuntimeError(f"No greedy assignment for {cid} in {instance.id}")
    # Hold access must finish before placing anything on its hatch cover.
    actions = sorted(assigned, key=lambda a: (int(a.slot[-2:]) >= 80, int(a.slot[-2:]), a.slot))
    plan = BoxSlotSpace().expand(instance, actions)
    replay = apply_plan(instance, plan)
    if not replay.legal or len(actions) != len(instance.load_list):
        raise RuntimeError(f"Sizing plan failed move-engine replay: {replay.violations}")
    return actions, plan



def prompt_cost_split(instance, tokenizer):
    """Keep metadata/layout/chat fixed, empty variable cargo/load/yard observations."""
    from llm.stowlm.format_prompt import encode_prompt
    empty = instance.model_copy(update={
        'containers': [], 'load_list': [], 'discharge_list': [],
        'yard': instance.yard.model_copy(update={'stacks': [], 'quay_buffer': []}),
        'onboard': instance.onboard.model_copy(update={'placements': []}),
    })
    fixed = len(encode_prompt(empty, tokenizer))
    total = len(encode_prompt(instance, tokenizer))
    count = len(instance.load_list)
    return dict(fixed_prompt_tokens=fixed, variable_prompt_tokens=total-fixed,
                variable_tokens_per_load=(total-fixed)/count,
                all_in_prompt_tokens_per_load=total/count,
                prompt_budget_tokens=2000+12*count,
                prompt_budget_headroom_tokens=2000+12*count-total,
                prompt_budget_fit=total<=2000+12*count,
                variable_12_tokens_per_load_fit=(total-fixed)<=12*count)


def measure(path, tokenizer):
    from llm.stowlm.format import ActionCodec
    from llm.stowlm.format_prompt import encode_prompt, instance_to_prompt, parse_prompt

    started = time.perf_counter()
    instance = load_instance(path)
    codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
    actions, plan = sizing_plan(instance)
    output = codec.encode_plan(actions)
    assert codec.decode_plan(output) == actions
    prompt_text = instance_to_prompt(instance)
    facts = parse_prompt(prompt_text)
    assert facts['instance'] == instance.id and facts['stage'] == instance.stage
    assert facts['load'] == [codec.box_index.index(cid) for cid in instance.load_list]
    assert facts['vessel']['id'] == instance.vessel.id
    assert facts['call'] == instance.call_index
    prompt = encode_prompt(instance, tokenizer)
    without_layout = encode_prompt(instance, tokenizer, include_layout=False)
    # Deliberately a generous physical candidate set, not a full-checker mask.
    # It contains initially supported/length-compatible slots for every load box.
    ex = Expander(instance)
    candidates = [Place(cid, slot) for cid in instance.load_list
                  for slot in legal_slots(instance, ex, cid)
                  if all(getattr(ex.cells[cell], f"accepts_{ex.boxes[cid].length_ft}")
                         for cell in cells_of_slot(slot))]
    t0 = time.perf_counter()
    trie = codec.trie(candidates)
    build_s = time.perf_counter() - t0
    example = codec.encode_action(candidates[0])
    prefixes = [example[:i] for i in range(len(example) + 1)]
    t0 = time.perf_counter()
    for _ in range(1000):
        for prefix in prefixes:
            trie.allowed_next_token_ids(prefix)
    query_us = (time.perf_counter() - t0) * 1e6 / (1000 * len(prefixes))
    # 31 tok/s to 2k generated tokens; 29 tok/s for the 2k–5k regime,
    # measured without a mask in experiments/glimmer-bench/run_long.log.
    rate = 31 if len(output) <= 2000 else 29
    override_tokens = [len(codec.encode_action(Relocate(cid, stack.key)))
                       for cid in instance.load_list for stack in instance.yard.stacks[:1]]
    override_count = math.ceil(.05 * len(actions))
    with_overrides = len(output) + override_count * max(override_tokens)
    return dict(
        **{**prompt_cost_split(instance, tokenizer), "total_sft_tokens": len(prompt)+len(output)},
        rung=instance.size_class, instance_id=instance.id, source=str(path.relative_to(ROOT)),
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        load_boxes=len(instance.load_list), yard_boxes=sum(len(s.boxes) for s in instance.yard.stacks),
        prompt_tokens=len(prompt), prompt_without_layout_tokens=len(without_layout),
        layout_tokens=len(prompt)-len(without_layout), output_tokens=len(output),
        tokens_per_action_excluding_eot=(len(output)-len(codec.end_plan_tokens))/len(actions),
        total_tokens=len(prompt)+len(output), context_tokens=131072,
        context_fit=len(prompt)+len(output)<=131072,
        action_budget_tokens=8*len(actions)*1.05+len(codec.end_plan_tokens),
        action_budget_fit=len(output)<=8*len(actions)*1.05+len(codec.end_plan_tokens),
        override_tokens_max=max(override_tokens), override_count_5pct_ceil=override_count,
        output_tokens_with_5pct_overrides=with_overrides,
        output_tokens_with_5pct_overrides_budget_fit=with_overrides<=8*len(actions)*1.05+len(codec.end_plan_tokens),
        lifts=len(plan.moves), move_engine_legal=True, full_checker_certified=False,
        prefill_seconds=len(prompt)/870, generation_tokens_per_second=rate,
        generation_seconds=len(output)/rate,
        estimated_plan_seconds=len(prompt)/870+len(output)/rate,
        trie_candidates=len(candidates), trie_nodes=trie.node_count, trie_edges=trie.edge_count,
        trie_build_seconds=build_s, trie_query_microseconds=query_us,
        measurement_seconds=time.perf_counter()-started,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rungs', nargs='+', default=['tiny', 'small', 'medium', 'full'])
    parser.add_argument('--output', default='experiments/format/measurements.json')
    args = parser.parse_args()
    from llm.stowlm.format import load_tokenizer
    t0 = time.perf_counter()
    tokenizer = load_tokenizer(ROOT / 'models/glimmer-30b-4bit')
    rows = []
    for rung in args.rungs:
        for path in sorted((ROOT / 'experiments/instances' / rung).glob('seed-*.json')):
            if path.name.endswith('.audit.json'):
                continue
            row = measure(path, tokenizer)
            rows.append(row)
            print(json.dumps(row), flush=True)
    report = dict(rows=rows, wall_seconds=time.perf_counter()-t0,
                  peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  cpu_cores=1, gpu=False,
                  tokenizer_sha256=hashlib.sha256((ROOT/'models/glimmer-30b-4bit/tokenizer.json').read_bytes()).hexdigest())
    (ROOT / args.output).write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()

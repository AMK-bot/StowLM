"""Retained regression test for the Model Runner V2 feasibility-mask LogitsProcessor in modal_smoke.py (Q2).

The real class is AST-extracted from the script and driven on CPU tensors against a stub of
`vllm.v1.worker.gpu.sample.logits_processor` (no vLLM, no GPU). The oracle is the script's pure `allowed_at`.
Covers shuffled row order (rows -> slots via ctx.expanded_idx_mapping), the first output position
(pos == prompt_len-1 -> prev=None, even when the prompt ends in the separator), separator -> after_sep, forced single
tokens, the no-repeat rule, recycled slots (masked -> other masked, masked -> unmasked) and untouched inactive rows.

torch is not in the project venv, so under `uv run pytest` this module SKIPS. Run it with the isolated torch venv,
which has no pytest, as a script:
    experiments/grpo-venue/.venv-pt/bin/python tests/experiments/test_grpo_venue_v2_mask.py
"""
import ast
import random
import sys
import time
import types
from pathlib import Path

try:
    import torch
except ImportError:  # project venv: no torch
    import pytest
    pytest.skip("needs torch: run with experiments/grpo-venue/.venv-pt/bin/python as a script", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/grpo-venue/modal_smoke.py"
VLLM_LP = "vllm.v1.worker.gpu.sample.logits_processor"
V, V_PAD, SLOTS = 16, 20, 6  # vocab, logits width (padded columns exercised), persistent-batch slots


def load_lp_cls():
    class LogitsProcessor:  # stub of the V2 ABC; the real class only subclasses it
        pass
    stubs = {}
    parts = VLLM_LP.split(".")
    for i in range(1, len(parts) + 1):
        stubs[".".join(parts[:i])] = types.ModuleType(".".join(parts[:i]))
    stubs[VLLM_LP].LogitsProcessor = LogitsProcessor
    tree = ast.parse(SCRIPT.read_text())
    nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in {"_make_mask_lp_cls", "allowed_at"})
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "MASK_KEY" for t in n.targets))]
    assert len(nodes) == 3, [getattr(n, "name", "MASK_KEY") for n in nodes]
    ns = {"time": time}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SCRIPT), "exec"), ns)
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        cls = ns["_make_mask_lp_cls"]()
    finally:
        for k, mod in saved.items():
            if mod is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = mod
    return cls, ns["allowed_at"], ns["MASK_KEY"]


SEP = 3
SPECS = [  # small sets so separators, forced tokens and repeats happen often
    {"allowed": [1, 2, 3, 5], "after_sep": [7], "sep": SEP},           # forced token after the separator
    {"allowed": [2, 3, 4, 8, 9], "after_sep": [4, 8, 10], "sep": SEP},  # repeat rule inside after_sep
    {"allowed": [3, 6], "after_sep": [3], "sep": SEP},                  # sep -> sep forever (only legal, repeat kept)
    {"allowed": [0, 3, 11, 15], "after_sep": [0, 15], "sep": SEP},
]


class Buf:
    def __init__(self, n):
        self.gpu = torch.zeros(n, dtype=torch.int32)


def check_v2_mask(steps=400, seed=0):
    LP, allowed_at, mask_key = load_lp_cls()
    rng = random.Random(seed)
    rs = types.SimpleNamespace(max_num_reqs=SLOTS, vocab_size=V, device=torch.device("cpu"), prompt_len=Buf(SLOTS))
    lp = LP(None, rs)
    slots = {}  # slot -> {"spec": dict|None, "seq": [...], "prompt_len": int, "left": int}
    events = dict(first_after_sep_prompt=0, after_sep=0, forced=0, repeat_dropped=0, recycled_masked=0,
                  recycled_to_unmasked=0, unmasked_rows=0, masked_rows=0, shuffled=0)
    last_spec = {}

    def add(slot):
        spec = rng.choice(SPECS + [None])  # ~1 in 5 requests is unmasked
        prompt = [rng.randrange(V) for _ in range(rng.randrange(1, 5))]
        if rng.random() < 0.4:
            prompt[-1] = SEP  # first output must still be prev=None
        if last_spec.get(slot) is not None:  # slot previously held a masked request
            if spec is None:
                events["recycled_to_unmasked"] += 1
            elif spec is not last_spec[slot]:
                events["recycled_masked"] += 1
        last_spec[slot] = spec
        params = types.SimpleNamespace(extra_args={mask_key: spec} if spec is not None else None)
        LP.validate_params(params)
        assert lp.add_request(slot, params) == (spec is not None)
        rs.prompt_len.gpu[slot] = len(prompt)
        slots[slot] = {"spec": spec, "seq": prompt, "prompt_len": len(prompt), "left": rng.randrange(1, 25)}

    for s in range(SLOTS):
        add(s)
    for _ in range(steps):
        lp.apply_staged_writes()
        live = [s for s in slots if rng.random() < 0.85] or list(slots)  # some requests sit out a step
        order = live[:]
        rng.shuffle(order)
        events["shuffled"] += order != sorted(order)
        ctx = types.SimpleNamespace(
            expanded_idx_mapping=torch.tensor(order, dtype=torch.int32),
            input_ids=torch.tensor([slots[s]["seq"][-1] for s in order], dtype=torch.int32),
            pos=torch.tensor([len(slots[s]["seq"]) - 1 for s in order], dtype=torch.int32),
            idx_mapping_np=types.SimpleNamespace(size=len(order)))
        logits = torch.randn(len(order), V_PAD)
        out = lp.apply(logits.clone(), ctx)
        for row, s in enumerate(order):
            st = slots[s]
            finite = set(torch.nonzero(torch.isfinite(out[row]))[:, 0].tolist())
            if st["spec"] is None:
                assert finite == set(range(V_PAD)), (s, "unmasked row was touched")
                assert torch.equal(out[row], logits[row])
                events["unmasked_rows"] += 1
                tok = rng.choice([SEP, st["seq"][-1], rng.randrange(V)])
            else:
                first = len(st["seq"]) == st["prompt_len"]
                prev = None if first else st["seq"][-1]
                legal = set(allowed_at(st["spec"], prev))
                assert finite == legal, (s, prev, first, sorted(finite), sorted(legal))
                events["masked_rows"] += 1
                events["first_after_sep_prompt"] += first and st["seq"][-1] == SEP
                events["after_sep"] += prev == SEP
                events["forced"] += len(legal) == 1
                base = st["spec"]["after_sep"] if prev == SEP else st["spec"]["allowed"]
                events["repeat_dropped"] += prev in base and len(base) > 1
                tok = rng.choice(sorted(finite))
            st["seq"].append(tok)
            st["left"] -= 1
        for s in [s for s in slots if slots[s]["left"] <= 0]:  # finished -> slot recycled for a new request
            add(s)
    for k, n in events.items():
        assert n > 0, (k, events)
    return events


def test_v2_mask_matches_oracle_over_shuffled_recycled_batches():
    check_v2_mask(steps=400, seed=0)


def test_v2_mask_other_seed():
    check_v2_mask(steps=300, seed=7)


if __name__ == "__main__":
    for fn in (test_v2_mask_matches_oracle_over_shuffled_recycled_batches, test_v2_mask_other_seed):
        fn()
    print("V2 mask:", check_v2_mask(steps=400, seed=0))
    print("2 passed")

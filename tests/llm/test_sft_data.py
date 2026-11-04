"""SFT contract tests on deterministic fixtures, with no retry-or-skip fallback.

These cases are synthetic interface evidence, never checker-approved physical
training gold. Production admission is covered separately by strict gold tests.
"""

from dataclasses import replace
import json

import pytest

from llm.stowlm.format import ActionCodec, load_tokenizer
from llm.stowlm.format_prompt import encode_prompt
from llm.stowlm.sft_data import (build_example, length_stats, order_by_size, plan_actions,
                                 read_dataset, verify_replay, write_dataset)
from stowage.actions.space import Place
from stowage.frozen import StubTaintError, require_clean
from stowage.model.provenance import Provenance

from .test_sft_data_fixtures import deterministic_case, diagnostic_engine


@pytest.fixture(scope="module")
def tokenizer():
    return load_tokenizer()


def test_example_layout_and_roundtrip(tokenizer):
    inst, plan = deterministic_case(2)
    ex = build_example(inst, plan, split="train", tokenizer=tokenizer)
    prompt = encode_prompt(inst, tokenizer)
    codec = ActionCodec(tokenizer, [c.id for c in inst.containers])
    assert tuple(ex.input_ids[:ex.prompt_len]) == prompt
    completion = ex.input_ids[ex.prompt_len:]
    assert codec.decode_plan(completion) == plan_actions(inst, plan)
    assert tuple(completion[-len(codec.end_plan_tokens):]) == codec.end_plan_tokens
    assert ex.n_actions == 2 and ex.completion_len == len(completion)
    assert not ex.stubs


def test_real_mask_deterministic_replay_complete_and_explicitly_diagnostic(tokenizer):
    inst, plan = deterministic_case(2)
    ex = build_example(inst, plan, split="train", tokenizer=tokenizer)
    rep = verify_replay(inst, ex, tokenizer=tokenizer, make_engine=diagnostic_engine)
    assert rep.ok, rep.first_failure
    assert rep.n_actions == 2 and rep.n_tokens == ex.completion_len
    assert rep.production is False
    assert rep.stubs and ex.stubs == rep.stubs
    with pytest.raises(StubTaintError):
        require_clean(ex, rep, what="diagnostic replay cannot become gold")


def test_real_mask_rejects_hand_authored_illegal_action(tokenizer):
    inst, plan = deterministic_case()
    ex = build_example(inst, plan, split="train", tokenizer=tokenizer)
    codec = ActionCodec(tokenizer, [c.id for c in inst.containers])
    # Existing but unsupported upper tier: structurally valid action tokens,
    # physically illegal placement, rather than an arbitrary corrupted opcode.
    illegal = codec.encode_plan([Place(inst.load_list[0], "0010184")])
    bad = replace(ex, input_ids=[*ex.input_ids[:ex.prompt_len], *illegal])
    rep = verify_replay(inst, bad, tokenizer=tokenizer, make_engine=diagnostic_engine)
    assert not rep.ok and "masked" in rep.first_failure
    short = replace(ex, input_ids=ex.input_ids[:-1])
    rep = verify_replay(inst, short, tokenizer=tokenizer, make_engine=diagnostic_engine)
    assert not rep.ok and "status" in rep.first_failure


def test_default_production_replay_refuses_real_fixture_without_c2(tokenizer):
    inst, plan = deterministic_case()
    ex = build_example(inst, plan, split="train", tokenizer=tokenizer)
    rep = verify_replay(inst, ex, tokenizer=tokenizer)
    assert not rep.ok
    assert rep.production is False
    assert "C2" in rep.first_failure


def test_taint_refused_unless_allowed_and_only_in_tiny_chain(tokenizer, tmp_path):
    inst, plan = deterministic_case()
    tainted = plan.model_copy(update={"provenance": plan.provenance.merge(Provenance(stubs={"x": "tiny run"}))})
    with pytest.raises(StubTaintError):
        build_example(inst, tainted, split="train", tokenizer=tokenizer)
    ex = build_example(inst, tainted, split="train", tokenizer=tokenizer, allow_stubs=True)
    assert ex.stubs == {"x": "tiny run"}
    with pytest.raises(RuntimeError, match="tiny-chain"):
        write_dataset([ex], tmp_path / "sft")
    stats = write_dataset([ex], tmp_path / "tiny-chain" / "sft")
    assert stats["tainted_examples"] == 1
    assert read_dataset(tmp_path / "tiny-chain" / "sft" / "train.jsonl")[0].stubs == {"x": "tiny run"}


def test_stats_and_multiscale_curriculum_order(tokenizer, tmp_path):
    examples = []
    for n_boxes, split in ((3, "train"), (1, "val"), (2, "train")):
        instance, plan = deterministic_case(n_boxes)
        examples.append(build_example(instance, plan, split=split, tokenizer=tokenizer))
    ordered = order_by_size(examples)
    assert [example.n_boxes for example in ordered] == [1, 2, 3]
    # Tie-breaks are sequence length, then instance ID, independent of input order.
    ties = [replace(ordered[0], instance_id="z"), replace(ordered[0], instance_id="a"),
            replace(ordered[0], instance_id="b", input_ids=[*ordered[0].input_ids, 1])]
    assert [example.instance_id for example in order_by_size(ties)] == ["a", "z", "b"]
    stats = length_stats(examples)
    lengths = sorted(len(example.input_ids) for example in examples)
    assert stats["examples"] == 3
    assert stats["total_tokens"] == {"n": 3, "min": lengths[0], "p50": lengths[1],
                                    "mean": round(sum(lengths) / 3, 1), "p95": lengths[-1],
                                    "max": lengths[-1], "sum": sum(lengths)}
    assert stats["n_actions"]["sum"] == 6
    assert stats["completion_tokens_per_action"] == round(sum(e.completion_len for e in examples) / 6, 2)
    written = write_dataset(examples, tmp_path / "out")
    assert set(written["splits"]) == {"train", "val"}
    assert [ex.n_boxes for ex in read_dataset(tmp_path / "out" / "train.jsonl")] == [2, 3]
    assert json.loads((tmp_path / "out" / "stats.json").read_text())["splits"]["train"]["examples"] == 2
    assert length_stats([])["examples"] == 0
    assert length_stats([])["completion_tokens_per_action"] is None

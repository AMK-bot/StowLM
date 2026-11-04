"""Reproducible CPU diagnostic: real decoder replay on hand-authored test fixtures.

These hull-free cases have no physical checker approval or production C2. The
output is diagnostic tainted data, never gold and never production completion.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from llm.stowlm.format import load_tokenizer
from llm.stowlm.sft_data import build_example, verify_replay, write_dataset


def main():
    root = Path(__file__).resolve().parents[2]
    source = root / 'tests/llm/test_sft_data_fixtures.py'
    spec = importlib.util.spec_from_file_location('sft_hand_authored_fixture', source)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    tokenizer = load_tokenizer()
    rows = []
    for split, size in (('train', 3), ('train', 1), ('val', 2), ('test', 1)):
        instance, plan = fixture.deterministic_case(size)
        instance.id += '-' + split
        plan.instance_id = instance.id
        example = build_example(instance, plan, split=split, tokenizer=tokenizer)
        report = verify_replay(instance, example, tokenizer=tokenizer,
                               make_engine=fixture.diagnostic_engine)
        if not report.ok:
            raise RuntimeError(f'fixture replay failed: {report.first_failure}')
        if report.production or not report.stubs:
            raise RuntimeError('synthetic diagnostic must retain its taint')
        rows.append(example)
    out = root / 'experiments/tiny-chain/synthetic-mask-fixtures'
    stats = write_dataset(rows, out, required_splits=('train', 'val', 'test'))
    if stats['replay']['verified'] != len(rows):
        raise RuntimeError('not all serialized fixture rows replayed')
    print(json.dumps({'output': str(out.relative_to(root)), 'diagnostic_only': True,
                      'stats': stats}, indent=2))


if __name__ == '__main__':
    main()

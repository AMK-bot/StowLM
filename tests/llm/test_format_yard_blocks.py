"""Block ordinals remove repeated block names without losing yard addresses."""
import json

from llm.stowlm.format_prompt import instance_to_prompt, parse_prompt
from stowage.model.fixtures import build_instance
from stowage.rules.catalogue import rule_freeze_v1


def test_v21_yard_uses_block_ordinals_and_preserves_addresses():
    instance = build_instance()
    wire = json.loads(instance_to_prompt(instance))
    assert wire['y'] == [[20, 1, 0, '4=2 6=3'], [20, 1, 1, '0=1'], [40, 1, 0, '0=0']]
    assert [row[0] for row in parse_prompt(json.dumps(wire))['yard']] == [
        'A-001-01', 'A-003-01', 'A-004-01', 'B-001-01']


def test_legacy_v20_named_yard_blocks_still_parse():
    with rule_freeze_v1(enabled=False):
        wire = json.loads(instance_to_prompt(build_instance()))
    # Write the original v2.0 block-name channel explicitly.
    wire['v'] = 'stowlm-prompt@2.0'
    wire['y'] = [[20, 1, 'B:0=1 A:4=2 A:6=3'], [40, 1, 'A:0=0']]
    assert [row[0] for row in parse_prompt(json.dumps(wire))['yard']] == [
        'A-001-01', 'A-003-01', 'A-004-01', 'B-001-01']

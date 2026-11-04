"""SFT admission must retain loaded component lineage, even after G1 approval."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm.stowlm import sft_data


def test_production_lineage_refuses_missing_or_replaced_loaded_components(monkeypatch):
    policy=sft_data.gold_rule_policy()
    # Isolate this record's provenance from G1's separate eight-point proof gate.
    monkeypatch.setattr(policy,'enforce_rule_policy',lambda *a:None)
    monkeypatch.setattr(policy,'validate_g1_approval',lambda summary,*a:summary)
    monkeypatch.setattr(sft_data,'gold_rule_policy',lambda:policy)
    gold=dict(data_mode='production',epoch_id='e',rung='tiny',required_rules={},g1_approval={})
    with pytest.raises(ValueError,match='M5_EPOCH_COMPONENTS_MISSING'):
        sft_data.validate_production_lineage(gold)
    actual=policy._module('migration_evidence').current_component_epoch()
    gold['component_epoch']={**actual,'catalogue_loaded_sha256':'sha256:'+'0'*64}
    with pytest.raises(ValueError,match='M5_COMPONENT_EPOCH_MISMATCH'):
        sft_data.validate_production_lineage(gold)
    gold['component_epoch']=actual
    assert sft_data.validate_production_lineage(gold)=={}

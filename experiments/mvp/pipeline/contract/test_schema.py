from __future__ import annotations

import pytest

from experiments.mvp.pipeline.contract.schema import (
    FileRef, Mode, StageRecord, VoyagePacket, chain, chain_head, chain_intact, sha256_json)


def row(stage):
    return StageRecord(stage=stage, mode=Mode.live, input_sha256='a'*64, output_sha256='b'*64, wall_s=1.)


def test_chain_detects_an_edited_step():
    rows = chain([row('intake'), row('reader'), row('engine')])
    assert chain_intact(rows)
    rows[1] = rows[1].model_copy(update={'cost_usd': 9.})
    assert not chain_intact(rows)


def test_head_catches_an_edited_or_dropped_tail():
    rows = chain([row('intake'), row('reader'), row('engine')])
    head = chain_head(rows)
    assert chain_intact(rows, head=head)
    assert not chain_intact(rows[:-1], head=head)
    assert not chain_intact([], head=head)
    edited = rows[:-1] + [rows[-1].model_copy(update={'output_sha256': 'c'*64})]
    assert chain_intact(edited)  # links alone can't see this; that's why the head is signed
    assert not chain_intact(edited, head=head)


def test_hash_ignores_key_order():
    assert sha256_json({'a': 1, 'b': 2}) == sha256_json({'b': 2, 'a': 1})


def test_packet_is_always_synthetic_and_rejects_unknown_fields():
    base = dict(packet_id='p', created_utc='2026-11-01T00:00:00Z',
                base_instance=FileRef(path='x.json', sha256='c'*64))
    assert VoyagePacket(**base).synthetic is True
    with pytest.raises(ValueError):
        VoyagePacket(**base, synthetic=False)
    with pytest.raises(ValueError):
        VoyagePacket(**base, extra_field=1)

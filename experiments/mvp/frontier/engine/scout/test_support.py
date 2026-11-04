"""Deterministic page-layout and temporary-oracle restoration regressions."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from stowage.model.fixtures import build_instance

from . import lash_oracle
from .method import block_layout


def test_block_layout_allocates_largest_cover_to_highest_remaining_demand():
    model = SimpleNamespace(
        T=SimpleNamespace(units={'a': 1, 'b': 1, 'c': 2}),
        boxes=['a', 'b', 'c'], pod={'a': 1, 'b': 1, 'c': 2},
        pages=[SimpleNamespace(kind='20', cover='large', slots=range(3)),
               SimpleNamespace(kind='20', cover='small', slots=range(1))])
    assert block_layout(model) == {'large': 1, 'small': 2}


def test_lashing_oracle_restores_loading_condition_on_exception():
    oracle = object.__new__(lash_oracle.Oracle)
    oracle.instance = SimpleNamespace(call_index=0)
    oracle.empty = SimpleNamespace(model_copy=lambda **kwargs: object())
    oracle.condition = object()
    oracle.cache = {}
    original = lash_oracle.visibility._loading_condition
    with patch.object(lash_oracle.lashing, '_check_lashing', side_effect=ValueError('probe failed')):
        with pytest.raises(ValueError, match='probe failed'):
            oracle.ratio({build_instance().containers[0].id: '0010102'})
    assert lash_oracle.visibility._loading_condition is original

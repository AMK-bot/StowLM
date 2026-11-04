"""Conservative exchange retains every cargo fact except id/category."""
from unittest.mock import patch

from stowage.model.fixtures import build_instance

from .assignment import interchangeable_signature
from . import method


def test_signatures_do_not_merge_different_masses():
    box = build_instance().containers[1]
    identical = box.model_copy(update={'id': 'other', 'category': 'different'})
    heavier = identical.model_copy(update={'gross_mass_kg': box.gross_mass_kg+1.})
    assert interchangeable_signature(box) == interchangeable_signature(identical)
    assert interchangeable_signature(box) != interchangeable_signature(heavier)


def test_public_alias_always_selects_conservative_exchange():
    with patch.object(method, '_propose', return_value=[]) as propose:
        method.propose(None, seed=0, budget_s=1., context={})
    assert propose.call_args.kwargs['mass_exchange'] is False

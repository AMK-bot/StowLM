"""Run every bounds regression against both complete catalogue snapshots."""

import pytest

from stowage.rules.catalogue import rule_freeze_v1


@pytest.fixture(autouse=True, params=[False, True], ids=["freeze-off", "freeze-on"])
def bounds_rule_freeze(request):
    with rule_freeze_v1(enabled=request.param):
        yield request.param

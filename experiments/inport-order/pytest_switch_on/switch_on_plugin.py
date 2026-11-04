"""pytest plugin: run every test inside rule_freeze_v1(enabled=True) (D75 'both switch settings' gate)."""
import pytest

from stowage.rules.catalogue import rule_freeze_v1


@pytest.fixture(autouse=True)
def _rule_freeze_v1_on():
    with rule_freeze_v1(enabled=True):
        yield

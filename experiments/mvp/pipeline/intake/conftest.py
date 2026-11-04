"""Make the feature's tests runnable by the repository's pytest entry point."""
from pathlib import Path
import sys
import urllib.request

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))


@pytest.fixture(autouse=True)
def offline_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('Offline intake tests cannot make network requests')
    monkeypatch.setattr(urllib.request, 'urlopen', blocked)

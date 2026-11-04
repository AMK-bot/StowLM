"""Host-script tests run main() for real, and those set thread variables in os.environ; the audit epoch pins them."""
import os

import pytest


@pytest.fixture(autouse=True)
def restore_environment():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)

"""Script fixtures may set process-wide options when their modules are imported."""
import os
import sys

import pytest


@pytest.fixture(scope='module', autouse=True)
def restore_process_settings():
    saved_environment = dict(os.environ)
    saved_bytecode = sys.dont_write_bytecode
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved_environment)
        sys.dont_write_bytecode = saved_bytecode

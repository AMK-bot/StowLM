"""Public CLI and explanatory contracts from the logic review."""
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
README = ROOT/'experiments/campaign-g1/README.md'


def require_explicit_instance_cli(script):
    result = subprocess.run([sys.executable, str(script), '--help'],
                            capture_output=True, text=True, check=True)
    usage = ' '.join(result.stdout.split('options:')[0].split())
    assert '--instances INSTANCES' in usage
    assert '[--instances INSTANCES]' not in usage, 'legacy implicit instance roots must be retired'


@pytest.mark.parametrize('name', ['driver.py', 'status.py'])
def test_standalone_gold_cli_requires_explicit_instance_root(name):
    require_explicit_instance_cli(ROOT/'experiments/gold-plans/tiny'/name)


def test_reading_box_explains_denominators_selection_pairing_and_holdouts():
    text = README.read_text()
    block = text.split('## How to read these numbers\n', 1)[1].lstrip('\n').split('\n\n', 1)[0]
    lines = [line for line in block.splitlines() if line.startswith('>')]
    assert len(lines) == 5
    for phrase in ('all attempted', 'pass among returned', 'conditional on solver success',
                   'Hernán & Robins', 'ch. 8', 'paired inputs', 'n pairs / n excluded',
                   'Efron & Tibshirani', 'Held-out families', 'not a wholly unseen ship'):
        assert phrase in block

"""Run the offline player DOM harness with the locally installed Node runtime."""
from pathlib import Path
import shutil
import subprocess

import pytest

HERE = Path(__file__).parent


def test_player_dom():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is required for the offline JavaScript DOM harness')
    result = subprocess.run(
        [node, str(HERE / 'player_dom_test.js')],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Player DOM checks passed' in result.stdout


def test_demo_is_static_and_scoped():
    page = (HERE / 'demo.html').read_text()
    assert 'fixtures/fake-illegal-bay.js' in page
    assert 'StowLMXray.mount' in page
    assert 'fetch(' not in page
    for filename in ('player.js', 'player.css'):
        text = (HERE / filename).read_text()
        assert 'https://' not in text
        assert 'http://' not in text
    assert '1920px' in (HERE / 'player.css').read_text()
    assert '1080px' in (HERE / 'player.css').read_text()

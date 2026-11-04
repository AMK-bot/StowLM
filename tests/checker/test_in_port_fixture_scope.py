"""The synthetic fixture berth cap does not declare a production port limit."""
import json
from pathlib import Path

import pytest

from stowage.model.fixtures import build_instance
from stowage.ports import list_ports, load_port
from stowage.params import load_param_file, quantity
from stowage.rules.catalogue import rule_freeze_v1


LABEL = 'synthetic test berth, 8.0 m; not a production bound'


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('layover', [False, True])
def test_fixture_retains_explicitly_labelled_eight_metre_test_berth(enabled, layover):
    with rule_freeze_v1(enabled=enabled):
        port = build_instance(layover=layover).port
    assert port.max_draught_m == 8.0
    assert LABEL in port.sources


@pytest.mark.parametrize('name', ['empty_ship', 'layover'])
def test_saved_fixture_retains_explicitly_labelled_eight_metre_test_berth(name):
    path = Path(__file__).resolve().parents[2] / 'data' / 'fixtures' / f'{name}.json'
    port = json.loads(path.read_text())['port']
    assert port['max_draught_m'] == 8.0
    assert LABEL in port['sources']


@pytest.mark.parametrize('enabled', [False, True])
def test_production_profiles_use_declared_bounds_and_preserve_off_unknowns(enabled):
    """D75 depths supersede the prior unknowns; none inherits the fixture cap."""
    with rule_freeze_v1(enabled=enabled):
        for archetype in list_ports():
            port = load_port(archetype)
            document = load_param_file('ports', archetype)
            declared = document.get('berth_depth_m')
            expected = quantity(declared).value if enabled and declared is not None else None
            assert port.max_draught_m == expected, archetype
            if enabled and declared is not None:
                assert document['berth_depth_assumption'] in port.sources
            assert LABEL not in port.sources, archetype

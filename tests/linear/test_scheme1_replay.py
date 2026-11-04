"""Historical affine reconstruction never consumes the independent exact targets."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture(scope='module')
def historical():
    path=Path(__file__).resolve().parents[2]/'experiments/linear-model/epoch_requalification/hash-scheme-1028-review/scheme1-history/replay.py'
    spec=importlib.util.spec_from_file_location('scheme1_replay_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    prepared=module.prepare('feeder_500')
    source=module.read_source(module.EVIDENCE/'edges-v1/feeder_500/necessary-edges-holdout.json')
    return module,prepared,source['list_row_samples'][0]


def test_exact_targets_are_not_used_to_reconstruct_affine_rows(historical):
    module,prepared,observed=historical
    changed=deepcopy(observed)
    for key in tuple(changed):
        if key.startswith('exact_'):changed[key]=None
    original=module.edge_model(prepared,observed);independent=module.edge_model(prepared,changed)
    for direction in ('necessary','conservative'):
        first=module.constraints(original,direction=direction)
        second=module.constraints(independent,direction=direction)
        assert first.names==second.names
        for field in ('a_cell','a_tank','const','lb','ub'):
            np.testing.assert_array_equal(getattr(first,field),getattr(second,field))


def test_inconsistent_retained_affine_prediction_is_rejected(historical):
    module,prepared,observed=historical;changed=deepcopy(observed)
    changed['affine_draught_m']+=.1
    with pytest.raises(AssertionError):module.edge_model(prepared,changed)


def test_original_strict_centroid_rejection_is_preserved(historical):
    module,prepared,observed=historical;changed=deepcopy(observed)
    changed['fixed_point']['z_m']=prepared[3]['family_domain']['kg_min_m']-1e-6
    with pytest.raises(ValueError,match='positive fixed load centroid is outside'):
        module.edge_model(prepared,changed)


def test_live_constraint_function_still_rejects_the_archive_model(historical):
    from stowage.linear import linear_constraints
    module,prepared,observed=historical;model=module.edge_model(prepared,observed)
    with pytest.raises(ValueError):linear_constraints(model,direction='necessary')
    assert module.constraints(model,direction='necessary').names


def test_archive_snapshot_does_not_relabel_changed_parameters(historical):
    module,prepared,_=historical
    assert module.snapshot('feeder_500',prepared[0].parameters)==prepared[4]
    with pytest.raises(ValueError,match='parameters differ'):
        module.snapshot('feeder_500',dict(prepared[0].parameters,changed_numerics=True))

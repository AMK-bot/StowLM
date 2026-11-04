"""Effective tank density must invalidate a reusable geometric FSM reserve."""
import numpy as np

from stowage.linear.physics import _maximum_tank_fsms
from stowage.model.fixtures import build_instance


def test_tank_density_variant_cannot_reuse_cached_mass_moment(monkeypatch,tmp_path):
    instance=build_instance()
    tank=instance.vessel.tanks[0]
    params={'id':'synthetic','tanks':[{'id':tank.id}]}
    monkeypatch.setattr('stowage.linear.cache._DEFAULT_ROOT',tmp_path)
    monkeypatch.setattr('stowage.vessel.tanks.TankGeometry.from_params',
                        lambda *args,**kwargs:object())
    calls=[]
    def maximum(solid):
        calls.append(solid)
        return 2.
    monkeypatch.setattr('stowage.linear.tank_fsm.max_slack_fsm_m4',maximum)
    original=_maximum_tank_fsms(instance,params,[tank])
    again=_maximum_tank_fsms(instance,params,[tank])
    changed=tank.model_copy(update={'content_density_kg_m3':tank.content_density_kg_m3*1.1})
    variant=_maximum_tank_fsms(instance,params,[changed])
    np.testing.assert_array_equal(original,again)
    np.testing.assert_allclose(variant,original*1.1)
    assert len(calls)==2

"""The warm-up orchestrator must be explicit about partial preparation."""
import json

import pytest

from stowage import cache_warm


@pytest.mark.parametrize("workers", ["0", "9", "-1"])
def test_rejects_worker_overcommit(workers):
    with pytest.raises(SystemExit) as error:
        cache_warm.main(["--workers", workers])
    assert error.value.code == 2


def test_serial_cli_deduplicates_ships_and_reports_incomplete(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cache_warm, "REPORTS", tmp_path)
    seen = []
    def warm(name, c2_off=False):
        assert not c2_off
        seen.append(name)
        return dict(ship=name, complete=False, warnings=["missing public linear hook"])
    monkeypatch.setattr(cache_warm, "_warm_ship", warm)
    assert cache_warm.main(["--ships", "feeder_500", "feeder_500", "--workers", "1"]) == 2
    assert seen == ["feeder_500"]
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["complete"] is False
    assert "missing public linear hook" in capsys.readouterr().out


def test_successful_cli_has_zero_exit_status(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_warm, "REPORTS", tmp_path)
    monkeypatch.setattr(cache_warm, "_warm_ship", lambda name, c2_off=False: dict(ship=name, complete=True))
    assert cache_warm.main(["--ships", "feeder_800", "--workers", "1"]) == 0


def setup_ship(tmp_path, monkeypatch, *, linear_hook=False):
    import stowage.vessel as fleet
    import stowage.linear as linear
    calls = []
    monkeypatch.setattr(cache_warm, "REPORTS", tmp_path)
    monkeypatch.setattr(cache_warm, "_vessel_cache_snapshot", lambda *args: {"cache": (1, 2)})
    monkeypatch.setattr(cache_warm, "_production_tiny", lambda vessel: "tiny_production")
    monkeypatch.setattr(fleet, "load_vessel", lambda name, **kw: calls.append(("full", kw)) or "full")
    monkeypatch.setattr(fleet, "load_fleet_preset", lambda name, preset, **kw: calls.append((preset, kw)) or preset)
    monkeypatch.setattr(fleet, "load_ship_params", lambda name: {})
    monkeypatch.setattr(cache_warm, "_warm_stability", lambda *args: [dict(cache="KN", status="reused")])
    if linear_hook:
        monkeypatch.setattr(linear, "warm_cache", lambda vessel, *, workers: dict(vessel=vessel, reused=True), raising=False)
    else:
        monkeypatch.delattr(linear, "warm_cache", raising=False)
    return calls


def test_all_presets_loaded_and_missing_linear_is_visible(tmp_path, monkeypatch):
    calls = setup_ship(tmp_path, monkeypatch)
    result = cache_warm._warm_ship("feeder_500")
    assert {name for name, kw in calls} == {"tiny", "small", "medium", "full"}
    assert all(kw["with_hull"] for name, kw in calls)
    assert result["errors"] == []
    assert result["complete"] is False
    assert "no public warm_cache" in result["warnings"][0]
    assert json.loads((tmp_path / "feeder_500/summary.json").read_text()) == result


def test_public_linear_hook_runs_for_each_geometry(tmp_path, monkeypatch):
    setup_ship(tmp_path, monkeypatch, linear_hook=True)
    result = cache_warm._warm_ship("feeder_800")
    assert result["complete"] is True
    assert len([row for row in result["caches"] if row["cache"] == "linear"]) == 5


def test_stability_qualification_precedes_all_linear_calibration(tmp_path, monkeypatch):
    import stowage.linear as linear
    setup_ship(tmp_path, monkeypatch, linear_hook=True)
    events = []
    def physics(*args):
        events.append("stability qualified")
        return [dict(cache="KN", status="reused")]
    def calibrate(vessel, *, workers):
        assert workers == 1
        assert events[0] == "stability qualified"
        events.append(vessel)
        return dict(status="reused")
    monkeypatch.setattr(cache_warm, "_warm_stability", physics)
    monkeypatch.setattr(linear, "warm_cache", calibrate)
    result = cache_warm._warm_ship("feeder_500")
    assert result["complete"]
    assert events == ["stability qualified", "tiny", "small", "medium", "full", "tiny_production"]
    assert all(row["status"] == "reused" for row in result["caches"] if row["cache"] == "linear")


def test_production_tiny_matches_actual_four_hold_two_deck_layout(fleet_vessel):
    vessel = fleet_vessel("feeder_500", with_hull=False)
    result = cache_warm._production_tiny(vessel)
    assert len(result.cells) == 6
    stacks = {stack.id: stack for stack in result.stacks}
    assert sum(stacks[cell.stack_id].on_deck for cell in result.cells) == 2
    assert sum(not stacks[cell.stack_id].on_deck for cell in result.cells) == 4
    assert result.hull == vessel.hull
    assert len(vessel.cells) > 6


@pytest.mark.parametrize("c2_off", [False, True])
def test_failed_stability_shard_keeps_diagnostic(tmp_path, monkeypatch, c2_off):
    import stowage.linear as linear
    setup_ship(tmp_path, monkeypatch, linear_hook=True)
    monkeypatch.setattr(linear, "warm_cache", lambda *a, **kw: pytest.fail("unqualified linear reference"))
    def fail(*args):
        raise ValueError("independent holdout exceeds tolerance")
    monkeypatch.setattr(cache_warm, "_warm_stability", fail)
    result = cache_warm._warm_ship("feeder_1700", c2_off=c2_off)
    assert result["complete"] is False
    assert result["errors"] == ["ValueError: independent holdout exceeds tolerance"]
    assert (tmp_path / "feeder_1700/summary.json").is_file()


@pytest.mark.parametrize("linear_hook", [False, True])
def test_c2_off_cli_skips_all_fifteen_linear_profiles(tmp_path, monkeypatch, linear_hook):
    import stowage.linear as linear
    setup_ship(tmp_path, monkeypatch, linear_hook=linear_hook)
    physics_calls = []
    monkeypatch.setattr(cache_warm, "_warm_stability",
        lambda name, params: physics_calls.append(name) or [dict(cache="KN", status="reused")])
    if linear_hook:
        def forbidden(*args, **kwargs):
            pytest.fail("C2 off must never call the linear hook")
        monkeypatch.setattr(linear, "warm_cache", forbidden)
    assert cache_warm.main(["--c2-off", "--workers", "1"]) == 0
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert physics_calls == list(cache_warm.PRODUCTION_SHIPS)
    assert len(summary["ships"]) == 3
    rows = [row for ship in summary["ships"] for row in ship["caches"] if row["cache"] == "linear"]
    assert len(rows) == 15
    assert all(row["status"] == "not used: C2 off (D78/D79)" for row in rows)
    assert all(not ship["errors"] and not ship["warnings"] for ship in summary["ships"])


def test_manifest_validation_rejects_missing_geometry_binding(tmp_path, monkeypatch):
    from stowage.checker import stability_liquid as liquid
    from stowage.checker import stability_liquid_tables as tables
    path = tmp_path / "manifest.json"
    monkeypatch.setattr(liquid, "manifest_path", lambda params: path)
    monkeypatch.setattr(liquid, "metadata", lambda params: {"inputs_sha256": "geometry"})
    monkeypatch.setattr(tables, "_identity", lambda solid: solid)
    payload = dict(metadata={"inputs_sha256": "geometry"},
        validation=dict(gz=.1, area=.1, gm=.1, samples=4), tank_table_keys={"P": "tank-p"})
    path.write_text(json.dumps(payload))
    assert cache_warm._manifest_valid({}, {"P": "tank-p"})
    assert not cache_warm._manifest_valid({}, {"P": "changed-geometry"})
    path.write_text("broken checkpoint")
    assert not cache_warm._manifest_valid({}, {"P": "tank-p"})


def test_repository_reference_validators_are_importable():
    # A cold warm-up needs all three validators, even when ordinary CI has
    # qualified physics caches. Importing them performs no geometry work.
    for relative, hook in [
        ("interpolation/validate.py", "validate"),
        ("method1_tables/qualify_tanks.py", "run"),
        ("method1_tables/qualify.py", "run"),
    ]:
        assert callable(getattr(cache_warm._validator(relative), hook))


@pytest.mark.parametrize('change', ['narrow heel','narrow trim','missing axis','nonfinite','descending'])
def test_required_domain_rejects_incomplete_or_malformed_axes(change):
    import numpy as np
    wanted=dict(heel=np.linspace(-70.,70.,29),trim=np.linspace(-.04,.04,5))
    actual={name:axis.copy() for name,axis in wanted.items()}
    if change=='narrow heel': actual['heel']=np.linspace(-60.,60.,25)
    elif change=='narrow trim': actual['trim']=np.linspace(-.02,.02,5)
    elif change=='missing axis': actual.pop('heel')
    elif change=='nonfinite': actual['heel'][2]=np.nan
    else: actual['heel']=actual['heel'][::-1]
    assert not cache_warm._covers_domain(actual,wanted)


def test_required_domain_accepts_denser_wider_grid():
    import numpy as np
    wanted=dict(heel=np.linspace(-70.,70.,29),trim=np.linspace(-.04,.04,5))
    actual=dict(heel=np.linspace(-75.,75.,61),trim=np.linspace(-.05,.05,21))
    assert cache_warm._covers_domain(actual,wanted)


@pytest.mark.parametrize('rebuild_reason',['domain','budget'])
def test_narrow_certified_tables_rebuild_and_loaded_qualification_uses_eight(tmp_path,monkeypatch,rebuild_reason):
    from types import SimpleNamespace
    import numpy as np
    from stowage.checker import stability_tables as kn
    from stowage.checker import stability_liquid_tables as liquid
    from stowage.checker import stability_sounding as sounding
    from stowage.checker import stability_liquid as dispatch
    from stowage.checker import stability_geometry as geometry
    from stowage.vessel.tanks import TankGeometry
    monkeypatch.setattr(cache_warm,'REPORTS',tmp_path)
    required=dict(immersion_m=np.linspace(0.,10.,4),trim_m=np.linspace(-4.,4.,4),heel_rad=np.linspace(0.,70.,4))
    narrow=dict(required,heel_rad=np.linspace(0.,60.,4))
    state={'kn':{},'liquid':None,'qualified':False}; events=[]
    monkeypatch.setattr(geometry,'StabilityGeometry',lambda params:object())
    monkeypatch.setattr(kn,'default_grid',lambda direct:required)
    def make_kn(rho,grid):
        path=tmp_path/f'kn-{rho}.npz'
        path.with_suffix('.certificate.json').write_text(json.dumps(dict(validation={},cache_sha256='test')))
        return SimpleNamespace(grid=grid,cache_path=path,metadata={})
    for rho in (1000.,1025.): state['kn'][rho]=make_kn(rho,narrow)
    monkeypatch.setattr(kn,'cached_geometry',lambda direct,params,rho:state['kn'][rho])
    monkeypatch.setattr(kn,'migrate_legacy',lambda *args:events.append('migrate KN'))
    solid=SimpleNamespace(shape='hull_sections')
    monkeypatch.setattr(TankGeometry,'from_params',lambda *a,**kw:solid)
    monkeypatch.setattr(sounding,'CACHE_DIRECTORY',tmp_path)
    monkeypatch.setattr(sounding,'_key',lambda solid:'test')
    monkeypatch.setattr(sounding,'lookup',lambda solid:None)
    state['liquid']=SimpleNamespace(fills=liquid.DEFAULT_FILLS,heels=np.radians(np.linspace(-60.,60.,25)),trims=liquid.DEFAULT_TRIMS)
    monkeypatch.setattr(liquid,'lookup',lambda solid:state['liquid'])
    if rebuild_reason=='budget':
        state['liquid']=SimpleNamespace(fills=np.linspace(0.,1.,129),
            heels=liquid.DEFAULT_HEELS,trims=liquid.DEFAULT_TRIMS)
    monkeypatch.setattr(cache_warm,'_liquid_budget_ready',lambda *args:
        rebuild_reason!='budget' or len(state['liquid'].fills)==len(liquid.DEFAULT_FILLS))
    monkeypatch.setattr(liquid,'migrate_legacy',lambda solid:events.append('migrate liquid'))
    def build(solid,**grid):
        events.append('build liquid')
        state['liquid']=SimpleNamespace(**grid)
    monkeypatch.setattr(liquid,'build',build)
    monkeypatch.setattr(liquid,'_path',lambda solid:tmp_path/'liquid.npz')
    monkeypatch.setattr(dispatch,'manifest_path',lambda params:tmp_path/'manifest.json')
    monkeypatch.setattr(dispatch,'migrate_legacy',lambda params:None)
    monkeypatch.setattr(cache_warm,'_manifest_valid',lambda *a:state['qualified'])
    def validate(name,rho,**kw):
        events.append(('build KN',rho))
        state['kn'][rho]=make_kn(rho,required)
        return {}
    def component(*args,**kw): events.append('qualify component')
    def loaded(*args,**kw):
        events.append(('qualify loaded',kw['samples']))
        state['qualified']=True
    def validator(path):
        return SimpleNamespace(**({'validate':validate} if path.startswith('interpolation/') else
              {'run':component if path.endswith('qualify_tanks.py') else loaded}))
    monkeypatch.setattr(cache_warm,'_validator',validator)
    rows=cache_warm._warm_stability('feeder_500',dict(tanks=[{'id':'FPT'}]))
    assert sum(isinstance(event,tuple) and event[0]=='build KN' for event in events)==2
    assert events.count('build liquid')==1
    assert ('qualify loaded',8) in events
    assert all(row['status']=='built and qualified' for row in rows if row['cache'] in ('KN','liquid transfer'))
    events.clear()
    rows=cache_warm._warm_stability('feeder_500',dict(tanks=[{'id':'FPT'}]))
    assert events==[]
    assert all(row['status']=='reused' for row in rows if row['cache'] in ('KN','liquid transfer'))


def test_liquid_default_refinement_has_well_separated_knots():
    import numpy as np
    from stowage.checker import stability_liquid_tables as tables
    grid=cache_warm._liquid_required_grid({'id':'DB-M-P'},tables)
    assert len(grid['fills'])==257
    assert len(grid['heels'])==57
    assert len(grid['trims'])==17
    assert np.min(np.diff(grid['trims']))>=.005-1e-12
    for angle in (-.03,-.015,-.01,-.005,.005,.01,.015,.03):
        assert np.min(abs(grid['trims']-angle))<1e-12


def test_certified_coarse_liquid_grid_can_cover_refined_defaults():
    from types import SimpleNamespace
    import numpy as np
    from stowage.checker import stability_liquid_tables as tables
    coarse=SimpleNamespace(fills=np.linspace(0.,1.,129),
        heels=np.radians(np.linspace(-70.,70.,29)),trims=np.linspace(-.06,.06,7))
    required=cache_warm._liquid_required_grid({'id':'FPT'},tables)
    assert cache_warm._liquid_covers_domain(coarse,required)


@pytest.mark.parametrize('component_area,kn_area,expected',[
    (.191883,.049545,False), # measured feeder_800 coarse grid: each passes alone
    (.1545,.01727,True),    # measured feeder_500 coarse grid: combined headroom
    (.07653,.01638,True),   # measured feeder_1700 refined grid
])
def test_liquid_reuse_requires_combined_error_budget(tmp_path,component_area,kn_area,expected):
    from types import SimpleNamespace
    certificate=tmp_path/'tank.qualified.json'
    certificate.write_text(json.dumps(dict(gz=.04,area=component_area,gm=.001)))
    liquid=SimpleNamespace(_path=lambda solid:tmp_path/'tank.npz')
    evidence=[dict(validation=dict(max_l5_fraction=dict(gz=.08,area=kn_area,gm=.01)))]
    assert cache_warm._liquid_budget_ready(object(),liquid,evidence) is expected


@pytest.mark.parametrize('problem',['missing certificate','broken certificate','nan','missing KN','second density'])
def test_liquid_budget_rejects_unmeasured_or_worse_density(tmp_path,problem):
    from types import SimpleNamespace
    path=tmp_path/'tank.qualified.json'
    path.write_text(json.dumps(dict(gz=.04,area=.1,gm=.001)))
    liquid=SimpleNamespace(_path=lambda solid:tmp_path/'tank.npz')
    evidence=[dict(validation=dict(max_l5_fraction=dict(gz=.08,area=.04,gm=.01)))]
    if problem=='missing certificate': path.unlink()
    elif problem=='broken certificate': path.write_text('interrupted')
    elif problem=='nan': path.write_text(json.dumps(dict(gz=.04,area=float('nan'),gm=.001)))
    elif problem=='missing KN': evidence=[]
    else: evidence.append(dict(validation=dict(max_l5_fraction=dict(gz=.08,area=.15,gm=.01))))
    assert not cache_warm._liquid_budget_ready(object(),liquid,evidence)


@pytest.mark.parametrize('failure_scope',['first','all'])
def test_failed_linear_profile_does_not_skip_other_geometry_hooks(tmp_path,monkeypatch,failure_scope):
    import stowage.linear as linear
    setup_ship(tmp_path,monkeypatch,linear_hook=True)
    calls=[]
    def warm(vessel,*,workers):
        assert workers==1
        calls.append(vessel)
        if failure_scope=='all' or len(calls)==1:
            raise ValueError('policy epoch stale')
        return dict(status='reused')
    monkeypatch.setattr(linear,'warm_cache',warm)
    result=cache_warm._warm_ship('feeder_500')
    assert calls==['tiny','small','medium','full','tiny_production']
    assert result['complete'] is False
    rows=[row for row in result['caches'] if row['cache']=='linear']
    assert len(rows)==5
    failed=[row for row in rows if row['status']=='failed']
    assert len(failed)==(5 if failure_scope=='all' else 1)
    assert all(row['error']=='ValueError: policy epoch stale' for row in failed)
    assert result['errors']==[
        f"linear {row['geometry']}: ValueError: policy epoch stale" for row in failed]
    assert json.loads((tmp_path/'feeder_500/summary.json').read_text())==result

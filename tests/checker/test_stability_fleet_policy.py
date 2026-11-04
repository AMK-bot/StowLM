"""S5: embedded fleet policy must retain its canonical public-builder baseline."""
from types import SimpleNamespace
import hashlib
from pathlib import Path

import numpy as np
import pytest

from stowage.checker import stability, stability_criteria, stability_curve
from stowage.checker.stability_loading import LoadingCondition
from stowage.model import VesselFieldProvenance
from stowage.model.fixtures import build_instance
from stowage.params import repo_root
from stowage.vessel import fleet

_public_load_vessel = fleet.load_vessel


@pytest.fixture(autouse=True)
def isolated_canonical_policy_cache():
    """Resolver monkeypatches must never share a previously trusted baseline."""
    def clear():
        reset = getattr(getattr(stability,'_canonical_policy_baseline',None),'cache_clear',None)
        if reset is not None:
            reset()
    clear()
    yield
    clear()


@pytest.fixture(scope='module')
def canonical():
    return fleet.load_vessel('feeder_500')


@pytest.fixture
def instance(monkeypatch, canonical):
    value = build_instance()
    value.vessel = canonical.model_copy(deep=True)
    value.onboard = value.onboard.model_copy(update={'vessel_id': value.vessel.id, 'tank_masses_kg': {}})
    # The resolver is the public builder; isolate its returned model for fast
    # failure injection while retaining the genuinely built canonical baseline.
    monkeypatch.setattr(fleet, 'load_vessel', lambda name: canonical.model_copy(deep=True))
    return value


def computed_report(monkeypatch, instance, *, trim=0., mass=None):
    monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD','2')
    monkeypatch.setenv('STOWAGE_STABILITY_DIRECT','1')
    hull = instance.vessel.hull
    mass = mass if mass is not None else (hull.min_displacement_kg+hull.max_displacement_kg)/2
    floating = SimpleNamespace(t_aft_m=5.+trim/2,t_fwd_m=5.-trim/2,
        mean_draught_m=5.,trim_m=trim,heel_rad=0.,residual_norm=0.)
    geometry = SimpleNamespace(equilibrium=lambda *args:floating,openings=[],
        full_submerged=lambda *args:SimpleNamespace(centroid_m=(50.,0.,4.),bm_t_m=3.),
        waterplane_normal=lambda *args:np.array([0.,0.,1.]))
    monkeypatch.setattr(stability,'_context',lambda vessel:(geometry,{}))
    monkeypatch.setattr(stability,'loading_condition',lambda *args,**kwargs:
        LoadingCondition(mass,50.,0.,4.,0.,0.))
    angles = np.array([0.,.5,1.])
    curve = SimpleNamespace(angles=angles,gz=np.array([0.,.5,.6]),
        negative_gz=np.array([0.,-.5,-.6]),drafts=np.full((3,2),5.),
        flooding_angle=1.,at_angle=lambda phi:(.5,(5.,5.)))
    monkeypatch.setattr(stability_curve,'build_stability_curve',lambda *args,**kwargs:curve)
    monkeypatch.setattr(stability,'_deck_edge_angle',lambda *args,**kwargs:.5)
    monkeypatch.setattr(stability,'_windage',lambda *args:(100.,5.))
    monkeypatch.setattr(stability_criteria,'evaluate_criteria',lambda *args,**kwargs:({}, {}, []))
    return stability.check_stability(instance,instance.onboard,call_index=0)


def assert_missing(report, text):
    assert not report.ran
    assert {v.rule_id for v in report.violations} == {'checker.missing_vessel_data'}
    assert text in report.error


@pytest.mark.parametrize(('field','delta'), [('min_trim_m',-1.),('max_trim_m',1.),
    ('min_displacement_kg',-1.),('max_displacement_kg',1_000_000.),('bilge_keel_area_m2',1.)])
def test_embedded_fleet_baseline_cannot_be_widened_with_arbitrary_provenance(monkeypatch, instance, field, delta):
    setattr(instance.vessel.hull,field,getattr(instance.vessel.hull,field)+delta)
    setattr(instance.vessel.hull,field+'_provenance',VesselFieldProvenance(
        assumption='Invented more permissive loading-manual baseline'))
    assert_missing(computed_report(monkeypatch,instance),field)


def test_review_wider_trim_hull_cannot_approve_outside_canonical_limit(monkeypatch, instance):
    instance.vessel.hull.max_trim_m = 100.
    instance.vessel.hull.max_trim_m_provenance = VesselFieldProvenance(assumption='arbitrary wider manual')
    assert_missing(computed_report(monkeypatch,instance,trim=2.),'max_trim_m')


def test_review_wider_displacement_hull_cannot_approve_outside_canonical_limit(monkeypatch, instance):
    original = instance.vessel.hull.max_displacement_kg
    instance.vessel.hull.max_displacement_kg += 2_000_000.
    instance.vessel.hull.max_displacement_kg_provenance = VesselFieldProvenance(assumption='arbitrary wider manual')
    assert_missing(computed_report(monkeypatch,instance,mass=original+1_000_000.),'max_displacement_kg')


@pytest.mark.parametrize('kind',['missing','blank','malformed','stale','fragment','conflicting','wrong_root'])
def test_fleet_parameter_source_requires_one_exact_current_digest(monkeypatch, instance, kind):
    prefix = 'params/fleet/feeder_500.toml'
    original = next(source for source in instance.vessel.sources if source.startswith(prefix+' '))
    instance.vessel.sources = [source for source in instance.vessel.sources if not source.startswith(prefix)]
    replacements = {
        'missing':[], 'blank':[prefix+' sha256:'], 'malformed':[prefix+' sha256:not-a-digest'],
        'stale':[prefix+' sha256:'+'0'*64], 'fragment':[original+'#unverified'],
        'conflicting':[original,prefix+' sha256:'+'0'*64],
        'wrong_root':[original.replace('feeder_500.toml','feeder_800.toml')],
    }
    instance.vessel.sources.extend(replacements[kind])
    assert_missing(computed_report(monkeypatch,instance),'parameter source')


@pytest.mark.parametrize(('vessel_id','root'), [('feeder_500','feeder_800'),
    ('feeder_500.tiny','feeder_800'),('feeder_500','unknown_parent')])
def test_known_identity_cannot_select_a_different_slice_root(monkeypatch, instance, vessel_id, root):
    instance.vessel.id,instance.vessel.slice_of = vessel_id,root
    instance.onboard.vessel_id = vessel_id
    assert_missing(computed_report(monkeypatch,instance),'root')


@pytest.mark.parametrize('exception',[FileNotFoundError('missing public vessel data'),
    ValueError('invalid canonical vessel'),RuntimeError('canonical resolver failed')])
def test_canonical_resolver_failure_is_missing_data_not_exception(monkeypatch, instance, exception):
    def fail(name):
        raise exception
    monkeypatch.setattr(fleet,'load_vessel',fail)
    assert_missing(computed_report(monkeypatch,instance),'canonical')


@pytest.mark.parametrize('slice_id',[None,'feeder_500.tiny','custom_nested_slice'])
def test_canonical_full_vessel_and_named_or_custom_slice_keep_checking(monkeypatch, instance, slice_id):
    if slice_id:
        instance.vessel.id,instance.vessel.slice_of = slice_id,'feeder_500'
        instance.onboard.vessel_id = slice_id
    report = computed_report(monkeypatch,instance)
    assert report.ran,report.error


def test_same_baseline_with_different_valid_evidence_and_stricter_instance_policy_is_valid(monkeypatch, instance):
    instance.vessel.hull.max_trim_m_provenance = VesselFieldProvenance(assumption='Independently cited unchanged bound')
    instance.rule_overrides = {'iscode.loading.trim':{'maximum':.5}}
    instance.vessel.sources.reverse()
    report = computed_report(monkeypatch,instance,trim=.75)
    assert report.ran,report.error
    assert any(v.rule_id=='iscode.loading.trim' and v.limit==.5 for v in report.violations)


def test_one_ulp_change_in_embedded_baseline_is_not_canonical(monkeypatch, instance):
    instance.vessel.hull.max_trim_m = np.nextafter(instance.vessel.hull.max_trim_m,np.inf)
    assert_missing(computed_report(monkeypatch,instance),'max_trim_m')


@pytest.mark.parametrize('bad_result',['wrong_id','missing_hull','missing_endpoint','invalid_endpoint'])
def test_inconsistent_canonical_resolver_model_fails_closed(monkeypatch, instance, canonical, bad_result):
    bad = canonical.model_copy(deep=True)
    if bad_result == 'wrong_id':
        bad.id = 'feeder_800'
    elif bad_result == 'missing_hull':
        bad.hull = None
    else:
        bad.hull = bad.hull.model_copy(update={'min_trim_m':None if bad_result == 'missing_endpoint' else float('nan')})
    monkeypatch.setattr(fleet,'load_vessel',lambda name:bad)
    assert_missing(computed_report(monkeypatch,instance),'canonical')


def test_current_parameter_bytes_are_checked_again_between_calls(monkeypatch, instance):
    report = computed_report(monkeypatch,instance)
    assert report.ran,report.error
    payload = fleet._parameter_bytes('feeder_500')
    changed = payload[:-1]+(b' ' if payload[-1:] != b' ' else b'\n')
    assert len(changed) == len(payload) and changed != payload
    monkeypatch.setattr(fleet,'_parameter_bytes',lambda name:changed)
    assert_missing(computed_report(monkeypatch,instance),'parameter source')


@pytest.mark.parametrize('widen',[False,True])
def test_real_public_builder_accepts_canonical_policy_and_rejects_embedded_widening(monkeypatch, instance, widen):
    monkeypatch.setattr(fleet,'load_vessel',_public_load_vessel)
    if widen:
        instance.vessel.hull.max_trim_m += 1.
    report = computed_report(monkeypatch,instance)
    if widen:
        assert_missing(report,'max_trim_m')
    else:
        assert report.ran,report.error


def test_renamed_vessel_cannot_bypass_its_preserved_fleet_parameter_claim(monkeypatch, instance):
    instance.vessel.id = 'renamed_vessel'
    instance.vessel.slice_of = None
    instance.onboard.vessel_id = instance.vessel.id
    assert_missing(computed_report(monkeypatch,instance),'canonical fleet root')


@pytest.mark.parametrize('exception',[OSError('unreadable parameter file'),FileNotFoundError('parameter file removed')])
def test_current_parameter_source_resolution_errors_fail_closed(monkeypatch, instance, exception):
    def fail(name):
        raise exception
    monkeypatch.setattr(fleet,'_parameter_bytes',fail)
    assert_missing(computed_report(monkeypatch,instance),'canonical')


def test_cached_baseline_resolves_builder_once_but_checks_offsets_every_call(monkeypatch, instance, canonical):
    from stowage.vessel import hull_offsets
    builds,guards = [],[]
    def build(name):
        builds.append(name)
        return canonical.model_copy(deep=True)
    original_guard = hull_offsets.verified_offset_bytes
    def guard(path, *, expected_sha256):
        guards.append((path,expected_sha256))
        return original_guard(path,expected_sha256=expected_sha256)
    monkeypatch.setattr(fleet,'load_vessel',build)
    monkeypatch.setattr(hull_offsets,'verified_offset_bytes',guard)
    for trim in (0.,.1,-.1):
        report = computed_report(monkeypatch,instance,trim=trim)
        assert report.ran,report.error
    assert builds == ['feeder_500']
    assert len(guards) == 3


def test_cached_baseline_new_parameter_digest_resolves_new_builder_identity(monkeypatch, instance, canonical):
    payload = fleet._parameter_bytes('feeder_500')
    current = [payload]
    builds = []
    def source():
        return 'params/fleet/feeder_500.toml sha256:'+hashlib.sha256(current[0]).hexdigest()
    def build(name):
        builds.append((name,hashlib.sha256(current[0]).hexdigest()))
        value = canonical.model_copy(deep=True)
        value.sources = [s for s in value.sources if not s.startswith('params/fleet/')]+[source()]
        return value
    monkeypatch.setattr(fleet,'_parameter_bytes',lambda name:current[0])
    monkeypatch.setattr(fleet,'load_vessel',build)
    for generation in range(2):
        if generation:
            current[0] = payload+b'\n# Independent test parameter identity, same physical fields.\n'
        instance.vessel.sources = [s for s in instance.vessel.sources if not s.startswith('params/fleet/')]+[source()]
        for _ in range(2):
            report = computed_report(monkeypatch,instance)
            assert report.ran,report.error
    assert len(builds) == 2
    assert builds[0][0] == builds[1][0] == 'feeder_500'
    assert builds[0][1] != builds[1][1]


@pytest.mark.parametrize('field',['min_trim_m','max_trim_m','min_displacement_kg','max_displacement_kg','bilge_keel_area_m2'])
def test_cached_baseline_still_rejects_embedded_policy_mutation(monkeypatch, instance, field):
    initial = computed_report(monkeypatch,instance)
    assert initial.ran,initial.error
    setattr(instance.vessel.hull,field,getattr(instance.vessel.hull,field)+1.)
    assert_missing(computed_report(monkeypatch,instance),field)


def test_cached_baseline_still_requires_current_embedded_evidence(monkeypatch, instance):
    initial = computed_report(monkeypatch,instance)
    assert initial.ran,initial.error
    instance.vessel.hull.max_trim_m_provenance = None
    assert_missing(computed_report(monkeypatch,instance),'max_trim_m_provenance')


def test_failed_canonical_resolution_is_not_cached_and_recovers(monkeypatch, instance, canonical):
    calls = []
    def build(name):
        calls.append(name)
        if len(calls) == 1:
            raise FileNotFoundError('temporarily missing canonical source')
        return canonical.model_copy(deep=True)
    monkeypatch.setattr(fleet,'load_vessel',build)
    assert_missing(computed_report(monkeypatch,instance),'canonical')
    for _ in range(2):
        recovered = computed_report(monkeypatch,instance)
        assert recovered.ran,recovered.error
    assert calls == ['feeder_500','feeder_500']


def test_cached_baseline_is_an_immutable_numeric_snapshot(monkeypatch, instance, canonical):
    returned = canonical.model_copy(deep=True)
    builds = []
    def build(name):
        builds.append(name)
        return returned
    monkeypatch.setattr(fleet,'load_vessel',build)
    initial = computed_report(monkeypatch,instance)
    assert initial.ran,initial.error
    returned.hull.max_trim_m += 1.
    repeated = computed_report(monkeypatch,instance)
    assert repeated.ran,repeated.error
    assert builds == ['feeder_500']


@pytest.mark.parametrize('exception',[FileNotFoundError('offset source removed'),ValueError('offset source checksum mismatch')])
def test_cached_baseline_checks_pinned_offsets_even_after_warming(monkeypatch, instance, exception):
    from stowage.vessel import hull_offsets
    initial = computed_report(monkeypatch,instance)
    assert initial.ran,initial.error
    def unavailable(*args,**kwargs):
        raise exception
    monkeypatch.setattr(hull_offsets,'verified_offset_bytes',unavailable)
    assert_missing(computed_report(monkeypatch,instance),'offset source')


@pytest.mark.parametrize('exception',[FileNotFoundError('parameter source removed'),OSError('parameter source unreadable')])
def test_cached_baseline_checks_parameter_source_even_after_warming(monkeypatch, instance, exception):
    initial = computed_report(monkeypatch,instance)
    assert initial.ran,initial.error
    def unavailable(name):
        raise exception
    monkeypatch.setattr(fleet,'_parameter_bytes',unavailable)
    assert_missing(computed_report(monkeypatch,instance),'parameter source')


@pytest.mark.parametrize('relative',['params/cargo/feeder_export.toml',
    'params/ports/laem_chabang_like.toml','params/ports/small_feeder.toml'])
@pytest.mark.parametrize('mode',['changed','unreadable'])
def test_cached_baseline_revalidates_auxiliary_builder_inputs(monkeypatch, instance, canonical, relative, mode):
    target = repo_root()/relative
    original_read = Path.read_bytes
    payload = original_read(target)
    builds = []
    changed = [False]
    def build(name):
        builds.append(name)
        if changed[0] and mode == 'changed':
            raise ValueError('canonical builder rejected changed auxiliary input')
        return canonical.model_copy(deep=True)
    monkeypatch.setattr(fleet,'load_vessel',build)
    initial = computed_report(monkeypatch,instance)
    assert initial.ran,initial.error
    def read(path):
        if path == target:
            if mode == 'unreadable':
                raise OSError('unreadable auxiliary builder input')
            return payload+b'\n# new auxiliary builder input identity\n'
        return original_read(path)
    changed[0] = True
    monkeypatch.setattr(Path,'read_bytes',read)
    assert_missing(computed_report(monkeypatch,instance),'canonical')
    assert len(builds) == (2 if mode == 'changed' else 1)

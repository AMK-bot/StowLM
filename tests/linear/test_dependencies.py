"""D30 identities follow numerical content, never global release metadata."""
from copy import deepcopy
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from stowage.linear import cache,dependencies
from stowage.linear.model import ValidityBand
from stowage.model.fixtures import build_instance


def keys(instance):
    band=ValidityBand(1.,2.,.1,1.,.1,.1)
    params={'hull':{'lpp_m':10.,'beam_m':2.,'depth_m':5.,'design_draught_m':2.,'water_density_kg_m3':1025.},
        'hull_form':{'family':'synthetic'},'envelope':{'hatch_coamings':[]},
        'tanks':[{'id':'T','geometry':{'shape':'rectangular_prism','x_min_m':1.}}]}
    return {
        **{kind:cache.vessel_cache_key('ship',params,inputs={'kind':label}) for kind,label in
           (('geometry','HullGeometry'),('integration','Gauss-panel force/moment integration'),
            ('fsm','continuous upright maximum slack FSM'))},
        'coefficient':cache.coefficient_family_key(instance,params,band),
        'calibration':cache.cache_key('family_calibration',vessel_id='ship',params=params,inputs={'samples':192},band=band),
        'instance':cache.model_cache_key(instance,band,64,params)}


def test_all_artifacts_ignore_global_component_versions_and_instance_provenance(monkeypatch):
    import stowage.versions as versions
    instance=build_instance();before=keys(instance)
    for name in tuple(vars(versions)):
        if name.endswith('_VERSION'):monkeypatch.setattr(versions,name,'unrelated-release')
    monkeypatch.setattr(versions,'component_versions',lambda:{'checker':'new','ballast':'new','schema':'new'})
    changed=instance.model_copy(update={'id':'different-display-id',
        'provenance':instance.provenance.model_copy(update={'versions':{'checker':'different','ballast':'different'}})})
    assert keys(changed)==before


def test_explicit_legacy_global_version_overrides_are_not_dependencies():
    args=dict(vessel_id='ship',params={},inputs={},source_hash='numerical-code')
    assert cache.cache_key('model',**args,versions={'checker':'1','ballast':'1'})==cache.cache_key(
        'model',**args,versions={'checker':'99','ballast':'99'})


def test_relevant_builder_version_invalidates_only_its_dependent_artifacts(monkeypatch):
    instance=build_instance();before=keys(instance)
    monkeypatch.setattr(dependencies,'LINEAR_CALIBRATION_VERSION','changed')
    after=keys(instance)
    assert after['calibration']!=before['calibration'] and after['instance']!=before['instance']
    assert all(after[name]==before[name] for name in ('geometry','integration','fsm','coefficient'))


def test_exact_upright_band_builder_invalidates_only_instances(source_tree):
    artifacts=('geometry','integration','fsm','coefficient','calibration','instance')
    before={name:dependencies.dependency_fingerprint(name) for name in artifacts}
    path=source_tree/'linear/upright_mass.py'
    raw=path.read_text()
    changed=raw.replace("UPRIGHT_MASS_VERSION = '1.0'", "UPRIGHT_MASS_VERSION = 'test-version'")
    assert changed!=raw
    path.write_text(changed)
    after={name:dependencies.dependency_fingerprint(name) for name in artifacts}
    assert after['instance']!=before['instance']
    assert all(after[name]==before[name] for name in artifacts if name!='instance')


def test_direct_liquid_fallback_invalidates_reference_not_coefficients(source_tree):
    artifacts=('geometry','coefficient','calibration','liquid_reference','instance')
    before={name:dependencies.dependency_fingerprint(name) for name in artifacts}
    path=source_tree/'checker/stability_direct_liquid_batch.py'
    path.write_text(path.read_text()+'\nDIRECT_REFERENCE_TEST_VERSION = 2\n')
    after={name:dependencies.dependency_fingerprint(name) for name in artifacts}
    assert all(after[name]!=before[name] for name in ('calibration','liquid_reference','instance'))
    assert all(after[name]==before[name] for name in ('geometry','coefficient'))


def test_fixed_tank_integrals_have_own_builder_and_no_gm_rule_dependency(monkeypatch):
    import stowage.rules.catalogue as catalogue
    params={'id':'fixed-liquid','tanks':[{'id':'T','geometry':{'shape':'rectangular_prism'}}]}
    def key():
        return cache.cache_key('fixed_tank_integrals',vessel_id='fixed-liquid',
            params=params,inputs={'tank_id':'T','volume_m3':3.,'stations_m':[0.,1.]})
    before=key()
    def forbidden(*args):
        pytest.fail('fixed tank integration consulted unrelated GM rule')
    monkeypatch.setattr(catalogue,'get_rule',forbidden)
    assert key()==before
    monkeypatch.setattr(dependencies,'LINEAR_FIXED_TANK_INTEGRALS_VERSION','changed')
    assert key()!=before


@pytest.fixture
def source_tree(monkeypatch,tmp_path):
    original=dependencies._ROOT
    names={name for profile in dependencies._PROFILES.values() for name in profile}
    for name in names:
        target=tmp_path/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((original/name).read_bytes())
    monkeypatch.setattr(dependencies,'_ROOT',tmp_path)
    return tmp_path


def test_whole_file_code_changes_invalidate_but_comments_and_unrelated_files_do_not(source_tree):
    before={name:dependencies.dependency_fingerprint(name) for name in ('geometry','coefficient','calibration')}
    unrelated=source_tree/'checker/visibility.py';unrelated.write_text('UNRELATED=999\n')
    path=source_tree/'vessel/hydrostatics.py';raw=path.read_text()
    assert {name:dependencies.dependency_fingerprint(name) for name in before}==before
    path.write_text('# Comments do not change numerical output\n'+raw)
    # Every admitted module uses the whole AST, excluding comments/docstrings.
    assert {name:dependencies.dependency_fingerprint(name) for name in before}==before
    # A numerical implementation edit is detected from fresh bytes, without
    # global version or file-stat changes being required.
    path.write_text(raw.replace('math.pi/2-1e-5','math.pi/2-2e-5'))
    assert path.read_text()!=raw
    assert all(dependencies.dependency_fingerprint(name)!=value for name,value in before.items())


def test_only_actual_initial_gm_rule_value_affects_coefficients(monkeypatch):
    import stowage.rules.catalogue as catalogue
    instance=build_instance();before=keys(instance)
    rule=catalogue.get_rule('iscode.a2_2.initial_gm')
    original=float(rule.parameters['minimum'].value)
    monkeypatch.setattr(catalogue,'get_rule',lambda *args:NS(parameters={'minimum':NS(value=original+.01)}))
    after=keys(instance)
    assert after['coefficient']!=before['coefficient'] and after['instance']!=before['instance']
    assert all(after[name]==before[name] for name in ('geometry','integration','fsm','calibration'))


def test_coefficients_exclude_weather_visibility_and_lashing_metadata():
    instance=build_instance();band=ValidityBand(1.,2.,.1,1.,.1,.1)
    params={'hull':{'lpp_m':10.},'hull_form':{'family':'synthetic'},'envelope':{},'windage':{'area':100.}}
    original=cache.coefficient_family_key(instance,params,band)
    changed_params=deepcopy(params);changed_params['windage']['area']=999.
    vessel=instance.vessel.model_copy(update={'bridge':None,'heat_sources':[],'sources':['a different description'],'name':'display only'})
    changed=instance.model_copy(update={'vessel':vessel})
    assert cache.coefficient_family_key(changed,changed_params,band)==original


def test_whole_file_fallback_and_selected_centroid_stencil_invalidate(source_tree):
    before={name:dependencies.dependency_fingerprint(name) for name in
            ('geometry','coefficient','calibration','liquid_reference','instance')}
    table=source_tree/'checker/stability_liquid_tables.py'
    original=table.read_text()
    assert 'max_transfer' in original
    table.write_text(original.replace('spline(candidates)', 'spline(candidates)+123.'))
    transfer=source_tree/'checker/stability_tank_transfer.py'
    transfer_original=transfer.read_text()
    # Perturb the unselected finite-angle envelope, independently of its
    # implementation's evolving cache-key spelling.
    transfer_changed=transfer_original.replace('result = max(0., best, -heap[0][0])',
                                              'result = max(0., best, -heap[0][0])+123.')
    assert transfer_changed!=transfer_original
    transfer.write_text(transfer_changed)
    after={name:dependencies.dependency_fingerprint(name) for name in before}
    # The table module has decorators: its whole file is deliberately bound.
    assert {name for name in before if after[name]!=before[name]}=={
        'calibration','liquid_reference','instance'}
    table.write_text(original)
    # Every admitted transfer body is bound even when the edited envelope
    # method is unselected.
    after={name:dependencies.dependency_fingerprint(name) for name in before}
    assert {name for name in before if after[name]!=before[name]}=={
        'calibration','liquid_reference','instance'}
    transfer.write_text(transfer_original)
    assert {name:dependencies.dependency_fingerprint(name) for name in before}==before
    for old,new in (('value < axis[0]-1e-13','value < axis[0]-2e-13'),
                    ('volume/self.capacity','volume/(self.capacity+1.)')):
        changed=original.replace(old,new)
        assert changed!=original
        table.write_text(changed)
        after={name:dependencies.dependency_fingerprint(name) for name in before}
        assert all(after[name]!=before[name] for name in ('calibration','liquid_reference','instance'))
        assert all(after[name]==before[name] for name in ('geometry','coefficient'))
    table.write_text(original)
    transfer.write_text(transfer_original.replace('trim_rad, polynomial=False)[0]',
                                                 'trim_rad, polynomial=True)[0]'))
    assert dependencies.dependency_fingerprint('calibration')!=before['calibration']


def test_method_selectors_fail_closed_on_missing_or_ambiguous_method():
    for raw in (b'class Table:\n    pass\n',
                b'class Table:\n    def query(self): pass\n    def query(self): pass\n'):
        with pytest.raises(AssertionError,match='ambiguous or missing numerical method'):
            dependencies._semantic_hash(raw,('Table.query',))


def test_liquid_reference_builder_version_is_a_calibration_dependency(monkeypatch):
    before={name:dependencies.dependency_fingerprint(name) for name in ('geometry','coefficient','calibration','instance')}
    monkeypatch.setattr(dependencies,'LINEAR_LIQUID_REFERENCE_VERSION','changed')
    after={name:dependencies.dependency_fingerprint(name) for name in before}
    assert all(after[name]!=before[name] for name in ('calibration','instance'))
    assert all(after[name]==before[name] for name in ('geometry','coefficient'))


def _profiles():
    return {name: dependencies.dependency_fingerprint(name) for name in dependencies._PROFILES}


@pytest.mark.parametrize('path,class_name', [
    ('model/vessel.py','HydrostaticRow'), ('model/vessel.py','TankSoundingPoint'),
    ('model/vessel.py','FixedLoad'), ('model/ship.py','ShipState'),
    ('model/base.py','StowModel'), ('linear/model.py','ValidityBand'),
    ('linear/model.py','LinearErrors'), ('linear/model.py','LinearModel'),
    ('checker/stability_loading.py','LoadingCondition'),
])
def test_module_class_field_additions_invalidate_conservative_consumers(source_tree,path,class_name):
    before=_profiles()
    target=source_tree/path
    raw=target.read_text()
    node=next(node for node in ast.parse(raw).body if isinstance(node,ast.ClassDef) and node.name==class_name)
    lines=raw.splitlines(keepends=True)
    lines.insert(node.end_lineno, '\n    review_evidence: str | None = None\n')
    target.write_text(''.join(lines))
    after=_profiles()
    expected={name for name,profile in dependencies._PROFILES.items() if path in profile}
    assert {name for name in before if after[name]!=before[name]}==expected


@pytest.mark.parametrize('path', ['model/base.py','conventions.py',
    'checker/stability_loading.py','checker/stability_liquid.py',
    'checker/stability_liquid_batch.py'])
def test_new_module_function_bindings_invalidate_conservative_consumers(source_tree,path):
    before=_profiles()
    target=source_tree/path
    target.write_text(target.read_text()+'\ndef evidence_only_voyage_policy(record):\n    return record.get("evidence", "unavailable")\n')
    after=_profiles()
    expected={name for name,profile in dependencies._PROFILES.items() if path in profile}
    assert {name for name in before if after[name]!=before[name]}==expected


def test_whole_module_ast_retains_executable_field_description_strings(source_tree):
    before=_profiles()
    target=source_tree/'model/vessel.py';raw=target.read_text()
    changed=raw.replace('description="transverse free-surface inertia i_t, m^4 (FSM = ρ·i_t, kg·m)"',
                        'description="Reviewed wording only"')
    assert changed!=raw
    target.write_text(changed)
    after=_profiles()
    assert {name for name in before if after[name]!=before[name]}=={
        'geometry','fsm','fixed_tank','coefficient','calibration','liquid_reference','instance'}


@pytest.mark.parametrize('path,old,new,changed', [
    ('model/vessel.py','return self.x_m, self.y_m, self.z_m, self.mass_kg',
     'return self.x_m, self.y_m, self.z_m, 2*self.mass_kg',
     ('geometry','fsm','fixed_tank','coefficient','calibration','liquid_reference','instance')),
    ('model/vessel.py','fsm_m4: float = Field(ge=0.0,',
     'fsm_m4: float = Field(ge=0.1,', ('geometry','fsm','coefficient','calibration','liquid_reference','instance','fixed_tank')),
    ('model/base.py','PosFloat = Annotated[float, Field(gt=0.0)]',
     'PosFloat = Annotated[float, Field(gt=0.1)]', ('geometry','fsm','coefficient','calibration','liquid_reference','instance','fixed_tank')),
    ('checker/stability_loading.py','mass = sum(w[0] for w in weights)',
     'mass = 2*sum(w[0] for w in weights)', ('calibration','liquid_reference','instance')),
    ('checker/stability_liquid_batch.py','centre += np.einsum(',
     'centre += 2*np.einsum(', ('calibration','liquid_reference','instance')),
    ('conventions.py','return (bay - 1, bay + 1)',
     'return (bay - 3, bay + 1)', ('calibration','instance')),
])
def test_selected_numeric_data_and_computation_still_invalidate(source_tree,path,old,new,changed):
    before=_profiles()
    target=source_tree/path;raw=target.read_text()
    assert old in raw
    target.write_text(raw.replace(old,new))
    after=_profiles()
    assert {name for name in before if after[name]!=before[name]}==set(changed)


def test_uncertain_loading_modules_bind_whole_file_including_gz_bodies(source_tree):
    before=_profiles()
    for name,old,new in (
        ('checker/stability_loading.py','return condition.fsc_m*np.sin(angles)',
         'return 2*condition.fsc_m*np.sin(angles)'),
        ('checker/stability_liquid_batch.py','maximum = max(0., float(np.max(values[column])))',
         'maximum = 2*max(0., float(np.max(values[column])))')):
        path=source_tree/name;raw=path.read_text()
        assert old in raw
        path.write_text(raw.replace(old,new))
    after=_profiles()
    assert {name for name in before if after[name]!=before[name]}=={
        'liquid_reference','calibration','instance'}


@pytest.mark.parametrize('raw,symbols', [
    (b'class Row:\n    pass\n',('Row.mass_kg',)),
    (b'class Row:\n    mass_kg: float\n    mass_kg: float\n',('Row.mass_kg',)),
    (b'def numeric(): pass\ndef numeric(): pass\n',('numeric',)),
])
def test_selected_fields_and_functions_fail_closed_on_missing_or_ambiguous_paths(raw,symbols):
    with pytest.raises(AssertionError,match='ambiguous|missing|incomplete'):
        dependencies._semantic_hash(raw,symbols)


@pytest.mark.parametrize('path,old,new,changed', [
    ('linear/reusable.py',"_ARRAYS=('response_sf','response_bm'",
     "_ARRAYS=('response_bm','response_sf'", ('coefficient','calibration','instance')),
    ('linear/reusable.py','    response_sf: np.ndarray','    response_sf: tuple',
     ('coefficient','calibration','instance')),
    ('linear/reusable.py','const_mass=float(sum(fixed[:,0]))',
     'const_mass=2*float(sum(fixed[:,0]))', ('coefficient','calibration','instance')),
    ('linear/bands.py','if tokens else 2.591','if tokens else 2.592', ('calibration','instance')),
    ('linear/physics.py','def _geometry_from_params(params):',
     'def _geometry_from_params(params):\n    params=dict(params, numerical_test=1)',
     ('integration','fsm','coefficient','calibration','instance')),
    ('linear/family_calibration.py','snapshot=reference_inputs(parameters)',
     "snapshot=reference_inputs(parameters)\n    snapshot['method']=2",
     ('liquid_reference','calibration','instance')),
    ('checker/stability_liquid_tables.py','    _validate(table)\n    if not np.isclose',
     '    table.moments *= 2\n    _validate(table)\n    if not np.isclose', ('liquid_reference','calibration','instance')),
    ('checker/stability_loading.py','    precomputed_soundings: bool = False',
     '    new_metadata_slot: str = ""\n    precomputed_soundings: bool = False',
     ('liquid_reference','calibration','instance')),
])
def test_transitive_runtime_numeric_closure_and_positional_layout_are_bound(source_tree,path,old,new,changed):
    before=_profiles();target=source_tree/path;raw=target.read_text()
    assert old in raw
    target.write_text(raw.replace(old,new))
    after=_profiles()
    assert {name for name in before if after[name]!=before[name]}==set(changed)


def test_loading_class_defaults_invalidate_conservative_consumers(source_tree):
    before=_profiles();target=source_tree/'checker/stability_loading.py';raw=target.read_text()
    target.write_text(raw.replace('assumptions: tuple[str, ...] = ()',
                                 'assumptions: tuple[str, ...] = ("reviewed",)'))
    after=_profiles()
    assert {name for name in before if after[name]!=before[name]}=={
        'liquid_reference','calibration','instance'}

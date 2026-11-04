"""The rule gate must reject staged semantics and report every missing capability."""
from __future__ import annotations

import importlib.util
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def driver():
    spec=importlib.util.spec_from_file_location('test_static_migration',HERE/'migration_contract.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_staging_tree_refuses_with_exact_aggregated_missing_capabilities(activated_root):
    import json
    ids=json.loads((HERE/'REQUIRED_RULES.json').read_bytes())['required_rule_ids']
    path=activated_root/'src/stowage/rules/catalogue.toml'
    path.write_text(path.read_text().replace('rule_freeze_v1 = true','rule_freeze_v1 = false'))
    result=driver().static_contract_report(activated_root,ids)
    codes={issue['code'] for issue in result['issues']}
    assert result['passed'] is False
    assert {'activation.rule_freeze_v1_disabled','catalogue.pending_contract','catalogue.superseded_parameter',
            'semantics.weight_order_mode','semantics.weight_order_tolerance','semantics.legacy_weight_parameter','semantics.e6_not_soft','semantics.e9_top_only'} == codes
    assert result['issues']==sorted(result['issues'],key=lambda issue:(issue['code'],issue['detail']))


import json
import shutil
import pytest


@pytest.fixture
def activated_root(tmp_path):
    for relative in ('src/stowage/rules/catalogue.toml','src/stowage/versions.py',
                     'src/stowage/ports/profiles.py','params/cargo/feeder_export.toml'):
        target=tmp_path/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/relative,target)
    shutil.copytree(ROOT/'params/ports',tmp_path/'params/ports')
    catalogue=tmp_path/'src/stowage/rules/catalogue.toml'
    catalogue.write_text(catalogue.read_text().replace('rule_freeze_v1 = false','rule_freeze_v1 = true'))
    return tmp_path


def required_ids():
    return json.loads((HERE/'REQUIRED_RULES.json').read_bytes())['required_rule_ids']


def test_activated_effective_contract_passes_without_numerical_work(activated_root):
    result=driver().static_contract_report(activated_root,required_ids())
    assert result['passed'],result['issues']
    assert result['catalogue_loaded_sha256']=='sha256:'+result['catalogue_sha256']
    assert result['rule_freeze_v1_enabled'] is True
    assert result['ports']['profile_count']==7
    assert 'src/stowage/ports/profiles.py' in result['pins']


def test_a8_yard_and_equipment_limits_are_independent_of_quay_h(activated_root):
    profile=activated_root/'params/ports/laem_chabang_like.toml'
    content=profile.read_text()
    for key in ('max_crane_height_tiers','max_on_deck_tier_count'):
        old=key+' = { value = 8,'
        assert old in content
        content=content.replace(old,key+' = { value = 4,')
    profile.write_text(content)
    result=driver().static_contract_report(activated_root,required_ids())
    assert result['passed'],result['issues']  # independent yard5 / quay4 is legal


@pytest.mark.parametrize('fault,code',[
    ('weight_mode','semantics.weight_order_mode'),('gm','semantics.d64_parameter'),
    ('h_missing','ports.operational_h_missing'),('environment_h','ports.environment_crane_capability'),
    ('environment_consumed','calls.environment_crane_limit'),('unresolved_call','calls.rotation_profile_unresolved'),
    ('schema','schema.minimum_not_met'),
])
def test_static_gate_rejects_independent_missing_migration_capabilities(activated_root,fault,code):
    paths={
        'weight_mode':('src/stowage/rules/catalogue.toml','"weight_order_mode" = { "value" = "gross_mass_kg"','"weight_order_mode" = { "value" = "weight_class"'),
        'gm':('src/stowage/rules/catalogue.toml','"minimum_fluid_gm_m" = { "value" = 0.15','"minimum_fluid_gm_m" = { "value" = 0.16'),
        'h_missing':('params/ports/laem_chabang_like.toml','max_crane_height_tiers = { value = 8,','not_the_crane_height = { value = 8,'),
        'environment_h':('params/ports/cat_lai_like.toml','profile_kind = "next_call_environment"','profile_kind = "next_call_environment"\nmax_crane_height_tiers = {value=1}'),
        'environment_consumed':('src/stowage/ports/profiles.py','if profile is not None and profile.profile_kind == "next_call_environment":\n        return None','if profile is not None and profile.profile_kind == "next_call_environment":\n        return 1'),
        'unresolved_call':('params/cargo/feeder_export.toml','"MYPKG"','"ZZZZZ"'),
        'schema':('src/stowage/versions.py','SCHEMA_VERSION: Final = "1.9"','SCHEMA_VERSION: Final = "1.8"'),
    }
    relative,old,new=paths[fault];path=activated_root/relative
    content=path.read_text();assert old in content
    path.write_text(content.replace(old,new))
    result=driver().static_contract_report(activated_root,required_ids())
    assert not result['passed']
    assert code in {item['code'] for item in result['issues']},result['issues']


def test_migration_check_calls_loaded_catalogue_activation_api(activated_root,monkeypatch):
    import stowage.rules.catalogue as catalogue
    seen=[]
    def refused(*,catalogue):
        seen.append(catalogue)
        return False
    monkeypatch.setattr(catalogue,'rule_freeze_v1_enabled',refused)
    result=driver().static_contract_report(activated_root,required_ids())
    assert seen and seen[0].activation.rule_freeze_v1 is True
    assert 'activation.rule_freeze_v1_disabled' in {i['code'] for i in result['issues']}


def test_semantics_report_aggregates_pending_superseded_class_hard_top_only_and_d64():
    api=driver();issues=[]
    doc={'rules':[
        {'id':'expert.e1.heavy_bottom','pending_contract':{},'parameters':{'class_order':{'value':['L','M','H'],'unit':'classes','superseded_by':'kg'}}},
        {'id':'expert.e2.weight_order','parameters':{'weight_order_mode':{'value':'weight_class'}}},
        {'id':'expert.e6.heavy_under_deck','hardness':'hard'},
        {'id':'expert.e9.over_height','parameters':{'top_only':{'value':True}}},
        {'id':'port.stability.cargo_operations','parameters':{}},
    ]}
    api._catalogue_checks(doc,[r['id'] for r in doc['rules']],lambda c,d:issues.append({'code':c,'detail':d}))
    codes={i['code'] for i in issues}
    assert {'catalogue.pending_contract','catalogue.superseded_parameter','semantics.legacy_weight_parameter',
            'semantics.weight_order_mode','semantics.e6_not_soft','semantics.e9_top_only','semantics.d64_parameter'}<=codes


def test_bad_catalogue_does_not_hide_port_schema_and_later_call_blockers(activated_root):
    (activated_root/'src/stowage/rules/catalogue.toml').write_text('not valid TOML !')
    profile=activated_root/'params/ports/cat_lai_like.toml'
    # D74 adds a trailing [quay_crane] table. Keep the deliberately invalid
    # environment capability at the root rather than accidentally nesting it.
    profile.write_text('max_crane_height_tiers={value=1}\n'+profile.read_text())
    result=driver().static_contract_report(activated_root,required_ids())
    codes={i['code'] for i in result['issues']}
    assert {'catalogue.validation','activation.rule_freeze_v1_disabled','catalogue.required_rule_missing','ports.environment_crane_capability'}<=codes


def test_catalogue_loaded_identity_is_bound_to_same_canonical_read(activated_root,monkeypatch):
    from types import SimpleNamespace
    import stowage.rules.catalogue as catalogue
    original=catalogue._snapshot_from_content
    def mismatched(data):
        snapshot=original(data)
        return SimpleNamespace(catalogue=snapshot.catalogue,content_sha256='sha256:'+'0'*64,content=b'replacement')
    monkeypatch.setattr(catalogue,'_snapshot_from_content',mismatched)
    result=driver().static_contract_report(activated_root,required_ids())
    assert not result['passed']
    assert 'catalogue.loaded_identity' in {i['code'] for i in result['issues']}


def test_malformed_active_parameter_types_do_not_short_circuit_other_checks():
    api=driver();issues=[]
    doc={'rules':[{'id':'expert.e1.heavy_bottom','parameters':'bad'},
                  {'id':'port.stability.cargo_operations','parameters':{'max_list_rad':None}}]}
    api._catalogue_checks(doc,['expert.e1.heavy_bottom','expert.e2.weight_order'],lambda c,d:issues.append({'code':c,'detail':d}))
    assert {'catalogue.required_rule_missing','semantics.weight_order_mode','semantics.e6_not_soft','semantics.d64_profile_parameter'} <= {i['code'] for i in issues}

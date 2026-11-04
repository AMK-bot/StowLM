"""Aggregate static evidence for checklist 1–4 of rule-freeze-v1 migration.

This does no vessel construction, numerical checking or solver work. Behavioural
canaries, stored-gold replay and D62 exercise counts are separate runtime gates.
Every parser and the loaded catalogue derive from the same pinned byte snapshot.
"""
from __future__ import annotations

import ast
import __future__
import math
from pathlib import Path
from types import SimpleNamespace
import tomllib

from eval.snapshot_io import read_snapshot


D64_VALUES = {
    'intermediate_stability': True,
    'equilibrium_frequency': 'every_lift',
    'minimum_fluid_gm_m': .15,
    'maximum_trim_angle_rad': math.radians(1),
    'harbour_wave_bending_fraction': .5,
    'harbour_bending_mode': 'per_sign',
    'strength_sampling_events': ['hatch_cover_events','bay_completions','last_discharge','departure'],
    'strength_top_surrogate_count': 3,
    'shear_limit_mode': 'seagoing',
    'open_hatch_condition': 'immersion_angle_exceeds_max_list_rad',
    'maximum_draughts': ['berth','load_line'],
    'lower_draught_bound_applied': False,
    'ballast_changeover_mode': 'single_index_k',
    'report_ballast_changeover_index': True,
    'departure_full_seagoing_check': True,
}
D64_PROFILES = {'max_list_rad':'vessel.hull.max_list_rad',
                'length_between_perpendiculars_m':'vessel.hull.lpp_m'}


def _mapping(value):
    return value if isinstance(value,dict) else {}


def _parameters(rule):
    return _mapping(_mapping(rule).get('parameters'))


def _value(parameter):
    return parameter.get('value') if isinstance(parameter,dict) else parameter


def _same(actual, expected):
    if isinstance(expected,bool):return actual is expected
    if isinstance(expected,(float,int)):
        return not isinstance(actual,bool) and isinstance(actual,(float,int)) and math.isclose(actual,expected,rel_tol=1e-12,abs_tol=1e-12)
    return actual==expected


def _schema(tree):
    for node in ast.walk(tree):
        targets=node.targets if isinstance(node,ast.Assign) else [node.target] if isinstance(node,ast.AnnAssign) else []
        if any(isinstance(target,ast.Name) and target.id=='SCHEMA_VERSION' for target in targets):
            return ast.literal_eval(node.value)
    return None


def _catalogue_checks(document, required_ids, issue):
    rules={rule['id']:rule for rule in document.get('rules',[]) if isinstance(rule,dict) and isinstance(rule.get('id'),str)}
    for rule_id in sorted(set(required_ids)):
        rule=rules.get(rule_id)
        if rule is None:
            issue('catalogue.required_rule_missing',rule_id)
            continue
        if rule.get('pending_contract') is not None:
            issue('catalogue.pending_contract',f'{rule_id}: pending contract has not replaced active semantics')
        for name,parameter in sorted(_parameters(rule).items()):
            if isinstance(parameter,dict) and parameter.get('superseded_by'):
                issue('catalogue.superseded_parameter',f'{rule_id}.{name}: {parameter["superseded_by"]}')
    for rule_id in ('expert.e1.heavy_bottom','expert.e2.weight_order'):
        params=_parameters(rules.get(rule_id,{}))
        if _value(params.get('weight_order_mode'))!='gross_mass_kg':
            issue('semantics.weight_order_mode',f'{rule_id}: active weight_order_mode must equal gross_mass_kg')
        if not _same(_value(params.get('weight_order_tolerance_kg')),0):
            issue('semantics.weight_order_tolerance',f'{rule_id}: active weight_order_tolerance_kg must equal 0 kg')
        legacy={'order_mode','class_order','tolerance','medium_min_kg','heavy_min_kg','heavy_threshold'}
        for name,parameter in sorted(params.items()):
            if (name in legacy or 'class' in name or (isinstance(parameter,dict) and parameter.get('unit')=='classes')):
                issue('semantics.legacy_weight_parameter',f'{rule_id}.{name}: legacy class/order-mode parameter is still active')
    if rules.get('expert.e6.heavy_under_deck',{}).get('hardness')!='soft':
        issue('semantics.e6_not_soft','expert.e6.heavy_under_deck: active hardness must equal soft')
    for rule_id in ('expert.e9.over_height','expert.e9.over_width_length'):
        if 'top_only' in _parameters(rules.get(rule_id,{})):
            issue('semantics.e9_top_only',f'{rule_id}: obsolete top_only parameter remains active')
    d64=rules.get('port.stability.cargo_operations',{})
    params=_parameters(d64)
    for name,expected in D64_VALUES.items():
        actual=_value(params.get(name))
        if not _same(actual,expected):
            issue('semantics.d64_parameter',f'port.stability.cargo_operations.{name}: expected {expected!r}, observed {actual!r}')
    for name,expected in D64_PROFILES.items():
        actual=_mapping(params.get(name)).get('profile_field')
        if actual!=expected:
            issue('semantics.d64_profile_parameter',f'port.stability.cargo_operations.{name}: expected profile_field {expected!r}, observed {actual!r}')
    if not any('D64' in ref for ref in d64.get('references',[])):
        issue('semantics.d64_reference','port.stability.cargo_operations: D64 reference is absent')
    if 'rule_freeze_v1' not in str(_value(params.get('activation_path'))):
        issue('semantics.d64_activation_path','port.stability.cargo_operations: shared rule_freeze_v1 activation route is absent')


def _port_checks(root, read, issue):
    profile_tree=read('src/stowage/ports/profiles.py','ast')
    default_locodes={}
    if profile_tree is not None:
        for node in profile_tree.body:
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_DEFAULT_LOCODES' for t in node.targets):
                try:
                    default_locodes=ast.literal_eval(node.value)
                    if not isinstance(default_locodes,dict):
                        raise ValueError('expected a dictionary')
                except (ValueError,TypeError):issue('ports.locode_map','_DEFAULT_LOCODES is not a literal archetype/LOCODE mapping')
    profiles={}
    paths=sorted((root/'params/ports').glob('*.toml'))
    if not paths:issue('ports.profiles_missing','no port profile parameter files are present')
    for path in paths:
        document=read(str(path.relative_to(root)),'toml')
        if document is None:continue
        name=document.get('id',path.stem)
        if not isinstance(name,str) or not name:
            issue('ports.profile_id',f'{path.stem}: invalid profile id {name!r}')
            name=path.stem
        kind=document.get('profile_kind','operational')
        height=_value(document.get('max_crane_height_tiers'))
        quay=_value(_mapping(document.get('quay_crane')).get('max_on_deck_tier_count'))
        yard=_value(_mapping(document.get('yard')).get('max_stack_tiers'))
        equipment=_value(_mapping(document.get('equipment')).get('stack_height_tiers'))
        if kind=='next_call_environment':
            if any(v is not None for v in (height,quay,yard,equipment)):
                issue('ports.environment_crane_capability',f'{name}: environment-only profile must not declare crane or yard limits')
        elif kind=='operational':
            if isinstance(height,bool) or not isinstance(height,int) or height<1:
                issue('ports.operational_h_missing',f'{name}: operational max_crane_height_tiers must be an integer >=1')
            else:
                if quay!=height:issue('ports.quay_h_mismatch',f'{name}: quay max_on_deck_tier_count {quay!r} differs from H={height}')
                for label,value in (('yard.max_stack_tiers',yard),('equipment.stack_height_tiers',equipment)):
                    if isinstance(value,bool) or not isinstance(value,int) or value<1:
                        issue('ports.storage_limit',f'{name}.{label}: must be an integer >=1 independently of quay H, observed {value!r}')
        else:issue('ports.profile_kind',f'{name}: unknown profile_kind {kind!r}')
        locode=document.get('locode') or default_locodes.get(name)
        if not locode:issue('ports.locode_missing',f'{name}: no operational/default or declared LOCODE')
        profiles[name]=SimpleNamespace(archetype=name,locode=locode,profile_kind=kind,max_crane_height_tiers=height,
            quay_crane=SimpleNamespace(max_on_deck_tiers=quay) if quay is not None else None)
    # Execute only the two metadata-lookup functions from their already-pinned
    # AST, with isolated in-memory profile loaders. No generator/model/vessel or
    # numerical functions are imported or invoked; source/lookup drift is pinned.
    wanted={'resolve_port_call_profile','crane_height_at_call'}
    definitions=[] if profile_tree is None else [n for n in profile_tree.body if isinstance(n,ast.FunctionDef) and n.name in wanted]
    names={n.name for n in definitions}
    for name in sorted(wanted-names):issue('calls.resolver_missing',f'{name}: required public later-call metadata API is absent')
    api={}
    if names==wanted:
        namespace={'load_port':lambda name:profiles[name],'list_ports':lambda:list(sorted(profiles))}
        try:
            code=compile(ast.Module(body=definitions,type_ignores=[]),str(root/'src/stowage/ports/profiles.py'),'exec',flags=__future__.annotations.compiler_flag)
            exec(code,namespace)
            api={name:namespace[name] for name in wanted}
        except Exception as exc:issue('calls.resolver_execution',f'cannot prepare isolated metadata resolver: {type(exc).__name__}: {exc}')
    cargo=read('params/cargo/feeder_export.toml','toml')
    locodes=sorted({locode for route in (cargo or {}).get('rotations',[]) for locode in route.get('port_calls',[])})
    for locode in locodes:
        matches=[p for p in profiles.values() if p.locode==locode]
        if len(matches)!=1:
            issue('calls.rotation_profile_unresolved',f'{locode}: expected one archetype profile, found {len(matches)}')
    if api:
        for name,profile in sorted(profiles.items()):
            for archetype in (name,None):
                call=SimpleNamespace(archetype=archetype,locode=profile.locode,max_on_deck_tiers=99)
                try:
                    resolved=api['resolve_port_call_profile'](call)
                    if resolved is None or resolved.archetype!=name:
                        issue('calls.profile_resolution',f'{name}: explicit/LOCODE lookup does not resolve its archetype')
                    cap=api['crane_height_at_call'](call)
                    if profile.profile_kind=='next_call_environment' and cap is not None:
                        issue('calls.environment_crane_limit',f'{name}: crane consumer returned {cap!r} for environment-only profile')
                    elif profile.profile_kind=='operational' and cap!=profile.max_crane_height_tiers:
                        issue('calls.operational_crane_limit',f'{name}: later-call crane limit {cap!r} differs from H={profile.max_crane_height_tiers!r}')
                except Exception as exc:issue('calls.resolver_execution',f'{name}: {type(exc).__name__}: {exc}')
    return dict(profile_count=len(profiles),rotation_locodes=locodes)


def static_contract_report(root, required_ids):
    """Return every static blocker, sorted deterministically, without short-circuit."""
    root=Path(root).resolve();issues=[];pins={}
    def issue(code,detail):issues.append(dict(code=code,detail=detail))
    def read(relative,kind):
        try:
            snapshot=read_snapshot(root/relative);pins[relative]=snapshot.sha256
            if kind=='toml':return tomllib.loads(snapshot.data.decode('utf-8'))
            if kind=='ast':return ast.parse(snapshot.data.decode('utf-8'))
            return snapshot
        except Exception as exc:
            issue('input.unavailable',f'{relative}: {type(exc).__name__}: {exc}')
            return None
    source=read('src/stowage/rules/catalogue.toml','bytes');document={};loaded_sha=None;enabled=False
    if source is not None:
        try:
            document=tomllib.loads(source.data.decode('utf-8'))
            from stowage.rules.catalogue import _snapshot_from_content,rule_freeze_v1_enabled
            snapshot=_snapshot_from_content(source.data)
            loaded_sha=snapshot.content_sha256
            if loaded_sha!='sha256:'+source.sha256 or snapshot.content!=source.data:
                issue('catalogue.loaded_identity','loaded catalogue identity differs from its parsed byte snapshot')
            enabled=rule_freeze_v1_enabled(catalogue=snapshot.catalogue)
            document=snapshot.catalogue.model_dump(mode='json')
        except Exception as exc:issue('catalogue.validation',f'{type(exc).__name__}: {exc}')
    if enabled is not True:
        issue('activation.rule_freeze_v1_disabled','rule_freeze_v1_enabled(catalogue=loaded_snapshot) must be True; L1 staging is not migration-complete')
    _catalogue_checks(document,required_ids,issue)
    version_tree=read('src/stowage/versions.py','ast');schema=None
    if version_tree is not None:
        try:
            schema=_schema(version_tree)
            if not isinstance(schema,str) or tuple(int(p) for p in schema.split('.'))<(1,9):
                issue('schema.minimum_not_met',f'operational model schema must be >=1.9, observed {schema!r}')
        except (ValueError,TypeError) as exc:issue('schema.minimum_not_met',str(exc))
    ports=_port_checks(root,read,issue)
    unique={(i['code'],i['detail']):i for i in issues}
    issues=[unique[key] for key in sorted(unique)]
    return dict(passed=not issues,issues=issues,pins=dict(sorted(pins.items())),
        catalogue_sha256=source.sha256 if source is not None else None,
        catalogue_loaded_sha256=loaded_sha,schema_version=schema,rule_freeze_v1_enabled=enabled,ports=ports)

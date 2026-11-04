"""D30 numerical dependency identities owned by the linear builders.

An unrelated checker/ballast release does not change a hull response. Each
artifact names its numerical implementation dependencies explicitly; global
component versions, rule inventories, warm-up orchestration and display
metadata do not enter these identities. Actual parameters and qualified table
contents are supplied separately by the cache caller.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from functools import lru_cache
import symtable

LINEAR_GEOMETRY_VERSION='1.0'
LINEAR_INTEGRATION_VERSION='1.0'
LINEAR_FSM_VERSION='1.0'
LINEAR_FIXED_TANK_INTEGRALS_VERSION='1.0'
LINEAR_COEFFICIENTS_VERSION='1.0'
LINEAR_CALIBRATION_VERSION='1.1'
LINEAR_INSTANCE_VERSION='1.2'
LINEAR_LIQUID_REFERENCE_VERSION='1.0'

_ROOT=Path(__file__).resolve().parents[1]


def _members(class_name, *names):
    return tuple(f'{class_name}.{name}' for name in names)


# Data-model classes are boundaries, not numerical implementations as a whole.
# Bind the fields/defaults/types read or produced by the numerical consumers,
# and the executable adapters they call. Evidence, voyage-policy and display
# additions must not invalidate geometry or a measured response family. Actual
# admitted field values independently enter the cache's numerical projection;
# the full evidence identity is still checked separately on instance reuse.
_NUMERIC_TYPES=('Kg','Metres','PosMetres','PosFloat')
_HYDRO=_members('HydrostaticRow','draught_m','displacement_kg','kb_m','kmt_m',
    'lcb_m','lcf_m','tpc_kg_per_cm','mct_nm_per_cm')
_SOUNDING=_members('TankSoundingPoint','volume_m3','lcg_m','tcg_m','vcg_m','fsm_m4')
_FIXED_LOAD=_members('FixedLoad','x_m','y_m','z_m','mass_kg','legacy_point_mass',
    'as_point_mass','__iter__','__getitem__','__len__','__lt__')
_BAND=_members('ValidityBand','displacement_min_kg','displacement_max_kg','draught_min_m',
    'draught_max_m','max_abs_trim_m','max_abs_heel_rad','_tank_mass_bounds_kg','_cargo_mass_kg')
_ERRORS=_members('LinearErrors','gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm')
_MODEL=_members('LinearModel','vessel_id','version','water_density_kg_m3','band','errors',
    'cell_ids','tank_ids','station_x_m','cell_lcg_m','cell_tcg_m','cell_vcg_m',
    'tank_lcg_m','tank_tcg_m','tank_vcg_m','tank_capacity_kg','tank_fsm_max_kgm','tank_adjustable',
    'sf_per_kg_cell','bm_per_kg_cell','sf_per_kg_tank','bm_per_kg_tank','const_mass_kg',
    'const_moment_x_kgm','const_moment_y_kgm','const_moment_z_kgm','const_sf_n','const_bm_nm',
    'km_line','trim_line','draught_line','gm_min_m','max_trim_m','max_list_rad',
    'sf_max_n','sf_min_n','bm_max_nm','bm_min_nm','hull_length_m','hull_max_draught_m',
    'hull_min_draught_fwd_m','_fixed_tank_masses','_reference_params','_cargo_mass_kg',
    '_tank_mass_bounds_kg','_nominal_height_m','_constraint_cell_vcg_m',
    '_family_response_sf','_family_response_bm','_family_buoyancy_sf','_family_buoyancy_bm',
    '_family_lightship_sf','_family_lightship_bm')
_SLOT_CONVENTIONS=('ON_DECK_TIER_MIN','_SLOT_RE','parse_slot','format_slot','odd_bays_of',
    'cells_of_slot',*_members('SlotAddress','bay','row','tier','__post_init__'))
_LOADING=('CONSUMABLE_KINDS','_interpolate','loading_condition','liquid_centroid',
    'liquid_cg','_upright','_centroid',
    *_members('CorrectionTank','id','density_kg_m3','volume_m3','fsm_kgm','geometry','variable'),
    *_members('LoadingCondition','mass_kg','lcg_m','tcg_m','kg_m','fsc_m','fsm_kgm',
        'tanks','actual_tanks','errors','precomputed_soundings','liquid_tables',
        'liquid_usage','upright_cache','cg_cache','liquid_cg_groups'))
_FAMILY=('family_band','coefficient_family','_ARRAYS',
    *_members('CoefficientFamily','template','response_sf','response_bm','buoyancy_sf',
        'buoyancy_bm','lightship_sf','lightship_bm','parameters','cache_key'))
_PERF_FLAGS=('_ENABLED','reuse_enabled')
_GEOMETRY={'vessel/hydrostatics.py':None,'vessel/hull_offsets.py':None,
           # Full hydrostatic integration can now dispatch through numerical
           # reuse. Bind its live flag provider as well as both formula paths.
           'checker/perf.py':_PERF_FLAGS,
           'model/vessel.py':_HYDRO,'model/base.py':_NUMERIC_TYPES}
_TANKS={**_GEOMETRY,'vessel/tanks.py':None,'vessel/hull_tanks.py':None,
        'vessel/hull_tank_sections.py':None,'model/vessel.py':(*_HYDRO,*_SOUNDING)}
_COEFFICIENTS={**_TANKS,'linear/reusable.py':_FAMILY,
    'linear/physics.py':('_km_support_lower_intercept','_polygon_upright_polynomials',
        '_maximum_tank_fsms','_tank_point','_integration_matrices','_geometry_from_params','G'),
    'linear/tank_fsm.py':None,'linear/tank_weights.py':None,
    'linear/model.py':(*_MODEL,*_ERRORS,*_BAND)}
_LIQUID={**_TANKS,'checker/stability_loading.py':_LOADING,'checker/stability_sounding.py':None,
    # The stencil wrapper delegates its arithmetic and exact scalar/type
    # reconstruction here. Keep the complete helper, including cache guards,
    # rather than hashing only the former public function's new dispatch body.
    'checker/stability_reuse.py':None,
    'checker/stability_tank_envelope.py':None,
    # The current loading centroid delegates its direct fallback here. The
    # finite-angle max_transfer envelope remains outside initial metrics.
    'checker/stability_tank_transfer.py':('liquid_centroid','_direct_liquid',
        '_polynomial_liquid','_section_polynomials'),
    'checker/stability_liquid.py':('CACHE_DIRECTORY','STABILITY_LIQUID_MANIFEST_VERSION',
        '_physics_inputs','metadata','manifest_path','_manifest_path','_TABLE_IDENTITIES',
        '_table_identity','_read_manifest','_valid_statistics','attach_tables'),
    'checker/stability_liquid_tables.py':('_stencil','_stencil_reference',
        '_stencil_dependencies','_ORIGINAL_STENCIL_DEPENDENCIES','LiquidTable.centroid',
        'CACHE_DIRECTORY','STABILITY_LIQUID_TABLES_VERSION','DEFAULT_FILLS','DEFAULT_HEELS',
        'DEFAULT_TRIMS','_provenance','_grid_spec','_base_identity','_identity','_path',
        'lookup','_lookup','_read','_validate',
        *_members('LiquidTable','fills','heels','trims','moments','capacity',
            'empty_centroid','prismatic','_angular_cache','_fill_cache')),
    'checker/stability_liquid_batch.py':('prepare','evaluate',
        *_members('CGGroup','heels','trims','shift_grid','prismatic')),
    # Loading uses this bounded fallback when no qualified liquid grid covers
    # the query. Its actual centroid roots therefore belong to the reference.
    'checker/stability_direct_liquid_batch.py':None,
    'linear/family_calibration.py':('_reference_solids','_reference_paths','reference_cache_identity',
        '_liquid_numeric_digest','reference_inputs','synchronize_reference','reference_inputs_cached',
        '_prepared_reference_paths','_reference_manifest_path','_DEFAULT_REFERENCE_PATHS')}
_CALIBRATION={**_COEFFICIENTS,**_LIQUID,'checker/stability_geometry.py':(
    '_contour_cycle','_bilateral_sections',*_members('StabilityGeometry',
        '_STATE_CACHE_LIMIT','_FULL_STATE_CACHE_LIMIT','__init__','_prepare_edges',
        '_state_key','_copy_state','_store_state','submerged','_submerged_many',
        '_submerged_integrals','_integral_result','full_submerged')),
    'checker/strength.py':('StrengthCurves','_integrate_segments','integrate_strength','_tank_segments','_tank_liquid','_section_tank_point_loads','G'),
    'model/vessel.py':(*_HYDRO,*_SOUNDING,*_FIXED_LOAD),
    'linear/reference_equilibrium.py':None,
    # exact_condition creates an empty-placement ShipState. Its actual tank
    # masses are explicit; placement/cover validators and voyage evidence are
    # not part of this synthetic reference's numerical calculation.
    'linear/signed_errors.py':None,'model/ship.py':_members('ShipState','vessel_id',
        'placements','tank_masses_kg','covers_off'),
    'conventions.py':_SLOT_CONVENTIONS,
    # Loading and distributed strength now call the scoped slot adapters. Their
    # original convention implementations above remain numerical dependencies.
    'checker/slots.py':None,
    'checker/perf.py':(*_PERF_FLAGS,'_CACHE','current_cache'),
    'linear/family_calibration.py':(*_LIQUID['linear/family_calibration.py'],'_parent_domain_bounds','domain_for','calibrate_family'),
    'linear/reusable.py':(*_FAMILY,'assemble_instance','_fixed_tank_integrals'),
    'linear/bands.py':('departure_manifest','nominal_region_heights','nominal_height','box_height'),
    'linear/physics.py':(*_COEFFICIENTS['linear/physics.py'],'exact_condition','_linear_arrays','_fixed','_weight_integrals'),
    'linear/operations.py':('actual_fsm_kgm',)}
_PROFILES={'geometry':_GEOMETRY,'integration':{'linear/physics.py':('_integration_matrices',)},
    'fsm':{**_TANKS,'linear/physics.py':('_polygon_upright_polynomials',),'linear/tank_fsm.py':None},
    'fixed_tank':{**_TANKS,'linear/tank_weights.py':None},
    'coefficient':_COEFFICIENTS,'calibration':_CALIBRATION,'liquid_reference':_LIQUID,
    'instance':{**_CALIBRATION,
        'linear/bands.py':None,'linear/upright_mass.py':None,
        'linear/operations.py':('actual_fsm_kgm','departure_manifest_identity',
            'public_loading_policy','vessel_physics_identity','_LOADING_FIELDS',
            '_LOADING_RULES','_field','_loading_number','_validated_loading_policy')}}
_VERSION_NAMES={'geometry':('LINEAR_GEOMETRY_VERSION',),'integration':('LINEAR_INTEGRATION_VERSION',),
    'fsm':('LINEAR_FSM_VERSION',),'coefficient':('LINEAR_GEOMETRY_VERSION','LINEAR_INTEGRATION_VERSION','LINEAR_FSM_VERSION','LINEAR_COEFFICIENTS_VERSION'),
    'fixed_tank':('LINEAR_FIXED_TANK_INTEGRALS_VERSION',),
    'calibration':('LINEAR_GEOMETRY_VERSION','LINEAR_INTEGRATION_VERSION','LINEAR_FSM_VERSION','LINEAR_FIXED_TANK_INTEGRALS_VERSION','LINEAR_COEFFICIENTS_VERSION','LINEAR_CALIBRATION_VERSION','LINEAR_LIQUID_REFERENCE_VERSION'),
    'liquid_reference':('LINEAR_LIQUID_REFERENCE_VERSION',),
    'instance':('LINEAR_GEOMETRY_VERSION','LINEAR_INTEGRATION_VERSION','LINEAR_FSM_VERSION','LINEAR_FIXED_TANK_INTEGRALS_VERSION','LINEAR_COEFFICIENTS_VERSION','LINEAR_CALIBRATION_VERSION','LINEAR_INSTANCE_VERSION','LINEAR_LIQUID_REFERENCE_VERSION')}


class _NumericalAST(ast.NodeTransformer):
    def visit_Call(self,node):
        self.generic_visit(node)
        # Pydantic field prose does not affect a numeric default, coercion or
        # validation. Retain every other keyword, including bounds/factories.
        if isinstance(node.func,ast.Name) and node.func.id=='Field':
            node.keywords=[keyword for keyword in node.keywords
                if keyword.arg not in ('title','description','examples','json_schema_extra')]
        return node

    def _body(self,node):
        self.generic_visit(node)
        if node.body and isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant) and isinstance(node.body[0].value.value,str):
            node.body=node.body[1:]
        return node
    visit_Module=_body
    visit_ClassDef=_body
    visit_FunctionDef=_body
    visit_AsyncFunctionDef=_body


def builder_versions(artifact):
    return {name:globals()[name] for name in _VERSION_NAMES[artifact]}


def _global_references(nodes):
    """Resolve lexical globals, excluding parameters, locals and closure cells."""
    tree=ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[]))
    table=symtable.symtable(ast.unparse(tree),
        '<numerical dependencies>','exec')
    def references(scope):
        result={symbol.get_name() for symbol in scope.get_symbols()
            if symbol.is_referenced() and symbol.is_global()}
        for child in scope.get_children():
            result.update(references(child))
        return result
    return references(table)


def _import_closure(module,chosen):
    """Retain the actual import providers reached by selected declarations.

    Follow same-module aliases/helpers too: ``provider = sin`` must not hide
    sin's import, or changes to that alias assignment. Partially selected
    classes remain boundaries, so omitted evidence fields stay excluded.
    """
    def bound_names(node):
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
            return {node.name}
        if isinstance(node,(ast.Assign,ast.AnnAssign)):
            targets=node.targets if isinstance(node,ast.Assign) else [node.target]
            return {value.id for target in targets for value in ast.walk(target)
                if isinstance(value,ast.Name) and isinstance(value.ctx,ast.Store)}
        return set()
    bindings={}
    for node in module.body:
        for name in bound_names(node):
            bindings.setdefault(name,[]).append(node)
    # Prefer the synthesized selected class over its original complete body.
    selected={name:node for node in chosen for name in bound_names(node)}
    pending=_global_references(chosen)
    seen=set();included={id(node) for node in chosen};extra=[]
    while pending:
        name=pending.pop()
        if name in seen:
            continue
        seen.add(name)
        declarations=[selected[name]] if name in selected else bindings.get(name,[])
        for node in declarations:
            if id(node) not in included:
                included.add(id(node));extra.append(node)
                pending.update(_global_references([node]))
    imports=[]
    for node in module.body:
        if isinstance(node,ast.Import):
            aliases=[alias for alias in node.names if (alias.asname or alias.name.split('.')[0]) in seen]
            if aliases:
                imports.append(ast.Import(names=aliases))
        elif isinstance(node,ast.ImportFrom):
            # A star binding cannot be resolved statically. Future directives
            # affect evaluation semantics even without an explicit name load.
            aliases=[alias for alias in node.names if alias.name=='*'
                or node.module=='__future__' or (alias.asname or alias.name) in seen]
            if aliases:
                imports.append(ast.ImportFrom(module=node.module,names=aliases,level=node.level))
    # Preserve source order for additional declarations, including rebindings.
    extra.sort(key=lambda node:node.lineno)
    return [*imports,*chosen,*extra]


@lru_cache(maxsize=256)
def _semantic_hash(raw,symbols):
    module=ast.parse(raw)
    if symbols is not None:
        def names(node):
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                return {node.name}
            if isinstance(node,ast.Assign):
                return {target.id for target in node.targets if isinstance(target,ast.Name)}
            if isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name):
                return {node.target.id}
            return set()
        chosen=[];selected_classes={}
        for symbol in symbols:
            if '.' not in symbol:
                matches=[node for node in module.body if symbol in names(node)]
                assert len(matches)==1,f'ambiguous or missing numerical dependency: {symbol}'
                chosen.append(matches[0])
                continue
            parts=symbol.split('.')
            assert len(parts)==2,f'unsupported numerical member selector: {symbol}'
            classes=[node for node in module.body if isinstance(node,ast.ClassDef) and node.name==parts[0]]
            assert len(classes)==1,f'ambiguous or missing numerical class: {symbol}'
            members=[node for node in classes[0].body if parts[1] in names(node)]
            assert len(members)==1,f'ambiguous or missing numerical method/field: {symbol}'
            selected_classes.setdefault(parts[0],(classes[0],[]))[1].append(members[0])
        for name,(original,members) in selected_classes.items():
            assert name not in symbols,f'ambiguous whole-class and member selection: {name}'
            # Preserve the selected declarations' actual order, class bases and
            # decorators (e.g. dataclass defaults), excluding unrelated fields.
            ordered=[node for node in original.body if node in members]
            dataclass=any((isinstance(decorator,ast.Name) and decorator.id=='dataclass')
                or (isinstance(decorator,ast.Call) and isinstance(decorator.func,ast.Name)
                    and decorator.func.id=='dataclass') for decorator in original.decorator_list)
            if dataclass:
                # Omitted diagnostic fields still occupy positional constructor
                # slots. Bind selected numerical fields' ordinal positions, not
                # the omitted fields' names/prose/defaults. Appended metadata is
                # inert, but inserting it before a numeric positional argument
                # must invalidate the caller's cached calculation.
                def positional(node):
                    return (isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name)
                        and not (isinstance(node.value,ast.Call)
                            and isinstance(node.value.func,ast.Name) and node.value.func.id=='field'
                            and any(keyword.arg=='kw_only' and isinstance(keyword.value,ast.Constant)
                                and keyword.value.value is True for keyword in node.value.keywords)))
                positions=tuple((node.target.id,index) for index,node in enumerate(
                    node for node in original.body if positional(node)) if node in members)
                ordered=[*ordered,ast.Assign(targets=[ast.Name(id='__numerical_field_positions__',
                    ctx=ast.Store())],value=ast.Constant(value=positions))]
            chosen.append(ast.ClassDef(name=name,bases=original.bases,keywords=original.keywords,
                body=ordered,decorator_list=original.decorator_list,type_params=original.type_params))
        # Drop evidence-only Field keywords before tracing their names, too.
        chosen=[_NumericalAST().visit(node) for node in chosen]
        module=ast.Module(body=_import_closure(module,chosen),type_ignores=[])
    normalized=_NumericalAST().visit(module)
    return hashlib.sha256(ast.dump(normalized,include_attributes=False).encode()).hexdigest()


def dependency_contents(artifact):
    """Fresh semantic source hashes; comments/docstrings are not numerics."""
    result={}
    for name,symbols in _PROFILES[artifact].items():
        result[name]=_semantic_hash((_ROOT/name).read_bytes(),symbols)
    return result


def dependency_fingerprint(artifact):
    return hashlib.sha256(json.dumps({'builder_versions':builder_versions(artifact),
        'numerical_sources':dependency_contents(artifact)},sort_keys=True).encode()).hexdigest()


def cache_artifact(kind,inputs=None):
    if kind=='fixed_tank_integrals':return 'fixed_tank'
    if kind=='coefficient_family':return 'coefficient'
    if kind=='family_calibration':return 'calibration'
    if kind=='model':return 'instance'
    label=(inputs or {}).get('kind','') if isinstance(inputs,dict) else ''
    if label=='HullGeometry':return 'geometry'
    if label=='Gauss-panel force/moment integration':return 'integration'
    if 'maximum slack FSM' in label:return 'fsm'
    return 'coefficient'

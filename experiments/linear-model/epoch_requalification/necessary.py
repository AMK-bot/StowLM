"""D49 independent outer-envelope qualification from unchanged epoch evidence.

Only archived opposite residuals and signed boundary observations are used.
The full interior population is included without an affine-GM eligibility
filter. Passing finite holdouts remains empirical, never a domain-wide proof.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

import numpy as np

SIDES = ("lower_unsafe", "upper_unsafe")
EDGE_KIND = 'necessary-domain-edges-v1'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def _checked_pin(promote, record):
    if not isinstance(record, dict) or not {'path', 'sha256'} <= set(record):
        raise ValueError('necessary edge evidence requires path/checksum pins')
    path = Path(record['path'])
    path = path if path.is_absolute() else promote.ROOT/path
    if promote.sha(path) != record['sha256']:
        raise ValueError('necessary edge evidence checksum mismatch')
    return path


def edge_population(record, raw, template, *, transfer):
    """Recompute every signed envelope from saved physical edge observations.

    Operational GM/strength/end-draught failures do not exclude a state. The
    family exact-attitude and dry-point centroid domain alone admits it. This
    prevents the affine predictor under test from filtering its own errors.
    """
    from stowage.linear import margin_policy as policy
    count = len(template.station_x_m)
    if (record.get('complete') is not True or record.get('kind') != EDGE_KIND
            or record.get('physical_inputs_unchanged') is not True
            or record.get('physical_inputs_start') != raw['physical_inputs']
            or record.get('physical_inputs_end') != raw['physical_inputs']):
        raise ValueError('necessary edge population is incomplete or has a stale physical epoch')
    if not np.array_equal(record.get('station_x_m'), template.station_x_m):
        raise ValueError('necessary edge station order differs from numerical model')
    calibration = record.get('calibration', {})
    for name in ('family_calibration_cache_key','response_calibration_identity','reference_identity','family_domain'):
        if calibration.get(name) != raw['diagnostics'].get(name):
            raise ValueError('necessary edge calibration mismatch: '+name)
    states = record.get('states')
    if not isinstance(states, list) or not states:
        raise ValueError('necessary edge population has no saved states')
    domain, band = raw['diagnostics']['family_domain'], template.band
    signed = policy._zeros(count)
    signed.update({side.replace('unsafe','conservatism'): deepcopy(values)
                   for side,values in tuple(signed.items())})
    admitted, identities = [], []
    seen = set()
    for state in states:
        if not isinstance(state, dict) or type(state.get('admitted')) is not bool:
            raise ValueError('necessary edge state requires explicit physical-domain admission')
        identity = state.get('state_id')
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError('necessary edge state identity missing or duplicated')
        seen.add(identity)
        if 'exact_draught_m' not in state:
            if state['admitted'] or not state.get('rejection_reason'):
                raise ValueError('necessary unevaluated edge state lacks a rejection reason')
            continue
        # Never trust an admission flag derived from an LP or operational GM.
        point = state['fixed_point']
        mass, draught, trim, heel = (float(state[name]) for name in
            ('mass_kg','exact_draught_m','exact_trim_m','exact_list_rad'))
        xyz = [float(point[name]) for name in ('x_m','y_m','z_m')]
        if not np.isfinite([mass,draught,trim,heel,*xyz,float(point['mass_kg'])]).all() or point['mass_kg'] <= 0:
            raise ValueError('nonfinite necessary edge physical state')
        in_domain = (band.displacement_min_kg-1e-6 <= mass <= band.displacement_max_kg+1e-6
            and band.draught_min_m-1e-7 <= draught <= band.draught_max_m+1e-7
            and abs(trim) <= band.max_abs_trim_m+1e-7
            and abs(heel) <= band.max_abs_heel_rad+1e-7
            and domain['fixed_x_min_m']-1e-7 <= xyz[0] <= domain['fixed_x_max_m']+1e-7
            and abs(xyz[1]) <= domain['fixed_y_abs_max_m']+1e-7
            and domain['kg_min_m']-1e-7 <= xyz[2] <= domain['kg_max_m']+1e-7)
        if state['admitted'] != bool(in_domain):
            raise ValueError('necessary edge physical-domain admission disagrees with exact state')
        if not in_domain:
            continue
        exact, affine = {}, {}
        for metric in ('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm'):
            shape = (count,) if metric in ('sf_n','bm_nm') else ()
            for prefix,target in (('exact_',exact),('affine_',affine)):
                value = np.asarray(state[prefix+metric], dtype=float)
                if value.shape != shape or not np.isfinite(value).all():
                    raise ValueError('invalid necessary edge quantity: '+prefix+metric)
                target[metric] = value
        for prefix,target in (('exact_',exact),('affine_',affine)):
            target['draught_aft_m'] = target['draught_m']+.5*target['trim_m']
            target['draught_forward_m'] = target['draught_m']-.5*target['trim_m']
        for metric in policy._METRICS:
            residual = affine[metric]-exact[metric]
            for side,sign in (('lower_unsafe',1.),('upper_unsafe',-1.),
                              ('lower_conservatism',-1.),('upper_conservatism',1.)):
                signed[side][metric] = np.maximum(signed[side][metric], np.maximum(sign*residual,0.)).tolist()
        admitted.append(state)
        identities.append(_digest({'point':point,'tanks':state.get('all_tank_masses_kg',state['tank_masses_kg'])}))
    if (type(record.get('samples')) is not int or record['samples'] != len(admitted)
            or not admitted or record.get('attempts') != len(states)):
        raise ValueError('necessary edge retained/attempted population counts disagree')
    strata = record.get('coverage', {}).get('strata')
    if not isinstance(strata, dict) or not strata:
        raise ValueError('necessary edge coverage strata are missing')
    required = record.get('coverage', {}).get('required_strata')
    if (not isinstance(required,list) or not required or len(set(required)) != len(required)
            or set(required) != set(strata)):
        raise ValueError('necessary edge coverage omitted a requested stratum')
    for stats in strata.values():
        if (not isinstance(stats, dict) or any(type(stats.get(name)) is not int
                for name in ('requested','evaluated','retained'))
                or stats['requested'] <= 0 or stats['evaluated'] != stats['requested']
                or not 0 <= stats['retained'] <= stats['evaluated']):
            raise ValueError('necessary edge coverage is incomplete')
    return dict(seed=record['seed'], samples=len(admitted), diagnostics={'signed_errors':signed},
        physical_inputs_start=record['physical_inputs_start'],list_row_samples=admitted,
        _edge_reference_optimistic=transfer,_edge_sample_ids=identities)


def load_edge_record(promote, path, raw, template, *, phase, role, _ancestors=()):
    """Validate one immutable observation and its explicit TRAIN-only lineage.

    V3 merged records already contain the unchanged ancestral prefix. Validate
    that prefix and every new lattice shard, but normalize the merged states
    only once. Failed holdout populations can never enter this ancestry.
    """
    path = Path(path).resolve()
    if path in _ancestors:
        raise ValueError('necessary edge training lineage contains a cycle')
    record = promote.read(path)
    ship = raw.get('ship', path.parent.name)
    if (record.get('format') != 1 or record.get('ship') != ship or record.get('level') != 'family'
            or record.get('phase') != phase or record.get('role') != role
            or type(record.get('seed')) is not int or record['seed'] < 0):
        raise ValueError('necessary edge population identity/role mismatch')
    source = record.get('sampler_source')
    source_path = _checked_pin(promote, source)
    config = record.get('sampler_config')
    version = record.get('sampler_builder_version')
    if (version not in ('1.0','2.0','3.0') or not isinstance(config, dict)
            or record.get('sampler_source_sha256') != source['sha256']
            or any(config.get(name) != record.get(name) for name in ('ship','level','seed','samples'))):
        raise ValueError('necessary edge sampler source/config mismatch')
    if (config.get('required_strata') != record.get('coverage',{}).get('required_strata')
            or config.get('affine_feasibility_filter') is not False):
        raise ValueError('necessary edge sampler must declare complete unfiltered coverage')
    inputs = dict(physical_inputs=record.get('physical_inputs_start'),config=config,
        builder_version=version,source_sha256=source['sha256'])
    if record.get('sampler_inputs_hash') != _digest(inputs):
        raise ValueError('necessary edge sampler numerical input hash mismatch')
    pins = [promote.pin(path),promote.pin(source_path)]
    if version in ('2.0','3.0'):
        base_source = record.get('sampler_base_source')
        if config.get('sampler_base_source') != base_source:
            raise ValueError('necessary edge base sampler source/config mismatch')
        pins.append(promote.pin(_checked_pin(promote,base_source)))
    reference_path = _checked_pin(promote,record.get('reference_transfer_source'))
    reference = promote.read(reference_path)
    if (reference.get('physical_inputs_unchanged') is not True
            or reference.get('physical_inputs_start') != raw['physical_inputs']
            or reference.get('physical_inputs_end') != raw['physical_inputs']
            or reference.get('phase') != phase):
        raise ValueError('necessary edge reference-transfer source is stale or not independent')
    pins.append(promote.pin(reference_path))
    normalized = edge_population(record,raw,template,
        transfer=reference_optimistic(reference,len(template.station_x_m)))
    seeds = {record['seed']} if phase == 'train' else set()
    if version == '3.0':
        for name in ('sampler_dependencies','prior_training','lattice_shards'):
            values = record.get(name)
            if not isinstance(values,list) or values != config.get(name):
                raise ValueError('necessary edge training lineage/config mismatch: '+name)
        if record['sampler_dependencies'] != [record['sampler_base_source']]:
            raise ValueError('necessary edge sampler dependencies omit its numerical base')
        for dependency in record['sampler_dependencies']:
            pins.append(promote.pin(_checked_pin(promote,dependency)))
        forbidden = config.get('forbidden_failed_holdout_seeds')
        if forbidden != [54932,54934]:
            raise ValueError('necessary v3 must exclude both failed holdout seeds')
        stage = config.get('sampler_stage')
        if phase == 'holdout':
            if (stage != 'independent-holdout' or record['prior_training']
                    or record['lattice_shards'] or config.get('training_seeds') != []):
                raise ValueError('necessary edge holdout cannot inherit training observations')
        elif stage == 'lattice-shard':
            if record['prior_training'] or record['lattice_shards']:
                raise ValueError('necessary lattice shard cannot inherit training observations')
        elif stage == 'merged-training':
            if len(record['prior_training']) != 1 or not record['lattice_shards']:
                raise ValueError('necessary merged training requires explicit prior TRAIN and lattice shards')
            prior_path = _checked_pin(promote,record['prior_training'][0])
            prior,prior_population,prior_pins = load_edge_record(promote,prior_path,raw,template,
                phase='train',role='grid',_ancestors=(*_ancestors,path))
            if prior.get('sampler_builder_version') != '2.0':
                raise ValueError('necessary v3 ancestry requires unchanged v2 training')
            prefix = prior['states']
            if (record['states'][:len(prefix)] != prefix
                    or config.get('prior_training_attempts') != prior['attempts']
                    or config.get('prior_training_samples') != prior['samples']
                    or config.get('prior_training_states_sha256') != _digest(prefix)):
                raise ValueError('necessary prior training prefix must remain unchanged')
            pins.extend(prior_pins)
            seeds.update(prior_population['_edge_training_seeds'])
            lattice,indices = {},set()
            shard_count = len(record['lattice_shards'])
            for shard_pin in record['lattice_shards']:
                shard_path = _checked_pin(promote,shard_pin)
                shard,_,shard_pins = load_edge_record(promote,shard_path,raw,template,
                    phase='train',role='grid',_ancestors=(*_ancestors,path))
                sc = shard['sampler_config']
                index = sc.get('shard_index')
                if (shard.get('sampler_builder_version') != version
                        or shard['sampler_source'] != source
                        or sc.get('sampler_stage') != 'lattice-shard'
                        or type(index) is not int or not 0 <= index < shard_count
                        or index in indices or sc.get('shard_count') != shard_count
                        or sc.get('requests_sha256') != config.get('requests_sha256')):
                    raise ValueError('necessary lattice shard identity/config mismatch')
                indices.add(index)
                for state in shard['states']:
                    identity = state['state_id']
                    if identity in lattice:
                        raise ValueError('necessary lattice shards repeat a state')
                    lattice[identity] = state
                pins.extend(shard_pins)
            fresh = record['states'][len(prefix):]
            if (config.get('new_lattice_attempts') != 800 or len(fresh) != 800
                    or len(lattice) != 800
                    or any(lattice.get(state['state_id']) != state for state in fresh)):
                raise ValueError('necessary merged lattice differs from pinned fresh shards')
        else:
            raise ValueError('necessary v3 training requires a declared lineage stage')
        declared = config.get('training_seeds')
        if (not isinstance(declared,list) or any(type(seed) is not int for seed in declared)
                or declared != sorted(seeds) or seeds.intersection(forbidden)):
            raise ValueError('necessary edge training seeds omit ancestry or include failed holdouts')
    normalized['_edge_training_seeds'] = sorted(seeds)
    return record,normalized,pins


def load_edge_populations(promote, folder, raw, template):
    """Require deterministic grid, independent edge holdouts and known N1 point.

    These observations enrich only the necessary side. A former counterexample
    is training evidence, never an independent passing holdout.
    """
    training, holdout, pins, coverage = [], [], [], {}
    ship = raw.get('ship', folder.name)
    expected = [('necessary-edges-train.json','train','grid'),
                ('necessary-edges-holdout.json','holdout','independent-edge-holdout')]
    review_path = folder/'necessary-review.json'
    if ship == 'feeder_500' or review_path.exists():
        expected.append(('necessary-review.json','train','review-regression'))
    for filename,phase,role in expected:
        path = folder/filename
        if not path.exists():
            raise ValueError('necessary edge/corner evidence missing: '+filename)
        record, normalized, record_pins = load_edge_record(promote,path,raw,template,phase=phase,role=role)
        if phase == 'holdout' and normalized['samples'] < 64:
            raise ValueError('necessary edge qualification requires64 independent holdouts per hull')
        (training if phase == 'train' else holdout).append(normalized)
        pins.extend(record_pins)
        coverage[role] = dict(samples=normalized['samples'],attempts=record['attempts'],
                              seed=record['seed'],coverage=record['coverage'])
    training_ids = {value for record in training for value in record['_edge_sample_ids']}
    holdout_ids = [value for record in holdout for value in record['_edge_sample_ids']]
    if training_ids.intersection(holdout_ids) or len(holdout_ids) != len(set(holdout_ids)):
        raise ValueError('necessary edge holdout repeats a training or held-out physical state')
    return dict(training=training,holdout=holdout,pins=pins,coverage=coverage)


def load_calibration_enrichment(promote, folders, raw, template):
    """Explicitly reuse unchanged former v1/v2 edge holds as calibration.

    This is qualification-builder enrichment, separate from the v3 sampler's
    training lineage. The source phase/seed/status remains archived unchanged;
    a reused observation no longer supplies independent qualification.
    """
    populations,pins,metadata = [],[],[]
    seen = set()
    for folder in folders:
        path = Path(folder)/'necessary-edges-holdout.json'
        original = promote.read(path)
        if original.get('sampler_builder_version') not in ('1.0','2.0'):
            raise ValueError('necessary calibration enrichment accepts former v1/v2 edge holds only')
        record,normalized,source_pins = load_edge_record(promote,path,raw,template,
            phase='holdout',role='independent-edge-holdout')
        population = (record['ship'],record['seed'])
        if population in seen:
            raise ValueError('duplicate necessary calibration enrichment population')
        seen.add(population)
        if normalized['samples'] < 64:
            raise ValueError('necessary calibration enrichment requires64 original edge holdouts')
        # Do not rewrite the source record, its phase, source hash or seed. This
        # isolated normalized view records the new statistical use explicitly.
        normalized.update(phase=record['phase'],role=record['role'],independent_holdout=False,
            _edge_training_seeds=[record['seed']])
        populations.append(normalized)
        pins.extend(source_pins)
        metadata.append(dict(source=promote.pin(path),
            role='former edge holdout retained unchanged; now calibration enrichment',
            original_phase=record['phase'],original_seed=record['seed'],original_role=record['role'],
            original_status=record.get('status'),original_complete=record['complete'],
            original_independent_holdout=record.get('independent_holdout'),
            original_warning=record.get('warning'),samples=normalized['samples'],
            independent_holdout=False,passing_independent_holdout_claim=False))
    return dict(populations=populations,pins=pins,metadata=metadata)


def reference_optimistic(record, count):
    """Recompute opposite core/public errors from their actual signed metrics."""
    from stowage.linear.margin_policy import _zeros
    if '_edge_reference_optimistic' in record:
        from stowage.linear.margin_policy import _envelope
        return _envelope(record['_edge_reference_optimistic'],count,name='edge reference transfer')
    public = record.get("public_checker_comparison")
    if not isinstance(public, dict):
        raise ValueError("necessary qualification requires public reference metrics")
    core = dict(public["exact_condition"])
    core["draught_aft_m"] = core["draught_m"] + .5*core["trim_m"]
    core["draught_forward_m"] = core["draught_m"] - .5*core["trim_m"]
    actual = public["actual_metrics"]
    result = _zeros(count)
    for side, sign in (("lower_unsafe", -1.), ("upper_unsafe", 1.)):
        for metric in result[side]:
            gap = sign*(np.asarray(core[metric])-np.asarray(actual[metric]))
            if not np.isfinite(gap).all():
                raise ValueError("nonfinite necessary reference-transfer residual")
            result[side][metric] = np.maximum(gap, 0.).tolist()
    return result


def interior_list(populations, limit, gm_min):
    """Outward slack actually needed by every archived exact-in-band state."""
    result = {side: 0. for side in SIDES}
    count = below = 0
    for population in populations:
        states = population.get("list_row_samples")
        if not isinstance(states, list) or not states:
            raise ValueError("necessary LIST requires archived unfiltered interior states")
        for state in states:
            heel, gm, restoring, moment = (float(state[name]) for name in
                ("exact_list_rad", "affine_gm_m", "affine_restoring_kgm", "transverse_moment_kgm"))
            # Match the saved exact-attitude domain's numerical equilibrium
            # tolerance; this qualifies observations, never a checker limit.
            if not np.isfinite([heel, gm, restoring, moment]).all() or abs(heel) > limit+1e-7:
                raise ValueError("necessary interior state is outside exact LIST domain")
            count += 1
            below += gm < gm_min
            for side, sign in (("lower_unsafe", -1.), ("upper_unsafe", 1.)):
                result[side] = max(result[side], sign*moment-math.tan(limit)*restoring, 0.)
    return result, count, int(below)


def training_seeds(populations):
    """Retain original ancestral seeds without recounting inherited samples."""
    return {seed for record in populations
            for seed in record.get('_edge_training_seeds', [record['seed']])}


def qualify_boundary(original, raw, populations, holdouts, *, pin, source):
    """Reverse signed capacity residuals, then test boundary AND interior states."""
    from stowage.linear import margin_policy as policy
    calibration, holdout = raw["calibration"], raw["holdout"]
    if not calibration or not holdout or raw.get("calibration_seed") == raw.get("holdout_seed"):
        raise ValueError("necessary boundary requires independent measured populations")
    limit, minimum = original["limit_rad"], original["gm_min_m"]
    train_interior, train_count, train_below = interior_list(populations, limit, minimum)
    test_interior, test_count, test_below = interior_list(holdouts, limit, minimum)
    roundoff = float(raw["roundoff_kgm"])
    if not np.isfinite(roundoff) or roundoff < 0:
        raise ValueError("invalid necessary boundary roundoff")
    result = deepcopy(original)
    result.pop("measured_unsafe_kgm", None)
    result.update(direction="necessary", residual_definition="exact outward capacity minus affine capacity")
    measured, transfer, excess, interior_excess = {}, {}, {}, {}
    for side in SIDES:
        # Recompute from signed residuals; do not substitute unsafe envelope values.
        measured[side] = max(train_interior[side], max(max(0., -float(
            row["sides"][side]["signed_residual_kgm"])) for row in calibration))
        physical = max(float(row["sides"][side]["reference_transfer_safe_kgm"]) for row in calibration)
        transfer[side] = physical+roundoff
        reserve = 1.1*(measured[side]+transfer[side])
        excess[side] = max(max(0., -float(row["sides"][side]["signed_residual_kgm"]))+
            float(row["sides"][side]["reference_transfer_safe_kgm"])-reserve for row in holdout)
        interior_excess[side] = test_interior[side]-reserve
        if excess[side] > 0 or interior_excess[side] > 0:
            raise ValueError(f"necessary LIST {side} holdout exceeded: "
                f"boundary={excess[side]}, interior={interior_excess[side]}")
        # Verify every archived branch, including the original holdout.
        for row in calibration+holdout:
            branch = row["sides"][side]["branch"]["outward_heel_offset_rad"]
            if not branch[0] < 0 < branch[2] or abs(branch[1]) > 2e-7:
                raise ValueError("necessary boundary LIST has unsupported branch")
    result["measured_optimistic_kgm"] = measured
    result["reference_transfer_kgm"] = transfer
    result["physical_reference_transfer_kgm"] = {s: transfer[s]-roundoff for s in SIDES}
    result["qualification"]["max_holdout_excess_kgm"] = excess
    result["interior_qualification"] = dict(independent_holdout_passed=True,
        affine_gm_filter_applied=False,calibration_seed=populations[0]["seed"],
        holdout_seed=holdouts[0]["seed"],calibration_samples=train_count,holdout_samples=test_count,
        calibration_below_affine_gm_min=train_below,holdout_below_affine_gm_min=test_below,
        max_holdout_excess_kgm=interior_excess, measured_required_kgm=train_interior,
        holdout_required_kgm=test_interior,
        calibration_seeds=sorted(training_seeds(populations)),
        holdout_seeds=sorted({record['seed'] for record in holdouts}))
    result["evidence"] = list(result["evidence"])+[pin(source)]
    result["builder_inputs_hash"] = policy._boundary_builder_inputs_hash(result)
    return result


def build(promote, folder, raw, populations, holdouts, boundary, template, reference_allowance, *,
          edge_folder=None, calibration_enrichment_folders=()):
    from stowage.linear import margin_policy as policy
    from stowage.linear.qualification import qualify_holdout
    count = len(template.station_x_m)
    edges = load_edge_populations(promote,edge_folder or folder,raw,template)
    enrichment = load_calibration_enrichment(promote,calibration_enrichment_folders,raw,template)
    populations = [*populations,*edges['training'],*enrichment['populations']]
    enriched_ids = {identity for record in enrichment['populations']
                    for identity in record.get('_edge_sample_ids',[])}
    if any(enriched_ids.intersection(record.get('_edge_sample_ids',[])) for record in edges['holdout']):
        raise ValueError('necessary edge holdout repeats a calibration enrichment physical state')
    opposite = lambda record: policy.optimistic_envelope(record["diagnostics"]["signed_errors"], count)
    physical = promote.merge_envelopes([opposite(r) for r in populations], count)
    transfer = promote.merge_envelopes([reference_optimistic(r, count) for r in populations], count)
    if reference_allowance is not None:
        for side in SIDES:
            transfer[side]["gm_m"] = max(transfer[side]["gm_m"], reference_allowance["gm_m"])
    # Preserve an explicit floating-point budget even at an exactly zero hull
    # endpoint. Two independently accumulated force curves can differ by ULPs
    # there while every training signed maximum is zero. This budget depends
    # only on the prior domain force/moment scales, never the observed holdout
    # error; the same 64-eps arithmetic convention is used by boundary.py.
    # APPROXIMATION: 64-eps is an explicit arithmetic budget following the
    # boundary builder's convention, not a formal operation-chain error bound.
    mass_scale = float(template.band.displacement_max_kg)
    length_scale = float(template._reference_params["hull"]["lpp_m"])
    force_roundoff = 64*np.finfo(float).eps*mass_scale*9.80665
    moment_roundoff = force_roundoff*length_scale
    for side in SIDES:
        transfer[side]["sf_n"] = (np.asarray(transfer[side]["sf_n"])+force_roundoff).tolist()
        transfer[side]["bm_nm"] = (np.asarray(transfer[side]["bm_nm"])+moment_roundoff).tolist()
    measured = promote.merge_envelopes([physical,opposite(raw)],count)
    observed = []
    for record in holdouts:
        value, public = opposite(record), reference_optimistic(record,count)
        observed.append({side: {metric: (np.asarray(value[side][metric])+
            public[side][metric]).tolist() for metric in policy._METRICS} for side in SIDES})
    observed = promote.merge_envelopes(observed, count)
    seeds = training_seeds(populations)
    if type(raw["diagnostics"].get("seed")) is int:
        seeds.add(raw["diagnostics"]["seed"])
    holdout_seeds = {r["seed"] for r in holdouts}
    if len(holdout_seeds) != 1 or seeds.intersection(holdout_seeds):
        raise ValueError("necessary holdout seed overlaps calibration")
    qualification = qualify_holdout(measured,transfer,observed,
        station_x_m=template.station_x_m,calibration_seed=populations[0]["seed"],
        holdout_seed=holdouts[0]["seed"],calibration_samples=sum(r["samples"] for r in populations),
        holdout_samples=sum(r["samples"] for r in holdouts))
    qualification.update(direction="necessary",calibration_seeds=sorted(seeds),
        residual_definition="lower=max(exact-linear); upper=max(linear-exact)",
        list_angles_are_diagnostics_only=True)
    edge_seeds = {record['seed'] for record in edges['holdout']}
    if (len(edge_seeds) != 1 or edge_seeds.intersection(seeds)
            or edge_seeds.intersection(holdout_seeds)):
        raise ValueError('necessary edge holdout seed overlaps calibration/ordinary holdout')
    edge_observed = []
    for record in edges['holdout']:
        value, public = opposite(record), reference_optimistic(record,count)
        edge_observed.append({side:{metric:(np.asarray(value[side][metric])+
            public[side][metric]).tolist() for metric in policy._METRICS} for side in SIDES})
    edge_qualification = qualify_holdout(measured,transfer,
        promote.merge_envelopes(edge_observed,count),station_x_m=template.station_x_m,
        calibration_seed=populations[0]['seed'],holdout_seed=next(iter(edge_seeds)),
        calibration_samples=sum(record['samples'] for record in populations),
        holdout_samples=sum(record['samples'] for record in edges['holdout']))
    edge_qualification.update(direction='necessary',kind=EDGE_KIND,
        calibration_seeds=sorted(seeds),list_angles_are_diagnostics_only=True)
    boundary_path = folder/"boundary.json"
    boundary_raw = promote.read(boundary_path)
    result_boundary = qualify_boundary(boundary,boundary_raw,populations,[*holdouts,*edges['holdout']],
        pin=promote.pin,source=__file__)
    # Pin all interior observations and the original signed boundary population.
    result_boundary["evidence"].append(promote.pin(boundary_path))
    for population in populations+holdouts+edges['holdout']:
        # Caller's evidence already pins these exact records in the parent entry.
        if population.get("physical_inputs_start") != raw["physical_inputs"]:
            raise ValueError("necessary interior physical epoch mismatch")
    policy._boundary_list(result_boundary,template,
        response=raw["diagnostics"]["response_calibration_identity"],
        reference=raw["diagnostics"]["reference_identity"],domain=raw["diagnostics"]["family_domain"],
        direction="necessary")
    result = dict(format=1,direction="necessary",physical_optimistic=physical,
        reference_transfer=transfer,holdout_qualification=qualification,boundary_list=result_boundary,
        edge_holdout_qualification=edge_qualification,edge_domain_coverage=edges['coverage'],
        edge_evidence=[*edges['pins'],*enrichment['pins']],
        calibration_enrichment=enrichment['metadata'],
        arithmetic_reserve=dict(kind="64 eps times prior domain force/moment scales",
            mass_scale_kg=mass_scale,length_scale_m=length_scale,sf_n=force_roundoff,bm_nm=moment_roundoff),
        warning="Finite independent empirical qualification; exact checker is the only certifier.")
    return result

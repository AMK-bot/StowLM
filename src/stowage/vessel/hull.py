"""Assemble integrated hull data without manufacturing missing safety limits.

``build_hull_model`` accepts the parsed fleet TOML, including its ``hull_form``
and sealed ``envelope``. Hydrostatics are even keel in the stated water density;
KN curves solve free trim about the upright LCB at each displacement. A KN
table necessarily has a reference longitudinal CG: use ``HullGeometry.kn``
directly when the actual loading condition has a different LCG.
The MCT table uses lightship KG as its declared reference KG, including the
``KB + BM_L - KG`` term; call ``hydrostatic_row(..., kg_m=actual_KG)`` for a
different loading condition. Cross curves use 2-degree heel intervals and every
hydrostatic displacement knot. They are interpolation aids; criteria evaluation
must use the geometry directly near a deck-edge or flooding-angle knee.

Operational limits and complete lightship windage (including the deckhouse)
must be supplied explicitly in ``hull`` or ``operating_limits`` before
assembling a safety-consumable HullModel. Missing
inputs raise ValueError; bare geometry remains available independently. This
follows params/fleet/README.md's instruction that absence remain an error.

Bonjean curves give each transverse section's immersed area versus draught;
integrating those areas along x gives displacement volume. Sources: the hull
offsets schema in hydrostatics.py; IMO 2008 IS Code MSC.267(85),
Part A 2 (intact stability) and Part B 3.1 (liquid free surfaces).
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import math
from typing import Any

import numpy as np
from scipy.optimize import brentq

from stowage.model import CrossCurve, HullModel, VesselFieldProvenance
from stowage.vessel.hydrostatics import HullGeometry


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        if "value" in value and "unit" in value:
            return _plain(value["value"])
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _field_evidence(params,field):
    """Retain the evidence of the declaration that actually supplies a value."""
    owner='operating_limits' if field in params.get('operating_limits',{}) else 'hull'
    section=params[owner]
    record=section.get('provenance',{}).get(field,{})
    if not isinstance(record,Mapping):
        raise ValueError(f'invalid vessel policy evidence: {owner}.{field}')
    if isinstance(section.get(field),Mapping):
        record={**section[field],**record}
    if not record.get('source') and not record.get('assumption'):
        raise ValueError(f'missing vessel policy evidence: {owner}.{field}')
    source=f'{owner}.{field}'
    if record.get('source'):
        source+='; '+json.dumps(record['source'],sort_keys=True)
    return VesselFieldProvenance(source=source,assumption=record.get('assumption'),
                                 provenance=record.get('provenance',{}))


def _load_line_draught(params):
    """Recover the declared operating waterline, never a fitted screen limit."""
    try:
        load_line=params['load_line']
        inputs=load_line['inputs']
        values=[inputs['depth_m'],load_line['deck_stringer_thickness_m'],
                load_line['available_freeboard_m'],inputs['draught_m']]
        if any(isinstance(v,bool) for v in values):
            raise ValueError('boolean geometry')
        depth,stringer,freeboard,declared=map(float,values)
        draught=depth+stringer-freeboard
        valid=(all(map(math.isfinite,(depth,stringer,freeboard,declared,draught)))
               and depth>0 and stringer>=0 and freeboard>0 and draught>0
               and math.isclose(depth,float(params['hull']['depth_m']),rel_tol=1e-12,abs_tol=1e-9)
               and math.isclose(draught,declared,rel_tol=1e-12,abs_tol=1e-9))
        if not valid or not load_line.get('assumption'):
            raise ValueError('inconsistent geometry or missing approximation evidence')
    except (KeyError,TypeError,ValueError) as error:
        raise ValueError('missing or inconsistent load_line operating waterline/evidence') from error
    return draught


def build_hull_model(ship_params: Mapping[str, Any]) -> HullModel:
    """Build integrated tables and limits; reject unspecified operational data.

    Required supplementary keys (in ``hull`` or ``operating_limits``):
    ``min_draught_fwd_m``, ``min_trim_m``, ``max_trim_m``, ``max_list_rad``,
    ``min_gm_m``, ``bilge_keel``, ``bilge_keel_area_m2``, ``windage_area_m2``
    and ``windage_lever_m``. D40 signed trim and keel-area declarations require
    per-field evidence. Displacement bounds use declared lightship and actual
    integrated displacement at the load_line record's operating waterline;
    missing policy/load-line evidence fails closed. Strength envelopes are empty.
    """
    params = _plain(ship_params)
    h = params["hull"]
    supplied = {**h, **params.get("operating_limits", {})}
    required = ("min_draught_fwd_m", "min_trim_m", "max_trim_m", "max_list_rad", "min_gm_m",
                "bilge_keel", "bilge_keel_area_m2", "windage_area_m2", "windage_lever_m")
    missing = [key for key in required if key not in supplied]
    if missing:
        raise ValueError("missing vessel operational/windage inputs: " + ", ".join(missing))
    if params["hull_form"]["family"] != "stowlm_offsets_v1":
        raise ValueError("production hull assembly requires checksum-pinned offsets; "
                         "the former analytic family is for geometry regression tests only")
    if supplied["min_gm_m"] < 0.15:
        raise ValueError("min_gm_m must be at least the IS Code 0.15 m minimum")
    if supplied["max_list_rad"] < 0:
        raise ValueError("list limit must be a nonnegative magnitude")
    for key in ('min_trim_m','max_trim_m','bilge_keel_area_m2'):
        value=supplied[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError(f'{key} must be a finite number')
    if supplied['min_trim_m']>supplied['max_trim_m']:
        raise ValueError('min_trim_m must not exceed max_trim_m')
    if supplied['bilge_keel_area_m2']<0:
        raise ValueError('bilge_keel_area_m2 must be nonnegative')
    evidence={key+'_provenance':_field_evidence(ship_params,key)
              for key in ('min_trim_m','max_trim_m','bilge_keel_area_m2')}
    evidence['min_displacement_kg_provenance']=_field_evidence(ship_params,'lightship_mass_kg')
    summer_draught=_load_line_draught(params)
    geometry = HullGeometry.from_params(params)
    density = float(h.get("water_density_kg_m3", 1025.0))
    maximum = float(h["scantling_draught_m"])
    light_mass = float(h["lightship_mass_kg"])
    # APPROXIMATION: D40 uses the synthetic design's declared load-line
    # operating waterline. Its available freeboard includes an assumed deck
    # stringer. The calibrated preliminary required-freeboard screen is NOT an
    # assigned summer freeboard and must never create a more permissive limit.
    maximum_mass=geometry.submerged(summer_draught,summer_draught,0.,density).displacement_kg
    load_line=params['load_line']
    evidence['max_displacement_kg_provenance']=VesselFieldProvenance(
        source='DERIVED: HullGeometry.submerged at load_line.inputs.depth_m + '
               'load_line.deck_stringer_thickness_m - load_line.available_freeboard_m; '
               'cross-checked against load_line.inputs.draught_m; '+json.dumps({
                   'load_line_inputs':load_line['inputs'],
                   'load_line_sources':load_line.get('sources',[]),
                   'load_line_provenance':load_line.get('provenance',{}),
                   'offsets':params['hull_form'],
                   'water_density_kg_m3':density},sort_keys=True),
        assumption='DERIVED APPROXIMATION: D40 synthetic summer operating displacement; '
                   'not certified assigned freeboard. '+load_line['assumption'])
    light_t = brentq(lambda t: geometry.submerged(t, t, 0.0, density).displacement_kg
                    - light_mass, 1e-5, maximum)
    # Include lightship and a lighter margin; tables cover through scantling.
    draughts = np.unique(np.r_[np.linspace(max(0.01, light_t * 0.8), maximum, 17),
                                 light_t, h["design_draught_m"]])
    rows = [geometry.hydrostatic_row(float(t), density,
                                   kg_m=float(h["lightship_vcg_m"])) for t in draughts]
    angles = np.radians(np.arange(0, 61, 2))
    kn_values = np.asarray([geometry.kn_curve(angles, row.displacement_kg,
                          lcg_m=row.lcb_m, density_kg_m3=density) for row in rows])
    cross_curves = []
    for index, angle in enumerate(angles):
        cross_curves.append(CrossCurve(
            heel_rad=float(angle),
            displacement_kg=[r.displacement_kg for r in rows],
            kn_m=kn_values[:, index].tolist(),
        ))
    bonjean: dict[str, list[tuple[float, float]]] = {}
    # Extend through the declared closed hull/coaming top. A trimmed condition
    # uses geometry.trimmed_sectional_areas directly, with no table extrapolation.
    bonjean_draughts = np.unique(np.r_[np.linspace(0.0, geometry.max_z_m, 41), draughts])
    for t in bonjean_draughts:
        submerged = geometry.submerged(float(t), float(t), 0.0, density)
        for x, area in zip(submerged.x_m, submerged.sectional_areas_m2, strict=True):
            bonjean.setdefault(format(float(x), ".12g"), []).append((float(t), float(area)))
    fields = ("lpp_m", "loa_m", "beam_m", "depth_m", "design_draught_m",
              "scantling_draught_m", "lightship_mass_kg", "lightship_lcg_m",
              "lightship_tcg_m", "lightship_vcg_m", "lightship_distribution")
    return HullModel(
        **{key: h[key] for key in fields},
        **{key: supplied[key] for key in required},
        **evidence,
        min_displacement_kg=light_mass,max_displacement_kg=maximum_mass,
        ap_x_m=h.get("ap_x_m", 0.0), fp_x_m=h.get("fp_x_m", h["lpp_m"]),
        water_density_kg_m3=density, hydrostatics=rows, cross_curves=cross_curves,
        bonjean=bonjean, stations=[], hull_offsets_ref=h.get("hull_offsets_ref"),
        max_draught_m=supplied.get("max_draught_m", maximum),
    )

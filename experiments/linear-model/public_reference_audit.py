"""Preserve strict equality failures, then audit their safety-margin coverage.

Core references exact 2.1 hull integration plus qualified liquid tables. Public
3.1 may also interpolate qualified hull KN tables. This proof does not rewrite
an observation or relax its original comparison tolerance: it tests whether the
measured directional public-reference gap fits the remaining empirical reserve.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import numpy as np
from stowage.linear.cache import source_fingerprint
HERE=Path(__file__).resolve().parent

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def proof_path(path):return path.with_suffix('.public-reference-audit.json')
def valid_proof(path,record):
    try:
        p=json.loads(proof_path(path).read_text())
        return (p['status'] in ('qualified_with_measured_public_interpolation_gap','qualified_with_measured_public_dispatch_gap')
            and p['source_fingerprint']==source_fingerprint()
            and p['original_case_sha256']==sha(path)
            and record['source_clean'] and p['method']==record['stability_free_surface_method']=='1'
            and all(sha(file)==digest for file,digest in {**p['kn_table_inputs'],**p['liquid_table_inputs']}.items())
            and all(row['covered'] for row in p['directional_coverage']))
    except (OSError,KeyError,TypeError,ValueError):return False

def main():
    parser=argparse.ArgumentParser();parser.add_argument('case',type=Path);a=parser.parse_args()
    d=json.loads(a.case.read_text());source=source_fingerprint()
    assert d['source_clean'] and d['source_fingerprint_end']==source
    assert d['stability_free_surface_method']=='1'
    public=d['public_checker_comparison'];uses_kn=public['production_usage']['geometry_table_used']==1
    assert d['liquid_table_inputs_end']['metadata_matches_current']
    assert d['liquid_table_inputs_start']==d['liquid_table_inputs_end']
    helper=runpy.run_path(str(HERE/'narrow.py'))
    instance,*_=helper['cargo_instance'](d['ship'],d['level'])
    assert helper['current_manifest_identity'](d['ship'],d['level'])==d['departure_manifest_identity']
    from stowage.checker.stability import _context
    from stowage.checker.stability_tables import cached_geometry
    geometry,params=_context(instance.vessel)
    table=cached_geometry(geometry,params,instance.port.water_density_kg_m3) if uses_kn else None
    pins={};cert=None;tablepath=None
    if uses_kn:
        assert table is not None
        tablepath=table.cache_path;certificate=tablepath.with_suffix('.certificate.json')
        pins={str(p):sha(p) for p in (tablepath,certificate)}
        cert=json.loads(certificate.read_text());assert cert['cache_sha256']==sha(tablepath)
    else:
        assert public['production_usage']['liquid_direct_queries']>0
        assert public['exact_condition']['liquid_table_queries']>0
    liquid_pins=d['liquid_table_inputs_end']['files']
    assert all(sha(file)==digest for file,digest in liquid_pins.items())
    cs=d['signed_calibration']['signed_errors'];hs=d['signed_heldout']['signed_errors']
    exact=public['exact_condition'];actual=public['stability_metrics'];rows=[]
    for metric in ('gm_m','trim_m','list_rad','sf_n','bm_nm'):
        core=np.asarray(exact[metric],dtype=float)
        if metric in ('sf_n','bm_nm'):
            actual_values=np.asarray([public['strength_metrics'][f'{metric}@{float(x):.12g}'] for x in d['station_x_m']])
        else:actual_values=np.asarray(actual[metric],dtype=float)
        # For a lower bound the new unsafe residual is affine-public,
        # i.e. affine-core + core-public; upper direction has opposite sign.
        for bound in (('lower',) if metric=='gm_m' else ('lower','upper')):
            gap=np.maximum((core-actual_values)*(1 if bound=='lower' else -1),0)
            raw_remaining=1.5*np.asarray(cs[bound+'_unsafe'][metric])-np.asarray(hs[bound+'_unsafe'][metric])
            remaining=np.maximum(raw_remaining,0)
            original_strict_passed=public['absolute_differences'][metric]<public['tolerances'][metric]
            rows.append({'metric':metric,'bound':bound,'gap':gap.tolist(),
                'remaining_empirical_margin':remaining.tolist(),
                'existing_heldout_deficit_at_scale1_5':np.maximum(-raw_remaining,0).tolist(),
                'original_strict_metric_passed':original_strict_passed,
                'coverage_basis':'unchanged original strict metric comparison' if original_strict_passed else 'actual scaled signed-family margin minus held-out unsafe; no numerical floor',
                'covered':bool(original_strict_passed or np.all(gap<=remaining))})
    covered=all(row['covered'] for row in rows)
    p={'status':('qualified_with_measured_public_interpolation_gap' if uses_kn else 'qualified_with_measured_public_dispatch_gap') if covered else 'uncovered_public_interpolation_gap',
       'method':'1','source_fingerprint':source,'original_case_sha256':sha(a.case),
       'original_strict_passed':public['passed'],'margin_scale':1.5,
       'core_reference':'exact 2.1 hull integration plus qualified method-1 liquid transfer',
       'public_reference':'qualified KN hull interpolation plus qualified method-1 liquid transfer' if uses_kn else 'production public checker direct hull/liquid fallback; fallback reason not exposed',
       'kn_table_inputs':pins,'liquid_table_inputs':liquid_pins,'certificate_validation':cert['validation'] if cert else None,
       'production_usage':public['production_usage'],'absolute_differences':public['absolute_differences'],
       'directional_coverage':rows,'scope':'one captured held-out condition; empirical remaining margin, not uniform equivalence or full-plan approval'}
    assert source_fingerprint()==source and all(sha(file)==digest for file,digest in pins.items())
    proof_path(a.case).write_text(json.dumps(p,indent=2)+'\n')
    print(json.dumps({'case':str(a.case),'status':p['status'],'kn_table':str(tablepath),'covered':covered}))
    if not covered:raise SystemExit(1)
if __name__=='__main__':main()

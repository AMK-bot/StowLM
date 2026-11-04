"""Read-only evidence checks, then write a compact C4 verification manifest."""
from pathlib import Path
from fractions import Fraction
import ast,hashlib,json,time,math
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[5]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    started=time.monotonic()
    path=HERE/'windows.json';w=json.loads(path.read_text())
    c=json.loads((HERE/'controls.json').read_text())
    assert w['complete'] and len(w['maps'])==23
    assert len(c['controls'])==19 and all(x['accepted'] and x['replay_legal'] and x['all_requested_aboard'] for x in c['controls'])
    assert w['summary']['controls_projection_passes']==19
    assert w['summary']['window_containment_passes']==19
    assert w['summary']['outer_witnesses']==23 and w['summary']['model_stage_errors']==0
    assert w['metadata']['experimental_sources_unchanged']
    assert w['area_summary']['sources_unchanged']
    assert w['area_summary']['maps_processed']==23 and w['area_summary']['phases_processed']==69
    assert w['area_summary']['controls_enclosed']==57 and len(w['area_summary']['control_phase_checks'])==57
    assert all(x['encloses'] for x in w['area_summary']['control_phase_checks'])
    assert not w['area_summary']['candidate_cap_installed_in_strict_windows']
    certificates=0;phases=0
    for row in w['maps'].values():
        assert row.get('error') is None and row['outer_solve']['has_witness']
        assert sha(ROOT/row['input'])==row['input_sha256']
        assert row['raw_mass_window_audit']['verified']
        assert len(row['securing_bound']['rows'])==6
        support=row['support_certificates'];p=ROOT/support['path']
        assert sha(p)==support['sha256'] and support['independent_audit']['verified']
        certificates+=len(support['independent_audit']['directions'])
        assert all(x['verified'] for x in support['independent_audit']['directions'].values())
        assert all(p['projected'] and p['window_audit']['contained'] for p in row['control_projections'])
        area=row['area_results'];p=ROOT/area['path']
        assert sha(p)==area['sha256']
        data=json.loads(p.read_text())
        assert data['input_sha256']==row['input_sha256']
        for phase,value in data['phases'].items():
            phases+=1
            assert value['full_free_trim_domain'] and value['whole_heel_range_covered']
            assert not value['checker_outward'] and not value['infeasibility_proof_eligible']
            assert math.isfinite(value['area_upper_mrad'])
            rectangle=sum((Fraction(s['pl_gz_upper_m'])*(Fraction(s['heel_rad'][1])-Fraction(s['heel_rad'][0])) for s in value['slab_bounds']),Fraction())
            assert Fraction(value['area_upper_mrad'])>=rectangle
            win=row['phase_windows'][phase]
            assert win['area_upper_mrad'] is None
            assert not win['area_relaxation']['physical_area_cap_installed']
            assert not win['area_relaxation']['checker_outward']
    assert certificates==92 and phases==69
    for name,digest in w['area_summary']['sources'].items():assert sha(ROOT/name)==digest
    for name,digest in w['metadata']['experimental_sources'].items():assert sha(ROOT/name)==digest
    sources=[*HERE.glob('*.py'),* (HERE/'_legacy').glob('*.py')]
    for source in sources:ast.parse(source.read_text(),filename=str(source))
    result=dict(verified=True,medium12_maps=12,control_input_maps=11,positive_control_plans=19,
        full_checker_passes=19,control_phase_enclosures=57,outer_witnesses=23,
        independently_verified_support_certificates=certificates,area_phase_windows=phases,
        checker_infeasibility_proofs=0,strict_area_caps=0,independent_rectangle_area_checks=phases,local_python_sources_parse=len(sources),
        windows_sha256=sha(path),wall_s=time.monotonic()-started,
        scope='artifact/source integrity and previously independent audits; no repeated full checks or numerical search')
    (HERE/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)
if __name__=='__main__':main()

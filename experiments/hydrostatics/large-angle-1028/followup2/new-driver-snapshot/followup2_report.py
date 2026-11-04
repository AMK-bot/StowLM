"""Compile new frozen-state artifacts without touching reviewed results."""
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import numpy as np
from followup2_common import HERE, ROOT, OUT, save, sha, source_hashes
from followup2_binding import COEFFICIENTS


def csv_new(path,rows):
    with path.open('x',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def table(headers,rows):
    return '\n'.join(['|'+'|'.join(headers)+'|','|'+'|'.join(['---']*len(headers))+'|',
        *['|'+'|'.join(map(str,row))+'|' for row in rows]])


def main():
    cases=[json.loads(p.read_text()) for p in sorted(OUT.glob('feeder_*-v*.json'))]
    cases.sort(key=lambda c:(int(c['ship'].split('_')[1]),c['label'],
        {'departure':0,'mid_voyage':1,'arrival':2}[c['phase']],c['rows'][0]['vent_height_above_deck_m']))
    assert len(cases)==20,len(cases)
    peer=json.loads((OUT/'peer17p2.json').read_text());free=json.loads((OUT/'peer17p2-free-trim.json').read_text())
    area_rows=[];gz_rows=[];state_rows=[];public=0;max_public_error=0.
    for case in cases:
        assert case['source_hashes_before']==case['source_hashes_after']
        assert len(case['rows'])==4
        factors={(r['coaming_credit'],r['cover_credit']):r for r in case['rows']}
        assert set(factors)=={(False,False),(True,False),(False,True),(True,True)}
        base=factors[True,False]
        if case['public_checker']:
            public+=1
            for key in (*COEFFICIENTS,'containership_form_factor','containership_depth_prime_m'):
                max_public_error=max(max_public_error,abs(base['metrics'][key]-case['public_checker']['metrics'][key]))
        for r in case['rows']:
            assert abs(r['metrics']['containership_depth_prime_m']-base['metrics']['containership_depth_prime_m'])<1e-12
            ident=dict(ship=case['ship'],case=case['label'],phase=case['phase'],
                vent_height_m=r['vent_height_above_deck_m'] if case['ship']=='feeder_500' else 7.5,
                coaming_credit=int(r['coaming_credit']),cover_credit=int(r['cover_credit']))
            values={key:r[key] for key in ('mass_kg','lcg_m','kg_m','fsc_m','gm_m','deck_edge_deg',
                'flooding_deg','total_area_end_deg','draft_aft_m','draft_fwd_m','min_b_ratio','binding_b_term')}
            values.update(water_density_kg_m3=case['density_kg_m3'],
                upper_area_0_40_deg=min(40.,r['flooding_deg']),
                containership_form_factor=r['metrics']['containership_form_factor'],
                containership_depth_prime_m=r['metrics']['containership_depth_prime_m'])
            for key,coef in COEFFICIENTS.items():
                values[key]=r['metrics'][key]
                values['B_required_'+key]=coef/r['metrics']['containership_form_factor']
                values['B_ratio_'+key]=r['all_b_ratios'][key]
            values.update(A_diagnostic_required_area_0_30_mrad=.055,
                A_diagnostic_required_area_0_40_mrad=.09,A_diagnostic_required_area_30_40_mrad=.03,
                failed_rules=json.dumps(r['failed_rules'],separators=(',',':')))
            area_rows.append({**ident,**values})
            for point in r['sealed_curve']:
                gz_rows.append({**ident,**point})
        ratio=lambda c,k:f'{factors[c,k]["min_b_ratio"]:.3f}'
        state_rows.append([case['ship'].replace('feeder_',''),case['label'],case['phase'],
            f'{base["vent_height_above_deck_m"]:.1f}' if case['ship']=='feeder_500' else '7.5',
            f'{base["deck_edge_deg"]:.2f}',f'{base["flooding_deg"]:.2f}',
            f'{base["metrics"]["area_0_flood_mrad"]:.4f}',
            ratio(False,False),ratio(True,False),ratio(False,True),ratio(True,True)])
    csv_new(OUT/'areas-and-criteria.csv',area_rows);csv_new(OUT/'gz-0to50.csv',gz_rows)
    peer_rows=[]
    for p,f in zip(peer['rows'],free['rows']):
        assert p['heel_deg']==f['heel_deg']
        peer_rows.append(dict(heel_deg=p['heel_deg'],fixed_gz_m=p['computed']['gz_m'],free_gz_m=f['gz_m'],
            abs_gz_m=p['abs']['reference_gz_m'],csi_gz_m=p['csi']['reference_gz_m'],
            fixed_minus_abs_m=p['abs']['error_gz_m'],fixed_minus_csi_m=p['csi']['error_gz_m'],
            free_minus_abs_m=f['abs']['error_m'],free_minus_csi_m=f['csi']['error_m']))
    csv_new(OUT/'peer-comparison.csv',peer_rows)
    old=json.loads((OUT/'reviewed-files-before.json').read_text())
    changed=[p for p,h in old.items() if not (HERE/p).is_file() or sha(HERE/p)!=h]
    assert not changed,changed
    sources=source_hashes()
    save(OUT/'final-driver-and-source-hashes.json',sources)
    snapshot=OUT/'new-driver-snapshot';snapshot.mkdir()
    for p in sorted(HERE.glob('followup2_*.py')):
        with (snapshot/p.name).open('xb') as f:f.write(p.read_bytes())
    save(OUT/'new-files-only-audit.json',dict(reviewed_file_count=len(old),changed_reviewed_files=changed,
        source_hashes_at_end=sources,date_utc=datetime.now(timezone.utc).isoformat()))
    changes=[];vent_changes=[]
    for case in cases:
        f={(r['coaming_credit'],r['cover_credit']):r for r in case['rows']}
        base=f[True,False];area=base['metrics']['area_0_flood_mrad']
        changes.append(dict(ship=case['ship'],case=case['label'],phase=case['phase'],vent=base['vent_height_above_deck_m'],
            no_coaming_area_change_percent=100*(f[False,False]['metrics']['area_0_flood_mrad']/area-1),
            full_cover_area_change_percent=100*(f[True,True]['metrics']['area_0_flood_mrad']/area-1),
            max_cover_delta_gz_20to50_m=max(b['gz_m']-a['gz_m'] for a,b in zip(base['sealed_curve'],f[True,True]['sealed_curve']) if 20<=a['heel_deg']<=50)))
        if case['ship']=='feeder_500' and base['vent_height_above_deck_m']==4.5:
            raised=next(c for c in cases if c['ship']==case['ship'] and c['label']==case['label']
                and c['phase']==case['phase'] and c['rows'][0]['vent_height_above_deck_m']==7.5)
            rb=raised['rows'][0]
            vent_changes.append(dict(case=case['label'],phase=case['phase'],
                flooding_before_deg=base['flooding_deg'],flooding_after_deg=rb['flooding_deg'],
                base_ratio=base['min_b_ratio'],raised_ratio=rb['min_b_ratio'],
                total_area_change_percent=100*(rb['metrics']['area_0_flood_mrad']/area-1)))
    summary=dict(cases=len(cases),curves=len(area_rows),public_baseline_replays=public,
        max_public_metric_difference=max_public_error,
        minimum_b_ratio=min(r['min_b_ratio'] for r in area_rows),
        b_failures=[r for r in area_rows if r['min_b_ratio']<1.],
        sensitivity=changes,vent_changes=vent_changes,
        total_successful_shard_wall_s=sum(c['elapsed_s'] for c in cases),
        max_successful_shard_wall_s=max(c['elapsed_s'] for c in cases),
        peak_rss_bytes=max(c['peak_rss_bytes'] for c in cases))
    save(OUT/'summary.json',summary)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(7.4,4.5),layout='constrained')
    for key,label,style in [('abs_gz_m','ABS, published','o-'),('csi_gz_m','CSI, published','s-'),
            ('fixed_gz_m','IGES17.2, fixed trim','-'),('free_gz_m','IGES17.2, free trim','--')]:
        ax.plot([r['heel_deg'] for r in peer_rows],[r[key] for r in peer_rows],style,label=label)
    ax.axhline(0,color='.5',lw=.6);ax.set(xlabel='Heel (degrees)',ylabel='GZ (m)',title='JMSE2022: volume54148m³, KG13.674m')
    ax.grid(alpha=.2);ax.legend(fontsize=8);fig.savefig(OUT/'peer-comparison.png',dpi=180);plt.close(fig)
    selected=[c for c in cases if c['phase']=='arrival' and (c['ship']!='feeder_500' or c['rows'][0]['vent_height_above_deck_m']==4.5)]
    fig,axes=plt.subplots(4,2,figsize=(11,13),layout='constrained')
    colors={(0,0):'#777777',(1,0):'#2376b7',(0,1):'#d89722',(1,1):'#279b64'}
    for ax,case in zip(axes.flat,selected):
        for r in case['rows']:
            xs=np.array([v['heel_deg'] for v in r['sealed_curve']]);ys=np.array([v['gz_m'] for v in r['sealed_curve']])
            factor=(int(r['coaming_credit']),int(r['cover_credit']));color=colors[factor]
            ax.plot(xs,ys,'--',color=color,alpha=.5)
            xp=np.r_[xs[xs<r['flooding_deg']],r['flooding_deg']]
            ax.plot(xp,np.interp(xp,xs,ys),color=color,label=f'C{factor[0]} K{factor[1]}')
        ax.axvline(case['rows'][0]['flooding_deg'],color='#2376b7',lw=.7)
        ax.set(title=f'{case["ship"]} {case["label"]} arrival',xlabel='Heel (degrees)',ylabel='GZ (m)',xlim=(0,50))
        ax.grid(alpha=.2);ax.legend(fontsize=7,ncol=2)
    for ax in list(axes.flat)[len(selected):]:ax.set_visible(False)
    fig.suptitle('Credit sensitivity; dashed curves after downflooding are sealed continuations')
    fig.savefig(OUT/'binding-state-curves.png',dpi=160);plt.close(fig)
    peer_table=table(['Heel°','IGES fixed GZ','IGES free GZ','ABS GZ','CSI GZ','Fixed−ABS','Fixed−CSI'],
        [[f'{r["heel_deg"]:.0f}',*[f'{r[k]:.3f}' for k in ('fixed_gz_m','free_gz_m','abs_gz_m','csi_gz_m','fixed_minus_abs_m','fixed_minus_csi_m')]]
         for r in peer_rows if r['heel_deg']>=20])
    state_table=table(['Ship','Case','Phase','Hold vent m','Deck edge°','φf°','A0–end m·rad','C0K0 ratio','C1K0 ratio','C0K1 ratio','C1K1 ratio'],state_rows)
    no_min=min(r['no_coaming_area_change_percent'] for r in changes);no_max=max(r['no_coaming_area_change_percent'] for r in changes)
    cover_min=min(r['full_cover_area_change_percent'] for r in changes);cover_max=max(r['full_cover_area_change_percent'] for r in changes)
    draft=peer['upright_at_source_volume']['centreline_intercept_m']
    report=f'''# Second follow-up: peer-reviewed KCS reference and binding feeder states

2026-10-28. **Peer-reviewed confirmation remains partial.** The prescribed IGES-upper shell clipped at17.2m agrees with ABS at5/7 and CSI at2/7 ordinates over20–50° under the0.05m L5-style diagnostic bars. Maximum discrepancies are0.118m and0.149m respectively. Free trim worsens the large-angle disagreement. This does not overturn the earlier like-envelope preprint match or numerical oracle, and does not independently validate the synthetic feeder topsides.

## Published reference

[Marlantes, Kim & Hurt, JMSE2022,10(1),41](https://doi.org/10.3390/jmse10010041), Tables23/24 and Fig22: depth17.2m, **volumetric displacement54148m³**, KG13.674m, aft/forward draught10.8m and GM1.32m. Existing vector/raster extraction retained exactly; no55–60° extrapolation. All values below are metres. LCG and Fig22's heeled trim convention are unspecified: fixed-zero-trim is primary, actual-KG free trim with LCG set to our own upright LCB is a labelled sensitivity, not a recovered author condition.

{peer_table}

Our draught at the paper's volume is{draft:.6f}m; our volume at10.8m is{peer['volume_at_source_draught_m3']:.3f}m³. Thus even the upright source inputs are not geometrically consistent with this offset loft. The paper explicitly labels54148 as m³, not tonnes; no silent reinterpretation is made. Mesh40→80 changes GZ by at most{peer['max_mesh40to80_gz_change_m']:.8f}m. GZ diagnostic tolerance is max(0.05m,5% of reference magnitude); plot-reading allowances remain0.02m ABS/0.01m CSI and are not used to expand the reported pass bars. L5 comparisons require identical input and approved reference data; these are diagnostic comparisons, not class approval. [Full ordinates](followup2/peer-comparison.csv), [plot](followup2/peer-comparison.png).

## Frozen binding-state probes

**C=coaming/hatchway-region buoyancy credit; K=full0.6m cover-body cuboid credit.** C1K0 is today's convention. C0K1 is an artificial attribution cell, not an open-hatch ship. Physical coamings remain in the production B2.3.2.6 D′ calculation in all four cells; no cover height is added to D′. Mass, liquid method1, liquid transfer, windage, opening closure declarations and all rule limits are fixed. The500's7.5m cases implement D67 **hold vents only**; ER inlet heights stay unchanged. Other ships already have7.5m hold vents.

Each reported ratio is the minimum of **all six** B2.3 area/lever measured-to-required ratios;1.0 is the limit. A0–end is production's positive total area through min(φf,first vanishing stability angle). Area0–40 and30–40 stop at min(40°,φf), as production defines them. The complete table includes every metric, limit, density, draft, D′, GM and all other evaluated stability-criterion failures: [80 factor-state rows](followup2/areas-and-criteria.csv). A2.2 area's0.055/0.09/0.03m·rad bars are included as diagnostics; D21 applies B2.3 for these L>100m feeders. [All GZ ordinates0–50°](followup2/gz-0to50.csv), [arrival curves](followup2/binding-state-curves.png). Mathematical sealed continuation after φf is excluded from the regulatory areas.

{state_table}

The D58 controls are the newer tare-valid medium seed0 `review-fixes/retry-random-feeder_*-seed0` accepted backgrounds, projected with `background_instance()` and their **saved empty-region ballast witnesses**, not the booked initial tank masses. Their fleet parameter pins matched at input freeze. These states are freshly solved with current stability code; they are not a new current-generator or full-checker acceptance certificate. All three D57 states are included. [Selection and hashes](followup2-d58-selection.md); frozen model inputs and hashes are archived in `followup2/`. Older tiny controls with sub-tare equivalent loads were deliberately excluded.

Across these states, withholding coaming-region credit changes usable total area by{no_min:.1f}% to{no_max:.1f}% relative to C1K0; adding the entire assumed cover-body volume changes it by+{cover_min:.1f}% to+{cover_max:.1f}%. D67's hold-only raise increases the500's usable total area by{min(v['total_area_change_percent'] for v in vent_changes):.1f}–{max(v['total_area_change_percent'] for v in vent_changes):.1f}%. Exact per-state effects are in [summary.json](followup2/summary.json). **{len(summary['b_failures'])} of80 factor-state rows fail a B2.3 minimum**; this is not a declaration that every full-checker rule passes. The tested500 baselines already pass; D67 increases their margins. The historical morning failure state is not this newer D58 witness, so no rescue claim is made for that historical state. Coaming/cover qualification and real feeder topside shape remain unverified; the overall direction relative to a real feeder is unknown. Cover omission is stricter only relative to a qualified closed cover-body hypothesis. Credited hatchway volume can be optimistic relative to withholding that credit. Container stacks receive no buoyancy credit.

## Clause and label for the leader

[2008 IS Code, MSC.267(85)](https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.267%2885%29.pdf), **B3.5.2.10** permits hatchway credit subject to closure effectiveness. **B2.3.2.6**, with Fig2.3-1, separately defines D′ from physical coaming dimensions. Effective hatchway closure alone does not prove that the full declared cover cuboid is itself a buoyant body. [Independent clause/credit review](followup2-credit-review.md) links the primary MCA reproduction too. Closure failure would need a separate flooding scenario, not this isolated credit matrix.

Proposed `params/fleet/README.md` label (leader applies later): **“hatchway credited to the coaming top; cover volume not credited: an intermediate convention pending Q20b”.** No production physics or parameters were changed. Proposed status: “KCS numerical integration independently confirmed;19m IGES case matches one digitised preprint, while17.2m JMSE2022 comparison is only partial. Feeder topsides remain synthetic and unvalidated.”

## Verification and compute

{public} baseline states also replayed through public production `check_stability(in_port=False)`; all six B2.3 metrics plus C and D′ match the experiment path within{max_public_error:.3g}. All20 successful shards have unchanged before/after dependency hashes. Concurrent production edits were caught during1700 D58 departure: the loading change was comments only, and the stability changes affect in-port handling, outside this seagoing study. The flagged shard is archived and rerun under stable new hashes. [Independent source audit](followup2-concurrent-source-audit.md); [three post-epoch public baseline replays](followup2/post-epoch-baseline-replay.json) reproduce **every stored criterion metric exactly**, after direct-geometry warm-up. A cold1700 replay hit its120s budget (132.7s at liquid/hull equilibrium), retained as a runtime limitation. A second source alert in the post-epoch replay concerned only unused batch dispatch; [batch-path audit](followup2-batch-source-audit.md). Later dependency manifests omit that uninvoked batch module and the uninvoked in-port state module, retaining the broader epoch evidence. No same-byte epoch claim is made across the whole campaign. New analytic credit/vent tests and artifact verification are recorded separately after report generation. Initial public-wrapper probe failed because an internal-only `_force_direct` keyword was passed; the new driver was corrected to use the documented environment switch, and that shard was rerun successfully. Its failed log is retained; no result was reused from the failed attempt.

Serial shards only,1CPU, BLAS1, no GPU. Pilot500 four-factor+public solve18.5s/0.37GB; subsequent500/800 estimate≤120s and≤0.6GB per shard. After the800 arrival measured91.6s,1700 was estimated≤180s/≤0.8GB per shard using its earlier geometry timing. Actual successful shard sum{summary['total_successful_shard_wall_s']:.1f}s, maximum{summary['max_successful_shard_wall_s']:.1f}s, peak RSS{summary['peak_rss_bytes']/1e9:.3f}GB. Peer fixed/free runs are separate short serial probes. No heavy multi-core job, paid call or ledger admission. New-file audit hashes{len(old)} pre-existing reviewed files and finds zero changed files; all new drivers and production source hashes are pinned in the new artifacts. Two new800 JSON outputs initially labelled the ignored CLI vent argument4.5m as the actual height; this metadata was corrected to the unchanged physical7.5m, preserving original new outputs and an audit. No geometry or numerical metric changed.
'''
    with (HERE/'FOLLOWUP2.md').open('x') as f:f.write(report)
    print(json.dumps({k:summary[k] for k in ('cases','curves','public_baseline_replays','max_public_metric_difference','minimum_b_ratio','total_successful_shard_wall_s','max_successful_shard_wall_s','peak_rss_bytes')},indent=2))


if __name__=='__main__':main()

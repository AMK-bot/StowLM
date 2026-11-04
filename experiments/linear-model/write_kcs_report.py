"""Render the final study from measured shards, never from pilot estimates."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
HERE=Path(__file__).resolve().parent

def main():
 p=argparse.ArgumentParser();p.add_argument('--directory',type=Path,default=HERE/'kcs-production/current');a=p.parse_args()
 cases=json.loads((a.directory/'narrow-results.json').read_text())
 proofs=json.loads((a.directory/'cache-verification-index.json').read_text())
 assert len(cases)==len(proofs)==9 and all(x['status']=='verified_disk_hit' for x in proofs.values())
 rows=[]
 for key,d in cases.items():
  rows.append('| '+key+' | '+str(round(d['manifest']['cargo_mass_kg']/1000,3))+' | '+
   ' | '.join(f'{v:.6g}' for v in (d['model_errors']['gm_m'],d['sweep_errors']['gm_m'],d['diagnostics']['p99']['gm_m'],d['known_measured_envelope_320']['gm_m']))+' |')
 factor=max(max(d['heldout_over_calibration_factors'].values()) for d in cases.values())
 recommendation=max(2.,float(np.ceil(1.1*factor*10)/10))
 default_margin=('does exceed' if factor<1.1 else 'DOES NOT exceed')
 maxgm=max(d['known_measured_envelope_320']['gm_m'] for d in cases.values())
 cold=[d['build_seconds'] for d in cases.values()];warm=[d['public_cached_build_seconds'] for d in proofs.values()]
 median=[d['constraints_median_seconds'] for d in proofs.values()];worst=[d['constraints_max_seconds'] for d in proofs.values()]
 current={d['source_fingerprint_at_summary'] for d in cases.values()};assert len(current)==1
 audits=sum('loader_only_epoch_audit' in d or 'schema13_metadata_epoch_audit' in d for d in cases.values())
 provenance=('All nine cases have identical current source, numerical files, component versions and raw inputs before and after measurement; no epoch qualification or cache promotion was required.' if audits==0 else f'{audits} cases have separately recorded loader/schema1.3 metadata equivalence audits. Original dirty flags are retained; numerical linear/hydro/stability/strength files, non-schema versions and raw input pins are unchanged. Exact current inputs or the reviewed reversible schema1.2 representation hit the original-source cache keys with exactly matching calibration vectors and full model-field digests before audited promotion. FixedLoad conversion is allowed only for default kind=other and no silhouette; schema/provenance version tags are recorded separately. The three light cases were measured directly on the unchanged current epoch and compared an accepted physical condition with the current public stability/strength references; no schema bridge was applied to those records. Visibility/lashing changes are outside these five physics comparisons and no full-legality claim is made.')
 text=f'''# Current KCS production linear-model study ({next(iter(cases.values()))['model_version']})

All nine fixed-manifest cases were measured against public free-surface method 2 (virtual-rise stability), with physical section-aware coupled liquid equilibrium retained for exact sectional strength integration: 192 deterministic calibration conditions plus 128 independent held-out conditions each, **2,880 accepted conditions**. The requested GM error target of 0.05–0.10 m is **not achieved** over these full tank ranges: the largest known GM residual is **{maxgm:.6g} m**. These are empirical maxima, not uniform global bounds or approved departures.

| Ship / case | Fixed cargo (t) | GM calibration max (m) | GM held-out max (m) | GM held-out p99 (m) | Known 320-condition max (m) |
|---|---:|---:|---:|---:|---:|
'''+ '\n'.join(rows)+f'''

[All six metrics, max and p99](current/narrow-errors.md) include GM, trim, list, mean draught, SF and BM. [The JSON](current/narrow-results.json) retains every station's max/p99, sample coverage, decomposition, all source/input pins, and calibration versus held-out counterexamples. [Bands](current/narrow-bands.md), [production proof and public-checker comparisons](current/narrow-production.md), and [cache verification](current/cache-verification-index.json) are separate reviewable artifacts.

Calibration `LinearErrors` is preserved unchanged. The known 320-condition envelope is its componentwise maximum with held-out errors. The largest observed held-out/calibration ratio is {factor:.6g}; a margin scale of **at least {recommendation:g}** exceeds this observed ratio with reserve. Default scale 1.1 **{default_margin}** all observed held-out/calibration factors above the stated roundoff floors. [Component-specific ratios](current/narrow-margin-factors.md) preserve every counterexample. Apply absolute error floors as well: GM/trim/draught 1e-6 m, list 1e-8 rad, SF 0.01 N and BM 1 N m. An explicit empirical row-margin policy is `scale * max(calibration_error, absolute_floor)` componentwise, with the scale above; `margin_scale` alone does not install these floors. These floors do not alter reported errors. This recommendation is empirical and does not guarantee all possible layouts. The default 1.1 therefore must not be treated as sufficient for this study when the table shows a counterexample. Every candidate must pass the exact checker.

Point GM uses the actual known-fill FSM under the exact checker's consumable policy. Affine safety rows separately reserve each tank's continuous maximum slack FSM. That deliberate reserve is reported separately rather than counted twice as point-estimate error. Fixed reference tank arms and free-trim KM variation remain approximations; the GM decomposition identifies their residuals. Narrower explicit ballast windows or improved local tank-arm models are needed to pursue the requested smaller error target.

Original production cell counts are 372 / 592 / 1604; checksum-pinned published KCS offsets are scaled to synthetic feeder dimensions. Original hull/cell/tank layouts and station envelopes are fingerprinted before presolve. Every benchmark box is at most 30,480 kg, so `min(production stack cap, 30480 * cell count)` is an exact implied local bound. Light/medium mean 20%/60% of the declared reachable mass budget, with known mixed standard/HC inventory and ±0.4 m trim bands. **1000 medium restricts HC under deck**; unrestricted HC previously exposed a greedy sampler coverage failure. Full means 100% mass budget using standard 20 ft boxes, maximum-gross bottom boxes and lighter top remainders, with the vessel's operating trim band; it does not mean all TEU occupied. Full tank ranges and all exact band windows are recorded per case. No OOG was sampled.

One accepted held-out physical loading per ship was compared with public `check_stability` and `check_strength` for GM/trim/list/SF/BM using equivalent point loads at true box centres. Those comparisons do not establish cargo GZ, windage, weather, packing legality or a full checker approval. The greedy physical sampler and 40 ft pair proposals do not enumerate every possible layout.

The authoritative 2.1 additive section polygons are retained, including observed one-ULP overlap artefacts. No union, snap or geometric cap is applied. The model and exact reference integrate the same geometry, and the corrected half-open interval selectors preserve its conservative continuous FSM bound. The recorded HEEL pair extra reserve is about 2105.77 kg m, or 0.000210577 m GM at 10 million kg displacement; this is conservative constraint reserve, not point-prediction error. This remains an upstream numerical-input limitation; independent real-ship validation is not claimed.

Current aggregation source fingerprint: `{next(iter(current))}`. {provenance}

Fresh-process cache verification passed for all nine current keys with recalibration forbidden and all public model fields preserved. Cold 192-condition model builds took **{min(cold):.2f}–{max(cold):.2f} s**; the first-build-in-seconds goal is therefore not met. Fresh-process public disk-hit builds took **{min(warm):.4f}–{max(warm):.4f} s**. Twenty `linear_constraints` calls per model had median **{min(median)*1000:.3f}–{max(median)*1000:.3f} ms**, largest observed call **{max(worst)*1000:.3f} ms**. These row-generation timings support cheap CP/MIP use.

The archived model 2.2 main driver exited 1 after loader source epochs changed; two light cases were rerun before the later method 2 numerical migration. Those historical logs/status and observations are preserved under `method1-observed/`. Prior incompatible bounding-box pilots and historical model 2.0 tables remain diagnostics only. See [report-before-section-fix.md](report-before-section-fix.md) and the archived interrupted pre-selector main round for provenance.
'''
 text=text.replace('current/',a.directory.name+'/')
 (a.directory.parent/'report.md').write_text(text)
 (a.directory.parent/'README.md').write_text('# KCS production model 2.3 measurement\n\nNine cases, 192 calibration + 128 held-out conditions each, all current numerical inputs qualified and all nine cache hits verified. **GM target 0.05–0.10 m is not achieved** over full tank ranges. See [the report](report.md), [all metric max/p99 tables]('+a.directory.name+'/narrow-errors.md), and [cache proofs]('+a.directory.name+'/cache-verification-index.json). Historical incompatible pilots are preserved separately; no approved departure is claimed.\n')
 print(json.dumps({'cases':len(cases),'known_gm_max_m':maxgm,'observed_max_ratio':factor,'recommended_empirical_margin_scale':recommendation,'epoch_audits':audits}))
if __name__=='__main__':main()

"""Summarize only saved real CPU calibration rows; no model/checker invocation."""
from pathlib import Path
import hashlib,json,statistics
HERE=Path(__file__).resolve().parent
pilot=HERE/'pilot-current'
rows=[json.loads(path.read_text()) for path in sorted(pilot.glob('row-*.json'))]
manifest=json.loads((pilot/'manifest.json').read_text())
small=[r for r in rows if '/small/' in r['input']]
summary=dict(schema='d137-teacher-measurements-v1',measured_maps=len(rows),passed=sum(r['passed'] for r in rows),
    source_manifest_sha256=hashlib.sha256((pilot/'manifest.json').read_bytes()).hexdigest(),
    source_epoch_unchanged=all(r['source_epoch_unchanged'] for r in rows),
    planning_budget_s=manifest['planning_s'],finisher_budget_s=manifest['finalize_s'],
    per_map=[dict(input=r['input'],passed=r['passed'],weighted_moves=r.get('moves'),
        planning_s=r['planning_s'],finisher_s=r.get('verification',{}).get('timings',{}).get('ballast_s'),
        checker_s=r.get('verification',{}).get('timings',{}).get('checker_s'),
        mask_s=r.get('verification',{}).get('timings',{}).get('mask_s'),total_s=r['total_s']) for r in rows],
    total_map_cpu_wall_s=sum(r['total_s'] for r in rows),
    small_mean_s=statistics.mean(r['total_s'] for r in small) if small else None,
    small_max_s=max((r['total_s'] for r in small),default=None),
    limitations=['Only three train maps with checked gold, two small ships. Not a random success-rate sample.',
        'No LNS polish timing in this probe; production adds15sLNS and requalification on improvement.',
        'Results bind source manifest; D132 installation requires fresh calibration.',
        'Actual complete DPO update/control-training times not measured by this CPU probe.',
        'Per-map wall time is single-worker under contemporaneous8.2GPUtraining; no parallel scaling measurement.'])
(HERE/'results.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))

"""True-content comparison of fresh V2 events with immutable V1 candidate."""
from hashlib import sha256
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
V1 = ROOT / 'experiments/mvp/batch-d132/masks'
prepared = json.loads((V1 / 'prepared/manifest.json').read_text())
old = json.loads((V1 / 'candidate/summary.json').read_text())
new = json.loads((HERE / 'candidate/summary.json').read_text())
assert old['complete'] and new['complete'] and old['source_stable'] and new['source_stable']
assert old['manifest_sha256'] == new['manifest_sha256']
counts = {'traces': 0, 'token_prefixes': 0, 'engine_probes': 0, 'complete_plans': 0}
differences = []
for trace in prepared['traces']:
    a = json.loads((V1 / 'candidate' / trace['file']).read_text())
    b = json.loads((HERE / 'candidate' / trace['file']).read_text())
    if not a['replayed'] or not b['replayed'] or a['events'] != b['events']:
        differences.append({'id': trace['id'], 'reason': 'fresh V2 event content differs from V1'})
        continue
    # Whole ordered event equality includes full legal arrays, not just digests.
    counts['traces'] += 1
    counts['token_prefixes'] += sum(e['type'] == 'prefix' for e in b['events'])
    counts['engine_probes'] += sum(e['type'] == 'engine_probe' for e in b['events'])
    counts['complete_plans'] += b['status'] == 'complete'
baseline = json.loads((HERE / 'comparison.json').read_text())
result = {'qualified': counts['traces'] == 214 and not differences and baseline['qualified'],
    'revision': 2, 'counts': counts, 'differences': differences,
    'fresh_v2_vs_v1_candidate_event_content_identical': not differences,
    'original_baseline_additions_only_policy': baseline['qualified'],
    'original_baseline_changed_queries': baseline['changed_queries'],
    'v2_manifest_sha256': new['revision2_manifest_sha256'],
    'source_delta_vs_v1_candidate': new['v1_candidate_source_delta'],
    'original_prepared_manifest_sha256': sha256((V1 / 'prepared/manifest.json').read_bytes()).hexdigest(),
    'no_old_artifacts_overwritten': True, 'test_bodies_read': False,
    'full_checker_or_model_run': False}
(HERE / 'v1-candidate-identity.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))
raise SystemExit(0 if result['qualified'] else 1)

"""Explicit retrospective validation audit; never authorizes a held-out score.

Only the already-scored v2 recordings are eligible. Their original input hashes,
readings and scores are preserved. Changed behavior is evaluated as development
replay in a temporary directory and reported separately, never as a fresh holdout.
No model client is constructed and no paid recording can occur here.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile

from experiments.mvp.pipeline.contract.schema import sha256_file
from . import read_documents
from .admission import wait_for_slot
from .evaluate_v2 import aggregate, verify_inputs
from .measure import score_case
from .spark_reader import RecordedClient

HERE = Path(__file__).resolve().parent
COHORT = HERE / 'fixtures/heldout_v2'


def behavior(result):
    value = result.model_dump(mode='json') if hasattr(result, 'model_dump') else result
    return {name: value[name] for name in ('status', 'rows', 'questions', 'field_sources')}


def run():
    wait_for_slot()
    manifest = verify_inputs(COHORT)
    historical = json.loads((COHORT / 'metrics.json').read_text())
    original_hashes = {str(path.relative_to(HERE)): sha256_file(path)
                       for path in sorted(COHORT.rglob('*')) if path.is_file()}
    cases = []
    for item in manifest['cases']:
        case = COHORT / item['split']
        documents = json.loads((case / 'documents.json').read_text())
        base = json.loads((case / 'base_instance.json').read_text())
        current = read_documents(documents, base_instance=base, client=RecordedClient(case / 'spark_raw.json'))
        old = json.loads((case / 'reading.json').read_text())
        changed = behavior(current) != behavior(old)
        if changed:
            # Deliberately a spent-data development replay, not score_case's
            # protected first-score entry point or a replacement freeze.
            with tempfile.TemporaryDirectory(prefix='intake-code4-development-') as directory:
                replay = Path(directory) / 'development'
                replay.mkdir()
                for name in ('expected.json', 'base_instance.json', 'documents.json'):
                    shutil.copyfile(case / name, replay / name)
                metrics = score_case(replay, raw_path=case / 'spark_raw.json', save=False)
        else:
            metrics = json.loads((case / 'metrics.json').read_text())
        cases.append({'case': case.name, 'behavior_changed': changed, **metrics})
    assert all(sha256_file(HERE / name) == digest for name, digest in original_hashes.items())
    report = {'label': 'measured', 'kind': 'retrospective development replay of spent held-out data',
              'created_utc': datetime.now(timezone.utc).isoformat(),
              'reason': 'Review fixes changed source authentication and independent conflict checks.',
              'fresh_heldout': False, 'additional_model_calls': 0, 'additional_cost_usd': 0,
              'historical_metrics_sha256': sha256_file(COHORT / 'metrics.json'),
              'old': historical, 'new': aggregate(cases), 'historical_artifacts_sha256': original_hashes,
              'current_source_files': {path.name: sha256_file(path) for path in sorted(HERE.glob('*.py'))}}
    target = HERE / 'review_v6_results.json'
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    demo = score_case(HERE / 'fixtures/demo_v2')
    assert demo['status'] == 'complete' and demo['assembled_sha256_equals_base'], demo
    print(json.dumps({'old': historical['before_answers'], 'new': report['new']['before_answers'],
                      'after': report['new']['after_answers'], 'traps_caught': report['new']['traps_caught'],
                      'hash_matches': report['new']['assembled_hash_matches'], 'demo': demo,
                      'additional_cost_usd': 0}, indent=2), flush=True)
    return report


if __name__ == '__main__':
    run()

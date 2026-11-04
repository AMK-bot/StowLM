"""CPU-only validation of D135's real sampled replay-cache fixture.

Does not run the checker, change gold, publish cache entries, or alter src/.
The cache must already contain current-epoch entries from the active runner.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[5]


def main():
    from llm.exit.identity import identity_guard
    from llm.exit.replay import checker_epoch, ensure_current_replay
    from stowage.checker import report_matches_inputs
    from stowage.model import CheckReport

    started = time.monotonic()
    fixture_path = Path(__file__).with_name('real-selected-fixture.json')
    fixture = json.loads(fixture_path.read_text())
    source = Path(fixture['source_selection'])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == fixture['source_selection_sha256']
    assert fixture['checker_epoch'] == checker_epoch()
    spec = importlib.util.spec_from_file_location('_d135_fixture_builder', ROOT / 'experiments/mvp/sft/dataset/build.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    guard = identity_guard(ROOT / 'experiments/mvp/corpus')

    def no_check(*args, **kwargs):
        raise AssertionError('Existing current-epoch row unexpectedly requested verification')

    evidence = []
    for row in fixture['evidence']:
        path = Path(row['record'])
        before = path.read_bytes()
        assert hashlib.sha256(before).hexdigest() == fixture['selected_sha256'][str(path)]
        instance, plan, report, digest, input_path = builder.load_triple(path)
        guard.require_allowed(instance, role='train', raw_sha=digest, input_path=input_path)
        key = hashlib.sha256((instance.model_dump_json() + plan.model_dump_json()).encode()).hexdigest()
        assert key == row['cache_key']
        cache_file = Path(row['cache_path'])
        assert hashlib.sha256(cache_file.read_bytes()).hexdigest() == row['cache_sha256']
        admitted_plan, admitted_report, rechecked = ensure_current_replay(
            path, instance, plan, report, cache_file.parent, verifier=no_check)
        assert not rechecked
        parsed = CheckReport.model_validate(admitted_report)
        assert parsed.passed and report_matches_inputs(parsed, instance, admitted_plan)
        assert path.read_bytes() == before
        evidence.append({'record': str(path), 'key': key, 'cache_hit': True, 'bound': True})
    result = {'rows': len(evidence), 'checker_epoch': checker_epoch(),
              'wall_s': time.monotonic() - started, 'results': evidence}
    target = Path(__file__).with_name('real-fixture-validation.json')
    target.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'rows': len(evidence), 'wall_s': result['wall_s'], 'report': str(target)}))


if __name__ == '__main__':
    main()

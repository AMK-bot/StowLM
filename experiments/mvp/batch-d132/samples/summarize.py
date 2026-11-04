"""Validate saved D132 sampling evidence without rerunning any physics."""
from pathlib import Path
from datetime import datetime, timezone

from runtime import FENCE, read, save, sha, verify_runtime


def main():
    runtime = Path('/private/tmp/stowlm-d132-assembly-20261030/candidate')
    epoch = verify_runtime(runtime)['source_epoch_sha256']
    samples = []
    for name, replay_name in [('showcase', 'replay-candidate.json'),
                              ('val-c-append', 'replay-candidate-lookahead.json')]:
        root = FENCE / name
        checked, replayed = read(root / 'check.json'), read(root / replay_name)
        assert checked['passed'] and replayed['passed']
        assert checked['runtime_epoch'] == replayed['runtime_epoch'] == epoch
        assert checked['plan_sha256'] == sha(root / 'plan.json')
        assert checked['report_sha256'] == sha(root / 'report.json')
        assert checked['input_sha256'] == sha(root / 'instance.json')
        report = read(root / 'report.json')
        assert report['passed'] and all(p['ran'] and not p.get('error') for p in report['parts'].values())
        counters = replayed['counters']
        assert counters['securing_active'] and counters['securing_metrics_complete']
        assert counters['securing_calls'] > 0 and counters['fail_open'] == counters['unavailable'] == 0
        samples.append({'name': name, 'instance_id': checked['instance_id'],
                        'input_sha256': checked['input_sha256'], 'plan_sha256': checked['plan_sha256'],
                        'report_sha256': checked['report_sha256'],
                        'checker_passed': True, 'production_mask_securing_passed': True,
                        'lookahead': replayed.get('lookahead', False),
                        'actions': replayed['actions'],
                        'weighted_nonproductive': report['count']['weighted_nonproductive'],
                        'checker_s': checked['wall_s'], 'mask_s': replayed['wall_s'],
                        'terminal_prefix_cure': checked['full_plan_cure']})
    baseline = read(FENCE / 'showcase/replay-baseline.json')
    assert baseline['passed'] is False and baseline['actions_replayed'] == 9
    assert baseline['blocked_action'] == {'op': 'P', 'box': 'QZXU0000040', 'slot': '0030184'}
    train = read(FENCE / 'train-default/construct.json')
    val = read(FENCE / 'val-c/construct.json')
    assert not train['passed'] and train['prefix']['preserved'] and not val['passed']
    prefixes = read(FENCE.parent / 'heat/val-prefixes.json')['prefixes']
    matched = [row for row in prefixes if row['instance_id'] == 'random.small.41100020.0.2' and row['arm'] in ('c', 'd')]
    assert len(matched) == 2 and matched[0]['actions'] == matched[1]['actions']
    assert matched[0]['input_sha256'] == matched[1]['input_sha256'] == samples[1]['input_sha256']
    assert read(FENCE / 'val-c-append/finalize.json')['prefix']['preserved']
    assert read(FENCE / 'val-c-append/check.json')['prefix']['preserved']
    processes = []
    for path in sorted(FENCE.glob('process-*/process.json')):
        record = read(path)
        assert record['reaped'] and record['exit_code'] == 0 and not record['source_drift']
        processes.append({key: record[key] for key in ['owned_pid', 'wall_s', 'cpu_s', 'peak_waited_child_rss_bytes']} | {'job': path.parent.name})
    output = {'passed': True, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'runtime_epoch': epoch, 'unique_checked_newly_reachable_plans': 2,
        'samples': samples, 'showcase_baseline': baseline,
        'val_prefixes_deduplicated': [row['id'] for row in matched],
        'val_unique_checked_plans': 1,
        'val_original_decoder_rewind_blacklists_recreated': False,
        'original_model_pilot_rerun': False,
        'train_original_terminal_cured': False,
        'train_result': {'status': train['status'], 'prefix_preserved': True,
                         'deepest_depth': train['metrics']['deepest_depth']},
        'val_d92_constructor_passed': False,
        'val_d92_rejection_ratio': val['metrics']['securing_screen_worst_ratio'],
        'jobs': processes, 'all_owned_workers_reaped': True,
        'total_worker_cpu_s': sum(p['cpu_s'] for p in processes),
        'max_waited_child_rss_bytes': max(p['peak_waited_child_rss_bytes'] for p in processes),
        'no_gpu_or_network': True, 'test_input_bodies_read': False}
    save(FENCE / 'SUMMARY.json', output)
    lines = ['# D132 newly reachable complete samples', '',
        'Two unique sampled complete plans pass the unchanged full checker and the combined production mask with active securing. No production sources were installed.', '',
        '| Sample | Actions | Weighted moves | Checker s | Token replay s | Result |',
        '|---|---:|---:|---:|---:|---|']
    for row in samples:
        lines.append(f"| {row['name']} | {row['actions']} | {row['weighted_nonproductive']:g} | {row['checker_s']:.3f} | {row['mask_s']:.3f} | PASS |")
    lines += ['',
        'SHOWCASE feeder_800 retains the exact D130 frozen input and checked plan. Fresh baseline token replay accepts nine actions and blocks the tenth (`P QZXU0000040 -> 0030184`); the combined candidate accepts all 18 and EOS. This is a directly paired newly reachable complete plan.', '',
        'VAL feeder_1700 retains every one of the 43 committed c/d actions, appends the sole newly legal heat action (`P QZXU0000035 -> 0020784`), and completes at 44 actions. Production mask plus securing accepts the sampled continuation; V4 ballast succeeds in 70.256 s; the unchanged full checker passes. A fresh post-ballast replay also passes with lookahead enabled in the historical order `LookaheadEngine(SecuringEngine(native))` and active securing (calls 3, evaluations 9, unavailable 0, fail_open 0). No prefix was released or reordered.', '',
        'The c/d input and all 43 actions are byte-equivalent as JSON values. This is one unique checked plan covering two identical action-prefix states, not two independent plans or a rerun of the model pilot. Historical rewind blacklists and sampling history were not recreated; no model pass-rate improvement is claimed.', '',
        'The default D92-screened constructor rejects this VAL suffix at design-GM max-racking ratio 1.0985028223317657. That conservative search screen is explicitly not relaxed or declared successful: this sample instead uses the production mask’s necessary securing layer and the unchanged actual-ballast full checker, as authorized for D132 sampling.', '',
        'TRAIN terminal attempt preserves all 24 committed actions, places one further action, then reaches another L1/L2 dead end. It is not cured. The failed VAL D92 constructor and TRAIN partials remain unaccepted; their saved raw plans are diagnostics only. No infeasibility claim follows from bounded search.', '',
        'Native threads = 1; one admitted sample worker at a time; every heavy launch has a fresh ledger/Mach admission and its own reservation, and every worker is reaped. All source hashes and input hashes are rechecked. The runtime epoch is `' + epoch + '`. Reads are only approved TRAIN/VAL and the explicit SHOWCASE; no TEST body, GPU, network or model weights are used.', '',
        'Commands are recorded exactly in each `process-*/process.json`; stage scripts are `run_stage.py`, `worker.py` and `append_val.py`. Each `ledger-*.json` contains estimate/admission/actuals, also appended to fleet history. `fresh_train.py` was prepared but not run, following the parent’s stop-after-VAL instruction.', '',
        'No new numerical approximation or hard-rule change is introduced. The sample-selection shortcut tries a single newly allowed action after a frozen prefix; it is a witness search, not an estimate of model pass rate.', '',
        '| Job | Estimate s | Actual wall s | CPU s | Max waited-child MiB |',
        '|---|---:|---:|---:|---:|']
    for process in processes:
        job = process['job'].removeprefix('process-')
        estimate = read(FENCE / f'ledger-{job}.json')['estimate']['wall_s']
        lines.append(f"| {job} | {estimate:g} | {process['wall_s']:.3f} | {process['cpu_s']:.3f} | {process['peak_waited_child_rss_bytes']/1048576:.2f} |")
    (FENCE / 'REPORT.md').write_text('\n'.join(lines) + '\n')
    print({'passed': True, 'unique_plans': 2, 'jobs': len(processes)})


if __name__ == '__main__':
    main()

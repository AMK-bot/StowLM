"""Independent read-only content audit before the parent seals the batch.

No checker is replaced, numerical value rounded, or report field omitted.
Missing long-guard outputs mean PENDING; no release receipt is then written.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def main():
    inventory = read(HERE / 'checker/inventory.json')
    cases = inventory['cases']
    assert len(cases) == len({row['key'] for row in cases}) == 100
    excluded = inventory['exclusion']
    pending = []
    pairs = []
    for case in cases:
        assert case['split'] in ('train', 'val')
        assert case['input_sha256'] not in excluded['test_input_hashes']
        assert case['instance_id'] not in excluded['test_instance_ids']
        assert digest(case['input_path']) == case['input_sha256']
        assert digest(case['plan_path']) == case['plan_sha256']
        payloads = []
        for arm in ('baseline', 'baseline-cold', 'candidate'):
            folder = HERE / 'checker' / arm / case['key']
            if not (folder / 'record.json').exists():
                pending.append(f'{arm}/{case["key"]}')
                continue
            record = read(folder / 'record.json')
            raw = (folder / 'report.json').read_bytes()
            value = json.loads(raw)
            assert record['passed'] and value['passed']
            assert record['input_sha256'] == case['input_sha256']
            assert record['plan_sha256'] == case['plan_sha256']
            assert record['inputs_immutable'] and record['full_report_bound']
            assert digest(folder / 'report.json') == record['report_sha256']
            assert raw == json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'
            payloads.append(raw)
        if len(payloads) == 3:
            assert payloads[0] == payloads[1] == payloads[2], f'Full report difference: {case["key"]}'
            pairs.append({'key': case['key'], 'report_sha256': sha256(payloads[0]).hexdigest()})
    # Independently re-read every archived query and every final-state field.
    masks = HERE / 'masks'
    traces = read(masks / 'prepared/manifest.json')['traces']
    counts = Counter()
    raw_pins = {}
    for row in traces:
        assert row['split'] in ('train', 'val', 'fixture')
        paths = [masks / arm / row['file'] for arm in ('baseline', 'candidate')]
        before, after = (read(path) for path in paths)
        for path in paths:
            raw_pins[str(path.relative_to(ROOT))] = digest(path)
        assert before['replayed'] and after['replayed']
        assert len(before['events']) == len(after['events'])
        for a, b in zip(before['events'], after['events']):
            assert a['type'] == b['type']
            if a['type'] not in ('prefix', 'engine_probe'):
                if a != b:
                    assert a['type'] == 'final' and a['status'] == 'dead_end' and b['status'] == 'running'
                    assert {k:v for k,v in a.items() if k != 'status'} == {k:v for k,v in b.items() if k != 'status'}
                    counts['terminal_escape'] += 1
                continue
            counts[a['type']] += 1
            key = lambda item: json.dumps(item, sort_keys=True, separators=(',', ':'))
            old, new = {key(x) for x in a['legal']}, {key(x) for x in b['legal']}
            assert not old - new, 'A legal action/token was removed'
            assert {k:v for k,v in a.items() if k not in ('legal','status')} == {k:v for k,v in b.items() if k not in ('legal','status')}
            if new - old:
                heat = a['heat_context']
                assert heat['remaining'] and heat['old_unrepresented_tanks'] and not heat['correct_unrepresented_tanks']
                counts['changed_queries'] += 1
                counts['additions'] += len(new - old)
            else:
                assert a['legal'] == b['legal']  # Stable ordering too.
            if a.get('status') != b.get('status'):
                assert new - old and a.get('status') == 'dead_end' and b.get('status') == 'running'
    assert len(traces) == 214
    assert counts == Counter(prefix=25454, engine_probe=618, terminal_escape=1,
                             changed_queries=23, additions=29)
    samples = read(HERE / 'samples/SUMMARY.json')
    assert samples['passed'] and samples['all_owned_workers_reaped']
    assert samples['unique_checked_newly_reachable_plans'] == len(samples['samples']) == 2
    for sample in samples['samples']:
        folder = HERE / 'samples' / sample['name']
        for name in ('instance', 'plan', 'report'):
            field = 'input_sha256' if name == 'instance' else name + '_sha256'
            assert digest(folder / (name + '.json')) == sample[field]
        assert read(folder / 'report.json')['passed']
        assert sample['checker_passed'] and sample['production_mask_securing_passed']
        replay = read(folder / ('replay-candidate-lookahead.json' if sample['lookahead'] else 'replay-candidate.json'))
        assert replay['passed'] and replay['actions_replayed'] == sample['actions']
        assert replay['decoder_status'] == 'complete'
        assert replay['counters']['securing_active']
        assert replay['counters']['unavailable'] == replay['counters']['fail_open'] == 0
    if pending:
        print(json.dumps({'status':'PENDING', 'threeway_complete':len(pairs),
                          'missing_checker_records':len(pending), 'mask_counts':counts,
                          'checked_complete_samples':2}))
        return
    value = {'status':'PASS', 'threeway_complete':100, 'pairs':pairs,
             'mask_traces':214, 'mask_counts':counts, 'mask_raw_artifact_sha256':raw_pins,
             'checked_complete_samples':2, 'no_report_fields_omitted':True,
             'production_installed':False}
    destination = HERE / 'independent-results.json'
    destination.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'status':'PASS','output':str(destination),'sha256':digest(destination)}))


if __name__ == '__main__':
    main()

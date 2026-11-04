"""Audit the reverse T=1 helper's exclusive publications after its recorded exit."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARM = HERE.parent
ROOT = ARM.parents[3]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def main():
    receipt = read(HERE / 'receipt.json')['binding']
    done, process, exited, priority = [read(HERE / name) for name in
                                     ('DONE.json', 'process.json', 'worker-exit.json', 'worker-priority.json')]
    assert done['stage'] == 'complete'
    assert process['pid'] == done['pid'] == exited['pid'] == priority['pid'] and exited['exit_code'] == 0
    assert process['workers'] == 1 and process['native_threads'] == 1 and priority['priority'] == 0
    assert sha(ARM / 'reverse_sampler.py') == receipt['helper_sha256']
    assert sha(ARM / 'frozen-source/fitted.json') == receipt['weights_sha256']
    assert sha(ARM / 'small/manifest.json') == receipt['primary_manifest_sha256']
    for rel, digest in receipt['primary_code'].items():
        assert sha(ROOT / rel) == digest
    for rel, digest in receipt['inputs'].items():
        assert rel.startswith('experiments/mvp/corpus/val/small/') and 'test' not in rel
        assert sha(ROOT / rel) == digest
    ids = {rel: read(ROOT / rel)['id'] for rel in receipt['inputs']}
    events = [json.loads(line) for line in (HERE / 'events.jsonl').read_text().splitlines()]
    published = [event for event in events if event['event'] == 'published']
    assert len(published) == done['published']
    assert len({(event['instance_id'], event['sample']) for event in published}) == len(published)
    assert len({event['target'] for event in published}) == len(published)
    complete = clear = 0
    maps = set()
    for event in published:
        target = ROOT / event['target']
        assert sha(target) == event['sample_sha256'], f'published sample changed: {target}'
        row = read(target)
        assert row['instance_id'] == event['instance_id'] == target.parent.name
        assert row['sample'] == event['sample'] and target.name == f"sample-{row['sample']:02d}.json"
        assert row['input'] in receipt['inputs']
        assert ids[row['input']] == row['instance_id']
        metadata = row['metadata']
        assert metadata['weights_sha256'] == receipt['weights_sha256']
        assert metadata['greedy'] is False and metadata['temperature'] == 1
        assert metadata['seed'] == row['sample'] and 0 <= row['sample'] < 16
        base_keys = {'instance_id', 'input', 'sample', 'metadata', 'plan'}
        if row['plan'] is None:
            assert event['complete'] is False and metadata['status'] == 'dead_end' and set(row) == base_keys
        else:
            assert event['complete'] is True and metadata['status'] == 'complete'
            assert set(row) == base_keys | {'prepared', 'chosen_plan', 'signature'}
            assert {'cost', 'original_cost', 'gate_clear', 'gate_rules', 'compaction', 'compacted'} <= set(row['prepared'])
            assert row['chosen_plan']['instance_id'] == row['instance_id']
            signature = hashlib.sha256(json.dumps(row['chosen_plan']['moves'], sort_keys=True).encode()).hexdigest()
            assert signature == row['signature']
            complete += 1
            clear += row['prepared']['gate_clear'] is True
        maps.add(row['instance_id'])
    stop_guard = None
    if done['reason'] == 'three-map separation reached':
        stops = [event for event in events if event['event'] in ('stop_admission', 'discard_gap_closed')]
        stop = stops[-1]
        indices = {ids[rel]: index for index, rel in enumerate(sorted(receipt['inputs']))}
        reverse_index = stop['reverse_index'] if 'reverse_index' in stop else indices[stop['instance_id']]
        assert stop['allowed'] is False and stop['forward_index'] >= reverse_index - 3
        stop_guard = dict(forward_index=stop['forward_index'], reverse_index=reverse_index,
                          gap=reverse_index - stop['forward_index'])
    result = dict(verified=True, verified_utc=datetime.now(timezone.utc).isoformat(), pid=process['pid'],
                  published=len(published), maps_with_publications=len(maps), complete=complete, gate_clear=clear,
                  sample_file_hashes_verified=len(published), exact_primary_schema_verified=len(published),
                  chosen_signatures_verified=complete, priority=priority['priority'],
                  skipped=done['skipped'], discarded=done['discarded'], stop_reason=done['reason'], stop_guard=stop_guard,
                  weights_sha256=receipt['weights_sha256'], helper_sha256=receipt['helper_sha256'])
    temporary = HERE / 'verification.json.tmp'
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    temporary.replace(HERE / 'verification.json')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()

"""Compare fresh score.py arms without mistaking construction for a pass."""
from pathlib import Path
import hashlib
import json

HERE = Path(__file__).resolve().parent


def rows(folder):
    return {r['instance_id']: r for p in folder.glob('*.json')
            if p.name != 'summary.json' and 'instance_id' in (r := json.loads(p.read_text()))}


def compare(reference, accelerated):
    old, new = rows(reference), rows(accelerated)
    assert old.keys() == new.keys(), (old.keys(), new.keys())
    pairs = []
    for identity, a in old.items():
        b = new[identity]
        am, bm = a.get('method_metadata', {}), b.get('method_metadata', {})
        decision_keys = ('accepted', 'best', 'evaluations', 'fast', 'fast_fallback',
                         'improvements', 'iterations', 'mask_rejected', 'masks',
                         'replay', 'unique')
        decisions_equal = (all(am['search'].get(k) == bm.get('search', {}).get(k)
                               for k in decision_keys) if 'search' in am else None)
        pairs.append(dict(instance=identity, reference_pass=a['passed'], fast_pass=b['passed'],
                          reference_lifts=a['lifts'], fast_lifts=b['lifts'],
                          reference_search_s=a['propose_s'], fast_search_s=b['propose_s'],
                          reference_wall_s=a['wall_s'], fast_wall_s=b['wall_s'],
                          candidate_hash_equal=(am['candidate_sha256'] == bm.get('candidate_sha256')
                                                if 'candidate_sha256' in am else None),
                          search_decisions_equal=decisions_equal,
                          reference_complete=bool(am.get('complete_replay_legal_stows', 0)),
                          fast_complete=bool(bm.get('complete_replay_legal_stows', 0)),
                          reference_iterations=am.get('search', {}).get('iterations'),
                          fast_iterations=bm.get('search', {}).get('iterations')))
    mutual = [r for r in pairs if r['reference_pass'] and r['fast_pass']]
    return dict(pairs=pairs, maps=len(pairs), mutual_passes=len(mutual),
                reference_passes=sum(r['reference_pass'] for r in pairs),
                fast_passes=sum(r['fast_pass'] for r in pairs),
                reference_complete_maps=sum(r['reference_complete'] for r in pairs),
                fast_complete_maps=sum(r['fast_complete'] for r in pairs),
                paired_reference_mean=(sum(r['reference_lifts'] for r in mutual)/len(mutual) if mutual else None),
                paired_fast_mean=(sum(r['fast_lifts'] for r in mutual)/len(mutual) if mutual else None),
                search_speedup=sum(r['reference_search_s'] for r in pairs)/sum(r['fast_search_s'] for r in pairs),
                total_speedup=sum(r['reference_wall_s'] for r in pairs)/sum(r['fast_wall_s'] for r in pairs),
                no_worse=all((not r['reference_pass'] or r['fast_pass']) and
                    (not r['reference_pass'] or r['fast_lifts'] <= r['reference_lifts']) for r in pairs))


if __name__ == '__main__':
    result = {}
    for kind in ('search', 'ladder'):
        reference, accelerated = (HERE/'results'/f'{kind}-{arm}' for arm in ('reference', 'fast'))
        if (reference/'summary.json').exists() and (accelerated/'summary.json').exists():
            result[kind] = compare(reference, accelerated)
    result['source_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in HERE.glob('*.py')}
    (HERE/'results/comparison.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k: {a: b for a, b in v.items() if a != 'pairs'}
                      for k, v in result.items() if k != 'source_sha256'}, indent=2))

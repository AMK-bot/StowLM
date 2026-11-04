"""Copy only the approved saved TRAIN/VAL100 blobs, or pin a ready runtime."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from runtime import FENCE, LIVE_ROOT, read, save, sha, source_pins, object_sha


def inventory():
    origin = LIVE_ROOT/'experiments/mvp/speed/checker/d114-followup/guard/inventory.json'
    prior = read(origin)
    cases = []
    forbidden_hash = set(prior['exclusion']['test_input_hashes'])
    forbidden_id = set(prior['exclusion']['test_instance_ids'])
    for original in prior['cases']:
        case = dict(original)
        assert case['split'] in ('train', 'val')
        assert case['corpus_path'].startswith(case['split']+'/')
        assert case['input_sha256'] not in forbidden_hash and case['instance_id'] not in forbidden_id
        for kind in ('input', 'plan'):
            source = Path(case[kind+'_path'])
            assert sha(source) == case[kind+'_sha256']
            target = FENCE/'blobs'/(case[kind+'_sha256']+'.json')
            target.parent.mkdir(exist_ok=True)
            if target.exists():
                assert sha(target) == case[kind+'_sha256']
            else:
                target.write_bytes(source.read_bytes())
            case[kind+'_path'] = str(target)
        cases.append(case)
    assert len(cases) == len({c['key'] for c in cases}) == len({c['input_sha256'] for c in cases}) == 100
    result = {'format':'d132-full-report-guard-selection-v1', 'created_utc':datetime.now(timezone.utc).isoformat(),
        'origin':str(origin), 'origin_sha256':sha(origin), 'cases':cases, 'exclusion':prior['exclusion'],
        'counts':dict(Counter('/'.join((c['split'],c['rung'],c['ship'])) for c in cases)),
        'policy':'Exact saved TRAIN/VAL bodies; no regeneration; historical reports never used as baseline; '
                 'balanced D114100 selection covers all3ships at tiny/small rather than narrower epoch4 selection'}
    save(FENCE/'inventory.json',result)
    print({'inventory_sha256':sha(FENCE/'inventory.json'),'count':len(cases),'counts':result['counts']})


def pin(root):
    root=root.resolve()
    files=source_pins(root)
    target=FENCE/'runtime-pins'/(root.name+'.json')
    value={'root':str(root),'files':files,'source_epoch_sha256':object_sha(files)}
    if target.exists():
        assert read(target)==value,'Immutable ready runtime changed; new arm needed'
    else:
        save(target,value)
    print({'runtime':str(root),'pin':str(target),'epoch':value['source_epoch_sha256']})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('inventory','pin'))
    p.add_argument('--runtime',type=Path)
    a=p.parse_args()
    inventory() if a.mode=='inventory' else pin(a.runtime)

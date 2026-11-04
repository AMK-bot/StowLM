"""D185 cohort adapter: consume the blockrule freeze verbatim, never draw maps.

Only the atomic final ``blockrule/cohort-medium.json`` is eligible. Generation
requests and progress files cannot substitute for completed current-epoch maps.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
              'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'HIGHS_THREADS'):
    os.environ[_name] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
BLOCKRULE = HERE.parent / 'blockrule'
MEDIUM_SOURCE = BLOCKRULE / 'cohort-medium.json'
DEFAULT_MANIFEST = HERE / 'maps_d185.json'
FORMAT = 'D185-proposer-frozen-train-v1'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _relative_train_path(value, *, size):
    path = (ROOT / value).resolve()
    relative = path.relative_to(ROOT)
    parts = relative.parts
    if any(part in {'val', 'validation', 'test', 'showcase', 'archive'} for part in parts):
        raise ValueError('D185 accepts current train inputs only')
    if size == 'small':
        prefix = ('experiments', 'mvp', 'corpus', 'train', 'small')
    elif size == 'medium':
        prefix = ('experiments', 'mvp', 'strategy', 'blockrule', 'maps', 'train', 'medium')
    else:
        raise ValueError('D185 cohort permits small and medium only')
    if parts[:len(prefix)] != prefix or path.suffix != '.json' or '.audit.' in path.name:
        raise ValueError(f'input is outside the registered {size} train root')
    return path, str(relative)


def _verify_row(row):
    if row.get('split') != 'train':
        raise ValueError('D185 cohort must explicitly declare train split')
    path, relative = _relative_train_path(row['input'], size=row['size'])
    if sha(path) != row.get('sha256', row.get('input_sha256')):
        raise ValueError(f'frozen input SHA mismatch: {relative}')
    if row['size'] == 'medium':
        if (row.get('release_epoch') != 'D185-current-epoch'
                or row.get('source_binding_status') != 'current_epoch_generated'
                or row.get('generator_gate_passed') is not True):
            raise ValueError('medium input lacks current-epoch producer provenance')
        audit = (ROOT / row['audit']).resolve()
        expected_audit = path.with_suffix('.audit.json')
        if audit != expected_audit or sha(audit) != row.get('audit_sha256'):
            raise ValueError('medium producer audit path/hash mismatch')
        instance = json.loads(path.read_text())
        if instance.get('size_class') != 'medium':
            raise ValueError('medium manifest/instance size mismatch')
    return dict(row, input=relative)


def freeze(*, target=DEFAULT_MANIFEST, small_source=None, medium_source=None):
    """Freeze unchanged old small inputs + exactly the externally frozen medium.

    Read no solver results. A failed/incomplete generator is not replaced and
    cannot lead to a partial cohort. Existing frozen files are never overwritten.
    """
    target = Path(target)
    if target.exists():
        raise ValueError('D185 cohort already frozen; refusing to overwrite')
    small_source = Path(small_source or HERE / 'maps.json')
    medium_source = Path(medium_source or MEDIUM_SOURCE)
    if medium_source.resolve() != (BLOCKRULE / 'cohort-medium.json').resolve():
        raise ValueError('only blockrule/cohort-medium.json is the final medium freeze')
    if not medium_source.is_file():
        raise FileNotFoundError('waiting for blockrule/cohort-medium.json; partial lists are forbidden')
    medium_bytes = medium_source.read_bytes()
    source = json.loads(medium_bytes)
    if source.get('format') != 'D185-blockrule-frozen-medium-train-v1':
        raise ValueError('not the final blockrule current-epoch medium cohort')
    requests_path = BLOCKRULE / 'cohort-requests.json'
    if sha(requests_path) != source.get('request_sha256'):
        raise ValueError('blockrule frozen generation requests changed')
    requests = json.loads(requests_path.read_text())
    specs = requests.get('specs', [])
    rows = source.get('maps', [])
    if not rows or len(rows) != len(specs) or source.get('counts') != {'medium': len(rows)}:
        raise ValueError('blockrule medium generation is incomplete')
    expected = {(r['vessel'], r['generator'], r['seed']) for r in specs if r['split'] == 'train' and r['rung'] == 'medium'}
    actual = {(r['ship'], r['family'], r['seed']) for r in rows}
    if actual != expected or len(actual) != len(rows):
        raise ValueError('medium rows differ from the fixed blockrule request list')
    small_bytes = small_source.read_bytes()
    previous = json.loads(small_bytes)
    small = [dict(row) for row in previous['maps'] if row['size'] == 'small']
    if len(small) != 20:
        raise ValueError('expected unchanged 20-map small proposer cohort')
    maps = [_verify_row(row) for row in [*small, *rows]]
    if len({row['input'] for row in maps}) != len(maps) or len({row['sha256'] for row in maps}) != len(maps):
        raise ValueError('duplicate cohort paths or content hashes')
    value = dict(format=FORMAT, protocol='D185-step2-v1',
        frozen_at_utc=datetime.now(timezone.utc).isoformat(), frozen_before_proposer_runs=True,
        selection='retain original 20 small; consume every final blockrule medium row in frozen order; no outcomes read',
        medium_source=dict(input=str(medium_source.relative_to(ROOT)),
            sha256=hashlib.sha256(medium_bytes).hexdigest(), request_sha256=source['request_sha256'],
            frozen_at_utc=source['frozen_at_utc']),
        small_source=dict(input=str(small_source.relative_to(ROOT)), sha256=hashlib.sha256(small_bytes).hexdigest()),
        counts=dict(Counter(row['size'] for row in maps)), maps=maps)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create: concurrent preparations cannot replace a drawn cohort.
    with target.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
    return value


def manifest_jobs(path=DEFAULT_MANIFEST):
    """Validate the D185 freeze before any arm, including plan-only preflight."""
    path = Path(path)
    manifest = json.loads(path.read_text())
    if (manifest.get('format') != FORMAT or manifest.get('protocol') != 'D185-step2-v1'
            or manifest.get('frozen_before_proposer_runs') is not True):
        raise ValueError('a frozen D185 proposer cohort is required')
    medium_path = BLOCKRULE / 'cohort-medium.json'
    medium_bytes = medium_path.read_bytes()
    medium = json.loads(medium_bytes)
    binding = manifest.get('medium_source', {})
    if (binding.get('input') != str(medium_path.relative_to(ROOT))
            or binding.get('sha256') != hashlib.sha256(medium_bytes).hexdigest()
            or binding.get('request_sha256') != medium.get('request_sha256')
            or medium.get('request_sha256') != sha(BLOCKRULE / 'cohort-requests.json')):
        raise ValueError('external medium freeze/request binding changed')
    small_path = HERE / 'maps.json'
    small_bytes = small_path.read_bytes()
    small_binding = manifest.get('small_source', {})
    if (small_binding.get('input') != str(small_path.relative_to(ROOT))
            or small_binding.get('sha256') != hashlib.sha256(small_bytes).hexdigest()):
        raise ValueError('original small freeze binding changed')
    canonical = [row for row in json.loads(small_bytes)['maps'] if row['size'] == 'small'] + medium['maps']
    if manifest['maps'] != canonical:
        raise ValueError('cohort rows/order differ from the exact registered source freezes')
    jobs, ids = [], set()
    for raw in manifest['maps']:
        row = _verify_row(raw)
        key = row['map_id']
        if Path(key).name != key or key in ids:
            raise ValueError('invalid/duplicate map_id')
        ids.add(key)
        jobs.append(dict(row, input_sha256=row['sha256']))
    if not jobs or manifest['counts'] != dict(Counter(row['size'] for row in jobs)):
        raise ValueError('D185 frozen cohort count mismatch')
    return jobs


def measure_tokens(path=DEFAULT_MANIFEST):
    """CPU raw-text sizing of this exact train cohort; never load model weights."""
    sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
    from tokenizers import Tokenizer
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1
    from experiments.mvp.strategy.headroom.layout import unrestricted_layout, BlockModel
    from experiments.mvp.strategy.proposer.format import format_layout
    from experiments.mvp.strategy.proposer.prompt import build_prompt
    from experiments.mvp.strategy.proposer.cost import preparation_budget_plan
    from experiments.mvp.strategy.proposer.registration import GENERATION_SETTINGS
    tokenizer_path = ROOT / 'models/glimmer-30b-4bit/tokenizer.json'
    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    rows = []
    for job in manifest_jobs(path):
        with rule_freeze_v1(enabled=False):
            instance = load_instance(ROOT / job['input'])
            prompt = build_prompt(instance)
            output = format_layout(unrestricted_layout(instance), instance)
        rows.append(dict(map_id=job['map_id'], size=job['size'], input_sha256=job['input_sha256'],
            loads=len(instance.load_list), blocks=len(BlockModel.from_instance(instance).blocks),
            prompt=dict(characters=len(prompt), glimmer_raw_tokens=len(tokenizer.encode(prompt, add_special_tokens=False).ids)),
            all_blocks_layout=dict(characters=len(output), glimmer_raw_tokens=len(tokenizer.encode(output, add_special_tokens=False).ids))))
    result = dict(format='D185-proposer-raw-token-sizes-v1', manifest_sha256=sha(path),
        measurements=rows, tokenizer=dict(path=str(tokenizer_path.relative_to(ROOT)),sha256=sha(tokenizer_path),
            method='local tokenizers CPU encode(add_special_tokens=False)',chat_template_included=False,
            spark='proxy only; actual Spark tokenization, chat wrappers and reasoning unmeasured'),
        calls_made=False, model_weights_loaded=False)
    result['budget_plan'] = preparation_budget_plan(result, output_token_cap=GENERATION_SETTINGS['max_output_tokens'])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze', action='store_true')
    parser.add_argument('--wait', action='store_true', help='Poll only for the final external medium freeze, then freeze locally')
    parser.add_argument('--token-sizes', type=Path, help='Measure frozen cohort without inference')
    args = parser.parse_args(argv)
    if args.token_sizes:
        result = measure_tokens()
        from experiments.mvp.strategy.proposer.run import atomic_json
        args.token_sizes.resolve().relative_to(HERE)
        atomic_json(args.token_sizes, result)
        print(json.dumps({'token_sizes': str(args.token_sizes), 'maps': len(result['measurements'])}))
    elif args.wait:
        while not MEDIUM_SOURCE.is_file():
            time.sleep(10)
        result = freeze()
        print(json.dumps({'manifest': str(DEFAULT_MANIFEST), 'counts': result['counts']}))
    elif args.freeze:
        result = freeze()
        print(json.dumps({'manifest': str(DEFAULT_MANIFEST), 'counts': result['counts']}))
    else:
        print(json.dumps({'verified_maps': len(manifest_jobs())}))


if __name__ == '__main__':
    sys.exit(main())

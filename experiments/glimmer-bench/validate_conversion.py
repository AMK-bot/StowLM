"""Queued local GPU check: teacher-forced source versus converted logits.

Run only after a GPU reservation; never downloads (HF_HUB_OFFLINE enforced).
Reports evidence rather than inventing an uncalibrated quantization tolerance.
Usage: uv run python experiments/glimmer-bench/validate_conversion.py MODEL OUT.json
"""
import json
import os
import re
import sys
from pathlib import Path

PROMPTS = (
    'The capital of Thailand is',
    'A container ship visits ports A, B, and C in that order. A box for port B should',
    'Two containers weigh 10 and 20 tonnes. Their combined weight is',
    'A restow means unloading and reloading a container to reach another container. Explain why',
)


def resolve_cached_snapshot(repo_id, revision, cache_dir=None):
    """Inspect a pinned HF cache locally; never call download or Hub APIs.

    Standard safetensors indexes give tensor bytes, not on-disk shard sizes.
    Require the pinned cached tree's per-file sizes instead of comparing their
    total_size to shard lengths (which also include safetensors headers).
    """
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Offline snapshot requires a full pinned commit hash')
    if cache_dir is None:
        from huggingface_hub.constants import HF_HUB_CACHE
        cache_dir = HF_HUB_CACHE
    root = Path(cache_dir) / ('models--' + repo_id.replace('/', '--'))
    snapshot = root / 'snapshots' / revision

    def local_path(name):
        if not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError(f'Offline snapshot: unsafe cached file name {name!r}')
        return snapshot / name

    def read_json(path):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError(f'Offline snapshot: cannot read {path}: {exc}') from exc

    if not snapshot.is_dir():
        raise ValueError(f'Offline snapshot: pinned directory missing: {snapshot}; no download attempted')
    tree = read_json(root / 'trees' / f'{revision}.json')
    entries = tree.get('files') if isinstance(tree, dict) else None
    if not isinstance(entries, dict):
        raise ValueError('Offline snapshot: cached tree has no file metadata; cannot verify shard sizes')
    required = {'config.json', 'tokenizer.json', 'tokenizer_config.json', 'chat_template.jinja',
                'model.safetensors.index.json'}
    for name in sorted(required):
        if not local_path(name).is_file() or local_path(name).stat().st_size == 0:
            raise ValueError(f'Offline snapshot: missing required file {name}')
    index = read_json(snapshot / 'model.safetensors.index.json')
    weights = index.get('weight_map') if isinstance(index, dict) else None
    if not isinstance(weights, dict) or not weights:
        raise ValueError('Offline snapshot: safetensors index has no weight_map')
    shards = set()
    for name in weights.values():
        path = local_path(name)
        if path.suffix != '.safetensors':
            raise ValueError(f'Offline snapshot: invalid weight shard name {name!r}')
        shards.add(name)
    verified = {}
    for name in sorted(shards):
        path = local_path(name)
        if not path.is_file():
            raise ValueError(f'Offline snapshot: missing required file {name}')
        metadata = entries.get(name, {})
        sizes = [metadata[key] for key in ('size', 'lfs_size') if key in metadata] if isinstance(metadata, dict) else []
        if not sizes or any(type(size) is not int or size <= 0 for size in sizes):
            raise ValueError(f'Offline snapshot: no trusted cached size for shard {name}')
        actual = path.stat().st_size
        if any(size != actual for size in sizes):
            raise ValueError(f'Offline snapshot: shard size mismatch for {name}: actual={actual}, expected={sizes}')
        verified[name] = actual
    missing = []
    for name in sorted(entries):
        if local_path(name).is_file():
            continue
        # Only documentation/evaluation metadata may be absent. Unknown missing
        # assets fail closed, including tokenizer extras and auxiliary weights.
        if name in {'.gitattributes', 'README.md', 'LICENSE', 'USAGE_POLICY.md'} or name.startswith('.eval_results/'):
            missing.append(name)
        else:
            raise ValueError(f'Offline snapshot: missing required file {name}')
    return snapshot, dict(missing_metadata_files=missing, verified_shard_sizes=verified)


def main():
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    from convert import SOURCE_REPO, SOURCE_REVISION, conversion_provenance

    snapshot, cache_evidence = resolve_cached_snapshot(SOURCE_REPO, SOURCE_REVISION)
    import gc
    import mlx.core as mx
    import numpy as np
    from mlx_lm import load
    from mlx_lm.models import muse_glimmer

    original = muse_glimmer.Model.sanitize

    def sanitize(self, weights):
        return original(self, {k: v for k, v in weights.items() if not k.startswith('model.vision_')})

    muse_glimmer.Model.sanitize = sanitize
    source, tokenizer = load(str(snapshot))
    ids = [tokenizer.encode(prompt) for prompt in PROMPTS]
    reference = []
    for tokens in ids:
        logits = source(mx.array([tokens]))[0].astype(mx.float32)
        mx.eval(logits)
        reference.append(np.array(logits))
    del source, tokenizer, logits
    gc.collect()
    mx.clear_cache()
    candidate, tokenizer = load(sys.argv[1])
    if [tokenizer.encode(prompt) for prompt in PROMPTS] != ids:
        raise ValueError('Source and converted tokenizers disagree on fixed prompts')
    rows = []
    for prompt, tokens, ref in zip(PROMPTS, ids, reference):
        actual = candidate(mx.array([tokens]))[0].astype(mx.float32)
        mx.eval(actual)
        actual = np.array(actual)
        if actual.shape != ref.shape or not (np.isfinite(actual).all() and np.isfinite(ref).all()):
            raise ValueError('Invalid logits shape or non-finite values')
        # KL is the information lost by using candidate probabilities for source tokens.
        def log_softmax(x):
            x = x - x.max(axis=-1, keepdims=True)
            return x - np.log(np.exp(x).sum(axis=-1, keepdims=True))
        p, q = log_softmax(ref), log_softmax(actual)
        kl = (np.exp(p) * (p - q)).sum(axis=-1)
        rows.append(dict(prompt=prompt, tokens=len(tokens), mean_kl_nats=float(kl.mean()),
                         max_kl_nats=float(kl.max()),
                         top1_agreement=float((ref.argmax(-1) == actual.argmax(-1)).mean()),
                         logit_rmse=float(np.sqrt(np.square(ref - actual).mean()))))
    result = dict(candidate=str(Path(sys.argv[1]).resolve()), source=conversion_provenance(4),
                  source_snapshot=str(snapshot), **cache_evidence,
                  method='same-token teacher forcing, all prompt positions, float32 metrics',
                  tolerance='UNSET: calibrate with trusted independent conversion before acceptance', rows=rows)
    Path(sys.argv[2]).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

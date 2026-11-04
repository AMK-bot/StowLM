"""Pre-declared illustrative case selection; no solving or outcome-based selection."""
from pathlib import Path
import hashlib

from app.identity import instance_content_digest
from app.inputs import _files, _json, _full_instance
from stowage.io import load_instance


def select_storyboard(root, *, split, rung, rule, split_manifest=None):
    if split not in {'train', 'val', 'test'} or rung not in {'tiny', 'small', 'medium', 'large', 'full'}:
        raise ValueError('Storyboard requires a declared split and rung')
    if rule != 'first-dg-direct-delivery':
        raise ValueError('Unknown storyboard selection rule')
    matches = []
    root = Path(root)
    frozen = (root / 'manifest.json').is_file()
    if frozen:
        from eval.harness_inventory import select_frozen_instances
        from eval.splits_epoch import load_bound_instance
        split_manifest = Path(split_manifest) if split_manifest else Path(__file__).resolve().parents[1] / 'experiments/splits/v2/manifest.json'
        selected = select_frozen_instances(root, split_manifest=split_manifest, split=split, rungs=(rung,))
        paths = [(Path(row['path']), row['input_sha256']) for row in selected.rows]
    else:
        paths = [(path, None) for path in _files(root)]
    for path, checksum in paths:
        if path.name.endswith(('.audit.json', '.plan.json', '.report.json')):
            continue
        raw = _json(path)
        if not _full_instance(raw) or raw.get('size_class') != rung or (not frozen and raw.get('family', {}).get('split') != split):
            continue
        instance = load_bound_instance(path, checksum) if frozen else load_instance(path)
        if any(box.dg is not None for box in instance.containers) and any(box.direct_delivery for box in instance.containers):
            matches.append((instance.id, str(path), instance_content_digest(instance)))
    if not matches:
        raise ValueError(f'No instance matches declared storyboard rule: {split}/{rung}/{rule}')
    matches.sort()
    identity, source, digest = matches[0]
    if any(cid == identity and other_digest != digest for cid, _, other_digest in matches):
        raise ValueError('Conflicting contents share the first storyboard instance id')
    return Path(source), dict(label='1 instance, illustrative', rule=rule, split=split, rung=rung,
        split_source='frozen inventory' if frozen else 'instance family declaration',
        instance_id=identity, instance_content_sha256=digest,
        description='First instance in id order with ≥ 1 DG box and ≥ 1 direct-delivery box; no result-based selection')

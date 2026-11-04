"""Render and verify the activation proposal. This program never applies it."""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import difflib
from hashlib import sha256
import json
from pathlib import Path
import tomllib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def snapshot(path):
    data = Path(path).read_bytes()
    return data, sha256(data).hexdigest()


def safe_path(root, relative):
    relative = Path(relative)
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError(f'path escapes repository: {relative}')
    return path


def render(root, plan):
    """All replacement checks and digests use the same original byte read."""
    grouped = defaultdict(list)
    for change in plan['changes']:
        if not change['path'].startswith('src/'):
            raise ValueError('activation proposal may target src/ files only')
        grouped[change['path']].append(change)
    originals, replacements, pins, patches = {}, {}, {}, []
    for relative, changes in sorted(grouped.items()):
        path = safe_path(root, relative)
        data, digest = snapshot(path)
        original = data.decode('utf-8')
        updated = original
        for change in changes:
            old, new = change['old'], change['new']
            if not old or old == new or updated.count(old) != 1:
                raise ValueError(f'preimage is not unique/current: {relative}: {change["label"]}')
            updated = updated.replace(old, new, 1)
        if path.suffix == '.py':
            ast.parse(updated, filename=relative)
        elif path.suffix == '.toml':
            tomllib.loads(updated)
        elif path.suffix == '.json':
            json.loads(updated)
        originals[relative] = data
        replacements[relative] = updated.encode()
        pins[relative] = dict(before_sha256=digest, after_sha256=sha256(updated.encode()).hexdigest())
        patches.extend(difflib.unified_diff(original.splitlines(keepends=True),
            updated.splitlines(keepends=True), fromfile='a/'+relative, tofile='b/'+relative))
    # Staged identities matter even when the patch has no edit for them.
    for item in plan.get('already_current', []):
        relative = item['path']
        if relative not in pins:
            data, digest = snapshot(safe_path(root, relative))
            originals[relative] = data
            pins[relative] = dict(before_sha256=digest, after_sha256=digest, unchanged=True)
    for relative, original in originals.items():
        if snapshot(safe_path(root, relative))[0] != original:
            raise ValueError(f'source changed while preparing patch: {relative}')
    return ''.join(patches).encode(), pins, replacements


def verify(root, directory=HERE):
    plan_data, plan_sha = snapshot(directory/'version-plan.json')
    pin_data, _ = snapshot(directory/'activation-preimages.json')
    pin = json.loads(pin_data)
    patch, patch_sha = snapshot(directory/'activation.patch')
    if pin['plan_sha256'] != plan_sha or pin['patch_sha256'] != patch_sha:
        raise ValueError('patch or version plan changed after preparation')
    expected, files, _ = render(root, json.loads(plan_data))
    if patch != expected or files != pin['files']:
        raise ValueError('patch preimages or staged identities drifted; regenerate and review')
    return dict(passed=True, applied=False, patch_sha256=patch_sha,
        files=len(files), modified_files=sum(not row.get('unchanged') for row in files.values()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    if args.check:
        print(json.dumps(verify(ROOT), sort_keys=True))
        return 0
    plan_data, plan_sha = snapshot(HERE/'version-plan.json')
    plan = json.loads(plan_data)
    patch, files, _ = render(ROOT, plan)
    pin = dict(schema_version=1, applied=False, evidence_eligible=False,
        plan_sha256=plan_sha, patch_sha256=sha256(patch).hexdigest(), files=files,
        unresolved_numeric_proposals=plan.get('unresolved_numeric_proposals', []))
    (HERE/'activation.patch').write_bytes(patch)
    (HERE/'activation-preimages.json').write_text(json.dumps(pin, indent=2, sort_keys=True)+'\n')
    print(json.dumps(verify(ROOT), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

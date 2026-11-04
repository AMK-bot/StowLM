"""Archive the exact fixed v2 runtime without mutating active source or results."""
import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from archive_assets import verify_offset_dependencies

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(target):
    target = Path(target).resolve()
    if target.exists():
        raise FileExistsError(f"Create a NEW archive epoch; existing archive is immutable: {target}")
    freeze_path = HERE / 'runtime-freeze-v2.json'
    freeze = json.loads(freeze_path.read_text())
    expected = dict(freeze['lookahead_runtime_sources'])
    extra = [HERE/'worker.py', HERE/'paired_v2.py', HERE/'controller.py', freeze_path,
             ROOT/'models/glimmer-30b-4bit/tokenizer.json', ROOT/'pyproject.toml', ROOT/'uv.lock']
    extra.append(HERE/'archive_assets.py')
    for name in verify_offset_dependencies(ROOT):
        extra.append(ROOT/name)
    for path in extra:
        expected[str(path.relative_to(ROOT))] = sha(path)
    assert expected[str((HERE/'worker.py').relative_to(ROOT))] == freeze['worker_sha256']
    assert expected[str((HERE/'paired_v2.py').relative_to(ROOT))] == freeze['paired_worker_sha256']
    assert expected[str((HERE/'controller.py').relative_to(ROOT))] == freeze['controller_sha256']
    target.mkdir(parents=True)
    prior = target / 'archive-manifest.json'
    if prior.exists():
        assert json.loads(prior.read_text())['files'] == expected, 'Existing immutable archive differs'
    for name, digest in expected.items():
        source = ROOT / name
        assert sha(source) == digest, f'Live source differs from fixed epoch: {name}'
        dest = target / name
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        assert sha(dest) == digest, f'Archived bytes differ: {name}'
    value = dict(created_utc=datetime.now(timezone.utc).isoformat(), files=expected,
                 files_verified=len(expected), worker_sha_pinned_prelaunch=True,
                 controller_sha_pinned_prelaunch=True,
                 policy='All fixed runtime sources plus actual driver/helper/controller, real CPU tokenizer, dependency manifests. No source exclusions; archive is independent of later live production changes.')
    if not prior.exists():
        prior.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')
    print(json.dumps(dict(files_verified=len(expected), archive=str(target), manifest_sha256=sha(prior))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, required=True)
    main(parser.parse_args().target)

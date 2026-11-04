"""Plain film hash inventory (D295): app/ and experiments/mvp/ui/ (no caches, frames or bytecode), the recorded
shots, and the evidence files the film cites. It's a record of what the film was built from, not a launch guard.

Run from the repository root:
    uv run --offline --no-sync python -I experiments/mvp/video/film_inventory.py --out experiments/mvp/video/film-inventory-YYYYMMDD.json \
        --decision "D3xx: why it was refreshed"
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TREES = ('app', 'experiments/mvp/ui')
SKIP_PARTS = {'__pycache__', 'cache', 'frames', 'video-draft', '.pytest_cache', 'node_modules'}
SKIP_SUFFIXES = {'.pyc', '.pyo'}
SHOTS = 'experiments/mvp/ui/video-draft/v6'
EVIDENCE = (
    'experiments/mvp/pipeline/lattice/hero-coverage/hero-impossible-plain.json',
    'experiments/mvp/pipeline/lattice/hero-facts-close-bay-9.json',
    'experiments/mvp/pipeline/radio/hero-selection.json',
    'experiments/mvp/pipeline/xray/fixtures/hero-trace.json',
    'experiments/mvp/showcase/reader/recordings.json',
    'experiments/mvp/pipeline/sealed/runs/final-3/story-figures.json',
    'experiments/mvp/pipeline/sealed/runs/final-3/report.json',
    'experiments/mvp/pipeline/package/fixtures/hero-run/package/receipt.json',
    'experiments/mvp/video/script-v6.md',
    'experiments/mvp/video/STORY-NEEDS-v6.md',
    'docs/impact.md',
    'experiments/mvp/video/demo-cut/out/demo-draft.mp4',
    'experiments/mvp/video/launch-cut/out/launch-draft.mp4',
    'experiments/mvp/video/social/out/social-draft.mp4',
    'experiments/mvp/video/readme-hero/out/hero.mp4',
    'experiments/mvp/video/xray-explainer/out/xray.mp4',
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def tree_files() -> list[str]:
    paths = []
    for tree in TREES:
        for path in (ROOT / tree).rglob('*'):
            relative = path.relative_to(ROOT)
            if path.is_file() and not path.is_symlink() and not SKIP_PARTS & set(relative.parts) \
                    and path.suffix not in SKIP_SUFFIXES:
                paths.append(relative.as_posix())
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', required=True)
    parser.add_argument('--decision', required=True)
    arguments = parser.parse_args()
    manifest = ROOT / 'experiments/mvp/pipeline/freeze/manifest-pipeline.json'
    missing = [name for name in EVIDENCE if not (ROOT / name).is_file()]
    if missing:
        raise SystemExit(f'evidence files missing: {missing}')
    files = {name: sha256(ROOT / name) for name in sorted(set(tree_files()) | set(EVIDENCE))}
    shots = {path.relative_to(ROOT).as_posix(): sha256(path)
             for path in sorted((ROOT / SHOTS).iterdir())
             if path.is_file() and (path.name.startswith('shot-') and path.suffix in {'.webm', '.png'}
                                    or path.name == 'timing.json')}
    listing = ''.join(f'{name}\0{digest}\n' for name, digest in {**files, **shots}.items())
    inventory = {
        'schema_version': 'stowlm.film-inventory/1',
        'created_local': time.strftime('%Y-%m-%d %H:%M %Z'),
        'decision': arguments.decision,
        'pipeline_manifest_sha256': sha256(manifest),
        'files': len(files),
        'inventory_sha256': hashlib.sha256(listing.encode()).hexdigest(),
        'recorded_shots': shots,
        'sha256': files,
    }
    Path(arguments.out).write_text(json.dumps(inventory, indent=1, sort_keys=False) + '\n')
    print(json.dumps({key: inventory[key] for key in ('files', 'inventory_sha256', 'pipeline_manifest_sha256')}
                     | {'recorded_shots': len(shots)}))


if __name__ == '__main__':
    main()

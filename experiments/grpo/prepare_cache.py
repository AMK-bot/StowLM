"""Explicit heavy CPU preparation; never launch Modal or any cloud operation.

Admit this job under the fleet compute rules first. It warms all production
families with the owner's checks; a stale linear policy or failed qualification
stops receipt generation. Re-run after any source, parameter, prompt or cache
change. Upload the resulting entire cache directory to the physics Volume.
"""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance', type=Path, required=True)
    args = parser.parse_args()
    from llm.rl.modal_grpo import PinManifest, verify_manifest
    from llm.rl.preflight import write_cache_receipt
    root = Path(__file__).resolve().parents[2]
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    manifest = PinManifest.capture(commit, root / 'src')
    subprocess.run(['uv', 'run', 'python', '-m', 'stowage.cache_warm', '--workers', '1'],
                   cwd=root, check=True)
    verify_manifest(manifest, root / 'src')  # shared workspace may change while warming
    cache = root / 'experiments/cache'
    summary = json.loads((cache / 'stability/warmup/summary.json').read_text())
    write_cache_receipt(json.loads(args.instance.read_text()), manifest, cache,
                        warmup_summary=summary)
    print(cache / 'grpo-preflight.json')


if __name__ == '__main__':
    main()

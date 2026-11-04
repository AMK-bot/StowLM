"""Explicit one-shot release. Fleet admission is awaited inside the driver."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from experiments.mvp.pipeline.sealed import driver
from experiments.mvp.pipeline.sealed.common import HarnessError


def release(config, out_dir):
    out_dir = Path(out_dir)
    if not (out_dir / 'freeze.json').exists():
        driver.freeze(config, out_dir)
    else:
        requested = dict(config) if isinstance(config, dict) else driver.read_json(config)
        frozen = driver.read_json(out_dir / 'config.json')
        for key, value in requested.items():
            if frozen.get(key) != value:
                raise HarnessError('Release config differs from frozen configuration')
    return driver.run(out_dir, release_sealed=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--out-dir', required=True)
    args = parser.parse_args()
    try:
        release(args.config, args.out_dir)
    except (HarnessError, OSError, ValueError) as exc:
        parser.exit(2, f'sealed release: {exc}\n')


if __name__ == '__main__':
    main()

"""CPU-only integration driver: spawn imports the staged source, never live edits."""
import json
import os
from pathlib import Path
import sys
import time

ROOT = next(p for p in Path(__file__).resolve().parents if (p/'pyproject.toml').exists())
STAGE = Path(__file__).resolve().parents[1]
import llm.exit
llm.exit.__path__.insert(0, str(STAGE))
from llm.exit import data
from llm.exit import dataset_pool

data.ROOT = ROOT


def thread_probe(index):
    return index, {name:os.environ.get(name) for name in dataset_pool._NATIVE_THREADS}


def main():
    out, workers = Path(sys.argv[1]), int(sys.argv[2])
    config = json.loads(Path(sys.argv[3]).read_text())
    started = time.monotonic()
    report = data.build_training_dataset(out_dir=out, workers=workers, **config)
    wall = time.monotonic()-started
    with dataset_pool.ordered_pool(workers) as work:
        probes=work(thread_probe,range(4))
    print(json.dumps(dict(wall_s=wall, workers=workers, report=report, probes=probes)))


if __name__ == '__main__':
    main()

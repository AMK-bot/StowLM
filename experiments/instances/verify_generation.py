"""Generate four real-physics smoke shards and measure setup separately."""
from dataclasses import asdict
import json
from pathlib import Path
from time import perf_counter
from stowage.generate.random_yard import SIZE_LADDER, RandomGenerationReport, generate_random_instance
from stowage.io import save_instance, load_instance

root = Path(__file__).parent
rows = []
for size in SIZE_LADDER:
    started = perf_counter()
    audit = RandomGenerationReport()
    instance = generate_random_instance(size, seed=1, report=audit)
    destination = root / size / 'seed-00000001.json'
    destination.parent.mkdir(exist_ok=True)
    save_instance(instance, destination)
    assert load_instance(destination) == instance
    row = {'size': size, 'seconds': perf_counter() - started, **asdict(audit), 'screening_rate': audit.screening_rate}
    destination.with_suffix('.audit.json').write_text(json.dumps(row, indent=2, sort_keys=True) + '\n')
    rows.append(row)
    print(json.dumps(row), flush=True)
(root / 'verification.json').write_text(json.dumps(rows, indent=2, sort_keys=True) + '\n')

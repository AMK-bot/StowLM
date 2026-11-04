"""Record one CPU-only mock proposal through the real task 1.3 validator."""
from pathlib import Path
import json

from experiments.mvp.pipeline.contract.schema import Mode, sha256_file, sha256_json
from stowage.model.instance import Instance
from .demo import FIXTURE, replay_fixture
from .private_intake import PrivateIntake
from .schema import ROW_KEYS, compact_json


def main():
    if FIXTURE.exists():
        replay_fixture()
        print('Existing recorded mock fixture verified; not overwritten.')
        return
    source = Path('experiments/mvp/frontier/lab/sets/quick_small.txt').read_text().splitlines()[0]
    base = Instance.model_validate_json(Path(source).read_text())
    box = next(b for b in base.containers if b.id in base.load_list and b.dg is None and not b.reefer)
    full = box.model_dump(mode='json')
    proposal = {k: full[k] for k in ROW_KEYS}
    proposal['gross_mass_kg'] = round(proposal['gross_mass_kg'], 3)
    text = compact_json(proposal)
    result = PrivateIntake(lambda *a, **kw: proposal).read_text(text, base_instance=base)
    for record in result.records:
        record.mode = Mode.recorded
        record.model = 'mock-model; preparation only'
        record.notes += ' Recorded mock proposal, not trained-model inference.'
    wire = result.model_dump(mode='json')
    fixture = {'synthetic': True, 'mode': 'recorded', 'model': 'mock', 'cost_usd': 0,
               'label': 'Recorded mock answer; validator exercised; no measured model accuracy',
               'base_instance': {'path': source, 'sha256': sha256_file(source)},
               'text': text, 'proposal': proposal, 'result': wire, 'result_sha256': sha256_json(wire)}
    with FIXTURE.open('x') as stream:
        stream.write(json.dumps(fixture, ensure_ascii=False, indent=2) + '\n')
    print('Recorded mock proposal through task 1.3 validator; no GPU, network or sealed access.')


if __name__ == '__main__':
    main()

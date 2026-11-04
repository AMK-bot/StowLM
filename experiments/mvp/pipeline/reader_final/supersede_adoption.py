"""Write the D266 successor to final-adoption.json. The original is read, hashed and left alone."""
import json

from experiments.mvp.pipeline.contract.schema import sha256_file
from .reader import V3

RESULTS = V3 / 'results-final'
OLD = RESULTS / 'final-adoption.json'
NEW = RESULTS / 'final-adoption-d266.json'
REASON = ('D266, leader, open for veto: D264 retained v2 by a tie-break on exact match. The mask study then measured what '
          'that tie-break never looked at: on 84 out-of-scope validation sentences v2 made a rule from 27, 16 naming a real '
          'bay, a misreading the checker cannot catch; v3 from 4. The reader promises to refuse what it cannot read, so the '
          'safer reader ships. This lowers the headline by two sentences; it is not a selection for a better number.')


def rules(report):
    outcomes = report['x4_unsupported']['outcomes']
    return {'rows': report['x4_unsupported']['rows'], 'refused': outcomes.get('refused', 0),
            'made_a_rule': sum(value for key, value in outcomes.items() if key != 'refused'),
            'naming_a_real_bay': outcomes.get('model_real_bay', 0) + outcomes.get('mask_real_bay', 0)}


def main():
    old = json.loads(OLD.read_text())
    selection = json.loads((RESULTS / 'selection.json').read_text())
    study = RESULTS / 'mask-study'
    swa, v2 = [json.loads((study / name).read_text()) for name in ('report.json', 'report-adopted.json')]
    record = {'supersedes': {'file': str(OLD), 'sha256': sha256_file(OLD), 'outcome': old['outcome'], 'rule': old['rule']},
              'decision': 'D266', 'reason': REASON, 'outcome': 'swa-last-5', 'adopted': selection['selected'],
              'sealed_128': old['sealed_128'], 'selection_400': old['selection_400'],
              'out_of_scope_84': {'swa-last-5': rules(swa), 'adapter-v2': rules(v2)},
              'gates_swa_last_5': old['gates_swa_last_5'], 'preregistered_adoption_status': old['preregistered_adoption_status'],
              'sealed_note': 'Sealed v3 is spent and was not rerun. Both sealed numbers stand.', 'caveat': old['caveat']}
    with NEW.open('x') as stream:
        stream.write(json.dumps(record, indent=2) + '\n')
    print(NEW)


if __name__ == '__main__':
    main()

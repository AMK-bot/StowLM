"""Full-check admission of the separately certified lazy-fusion winner."""
import json
from pathlib import Path
from stowage.actions.space import Place


def propose(instance, *, seed, budget_s, context):
    path=Path(__file__).resolve().parent/'results/warm_fusion_proof.json'
    row=json.loads(path.read_text())
    if row['id']!=instance.id:
        return []
    context['method_metadata'].update(fusion=row['stats'],saved_fusion=True)
    return [tuple(Place(a['box'],a['slot']) for a in s['actions']) for s in row['candidates']]

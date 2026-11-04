"""D56 six-state replay with explicit additive metadata migration on copies."""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'experiments/checker'))
from small_stow_common import background_only, cases, load_case, source_identity
from stowage.checker.lashing import check_lashing
from stowage.vessel.fleet import load_vessel

FIELDS = {'max_gm_lashing_m', 'max_gm_lashing_m_provenance'}


def main():
    out = Path(__file__).with_name('six-states')
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    before = source_identity()
    parents, rows = {}, []
    for case in cases():
        original, plan, full = load_case(case)
        original_json = original.model_dump_json(), plan.model_dump_json(), full.model_dump_json()
        instance = original.model_copy(deep=True)
        parent_id = instance.vessel.slice_of
        if parent_id not in parents:
            parents[parent_id] = load_vessel(parent_id)
        parent = parents[parent_id]
        # These artifacts predate D56. Require exact equality of every other
        # hull value before copying the owner's two additive design fields.
        assert instance.vessel.hull.model_dump(exclude=FIELDS) == parent.hull.model_dump(exclude=FIELDS)
        updates = {name: getattr(parent.hull, name) for name in FIELDS}
        instance.vessel.hull = instance.vessel.hull.model_copy(deep=True, update=updates)
        assert instance.model_dump(exclude={'vessel': {'hull': FIELDS}}) == original.model_dump(
            exclude={'vessel': {'hull': FIELDS}})
        report = check_lashing(instance, background_only(full), call_index=instance.call_index)
        assert report.ran, (case, report.error)
        row = dict(case=case, parent_id=parent_id,
                   copied_metadata=instance.vessel.hull.model_dump(include=FIELDS),
                   gm_m=report.metrics['gm_m'], guide_ratio=report.metrics['max_cell_guide_ratio'],
                   report=report.model_dump(mode='json'))
        (out / f'{case}.json').write_text(json.dumps(row, indent=2)+'\n')
        assert original_json == (original.model_dump_json(), plan.model_dump_json(), full.model_dump_json())
        rows.append(row)
        print(json.dumps({key: row[key] for key in ('case', 'gm_m', 'guide_ratio')}), flush=True)
    result = dict(rows=rows, wall_s=time.monotonic()-started,
                  source_before=before, source_after=source_identity(),
                  metadata_migration_only=sorted(FIELDS), original_inputs_unchanged=True,
                  diagnostic_only=True, note='Decision cargo omitted; not a full-plan approval.')
    (out / 'summary.json').write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()

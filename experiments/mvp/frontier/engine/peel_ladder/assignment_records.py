# Copied from experiments/mvp/frontier/lab/c5/c5-peel/assignment_records.py; frozen body unchanged.
"""D242 serialization shared with the stopped pages experiment."""
from tiers import load_table
from stowage.model import PlannerResult

def _record(instance,row,*,searched_level,trials):
    from stowage.moves import apply_plan
    plan,report,analysis=row['plan'],row['report'],row['analysis']
    aboard={p.container_id for p in apply_plan(instance,plan).final_state.ship.placements}
    wanted=set(instance.load_list)
    loaded=wanted&aboard
    left=sorted(wanted-aboard)
    boxes={b.id:b for b in instance.containers}
    level=analysis['level']
    labels={0:'passes the full checker',1:'relaxed plan',2:'relaxed plan',3:'relaxed plan',
        4:"breaks a class or statutory limit, needs a person's sign-off",
        5:'not safe to sail, shown only to say what would have to change'}
    return dict(level=level,level_name=f'L{level}',searched_level=searched_level,
        tiers_peeled=list(load_table().levels[level]),status=labels[level],passed=bool(report.passed),
        rules_broken=analysis['broken_rules'],boxes_left_ashore=left,
        boxes_aboard=len(loaded),boxes_total=len(wanted),
        boxes_aboard_percent=100.*len(loaded)/len(wanted) if wanted else 100.,
        teu_aboard=sum(boxes[b].length_ft/20 for b in loaded),
        mass_aboard_kg=sum(boxes[b].gross_mass_kg for b in loaded),
        lifts=float(report.count.weighted_nonproductive),checker_report=report.model_dump(mode='json'),
        plan=plan.model_dump(mode='json'),trials=list(trials),source=row['source'])


def _package(instance,bank,*,complete,searched_level,trials,seed,result=None):
    safe=bank.best_safe_partial()
    selected=complete or safe or bank.best.get(5)
    if selected is None:
        return None,dict(level=None,passed=False,status='no plan found at L5 within this work',
            completion_status='not_completed',completed=None,best_safe_partial=None,
            trials=trials,checked_candidates=len(bank.rows))
    record=_record(instance,selected,searched_level=searched_level,trials=trials)
    def nested(row):
        if row is None:return None
        r=_record(instance,row,searched_level=searched_level,trials=[])
        r.pop('trials',None)
        return r
    record.update(completed=nested(complete),best_safe_partial=nested(safe),
                  finishing_diagnostics=bank.finishing_records,
                  completion_status='completed' if complete is not None else 'partial')
    if result is None:
        result=PlannerResult(status='ok',plan=selected['plan'],planner='frontier.peel',
                             planner_version='0.2.0',seed=seed)
    return result,record


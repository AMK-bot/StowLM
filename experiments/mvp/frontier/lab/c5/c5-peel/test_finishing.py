from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from types import SimpleNamespace
from finishing import Candidates

def report(rule, measured, limit, lifts):
    return dict(passed=False,count={'weighted_nonproductive':lifts},parts={},
        violations=[dict(rule_id=rule,severity='hard',measured=measured,limit=limit)])

def test_failed_candidates_retained_and_breach_beats_lifts(monkeypatch):
    instance=SimpleNamespace(load_list=[],containers=[])
    bank=Candidates(instance)
    # Isolate the measured physical ranking from parsing an unrelated plan.
    monkeypatch.setattr(bank,'rank',lambda row: __import__('tiers').rank_report(row['report']))
    class Plan:
        def __init__(self,id):self.id=id
        def model_dump_json(self):return self.id
    more=Plan('more');less=Plan('less')
    bank.observe(more,report('iscode.loading.trim',2.,1.,1.),source='failed')
    bank.observe(less,report('iscode.loading.trim',1.1,1.,100.),source='failed')
    assert bank.best[4]['plan'] is less
    assert 3 not in bank.best
    assert not bank.best[4]['report']['passed']

def test_unknown_booking_gap_does_not_hide_measured_securing():
    instance=SimpleNamespace(load_list=[],containers=[])
    bank=Candidates(instance)
    def candidate(name,ratio,lifts):
        plan=SimpleNamespace(model_dump_json=lambda:name)
        row=report('iacs.member_irs.container_securing',ratio,1.,lifts)
        row['violations'].append(dict(rule_id='expert.e6.heavy_under_deck',
            severity='hard',measured=None,limit=None))
        return bank.observe(plan,row)
    worse=candidate('larger',1.5,0.)
    better=candidate('smaller',1.03,100.)
    assert worse['analysis']['total_breach'] is None
    assert better['analysis']['total_breach'] is None
    assert bank.rank(better)<bank.rank(worse)
    assert bank.best[4] is better

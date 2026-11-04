import pytest

from experiments.mvp.pipeline.copilot.safety import acceptance_supported


def cards():
    return [{'alternatives':[dict(id=name,label='checked',checked=True,passed=True,
        plan_sha256=digit*64,checker={'passed':True,'screen':'full'},instruction_recheck={'passed':True})
        for name,digit in [('dg-clearance','a'),('bay-closure','b')]]}]


@pytest.mark.parametrize('text',[
    'Both what-if cards are checked but neither is chosen or applied.',
    'dg-clearance checker passed true with 70.0 weighted lifts.',
    'bay-closure checker passed true with 82.0 weighted lifts.',
    'The checked cards require the planner to choose before application.',
    'dg-clearance: checked true, full checker passed true.',
])
def test_checked_hypothetical_cards_do_not_need_current_plan_acceptance(text):
    assert acceptance_supported(text,cards(),[])


@pytest.mark.parametrize('text',[
    'The current plan is legal because dg-clearance is checked.',
    'My plan is legal based on dg-clearance.',
    'The vessel is legal because dg-clearance is checked.',
    'We are safe because dg-clearance passed.',
    'dg-clearance makes the ship safe.',
    'I approve the current plan using dg-clearance.',
    'Card made-up is checked.',
    'The full checker passed.',
    'dg-clearance is checked.\n\nThe full checker passed.',
    'dg-clearance is checked. Phantom checker passed.',
    'dg-clearance is checked. The current plan passed.',
])
def test_checked_card_cannot_launder_an_unchecked_plan_or_unknown_claim(text):
    assert not acceptance_supported(text,cards(),[])


def test_named_card_still_requires_its_own_full_checker_and_instruction_audit():
    evidence=cards()
    evidence[0]['alternatives'][0]['instruction_recheck']['passed']=False
    assert not acceptance_supported('dg-clearance is checked.',evidence,[])


def test_actual_recorded_card_comparison_retains_prose_without_accepting_a_plan():
    import json
    from pathlib import Path
    from experiments.mvp.pipeline.copilot.safety import finish_turn, gate_diagnostics
    recording=json.loads((Path(__file__).parent/'fixtures/hero-shot10/shot10.json').read_text())
    row=recording['turns'][2]
    assert row['gate']['reasons']==['unsupported_acceptance']
    calls=row['turn']['tool_calls']
    results=[call['result'] for call in calls]
    assert gate_diagnostics(row['raw_say'],calls,results)['reasons']==[]
    output=finish_turn(row['raw_say'],calls,results,'recorded')
    assert row['raw_say'] in output.say
    assert output.plan_sha256 is None
    assert 'dg-clearance' in output.say and 'bay-closure' in output.say


def test_card_scoped_checker_fragments_retain_actual_complete_recorded_comparison():
    import json
    from pathlib import Path
    from experiments.mvp.pipeline.copilot.safety import gate_diagnostics,finish_turn
    recording=json.loads((Path(__file__).parent/'fixtures/hero-shot10-final/shot10.json').read_text())
    row=recording['turns'][0]
    assert row['gate']['reasons']==['unsupported_acceptance']
    calls=row['turn']['tool_calls'];results=[call['result'] for call in calls]
    assert gate_diagnostics(row['raw_say'],calls,results)['reasons']==[]
    output=finish_turn(row['raw_say'],calls,results,'recorded')
    assert row['raw_say'] in output.say and output.plan_sha256 is None


def test_checked_numeric_fact_language_is_not_a_plan_acceptance_claim():
    from experiments.mvp.pipeline.copilot.safety import gate_diagnostics
    assert acceptance_supported('Comparison: Arithmetic over checked weighted lifts.',[],[])
    assert gate_diagnostics('Checked weighted lifts: 9999999.',[],[])['reasons']==['unsupported_number']
    assert not acceptance_supported('The current plan passed with checked weighted lifts.',cards(),[])

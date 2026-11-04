from experiments.mvp.pipeline.copilot.hero_take import ranking_delivered


def test_withheld_or_incomplete_comparison_is_not_a_delivered_ranking():
    text='I recommend dg-clearance at 70.0 rather than bay-closure at 82.0 for fewer weighted lifts.'
    assert ranking_delivered({'turn':{'say':text},'gate':{'reasons':[]}})
    assert not ranking_delivered({'turn':{'say':text},'gate':{'reasons':['unsupported_acceptance']}})
    assert not ranking_delivered({'turn':{'say':'I recommend dg-clearance.'},'gate':{'reasons':[]}})

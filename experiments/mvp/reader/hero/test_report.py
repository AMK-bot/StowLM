from experiments.mvp.reader.hero.report import ranking_key


def case(kind='dg_keep_away',sentence='Keep the corrosives clear.'):
    return {'expected':{'constraint':{'type':kind}},'sentence':sentence,'n_load':18,'input':'val/a'}


def row(delta=0,changed=6,dg=1):
    return {'move_delta':delta,'parse_wall_s':1,'new_replan_s':None,'historical_planner_component_s':100,
            'visual_delta':{'matched_dg_changed_physical_bay_count':dg,'changed_load_placements_count':changed}}


def test_visibility_breaks_equal_zero_cost_corrosive_tie_before_latency():
    assert ranking_key(case(),row(changed=6))<ranking_key(case(),row(changed=3))


def test_lower_extra_cost_wins_for_same_understandable_sentence():
    assert ranking_key(case(),row(delta=0))<ranking_key(case(),row(delta=1))


def test_plain_corrosive_story_precedes_unexplained_imdg_number():
    assert ranking_key(case(),row())<ranking_key(case(sentence='Keep IMDG class 6.1 clear.'),row())

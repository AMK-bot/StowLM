from collections import Counter
from experiments.mvp.stowlm9.run import binding, reusable, stratified


def test_ship_yard_round_robin_stable_under_input_order():
    rows = [dict(ship=ship,yard=yard,input=f'{ship}/{yard}/{i}')
            for ship,yard,count in [('a','random',3),('a','sim',1),('b','sim',2)]
            for i in range(count)]
    expected = ['a/random/0','a/sim/0','b/sim/0','a/random/1','b/sim/1','a/random/2']
    assert [row['input'] for row in stratified(rows)] == expected
    assert stratified(rows[::-1]) == stratified(rows)


def test_resume_requires_complete_epoch_and_candidate_binding(tmp_path,monkeypatch):
    import experiments.mvp.stowlm9.run as run
    monkeypatch.setattr(run,'ROOT',tmp_path)
    source = tmp_path/'map.json'
    source.write_text('{}')
    entry = dict(input='map.json',input_sha256=run.sha(source),baseline_record_sha256='old')
    manifest = dict(manifest_id='run-a',outcome_epoch='epoch-a')
    row = dict(status='complete',outcome_epoch='epoch-a',binding=binding(entry,manifest,'rule'))
    assert reusable(row,entry,manifest,'rule')
    assert not reusable(dict(row,status='error'),entry,manifest,'rule')
    assert not reusable(row,entry,dict(manifest,outcome_epoch='epoch-b'),'rule')
    assert not reusable(row,dict(entry,baseline_record_sha256='new'),manifest,'rule')
    source.write_text('{"changed":true}')
    assert not reusable(row,entry,manifest,'rule')


def test_cp_sat_resource_policy_overrides_explicit_parallel_workers(monkeypatch):
    from ortools.sat.python import cp_model
    from experiments.mvp.stowlm9.run import single_thread_solver_policy
    observed=[]
    def native_solve(self,*args,**kwargs):
        observed.append(self.parameters.num_workers)
        return 'solved'
    monkeypatch.setattr(cp_model.CpSolver,'solve',native_solve)
    monkeypatch.setattr(cp_model.CpSolver,'Solve',native_solve)
    single_thread_solver_policy()
    solver=cp_model.CpSolver()
    solver.parameters.num_workers=2
    assert solver.Solve(None)=='solved'
    solver.parameters.num_workers=8
    assert solver.solve(None)=='solved'
    assert observed==[1,1]


def test_d209_continues_identical_order_beyond_original_quota():
    from experiments.mvp.stowlm9.run import stream_entries
    entries = list(range(2621))
    legacy = dict(entries=entries)
    overnight = dict(entries=entries, config=dict(rule_entry_limit=None))
    assert stream_entries(legacy, 'rule') == entries[:1700]
    assert stream_entries(overnight, 'rule') == entries
    assert stream_entries(legacy, 'frontier') == entries

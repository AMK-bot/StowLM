"""Small driver integrity without expensive planner workloads."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest

P = Path(__file__).with_name('run_small_v2.py')
spec = importlib.util.spec_from_file_location('small_v2_driver_test', P)
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


def test_scoped_ballast_restores_inherited_and_clear(monkeypatch):
    monkeypatch.setenv('STOWAGE_BALLAST_V4', 'inherited')
    with driver.ballast_scope(False):
        assert os.environ.get('STOWAGE_BALLAST_V4') is None
        with driver.ballast_scope(True):
            assert os.environ['STOWAGE_BALLAST_V4'] == '1'
        assert os.environ.get('STOWAGE_BALLAST_V4') is None
    assert os.environ['STOWAGE_BALLAST_V4'] == 'inherited'


def test_stale_journal_rebuild_retains_failed_attempt(tmp_path):
    first = dict(instance_id='a',spec_key='a',shippable=True)
    failed = dict(instance_id='b',spec_key='b',shippable=False,error='timeout')
    driver.write(tmp_path/'instances/a/record.json', first)
    driver.write(tmp_path/'instances/b/record.json', failed)
    (tmp_path/'records.jsonl').write_text(json.dumps(first)+'\n')
    assert len(driver.reconcile(tmp_path)) == 2
    assert len((tmp_path/'records.jsonl').read_text().splitlines()) == 2


def test_orphan_journal_fails_closed(tmp_path):
    (tmp_path/'records.jsonl').write_text(json.dumps(dict(spec_key='orphan'))+'\n')
    with pytest.raises(ValueError, match='orphan'):
        driver.reconcile(tmp_path)


def test_duplicate_checkpoints_fail_closed(tmp_path):
    for name in ('a','b'):
        driver.write(tmp_path/f'instances/{name}/record.json', dict(spec_key='same'))
    with pytest.raises(ValueError, match='Duplicate'):
        driver.reconcile(tmp_path)


def test_released_small_inputs_and_explicit_list(tmp_path):
    args = SimpleNamespace(corpus_root=driver.ROOT/'experiments/mvp/corpus',
        # Later amendments may grow the live producer report; this cohort is
        # explicitly the leader-authorized ten-input frozen selection.
        release=Path(__file__).with_name('small-v2-release-frozen.json'),
        instance_list=None, limit=None)
    selected = driver.select(args)
    assert len(selected) == 10
    paths = tmp_path/'list.json'
    paths.write_text(json.dumps([selected[0]['path']]))
    args.instance_list = paths
    assert driver.select(args) == selected[:1]


def test_completed_checkpoint_resumes_failure_without_import(tmp_path, monkeypatch):
    path = tmp_path/'seed-1.json'
    audit = tmp_path/'seed-1.audit.json'
    path.write_text('{}')
    audit.write_text('{}')
    config = dict(source_root='missing',source_pin={'pin':'a'})
    selection = dict(path=str(path),input_sha256=driver.digest(path),audit_path=str(audit),audit_sha256=driver.digest(audit))
    row = dict(completed=True,config_sha256=driver.json_hash(config),input_sha256=driver.digest(path),shippable=False)
    driver.write(tmp_path/'instances/seed-1/record.json',row)
    monkeypatch.setattr(driver,'source_pin',lambda root: {'pin':'a'})
    assert driver.run_case(dict(selection=selection,config=config,output=str(tmp_path))) == row


@pytest.mark.parametrize('exception', [RuntimeError('failed'), TimeoutError('expired')])
def test_ballast_exception_preserves_candidate_for_full_check(exception):
    raw = SimpleNamespace(status='ok',plan=object())
    def finish(*args, **kwargs):
        raise exception
    candidate, ballast_ok, error = driver.finalize_candidate(None,raw,finish,1)
    assert candidate is raw
    assert candidate.plan is not None  # caller proceeds into full checker
    assert ballast_ok is False  # independently prevents shipping even if checker passes
    assert type(exception).__name__ in error


def test_interrupted_resume_validates_identity_first(tmp_path):
    path = tmp_path/'seed-1.json'
    row = dict(completed=False,config_sha256='wrong',input_sha256='a')
    driver.write(tmp_path/'instances/seed-1/record.json',row)
    with pytest.raises(ValueError,match='configuration/input'):
        driver.run_case(dict(selection=dict(path=str(path),input_sha256='a'),config={},output=str(tmp_path)))


@pytest.mark.parametrize('screen', [False, True])
def test_screen_configuration_matches_gold_and_keeps_c2_off(screen):
    from dataclasses import asdict
    from stowage.feasibility import FeasibilityConfig
    from stowage.solver.scalable import ScalableConfig
    feasibility = FeasibilityConfig(extra={'global_screen': 0})
    actual = ScalableConfig(**driver.scalable_options(
        dict(securing_screen=screen, max_neighborhoods=12), feasibility))
    gold = ScalableConfig(feasibility_config=feasibility, max_neighborhoods=12,
                          securing_screen=screen)
    assert asdict(actual) == asdict(gold)
    assert actual.heavy_first_ordering is screen
    assert actual.constructor_v4 is False
    assert actual.feasibility_config.extra == {'global_screen': 0}


def test_screen_flag_is_opt_in_and_three_workers_supported():
    parser = driver.argument_parser()
    required = ['solver', '--release', 'release.json', '--source-root', 'runtime']
    assert parser.parse_args(required).securing_screen is False
    args = parser.parse_args(required + ['--securing-screen', '--workers', '3'])
    assert args.securing_screen is True
    assert args.workers == 3


def test_seed_policy_matches_gold_or_preserves_historical_fixed_seed():
    parser = driver.argument_parser()
    required = ['solver', '--release', 'release.json', '--source-root', 'runtime']
    old = parser.parse_args(required)
    assert old.seed_policy == 'fixed'
    assert driver.effective_seed(vars(old), SimpleNamespace(seed=28100001)) == 0
    gold = parser.parse_args(required + ['--seed-policy', 'instance'])
    assert driver.effective_seed(vars(gold), SimpleNamespace(seed=28100001)) == 28100001
    fixed = parser.parse_args(required + ['--seed', '17'])
    assert driver.effective_seed(vars(fixed), SimpleNamespace(seed=28100001)) == 17

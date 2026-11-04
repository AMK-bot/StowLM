"""Tiny v4.4 rerun pins, D88 scope and P2 denominator regressions."""
from importlib.util import spec_from_file_location, module_from_spec
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

spec = spec_from_file_location('tiny_v44', Path(__file__).with_name('run_tiny_twostep_v44.py'))
driver = module_from_spec(spec)
spec.loader.exec_module(driver)


def test_default_cohort_is_original_fifteen_and_seed_zero():
    args = driver.argument_parser().parse_args([])
    rows = driver.select(args)
    assert len(rows) == 15
    assert {r['input_sha256'] for r in rows} == set(driver.ORIGINAL_TINY_HASHES.values())
    assert args.solve_s == 60
    assert args.ballast_s == args.checker_s == 120
    assert args.seed_policy == 'fixed' and args.seed == 0


def test_explicit_list_selects_same_pinned_tiny_cases():
    args = driver.argument_parser().parse_args(['--instance-list', str(Path(__file__).with_name('tiny-instances.txt'))])
    assert {r['spec_key'] for r in driver.select(args)} == {p.removesuffix('.json') for p in driver.ORIGINAL_TINY_HASHES}


def test_unknown_case_rejected(tmp_path):
    names = tmp_path/'cases.txt'
    names.write_text('small/feeder_500/random_yard/seed-028100000.json\n')
    args = driver.argument_parser().parse_args(['--instance-list', str(names)])
    with pytest.raises(ValueError, match='Not an original frozen tiny'):
        driver.select(args)


def test_stale_journal_recovers_failed_direct_checkpoint(tmp_path):
    a={'spec_key':'tiny/a','shippable':True}
    b={'spec_key':'tiny/b','shippable':False}
    driver.write(tmp_path/'instances/a/record.json',a)
    driver.write(tmp_path/'instances/b/record.json',b)
    (tmp_path/'records.jsonl').write_text(json.dumps(a)+'\n')
    rows = driver.reconcile(tmp_path)
    assert rows == [a,b]
    assert len((tmp_path/'records.jsonl').read_text().splitlines()) == 2


def test_ballast_opt_in_is_only_inside_scope(monkeypatch):
    monkeypatch.delenv('STOWAGE_BALLAST_V4', raising=False)
    with driver.ballast_scope(True):
        assert driver.os.environ['STOWAGE_BALLAST_V4'] == '1'
    assert 'STOWAGE_BALLAST_V4' not in driver.os.environ


def test_failed_finisher_keeps_raw_plan_for_full_checker():
    raw=SimpleNamespace(plan=object())
    def fail(*args,**kwargs):
        raise TimeoutError('test cap')
    kept,passed,error=driver.finalize_candidate(object(),raw,fail,120)
    assert kept is raw and not passed
    assert error == 'TimeoutError: test cap'


def test_source_pin_includes_new_driver_and_all_twostep_files(tmp_path):
    source=tmp_path/'src/stowage/solver'
    source.mkdir(parents=True)
    (source/'twostep.py').write_text('VERSION="4.4.0"\n')
    (source/'twostep_helpers.py').write_text('# helper\n')
    pins=driver.source_pin(tmp_path)
    assert 'src/stowage/solver/twostep.py' in pins
    assert 'src/stowage/solver/twostep_helpers.py' in pins
    assert str(Path(driver.__file__).relative_to(driver.ROOT)) in pins

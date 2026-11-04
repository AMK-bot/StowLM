"""Verify saved v4.4 tiny/small references without running numerical jobs."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re

from validate_small_v21 import ROOT, direct_rows, object_sha, read, sha, validate_row, validate_source_files
from validate_small_twostep_v21 import pair_summary
from stowage.io import load_instance
from eval.harness_metrics import checker_outcome

VERSION = '4.4.0'
SMALL = 'test-twostep-small-v21-v44'
TINY = 'test-twostep-tiny-v44'


def validate_cohort(folder, admitted, *, rung):
    manifest = read(folder / 'manifest.json')
    config = manifest['config']
    assert manifest['complete'] and manifest['n_records'] == len(manifest['selection']) == 15
    assert manifest['config_sha256'] == object_sha(config)
    assert config['method'] == 'twostep'
    assert config['seed_policy'] == ('fixed' if rung == 'tiny' else 'instance')
    assert config['seed'] == 0
    assert config['solve_s'] == (60 if rung == 'tiny' else 120)
    assert config['c2_global_screen'] == 0 and config['rule_freeze_v1_enabled'] is False
    assert config['ballast_v4_scope'] == 'finalize_only'
    assert {r['input_sha256'] for r in manifest['selection']} == set(admitted)
    assert all(sha(path) == digest for digest,path in admitted.items())
    validate_source_files(config)
    source = Path(config['source_root'])
    expected = {name:digest for name,digest in read(source/'snapshot.json')['source_files'].items()
                if Path(name).name.startswith('twostep') and name.endswith('.py')}
    assert len(expected) == 4, expected
    assert all(config['source_pin'][name] == sha(source/name) == digest for name,digest in expected.items())
    assert re.search(r'^VERSION\s*=\s*[\"\']4\.4\.0[\"\']',
                     (source/'src/stowage/solver/twostep.py').read_text(), re.MULTILINE)
    if rung == 'small':
        provenance = read(folder/'twostep-source-provenance.json')
        assert provenance['version'] == VERSION and provenance['source_root'] == str(source)
        assert provenance['snapshot_sha256'] == sha(source/'snapshot.json')
        assert provenance['twostep_source_sha256'] == expected
        assert provenance['driver_sha256'] == config['source_pin']['experiments/mvp/refs_tools/run_small_v2.py']
    rows = direct_rows(folder)
    assert len(rows) == 15 and {row['input_sha256'] for _,row in rows} == set(admitted)
    checked = shipped = 0
    for checkpoint,row in rows:
        assert row['rung'] == rung and row['method'] == 'twostep'
        seed = 0 if rung == 'tiny' else load_instance(admitted[row['input_sha256']]).seed
        assert row['effective_seed'] == seed and row['planner'] == 'baseline.twostep'
        assert not row.get('scalable_config')
        result = row.get('result')
        if result is not None:
            assert result['planner_version'] == VERSION and result['planner'] == 'baseline.twostep'
            assert result['seed'] == seed and not result['repaired']
        rawpath = checkpoint.parent/'raw-result.json'
        if rawpath.exists():
            raw = read(rawpath)
            assert raw['planner_version'] == VERSION and not raw['repaired'] and raw['seed'] == seed
        if rung == 'tiny':
            assert row['twostep_version'] == VERSION and row['twostep_source_sha256'] == expected
        did_check,did_ship = validate_row(checkpoint,row,config,admitted,official=False,bounds=False)
        checked += did_check
        shipped += did_ship
    summary = read(folder/'summary.json')
    assert summary['twostep_version'] == VERSION
    assert summary['twostep_source_sha256'] == expected['src/stowage/solver/twostep.py']
    canonical = {row['input_sha256']:row for _,row in rows}
    validate_published_rows(canonical, summary['records']['twostep'], rung=rung)
    return canonical, dict(attempts=len(rows),checked=checked,shippable=shipped,source=expected)


def validate_published_rows(canonical, rows, *, rung):
    published = {}
    for row in rows:
        value = dict(row)
        if rung == 'small':
            assert value.pop('reference_epoch') == 'two-step 4.4.0 source-pinned'
            assert value.pop('reference_run_folder') == SMALL
        key = value['input_sha256']
        assert key not in published
        published[key] = value
    assert canonical == published


def validate_pairs(summary, pairs):
    published = {row['ship']:row for row in summary['paired_mutual_pass']}
    assert len(published) == len(summary['paired_mutual_pass']) == 4
    for group,actual in pairs.items():
        row = published['overall' if group == 'all' else group]
        expected = dict(frozen_n=actual['attempts'],mutual_n=actual['mutual_passes'],
            excluded_from_mutual_n=actual['attempts']-actual['mutual_passes'],
            twostep_pass=actual['twostep_checker_passes'],solver_pass=actual['solver_checker_passes'],
            twostep_shippable=actual['twostep_passes'],solver_shippable=actual['solver_passes'],
            mean_delta_twostep_minus_solver=actual['delta_mean'],twostep_wins=actual['twostep_wins'],
            ties=actual['ties'],twostep_losses=actual['solver_wins'])
        assert all(row[k] == v for k,v in expected.items()), (group,row,expected)
        assert sorted(pair['input_sha256'] for pair in row['pairs']) == actual['mutual_sha256']
        for method in ('twostep','solver','bound'):
            assert row[method] == dict(n=actual['mutual_passes'],sum=actual[f'{method}_sum'],mean=actual[f'{method}_mean'])


def validate_comparison(value, before, after):
    old = {r['input_sha256']:r for r in before}
    new = {r['input_sha256']:r for r in after}
    assert old.keys() == new.keys()
    rows = {r['input_sha256']:r for r in value['rows']}
    assert len(rows) == len(value['rows']) == value['n'] == len(new)
    for key in new:
        a,b = old[key],new[key]
        def signature(r):
            # This mirrors the published diagnostic definition, independently of its renderer.
            report = r.get('report') or {}
            violations = list(report.get('violations', []))
            for part in report.get('parts', {}).values():
                violations.extend(part.get('violations', []))
            first = next((str(v.get('rule_id', 'unknown')) for v in violations if v.get('severity') == 'hard'),None)
            count = report.get('count') or {}
            counts = {k:count.get(k) for k in ('relocations','restow_lifts','hatch_cover_lifts')}
            future = count.get('future')
            for k in ('restow_lifts','hatch_cover_lifts'):
                counts[f'future_{k}'] = (sum(v[k] for v in future)
                    if isinstance(future,list) and all(k in v for v in future) else None)
            counts['weighted_nonproductive'] = count.get('weighted_nonproductive')
            return dict(checker=checker_outcome(r),shippable=r.get('shippable'),first_rule=first,
                        counts=counts,planner_status=(r.get('result') or {}).get('status'))
        fields = rows[key]['changed_fields']
        assert rows[key]['before_version'] == (a.get('result') or {}).get('planner_version')
        assert rows[key]['after_version'] == VERSION
        actual_fields = [k for k in rows[key]['before'] if rows[key]['before'][k] != rows[key]['after'][k]]
        assert len(fields) == len(set(fields)) and sorted(fields) == sorted(actual_fields)
        assert rows[key]['before'] == signature(a)
        assert rows[key]['after'] == signature(b)
    assert value['changed_n'] == sum(bool(r['changed_fields']) for r in rows.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs-root',type=Path,default=ROOT/'experiments/mvp/runs')
    parser.add_argument('--release',type=Path,default=Path(__file__).with_name('small-v21-release-frozen.json'))
    parser.add_argument('--preservation-pins',type=Path,default=ROOT/'experiments/mvp/runs'/SMALL/'archived-run-hashes.json')
    args = parser.parse_args()
    runs = args.runs_root
    corpus = ROOT/'experiments/mvp/corpus'
    small = {digest:corpus/name for name,digest in read(args.release)['hashes'].items() if name.startswith('test/small/')}
    tiny = {entry['input_sha256']:corpus/'test'/entry['path'] for entry in read(runs/'test-twostep/manifest.json')['selection']}
    assert len(small) == len(tiny) == 15
    tiny_rows,tiny_counts = validate_cohort(runs/TINY,tiny,rung='tiny')
    small_rows,small_counts = validate_cohort(runs/SMALL,small,rung='small')
    assert tiny_counts['source'] == small_counts['source']
    solver = {r['input_sha256']:r for _,r in direct_rows(runs/'test-solver-small-v21-screen')}
    assert set(solver) == set(small)
    for key in small:
        assert solver[key]['lower_bound'] == small_rows[key]['lower_bound']
    pairs = pair_summary(small_rows,solver,small)
    validate_pairs(read(runs/SMALL/'summary.json'),pairs)
    comparison = read(runs/SMALL/'version-comparison.json')
    validate_comparison(comparison['small_43_to_44'],[r for _,r in direct_rows(runs/'test-twostep-small-v21')],list(small_rows.values()))
    validate_comparison(comparison['tiny_original_to_44'],[r for _,r in direct_rows(runs/'test-twostep')],list(tiny_rows.values()))
    archive = runs/'test-twostep-small-v21/archive'
    archive_meta = read(archive/'ARCHIVE.json')
    assert sha(archive/'test-references-before-v44.md') == archive_meta['report_sha256']
    report = (runs/'test-references.md').read_text()
    assert 'Current two-step version: **4.4.0**' in report
    assert 'two-step 4.3.0 references' not in report and 'two-step 4.1.0 references' not in report
    assert small_counts['source']['src/stowage/solver/twostep.py'] in report
    if args.preservation_pins:
        pins = read(args.preservation_pins)['files']
        assert pins
        for name,digest in pins.items():
            assert sha(ROOT/name) == digest, name
    print(json.dumps(dict(valid=True,tiny=tiny_counts,small=small_counts,paired=pairs),sort_keys=True))


if __name__ == '__main__':
    main()

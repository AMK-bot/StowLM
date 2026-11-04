"""Fixed gates and strict report integration with shared cohort validation."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


def module(name):
    spec = importlib.util.spec_from_file_location('hybrid_report_test_' + name, HERE/(name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


tables, report, fixtures = module('tables'), module('report'), module('test_validation')


def write_fixture(base, arms, manifest):
    (base/'manifest.json').write_text(json.dumps(manifest))
    for arm, rows in arms.items():
        (base/arm).mkdir(exist_ok=True)
        for iid, result in rows.items():
            (base/arm/f'{iid}.json').write_text(json.dumps(result))
            (base/arm/f'{iid}.audit.json').write_text(json.dumps(dict(instance_id=iid)))


def reseal(arms, manifest):
    manifest['cohort_id'] = fixtures.validation.cohort_id(manifest)
    for rows in arms.values():
        for result in rows.values():
            result['cohort_id'] = manifest['cohort_id']
            result['binding'] = fixtures.validation.expected_binding(manifest, result['input'])
            if 'audit' in result:
                result['audit']['cohort_id'] = manifest['cohort_id']


class ReportTests(unittest.TestCase):
    def test_held_report_is_explicit_and_preserves_profile_evidence(self):
        manifest = fixtures.manifest_for()
        manifest.update(profile=dict(conclusion='Distinct E6 checks explain the gap.'), profile_report_sha256='c'*64)
        manifest['cohort_id'] = fixtures.validation.cohort_id(manifest)
        with tempfile.TemporaryDirectory(dir=HERE) as temporary:
            base = Path(temporary)
            (base/'HOLD').touch()
            text, complete = report.render({arm:{} for arm in manifest['arms']},manifest=manifest,run_dir=str(base))
        self.assertFalse(complete)
        self.assertIn('held for skill-check Part 2',text)
        self.assertIn('Distinct E6 checks explain the gap.',text)
        self.assertIn('c'*64,text)

    def test_exact_two_sided_sign_ties(self):
        for fewer, more, p in ((0, 0, 1), (5, 0, .0625), (6, 0, .03125), (0, 6, .03125), (25, 25, 1)):
            self.assertEqual(tables.sign(fewer, more), p)
        arms, manifest = fixtures.good_arms()
        fixtures.set_cost(next(iter(arms['llm'].values())), 100)
        result = tables.paired(arms['llm'], arms['none'], manifest)
        self.assertEqual((result['fewer'], result['equal'], result['more']), (9, 1, 0))
        self.assertEqual(result['p'], tables.sign(9, 0))

    def test_fixed_gates_at_presence_and_gain_threshold(self):
        arms, manifest = fixtures.good_arms()
        self.assertEqual({k: v for k, v in tables.gates(arms, manifest).items() if k.startswith('H')},
                         dict(H1=True, H2=True, H3=True))
        llm = list(arms['llm'].values())
        iid = list(arms['llm'])[1]
        arms['llm'][iid] = fixtures.row(1, 97, 'llm', manifest, extra=False)
        self.assertFalse(tables.gates(arms, manifest)['H1'])
        fixtures.set_cost(llm[0], 98)
        self.assertFalse(tables.gates(arms, manifest)['H2'])

    def test_pass_count_and_fresh_audit_required(self):
        arms, manifest = fixtures.good_arms()
        first = next(iter(arms['llm'].values()))
        first['audit']['fresh_process'] = False
        self.assertFalse(tables.audited_pass(first, manifest))
        self.assertFalse(tables.gates(arms, manifest)['H2'])
        with self.assertRaises(ValueError):
            report.render(arms, manifest=manifest, expected=10)
        first['audit']['fresh_process'] = True
        first['repaired'] = True
        self.assertFalse(tables.audited_pass(first, manifest))

    def test_lineage_is_explicit_and_adapter_compatible(self):
        manifest = fixtures.manifest_for()
        result = fixtures.row(0, 1, 'llm', manifest)
        result['final_origin'] = 'search-0'
        with self.assertRaises(ValueError):
            tables.origin(result)
        result['final_origin'] = 'adapter:/new/8.5/sample0.json'
        result['final_search_origin'] = result['final_origin']
        result['candidates'] = dict(admitted=[dict(label=result['final_origin'])])
        self.assertTrue(tables.extra_origin(result, 'llm'))
        self.assertFalse(tables.extra_origin(result, 'random'))
        result['final_search_changed'] = None
        with self.assertRaises(ValueError):
            tables.origin(result)

    def test_zero_partial_and_shared_retention_rules(self):
        arms, manifest = fixtures.good_arms()
        key = next(iter(arms['llm']))
        result = arms['llm'][key]
        baseline = arms['none'][key]
        boxes = result['final_load_list']
        def final(decisions):
            evidence = fixtures.validation.decision_contribution(result['final_origin'], boxes,
                         result['final_seed_decisions'], decisions)
            result['final_contribution'] = evidence
            result['audit']['final_contribution'] = json.loads(json.dumps(evidence))
        final(baseline['final_contribution']['final_decisions'])
        self.assertFalse(tables.retained(result, baseline, 'llm')['qualified'])
        kept = dict(baseline['final_contribution']['final_decisions'])
        kept.update({b: result['final_seed_decisions'][b] for b in boxes[:2]})
        final(kept)
        self.assertTrue(tables.retained(result, baseline, 'llm')['qualified'])
        result['final_reaching_runs'] = [dict(run=0, origin=result['final_origin']), dict(run=1, origin='blocked')]
        result['final_shared_stow'] = True
        self.assertEqual(tables.origin(result), 'shared')
        measure = tables.retained(result, baseline, 'llm')
        self.assertFalse(measure['qualified'])
        self.assertTrue(measure['shared_qualified'])
        self.assertFalse(measure['H1m'])
        result['final_reaching_runs'][1]['origin'] = result['final_origin']
        self.assertTrue(tables.retained(result, baseline, 'llm')['qualified'])
        self.assertTrue(tables.retained(result, baseline, 'llm')['H1m'])

    def test_software_origin_cannot_be_spoofed_as_extra_by_admitted_metadata(self):
        arms, manifest = fixtures.good_arms()
        result = next(iter(arms['llm'].values()))
        baseline = arms['none'][result['instance_id']]
        for label in ('twostep', 'blocked', 'master', 'yard_first'):
            result['final_origin'] = result['final_search_origin'] = label
            result['candidates']['admitted'] = [dict(label=label)]
            self.assertFalse(tables.extra_origin(result, 'llm'), label)
            self.assertFalse(tables.retained(result, baseline, 'llm')['qualified'], label)
        result['final_origin'] = result['final_search_origin'] = 'llm:unadmitted'
        result['candidates']['admitted'] = []
        self.assertFalse(tables.extra_origin(result, 'llm'))

    def test_bootstrap_is_paired_and_deterministic(self):
        first = tables.interval([(97, 100)]*10, draws=100)
        self.assertEqual(first, tables.interval([(97, 100)]*10, draws=100))
        self.assertAlmostEqual(first[0], .03)
        self.assertAlmostEqual(first[1], .03)

    def test_h3_requires_lower_mean_and_significance(self):
        arms, manifest = fixtures.good_arms()
        fixtures.set_cost(list(arms['llm'].values())[-1], 1000)
        self.assertFalse(tables.gates(arms, manifest)['H3'])
        arms, manifest = fixtures.good_arms()
        for result in list(arms['llm'].values())[:5]:
            fixtures.set_cost(result, 99)
        self.assertFalse(tables.gates(arms, manifest)['H3'])

    def test_h3_requires_no_fewer_passes_than_random(self):
        arms, manifest = fixtures.good_arms()
        self.assertTrue(tables.gates(arms, manifest)['H3'])
        iid = next(iter(arms['llm']))
        arms['llm'][iid] = fixtures.row(0, None, 'llm', manifest, passed=False)
        mutual = tables.paired(arms['llm'], arms['random'], manifest)
        self.assertLess(mutual['mean_left'], mutual['mean_right'])
        self.assertLess(mutual['p'], .05)
        self.assertFalse(tables.gates(arms, manifest)['H3'])

    def test_final_report_api_skips_sidecars_and_requires_all_trials(self):
        with tempfile.TemporaryDirectory(dir=HERE, prefix='report-test-') as directory:
            base = Path(directory)
            arms, manifest = fixtures.good_arms()
            write_fixture(base, arms, manifest)
            output = base/'HYBRID.md'
            self.assertTrue(report.write_report(base, output, expected=10))
            for fragment in ('Status: final', 'H1 **met**', 'H2 **met**', 'H3 **met**',
                             'llm total | 2 maps | — | — | +6', 'Elapsed trial window: 60.0 s', manifest['cohort_id']):
                self.assertIn(fragment, output.read_text())
            iid = next(iter(arms['none']))
            (base/'none'/f'{iid}.json').unlink()
            with self.assertRaises(ValueError):
                report.write_report(base, output, expected=10)
            self.assertFalse(report.write_report(base, output, expected=10, require_complete=False))

    def test_stale_audit_and_foreign_cohort_rejected_by_all_entry_points(self):
        for mode in ('plan', 'cost', 'foreign'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=HERE, prefix='report-test-') as directory:
                base = Path(directory)
                arms, manifest = fixtures.good_arms()
                broken = next(iter(arms['llm'].values()))
                if mode == 'plan':
                    broken['plan']['actions'][0]['slot'] = 'foreign'
                elif mode == 'cost':
                    broken['audit'].update(weighted=1000, stored=1000)
                else:
                    broken['cohort_id'] = 'f'*64
                with self.assertRaises(ValueError):
                    report.render(arms, manifest=manifest, expected=10)
                write_fixture(base, arms, manifest)
                with self.assertRaises(ValueError):
                    report.load_rows(base)
                with self.assertRaises(ValueError):
                    report.write_report(base, base/'HYBRID.md', expected=10)

    def test_quarantine_abort_and_missing_manifest_cannot_report(self):
        for marker in ('QUARANTINED.json', 'ABORTED.json'):
            with tempfile.TemporaryDirectory(dir=HERE, prefix='report-test-') as directory:
                base = Path(directory)
                arms, manifest = fixtures.good_arms()
                write_fixture(base, arms, manifest)
                (base/marker).write_text('{}')
                with self.assertRaises(ValueError):
                    report.load_rows(base)
                with self.assertRaises(ValueError):
                    report.render(arms, manifest=manifest, expected=10, run_dir=str(base))
                with self.assertRaises(ValueError):
                    report.write_report(base, base/'HYBRID.md', expected=10)
        arms, _ = fixtures.good_arms()
        with self.assertRaises(TypeError):
            report.render(arms, expected=10)

    def test_timeout_attempts_and_infrastructure_errors(self):
        manifest = fixtures.manifest_for()
        result = fixtures.row(0, 1, 'none', manifest)
        attempt = dict(label='blocked', finalize_status='inconclusive', finalize_s=299, message='undecided')
        result.update(finalize_s=300, tries=[attempt], blocked_finish=attempt)
        self.assertEqual(report._finisher_timeouts(result), 1)
        self.assertTrue(report._trial_timeout(result))
        result['twostep_finish'] = dict(label='twostep', finalize_status='inconclusive', finalize_s=20,
                                       message='finisher time limit reached')
        self.assertEqual(report._finisher_timeouts(result), 2)
        arms, manifest = fixtures.good_arms()
        next(iter(arms['none'].values()))['error'] = 'source drift'
        with self.assertRaises(ValueError):
            report.render(arms, manifest=manifest, expected=10)

    def test_copied_report_uses_frozen_helpers_and_external_output_root(self):
        with tempfile.TemporaryDirectory(dir=HERE, prefix='report-test-') as directory:
            base = Path(directory)
            frozen = base/'snapshot'
            frozen.mkdir()
            for name in ('report.py', 'tables.py', 'validation.py', 'contribution.py', 'admission.py'):
                shutil.copyfile(HERE/name, frozen/name)
            arms, manifest = fixtures.good_arms()
            write_fixture(base, arms, manifest)
            output = base/'HYBRID.md'
            process = subprocess.run([sys.executable, str(frozen/'report.py'), str(base), '--expected', '10',
                                      '--require-complete', '--output', str(output)],
                                     env={**os.environ, 'HYBRID_WRITE_ROOT': str(base),
                                          'HYBRID_ORIGINAL_ROOT': str(HERE.parents[2]), 'PYTHONDONTWRITEBYTECODE': '1'},
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIn('Status: final', output.read_text())

    def test_declared_diagnostics_use_admission_and_sealed_reference(self):
        arms, manifest = fixtures.good_arms()
        manifest['reference_rows'] = {}
        for arm in ('none', 'llm', 'random'):
            for result in arms[arm].values():
                result['runs'] = [dict(origin=result['final_origin'], start=result['final_origin'],
                                      start_objective=result['weighted'], iterations=600)]
                if arm != 'none':
                    result['candidates'] = dict(n_files=32, n_complete=20, n_distinct=10,
                        admitted=[dict(label=result['final_origin'], cost=result['weighted'])]
                        if result['final_origin'].startswith(arm + ':') else [])
                else:
                    manifest['reference_rows'][result['instance_id']] = dict(passed=True, weighted=100,
                        runs=[dict(start=result['final_origin'], start_objective=100, iterations=1000)])
        manifest['cohort_id'] = fixtures.validation.cohort_id(manifest)
        for rows in arms.values():
            for result in rows.values():
                result['cohort_id'] = manifest['cohort_id']
                result['binding'] = fixtures.validation.expected_binding(manifest, result['input'])
                result['audit']['cohort_id'] = manifest['cohort_id']
        text, complete = report.render(arms, manifest=manifest, expected=10)
        self.assertTrue(complete)
        for fragment in ('A/A exploratory actual no-extra llm vs none | 8', 'excluding 40100025', 'Post hoc portfolio vs none',
                         'Best LLM admitted vs best random admitted', '320 | 32–32 | 200',
                         '10/10 matching search runs below 70%', 'Measured a weakened frontier.'):
            self.assertIn(fragment, text)

    def test_development_report_orders_evidence_before_gates(self):
        arms, manifest = fixtures.good_arms()
        text, complete = report.render(arms, manifest=manifest)
        self.assertTrue(complete)
        markers = ('Start-level comparison', 'A/A timing noise', 'Maps where an extra start supplied',
                   'Retained presence (logic P0.1)', '| LLM minus random |', '- H1 **', '- H2 **', '- H3 **')
        positions = [text.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))
        for fragment in ('Development only', 'performance claims await', '≥8 set bits',
                         '12.5042', 'at least as many passes as random'):
            self.assertIn(fragment, text)

    def test_manifest_declared_40_map_report_needs_120_trials_without_override(self):
        arms, manifest = fixtures.good_arms(40)
        ids = sorted(manifest['maps'])
        manifest['groups'] = dict(entering=ids[:32], aa=ids[32:])
        manifest['amendments'] = ['D205', 'D206']
        reseal(arms, manifest)
        text, complete = report.render(arms, manifest=manifest)
        self.assertTrue(complete)
        self.assertIn('120/120 completed trials on 40 validation-small maps', text)
        self.assertIn('32 entry maps and 8 no-entry A/A maps', text)
        with tempfile.TemporaryDirectory(dir=HERE, prefix='report-test-') as directory:
            base = Path(directory)
            write_fixture(base, arms, manifest)
            self.assertTrue(report.write_report(base, base/'HYBRID.md'))
            iid = next(iter(arms['random']))
            (base/'random'/f'{iid}.json').unlink()
            with self.assertRaises(ValueError):
                report.write_report(base, base/'HYBRID.md')

    def test_primary_aa_uses_declared_group_and_strength_uses_fixed_12_14_limit(self):
        arms, manifest = fixtures.good_arms()
        ids = sorted(manifest['maps'])
        manifest['groups'] = dict(entering=ids[:8], aa=ids[8:])
        manifest['reference_rows'] = {}
        for result in arms['none'].values():
            fixtures.set_cost(result, 13)
            result['runs'] = [dict(start='blocked', origin='blocked', start_objective=13, iterations=700)]
            manifest['reference_rows'][result['instance_id']] = dict(passed=True, weighted=14,
                runs=[dict(start='blocked', start_objective=13, iterations=1000)])
        reseal(arms, manifest)
        diagnostics = report._diagnostics(arms, manifest)
        aa = '\n'.join(diagnostics['aa'])
        self.assertIn('A/A declared llm vs none | 2', aa)
        self.assertIn('A/A exploratory actual no-extra llm vs none | 8', aa)
        strength = '\n'.join(diagnostics['strength'])
        self.assertIn('0/10 matching search runs below 70%', strength)
        self.assertIn('fixed +3% limit 12.5042', strength)
        self.assertIn('Measured a weakened frontier.', strength)


if __name__ == '__main__':
    unittest.main()

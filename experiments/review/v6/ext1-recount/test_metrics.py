"""Synthetic denominator, audit, deadline, counterfactual and CP edge cases."""
import copy
import unittest
from metrics import ARMS, RULE_TYPES, changed_cells, cp, passed, recount


def valid(mid='m0', arm='pipeline'):
    return {'map_id': mid, 'ship_id': 'feeder_500', 'arm': arm,
            'status': 'checked_plan', 'level': 0, 'relaxed_level': None,
            'full_checker_pass': True,
            'checker': {'passed': True, 'violations': [],
                        'parts': {'moves': {'error': None, 'violations': []}}},
            'all_loaded': True, 'complete': True, 'replay_legal': True,
            'audit_verified': True, 'audit_fresh_process': True,
            'audit_returncode': 0, 'audit_pid': 20, 'planning_pid': 10,
            'envelope': {'in_envelope': True}, 'instruction_recheck': True,
            'counterfactual_instruction_recheck': False,
            'final_cells': {'A': ['0010102'], 'B': ['0030102']}}


def inventory():
    return ([{'map_id': 'm' + str(i), 'ship_id': 'feeder_500' if i < 12 else 'feeder_800'}
             for i in range(24)],
            [{'map_id': 'm' + str(i), 'instruction_type': RULE_TYPES[i % 4]}
             for i in range(24)])


class ExactIntervals(unittest.TestCase):
    def test_empty_and_extremes(self):
        self.assertIsNone(cp(0, 0))
        self.assertEqual(cp(0, 24)[0], 0)
        self.assertEqual(cp(24, 24)[1], 1)
        self.assertAlmostEqual(cp(24, 24)[0], .025 ** (1 / 24), places=14)
        self.assertAlmostEqual(cp(0, 24)[1], 1 - .025 ** (1 / 24), places=14)
        self.assertAlmostEqual(cp(1, 1)[0], .025, places=14)
        self.assertAlmostEqual(cp(0, 1)[1], .975, places=14)
    def test_known_example(self):
        lo, hi = cp(13, 18)
        self.assertEqual((round(lo, 4), round(hi, 4)), (.4652, .9031))
    def test_invalid(self):
        for args in ((-1, 24), (25, 24), (0, -1), (1.0, 24)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                cp(*args)


class PassPredicate(unittest.TestCase):
    def test_independent_of_published_flag(self):
        row = valid()
        row['passed'] = False
        self.assertTrue(passed(row))
    def test_each_requirement(self):
        updates = [{'status': s} for s in ('not_run', 'error', 'unavailable',
                    'out_of_envelope', 'relaxed_plan', 'no_checked_plan')] + [
            {'level': None}, {'level': False}, {'level': 1}, {'relaxed_level': 1},
            {'full_checker_pass': False}, {'checker': {}},
            {'checker': {'passed': True, 'parts': {}}},
            {'checker': {'passed': True, 'parts': {'x': {'error': 'oops'}}}},
            {'checker': {'passed': True, 'parts': {'x': {'violations': ['bad']}}}},
            {'checker': {'passed': True, 'violations': ['bad']}},
            {'evidence': {'broken_rules': ['bad']}},
            {'classification': {'breaches': ['bad']}},
            {'classification': {'all_violations': ['bad']}},
            {'envelope': {'in_envelope': False}},
            {'audit_returncode': 1}, {'audit_pid': 10}, {'audit_pid': None},
            {'planning_pid': None}, {'audit_cleanup_errors': ['bad']},
            {'audit_watchdog_errors': ['bad']},
        ]
        updates += [{k: False} for k in ('all_loaded', 'complete', 'replay_legal',
                    'audit_verified', 'audit_fresh_process')]
        updates += [{k: True} for k in ('audit_timed_out', 'audit_cleanup_failed',
                    'child_cleanup_failed', 'fixture_surrogate', 'audit_watchdog_fired')]
        for update in updates:
            with self.subTest(update=update):
                row = valid()
                row.update(update)
                self.assertFalse(passed(row))
    def test_instruction_requires_successful_recheck(self):
        r = valid(arm='instruction')
        self.assertTrue(passed(r))
        for value in (False, None, 1):
            r['instruction_recheck'] = value
            self.assertFalse(passed(r))
        r['instruction_recheck'] = True
        r['instruction_violations'] = ['bad']
        self.assertFalse(passed(r))
    def test_only_verified_deadline_incumbent(self):
        r = valid()
        r['status'] = 'deadline_exceeded'
        self.assertFalse(passed(r))
        r['deadline_with_incumbent'] = True
        self.assertFalse(passed(r))
        r['deadline_incumbent_verified'] = True
        self.assertTrue(passed(r))


class PopulationAndBinding(unittest.TestCase):
    def test_missing_and_unavailable_stay_in_all_denominators(self):
        maps, alloc = inventory()
        rows = [valid()]
        for i, status in enumerate(('error', 'unavailable', 'out_of_envelope', 'not_run'), start=1):
            r = valid('m' + str(i))
            r['status'] = status
            rows.append(r)
        result = recount(rows, maps, alloc)
        self.assertEqual(result['population']['trials'], 72)
        self.assertEqual(result['arms']['pipeline']['all']['passes'], 1)
        self.assertEqual(result['arms']['pipeline']['all']['trials'], 24)
        self.assertEqual(result['arms']['pipeline']['by_ship']['feeder_500']['trials'], 12)
        self.assertEqual(result['arms']['twostep']['all']['not_run'], 24)
        self.assertEqual(result['arms']['pipeline']['all']['out_of_envelope'], 1)
    def test_binding_requires_fresh_L0_baseline_and_failed_rule(self):
        maps, alloc = inventory()
        rows = []
        for i in range(6):
            b, r = valid('m' + str(i)), valid('m' + str(i), 'instruction')
            if i == 1:
                b['counterfactual_instruction_recheck'] = True
            elif i == 2:
                b['counterfactual_instruction_recheck'] = None
            elif i == 3:
                b['audit_verified'] = False
            elif i == 4:
                r['status'] = 'error'
            elif i == 5:
                b['relaxed_level'] = 1
            rows.extend((b, r))
        result = recount(rows, maps, alloc)['instruction']['all']
        self.assertEqual(result['binding']['passes'], 1)
        self.assertEqual(result['binding']['trials'], 2)
        self.assertEqual(result['already_obeyed'], 1)
        self.assertEqual(result['already_obeyed_passes'], 1)
        self.assertEqual(result['counterfactual_unavailable'], 21)
        self.assertEqual(result['passes'], 5)
        self.assertEqual(result['trials'], 24)
        self.assertEqual(result['intended_rechecks'], {'passed': 6, 'failed': 0, 'unavailable': 18})
    def test_discrepancies_and_nonmutation(self):
        maps, alloc = inventory()
        r = valid()
        r['passed'] = False
        original = copy.deepcopy(r)
        result = recount([r], maps, alloc)
        self.assertEqual(result['integrity_issues'][0]['issue'], 'stored passed differs')
        self.assertEqual(r, original)
    def test_duplicate_and_unregistered_trials_rejected(self):
        maps, alloc = inventory()
        for rows in ([valid(), valid()], [valid('pilot')], [dict(valid(), arm='sensitivity')]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                recount(rows, maps, alloc)
    def test_reader_engine_flags_separate_from_deadline(self):
        maps, alloc = inventory()
        r = valid(arm='instruction')
        r.update(status='deadline_exceeded', reader_failure=True, engine_failure=False)
        g = recount([r], maps, alloc)['instruction']['all']
        self.assertEqual((g['reader_failures'], g['engine_failures'], g['deadline_exceeded']), (1, 0, 1))
    def test_changed_cells_is_boxes_not_slots(self):
        b = {'final_cells': {'A': ['1', '3'], 'B': ['5'], 'D': ['9']}}
        i = {'final_cells': {'A': ['3', '1'], 'B': ['7'], 'C': ['8']}}
        self.assertEqual(changed_cells(b, i), 3)
        self.assertIsNone(changed_cells(b, {}))
        self.assertEqual(changed_cells({'final_cells': {}}, {'final_cells': {}}), 0)


if __name__ == '__main__':
    unittest.main()

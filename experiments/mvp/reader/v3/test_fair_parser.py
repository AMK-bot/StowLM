"""Synthetic public-contract fixtures only. Never reads datasets or sealed files."""
import json
import unittest

from experiments.mvp.reader.schema import validate_answer
from experiments.mvp.reader.v3.fair_parser import parse

CONTEXT = {'bays': [1, 2, 3, 7], 'max_tiers': {'hold': 4, 'deck': 6},
           'max_tiers_by_bay': {'7': {'deck': 2, 'hold': 1}},
           'has_accommodation': True, 'hatch_covers': ['P', 'S'], 'ports': ['AAA']}


class FairParserTest(unittest.TestCase):
    def check_constraint(self, sentence, expected, context=None):
        actual = parse(sentence, context or CONTEXT)
        validate_answer(json.dumps(actual), context or CONTEXT)
        self.assertEqual(actual.get('constraint'), expected, sentence)

    def check_refusal(self, sentence, code, context=None):
        actual = parse(sentence, context or CONTEXT)
        validate_answer(json.dumps(actual), context or CONTEXT)
        self.assertEqual(actual.get('refusal', {}).get('code'), code, sentence)

    def test_close_and_requests(self):
        for sentence, bay in [('Close bay two.', 2), ('Could you please block bay 7?', 7),
                              ('Terminal, no loading in bay three, over.', 3),
                              ('Keep bay 1 empty.', 1), ('Bay 2 is out of service.', 2)]:
            self.check_constraint(sentence, {'type': 'close_bay', 'bay': bay})

    def test_height_scopes_and_numbers(self):
        fixtures = [('Cap bay two on deck at three tiers.', 2, 'deck', 3),
                    ('Keep below deck stacks at most two tiers.', None, 'hold', 2),
                    ('Set a zero-tier maximum in the hold of bay 1.', 1, 'hold', 0),
                    ('Above hatch deck stacks must not exceed 4 tiers in bay 3.', 3, 'deck', 4)]
        for text, bay, zone, tiers in fixtures:
            self.check_constraint(text, {'type': 'max_tiers', 'bay': bay, 'zone': zone, 'max_tiers': tiers})

    def test_two_digit_bays_and_radio_numbers(self):
        context = {**CONTEXT, 'bays': [3, 5, 9, 27, 35, 90]}
        for wording, number in [('twenty seven', 27), ('thirty-five', 35),
                                ('ninety', 90), ('tree', 3), ('fife', 5), ('niner', 9)]:
            self.check_constraint('Close bay ' + wording + '.',
                                  {'type': 'close_bay', 'bay': number}, context)
        self.check_constraint('Cap bay thirty five on deck at fife tiers.',
            {'type': 'max_tiers', 'bay': 35, 'zone': 'deck', 'max_tiers': 5}, context)
        self.check_constraint('Keep class eight tree bays away from accommodation.',
            {'type': 'dg_keep_away', 'imdg_class': '8', 'min_bays': 3,
             'away_from': {'kind': 'accommodation'}}, context)

    def test_dg_public_names(self):
        for wording, cls in [('class 8', '8'), ('corrosive cargo', '8'), ('flammable liquids', '3'),
                             ('toxic gases', '2.3'), ('oxidisers', '5.1'), ('class two point one', '2.1')]:
            self.check_constraint(f'Keep {wording} at least two bays away from accommodation.',
                {'type': 'dg_keep_away', 'imdg_class': cls, 'min_bays': 2,
                 'away_from': {'kind': 'accommodation'}})
        self.check_constraint('Keep class 4.3 three bays clear of bay seven.',
            {'type': 'dg_keep_away', 'imdg_class': '4.3', 'min_bays': 3, 'away_from': {'kind': 'bay', 'bay': 7}})

    def test_hatch(self):
        for text in ['One port per hatch.', 'Allow at most one discharge destination per physical cover.',
                     'Ensure all retained and new cargo use the same destination across each hatch cover.',
                     'Do not mix ports across a hatch cover.']:
            self.check_constraint(text, {'type': 'one_port_per_hatch'})

    def test_loosening_precedes_everything(self):
        for text in ['Perhaps reopen bay 999.', 'Close bay 2 but waive class 8 separation.',
                     'Allow mixed ports on the hatch covers.', 'Suspend the current deck limit.',
                     'Ignore existing safety rules.', 'Increase the maximum to 5 tiers.',
                     'Remove the existing caps.', 'Drop current restrictions.',
                     'Ignore closures and exclusions.', 'Increase the deck limits.',
                     'Reduce the accommodation separation to 1 bay.',
                     'Permit loading in bay 2 even when it is closed.',
                     'Cancel the existing no-loading restriction.',
                     'Grant an exception to the existing tier cap.']:
            self.check_refusal(text, 'loosening')

    def test_ambiguity(self):
        for text in ['Perhaps close bay 2.', 'If survey fails, close bay 3.',
                     'Cap bay 2 at three tiers.', 'Keep class 8 away from bay 3.',
                     'Keep class 8 two bays clear.', 'Close bay.',
                     'Keep flammable cargo two bays away from accommodation.',
                     'A limit is pending for deck stacks.']:
            self.check_refusal(text, 'ambiguous')

    def test_unsupported(self):
        for text in ['Close bay 999.', 'Close bay 1 and close bay 3.',
                     'Close bay 2 and cap bay 2 deck at three tiers.',
                     'Cap bay 7 deck at three tiers.', 'Cap bay 1 hold at five tiers.',
                     'Keep class 3.9 two bays away from accommodation.',
                     'Keep class 8 zero bays away from accommodation.',
                     'One port per hatch on deck only.', 'Reserve bay 2 for AAA cargo.',
                     'Cap bay 1 hold at minus two tiers.',
                     'Keep class 8 no more than two bays from accommodation.',
                     'Keep class 8 and class 3 two bays away from accommodation.',
                     'One destination per hatch below deck.', 'One port per hatch 2.']:
            self.check_refusal(text, 'unsupported')
        self.check_refusal('Keep class 8 two bays away from accommodation.', 'unsupported',
                           {**CONTEXT, 'has_accommodation': False})
        self.check_refusal('One port per hatch.', 'unsupported', {**CONTEXT, 'hatch_covers': []})
        self.check_refusal('Keep class 8 two bays away from accommodation.', 'unsupported',
                           {**CONTEXT, 'allowed_imdg_classes': ['3']})
        self.check_refusal('Keep class 8 two bays away from accommodation.', 'unsupported',
                           {**CONTEXT, 'imdg_classes': ['8'], 'allowed_imdg_classes': ['3']})

    def test_commentary(self):
        for text in ['What does a deck limit mean?', 'Yesterday bay 2 was closed.',
                     'For information only, close bay 2; do not apply it.', 'Thanks for the update.']:
            self.check_refusal(text, 'not_an_instruction')


class ParserFreezeTest(unittest.TestCase):
    """Synthetic files in a temporary directory; no real parser/ or sealed reads."""
    def setUp(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from experiments.mvp.reader.v3 import parser_baseline as baseline
        self.baseline = baseline
        self.temp = tempfile.TemporaryDirectory(prefix='fair-parser-public-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.here = Path(self.temp.name) / 'v3'
        self.here.mkdir()
        (self.here / 'fair_parser.py').write_text('def parse(sentence, context):\n    return {}\n')
        public = self.here / 'public.txt'
        public.write_text('Synthetic public contract fixture')
        for name, value in [('HERE', self.here), ('OUT', self.here / 'parser'),
                            ('DEPENDENCIES', [self.here / 'fair_parser.py', public])]:
            modifier = patch.object(baseline, name, value)
            modifier.start()
            self.addCleanup(modifier.stop)
        modifier = patch('experiments.mvp.reader.v3.admission.admit', return_value={'workers': 1})
        modifier.start()
        self.addCleanup(modifier.stop)

    def test_freeze_is_immutable(self):
        self.baseline.freeze()
        with self.assertRaises(ValueError):
            self.baseline.freeze()
        (self.here / 'fair_parser.py').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'dependency changed'):
            self.baseline.score()
        self.assertFalse((self.here / 'parser' / 'score-started.json').exists())

    def test_repeat_score_marker_and_case_integrity(self):
        self.baseline.freeze()
        fixture = self.here / 'sealed'
        fixture.mkdir()
        cases = fixture / 'cases.jsonl'
        cases.write_text('')
        (fixture / 'manifest.json').write_text(json.dumps({'cases_sha256': self.baseline.sha(cases)}))
        marker = self.here / 'parser' / 'score-started.json'
        marker.write_text('{"one_shot":true}')
        with self.assertRaises(FileExistsError):
            self.baseline.score()
        self.assertFalse((self.here / 'parser' / 'sealed-v3-report.json').exists())
        cases.write_text('mutated synthetic fixture')
        with self.assertRaisesRegex(ValueError, 'cases changed'):
            self.baseline.score()
        self.assertEqual(marker.read_text(), '{"one_shot":true}')


if __name__ == '__main__':
    unittest.main()

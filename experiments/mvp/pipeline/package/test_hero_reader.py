"""Actual adopted recordings and authenticated confirmation, without inference."""
import json
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from experiments.mvp.reader.schema import answer_envelope, context_from_instance
from stowage.model import Instance

from . import hero_reader


class HeroReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from .build_fixture import wait_for_admission
        wait_for_admission()
        cls.instance = Instance.model_validate(json.loads(hero_reader.RECORDED_INSTANCE.read_text()))
        cls.context = context_from_instance(cls.instance)
        cls.parser, cls.metadata = hero_reader.adopted_parser('recorded')
        cls.selection = json.loads(hero_reader.SELECTION.read_text())
        cls.sentence = cls.selection['hero_sentence']
        cls.rule = {'schema_version': 'stowlm.reader/2',
                    'constraint': {'type': 'close_bay', 'bay': 9}}

    def test_actual_recording_has_adopted_identity_and_exact_rule(self):
        self.assertEqual(len(self.instance.load_list), 87)
        self.assertEqual(sha256_json(self.instance.model_dump(mode='json')),
                         hero_reader.RECORDED_INSTANCE_SHA256)
        self.assertEqual(sha256_file(hero_reader.RECORDING), hero_reader.RECORDING_SHA256)
        answer, evidence = self.parser.parse(self.sentence, self.context)
        self.assertEqual(answer_envelope(answer), self.rule)
        self.assertEqual(evidence['name'], 'swa-last-5')
        self.assertEqual(evidence['model'], 'muse-glimmer-30b')
        self.assertTrue(evidence['recorded'])
        self.assertTrue(evidence['masked'])
        self.assertEqual(evidence['label'], 'Recorded reading')
        for key in ('adapter_sha256', 'decoder_sha256', 'domain_words_sha256'):
            self.assertEqual(evidence[key], self.selection['reader'][key])
            self.assertEqual(self.metadata[key], evidence[key])
        self.assertEqual(evidence['recording_sha256'], hero_reader.RECORDING_SHA256)
        self.assertEqual(self.metadata['label'], 'Recorded reading')
        self.assertEqual(self.metadata['recording_context_sha256'], sha256_json(self.context))
        self.assertIn(evidence, self.metadata['recording_evidence'])

    def test_recording_rejects_another_ship_context(self):
        from experiments.mvp.reader.pipeline import ParserUnavailableError
        with self.assertRaisesRegex(ParserUnavailableError, 'No stored masked reading'):
            self.parser.parse(self.sentence, dict(self.context, id='another-synthetic-voyage'))

    def test_recording_requires_independent_published_hash(self):
        with patch.object(hero_reader, 'RECORDING_SHA256', '0' * 64):
            with self.assertRaisesRegex(ValueError, 'untrusted adopted hero reader recording'):
                hero_reader.adopted_parser('recorded')

    def test_empty_standing_does_not_claim_a_model_reading(self):
        answer, evidence = self.parser.parse('', self.context)
        self.assertEqual(answer_envelope(answer)['refusal']['code'], 'not_an_instruction')
        self.assertEqual(evidence['parser'], 'EmptyStanding')
        self.assertIsNone(evidence['model'])
        self.assertIsNone(evidence['adapter_sha256'])
        self.assertTrue(evidence['deterministic'])
        self.assertFalse(evidence['recorded'])

    def test_live_backend_is_lazy_and_empty_standing_needs_no_metal(self):
        from experiments.mvp.pipeline.reader_final import reader
        backend = Mock()
        backend.load.side_effect = AssertionError('test must not load Metal')
        backend.side_effect = AssertionError('test must not invoke the model')
        with patch.object(reader, 'MlxBackend', return_value=backend) as factory:
            parser, metadata = hero_reader.adopted_parser('live')
            factory.assert_called_once()
            self.assertFalse(parser.recorded_offline)
            self.assertEqual(metadata['name'], 'swa-last-5')
            self.assertEqual(metadata['mode'], 'live')
            parser.parse('', self.context)
            backend.load.assert_not_called()
            backend.assert_not_called()

    def test_actual_public_reading_is_authenticated_and_host_confirmed(self):
        from experiments.mvp.reader.public_brief import VoyageBrief, read_brief
        from experiments.mvp.pipeline.binding import confirm_brief
        from experiments.mvp.pipeline.binding.confirmation import verify_confirmation
        receipt = read_brief(self.instance, VoyageBrief(standing='', voyage=(self.sentence,)),
                             parser=self.parser)
        rows = receipt.evidence()['sentences']
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['role'], 'standing')
        self.assertFalse(rows[0]['binds'])
        self.assertEqual(rows[1]['role'], 'voyage')
        self.assertEqual(rows[1]['status'], 'accepted')
        self.assertEqual(rows[1]['parsed'], self.rule)
        self.assertEqual(rows[1]['parser']['name'], 'swa-last-5')
        self.assertEqual(rows[1]['parser']['label'], 'Recorded reading')
        for key in ('adapter_sha256', 'decoder_sha256'):
            self.assertEqual(rows[1]['parser'][key], self.metadata[key])
        confirmed = confirm_brief(receipt, 'test_exact_recorded_hero_reading_match')
        confirmation = verify_confirmation(confirmed)
        self.assertEqual(confirmation['source'], 'test_exact_recorded_hero_reading_match')
        self.assertEqual(len(confirmation['confirmation_sha256']), 64)
        self.assertEqual(confirmed.evidence(), receipt.evidence())
        with self.assertRaisesRegex(ValueError, 'Invalid authenticated brief confirmation'):
            verify_confirmation(replace(confirmed, source='unconfirmed source'))

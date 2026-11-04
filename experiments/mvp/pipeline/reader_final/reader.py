"""The adopted reader through the same masked path as sealed scoring.

`AdoptedReader.parse(sentence, context)` is the parser interface the runner and
public_brief already use. Prompt, decoder, EOS choice and greedy decoding match
reader/v3/evaluate.py. There is no repair and no fallback to Spark: bad output
raises ParserOutputError with its evidence.

A reader label comes only from evidence, and `reader_label` needs all of it: the
adopted adapter hash, the sealed-scoring decoder hash, the mask, a prompt hash.
A stored reading replays through `StoredReadings`, marked recorded.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from experiments.mvp.reader.pipeline import GlimmerParser, ParserOutputError, ParserUnavailableError

ROOT = Path(__file__).resolve().parents[4]
V3 = ROOT / 'experiments/mvp/reader/v3'
ADOPTION = V3 / 'results-final/final-adoption-d266.json'  # succeeds final-adoption.json, kept as written
FREEZE = V3 / 'results/final-freeze.json'
MODEL = ROOT / 'models/glimmer-30b-4bit'
MODEL_NAME = 'muse-glimmer-30b'
LABEL = 'StowLM Reader · fine-tuned Muse Glimmer 30B · on this laptop'
UNKNOWN = 'Reader · provenance not recorded'
REQUIRED = ('parser', 'model', 'adapter_sha256', 'decoder_sha256', 'domain_words_sha256', 'decoding_policy',
            'prompt_sha256', 'masked')


def identity(adoption=ADOPTION, freeze=FREEZE, v3=V3):
    """Adopted adapter and sealed-scoring decoder; refuses if either has moved."""
    adopted = json.loads(Path(adoption).read_text())['adopted']
    folder = Path(adopted['path'])
    if sha256_file(folder / 'adapters.safetensors') != adopted['adapter_sha256']:
        raise ValueError('adopted adapter weights changed')
    frozen = json.loads(Path(freeze).read_text())['files_sha256']
    result = {}
    for key, name in (('decoder_sha256', 'decoder.py'), ('domain_words_sha256', 'domain_words.py')):
        digest = sha256_file(Path(v3) / name)
        if frozen['experiments/mvp/reader/v3/' + name] != digest:
            raise ValueError(f'{name} differs from the sealed-scoring freeze')
        result[key] = digest
    from experiments.mvp.reader.v3.decoder import DECODING_POLICY
    return {**result, 'name': adopted['name'], 'adapter_path': str(folder),
            'adapter_sha256': adopted['adapter_sha256'], 'decoding_policy': DECODING_POLICY}


class MlxBackend:
    """Lazy MLX: importing this module loads no model and touches no GPU."""

    def __init__(self, adapter_path, model_path=MODEL):
        self.adapter_path, self.model_path = adapter_path, model_path
        self.model = self.tokenizer = None
        self.load_s = None

    def load(self):
        if self.model is None:
            from mlx_lm import load
            began = perf_counter()
            self.model, self.tokenizer = load(str(self.model_path), adapter_path=str(self.adapter_path))
            self.load_s = perf_counter() - began

    def __call__(self, sentence, context):
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler
        from experiments.mvp.reader.evaluate import render_prompt
        from experiments.mvp.reader.schema import messages
        from experiments.mvp.reader.v3.decoder import make_logits_processor
        self.load()
        stops = set(self.tokenizer.eos_token_ids)
        if not stops:
            raise ValueError('tokenizer has no EOS token for schema completion')
        prompt = render_prompt(self.tokenizer, messages(sentence, context))
        tokens = self.tokenizer.encode(prompt, add_special_tokens=False)
        processor = make_logits_processor(self.tokenizer, context, len(tokens), min(stops))
        text = generate(self.model, self.tokenizer, prompt=tokens, max_tokens=256,
                        sampler=make_sampler(temp=0), logits_processors=[processor], verbose=False)
        return text, hashlib.sha256(prompt.encode()).hexdigest()


class AdoptedReader:
    def __init__(self, backend=None, known=None):
        self.known = known or identity()
        self.backend = backend or MlxBackend(self.known['adapter_path'])

    def warm(self):
        """Load before the first sentence: the runner's reader stage allows 30 s."""
        if hasattr(self.backend, 'load'):
            self.backend.load()

    def parse(self, sentence, context):
        started, prompt = perf_counter(), {}

        def generate(text, ship):
            output, prompt['sha256'] = self.backend(text, ship)
            return output

        parser = GlimmerParser(generate, adapter_sha256=self.known['adapter_sha256'], model=MODEL_NAME)
        proof = {key: self.known[key] for key in ('decoder_sha256', 'domain_words_sha256', 'decoding_policy')}

        def complete(evidence):
            evidence.update(proof, masked=True, prompt_sha256=prompt.get('sha256'),
                            source_sha256=sha256_file(Path(__file__)), wall_s=perf_counter() - started)
            return evidence

        try:
            answer, evidence = parser.parse(sentence, context)
        except ParserOutputError as error:
            complete(error.evidence)
            raise
        return answer, complete(evidence)


def reader_label(evidence, known):
    """The on-device label only when every provenance field is present and right."""
    evidence = evidence if isinstance(evidence, dict) else {}
    true = (all(evidence.get(key) for key in REQUIRED) and evidence['parser'] == 'GlimmerParser'
            and evidence['model'] == MODEL_NAME and evidence['masked'] is True
            and all(evidence[key] == known[key] for key in ('adapter_sha256', 'decoder_sha256',
                                                            'domain_words_sha256', 'decoding_policy')))
    return LABEL if true else UNKNOWN


def reading_key(sentence, context):
    return sha256_json({'sentence': sentence, 'context': context})


class StoredReadings:
    """Replay real readings by exact sentence and ship. Anything else is unavailable."""

    def __init__(self, rows):
        self.rows = {row['key']: row for row in rows}

    def parse(self, sentence, context):
        from experiments.mvp.reader.schema import validate_answer
        row = self.rows.get(reading_key(sentence, context))
        if row is None:
            raise ParserUnavailableError('No stored masked reading for this sentence on this ship')
        evidence = {**row['evidence'], 'recorded': True}
        if row['status'] != 'read':
            raise ParserOutputError(evidence)
        return validate_answer(evidence['raw'], context), evidence


def timing(rows, load_s):
    """Cold start is load plus the first sentence; the rest are warm seconds per sentence."""
    walls = [row['evidence']['wall_s'] for row in rows]
    if not walls:
        return {'sentences': 0}
    warm = sorted(walls[1:]) or walls
    return {'sentences': len(walls), 'model_load_s': load_s, 'first_sentence_s': walls[0],
            'cold_start_s': (load_s or 0) + walls[0], 'warm_median_s': median(warm),
            'warm_p90_s': warm[int(.9 * (len(warm) - 1))], 'warm_mean_s': sum(warm) / len(warm)}

"""CPU grammar and byte-token intersection; reuse reader v3's exact BPE trie."""
from __future__ import annotations

from functools import lru_cache
import json
import string

from experiments.mvp.reader.v3.decoder import (
    _CharNode, _Grammar, ReaderDecoder, ReaderLogitsProcessor,
    TokenVocabulary, token_vocabulary, masked_logits,
)
from .schema import context_json_schema, normalize_context, validate_answer

DECODING_POLICY = 'intake-lora-schema-mask/1'


class _IntakeGrammar(_Grammar):
    def __init__(self, schema):
        self._build_cache = {}
        super().__init__(schema)

    def digits(self, minimum, maximum, tail):
        """Compact bounded integer automaton; O(digits), never O(maximum)."""
        root = _CharNode()
        for size in range(len(str(minimum)), len(str(maximum)) + 1):
            low = str(max(minimum, 0 if size == 1 else 10 ** (size - 1))).zfill(size)
            high = str(min(maximum, 10 ** size - 1)).zfill(size)
            if low > high:
                continue

            @lru_cache(None)
            def build(pos, at_low, at_high):
                if pos == size:
                    return tail
                node = _CharNode()
                lower = int(low[pos]) if at_low else 0
                upper = int(high[pos]) if at_high else 9
                for value in range(lower, upper + 1):
                    node.edges[str(value)].add(build(pos + 1, at_low and value == lower, at_high and value == upper))
                return node
            root.epsilon.add(build(0, True, True))
        return root

    def fractional(self, tail):
        # One to three fractional digits, including explicit .0.
        first = _CharNode()
        second = _CharNode()
        second.epsilon.add(tail)
        third = _CharNode()
        third.epsilon.add(tail)
        for digit in string.digits:
            third.edges[digit].add(tail)
            second.edges[digit].add(third)
            first.edges[digit] = {second}
        return self.text('.', first)

    def nonnegative_number(self, minimum, maximum, tail):
        node = _CharNode()
        node.epsilon.add(self.digits(minimum, maximum, tail))
        if minimum < maximum:
            node.epsilon.add(self.digits(minimum, maximum - 1, self.fractional(tail)))
        # Upper bound may be written with zero decimal places only (or .000).
        zeros = _CharNode()
        zeros.epsilon.add(tail)
        zeros.epsilon.add(self.text('0', tail))
        zeros.epsilon.add(self.text('00', tail))
        node.epsilon.add(self.text(str(maximum) + '.0', zeros))
        return node

    def build(self, schema, tail):
        key = (json.dumps(schema, sort_keys=True), tail)
        if key not in self._build_cache:
            self._build_cache[key] = self._build(schema, tail)
        return self._build_cache[key]

    def _build(self, schema, tail):
        kind = schema.get('type')
        if kind == 'integer':
            return self.digits(schema['minimum'], schema['maximum'], tail)
        if kind == 'number':
            low, high = schema['minimum'], schema['maximum']
            node = _CharNode()
            node.epsilon.add(self.nonnegative_number(max(0, low), high, tail))
            if low < 0:
                node.epsilon.add(self.text('-', self.nonnegative_number(0, -low, tail)))
            return node
        if kind == 'string':
            patterns = {'^[A-Z]{3}[UJZ][0-9]{7}$': [string.ascii_uppercase] * 3 + ['UJZ'] + [string.digits] * 7,
                        '^[0-9]{4}$': [string.digits] * 4}
            if schema.get('pattern') not in patterns:
                raise ValueError('unsupported intake string pattern')
            tail = self.text('"', tail)
            for alphabet in reversed(patterns[schema['pattern']]):
                node = _CharNode()
                for char in alphabet:
                    node.edges[char].add(tail)
                tail = node
            return self.text('"', tail)
        return super().build(schema, tail)


class IntakeDecoder(ReaderDecoder):
    def __init__(self, tokenizer, context, *, vocabulary=None):
        self.context = normalize_context(context)
        self.vocabulary = vocabulary if vocabulary is not None else token_vocabulary(tokenizer)
        self.grammar = _IntakeGrammar(context_json_schema(context))
        self.states = self.grammar.start
        self.tokens, self.text, self.status, self._allowed = [], '', 'running', None

    def advance(self, token_id):
        following = self._transitions().get(token_id)
        if following is None:
            raise ValueError('token is outside intake schema mask')
        self.states = following
        self.tokens.append(token_id)
        self.text += self.vocabulary.text_by_id[token_id]
        self._allowed = None
        if any(node.terminal for node in following):
            validate_answer(self.text, self.context)
            self.status = 'complete'

    def validate_output(self, text):
        states = self.grammar.consume(self.grammar.start, text)
        if not any(node.terminal for node in states):
            raise ValueError('generation is incomplete or outside intake schema mask')
        validate_answer(text, self.context)
        return text


IntakeLogitsProcessor = ReaderLogitsProcessor


def make_logits_processor(tokenizer, context, prompt_len, stop_token):
    return IntakeLogitsProcessor(IntakeDecoder(tokenizer, context), prompt_len, stop_token)

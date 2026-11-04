"""Reader v3 schema mask; no model imports or output repair.

Like ``llm.stowlm.mask``, expose deterministic allowed IDs and put -inf on
all other logits before sampling. Unlike a trie of encoded whole answers,
intersect an ASCII JSON grammar with the actual ByteLevel BPE token trie:
tokens spanning keys, numbers and punctuation are checked in their entirety.

The language is deliberately canonical: compact JSON, declared property order,
explicit optional/default fields, and fixed refusal reasons. The *same object
language* is exported as JSON Schema for the server-side Spark mask. This is a
schema-valid subset, not a semantic classifier: incorrect valid answers remain
possible. Apply this policy to every current local arm; truncated outputs fail.
The preserved Spark baseline uses its original decoder and is reference-only.
"""
from __future__ import annotations

from collections import defaultdict
import json
from typing import Any

from experiments.mvp.reader.schema import REFUSAL_REASONS, SCHEMA_VERSION, validate_answer

DECODING_POLICY = "reader-v3-schema-mask/2-d224"


def _object(properties: dict) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def _const(value: Any) -> dict:
    return {"const": value}


def context_json_schema(context: dict) -> dict:
    """Schema for Spark and the local mask, using public vessel context only.

    Class declarations allowed_imdg_classes, imdg_classes or declared_imdg_classes
    (also nested vessel/ship_context), then public cargo/containers class metadata,
    define available classes; cargo presence alone is not ship permission. No
    class metadata means the legacy public class
    registry, never an inferred absence of dangerous cargo. Explicit [] forbids DG.
    No sentence, target, validation error or sealed content enters this function.
    Bounds combine the reader schema and physical context; no bay is remapped.
    Every bay+zone tier branch has its own physical cap (including zero).
    """
    from experiments.mvp.reader.v3.domain_words import allowed_imdg_classes
    classes = allowed_imdg_classes(context)
    bays = context["bays"]
    if any(type(b) is not int or not 1 <= b <= 999 for b in bays):
        raise ValueError("context bays must be schema-valid integers")
    bays = sorted(set(bays))
    variants = []
    if bays:
        variants.append(_object({"type": _const("close_bay"), "bay": {"enum": bays}}))
    for bay in [None, *bays]:
        for zone in ("hold", "deck"):
            cap = context["max_tiers"][zone]
            if bay is not None:
                cap = context.get("max_tiers_by_bay", {}).get(str(bay), {}).get(zone, cap)
            if type(cap) is not int or cap < 0:
                raise ValueError("context tier limits must be nonnegative integers")
            variants.append(_object({"type": _const("max_tiers"), "bay": _const(bay),
                                     "zone": _const(zone),
                                     "max_tiers": {"type": "integer", "minimum": 0,
                                                   "maximum": min(12, cap)}}))
    away = []
    if context.get("has_accommodation", False):
        away.append(_object({"kind": _const("accommodation")}))
    if bays:
        away.append(_object({"kind": _const("bay"), "bay": {"enum": bays}}))
    if away and classes:
        variants.append(_object({"type": _const("dg_keep_away"),
                                 "imdg_class": {"enum": sorted(classes)},
                                 "min_bays": {"type": "integer", "minimum": 1, "maximum": 20},
                                 "away_from": {"anyOf": away}}))
    if context.get("hatch_covers"):
        variants.append(_object({"type": _const("one_port_per_hatch")}))
    refusals = [_object({"refused": _const(True), "code": _const(code), "reason": _const(reason)})
                for code, reason in REFUSAL_REASONS.items()]
    return {"anyOf": [
        _object({"schema_version": _const(SCHEMA_VERSION), "constraint": {"anyOf": variants}}),
        _object({"schema_version": _const(SCHEMA_VERSION), "refusal": {"anyOf": refusals}}),
    ]}


class _CharNode:
    __slots__ = ("edges", "epsilon", "terminal")

    def __init__(self, terminal=False):
        self.edges: dict[str, set[_CharNode]] = defaultdict(set)
        self.epsilon: set[_CharNode] = set()
        self.terminal = terminal


class _Grammar:
    def __init__(self, schema):
        self._closure_cache = {}
        self._step_cache = {}
        self.end = _CharNode(terminal=True)
        self.start = self.closure(frozenset([self.build(schema, self.end)]))

    def text(self, text, tail):
        for char in reversed(text):
            node = _CharNode()
            node.edges[char].add(tail)
            tail = node
        return tail

    def build(self, schema, tail):
        choices = schema.get("anyOf", schema.get("oneOf"))
        if choices is not None:
            node = _CharNode()
            node.epsilon.update(self.build(s, tail) for s in choices)
            return node
        if "const" in schema:
            return self.text(json.dumps(schema["const"], separators=(",", ":")), tail)
        if "enum" in schema:
            return self.build({"anyOf": [_const(v) for v in schema["enum"]]}, tail)
        if schema["type"] == "integer":
            return self.build({"enum": list(range(schema["minimum"], schema["maximum"] + 1))}, tail)
        if schema["type"] == "object":
            tail = self.text("}", tail)
            properties = list(schema["properties"].items())
            for i, (key, value) in reversed(list(enumerate(properties))):
                tail = self.build(value, tail)
                tail = self.text(("," if i else "") + json.dumps(key) + ":", tail)
            return self.text("{", tail)
        raise ValueError("unsupported mask schema node")

    def closure(self, states):
        hit = self._closure_cache.get(states)
        if hit is None:
            out, pending = set(states), list(states)
            while pending:
                for node in pending.pop().epsilon:
                    if node not in out:
                        out.add(node)
                        pending.append(node)
            hit = self._closure_cache[states] = frozenset(out)
        return hit

    def step(self, states, char):
        key = (states, char)
        if key not in self._step_cache:
            following = set()
            for node in states:
                following.update(node.edges.get(char, ()))
            self._step_cache[key] = self.closure(frozenset(following))
        return self._step_cache[key]

    def consume(self, states, text):
        for char in text:
            states = self.step(states, char)
            if not states:
                break
        return states


class _TokenNode:
    __slots__ = ("children", "ids")

    def __init__(self):
        self.children = {}
        self.ids = []


class TokenVocabulary:
    """Exact raw bytes for a local ByteLevel BPE tokenizer, never decoded guesses.

    The grammar is ASCII. Tokens with non-ASCII bytes can never occur in its
    outputs and are excluded, including partial UTF-8 byte tokens. Added tokens
    and all control/special IDs are excluded. Unknown tokenizer formats fail shut.
    Build once per loaded tokenizer and share across sequence-specific decoders.
    """
    def __init__(self, tokenizer):
        backend = getattr(tokenizer, "backend_tokenizer", None)
        if backend is None:
            backend = getattr(tokenizer, "_tokenizer", tokenizer)
        spec = json.loads(backend.to_str())
        model = spec["model"]
        if (spec.get("decoder", {}).get("type") != "ByteLevel" or model.get("type") != "BPE"
                or model.get("continuing_subword_prefix") or model.get("end_of_word_suffix")):
            raise ValueError("reader v3 requires a ByteLevel BPE tokenizer")
        visible = list(range(ord("!"), ord("~") + 1)) + list(range(161, 173)) + list(range(174, 256))
        mapped = list(visible)
        missing = [b for b in range(256) if b not in visible]
        mapped += missing
        codepoints = visible + list(range(256, 256 + len(missing)))
        inverse = {chr(cp): b for cp, b in zip(codepoints, mapped)}
        added = {item["id"] for item in spec.get("added_tokens", [])}
        self.text_by_id = {}
        self.root = _TokenNode()
        for raw, token_id in model["vocab"].items():
            if token_id in added:
                continue
            try:
                data = bytes(inverse[char] for char in raw)
                text = data.decode("ascii")
            except (KeyError, UnicodeDecodeError):
                continue
            if not text:
                continue
            self.text_by_id[token_id] = text
            node = self.root
            for char in text:
                if char not in node.children:
                    node.children[char] = _TokenNode()
                node = node.children[char]
            node.ids.append(token_id)


# Strong references prevent id reuse. Keep at most two tokenizer objects alive.
_VOCABULARIES: list[tuple[Any, TokenVocabulary]] = []


def token_vocabulary(tokenizer):
    for existing, vocab in _VOCABULARIES:
        if existing is tokenizer:
            return vocab
    vocab = TokenVocabulary(tokenizer)
    _VOCABULARIES.append((tokenizer, vocab))
    del _VOCABULARIES[:-2]
    return vocab


class ReaderDecoder:
    """One response's deterministic token state machine; no semantic label input."""
    def __init__(self, tokenizer, context: dict, *, vocabulary=None):
        self.context = context
        self.vocabulary = vocabulary if vocabulary is not None else token_vocabulary(tokenizer)
        self.grammar = _Grammar(context_json_schema(context))
        self.states = self.grammar.start
        self.tokens = []
        self.text = ""
        self.status = "running"
        self._allowed = None

    def _transitions(self):
        if self._allowed is None:
            allowed = {}
            if self.status == "running":
                stack = [(self.vocabulary.root, self.states)]
                while stack:
                    node, states = stack.pop()
                    for token_id in node.ids:
                        allowed[token_id] = states
                    for char, child in node.children.items():
                        following = self.grammar.step(states, char)
                        if following:
                            stack.append((child, following))
            self._allowed = allowed
        return self._allowed

    def allowed_token_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self._transitions()))

    def advance(self, token_id: int):
        following = self._transitions().get(token_id)
        if following is None:
            raise ValueError("token is outside reader schema mask")
        self.states = following
        self.tokens.append(token_id)
        self.text += self.vocabulary.text_by_id[token_id]
        self._allowed = None
        if any(node.terminal for node in following):
            # Every grammar route is complete here; no route permits a suffix.
            validate_answer(self.text, self.context)
            self.status = "complete"

    def validate_output(self, text: str) -> str:
        """Reject incomplete/altered generation, never strip/repair/replace it.

        Needed even after masking: a max-token stop may truncate valid prefixes.
        Replays characters, so the last token need not have reached the processor
        callback yet. Returns the exact supplied string for convenient persistence.
        """
        states = self.grammar.consume(self.grammar.start, text)
        if not any(node.terminal for node in states):
            raise ValueError("generation is incomplete or outside reader schema mask")
        validate_answer(text, self.context)
        return text


def masked_logits(logits, allowed):
    """Reference-compatible 0/-inf additive bias, NumPy or lazily imported MLX."""
    import numpy as np
    if len(logits.shape) not in (1, 2) or (len(logits.shape) == 2 and logits.shape[0] != 1):
        raise ValueError("reader logits processor requires batch size one")
    bias = np.full(logits.shape[-1], -np.inf, dtype=np.float32)
    if not allowed or min(allowed) < 0 or max(allowed) >= logits.shape[-1]:
        raise ValueError("invalid or empty allowed token set")
    bias[list(allowed)] = 0
    if type(logits).__module__.startswith("mlx"):
        import mlx.core as mx
        return logits + mx.array(bias).astype(logits.dtype)
    return logits + bias.astype(logits.dtype)


class ReaderLogitsProcessor:
    """mlx-lm batch-one callback, matching stowlm.MaskLogitsProcessor contract.

    ``tokens`` includes the prompt; only newly generated tokens are advanced.
    Unlike a dead-end fallback, EOS is allowed *only* after valid complete JSON.
    Instantiate once per response, identically for tuned and untrained models.
    """
    def __init__(self, decoder: ReaderDecoder, prompt_len: int, stop_token: int):
        if prompt_len < 0 or stop_token < 0:
            raise ValueError("prompt length and stop token must be nonnegative")
        self.decoder = decoder
        self.prompt_len = prompt_len
        self.stop_token = stop_token
        self._seen = 0

    def __call__(self, tokens, logits):
        import numpy as np
        flat = np.array(tokens).reshape(-1).tolist()
        new = len(flat) - self.prompt_len
        if new < self._seen or new < 0:
            raise ValueError("processor cannot be reused for another response")
        for token_id in flat[self.prompt_len + self._seen:]:
            if self.decoder.status == "complete":
                if token_id != self.stop_token:
                    raise ValueError("non-stop token after complete JSON")
            else:
                self.decoder.advance(int(token_id))
        self._seen = new
        allowed = ((self.stop_token,) if self.decoder.status == "complete"
                   else self.decoder.allowed_token_ids())
        return masked_logits(logits, allowed)

    def validate_output(self, text: str) -> str:
        return self.decoder.validate_output(text)


def make_logits_processor(tokenizer, context: dict, prompt_len: int, stop_token: int):
    return ReaderLogitsProcessor(ReaderDecoder(tokenizer, context), prompt_len, stop_token)

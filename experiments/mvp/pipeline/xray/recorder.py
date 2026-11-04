"""Observe v3 mask calls without replacing or modifying its logits result.

Blocked reasons are *inferences*: replay the entire prefix plus the candidate's
raw BPE bytes through a grammar with just bays or tier caps relaxed. A token
failing both counterfactuals is labelled not_in_grammar, including compound
violations. These labels explain the mask, not the model's intent or accuracy.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import time

import numpy as np

from experiments.mvp.pipeline.contract.schema import Mode, StageRecord, sha256_file, sha256_json
from experiments.mvp.reader.v3 import decoder as v3
from .trace import FORMAT, seal_trace, validate_trace


def _plain(logits):
    """Glimmer's logits are MLX bfloat16, which NumPy cannot read; float32 holds them exactly."""
    if type(logits).__module__.startswith("mlx"):
        import mlx.core as mx
        return logits.astype(mx.float32)
    return logits


def _probabilities(logits):
    scores = np.array(_plain(logits), dtype=np.float64).reshape(-1)
    if np.isnan(scores).any() or not np.isfinite(scores).any():
        raise ValueError("recording requires finite raw logits")
    if np.isposinf(scores).any():
        values = np.isposinf(scores).astype(float)
    else:
        values = np.exp(scores - scores.max())
    return values / values.sum()


def _relax_tiers(schema):
    schema = deepcopy(schema)
    def visit(node):
        if isinstance(node, dict):
            properties = node.get("properties", {})
            if "max_tiers" in properties:
                properties["max_tiers"]["maximum"] = 12
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(schema)
    return schema


class RecordingLogitsProcessor:
    """Wrap one ReaderLogitsProcessor; call observe_token for streamed samples.

    Alternatively the next callback observes the previous sample from its token
    prefix. The last sample MUST be supplied with observe_token or finish's
    final_token_ids; it is never guessed from an argmax or silently omitted.
    """
    def __init__(self, processor, *, sentence: str, top_k: int = 8,
                 fake: bool = False, model: str | None = None, tokenizer=None,
                 adapter_sha256: str | None = None, prompt_sha256: str | None = None):
        if not isinstance(processor, v3.ReaderLogitsProcessor):
            raise TypeError("wrap a v3 ReaderLogitsProcessor")
        if type(top_k) is not int or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        if processor._seen or processor.decoder.text:
            raise ValueError("recording must begin before decoding")
        self.processor = processor
        self.sentence, self.top_k, self.fake = sentence, top_k, fake
        self.model, self.adapter_sha256, self.prompt_sha256 = model, adapter_sha256, prompt_sha256
        self.tokenizer = tokenizer
        self.context = deepcopy(processor.decoder.context)
        self.steps, self._observed = [], []
        self._pending = None
        self._text = ""
        self._started = time.monotonic()
        self._finished = False
        self._tier_grammar = v3._Grammar(_relax_tiers(v3.context_json_schema(self.context)))
        self._bay_grammars = {}

    def _token(self, token_id):
        if token_id == self.processor.stop_token:
            return "<eos>"
        vocabulary = self.processor.decoder.vocabulary.text_by_id
        if token_id in vocabulary:
            return vocabulary[token_id]
        if self.tokenizer is not None:
            return self.tokenizer.decode([token_id], skip_special_tokens=False)
        return f"<token:{token_id}>"

    def _reason(self, token_id, prefix):
        token = self.processor.decoder.vocabulary.text_by_id.get(token_id)
        if token is None:
            return "not_in_grammar"
        text = prefix + token
        # Candidate-specific bay widening avoids materialising 999 ship branches.
        numbers = tuple(sorted({int(n) for n in re.findall(r"[0-9]+", text) if 1 <= int(n) <= 999}
                               - set(self.context["bays"])))
        if numbers:
            if numbers not in self._bay_grammars:
                context = deepcopy(self.context)
                context["bays"] = sorted(set(context["bays"]) | set(numbers))
                self._bay_grammars[numbers] = v3._Grammar(v3.context_json_schema(context))
            grammar = self._bay_grammars[numbers]
            if grammar.consume(grammar.start, text):
                return "bay_not_on_ship"
        if self._tier_grammar.consume(self._tier_grammar.start, text):
            return "tier_over_cap"
        return "not_in_grammar"

    def __call__(self, tokens, logits):
        if self._finished:
            raise ValueError("recorder already finished")
        flat = np.array(tokens).reshape(-1).tolist()[self.processor.prompt_len:]
        if flat[:len(self._observed)] != self._observed[:len(flat)] or len(flat) < self.processor._seen:
            raise ValueError("recorder cannot be reused for another response")
        for token in flat[len(self._observed):]:
            self.observe_token(int(token))
        masked = self.processor(tokens, logits)
        # The delegated processor is the only producer of the returned logits.
        allowed = ({self.processor.stop_token} if self.processor.decoder.status == "complete"
                   else set(self.processor.decoder.allowed_token_ids()))
        probabilities = _probabilities(logits)
        ranked = np.argsort(-probabilities, kind="stable")[:self.top_k]
        prefix = self.processor.decoder.text
        candidates = [{"token_id": int(token_id), "token": self._token(int(token_id)),
                       "probability": float(probabilities[token_id]), "blocked": int(token_id) not in allowed,
                       "reason": self._reason(int(token_id), prefix) if int(token_id) not in allowed else None}
                      for token_id in ranked]
        self._pending = {"top_k": candidates, "probabilities": probabilities, "allowed": allowed}
        return masked

    def observe_token(self, token_id: int):
        """Commit one actual sample, including EOS, without advancing the mask."""
        if self._finished or self._pending is None:
            raise ValueError("no pending logits for a sampled token")
        token_id = int(token_id)
        pending = self._pending
        if token_id not in pending["allowed"]:
            raise ValueError("sampled token was blocked by the mask")
        stop = token_id == self.processor.stop_token
        token = self._token(token_id)
        if not stop:
            self._text += token
        self.steps.append({"step": len(self.steps) + 1, "top_k": pending["top_k"],
                           "chosen": {"token_id": token_id, "token": token,
                                      "probability": float(pending["probabilities"][token_id])},
                           "json_so_far": self._text, "is_stop": stop})
        self._observed.append(token_id)
        self._pending = None

    def finish(self, output: str, *, final_token_ids=None, require_complete: bool = True) -> dict:
        """Return JSON-able trace; unfinished callback snapshots are never steps.

        final_token_ids is the full generated sequence (excluding prompt), useful
        when integrating a non-streaming generation API that returns token IDs.
        """
        if self._finished:
            raise ValueError("recorder already finished")
        if final_token_ids is not None:
            final = [int(t) for t in final_token_ids]
            if final[:len(self._observed)] != self._observed:
                raise ValueError("final token sequence differs from observed samples")
            for token in final[len(self._observed):]:
                self.observe_token(token)
            # MLX pipelines one callback ahead, including after EOS/length stop.
            # An authoritative returned sequence identifies that unused snapshot.
            self._pending = None
        if self._pending is not None:
            raise ValueError("final sampled token is missing; call observe_token or pass final_token_ids")
        if output != self._text:
            raise ValueError("output differs from recorded token stream")
        complete = True
        try:
            self.processor.validate_output(output)
        except (ValueError, TypeError):
            complete = False
            if require_complete:
                raise
        value = {"format": FORMAT,
                 "source": {"kind": "fake" if self.fake else "recorded",
                            "label": "FAKE — scripted model, not Glimmer" if self.fake else "Recorded answer",
                            "synthetic": True},
                 "sentence": self.sentence, "context": self.context,
                 "decoding_policy": v3.DECODING_POLICY,
                 "probability_basis": "full-vocabulary softmax of raw logits before the mask",
                 "reason_basis": "inferred by whole-token counterfactual grammar; compound failures use not_in_grammar",
                 "top_k": self.top_k, "steps": self.steps, "output": output, "complete": complete}
        record = StageRecord(stage="reader_xray", mode=Mode.generated if self.fake else Mode.recorded,
                             input_sha256=sha256_json({"sentence": self.sentence, "context": self.context}),
                             output_sha256=sha256_json(value), model=self.model,
                             adapter_sha256=self.adapter_sha256, prompt_sha256=self.prompt_sha256,
                             source_sha256=sha256_file(Path(v3.__file__)),
                             wall_s=time.monotonic() - self._started, cost_usd=0.,
                             notes="FAKE scripted logits; no model inference" if self.fake else "Observed local inference; no API spend")
        value["stage_record"] = record.model_dump(mode="json")
        value = validate_trace(seal_trace(value))
        self._finished = True
        return value

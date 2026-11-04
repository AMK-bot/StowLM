"""Canonical StowLM token sequences (task 4.5, decision D9).

A sequence is assembled from independently encoded fields, never by tokenising
its concatenated text. This prevents a BPE merge from crossing a field boundary:
which token encodes a bay cannot depend on the preceding box index. Forced
spaces, newlines and the end-of-turn token remain in training and generation.
The text is readable, but retokenising that text is NOT the serialisation API.

A Place is ``P000 0010182\\n``: opcode, zero-based box index (at least three
digits), space, bay(3), row(2), tier(2), newline. Glimmer uses seven tokens up
to index 999. D is discharge (optional yard target), R is explicit relocation,
and O is restow-off. Every action ends in a newline; a plan ends in ``<|eot|>``.
Indices cover all instance containers in sorted id order, including yard blockers.

A trie shares common token prefixes among allowed actions: walking it exposes
exactly the legal next tokens without trying every action at every decode step.
This is constrained decoding, not a replacement for the feasibility checker.
Source: Hugging Face Tokenizers documentation (encoding/pre-tokenization),
https://huggingface.co/docs/tokenizers/pipeline ; Glimmer's local tokenizer.json
and chat_template.jinja; boxslot@1.0 in stowage.actions.space.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
import re
from typing import Any

from stowage.actions.space import Action, Discharge, Place, Relocate, RestowOff
from stowage.conventions import parse_slot, parse_yard
from stowage.model import Instance

FORMAT_VERSION = "stowlm@1.0"
DEFAULT_TOKENIZER_PATH = Path(__file__).resolve().parents[3] / "models/glimmer-30b-4bit"
END_PLAN = "<|eot|>"


@lru_cache(maxsize=4)
def load_tokenizer(path: str | Path = DEFAULT_TOKENIZER_PATH) -> Any:
    """Load only the local CPU tokenizer, never model weights or remote code."""
    from tokenizers import Tokenizer

    path = Path(path)
    return Tokenizer.from_file(str(path / "tokenizer.json" if path.is_dir() else path))


def build_sequence(tokenizer: Any, fragments: Iterable[str | Sequence[int]]) -> tuple[int, ...]:
    """The sole sequence builder: append each field's IDs without cross-field merges.

    Strings are encoded with automatic special tokens disabled. Explicit token
    sequences allow already-built prompts/actions to use the same builder.
    """
    output: list[int] = []
    for fragment in fragments:
        if isinstance(fragment, str):
            encoded = tokenizer.encode(fragment, add_special_tokens=False)
            output.extend(encoded.ids if hasattr(encoded, "ids") else encoded)
        else:
            if any(not isinstance(t, int) or isinstance(t, bool) or t < 0 for t in fragment):
                raise ValueError("token ids must be nonnegative integers")
            output.extend(fragment)
    return tuple(output)


@dataclass(frozen=True, slots=True)
class EncodedExample:
    """Canonical prompt/completion pair with the versions needed to replay it.

    This is serialization only: callers must attach provenance and obtain checker
    and mask approval before treating an example as training gold.
    """

    input_ids: tuple[int, ...]
    prompt_len: int
    n_actions: int
    meta: dict[str, str]


def encode_example(instance: Instance, actions: Sequence[Action], tokenizer: Any, *,
                   include_layout: bool = True) -> EncodedExample:
    """Encode an example and bind its prompt, token format and action schema.

    The SFT builder (7.1) should copy ``meta`` into each dataset row rather than
    pinning a module-level prompt constant: the activation switch selects the
    actual prompt version. Action tokens retain their existing field boundaries.
    """
    from llm.stowlm.format_prompt import encode_prompt, prompt_version
    from stowage.actions import ACTION_SPACE_VERSION

    prompt = encode_prompt(instance, tokenizer, include_layout=include_layout)
    codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
    return EncodedExample(
        input_ids=build_sequence(tokenizer, [prompt, codec.encode_plan(actions)]),
        prompt_len=len(prompt), n_actions=len(actions),
        meta={"format": FORMAT_VERSION, "prompt": prompt_version(instance),
              "action_space": ACTION_SPACE_VERSION},
    )


class BoxIndex:
    """Stable numbering shared by prompts and actions; all containers, sorted by id."""

    def __init__(self, box_ids: Sequence[str]) -> None:
        self.ids = tuple(sorted(box_ids))
        if len(set(self.ids)) != len(self.ids):
            raise ValueError("duplicate box ids")
        self.by_id = {box: index for index, box in enumerate(self.ids)}

    def index(self, box: str) -> int:
        try:
            return self.by_id[box]
        except KeyError as exc:
            raise ValueError(f"unknown box: {box}") from exc

    def box(self, index: int) -> str:
        if index < 0 or index >= len(self.ids):
            raise ValueError(f"unknown box index: {index}")
        return self.ids[index]


@dataclass(slots=True)
class _Node:
    children: dict[int, _Node] = field(default_factory=dict)
    terminal: bool = False


class TokenTrie:
    """Prefix tree over canonical actions; build O(total tokens), lookup O(prefix).

    A completed action has no outgoing tokens: the mask refreshes legal actions
    after committing it. Insert end_plan_tokens separately only when completion
    is feasible. No legality decision is made here.
    """

    def __init__(self, sequences: Iterable[Sequence[int]]) -> None:
        self.root = _Node()
        self.node_count = 1
        self.edge_count = 0
        for sequence in sequences:
            if not sequence:
                raise ValueError("empty trie sequence")
            node = self.root
            for token in sequence:
                if token not in node.children:
                    node.children[token] = _Node()
                    self.node_count += 1
                    self.edge_count += 1
                node = node.children[token]
            node.terminal = True

    def _walk(self, prefix: Sequence[int]) -> _Node | None:
        node = self.root
        for token in prefix:
            node = node.children.get(token)
            if node is None:
                return None
        return node

    def allowed_next_token_ids(self, prefix: Sequence[int]) -> set[int]:
        node = self._walk(prefix)
        return set(node.children) if node is not None else set()

    def is_complete(self, prefix: Sequence[int]) -> bool:
        node = self._walk(prefix)
        return node is not None and node.terminal


class ActionCodec:
    """boxslot actions ↔ canonical IDs, with no lost forced tokens.

    decode_action/decode_plan reject noncanonical tokenisations, truncated actions,
    unknown boxes and malformed targets. They validate format, not physical legality.
    """

    def __init__(self, tokenizer: Any, box_ids: Sequence[str] | BoxIndex) -> None:
        self.tokenizer = tokenizer
        self.box_index = box_ids if isinstance(box_ids, BoxIndex) else BoxIndex(box_ids)
        self.end_plan_tokens = build_sequence(tokenizer, [END_PLAN])

    def action_fragments(self, action: Action) -> tuple[str, ...]:
        index = f"{self.box_index.index(action.box):03d}"
        if isinstance(action, Place):
            parse_slot(action.slot)
            return ("P", index, " ", action.slot[:3], action.slot[3:5], action.slot[5:], "\n")
        if isinstance(action, RestowOff):
            return ("O", index, "\n")
        if isinstance(action, (Discharge, Relocate)):
            op = "D" if isinstance(action, Discharge) else "R"
            if action.to is None:
                if isinstance(action, Relocate):
                    raise ValueError("relocation requires a yard target")
                return (op, index, "\n")
            parse_yard(action.to + "-01")
            block, bay, row = action.to.split("-")
            return (op, index, " ", block, "-", bay, "-", row, "\n")
        raise ValueError(f"unsupported action: {action!r}")

    def encode_action(self, action: Action) -> tuple[int, ...]:
        return build_sequence(self.tokenizer, self.action_fragments(action))

    def _parse_action_text(self, text: str) -> Action:
        match = re.fullmatch(r"([PDRO])([0-9]{3,})(?: ([^\n]+))?\n", text)
        if match is None:
            raise ValueError("malformed action")
        op, index, target = match.groups()
        box = self.box_index.box(int(index))
        if op == "P" and target is not None:
            return Place(box, target)
        if op == "D":
            return Discharge(box, target)
        if op == "R" and target is not None:
            return Relocate(box, target)
        if op == "O" and target is None:
            return RestowOff(box)
        raise ValueError("missing or unexpected action target")

    def decode_action(self, token_ids: Sequence[int]) -> Action:
        text = self.tokenizer.decode(list(token_ids), skip_special_tokens=False)
        action = self._parse_action_text(text)
        if self.encode_action(action) != tuple(token_ids):
            raise ValueError("noncanonical action token sequence")
        return action

    def encode_plan(self, actions: Sequence[Action]) -> tuple[int, ...]:
        return build_sequence(self.tokenizer, [*(self.encode_action(a) for a in actions), self.end_plan_tokens])

    def decode_plan(self, token_ids: Sequence[int]) -> list[Action]:
        text = self.tokenizer.decode(list(token_ids), skip_special_tokens=False)
        if not text.endswith(END_PLAN):
            raise ValueError("missing end-of-plan token")
        body = text[:-len(END_PLAN)]
        actions = [self._parse_action_text(line + "\n") for line in body.splitlines()]
        if self.encode_plan(actions) != tuple(token_ids):
            raise ValueError("noncanonical plan token sequence")
        return actions

    def trie(self, allowed_actions: Iterable[Action], *, allow_end: bool = False) -> TokenTrie:
        sequences = [self.encode_action(a) for a in allowed_actions]
        if allow_end:
            sequences.append(self.end_plan_tokens)
        return TokenTrie(sequences)

    def allowed_next_token_ids(self, prefix_tokens_of_current_action: Sequence[int],
                               allowed_actions: Iterable[Action], *, allow_end: bool = False) -> set[int]:
        """Convenience helper; repeated decoding should build and reuse ``trie``."""
        return self.trie(allowed_actions, allow_end=allow_end).allowed_next_token_ids(prefix_tokens_of_current_action)

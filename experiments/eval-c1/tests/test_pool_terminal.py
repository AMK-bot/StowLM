"""Code review High (w2-rev-c1, pool.py:169): a pooled sample that completes on its last token must be classified
``complete`` by ``generate_masked`` exactly like the in-thread decoder (otherwise best-of-k drops a valid candidate).
Runs the real generator with a NumPy stand-in for MLX (no Metal), as in the reviewer's reproduction P1."""

import sys
import types
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


@pytest.fixture
def generate(monkeypatch):
    mx = types.ModuleType("mlx.core")
    mx.array = np.array
    mx.async_eval = lambda x: None
    mx.eval = lambda *x: None
    mx.random = types.SimpleNamespace(key=lambda x: x, split=lambda x: (x + 1, x + 2))
    mlx = types.ModuleType("mlx")
    mlx.core = mx
    monkeypatch.setitem(sys.modules, "mlx", mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", mx)
    monkeypatch.delitem(sys.modules, "llm.stowlm.mask_mlx", raising=False)
    from llm.stowlm import mask_mlx

    monkeypatch.setattr(mask_mlx, "masked_sample",
                        lambda logits, idx, valid, temperature, key: (idx[:, 0], np.zeros(len(idx))))
    yield mask_mlx.generate_masked
    sys.modules.pop("llm.stowlm.mask_mlx", None)


class Model:
    def make_cache(self):
        return []

    def __call__(self, x, cache=None):
        return np.zeros((x.shape[0], x.shape[1], 6))


class Transport:
    """Worker stand-in: one allowed token (1) that completes the plan; replies follow the worker payload format."""

    def __init__(self):
        self.done = False

    def _send(self, worker, message):
        if message[0] == "advance":
            self.done = True

    def _await(self, worker, row, kind="allowed"):
        if self.done:
            return {"allowed": (), "status": "complete", "dead_end": None, "n_legal": None, "completing": ()}
        return {"allowed": (1,), "status": "running", "dead_end": None, "n_legal": 1, "n_possible": 5, "completing": (1,)}


class Local:
    status = "running"

    def allowed_token_ids(self):
        return (1,)

    def advance(self, token):
        self.status = "complete"

    def prefetch(self):
        return False


@pytest.mark.parametrize("max_tokens", [1, 5])
def test_pooled_sample_completing_on_its_last_token_is_complete(generate, max_tokens):
    from pool import RemoteDecoder

    local = Local()
    remote = RemoteDecoder(Transport(), 0, 0)
    rows_local, _ = generate(Model(), [0], [local], max_tokens=max_tokens, temperature=0)
    rows_remote, _ = generate(Model(), [0], [remote], max_tokens=max_tokens, temperature=0)
    assert rows_local[0].status == "complete"
    assert rows_remote[0].status == rows_local[0].status and rows_remote[0].tokens == [1]
    assert remote.status == "complete"


def test_worker_payload_lists_plan_completing_tokens():
    """The worker reports which allowed tokens finish the end-of-plan sequence (MaskedDecoder's own rule:
    plan view complete and the token reaches a leaf)."""
    from pool import completing_tokens

    leaf, inner = types.SimpleNamespace(children={}), types.SimpleNamespace(children={7: object()})
    node = types.SimpleNamespace(children={3: leaf, 4: inner})
    done_view = types.SimpleNamespace(complete=True)
    d = types.SimpleNamespace(status="running", _node=node, _view=lambda: done_view)
    assert completing_tokens(d, (3, 4)) == (3,)
    d._view = lambda: types.SimpleNamespace(complete=False)  # still placing boxes: nothing completes the plan
    assert completing_tokens(d, (3, 4)) == ()

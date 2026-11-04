"""Synthetic path aliases only; never open the repository's protected sets."""
from __future__ import annotations

import builtins
from pathlib import Path
import sys

import pytest

from . import record


def _arguments(monkeypatch, context, output):
    monkeypatch.setattr(sys, "argv", ["record", "--adapter", "unused-adapter",
                                     "--context", str(context), "--output", str(output)])


@pytest.mark.parametrize("alias_kind", ["file", "parent"])
def test_alias_rejected_before_admission_import_or_read(tmp_path, monkeypatch, capsys, alias_kind):
    private = tmp_path / "sealed"
    private.mkdir()
    target = private / "context.json"
    target.write_text('{"synthetic":true}')
    if alias_kind == "file":
        alias = tmp_path / "public-context.json"
        alias.symlink_to(target)
    else:
        parent = tmp_path / "public-contexts"
        parent.symlink_to(private, target_is_directory=True)
        alias = parent / "context.json"
    assert "sealed" not in str(alias).lower()
    events = []
    def forbidden_admission(root):
        events.append("admission")
        raise AssertionError("must reject before admission")
    def forbidden_read(path, *args, **kwargs):
        events.append("read")
        raise AssertionError("must reject before reading bytes")
    original_import = builtins.__import__
    def checked_import(name, *args, **kwargs):
        if name == "mlx" or name.startswith("mlx.") or name == "mlx_lm" or name.startswith("mlx_lm."):
            events.append("mlx")
            raise AssertionError("must reject before MLX imports")
        return original_import(name, *args, **kwargs)
    _arguments(monkeypatch, alias, tmp_path / "trace.json")
    monkeypatch.setattr(record, "_wait_admission", forbidden_admission)
    monkeypatch.setattr(Path, "read_text", forbidden_read)
    monkeypatch.setattr(builtins, "__import__", checked_import)
    with pytest.raises(SystemExit) as exit:
        record.main()
    assert exit.value.code == 2
    assert "sealed contexts are outside this task" in capsys.readouterr().err
    assert events == []


def test_named_protected_root_rejected_without_keyword(tmp_path):
    target = tmp_path / "experiments/mvp/testv3/context.json"
    target.parent.mkdir(parents=True)
    target.write_text('{}')
    alias = tmp_path / "public-context.json"
    alias.symlink_to(target)
    assert "sealed" not in str(target).lower()
    with pytest.raises(ValueError, match="sealed contexts"):
        record._public_context_path(alias, tmp_path)


def test_supplied_spelling_is_checked_even_when_target_is_public(tmp_path):
    target = tmp_path / "public-context.json"
    target.write_text('{}')
    alias = tmp_path / "sealed-alias.json"
    alias.symlink_to(target)
    with pytest.raises(ValueError, match="sealed contexts"):
        record._public_context_path(alias, tmp_path)


def test_public_alias_is_read_only_through_resolved_path(tmp_path, monkeypatch):
    directory = tmp_path / "public-data"
    directory.mkdir()
    target = directory / "context.json"
    target.write_text('{"synthetic":true}')
    alias = tmp_path / "public-context.json"
    alias.symlink_to(target)
    assert record._public_context_path(alias, tmp_path) == target.resolve()
    events = []
    def admission(root):
        events.append("admission")
    original_read = Path.read_text
    def checked_read(path, *args, **kwargs):
        events.append(path)
        assert path == target.resolve()
        return original_read(path, *args, **kwargs)
    original_import = builtins.__import__
    class StopBeforeGPU(Exception):
        pass
    def checked_import(name, *args, **kwargs):
        if name == "mlx.core":
            events.append("mlx")
            raise StopBeforeGPU
        return original_import(name, *args, **kwargs)
    _arguments(monkeypatch, alias, tmp_path / "trace.json")
    monkeypatch.setattr(record, "_wait_admission", admission)
    monkeypatch.setattr(Path, "read_text", checked_read)
    monkeypatch.setattr(builtins, "__import__", checked_import)
    with pytest.raises(StopBeforeGPU):
        record.main()
    assert events == ["admission", target.resolve(), "mlx"]


def test_bfloat16_logits_become_the_same_probabilities():
    mx = pytest.importorskip("mlx.core")
    from .recorder import _probabilities
    with mx.stream(mx.cpu):
        probabilities = _probabilities(mx.array([1.0, 2.0, 3.0], dtype=mx.bfloat16))
    expected = _probabilities([1.0, 2.0, 3.0])
    assert probabilities == pytest.approx(expected)

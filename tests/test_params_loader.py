"""params/ loader helpers (the files themselves are tested by their owners: tests/params/)."""

from __future__ import annotations

import pytest

from stowage import params


def test_dirs_and_listing() -> None:
    assert (params.repo_root() / "pyproject.toml").is_file()
    for kind in params.PARAM_KINDS:
        assert isinstance(params.list_param_files(kind), list)
    with pytest.raises(ValueError):
        params.list_param_files("nope")  # type: ignore[arg-type]


def test_quantity_helpers() -> None:
    q = params.quantity({"value": 5, "unit": "tiers", "assumption": "Q3"})
    assert q.value == 5 and q.unit == "tiers" and q.assumption == "Q3" and q.source is None
    with pytest.raises(ValueError):
        params.quantity({"value": 5, "unit": "tiers"})
    tree = {"a": {"value": 1, "unit": "m", "source": "x"}, "b": 2, "c": [{"value": 3, "unit": "kg"}], "d": "text"}
    assert params.unsourced_numbers(tree) == ["b", "c[0]"]


def test_existing_files_parse() -> None:
    for kind in params.PARAM_KINDS:
        for name in params.list_param_files(kind):
            assert isinstance(params.load_param_file(kind, name), dict)

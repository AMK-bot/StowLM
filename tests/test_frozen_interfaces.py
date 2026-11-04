"""Frozen interfaces (plan 0.9): every module imports, every frozen signature matches the snapshot below (X2: change
it only together with the interface owner), and unimplemented stubs raise NotImplementedError."""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
import textwrap
from pathlib import Path

import pytest

import stowage.interfaces  # noqa: F401  — registers every frozen interface
from stowage.frozen import FROZEN

# fmt: off
FROZEN_SIGNATURES = {
    'actions.get_action_space': "(name: 'str' = 'default') -> 'ActionSpace[Any]'",
    'bounds.lower_bound': "(instance: 'Instance', *, time_limit_s: 'float' = 10.0) -> 'MoveLowerBound'",
    'checker.check': "(instance: 'Instance', plan: 'MovePlan', *, intermediate_stability: 'bool' = False) -> 'CheckReport'",
    'checker.check_lashing': "(instance: 'Instance', ship: 'ShipState', *, call_index: 'int') -> 'PartReport'",
    'checker.check_stability': "(instance: 'Instance', ship: 'ShipState', *, call_index: 'int', in_port: 'bool' = False) -> 'PartReport'",  # X2: in_port added by contract C7/D64 (leader, 29 Oct)
    'checker.check_static_rules': "(instance: 'Instance', ship: 'ShipState', *, call_index: 'int') -> 'PartReport'",
    'checker.check_strength': "(instance: 'Instance', ship: 'ShipState', *, call_index: 'int', in_port: 'bool' = False) -> 'PartReport'",  # X2: in_port added by contract C7/D64 (leader, 29 Oct)
    'checker.check_visibility': "(instance: 'Instance', ship: 'ShipState', *, call_index: 'int') -> 'PartReport'",
    'checker.departure_condition': "(instance: 'Instance', plan: 'MovePlan') -> 'ShipState'",
    'feasibility.legal_actions_batch': "(engines: 'Sequence[FeasibilityEngine[A]]') -> 'list[Sequence[A]]'",
    'feasibility.make_engine': "(instance: 'Instance', space: 'ActionSpace[A]', *, config: 'FeasibilityConfig | None' = None) -> 'FeasibilityEngine[A]'",
    'io.load_instance': "(path: 'str | Path') -> 'Instance'",
    'io.load_plan': "(path: 'str | Path') -> 'MovePlan'",
    'io.load_result': "(path: 'str | Path') -> 'PlannerResult'",
    'io.save_instance': "(instance: 'Instance', path: 'str | Path', *, allow_stubs: 'bool' = False) -> 'Path'",
    'io.save_plan': "(plan: 'MovePlan', path: 'str | Path', *, allow_stubs: 'bool' = False) -> 'Path'",
    'io.save_result': "(result: 'PlannerResult', path: 'str | Path', *, allow_stubs: 'bool' = False) -> 'Path'",
    'linear.build_model': "(instance: 'Instance', *, band: 'ValidityBand | None' = None) -> 'LinearModel'",
    'linear.constraints': '(model: \'LinearModel\', *, direction: "Literal[\'necessary\', \'conservative\']", margin_scale: \'float\' = 1.0) -> \'LinearConstraintSet\'',
    'linear.evaluate': "(model: 'LinearModel', ship: 'ShipState', instance: 'Instance') -> 'LinearCondition'",
    'linear.solve_ballast_lp': '(model: \'LinearModel\', ship: \'ShipState\', instance: \'Instance\', *, direction: "Literal[\'necessary\', \'conservative\']", margin_scale: \'float\' = 1.0, objective: "Literal[\'min_total_ballast\', \'max_gm_margin\']" = \'min_total_ballast\') -> \'BallastLPResult\'',
    'moves.apply_move': "(instance: 'Instance', state: 'WorldState', move: 'Move', *, move_index: 'int | None' = None) -> 'WorldState'",
    'moves.apply_plan': "(instance: 'Instance', plan: 'MovePlan', *, stop_at_first: 'bool' = True) -> 'ApplyResult'",
    'moves.count_moves': "(instance: 'Instance', plan: 'MovePlan') -> 'MoveCount'",
    'moves.initial_state': "(instance: 'Instance') -> 'WorldState'",
    'moves.project_future_moves': "(instance: 'Instance', ship: 'ShipState', *, from_call: 'int') -> 'list[FutureMoves]'",
    'planner.finalize': "(instance: 'Instance', result: 'PlannerResult', *, time_limit_s: 'float' = 30.0) -> 'PlannerResult'",
    'planner.run_planner': "(planner: 'Planner', instance: 'Instance', *, seed: 'int' = 0, time_limit_s: 'float | None' = None, allow_repair: 'bool' = True) -> 'tuple[PlannerResult, CheckReport | None]'",
    'solver.finish_ballast': "(instance: 'Instance', plan: 'MovePlan', *, time_limit_s: 'float' = 30.0, seed: 'int' = 0) -> 'BallastOutcome'",
    'solver.repair': "(instance: 'Instance', result: 'PlannerResult', report: 'CheckReport | None', *, time_limit_s: 'float' = 60.0, seed: 'int' = 0) -> 'PlannerResult'",
}
# fmt: on

SRC = Path(__file__).resolve().parents[1] / "src"


METAL_UNAVAILABLE_MARKERS = ("No Metal device available", "metal::load_device")


def _is_metal_unavailable(exc: BaseException) -> bool:
    """True only for "this machine has no Metal device" while importing MLX (Codex sandboxes, CI without a GPU).
    mlx raises it as an ImportError (sometimes a RuntimeError/OSError from the native extension) with
    ``[metal::load_device] No Metal device available``. Any other import failure is a real bug and must fail."""
    chain: BaseException | None = exc
    while chain is not None:
        if isinstance(chain, (ImportError, RuntimeError, OSError)) and any(
            m in str(chain) for m in METAL_UNAVAILABLE_MARKERS
        ):
            return True
        chain = chain.__cause__ or chain.__context__
    return False


_PACKAGE_IMPORT_ERRORS: dict[str, BaseException] = {}


def _all_modules() -> list[str]:
    """Every module under stowage/llm/eval. Runs at collection time, so it must never raise: a package that fails
    to import while walking is recorded and reported by its own parametrized test instead of stopping collection."""
    names = []
    for top in ("stowage", "llm", "eval"):
        pkg = importlib.import_module(top)
        names.append(top)

        def onerror(name: str) -> None:
            import sys

            _PACKAGE_IMPORT_ERRORS[name] = sys.exc_info()[1]  # type: ignore[assignment]
            names.append(name)

        names += [m.name for m in pkgutil.walk_packages(pkg.__path__, prefix=f"{top}.", onerror=onerror)]
    return sorted(set(names))


@pytest.mark.parametrize("module", _all_modules())
def test_every_module_imports(module: str) -> None:
    try:
        if module in _PACKAGE_IMPORT_ERRORS:
            raise _PACKAGE_IMPORT_ERRORS[module]
        importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001
        if _is_metal_unavailable(exc):
            pytest.skip(f"{module} needs MLX/Metal and this machine has no Metal device ({exc}); "
                        "convention: import MLX lazily or keep it in llm/stowlm/*_mlx.py, mark MLX tests `gpu`")
        raise


def test_metal_error_classifier() -> None:
    metal = ImportError("[metal::load_device] No Metal device available")
    assert _is_metal_unavailable(metal)
    assert _is_metal_unavailable(RuntimeError("[metal::load_device] No Metal device available"))
    wrapped = ImportError("cannot import mask_mlx")
    wrapped.__cause__ = metal
    assert _is_metal_unavailable(wrapped)
    assert not _is_metal_unavailable(ImportError("No module named 'mlx'"))
    assert not _is_metal_unavailable(ModuleNotFoundError("No module named 'stowage.nope'"))
    assert not _is_metal_unavailable(ValueError("No Metal device available"))  # wrong type: still a failure


def test_metal_unavailable_import_is_skipped_but_other_errors_fail(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """End-to-end on throwaway modules: a Metal failure skips, a plain ImportError still fails."""
    (tmp_path / "fake_metal_mod.py").write_text(
        'raise ImportError("[metal::load_device] No Metal device available")\n')
    (tmp_path / "fake_broken_mod.py").write_text('raise ImportError("cannot import name x")\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(pytest.skip.Exception):
        test_every_module_imports("fake_metal_mod")
    with pytest.raises(ImportError):
        test_every_module_imports("fake_broken_mod")


def test_frozen_set_is_exactly_the_snapshot() -> None:
    assert sorted(FROZEN) == sorted(FROZEN_SIGNATURES)


@pytest.mark.parametrize("name", sorted(FROZEN_SIGNATURES))
def test_signature_unchanged(name: str) -> None:
    assert str(FROZEN[name].signature) == FROZEN_SIGNATURES[name], (
        f"frozen signature of {name} changed; that needs the owner's agreement (plan X2) and a snapshot update"
    )


UNIMPLEMENTED = sorted(n for n, f in FROZEN.items() if not f.implemented)
IMPLEMENTED = sorted(n for n, f in FROZEN.items() if f.implemented)


def _is_stub_body(func: object) -> bool:
    """True if the function body is only an (optional) docstring + ``raise NotImplementedError``.

    Decided from the *compiled code object*, not the source file: ``inspect.getsource`` re-reads the file from disk,
    and while an owner is editing it the recorded line numbers can point at a different function (a false
    positive seen once on ``checker.check_static_rules``). A stub's code references exactly one global name,
    ``NotImplementedError``, and contains a raise; any delegating wrapper references other names (its import or
    callee) and therefore never counts as a stub.
    """
    import dis

    code = func.__code__  # type: ignore[attr-defined]
    if code.co_names != ("NotImplementedError",):
        return False
    ops = {ins.opname for ins in dis.get_instructions(code)}
    straight_line = {"RESUME", "NOP", "CACHE", "LOAD_GLOBAL", "LOAD_CONST", "PUSH_NULL", "PRECALL", "CALL",
                     "RAISE_VARARGS", "POP_TOP", "RETURN_CONST", "RETURN_VALUE"}
    # Only a straight-line "raise NotImplementedError(...)": no branches, no other calls (co_names checked above).
    return "RAISE_VARARGS" in ops and ops <= straight_line and not any(op.startswith(("POP_JUMP", "JUMP")) for op in ops)


def test_stub_detector_examples() -> None:
    def stub(x):  # noqa: ANN001, ANN202
        """doc"""
        raise NotImplementedError

    def stub_call(x):  # noqa: ANN001, ANN202
        raise NotImplementedError("later")

    def wrapper(x):  # noqa: ANN001, ANN202
        """doc"""
        from stowage.conventions import parse_slot  # noqa: PLC0415

        return parse_slot(x)

    def partial(x):  # noqa: ANN001, ANN202
        if x:
            return x
        raise NotImplementedError

    assert _is_stub_body(stub) and _is_stub_body(stub_call)
    assert not _is_stub_body(wrapper) and not _is_stub_body(partial)


@pytest.mark.parametrize("name", UNIMPLEMENTED or ["<none>"])
def test_unimplemented_stub_raises(name: str) -> None:
    if name == "<none>":
        pytest.skip("every frozen interface is implemented")
    fi = FROZEN[name]
    assert _is_stub_body(fi.func), f"{name} has a real body but implemented=False: set implemented=True"
    params = fi.signature.parameters.values()
    args = [None for p in params if p.default is p.empty and p.kind is p.POSITIONAL_OR_KEYWORD]
    kwargs = {p.name: None for p in params if p.default is p.empty and p.kind is p.KEYWORD_ONLY}
    wrapper = getattr(importlib.import_module(fi.func.__module__), fi.func.__name__)
    with pytest.raises(NotImplementedError):
        wrapper(*args, **kwargs)


@pytest.mark.parametrize("name", IMPLEMENTED or ["<none>"])
def test_implemented_is_not_the_stub_body(name: str) -> None:
    if name == "<none>":
        pytest.skip("nothing implemented yet")
    assert not _is_stub_body(FROZEN[name].func), f"{name} is marked implemented=True but still has the stub body"


def _empty_ship_fixture():  # noqa: ANN202
    path = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "empty_ship.json"
    if not path.is_file() or not FROZEN["io.load_instance"].implemented:
        pytest.skip("empty-ship fixture or io.load_instance not available")
    from stowage.io import load_instance

    return load_instance(path)


def _fixture_calls(inst):  # noqa: ANN001, ANN202
    """Cheap fixture calls per implemented interface. Only NotImplementedError fails here; behaviour is the
    owner's tests' job (other exceptions, e.g. ValueError for an incomplete empty plan, are accepted)."""
    import tempfile

    from stowage import checker, io
    from stowage.model import MovePlan

    tmp = Path(tempfile.mkdtemp(prefix="frozen-fixture-"))
    empty_plan = MovePlan(id=f"{inst.id}.empty", instance_id=inst.id, call_index=inst.call_index)
    return {
        "moves.initial_state": lambda: __import__("stowage.moves", fromlist=["x"]).initial_state(inst),
        "moves.apply_plan": lambda: __import__("stowage.moves", fromlist=["x"]).apply_plan(inst, empty_plan),
        "moves.count_moves": lambda: __import__("stowage.moves", fromlist=["x"]).count_moves(inst, empty_plan),
        "moves.project_future_moves": lambda: __import__("stowage.moves", fromlist=["x"]).project_future_moves(
            inst, inst.onboard, from_call=inst.call_index),
        "checker.check": lambda: __import__("stowage.checker", fromlist=["x"]).check(inst, empty_plan),
        "bounds.lower_bound": lambda: __import__("stowage.bounds", fromlist=["x"]).lower_bound(inst),
        "linear.build_model": lambda: __import__("stowage.linear", fromlist=["x"]).build_linear_model(inst),
        "io.load_instance": lambda: inst,  # already called to load the fixture
        "io.save_instance": lambda: io.save_instance(inst, tmp / "i.json", allow_stubs=True),
        "io.save_plan": lambda: io.save_plan(empty_plan, tmp / "p.json", allow_stubs=True),
        "io.load_plan": lambda: io.load_plan(io.save_plan(empty_plan, tmp / "p2.json", allow_stubs=True)),
        **{
            f"checker.{part}": (lambda part=part: getattr(checker, part)(inst, inst.onboard, call_index=inst.call_index))
            for part in ("check_static_rules", "check_stability", "check_strength", "check_visibility",
                         "check_lashing")
        },
    }


@pytest.mark.parametrize("name", IMPLEMENTED or ["<none>"])
def test_implemented_does_not_raise_not_implemented_on_fixture(name: str) -> None:
    if name == "<none>":
        pytest.skip("nothing implemented yet")
    inst = _empty_ship_fixture()
    call = _fixture_calls(inst).get(name)
    if call is None:
        pytest.skip(f"no cheap fixture call defined for {name}; covered by the stub-body check")
    try:
        call()
    except NotImplementedError as e:  # pragma: no cover — the failure we're looking for
        pytest.fail(f"{name} is implemented=True but raised NotImplementedError on the fixture: {e}")
    except Exception:  # noqa: BLE001 — behaviour is the owner's tests' job
        pass


def test_every_frozen_interface_has_an_owner_task() -> None:
    from stowage.ownership import TASK_HOMES

    for name, fi in FROZEN.items():
        assert fi.owner_task in TASK_HOMES, name

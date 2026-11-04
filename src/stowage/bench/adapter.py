"""Map idealised public block-relocation bays to the physical yard move engine.

Stacks are bottom to top, and smaller distinct integer priorities leave first.
A relocation is one top-box lift between stacks; a retrieval is ``gate_out``
and is counted separately. Restricted BRP permits moves only from the stack
blocking the next retrieval; unrestricted BRP also permits preparation moves.
Source: Tanaka and Mizuno (2018), https://doi.org/10.1016/j.cor.2018.02.019.

The ship fixture is only a model-contract carrier: no ship moves or departure
checker are exercised. No ship-safety or BRP optimality claim follows from a
successful replay. The model does not encode BRP's fixed retrieval order, so
its free-load-order yard bound is reported separately from ordered BRP LB1.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache

from stowage.bounds import lower_bound
from stowage.bounds.components import grouped_blocking_bound
from stowage.conventions import make_container_id
from stowage.frozen import current_provenance
from stowage.model import GateOut, Instance, MoveCount, MovePlan, Relocate, WorldState
from stowage.model.fixtures import build_instance
from stowage.moves import apply_move, initial_state, project_future_moves


def _validate(stacks: Sequence[Sequence[int]], height_limit: int) -> tuple[int, ...]:
    if not stacks or not isinstance(height_limit, int) or isinstance(height_limit, bool) or height_limit < 1:
        raise ValueError("BRP requires stacks and a positive integer height limit")
    if len(stacks) > 999 or height_limit > 99:
        raise ValueError("BRP bay exceeds model address capacity (999 stacks, 99 tiers)")
    labels = tuple(label for stack in stacks for label in stack)
    if any(not isinstance(label, int) or isinstance(label, bool) or label < 1 for label in labels):
        raise ValueError("BRP priorities must be positive integers")
    if len(set(labels)) != len(labels):
        raise ValueError("this adapter requires distinct retrieval priorities")
    if any(len(stack) > height_limit for stack in stacks):
        raise ValueError("initial stack exceeds the operational height limit")
    return labels


@lru_cache(maxsize=1)
def _template() -> Instance:
    return build_instance()


def _ids(labels: Iterable[int]) -> dict[int, str]:
    return {label: make_container_id("QZXU", serial) for serial, label in enumerate(sorted(labels), 1)}


def _key(index: int) -> str:
    return f"BRP-{index + 1:03d}-01"


def to_instance(
    stacks: Sequence[Sequence[int]], height_limit: int, *, name: str = "brp",
) -> Instance:
    """Build a validated yard instance with every BRP box on the load list.

    Retrieval precedence remains external: the frozen Instance contract permits
    any load order. Synthetic equal 20-foot boxes preserve BRP's homogeneous
    top-access and height constraints exactly; masses never affect yard replay.
    """
    labels = _validate(stacks, height_limit)
    ids = _ids(labels)
    data = _template().model_dump()
    data.update(id=name, family={"benchmark": "brp"}, seed=0)
    data["port"]["yard"] = dict(id="brp", blocks=[dict(
        id="BRP", n_bays=len(stacks), n_rows=1, max_tiers=height_limit,
        equipment_id="brp", allowed_lengths_ft=[20],
    )])
    data["port"]["equipment"] = [dict(id="brp", kind="other", stack_tiers=height_limit, count=1)]
    data["port"]["yard_max_tiers"] = height_limit
    data["yard"] = dict(yard_id="brp", stacks=[dict(key=_key(i), length_ft=20,
        boxes=[ids[label] for label in stack]) for i, stack in enumerate(stacks) if stack])
    data["containers"] = [dict(id=ids[label], iso_type="22G1", length_ft=20,
        height_m=2.591, tare_kg=2200, gross_mass_kg=12000, pol="THLCH", pod="THBKK")
        for label in sorted(labels)]
    data["load_list"] = list(ids.values())
    data["provenance"]["created_by"] = "bench.adapter"
    return Instance.model_validate(data)


@dataclass(frozen=True)
class BoundResult:
    """Keep the engine's order-free relaxation distinct from ordered BRP LB1."""

    engine_yard: int
    ordered_lb1: int


def benchmark_bounds(
    stacks: Sequence[Sequence[int]], height_limit: int, *, name: str = "brp",
) -> BoundResult:
    """Evaluate the actual frozen bound and its order-aware public component.

    Engine yard = zero when all boxes are interchangeable required loads.
    Ordered LB1 counts each box above an earlier box once, valid for both BRP
    variants (Borjian et al., 2015, https://arxiv.org/abs/1505.04229).
    Neither result is a restricted-problem optimum or a sum over snapshots.
    """
    instance = to_instance(stacks, height_limit, name=name)
    labels = [label for stack in stacks for label in stack]
    return BoundResult(lower_bound(instance).relocations,
        grouped_blocking_bound(stacks, {label: label for label in labels}))


@dataclass(frozen=True)
class ReplayResult:
    count: MoveCount
    plan: MovePlan
    final_state: WorldState


def replay_relocations(
    stacks: Sequence[Sequence[int]], height_limit: int,
    relocations: Iterable[tuple[int, int]], *, expected_relocations: int | None = None,
    restricted: bool = False,
) -> ReplayResult:
    """Replay zero-based stack pairs with eager free retrievals between moves.

    This compressed-solution format assumes every accessible next-priority box
    leaves immediately. Use only for publications using that convention. Every
    relocation and retrieval passes through ``apply_move`` and is counted only
    after successful replay. Only relocations enter the yard objective. The
    returned plan records a simulator trace: its productive gate retrievals are
    deliberately illegal in operational ``apply_plan`` / ``count_moves``.
    Reject incomplete solutions and restricted moves which do not unblock the
    current target.
    """
    instance = to_instance(stacks, height_limit)
    ids = _ids(label for stack in stacks for label in stack)
    bay = [list(stack) for stack in stacks]
    remaining = sorted(ids)
    cursor = 0
    state = initial_state(instance)
    moves = []
    relocation_count = 0
    gate_count = 0

    def retrieve() -> None:
        nonlocal cursor, state, gate_count
        while cursor < len(remaining):
            target = remaining[cursor]
            index = next(i for i, stack in enumerate(bay) if target in stack)
            if bay[index][-1] != target:
                break
            move = GateOut(call_index=0, container_id=ids[target], src=f"{_key(index)}-{len(bay[index]):02d}")
            state = apply_move(instance, state, move, move_index=len(moves))
            gate_count += 1
            moves.append(move)
            bay[index].pop()
            cursor += 1

    retrieve()
    for src, dst in relocations:
        if not (0 <= src < len(bay) and 0 <= dst < len(bay)) or src == dst:
            raise ValueError("relocation must name two distinct in-range stack indices")
        if not bay[src] or cursor == len(remaining):
            raise ValueError("relocation has no source box or follows completed retrieval")
        if restricted and remaining[cursor] not in bay[src]:
            raise ValueError("restricted relocation must unblock the next retrieval")
        label = bay[src][-1]
        move = Relocate(call_index=0, container_id=ids[label],
            src=f"{_key(src)}-{len(bay[src]):02d}", dst=f"{_key(dst)}-{len(bay[dst]) + 1:02d}")
        state = apply_move(instance, state, move, move_index=len(moves))
        relocation_count += 1
        moves.append(move)
        bay[src].pop()
        bay[dst].append(label)
        retrieve()
    if cursor != len(remaining):
        raise ValueError("published relocation sequence leaves unretrieved boxes")
    # WorldState has no provenance field, so retain active engine overrides at
    # this artifact boundary as well as any taint/lineage carried by the input.
    provenance = current_provenance(created_by="bench.replay", seed=instance.seed).merge_nested(
        instance, parent_ids=[instance.id],
    )
    plan = MovePlan(id="brp.replay", instance_id=instance.id, call_index=0,
                    moves=moves, provenance=provenance)
    count = MoveCount(
        relocations=relocation_count, gate_moves=gate_count,
        # Preserve the zero-per-later-call projection of the empty ship fixture.
        future=project_future_moves(instance, state.ship, from_call=instance.call_index),
        weighted_nonproductive=instance.objective.relocate * relocation_count,
    )
    if expected_relocations is not None and count.relocations != expected_relocations:
        raise ValueError(f"replayed {count.relocations} relocations, expected {expected_relocations}")
    return ReplayResult(count, plan, state)

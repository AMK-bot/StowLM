"""Compact move-decision observations (task 4.5, D9).

The model sees indexed boxes and yard retrieval order; the feasibility engine
retains the exact Instance and enforces all physical constraints. This is a
lossy observation, never a physics approximation or a substitute for checking.
Legacy v2.2 rounds masses upward to 2000 kg buckets with exact D25 class cues.
Activated v3.0 puts exact dense mass ranks beside each box record; ties share a
rank. Coarse mass buckets remain magnitude cues; ship class cues are removed. Heights are
rounded to mm. DG is
summarized by class and segregation codes, OOG by H/W/L extent categories;
reefer temperature and detailed UN declarations remain only in the full Instance. Unrelated yard
occupancy is aggregated except v3's exact addresses of effective-yard-limit blockers;
exact relocation destinations remain mask decisions.
Static vessel physics tables and box ISO IDs are intentionally absent.
Sources: docs/architecture.md; stowage/actions/expander.py (retrieval dependencies).
"""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from typing import Any

from stowage.conventions import canonical_vessel_id
from stowage.model import Instance

LEGACY_PROMPT_VERSION = "stowlm-prompt@2.2"
PROMPT_VERSION = LEGACY_PROMPT_VERSION
EXPERT_PROMPT_VERSION = "stowlm-prompt@3.0"
# APPROXIMATION: legacy observations bucket mass; both summarize DG/OOG and unrelated
# yard occupancy. Exact Instance values remain authoritative for mask and checker;
# preserving feasibility does not prove that these lost cues preserve plan quality.
SYSTEM_PROMPT = (
    "You are StowLM. Write only boxslot actions using supplied box indices; no analysis. "
    "Load all targets, satisfy all hard rules and minimise crane lifts. "
    "The mask and checker enforce exact vessel physics and full instance constraints.\n"
    "Codes: c=[category,index-runs]; category letters=dimension(A20std,B20HC,C40std,D40HC,E45std,F45HC), "
    "mass(A=2000kg,B=4000,...),POD-call(A=0,...); flags w=exact-VGM E2 class(L/M/H), "
    "h1/h6=E1/E6 heavy when their threshold differs from E2 (else H); null=unknown. "
    "r=reefer,d=DG-class/SG,o=OOG-HWL,s=UHDT-special. y=[length,occupied-tiers,block-index,ordinal=top-box/...]; "
    "z lists block IDs/rows; "
    "ordinal=(bay-1)*block-rows+row-1. g=layout groups of deck,bay-runs,row-runs,tiers,flags "
    "(20=1,40=2,45=4,reefer=8). l=loads,d=discharges,b=onboard,a=anonymous occupancy,x=instructions.\n"
    "Reasoning strength: low."
)

EXPERT_SYSTEM_PROMPT = (
    "Boxslot: hard rules;load all,minimise lifts. "
    "At tolerance0 ranks ascend upward (1=heaviest,ties equal; both 20ft supports); inherited breaches tolerated. "
    "Prefer heavy underdeck. Crossing blocks at H(ship deck)/Y(yard); exclude endpoints; StageA far rows first. "
    "Direct=truck lift,no yard. "
    "Refused primary classes: untouched transit only. Every-lift stability: checker.\n"
    "Codes:c=category+indices:rank. "
    "A-F=20/40/45 std/HC; mass A=2t,+2t; POD A=0. "
    "Flags r/d/o/s/f/h=reefer/DG-SG/OOG-HWL/special-UHDT/direct/heavy. "
    "e=[H,p/s berth,refused,low/high lanes,Y]; null=unknown; IDs=plain/[prefix,width,runs]. "
    "t=positions/y(+port); positions=deck/bays/rows; runs=start:end:step. "
    "y=[length,height,block,ordinal=top-first IDs]; z=[block,rows]; ordinal=(bay-1)*rows+row-1. "
    "j=yard blockers; f=[fixed positions,height,y]. "
    "g=positions/tiers/bits0-3(20/40/45/reefer); l/d/b/a=load/discharge/onboard/occupancy. "
    "Defaults: stage A,call0,empty. u=tolerance/heavykg(default0/20000); kg exact; Reasoning strength: low."
)
# APPROXIMATION: direct-delivery trucks are available at their lift, without a
# timing schedule (contract A4). Numeric HEEL authority remains unobserved.


def active_prompt_version() -> str:
    """Select semantics from the one catalogue activation switch, never the instance age."""
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    return EXPERT_PROMPT_VERSION if rule_freeze_v1_enabled() else PROMPT_VERSION


def prompt_version(instance: Instance) -> str:
    return active_prompt_version()


def system_prompt(instance: Instance) -> str:
    return EXPERT_SYSTEM_PROMPT if prompt_version(instance) == EXPERT_PROMPT_VERSION else SYSTEM_PROMPT


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _runs(values) -> str:
    numbers = sorted(set(values))
    result = []
    start = end = None
    for value in numbers:
        if end is not None and value == end + 1:
            end = value
        else:
            if end is not None:
                result.append(str(start) if start == end else f"{start}-{end}")
            start = end = value
    if end is not None:
        result.append(str(start) if start == end else f"{start}-{end}")
    return "/".join(result)


def _unruns(text: str) -> list[int]:
    result = []
    for part in text.split("/") if text else []:
        if ":" in part:
            start, end, step = map(int, part.split(":"))
            result.extend(range(start, end + 1, step))
            continue
        endpoints = [int(x) for x in part.split("-")]
        result.extend(range(endpoints[0], endpoints[-1] + 1))
    return result


def _positions(stack_ids) -> list[list[Any]]:
    """Factor identical bay row patterns into Cartesian products (no lost slots)."""
    by_deck = defaultdict(lambda: defaultdict(list))
    for sid in stack_ids:
        by_deck[sid[-1]][int(sid[:3])].append(int(sid[3:5]))
    result = []
    for deck, bays in sorted(by_deck.items()):
        by_rows = defaultdict(list)
        for bay, rows in sorted(bays.items()):
            by_rows[_runs(rows)].append(bay)
        result.extend([deck, _runs(bay_ids), rows] for rows, bay_ids in sorted(by_rows.items()))
    return result


def _stack_ids(positions) -> list[str]:
    return [f"{bay:03}{row:02}{deck}" for deck, bays, rows in positions
            for bay in _unruns(bays) for row in _unruns(rows)]


def _compact_positions(stack_ids):
    positions = _positions(stack_ids)
    for entry in positions:
        for column in (1, 2):
            numbers = _unruns(entry[column])
            if len(numbers) >= 3 and len(set(b - a for a, b in zip(numbers, numbers[1:]))) == 1:
                step = numbers[1] - numbers[0]
                if step > 1:
                    entry[column] = f"{numbers[0]}:{numbers[-1]}:{step}"
    return positions


def expand_layout(layout: dict[str, Any]) -> dict[str, int]:
    """Expand exact cell IDs and bit flags: 20=1, 40=2, 45=4, reefer=8."""
    cells = {}
    for positions, tiers, flags in layout.get("groups", []):
        for sid in _stack_ids(positions):
            for i, tier in enumerate(tiers):
                cells[f"{sid[:5]}{tier:02}"] = flags[i] if isinstance(flags, list) else flags
    return cells


_DIMS = [(length, height) for length in (20, 40, 45) for height in (2591, 2896)]


def _cargo_code(code):
    length, height, mass, pod, flags = code
    flags = {{"reefer": "r", "dg": "d", "oog": "o", "special_stowage": "s",
              "weight_class": "w", "e1_heavy": "h1", "e6_heavy": "h6",
              "direct_delivery": "f", "heavy_preference": "h"}.get(k, k): v for k, v in flags.items()}
    if "s" in flags:
        flags["s"] = "".join({"under_deck": "U", "on_deck": "D", "away_from_heat": "H", "top_stowage": "T"}[v] for v in flags["s"])
    if "d" in flags:
        flags["d"] = {{"imdg_class": "c", "segregation_codes": "s"}.get(k, k): v for k, v in flags["d"].items()}
    if (length, height) in _DIMS and 1 <= mass // 2000 <= 26 and pod < 26:
        compact = chr(65 + _DIMS.index((length, height))) + chr(64 + mass // 2000) + chr(65 + pod)
        return [compact, flags] if flags else compact
    return [length, height, mass, pod, flags]


def _cargo_values(code):
    if isinstance(code, str) or (len(code) == 2 and isinstance(code[0], str)):
        compact, flags = (code, {}) if isinstance(code, str) else code
        length, height = _DIMS[ord(compact[0]) - 65]
        code = [length, height, (ord(compact[1]) - 64) * 2000, ord(compact[2]) - 65, flags]
    length, height, mass, pod, flags = code
    flags = {{"r": "reefer", "d": "dg", "o": "oog", "s": "special_stowage",
              "w": "weight_class", "h1": "e1_heavy", "h6": "e6_heavy",
              "f": "direct_delivery", "h": "heavy_preference"}.get(k, k): v for k, v in flags.items()}
    if "special_stowage" in flags:
        flags["special_stowage"] = [{"U": "under_deck", "D": "on_deck", "H": "away_from_heat", "T": "top_stowage"}[v] for v in flags["special_stowage"]]
    if "dg" in flags:
        flags["dg"] = {{"c": "imdg_class", "s": "segregation_codes"}.get(k, k): v for k, v in flags["dg"].items()}
    return [length, height, mass, pod, flags]


def _weight_policy(instance: Instance):
    """Use D25's override-first lookup, without trusting cached cargo labels."""
    from stowage.rules.catalogue import load_catalogue

    catalogue = load_catalogue()

    def boundary(rule, name):
        value = catalogue.resolve_parameter(rule, name, instance.rule_overrides)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0):
            return None
        return value

    medium = boundary("expert.e2.weight_order", "medium_min_kg")
    heavy = boundary("expert.e2.weight_order", "heavy_min_kg")
    valid_classes = medium is not None and heavy is not None and 0 < medium < heavy
    return (catalogue.weight_classes.parameters["labels"].value, medium, heavy, valid_classes,
            boundary("expert.e1.heavy_bottom", "heavy_threshold"),
            boundary("expert.e6.heavy_under_deck", "heavy_threshold"))


def _weight_flags(mass, policy):
    labels, medium, heavy, valid_classes, e1, e6 = policy
    # The rounded mass channel can cross either inclusive D25 boundary. Carry
    # the exact VGM-derived class independently; class_order/tolerance remain
    # available in the existing rule_overrides instruction channel.
    flags = {"weight_class": labels[int(mass >= medium) + int(mass >= heavy)]
             if valid_classes else None}
    for name, threshold in (("e1_heavy", e1), ("e6_heavy", e6)):
        if not valid_classes or threshold != heavy:
            flags[name] = mass >= threshold if threshold is not None else None
    return flags


def _call_rules(instance: Instance) -> list[list[Any]]:
    """Resolve future calls exactly as crane/voyage consumers do; no placeholder H."""
    from stowage.ports import crane_height_at_call, resolve_port_call_profile, yard_blocking_tiers

    result = []
    for call in instance.rotation.calls:
        current = call.index == instance.call_index
        profile = instance.port if current else resolve_port_call_profile(call, current_port=instance.port)
        height = instance.port.max_crane_height_tiers if current else crane_height_at_call(call, current_port=instance.port)
        refused = sorted({str(value) for rule in profile.port_rules if rule.rule_id == "port.dg.refused_class"
                          for value in rule.params.get("classes", [])}) if profile is not None else []
        lanes = [_id_runs([b.id for b in profile.yard.blocks if b.truck_lane_side == side])
                 if profile is not None else [] for side in ("low", "high")]
        yard_limits = defaultdict(list)
        if profile is not None:
            for block in profile.yard.blocks:
                yard_limits[yard_blocking_tiers(profile, block)].append(block.id)
        yard_limit = (next(iter(yard_limits)) if len(yard_limits) == 1 else
                      [[limit, _id_runs(ids)] for limit, ids in sorted(yard_limits.items())] if yard_limits else None)
        row = [height, {"port": "p", "starboard": "s"}.get(profile.berthing_side) if profile is not None else None,
               refused, lanes, yard_limit]
        if row[-1] is None:
            row.pop()
            if row[-1] == [[], []]:
                row.pop()
                if not row[-1]:
                    row.pop()
                    if row[-1] is None:
                        row.pop()
        result.append(row)
    return result


def _id_runs(ids):
    """Factor numeric block suffixes without losing IDs or zero-padding."""
    plain, groups = [], defaultdict(list)
    for value in ids:
        match = re.fullmatch(r"(.*?)([0-9]+)", value)
        if match:
            prefix, digits = match.groups()
            groups[(prefix, len(digits))].append(int(digits))
        else:
            plain.append(value)
    return ["/".join(sorted(plain)), *[[prefix, width, _runs(numbers)] for (prefix, width), numbers in sorted(groups.items())]] if ids else []


def _expand_ids(patterns):
    if not patterns:
        return []
    return [*(patterns[0].split("/") if patterns[0] else []),
            *[f"{prefix}{number:0{width}d}" for prefix, width, numbers in patterns[1:] for number in _unruns(numbers)]]


def _rank_records(rows):
    groups = defaultdict(list)
    for index, rank in rows:
        groups[rank].append(index)
    return " ".join(f"{_runs(indices)}:{rank}" for rank, indices in sorted(groups.items()))


def _stack_geometry(instance: Instance) -> list[list[Any]]:
    """Retain each physical endpoint's own y, including coordinate ties."""
    cells = {cell.id: cell for cell in instance.vessel.cells + instance.vessel.fixed_load_cells}
    groups = defaultdict(list)
    # Fixed cargo never changes. Only fixed stacks at H can affect crossing;
    # their own coordinates are carried in f, avoiding a duplicate whole hull.
    for stack in instance.vessel.stacks:
        if stack.cell_ids:
            groups[cells[stack.cell_ids[0]].tcg_m].append(stack.id)
    return [[_compact_positions(stacks), transverse] for transverse, stacks in sorted(groups.items())]


def _fixed_deck_stacks(instance: Instance, blocking_height: int | None) -> list[list[Any]]:
    """Exact immutable blockers across every known route H, never silhouettes."""
    cells = {cell.id: cell for cell in instance.vessel.fixed_load_cells}
    occupied = {cid for load in instance.vessel.fixed_loads for box in load.cargo_boxes or [] for cid in box.cell_ids}
    groups = defaultdict(list)
    for stack in instance.vessel.fixed_load_stacks:
        if not stack.on_deck:
            continue
        height = max((i + 1 for i, cid in enumerate(stack.cell_ids) if cid in occupied), default=0)
        if blocking_height is not None and height >= blocking_height:
            groups[(height, cells[stack.cell_ids[0]].tcg_m)].append(stack.id)
    return [[_compact_positions(stacks), height, transverse] for (height, transverse), stacks in sorted(groups.items())]


def _wire(instance: Instance, include_layout: bool) -> dict[str, Any]:
    from llm.stowlm.format import BoxIndex

    index = BoxIndex([box.id for box in instance.containers])
    version = prompt_version(instance)
    exact_kg = version == EXPERT_PROMPT_VERSION
    call_rules = _call_rules(instance) if exact_kg else []
    required = set(instance.load_list)
    relevant = required | {p.container_id for p in instance.onboard.placements} | set(instance.yard.quay_buffer)
    blocks = instance.port.yard.blocks
    block_rows = {block.id: block.n_rows for block in blocks}
    block_indices = {block.id: i for i, block in enumerate(blocks)}
    def short_key(key):
        block, bay, row = key.split("-")
        ordinal = (int(bay)-1)*block_rows[block]+int(row)-1
        return str(ordinal)

    yard = defaultdict(list)
    occupancy = defaultdict(int)
    crossing_blockers = defaultdict(list)
    if exact_kg:
        from stowage.ports import yard_blocking_tiers

        yard_heights = {block.id: yard_blocking_tiers(instance.port, block) for block in blocks}
    for stack in instance.yard.stacks:
        targets = [i for i, cid in enumerate(stack.boxes) if cid in required]
        if targets:
            suffix = list(reversed(stack.boxes[min(targets):]))
            relevant.update(suffix)
            yard[(stack.length_ft, len(stack.boxes), block_indices[stack.key.split("-")[0]])].append(
                short_key(stack.key) + "=" + "/".join(str(index.index(cid)) for cid in suffix))
        else:
            occupancy[(stack.key.split("-")[0], stack.length_ft, len(stack.boxes))] += 1
            if exact_kg and len(stack.boxes) >= yard_heights[stack.key.split("-")[0]]:
                crossing_blockers[(stack.length_ft, len(stack.boxes), block_indices[stack.key.split("-")[0]])].append(int(short_key(stack.key)))
    categories = defaultdict(list)
    mass_ranks = {mass: rank for rank, mass in enumerate(sorted(
        {box.gross_mass_kg for box in instance.containers if box.id in relevant}, reverse=True), 1)} if exact_kg else {}
    if exact_kg:
        from stowage.rules.catalogue import load_catalogue

        catalogue = load_catalogue()
        tolerance = catalogue.resolve_parameter("expert.e2.weight_order", "weight_order_tolerance_kg", instance.rule_overrides)
        heavy_threshold = catalogue.resolve_parameter("expert.e6.heavy_under_deck", "heavy_on_deck_threshold_kg", instance.rule_overrides)
    # Activated E2 no longer has the legacy D25 class parameters. Looking them
    # up crashes even otherwise valid direct-delivery examples.
    weight_policy = None if exact_kg else _weight_policy(instance)
    for box in instance.containers:
        if box.id not in relevant:
            continue
        flags = box.model_dump(mode="json", include={"reefer", "reefer_setpoint_c", "dg", "oog", "special_stowage"},
                               exclude_defaults=True, exclude_none=True)
        if not exact_kg:
            flags.update(_weight_flags(box.gross_mass_kg, weight_policy))
        else:
            if box.direct_delivery:
                flags["direct_delivery"] = True
            if box.gross_mass_kg >= heavy_threshold:
                flags["heavy_preference"] = True
        if "dg" in flags:
            dg = flags["dg"]
            flags["dg"] = {k: dg[k] for k in ("imdg_class", "segregation_codes") if dg.get(k)}
        flags.pop("reefer_setpoint_c", None)
        if "oog" in flags:
            dimensions = flags["oog"]
            flags["oog"] = "".join(code for prefix, code in (("over_height", "H"), ("over_width", "W"), ("over_length", "L"))
                                     if any(value > 0 for key, value in dimensions.items() if key.startswith(prefix)))
        pod = next(call.index for call in instance.rotation.calls[instance.call_index + 1:] if call.locode == box.pod) if box.id not in instance.discharge_list else instance.call_index
        mass = math.ceil(box.gross_mass_kg / 2000) * 2000
        code = [box.length_ft, round(box.height_m * 1000), mass, pod, flags]
        categories[_json(code)].append((index.index(box.id), mass_ranks[box.gross_mass_kg]) if exact_kg else index.index(box.id))
    vessel = instance.vessel
    wire = {
        "v": version, "i": instance.id, "s": instance.stage, "k": instance.call_index,
        "ship_id": [canonical_vessel_id(vessel.id),
                    canonical_vessel_id(vessel.slice_of) if vessel.slice_of is not None else None],
        "r": [[c.locode, c.max_on_deck_tiers] for c in instance.rotation.calls],
        "l": _runs(index.index(cid) for cid in instance.load_list),
        "d": _runs(index.index(cid) for cid in instance.discharge_list),
        "c": [[_cargo_code(json.loads(code)), _rank_records(ids) if exact_kg else _runs(ids)]
              for code, ids in sorted(categories.items())],
        "z": [[b.id, b.n_rows] for b in blocks],
        "y": [[length, height, block, " ".join(stacks)]
              for (length, height, block), stacks in sorted(yard.items())],
        "a": [[block, length, height, count] for (block, length, height), count in sorted(occupancy.items())],
        "q": [index.index(cid) for cid in instance.yard.quay_buffer],
        "b": [[p.slot, index.index(p.container_id)] for p in instance.onboard.placements],
        "o": instance.onboard.covers_off,
        "p": [instance.port.locode, instance.port.yard_max_tiers, instance.port.quay_crane.max_on_deck_tiers],
        "x": {"instructions": [c.model_dump(mode="json", exclude_none=True) for c in instance.plan_constraints],
              "rule_overrides": instance.rule_overrides,
              "port_rules": [r.model_dump(mode="json") for r in instance.port.port_rules]},
    }
    if exact_kg:
        from stowage.rules.catalogue import load_catalogue

        catalogue = load_catalogue()
        wire["e"] = call_rules
        wire["p"] = wire["p"][1:]
        if (tolerance, heavy_threshold) != (0, 20000):
            wire["u"] = [tolerance, heavy_threshold]
        # Rank comparisons decide the binding zero-tolerance contract exactly.
        # Nonzero override policies retain exact, individually delimited kg.
        if tolerance != 0:
            wire["kg"] = [[index.index(box.id), box.gross_mass_kg] for box in sorted(instance.containers, key=lambda box: box.id) if box.id in relevant]
        wire["t"] = _stack_geometry(instance)
        # An immutable stack below every known route H can never block a lift.
        # Retain every fixed blocker relevant at this or a later call.
        blocking_height = min((rule[0] for rule in call_rules[instance.call_index:] if rule[0] is not None), default=None)
        wire["f"] = _fixed_deck_stacks(instance, blocking_height)
        wire["j"] = [[length, height, block, _runs(ordinals)] for (length, height, block), ordinals in sorted(crossing_blockers.items())]
        ship_rules = {"expert.e1.heavy_bottom", "expert.e2.weight_order"}
        obsolete = {"class_order", "order_mode", "medium_min_kg", "heavy_min_kg", "heavy_threshold"}
        wire["x"]["rule_overrides"] = {
            rule: {name: value for name, value in values.items() if name not in obsolete}
            if rule in ship_rules and isinstance(values, dict) else values
            for rule, values in instance.rule_overrides.items()
        }
        wire["x"] = {key: value for key, value in wire["x"].items() if value}
        if not wire["x"]:
            del wire["x"]
        for key in ("a", "b", "d", "q", "o", "f", "j"):
            if not wire[key]:
                del wire[key]
        if wire["k"] == 0:
            del wire["k"]
        if wire["s"] == "A":
            del wire["s"]
    if include_layout:
        positions = _compact_positions if exact_kg else _positions
        cells = {c.id: c for c in vessel.cells}
        groups = defaultdict(list)
        for stack in vessel.stacks:
            column = [cells[cid] for cid in stack.cell_ids]
            flags = [int(c.accepts_20) + 2*int(c.accepts_40) + 4*int(c.accepts_45) + 8*int(c.reefer_plug) for c in column]
            if flags and len(set(flags)) == 1:
                flags = flags[0]
            signature = _json([[int(c.id[-2:]) for c in column], flags])
            groups[signature].append(stack.id)
        wire["g"] = {"groups": [[positions(ids), *json.loads(signature)] for signature, ids in sorted(groups.items())],
                     "covers": [[c.id, positions(c.hold_stack_ids), positions(c.deck_stack_ids)] for c in vessel.hatch_covers]}
    return wire


def _facts(wire: dict[str, Any]) -> dict[str, Any]:
    exact_kg = wire["v"] == EXPERT_PROMPT_VERSION
    cargo = []
    for code, records in wire["c"]:
        if exact_kg:
            for record in records.split():
                indices, rank = record.split(":")
                for i in _unruns(indices):
                    values = _cargo_values(code)
                    values[-1]["mass_rank"] = int(rank)
                    cargo.append([i, *values])
        else:
            cargo.extend([i, *_cargo_values(code)] for i in _unruns(records))
    yard = []
    for entry in wire["y"]:
        # v2.1 factors the block once per group; v2.0 repeats names in keys.
        if len(entry) == 4:
            length, height, block_index, group = entry
            group_block = wire["z"][block_index][0]
        else:
            length, height, group = entry
            group_block = None
        for item in group.split():
            key, boxes = item.split("=")
            if group_block is not None:
                block, ordinal = group_block, key
            elif ":" in key:
                block, ordinal = key.split(":")
                if wire["v"] == "stowlm-prompt@2.1":
                    block = wire["z"][int(block)][0]
            else:
                block, ordinal = wire["z"][0][0], key
            nrows = dict(wire["z"])[block]
            bay, row = divmod(int(ordinal), nrows)
            address = f"{block}-{bay+1:03}-{row+1:02}"
            yard.append([address, length, height, [int(i) for i in boxes.split("/")]])
    facts = {"format": wire["v"], "instance": wire["i"], "stage": wire.get("s", "A"), "call": wire.get("k", 0),
             "vessel": {"id": wire["ship_id"][0], "slice_of": wire["ship_id"][1]},
             "rotation": wire["r"], "load": _unruns(wire["l"]), "discharge": _unruns(wire.get("d", "")),
             "cargo": sorted(cargo), "yard": sorted(yard), "anonymous_occupancy": wire.get("a", []),
             "ship": {"placements": wire.get("b", []), "covers_off": wire.get("o", {})}, "quay": wire.get("q", []),
             "port": wire["p"], "constraints": wire.get("x", {})}
    if wire["v"] == EXPERT_PROMPT_VERSION:
        facts["call_rules"] = [{"height_tiers": height, "berthing_side": {"p": "port", "s": "starboard"}.get(side),
                                "crane_limits_known": height is not None, "refused_classes": refused,
                                "truck_lane_sides": sorted([[block, lane] for lane, patterns in zip(("low", "high"), lanes)
                                                             for block in _expand_ids(patterns)]),
                                "yard_blocking_tiers": sorted([[block, yard_limit] for patterns in lanes for block in _expand_ids(patterns)])
                                if isinstance(yard_limit, int) else sorted([[block, limit] for limit, patterns in yard_limit or []
                                                                          for block in _expand_ids(patterns)])}
                               for height, side, refused, lanes, yard_limit in
                               [(row + [None, None, [], [[], []], None][len(row):]) for row in wire["e"]]]
        weight_rules = wire.get("u", [0, 20000])
        facts["weight_rules"] = {"tolerance_kg": weight_rules[0], "heavy_under_deck_threshold_kg": weight_rules[1]}
        current = facts["call_rules"][wire.get("k", 0)]
        facts["port"] = [wire["r"][wire.get("k", 0)][0], *wire["p"], current["height_tiers"], current["berthing_side"]]
        facts["constraints"] = {"instructions": [], "rule_overrides": {}, "port_rules": [], **wire.get("x", {})}
        facts["stack_geometry"] = sorted([[sid, transverse] for positions, transverse in wire["t"] for sid in _stack_ids(positions)] +
                                         [[sid, transverse] for positions, _, transverse in wire.get("f", []) for sid in _stack_ids(positions)])
        facts["fixed_deck_stacks"] = wire.get("f", [])
        facts["anonymous_crossing_blockers"] = wire.get("j", [])
        for row in facts["cargo"]:
            flags = row[5]
            flags.setdefault("direct_delivery", False)
            flags.setdefault("heavy_preference", False)
            flags["route_refused_classes"] = [[call, facts["call_rules"][call]["refused_classes"]]
                                              for call in range(wire.get("k", 0), row[4] + 1)
                                              if facts["call_rules"][call]["refused_classes"]]
        if "kg" in wire:
            facts["exact_mass_kg"] = wire["kg"]
    if "g" in wire:
        facts["layout"] = wire["g"]
    return facts


def prompt_facts(instance: Instance, *, include_layout: bool = True) -> dict[str, Any]:
    """Cargo rows: index, length_ft, height_mm, kg bucket, POD call, flags (v3 mass_rank)."""
    return _facts(_wire(instance, include_layout))


def instance_to_prompt(instance: Instance, *, include_layout: bool = True) -> str:
    """Dictionary-coded cargo and range-coded layout; no ISO IDs or physics tables."""
    return _json(_wire(instance, include_layout))


def parse_prompt(text: str) -> dict[str, Any]:
    wire = json.loads(text)
    if not isinstance(wire, dict) or wire.get("v") not in {"stowlm-prompt@2.0", "stowlm-prompt@2.1", PROMPT_VERSION, EXPERT_PROMPT_VERSION}:
        raise ValueError("unsupported StowLM prompt format")
    return _facts(wire)


def encode_prompt(instance: Instance, tokenizer: Any, *, include_layout: bool = True) -> tuple[int, ...]:
    """Native Glimmer chat prefill ending inside the assistant user-recipient turn.

    The native template's generation prefix ends at ``assistant`` and allows
    ``to=self`` reasoning. Supplying ``to=user<|message|>`` fixes the recipient;
    SFT labels begin immediately with action IDs, never an analysis channel.
    """
    from llm.stowlm.format import build_sequence

    return build_sequence(tokenizer, [
        "<|begin_of_text|><|start|>system<|message|>", system_prompt(instance),
        '\n\n# Valid recipients: "self", "user".<|eot|><|start|>user<|message|>', instance_to_prompt(instance, include_layout=include_layout),
        "<|eot|><|start|>assistant to=user<|message|>",
    ])

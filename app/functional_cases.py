"""Fresh synthetic calls, using the envelope cohort's producer and selection.

Generation belongs in a dedicated worker: producer adapters and cache locations
are process-global. Importing this module, options(), and seed_status() load no
models and do not import the engine or read stored map payloads.
Freshness means exclusion from the seed inventories and protected namespaces
below. Artifact hashes bind bytes and provenance; they do not prove cargo novelty.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import redirect_stdout
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import importlib
import importlib.util
import io
import csv
import json
import math
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "experiments/mvp/ui/functional/cache"
PRODUCER = ROOT / "experiments/mvp/corpus_tools/build.py"
SHIPS = ("feeder_500", "feeder_800", "feeder_1700")
SIZES = ("tiny", "small", "medium")
GENERATORS = ("random_yard", "simulated_yard")
BOOKING_MAX_BOXES = 6
SYNTHETIC_REEFER_SETPOINT_C = -18.0
DEFAULTS = dict(ship="feeder_500", size_class="tiny", fill_pct=60,
                generator="random_yard", port="laem_chabang_like", seed=None)

# Exact formulas, without importing the preregistered map generator:
# pipeline/maps/generate.py:specs; sealed/measurements/ext-1/map-generation/PREREG.md;
# corpus_tools/test_v3_identity.py:make_specs (test-v3 is itself sealed).
_COHORTS = {
    "dev": frozenset(306100000 + 100*c + r for c in range(8) for r in range(4)),
    "sealed": frozenset(306110000 + 100*c + r for c in range(8) for r in range(3)),
    "ext-1": frozenset(306320000 + 100*c + r for c in range(8) for r in range(3)),
    "test-v3": frozenset(range(187000000, 187000015)) | frozenset(range(187100000, 187100030)),
}
# Conservatively reserve whole rung-address namespaces, including roots not
# emitted into the accessible inventories. Sources: corpus_tools/build.py,
# regenerate.py, campaign.py, rebalance_v21.py, test_v3_identity.py. These are
# reservations, not published cohort counts; never read valsplit or testv3.
_PROTECTED_RANGES = {
    "corpus/test": (range(8_000_000, 8_300_000),),
    "corpus/test-v2": (range(28_000_000, 28_300_000),),
    "corpus/val": (range(20_000_000, 20_300_000),),
    "corpus/val-v2": (range(40_000_000, 40_300_000),),
    "corpus/val-v2.1": (range(41_100_000, 41_200_000),),
    "test-v3": (range(187_000_000, 187_200_000),),
}


class CaseError(ValueError):
    """A request or producer failure that can be shown directly to a planner."""


@lru_cache(maxsize=1)
def _published_metadata():
    """Seed memberships and protection, from metadata only; no map contents."""
    cohorts = {name: set(seeds) for name, seeds in _COHORTS.items()}
    protected = {name: set(_COHORTS[name]) for name in ("sealed", "ext-1", "test-v3")}

    def collect(value, seeds, group, split=None, held_out=False):
        if isinstance(value, list):
            for item in value:
                collect(item, seeds, group, split, held_out)
        elif isinstance(value, dict):
            if isinstance(value.get("split"), str):
                split = value["split"].lower().replace("-", "_")
            split_is_protected = bool(split and split.startswith(
                ("val", "test", "heldout", "held_out", "sealed", "unseen", "blind")))
            held_out = held_out or split_is_protected or any(
                value.get(flag) is True for flag in ("sealed", "held_out", "heldout"))
            if type(value.get("seed")) is int:
                seed = value["seed"]
                seeds.add(seed)
                label = f"{group}/{split}" if split and group not in _COHORTS else group
                cohorts.setdefault(label, set()).add(seed)
                if held_out:
                    protected.setdefault(label, set()).add(seed)
            for key in ("specs", "spec", "entries", "files", "maps", "groups", "shards",
                        "seed_selection", "config", "records"):
                child = value.get(key)
                if key == "groups" and isinstance(child, dict):
                    for child_group in child.values():
                        collect(child_group, seeds, group, split, held_out)
                elif isinstance(child, (list, dict)):
                    collect(child, seeds, group, split, held_out)

    inventories = {
        "instances-v2": [ROOT / "experiments/instances-v2/manifest.json"],
        "splits-v1": [ROOT / "experiments/splits/v1/manifest.json"],
        "corpus": [root / name for root in (
            ROOT / "experiments/mvp/corpus",
            ROOT / "experiments/mvp/corpus/archive/v1",
            ROOT / "experiments/mvp/corpus/archive/v2-before-D90")
            for name in ("manifest.json", "train.requested.json", "val.requested.json", "test.requested.json")],
        "test-v3": [ROOT / "experiments/mvp/corpus/test-v3/manifest.json"],
        "ext-1": [ROOT / "experiments/mvp/pipeline/sealed/measurements/ext-1/maps-manifest.json"],
    }
    for root in (ROOT / "experiments/mvp/corpus",
                 ROOT / "experiments/mvp/corpus/archive/v1",
                 ROOT / "experiments/mvp/corpus/archive/v2-before-D90"):
        # Selection inventories include rejected showcase roots, which are
        # already published even when they never emitted an accepted shard.
        inventories["corpus"].extend(sorted((root / "showcase").glob("*.selection.json")))
    for name, paths in inventories.items():
        seeds = cohorts.setdefault(name, set())
        for path in paths:
            if path.is_file():
                file_split = path.name.split(".", 1)[0] if path.name.endswith(".requested.json") else None
                collect(json.loads(path.read_text()), seeds, name, file_split,
                        name in {"sealed", "ext-1", "test-v3"})
    return ({name: frozenset(seeds) for name, seeds in cohorts.items()},
            {name: frozenset(seeds) for name, seeds in protected.items()})


def seed_status(seed: int) -> dict:
    if type(seed) is not int or not 0 <= seed < 2**31:
        raise CaseError("Seed must be an integer from 0 through 2147483647.")
    cohorts, protected = _published_metadata()
    known = [name for name, seeds in cohorts.items() if seed in seeds]
    refused = [name for name, seeds in protected.items() if seed in seeds]
    for name, ranges in _PROTECTED_RANGES.items():
        if any(seed in reserved for reserved in ranges):
            if name not in known:
                known.append(name)
            if name not in refused:
                refused.append(name)
    label = ("Refused: held-out seed of " + ", ".join(refused) if refused else
             "Published seed: " + ", ".join(known) if known else
             "Fresh seed: in no published map set")
    # Retain the compatibility keys: `sealed` now means refused for any held-out
    # membership, while the label states the actual set rather than calling it sealed.
    return dict(seed=seed, known_in=known, unseen=not known, sealed=bool(refused), label=label)


def new_seed() -> int:
    while True:
        seed = secrets.randbelow(2**31)
        if seed_status(seed)["unseen"]:
            return seed


def _headline_envelope(envelope):
    """The claims contract is narrower than the runner's tiny/small gate."""
    reasons = [dict(reason) for reason in envelope["reasons"]]
    ship = envelope["numbers"]["ship"]
    if ship not in ("feeder_500", "feeder_800") and not any(reason["test"] == "ship" for reason in reasons):
        reasons.append(dict(
            test="ship", measured=ship, limit=["feeder_500", "feeder_800"],
            message=f"{ship} is outside the headline envelope: only feeder_500 and feeder_800 (500–800 TEU), including their tiny and small sections."))
    return dict(in_envelope=not reasons, reasons=reasons)


def options() -> dict:
    return dict(
        ships=[dict(id=ship, teu=teu, in_envelope_sizes=list(SIZES if teu < 1000 else ()))
               for ship, teu in zip(SHIPS, (500, 800, 1700))],
        size_classes=list(SIZES), generators=list(GENERATORS),
        ports=sorted(path.stem for path in (ROOT / "params/ports").glob("*.toml")),
        fill_pct=dict(min=10, max=100, envelope_max=70, default=60),
        envelope=dict(max_boxes=195, teu=[500, 800], max_fill=.70,
                      arrival="empty ship, one loading call"),
        defaults=dict(DEFAULTS), published_seeds={name: len(seeds) for name, seeds in _COHORTS.items()})


def _request(request):
    if not isinstance(request, dict):
        raise CaseError("The call request must be a JSON object.")
    unknown = set(request) - DEFAULTS.keys()
    if unknown:
        raise CaseError("Unknown call parameter: " + ", ".join(sorted(map(str, unknown))) + ".")
    normalized = {**DEFAULTS, **request}
    for field, choices in (("ship", SHIPS), ("size_class", SIZES), ("generator", GENERATORS),
                           ("port", options()["ports"])):
        if normalized[field] not in choices:
            raise CaseError(f"Choose a supported {field.replace('_', ' ')}.")
    fill = normalized["fill_pct"]
    if isinstance(fill, bool) or not isinstance(fill, (int, float)) or not math.isfinite(fill) or not 10 <= fill <= 100:
        raise CaseError("Fill must be a finite percentage from 10 through 100.")
    if normalized["seed"] is not None:
        status = seed_status(normalized["seed"])
        if status["sealed"]:
            raise CaseError(status["label"] + "; choose a fresh seed.")
    return normalized


def _atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix="." + path.name + "-", suffix=".partial", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _configure(ship):
    # Same OFF generation policy as maps/generate.configure, with a writable
    # disposable cache. Never write to its frozen runtime or the demo cache.
    for key in list(os.environ):
        if key.startswith("STOWAGE_"):
            del os.environ[key]
    vessels = CACHE / "vessels"
    vessels.mkdir(parents=True, exist_ok=True)
    os.environ["STOWAGE_VESSEL_CACHE_DIR"] = str(vessels)
    # Reuse only the current content-keyed full-vessel physics, never cargo.
    from stowage.vessel.fleet import _parameter_bytes
    from stowage.vessel.fleet_cache import _cache_key
    key = _cache_key(ship, "full", True, _parameter_bytes(ship))
    filename = f"{ship}.full.hull.{key}.json"
    source = ROOT / "experiments/cache/vessels" / filename
    target = vessels / filename
    if not target.exists() and source.is_file():
        _atomic_bytes(target, source.read_bytes())
    # Sounding preprocessing writes on a cache miss; isolate those writes too.
    # The other stability tables are read-only during ordinary generation.
    sounding = importlib.import_module("stowage.checker.stability_sounding")
    sounding.CACHE_DIRECTORY = CACHE / "stability"


def _produce(request, seed, private_dir):
    _configure(request["ship"])
    # Import build.py as configure() does, without generate.py's prereg asserts.
    module_spec = importlib.util.spec_from_file_location("functional_corpus_build", PRODUCER)
    build = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = build
    module_spec.loader.exec_module(build)
    spec = dict(split="functional", rung=request["size_class"], vessel=request["ship"],
                generator=request["generator"], seed=seed, port=request["port"], noise=.5,
                n_boxes=None, dense_20ft=False, tiny_dg_enabled=True, max_attempts=100,
                booking_slack=0., booking_mass_fraction=None)
    sources = build.source_snapshot()
    # Producer diagnostics belong in worker logs; the CLI's stdout is case.json.
    with redirect_stdout(sys.stderr):
        result = build.generate_one((spec, str(private_dir), sources))
    if result["status"] == "failed":
        raise CaseError("The synthetic producer could not generate this request with this seed; choose different parameters or a new seed.")
    _, parent = build.validate_shard(build.shard_path(spec, private_dir), current_sources=sources)
    return parent


def _ordered(cargo, seed):
    # Copied selection only from pipeline/maps/generate.py:ordered (C6 namespace
    # and tie-breaking); do not import that preregistered generator itself.
    groups = defaultdict(list)
    for box in cargo:
        key = json.dumps([box["pod"], box["length_ft"], box["reefer"], box["dg"] is not None,
                          box["empty"], box["special_stowage"]], sort_keys=True)
        groups[key].append(box)
    for group in groups.values():
        group.sort(key=lambda box: hashlib.sha256(f"C6:{seed}:{box['id']}".encode()).hexdigest())
    counts = {key: len(group) for key, group in groups.items()}
    used, result = Counter(), []
    for n in range(len(cargo)):
        key = min((key for key in groups if used[key] < counts[key]),
                  key=lambda key: (-((n+1)*counts[key]/len(cargo)-used[key]), key))
        result.append(groups[key][used[key]])
        used[key] += 1
    return result


def _select(parent, request):
    from stowage.frozen import current_provenance, require_clean
    from stowage.model.instance import Instance
    from stowage.generate.random_yard import regional_booking_mass_budget, _full_vessel
    from stowage.generate.random_export import full_export_policy
    from stowage.params import load_param_file

    require_clean(parent, what="functional parent")
    if (parent.stage != "A" or parent.onboard.placements or parent.discharge_list
            or parent.forecasts or parent.plan_calls):
        raise CaseError("The producer did not return an empty arrival with one loading call.")
    payload = parent.model_dump(mode="json")
    load_ids = set(parent.load_list)
    ordered = _ordered([box for box in payload["containers"] if box["id"] in load_ids], request["seed"])
    limit = math.floor(request["fill_pct"] * len(parent.vessel.cells) / 100)
    selected, physical = [], 0
    # pipeline/maps/generate.py:_make_one fill/195 trim. Keep the full parent
    # yard and its non-load-list boxes (they still block access to selected boxes).
    for box in ordered:
        cost = 1 if box["length_ft"] == 20 else 2
        if physical + cost > limit:
            break
        selected.append(box)
        physical += cost
    selected = selected[:195]
    if not selected:
        raise CaseError("This fill selects no boxes in the chosen section; increase the fill percentage.")
    keep = {box["id"] for box in selected}
    defaulted = []
    for box in payload["containers"]:
        if box["id"] in keep and box["reefer"] and box["reefer_setpoint_c"] is None:
            # This is a synthetic booking declaration, not an inferred cargo
            # requirement. Bind it into the base before hashing or making papers.
            box["reefer_setpoint_c"] = SYNTHETIC_REEFER_SETPOINT_C
            defaulted.append(box["id"])
    payload["load_list"] = sorted(keep)
    payload["id"] = f"functional.{request['ship']}.{request['size_class']}.{request['generator']}.s{request['seed']}.f{request['fill_pct']:g}.{request['port']}"
    capacity = len(parent.vessel.cells)
    payload["family"].update(functional_parent_id=parent.id, n_boxes=str(len(selected)),
                             booking_utilisation_teu=repr(sum(box["length_ft"]/20 for box in selected)/capacity))
    if defaulted:
        payload["family"]["functional_reefer_setpoint_defaults"] = json.dumps(
            {cid: SYNTHETIC_REEFER_SETPOINT_C for cid in defaulted}, sort_keys=True)
    payload["provenance"] = current_provenance(seed=request["seed"], created_by="app.functional_cases").merge_nested(
        parent, parent_ids=[parent.id]).model_dump(mode="json")
    # Intake normalizes booking names to uppercase; explicitly declare the same
    # synthetic identity. This policy changes no vessel or checker limit.
    payload["rule_overrides"].setdefault("intake.policy", {})["vessel"] = parent.vessel.id.upper()
    child = Instance.model_validate(payload)
    reference = full_export_policy(_full_vessel(request["ship"]), load_param_file("cargo", "feeder_export"))
    mass = sum(box.gross_mass_kg for box in child.containers if box.id in keep)
    child.family["booking_utilisation_mass"] = repr(mass / regional_booking_mass_budget(child.vessel, reference, mass))
    require_clean(child, what="functional derivative")
    return child


def _paper_contents(instance, booking_ref):
    from experiments.mvp.pipeline.intake.paper import _ID

    boxes = {box.id: box.model_dump(mode="json") for box in instance.containers}
    if any(_ID.fullmatch(cid) is None for cid in instance.load_list):
        raise CaseError("Cargo paper identities must have four uppercase letters and seven digits; yard identities were not changed.")
    banner = "SYNTHETIC DOCUMENT — synthetic ship and cargo / simulated port"
    vessel = instance.rule_overrides.get("intake.policy", {}).get("vessel", instance.vessel.id.upper())

    def dg_lines(cid, dg):
        lines = []
        for index, entry in enumerate([dg, *dg["additional"]]):
            lines.append(
                f"{cid}; DG {'primary' if index == 0 else 'additional ' + str(index - 1)}; "
                f"UN {entry['un_number']}; CLASS {entry['imdg_class']}; PG {entry['packing_group'] or 'NONE'}; "
                f"LQ {'YES' if entry['limited_quantity'] else 'NO'}; "
                f"MARINE POLLUTANT {'YES' if entry['marine_pollutant'] else 'NO'}")
        return lines

    fields = ["id", "iso_type", "pol", "pod", "gross_mass_kg", "tare_kg", "max_gross_mass_kg",
              "empty", "reefer", "reefer_setpoint_c", "dg", "oog", "special_stowage",
              "booked_vessel", "booking_status"]
    # Direct delivery has no document authority: intake derives it from the
    # current port's policy. Printing it as cargo input causes spurious tasks.
    dg_fields = ["un_number", "imdg_class", "packing_group", "limited_quantity", "marine_pollutant"]
    max_additional = max((len((boxes[cid]["dg"] or {}).get("additional", [])) for cid in instance.load_list), default=0)
    dg_columns = ["dg." + name for name in dg_fields]
    dg_columns += [f"dg.additional.{index}.{name}" for index in range(max_additional) for name in dg_fields]
    stream = io.StringIO(newline="")
    stream.write(banner + "\n")
    writer = csv.DictWriter(stream, fieldnames=fields + dg_columns, lineterminator="\n")
    writer.writeheader()
    note = [banner, "BOOKING CONFIRMATION", f"Booking reference: {booking_ref}",
            f"Complete container list for booking {booking_ref}; {len(instance.load_list)} units.",
            "Declarations refer only to the listed yard container IDs."]
    for cid in instance.load_list:
        box = boxes[cid]
        row = {name: box.get(name) for name in fields if name in box}
        row.update(booked_vessel=vessel, booking_status="confirmed",
                   dg="yes" if box["dg"] else "none", oog="" if box["oog"] else "none",
                   special_stowage="; ".join(box["special_stowage"]))
        if box["dg"]:
            entries = [("dg.", box["dg"])] + [(f"dg.additional.{i}.", dg) for i, dg in enumerate(box["dg"]["additional"])]
            for prefix, dg in entries:
                row.update({prefix + name: dg[name] for name in dg_fields})
        writer.writerow(row)
        # Plain freight labels are independently readable by paper.baseline_paper;
        # the full DG/OOG declarations remain printed for Spark and questions.
        line = (f"{cid}; ISO {box['iso_type']}; POL {box['pol']}; POD {box['pod']}; "
                f"VGM {box['gross_mass_kg']} kg; TARE {box['tare_kg']} kg; "
                f"MAX GROSS {box['max_gross_mass_kg']} kg; EMPTY {'YES' if box['empty'] else 'NO'}; "
                f"REEFER {'YES' if box['reefer'] else 'NO'}; Vessel {vessel}; Status CONFIRMED; "
                + ("DG NO" if not box["dg"] else "DG declaration " + json.dumps(box["dg"], sort_keys=True))
                + ("; IN GAUGE" if not box["oog"] else "; OOG declaration " + json.dumps(box["oog"], sort_keys=True)))
        if box["reefer_setpoint_c"] is not None:
            line += f"; SETPOINT {box['reefer_setpoint_c']} C"
        if box["special_stowage"]:
            line += "; Booking stowage request: " + ", ".join(box["special_stowage"])
        note.append(line)
        if box["dg"]:
            # paper.grounded authenticates freight labels, not JSON key names.
            # Separate entries let Spark quote one UN entry's own flags.
            note.extend(dg_lines(cid, box["dg"]))

    if booking_ref is None:
        # The single planner sheet remains available for download or the
        # explicit no-model path. Never include it in a per-booking Spark call.
        return [("load-list.csv", "unspecified", "text/csv", stream.getvalue())]

    selected = [boxes[cid] for cid in instance.load_list]
    si = [banner, "SHIPPING INSTRUCTIONS", f"Booking reference: {booking_ref}",
          "Operating-reefer set points below are synthetic booking declarations in degrees Celsius."]
    vgm = [banner, "VERIFIED GROSS MASS DECLARATION — gross mass includes tare",
           f"Booking reference: {booking_ref}"]

    def rating(box):
        value = box["max_gross_mass_kg"]
        return f"MAX GROSS {value} kg" if value is not None else "MAX GROSS NOT DECLARED"

    def setpoint(box):
        value = box["reefer_setpoint_c"]
        if box["reefer"] and value is None:
            raise CaseError("An operating reefer has no synthetic set point in the base instance; generate a new call before making papers.")
        return f"SETPOINT {value} C" if value is not None else "SETPOINT NONE (non-operating)"

    for box in selected:
        commodity = "EMPTY CONTAINER" if box["empty"] else "SYNTHETIC CARGO (commodity not modelled)"
        si.append(f"{box['id']}; ISO {box['iso_type']}; POL {box['pol']}; "
                  f"EMPTY {'YES' if box['empty'] else 'NO'}; REEFER {'YES' if box['reefer'] else 'NO'}; "
                  f"COMMODITY {commodity}; TARE {box['tare_kg']} kg; {rating(box)}; {setpoint(box)}")
        vgm.append(f"{box['id']}; VGM {box['gross_mass_kg']} kg; "
                   f"TARE {box['tare_kg']} kg; {rating(box)}")
    gate = [banner, "EQUIPMENT INTERCHANGE RECEIPTS / GATE SLIPS", f"Booking reference: {booking_ref}",
            "Seal references below are invented synthetic document metadata; seals are not modelled in the base instance."]
    for box in selected:
        seal = "SYN-" + hashlib.sha256(f"{instance.id}:{box['id']}:seal".encode()).hexdigest()[:12]
        gate.append(f"{box['id']}; Seal {seal}; ISO {box['iso_type']}")

    dg_boxes = [box for box in selected if box["dg"] is not None]
    dg_doc = [banner, "MULTIMODAL DANGEROUS GOODS DECLARATION", f"Booking reference: {booking_ref}",
              f"COMPLETE DANGEROUS GOODS LIST FOR BOOKING {booking_ref}.",
              "Only the containers listed below carry dangerous goods. All other booked containers are non-DG."]
    if not dg_boxes:
        dg_doc.append("No DG containers on this booking. The complete list is empty.")
    for box in dg_boxes:
        dg_doc.extend(dg_lines(box["id"], box["dg"]))
        dg_doc.append(f"{box['id']}; Full synthetic shipment declaration: " + json.dumps(box["dg"], sort_keys=True))

    # authority.prepare treats every printed ID in these three complete lists
    # as positive membership. Never print the full roster or negative rows here.
    reefers = [box for box in selected if box["reefer"]]
    reefer = [banner, "OPERATING REEFER LIST", f"Booking reference: {booking_ref}",
              f"COMPLETE OPERATING REEFER LIST FOR BOOKING {booking_ref}.",
              "Only listed containers require power; all others are non-operating."
              if reefers else "No operating reefers on this booking. The complete list is empty."]
    for box in reefers:
        reefer.append(f"{box['id']}; REEFER YES; {setpoint(box)}")
    oog_boxes = [box for box in selected if box["oog"] is not None]
    oog_doc = [banner, "OUT OF GAUGE DECLARATION", f"Booking reference: {booking_ref}",
               f"COMPLETE OOG LIST FOR BOOKING {booking_ref}.",
               "Only listed containers are OOG; all other booked containers are in gauge."]
    if not oog_boxes:
        oog_doc.append("No OOG containers on this booking. The complete list is empty.")
    for box in oog_boxes:
        oog_doc.append(f"{box['id']}; OOG overhang dimensions in metres: " + json.dumps(box["oog"], sort_keys=True))
    # Frozen intake has no grounded structured-OOG proposal. Keep all actual
    # dimensions printed; its missing_oog_declaration question must remain.

    return [
        ("booking-note.txt", "booking", "text/plain", "\n".join(note) + "\n"),
        ("shipping-instructions.txt", "shipping_instructions", "text/plain", "\n".join(si) + "\n"),
        ("vgm.txt", "vgm", "text/plain", "\n".join(vgm) + "\n"),
        ("dg-declaration.txt", "dg_declaration", "text/plain", "\n".join(dg_doc) + "\n"),
        ("gate-slip.txt", "gate_slip", "text/plain", "\n".join(gate) + "\n"),
        ("reefer-list.txt", "reefer_list", "text/plain", "\n".join(reefer) + "\n"),
        ("oog-list.txt", "oog_list", "text/plain", "\n".join(oog_doc) + "\n"),
    ]


def _write_papers(instance, call_dir):
    """One planner sheet, followed by contiguous seven-role booking groups.

    Flat document items preserve the Extension A shape. Hosts group non-null
    booking_ref values, omit the global sheet from Spark input, and scope the
    intake base load_list to that booking's confirmation roster.
    """
    papers = []

    def write(contents, prefix, booking_ref):
        for name, role, media, content in contents:
            name = prefix + name
            path = "papers/generated/" + name
            _atomic_bytes(Path(call_dir) / path, content.encode("utf-8"))
            papers.append(dict(id="generated-" + (prefix or "global-") + role, name=name,
                               path=path, role=role, booking_ref=booking_ref,
                               complete_booking_list=role in {"dg_declaration", "reefer_list", "oog_list"},
                               synthetic=True, media_type=media, label="Synthetic document"))

    write(_paper_contents(instance, None), "", None)
    reference = "SYN-" + hashlib.sha256(instance.id.encode()).hexdigest()[:16]
    for offset in range(0, len(instance.load_list), BOOKING_MAX_BOXES):
        number = offset // BOOKING_MAX_BOXES + 1
        booking_ref = f"{reference}-B{number}"
        scoped = instance.model_copy(update={"load_list": instance.load_list[offset:offset + BOOKING_MAX_BOXES]})
        write(_paper_contents(scoped, booking_ref), f"B{number}-", booking_ref)
    _atomic_bytes(Path(call_dir) / "papers/generated/manifest.json",
                  (json.dumps(papers, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())
    return papers


def generate_case(request: dict, call_dir: Path) -> dict:
    started = time.perf_counter()
    normalized = _request(request)
    seed_source = "random" if normalized["seed"] is None else "planner"
    seed = new_seed() if normalized["seed"] is None else normalized["seed"]
    normalized["seed"] = seed
    call_dir = Path(call_dir)
    if (call_dir / "case.json").exists() or (call_dir / "base-instance.json").exists():
        raise CaseError("This call already has a generated instance; create a new call.")
    CACHE.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="produce-", dir=CACHE) as private:
            parent = _produce(normalized, seed, Path(private))
            instance = _select(parent, normalized)
        from stowage.io import save_instance, load_instance
        from experiments.mvp.pipeline.contract.schema import sha256_json
        from experiments.mvp.pipeline.runner.envelope import check

        # stowage.io.save_instance is deterministic but not atomic by itself.
        call_dir.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=".base-instance-", suffix=".json", dir=call_dir)
        os.close(handle)
        try:
            save_instance(instance, temporary)
            if load_instance(temporary) != instance:
                raise CaseError("The generated instance did not survive its schema round trip.")
            canonical_hash, file_hash = sha256_json(instance.model_dump(mode="json")), _sha(temporary)
            # Integrity hashes bind this artifact. Freshness is the seed
            # inventory check above, not a comparison of renamed map hashes.
            os.replace(temporary, call_dir / "base-instance.json")
        finally:
            Path(temporary).unlink(missing_ok=True)
        envelope = check(instance)
        selected = {box.id: box for box in instance.containers if box.id in set(instance.load_list)}
        papers = _write_papers(instance, call_dir)
        case = dict(schema="stowlm.functional-case/1", request=normalized, seed=seed, seed_source=seed_source,
                    seed_status=seed_status(seed), instance_sha256=canonical_hash, file_sha256=file_hash,
                    instance_id=instance.id, ship=normalized["ship"], size_class=instance.size_class,
                    boxes=len(instance.load_list), teu=sum(box.length_ft/20 for box in selected.values()),
                    fill=envelope["numbers"]["physical_cell_fill"],
                    ports=[call.locode for call in instance.rotation.calls],
                    generator=dict(producer="experiments/mvp/corpus_tools/build.py", producer_sha256=_sha(PRODUCER),
                                   selection="maps/generate.py fill/195 trim", module_sha256=_sha(__file__)),
                    envelope=envelope, headline_envelope=_headline_envelope(envelope),
                    wall_s=time.perf_counter()-started,
                    created_utc=datetime.now(timezone.utc).isoformat(),
                    label="Synthetic ship and cargo, simulated port", papers=papers)
        _atomic_bytes(call_dir / "case.json", (json.dumps(case, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())
        return case
    except CaseError:
        raise
    except Exception as error:
        raise CaseError("Synthetic case generation failed; check the worker log or choose different parameters.") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="Ship/call request as JSON")
    parser.add_argument("--out", required=True, type=Path, help="Call directory")
    args = parser.parse_args()
    try:
        request = json.loads(args.request)
        case = generate_case(request, args.out)
    except (CaseError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(case, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

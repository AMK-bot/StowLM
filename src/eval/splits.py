"""Frozen, pre-solve partitions of synthetic instance specifications (plan 5.3).

A split separates examples used for fitting, tuning and final measurement.
Holding out a family measures transfer to conditions absent during fitting;
ordinary test seeds measure performance within familiar conditions. See
experiments/splits/v2/README.md and the split-before-preprocessing principle in
https://scikit-learn.org/stable/common_pitfalls.html#data-leakage .

Only generation inputs determine membership. Embedded vessel physics, generator
implementation versions, filenames and solver outcomes never enter the key.
The feeder_1700 medium decision region is a held-out *combination*, so training
still sees each feeder layout at other scales. It is not unseen-vessel evidence.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Literal

from stowage.conventions import canonical_vessel_id

Split = Literal["train", "val", "test"]
SPLITS_VERSION = "v2"
SPLIT_SEED = 20261027
SPLIT_POLICY = "stowlm-splits-v2:20261027"
DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / "experiments/splits/v2/manifest.json"


@dataclass(frozen=True)
class InstanceSpec:
    """Generation inputs; slice is a preset, never a versioned vessel identifier.

    The current generators bind slice to rung. Inactive controls must be None:
    random yards use blocking_ratio, simulated yards use noise. V2 includes raw
    requested quantity (None means automatic demand) and equipment/DG controls;
    realised accepted counts are outcomes, not generation inputs. Equipment tags
    must agree with those controls. Tiny random yards retain their loaded/empty
    mix even when their historical tiny_dg_enabled compatibility flag is false.
    The original nine positional fields remain unchanged. Explicit v1 identity
    is available only to reproduce the archived, incomplete specification schema.
    V3 is the random-yard DG-stress identity: it adds an explicit pair cohort
    and a dry20 stress equipment label without changing canonical v1/v2 keys.
    """
    generator: str
    rung: str
    vessel: str
    slice: str
    port: str
    noise: float | None
    blocking_ratio: float | None
    seed: int
    cargo_profile: str = "feeder_export"
    identity_version: str = "v2"
    n_boxes: int | None = None
    dense_20ft: bool = False
    tiny_dg_enabled: bool = True
    equipment_profile: str | None = None
    without_dg: bool | None = None
    dg_stress_pair: str | None = None

    def __post_init__(self):
        if self.generator not in ("random_yard", "simulated"):
            raise ValueError("unsupported generator")
        if self.rung not in ("tiny", "small", "medium", "full") or self.slice != self.rung:
            raise ValueError("current generators require a matching rung/slice preset")
        if canonical_vessel_id(self.vessel) not in ("feeder_500", "feeder_800", "feeder_1700"):
            raise ValueError("unsupported vessel")
        if self.port not in ("laem_chabang_like", "singapore_like", "small_feeder", "us_west_coast"):
            raise ValueError("unsupported port")
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        if self.identity_version not in ("v1", "v2", "v3"):
            raise ValueError("unsupported identity version")
        if self.dg_stress_pair not in (None, "flammable_water_reactive", "water_reactive_acid",
                                       "flammable_solid_flammable_liquid"):
            raise ValueError("unsupported DG-stress pair cohort")
        if self.dg_stress_pair is not None and self.identity_version != "v3":
            raise ValueError("DG-stress pair selection requires v3 identity")
        if self.identity_version == "v3" and (
                self.cargo_profile != "dg_stress" or self.generator != "random_yard"
                or self.dense_20ft or not self.tiny_dg_enabled):
            raise ValueError("v3 identity requires random-yard dry20 DG-stress conditioning")
        if self.cargo_profile not in ("feeder_export", "no_dg", "empty_dry20", "dense_dry20_dg", "dg_stress"):
            raise ValueError("unsupported cargo profile")
        if self.cargo_profile == "dense_dry20_dg" and (self.generator != "random_yard" or not self.dense_20ft):
            raise ValueError("dense_dry20_dg requires random-yard dense conditioning")
        if self.generator == "random_yard" and self.cargo_profile in ("no_dg", "empty_dry20"):
            raise ValueError("random_yard does not support this cargo conditioning")
        if self.n_boxes is not None and (type(self.n_boxes) is not int or self.n_boxes < 1):
            raise ValueError("n_boxes must be a positive integer or None")
        if type(self.dense_20ft) is not bool or type(self.tiny_dg_enabled) is not bool:
            raise ValueError("equipment controls must be boolean")
        if self.without_dg is None:
            object.__setattr__(self, "without_dg", self.cargo_profile == "no_dg")
        if type(self.without_dg) is not bool:
            raise ValueError("without_dg must be boolean")
        if self.cargo_profile != "empty_dry20" and self.without_dg != (self.cargo_profile == "no_dg"):
            raise ValueError("cargo profile conflicts with without_dg")
        empty = self.generator == "simulated" and self.rung == "tiny" and not self.tiny_dg_enabled
        if (self.cargo_profile == "empty_dry20") != empty:
            raise ValueError("empty_dry20 requires tiny equipment conditioning")
        equipment = ("dry20_dg_stress" if self.identity_version == "v3"
                     else "empty_dry20" if empty else "dry20_with_dg" if self.rung == "tiny"
                     else "dense_20ft" if self.dense_20ft else "full_mix")
        if self.equipment_profile is None:
            object.__setattr__(self, "equipment_profile", equipment)
        elif self.equipment_profile != equipment:
            raise ValueError("equipment profile conflicts with generation controls")
        if self.identity_version == "v1" and (self.n_boxes is not None or self.dense_20ft
                or not self.tiny_dg_enabled or self.cargo_profile == "empty_dry20"):
            raise ValueError("v1 identity cannot represent explicit demand/equipment controls")
        active = self.blocking_ratio if self.generator == "random_yard" else self.noise
        inactive = self.noise if self.generator == "random_yard" else self.blocking_ratio
        if inactive is not None or isinstance(active, bool) or not isinstance(active, (int, float)):
            raise ValueError("provide only the generator's active noise/blocking control")
        if not math.isfinite(active) or not 0 <= active <= 1:
            raise ValueError("noise/blocking control must be finite and in [0, 1]")
        name = "blocking_ratio" if self.generator == "random_yard" else "noise"
        object.__setattr__(self, name, 0.0 if active == 0 else float(active))


def _spec(spec: InstanceSpec | Mapping) -> InstanceSpec:
    if isinstance(spec, InstanceSpec):
        return spec
    # A historical canonical mapping has only v1's nine keys. New canonical
    # mappings always carry their explicit identity version; direct positional
    # InstanceSpec construction remains compatible and selects the v2 defaults.
    controls = {"identity_version", "n_boxes", "dense_20ft", "tiny_dg_enabled", "equipment_profile", "without_dg", "dg_stress_pair"}
    values = dict(spec)
    if not controls.intersection(values):
        values["identity_version"] = "v1"
    return InstanceSpec(**values)


def canonical_spec(spec: InstanceSpec | Mapping) -> dict:
    """Stable JSON fields, normalising numeric representations before hashing."""
    s = _spec(spec)
    values = asdict(s)
    if s.identity_version == "v1":
        return {name: values[name] for name in ("generator", "rung", "vessel", "slice", "port",
            "noise", "blocking_ratio", "seed", "cargo_profile")}
    if s.identity_version == "v2":
        # Preserve every previously frozen key and digest; only v3 adds cohorts.
        values.pop("dg_stress_pair")
    return values


def _membership_spec(canonical: Mapping) -> dict:
    """Normalize only lookup keys; frozen identity and assignment hashes stay raw."""
    values = dict(canonical)
    values["vessel"] = canonical_vessel_id(values["vessel"])
    return values


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def spec_key(spec: InstanceSpec | Mapping) -> str:
    """Content identifier of the specification, excluding component versions."""
    return hashlib.sha256(_json(canonical_spec(spec))).hexdigest()


def draw_group(spec: InstanceSpec | Mapping) -> dict:
    """Conservatively keep all uses of one root seed in one partition.

    Random-yard cargo historically ignores port/blocking controls; the campaign
    random-yard adapter also shares the simulated producer's route/cargo draws.
    Group across generators and all settings rather than claiming that changed
    controls make legacy draws independent. Frozen v2 assigns unique root seeds
    to its specs and disjoint seed namespaces to held-out families. A manifest
    must additionally reject a held-out override crossing a group's label.
    """
    return {"seed": _spec(spec).seed}


def draw_group_key(spec: InstanceSpec | Mapping) -> str:
    return hashlib.sha256(_json(draw_group(spec))).hexdigest()


def test_family(spec: InstanceSpec | Mapping) -> str:
    """Keep DG-stress separate; otherwise report overlapping held-out axes."""
    s = _spec(spec)
    # D70 is a separate stress test population, never an extra main-set panel.
    if s.cargo_profile == "dg_stress":
        return "dg_stress"
    labels = []
    if s.vessel == "feeder_1700" and s.slice == "medium":
        labels.append("heldout_slice")
    if s.port == "us_west_coast":
        labels.append("heldout_port")
    if s.noise == 0.8:
        labels.append("heldout_noise")
    return "+".join(labels) or "in_distribution"


def split_of(spec: InstanceSpec | Mapping) -> Split:
    """Assign before solving: held-out axes, then seeded 90/5/5 group hashing."""
    s = _spec(spec)
    if test_family(s) != "in_distribution":
        return "test"
    policy = "stowlm-splits-v1:20261027" if s.identity_version == "v1" else SPLIT_POLICY
    identity = canonical_spec(s) if s.identity_version == "v1" else draw_group(s)
    digest = hashlib.sha256(policy.encode() + b":" + _json(identity)).digest()
    bucket = int.from_bytes(digest, "big") % 10000
    return "train" if bucket < 9000 else "val" if bucket < 9500 else "test"


def spec_from_instance(instance) -> InstanceSpec:
    """Adapt 3.5/4.3 family tags without looking at regenerated Vessel contents.

    Their 'generator' tag currently stores an implementation version. The producer
    name therefore comes from provenance.created_by, never that version string.
    Missing identity and inconsistent seed/rung tags fail closed. An epoch may
    retain an archived requested ship label in split_vessel while vessel stores
    its canonical D33 label; these must resolve to the same ship.
    """
    family = instance.family
    names = {"generate.random_yard": "random_yard", "generate.instances": "simulated"}
    try:
        generator = names[instance.provenance.created_by]
        seed = int(family["seed"])
        if seed != instance.seed or family["size"] != instance.size_class:
            raise ValueError("instance seed/rung disagrees with family tags")
        def boolean(name, default):
            value = family.get(name)
            if value is None:
                return default
            if value not in ("true", "false"):
                raise ValueError(f"invalid boolean generation tag: {name}")
            return value == "true"

        requested = family.get("requested_n_boxes", family.get("n_boxes", "auto"))
        quantity = None if requested == "auto" else int(requested)
        profile = family.get("cargo_profile", "feeder_export")
        equipment = family.get("equipment_profile")
        identity = family.get("split_identity_version", family.get("identity_version", "v2"))
        if ("split_identity_version" in family and "identity_version" in family
                and family["split_identity_version"] != family["identity_version"]):
            raise ValueError("conflicting generation identity versions")
        pair = family.get("dg_stress_pair")
        if identity == "v3" and "dg_stress_pair" not in family:
            raise ValueError("v3 instance lacks explicit DG-stress cohort tag")
        if pair == "mixed":
            pair = None
        vessel = family.get("split_vessel", family["vessel"])
        if canonical_vessel_id(vessel) != canonical_vessel_id(family["vessel"]):
            raise ValueError("archived split vessel disagrees with canonical family vessel")
        return InstanceSpec(generator=generator, rung=family["size"], vessel=vessel,
                            slice=family.get("slice", family["size"]), port=family["port"],
                            noise=float(family["noise"]) if generator == "simulated" else None,
                            blocking_ratio=float(family["blocking_ratio"]) if generator == "random_yard" else None,
                            seed=seed, cargo_profile=profile, n_boxes=quantity,
                            identity_version=identity, dg_stress_pair=pair,
                            dense_20ft=boolean("dense_20ft", equipment == "dense_20ft"),
                            tiny_dg_enabled=boolean("tiny_dg_enabled", profile != "empty_dry20"),
                            without_dg=boolean("without_dg", profile == "no_dg"),
                            equipment_profile=equipment)
    except (KeyError, TypeError) as exc:
        raise ValueError("instance lacks recognised generation identity tags") from exc


def entries_sha256(entries: list[dict]) -> str:
    """Checksum the frozen ordered spec list, including split and test labels."""
    return hashlib.sha256(_json(entries)).hexdigest()


def summarize_entries(entries: list[dict]) -> dict:
    """Counts by rung, split and evaluation family (train/val use 'in_distribution')."""
    counts = {}
    for entry in entries:
        rung = entry["spec"]["rung"]
        family = entry["test_family"] or "in_distribution"
        cell = counts.setdefault(rung, {}).setdefault(entry["split"], {})
        cell[family] = cell.get(family, 0) + 1
    return counts


def validate_manifest(manifest: dict) -> None:
    """Validate content and metadata against an independently pinned release."""
    from eval.splits_inventory import validate_manifest as validate_release

    validate_release(manifest)


def load_manifest(path: str | Path | None = None) -> dict:
    """Read pinned v2 by default, or select a release name or manifest path."""
    from eval.splits_inventory import load_manifest as load_release

    return load_release(path)


def load_inventory(path: str | Path | None = None):
    """Load once, then enforce exact membership before opening campaign shards."""
    from eval.splits_inventory import load_inventory as load_release_inventory

    return load_release_inventory(path)


def require_membership(spec, *, inventory=None, split: Split | None = None) -> dict:
    """Return the frozen entry using vessel aliases only for membership lookup."""
    from eval.splits_inventory import require_membership as require_release_member

    return require_release_member(spec, inventory=inventory, split=split)


def membership_label(spec, *, inventory=None) -> Split:
    """Look up a release label; policy assignment alone never proves membership."""
    return require_membership(spec, inventory=inventory)["split"]


def summarize_families(entries: list[dict]) -> dict:
    """Joint generator/vessel/slice/port/control counts, with seeds aggregated."""
    counts = {}
    for entry in entries:
        family = {k: v for k, v in entry["spec"].items() if k not in ("rung", "seed")}
        key = _json(family).decode("utf-8")
        cell = counts.setdefault(entry["spec"]["rung"], {}).setdefault(entry["split"], {})
        cell[key] = cell.get(key, 0) + 1
    return counts

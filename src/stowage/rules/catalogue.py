"""Typed, traceable rule metadata; this catalogue does not execute constraints.

SI units are used for physical quantities. ``1`` denotes dimensionless values;
Profile-field references use frozen Vessel, PortProfile or Instance fields.
Collection paths project over their elements. ``instance.rule_overrides`` uses
the enclosing rule id and parameter name as its two dictionary keys.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import math
from pathlib import Path
import re
from stowage.toml_cache import load_bytes as load_toml_bytes
from threading import RLock
from types import MappingProxyType
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

Source = Literal["IMDG", "IS Code", "IACS", "SOLAS", "expert", "port", "user specification (D62)"]
Scope = Literal["global", "vessel", "port"]
Hardness = Literal["hard", "soft", "tolerated_if_inherited"]
EnforcementTarget = Literal["checker", "feasibility_engine", "linear_model", "solver"]
Scalar = StrictBool | StrictInt | StrictFloat | StrictStr


class CatalogueModel(BaseModel):
    """Reject misspellings instead of silently losing catalogue metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Provenance(CatalogueModel):
    document: str | None = Field(default=None, min_length=1)
    clause: str | None = Field(default=None, min_length=1)
    page: str | int | None = None
    url: str | None = Field(default=None, min_length=1)
    assumption: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def traceable(self) -> Self:
        if self.assumption is None and not (self.document and (self.clause or self.page is not None)):
            raise ValueError("provenance requires document and clause/page, or an explicit assumption")
        return self


class Parameter(CatalogueModel):
    value: Scalar | list[Scalar] | None = None
    unit: str = Field(min_length=1)
    provenance: Provenance
    profile_field: str | None = Field(default=None, min_length=1)
    shared_parameter: str | None = Field(default=None, min_length=1)
    superseded_by: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def typed_value_or_profile(self) -> Self:
        if self.shared_parameter is not None:
            if self.profile_field is not None:
                raise ValueError("shared defaults cannot also name a profile field")
        elif (self.value is None) == (self.profile_field is None):
            raise ValueError("parameter requires exactly one of value or profile_field")
        if isinstance(self.value, list):
            kinds = {type(item) for item in self.value}
            if not (len(kinds) == 1 or kinds <= {int, float}):
                raise ValueError("parameter value lists must have one type (numeric int/float may mix)")
        if self.profile_field is not None and not re.fullmatch(r"(?:vessel|port|instance)\.[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", self.profile_field):
            raise ValueError("profile_field must name a vessel.*, port.* or instance.* field")
        return self


class WeightClasses(CatalogueModel):
    """D25 class boundaries; D61 kg ordering remains staged until consumer migration."""

    status: Literal["provisional"]
    open_question: list[Literal[2, 4, 11]]
    parameters: dict[str, Parameter]

    @model_validator(mode="after")
    def valid_boundaries(self) -> Self:
        if set(self.parameters) != {"labels", "medium_min_kg", "heavy_min_kg"}:
            raise ValueError("weight classes require labels, medium_min_kg and heavy_min_kg")
        if self.parameters["labels"].value != ["L", "M", "H"] or self.parameters["labels"].unit != "1":
            raise ValueError("weight class order must be L, M, H")
        bounds = [self.parameters[name] for name in ("medium_min_kg", "heavy_min_kg")]
        if any(p.unit != "kg" or isinstance(p.value, bool)
               or not isinstance(p.value, (int, float)) for p in bounds):
            raise ValueError("weight boundaries must be numeric kg values")
        if not 0 < bounds[0].value < bounds[1].value:
            raise ValueError("weight boundaries must be positive and strictly increasing")
        if any(p.shared_parameter or p.profile_field for p in self.parameters.values()):
            raise ValueError("shared class parameters must define their values directly")
        return self

    def classify(self, mass_kg: float) -> str:
        """Classify actual VGM, never a cargo's untrusted cached weight_class label."""
        if isinstance(mass_kg, bool) or not isinstance(mass_kg, (int, float)) or not math.isfinite(mass_kg) or mass_kg < 0:
            raise ValueError("mass must be finite, nonnegative kg")
        labels = self.parameters["labels"].value
        if mass_kg < self.parameters["medium_min_kg"].value:
            return labels[0]
        if mass_kg < self.parameters["heavy_min_kg"].value:
            return labels[1]
        return labels[2]


class Penalty(CatalogueModel):
    """D11 permits preferences only after feasible intake and moves are tied."""

    kind: Literal["tie_break"]
    priority: Literal[4]
    metric: Literal["heavy_on_deck_count"] | None = None
    description: str = Field(min_length=1)


class PendingRuleContract(CatalogueModel):
    """Reviewed next-layer contract; inactive until its consumers migrate together."""

    hardness: Hardness
    enforced_by: list[EnforcementTarget] = Field(min_length=1)
    penalty: Penalty | None = None
    activation: str = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    parameters: dict[str, Parameter] | None = None


class Rule(CatalogueModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")
    title: str = Field(min_length=1)
    description: str = Field(default="", description="Plain-language interpretation and caveats")
    source: Source
    scope: Scope
    hardness: Hardness
    status: Literal["verified", "provisional"]
    domain_rows: list[str] = Field(min_length=1)
    enforced_by: list[EnforcementTarget] = Field(min_length=1)
    parameters: dict[str, Parameter]
    references: list[str] = Field(min_length=1)
    expert_rules: list[str] = Field(default_factory=list)
    open_question: list[int] = Field(default_factory=list)
    penalty: Penalty | None = None
    pending_contract: PendingRuleContract | None = None

    @model_validator(mode="after")
    def rule_semantics(self) -> Self:
        if any(number < 1 or number > 14 for number in self.open_question):
            raise ValueError("open_question numbers must be in 1..14")
        if self.open_question and self.status != "provisional":
            raise ValueError("rules with open questions must remain provisional")
        if any(not text.strip() for text in (*self.domain_rows, *self.references)):
            raise ValueError("domain rows and references must not be blank")
        if len(set(self.enforced_by)) != len(self.enforced_by):
            raise ValueError("enforcement targets must be unique")
        if self.hardness == "soft":
            margin = self.source in {"IS Code", "IACS"} and "margin" in self.id.split(".")
            heavy_preference = (
                self.id == "expert.e6.heavy_under_deck" and self.source == "expert"
                and self.expert_rules == ["E6"] and self.penalty is not None
                and self.penalty.metric == "heavy_on_deck_count"
            )
            if self.penalty is None or not (margin or heavy_preference):
                raise ValueError("soft rules require a D11 margin or E6 heavy-on-deck tie-break penalty")
        elif self.penalty is not None:
            raise ValueError("hard or inherited-tolerated rules cannot carry a penalty")
        if self.hardness == "tolerated_if_inherited":
            if self.source != "expert" or not self.expert_rules or not set(self.expert_rules) <= {"E1", "E2", "E7"}:
                raise ValueError("Stage B inheritance applies only to expert stack-weight-order preferences, never safety limits")
        pending = self.pending_contract
        if pending is not None:
            if len(set(pending.enforced_by)) != len(pending.enforced_by):
                raise ValueError("pending enforcement targets must be unique")
            if pending.hardness == "soft":
                margin = self.source in {"IS Code", "IACS"} and "margin" in self.id.split(".")
                heavy_preference = (
                    self.id == "expert.e6.heavy_under_deck" and self.source == "expert"
                    and self.expert_rules == ["E6"] and pending.penalty is not None
                    and pending.penalty.metric == "heavy_on_deck_count"
                    and pending.enforced_by == ["solver"]
                )
                if pending.penalty is None or not (margin or heavy_preference):
                    raise ValueError("pending soft rules require a D11 margin or E6 heavy-on-deck tie-break penalty")
            elif pending.penalty is not None:
                raise ValueError("pending hard or inherited-tolerated rules cannot carry a penalty")
            if pending.hardness == "tolerated_if_inherited" and (
                self.id not in {"expert.e1.heavy_bottom", "expert.e2.weight_order"}
                or self.source != "expert" or not self.expert_rules
                or not set(self.expert_rules) <= {"E1", "E2", "E7"}
            ):
                raise ValueError("pending inheritance cannot waive safety limits")
        return self

    def is_hard(self, *, stage: Literal["A", "B", "C", "D"] = "A", inherited: bool = False) -> bool:
        """New placements always obey inherited-tolerated rules (D11)."""
        return self.hardness != "soft" and not (
            self.hardness == "tolerated_if_inherited" and stage == "B" and inherited
        )


class Activation(CatalogueModel):
    """The leader flips this only after all rule-freeze-v1 consumers are reviewed."""

    rule_freeze_v1: StrictBool = False


class Catalogue(CatalogueModel):
    schema_version: Literal[1]
    activation: Activation = Field(default_factory=Activation)
    weight_classes: WeightClasses
    rules: list[Rule] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def resolve_shared_defaults(cls, data):
        """Materialise references so existing parameter.value readers see defaults.

        TOML stores boundaries once. Revalidation always refreshes materialised
        values from that shared block, including after a boundary is edited.
        """
        if not isinstance(data, dict) or "weight_classes" not in data:
            return data
        data = deepcopy(data)
        shared = WeightClasses.model_validate(data["weight_classes"])
        for index, rule in enumerate(data.get("rules", [])):
            if isinstance(rule, Rule):
                rule = rule.model_dump()
                data["rules"][index] = rule
            groups = [rule.get("parameters", {})]
            pending = rule.get("pending_contract")
            if isinstance(pending, PendingRuleContract):
                pending = pending.model_dump()
                rule["pending_contract"] = pending
            if pending is not None and pending.get("parameters") is not None:
                groups.append(pending["parameters"])
            for parameters in groups:
                for key, parameter in parameters.items():
                    if isinstance(parameter, Parameter):
                        parameter = parameter.model_dump()
                        parameters[key] = parameter
                    name = parameter.get("shared_parameter")
                    if name is not None:
                        if name not in shared.parameters:
                            raise ValueError(f"unknown shared weight-class parameter: {name}")
                        target = shared.parameters[name]
                        if parameter.get("unit") != target.unit:
                            raise ValueError(f"shared parameter unit mismatch: {name}")
                        parameter["value"] = deepcopy(target.value)
        return data

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        ids = [rule.id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("catalogue rule ids must be unique")
        return self

    def get_rule(self, rule_id: str) -> Rule:
        """Return a rule by stable id, raising KeyError for an unknown id."""
        for rule in self.rules:
            if rule.id == rule_id:
                return rule
        raise KeyError(rule_id)

    def __getitem__(self, rule_id: str) -> Rule:
        return self.get_rule(rule_id)

    def __iter__(self) -> Iterator[Rule]:
        return iter(self.rules)

    def for_domain_row(self, row: str) -> tuple[Rule, ...]:
        return tuple(rule for rule in self.rules if row in rule.domain_rows)

    def resolve_parameter(self, rule_id: str, name: str, rule_overrides: dict | None = None):
        """Instance override first, else catalogue default; invalid overrides stay visible.

        Pass ``instance.rule_overrides``. Presence, rather than truthiness, wins:
        a zero override is valid data, and callers must reject invalid values
        instead of silently falling back to a default.
        """
        parameter = self.get_rule(rule_id).parameters[name]
        overrides = (rule_overrides or {}).get(rule_id, {})
        if name in overrides:
            value = overrides[name]
            return value.get("value") if isinstance(value, dict) else value
        return parameter.value


DEFAULT_CATALOGUE_PATH = Path(__file__).with_suffix(".toml")


@dataclass(frozen=True)
class CatalogueSnapshot:
    """Read-only shared metadata and lookup map derived from exactly ``content``.

    Consumers must not mutate nested model collections. ``load_catalogue`` gives
    callers an independent deep copy when they need editable data.
    """

    content: bytes
    content_sha256: str
    catalogue: Catalogue
    rules: Mapping[str, Rule]


_snapshot_lock = RLock()
_file_snapshots: OrderedDict[Path, tuple[tuple, CatalogueSnapshot]] = OrderedDict()
_content_snapshots: OrderedDict[str, CatalogueSnapshot] = OrderedDict()
_snapshot_override: ContextVar[tuple[Path, CatalogueSnapshot] | None] = ContextVar(
    "rule_catalogue_snapshot_override", default=None)


def _stat_key(path: Path) -> tuple:
    value = path.stat()
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _snapshot_from_content(content: bytes) -> CatalogueSnapshot:
    identity = "sha256:" + sha256(content).hexdigest()
    previous = _content_snapshots.get(identity)
    if previous is not None:
        _content_snapshots.move_to_end(identity)
        return previous
    catalogue = Catalogue.model_validate(load_toml_bytes(content))
    if catalogue.activation.rule_freeze_v1:
        effective = []
        for rule in catalogue:
            pending = rule.pending_contract
            if pending is None:
                effective.append(rule)
                continue
            values = rule.model_dump()
            for name in ("hardness", "enforced_by", "penalty"):
                value = getattr(pending, name)
                values[name] = value.model_dump() if isinstance(value, CatalogueModel) else value
            for name in ("title", "description", "parameters"):
                value = getattr(pending, name)
                if value is not None:
                    values[name] = value
            values["pending_contract"] = None
            effective.append(Rule.model_validate(values))
        catalogue = catalogue.model_copy(update={"rules": effective})
    snapshot = CatalogueSnapshot(content, identity, catalogue,
                                 MappingProxyType({rule.id: rule for rule in catalogue}))
    _content_snapshots[identity] = snapshot
    if len(_content_snapshots) > 64:
        _content_snapshots.popitem(last=False)
    return snapshot


def catalogue_snapshot(path: str | Path | None = None) -> CatalogueSnapshot:
    """Stat on each call; read/hash/parse only after file identity changes.

    Invalid or concurrently changing replacements fail closed, never returning
    the prior snapshot as if it described the changed file. Long operations
    should retain one snapshot while they execute and record its identity.
    """
    selected = Path(DEFAULT_CATALOGUE_PATH if path is None else path).absolute()
    override = _snapshot_override.get()
    if override is not None and selected == override[0]:
        return override[1]
    with _snapshot_lock:
        fingerprint = _stat_key(selected)
        previous = _file_snapshots.get(selected)
        if previous is not None and previous[0] == fingerprint:
            _file_snapshots.move_to_end(selected)
            return previous[1]
        for _ in range(3):
            content = selected.read_bytes()
            after = _stat_key(selected)
            if fingerprint == after:
                snapshot = _snapshot_from_content(content)
                _file_snapshots[selected] = (after, snapshot)
                _file_snapshots.move_to_end(selected)
                if len(_file_snapshots) > 32:
                    _file_snapshots.popitem(last=False)
                return snapshot
            fingerprint = after
        raise RuntimeError("rule catalogue changed repeatedly while reading its content snapshot")


def clear_catalogue_snapshot_cache() -> None:
    """Compatibility/testing reset; normal callers never need manual invalidation."""
    with _snapshot_lock:
        _file_snapshots.clear()
        _content_snapshots.clear()


def load_catalogue(path: str | Path | None = None) -> Catalogue:
    """Load fresh, caller-owned data from the current validated content snapshot."""
    return catalogue_snapshot(path).catalogue.model_copy(deep=True)


def rule_freeze_v1_enabled(*, catalogue: Catalogue | None = None) -> bool:
    """One activation switch for every D61/D62/D64 execution consumer; default off."""
    selected = catalogue if catalogue is not None else catalogue_snapshot().catalogue
    return selected.activation.rule_freeze_v1


@contextmanager
def rule_freeze_v1(*, enabled: bool = True) -> Iterator[CatalogueSnapshot]:
    """Test helper: override the complete effective snapshot, including its SHA.

    Nested contexts and exceptions restore the prior snapshot. No file is edited;
    provenance changes with the effective activation value, so different rule
    semantics cannot be recorded under an unchanged catalogue identity.
    """
    if not isinstance(enabled, bool):
        raise TypeError("rule_freeze_v1 enabled must be a boolean")
    original = catalogue_snapshot()
    source = original.content.decode("utf-8")
    declaration = f"rule_freeze_v1 = {'true' if enabled else 'false'}"
    source, replacements = re.subn(
        r"^(rule_freeze_v1[ \t]*=[ \t]*)(?:true|false)([ \t]*(?:#[^\r\n]*)?)$",
        lambda match: match[1] + ('true' if enabled else 'false') + match[2],
        source, flags=re.MULTILINE)
    if not replacements:
        source, tables = re.subn(r"^\[activation\][ \t]*$", "[activation]\n" + declaration,
                                source, count=1, flags=re.MULTILINE)
        if not tables:
            source += "\n[activation]\n" + declaration + "\n"
    with _snapshot_lock:
        snapshot = _snapshot_from_content(source.encode("utf-8"))
    token = _snapshot_override.set((Path(DEFAULT_CATALOGUE_PATH).absolute(), snapshot))
    try:
        yield snapshot
    finally:
        _snapshot_override.reset(token)


def get_rule(rule_id: str, catalogue: Catalogue | None = None) -> Rule:
    """Look up a stable id in the supplied catalogue, or the bundled v0 data."""
    return (catalogue if catalogue is not None else load_catalogue()).get_rule(rule_id)

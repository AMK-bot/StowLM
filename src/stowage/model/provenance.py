"""Provenance carried by every artifact (Instance, MovePlan, PlannerResult, CheckReport, eval rows).

Built by ``stowage.frozen.current_provenance()``; checked by ``stowage.frozen.require_clean``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from stowage.model.base import StowModel


class Provenance(StowModel):
    """Where an artifact came from and whether a stub touched it.

    Fields:
        versions: component → version string at creation time (``stowage.versions.component_versions()``):
            schema, move_engine, checker, feasibility, linear_model, ballast, action_space, rule_catalogue.
            GRPO (6.4) pins these; the eval harness reports them per row.
        stubs: frozen interface name → reason, for every stub override active when this artifact *or any input it
            was derived from* was made. Non-empty = tainted: never a gold plan, never a shipped/evaluated result.
        seed: RNG seed used to produce the artifact, if any (generators, sampling planners).
        created_by: producer id, e.g. ``"generate.random_yard"``, ``"solver.scalable"``, ``"stowlm@adapter-v3"``.
        created_at: ISO 8601 UTC timestamp (informational; excluded from determinism comparisons).
        git_commit: ``git rev-parse HEAD`` if available, plus ``"+dirty"`` when the tree is modified.
        parents: ids of input artifacts (instance id, plan id), for lineage. Producers add their direct inputs'
            ids explicitly (``merge(..., parent_ids=[instance.id])``) since Provenance has no id of its own.
        input_versions: component versions recorded by the inputs when they differ from ``versions`` (component →
            sorted distinct versions); non-empty = this artifact mixes component versions (eval flags it).
    """

    versions: dict[str, str] = Field(default_factory=dict)
    stubs: dict[str, str] = Field(default_factory=dict)
    seed: int | None = None
    created_by: str | None = None
    created_at: str | None = None
    git_commit: str | None = None
    parents: list[str] = Field(default_factory=list)
    input_versions: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("parents")
    @classmethod
    def canonical_parents(cls, value: list[str]) -> list[str]:
        return sorted(set(value))

    @field_validator("input_versions")
    @classmethod
    def canonical_versions(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        return {key: sorted(set(versions)) for key, versions in value.items()}

    @property
    def tainted(self) -> bool:
        return bool(self.stubs)

    def merge(self, *inputs: Provenance, parent_ids: list[str] | None = None) -> Provenance:
        """This provenance plus the stubs, parents and differing versions of ``inputs`` (taint is contagious;
        ``versions`` stay ours). ``parent_ids``: ids of the direct inputs, appended to ``parents``."""
        stubs = dict(self.stubs)
        parents = list(self.parents)
        mixed = {k: set(v) for k, v in self.input_versions.items()}
        for p in inputs:
            stubs.update(p.stubs)
            parents.extend(x for x in p.parents if x not in parents)
            for comp, ver in p.versions.items():
                if comp in self.versions and self.versions[comp] != ver:
                    mixed.setdefault(comp, set()).add(ver)
            for comp, vers in p.input_versions.items():
                mixed.setdefault(comp, set()).update(vers)
        parents.extend(x for x in (parent_ids or []) if x not in parents)
        return self.model_copy(
            update={"stubs": stubs, "parents": sorted(set(parents)), "input_versions": {k: sorted(v) for k, v in mixed.items()}}
        )

    def merge_nested(self, *inputs: object, parent_ids: list[str] | None = None) -> Provenance:
        """Merge all provenance in input models/containers, including nested evidence.

        Like ``merge``, preserve our producer and versions. Shared references and
        cycles are visited once. Consumers of mutable artifacts can call this at
        their output boundary to retain evidence added after model validation.
        """
        found: list[Provenance] = []
        visited: set[int] = set()

        def visit(value: object) -> None:
            if id(value) in visited:
                return
            visited.add(id(value))
            if isinstance(value, Provenance):
                found.append(value)
            elif isinstance(value, BaseModel):
                for name in type(value).model_fields:
                    visit(getattr(value, name))
            elif isinstance(value, dict):
                for item in value.values():
                    visit(item)
            elif isinstance(value, (list, tuple, set, frozenset)):
                for item in value:
                    visit(item)

        for value in inputs:
            visit(value)
        return self.merge(*found, parent_ids=parent_ids)

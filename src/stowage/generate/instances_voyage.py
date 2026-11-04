"""D50 producer declarations using explicit synthetic port-profile evidence.

D34's dock density is an existing synthetic scenario assumption, not a measured
LOCODE fact. A route label alone supplies no environment or density: unresolved
next calls fail closed instead of borrowing the current port's metadata.
"""
from __future__ import annotations

from hashlib import sha256
import json

from stowage.frozen import current_provenance
from stowage.model import Instance, PortProfile, Provenance, Rotation, VoyageConditionIdentity, VoyageSpec
from stowage.ports.profiles import list_ports, load_port

VOYAGE_DECLARATION_VERSION = "1.0.0"


def _digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")).hexdigest()


def _next_profile(rotation: Rotation, call_index: int, explicit: PortProfile | None) -> PortProfile:
    call = rotation.calls[call_index + 1]
    if explicit is not None:
        profile = PortProfile.model_validate(explicit.model_dump())
        if profile.locode != call.locode:
            raise ValueError("voyage next-call environment LOCODE disagrees with rotation")
        if call.archetype is not None and profile.archetype != call.archetype:
            raise ValueError("voyage next-call environment archetype disagrees with rotation")
        return profile
    if call.archetype is not None:
        profile = load_port(call.archetype)
        if profile.locode != call.locode:
            raise ValueError("voyage next-call environment archetype LOCODE disagrees with rotation")
        return profile
    profiles = [load_port(name) for name in list_ports()]
    matches = [profile for profile in profiles if profile.locode == call.locode]
    if len(matches) != 1:
        raise ValueError(f"voyage next-call environment unavailable for {call.locode} "
                         f"at call {call_index + 1}: expected one exact declared port profile, found {len(matches)}")
    return matches[0]


def build_voyage_spec(
    instance: Instance | None = None, *, instance_id: str | None = None,
    rotation: Rotation | None = None, port: PortProfile | None = None,
    call_index: int | None = None, provenance: Provenance | None = None,
    next_port: PortProfile | None = None,
) -> VoyageSpec:
    """Declare stable departure/immediate-arrival identities and default use.

    Supply an Instance or explicit identity/context before Instance validation.
    An explicit next_port is evidence supplied by a caller, never a generated
    fallback. The vessel owner supplies grouping/order; this helper creates no
    tank policy, consumption quantities, ballast operations or arrival liquids.
    """
    if instance is not None:
        if any(value is not None for value in (instance_id, rotation, port, call_index)):
            raise ValueError("voyage context must be an instance or explicit fields, not both")
        instance_id, rotation, port, call_index = instance.id, instance.rotation, instance.port, instance.call_index
    if not isinstance(instance_id, str) or not instance_id.strip():
        raise ValueError("voyage requires a nonblank instance id")
    if rotation is None or port is None or type(call_index) is not int:
        raise ValueError("voyage requires rotation, port and integer call_index")
    rotation = Rotation.model_validate(rotation.model_dump())
    port = PortProfile.model_validate(port.model_dump())
    if call_index < 0 or call_index + 1 >= len(rotation.calls):
        raise ValueError("voyage requires an immediate next call in rotation")
    if rotation.calls[call_index].locode != port.locode:
        raise ValueError("voyage current port LOCODE disagrees with rotation")
    following = _next_profile(rotation, call_index, next_port)
    base = current_provenance(created_by="generate.instances_voyage")
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if rule_freeze_v1_enabled():
        from stowage.generate.cargo import effective_cargo_generator_version
        from stowage.generate.random_yard import effective_random_yard_version
        from stowage.yard import effective_yard_simulator_version

        base.versions.update(cargo_generator=effective_cargo_generator_version(),
                             yard_simulator=effective_yard_simulator_version(),
                             random_yard=effective_random_yard_version())
    # Destination epochs must exist before merging, so historical producer
    # epochs remain disclosed in input_versions rather than being relabelled.
    base = base.merge_nested(instance, provenance,
        parent_ids=[instance_id, "docs/decisions.md:D34", "docs/decisions.md:D50"])
    base.versions["instances_voyage"] = VOYAGE_DECLARATION_VERSION

    def condition(profile: PortProfile, index: int, phase: str) -> VoyageConditionIdentity:
        if not profile.sources:
            raise ValueError(f"voyage {phase} environment requires explicit port-profile source evidence")
        environment = dict(locode=profile.locode, archetype=profile.archetype,
                           max_draught_m=profile.max_draught_m, sources=profile.sources)
        density = dict(environment=environment, water_density_kg_m3=profile.water_density_kg_m3)
        environment_id = f"synthetic-port:{profile.archetype}:{_digest(environment)}"
        density_id = f"dock-water:{profile.locode}:{_digest(density)}"
        identity = [instance_id, rotation.service, index, profile.locode, phase]
        evidence = base.merge(parent_ids=[*profile.sources, environment_id, density_id,
            f"rotation:{rotation.service}:call:{index}:{profile.locode}"])
        return VoyageConditionIdentity(
            id=f"voyage:{rotation.service}:call:{index}:{profile.locode}:{phase}:{_digest(identity)}",
            phase=phase, call_index=index, locode=profile.locode,
            environment_id=environment_id, density_id=density_id,
            water_density_kg_m3=float(profile.water_density_kg_m3), provenance=evidence)

    departure = condition(port, call_index, "departure")
    arrival = condition(following, call_index + 1, "arrival")
    # IS Code B3.4.1.2-.3, data/raw/is-code/msc267-85.pdf, PDF p41:
    # "with 10 % stores and fuel remaining". D50 applies the vessel owner's
    # default GROUP retention rule; leaving consumption=None invokes that rule.
    return VoyageSpec(departure=departure, arrival=arrival, consumption=None,
                      ballast_operations=[], provenance=base.merge_nested(departure, arrival))


def attach_default_voyage(instance: Instance) -> Instance:
    """Return a validated independent instance, preserving any explicit leg."""
    payload = instance.model_dump()
    if instance.voyage is None:
        payload["voyage"] = build_voyage_spec(instance).model_dump()
    return Instance.model_validate(payload)

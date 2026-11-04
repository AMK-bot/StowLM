"""D50 voyage declarations for synthetic random-yard instances.

Water density comes from a declared synthetic port profile (D34), never from
the spelling of an unmatched real port's LOCODE.
"""
from __future__ import annotations

from hashlib import sha256
import json

from stowage.frozen import current_provenance
from stowage.model import VoyageConditionIdentity, VoyageSpec
from stowage.model.port import PortCall, PortProfile, Rotation
from stowage.ports.profiles import list_ports, load_port


def _fingerprint(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()).hexdigest()


def _arrival_profile(call: PortCall) -> PortProfile:
    if call.archetype is not None:
        profile = load_port(call.archetype)
        if profile.locode != call.locode:
            raise ValueError("arrival archetype profile LOCODE must match the next call LOCODE")
        return profile
    matches = [profile for archetype in list_ports()
               if (profile := load_port(archetype)).locode == call.locode]
    if not matches:
        raise ValueError(f"no matching synthetic port profile for next call {call.locode}")
    if len(matches) != 1:
        raise ValueError(f"ambiguous synthetic port profile for next call {call.locode}; declare archetype")
    return matches[0]


def _condition(rotation: Rotation, call: PortCall, port: PortProfile, phase: str,
               *, seed: int) -> VoyageConditionIdentity:
    profile_hash = _fingerprint(port.model_dump(mode="json"))
    environment = {"locode": call.locode, "archetype": port.archetype,
                   "profile_sha256": profile_hash}
    density = {**environment, "water_density_kg_m3": port.water_density_kg_m3}
    provenance = current_provenance(seed=seed, created_by="generate.random_voyage", parents=[
        *port.sources,
        f"synthetic port profile {port.archetype} sha256:{profile_hash}",
        f"rotation {rotation.service} call {call.index} LOCODE {call.locode}",
        "D34: port labels and profile water densities describe synthetic scenarios, not measured real docks.",
    ])
    return VoyageConditionIdentity(
        id=f"{rotation.service}:call-{call.index}:{call.locode}:{phase}",
        phase=phase, call_index=call.index, locode=call.locode,
        environment_id=f"synthetic-port-environment:sha256:{_fingerprint(environment)}",
        density_id=f"synthetic-port-density:sha256:{_fingerprint(density)}",
        water_density_kg_m3=float(port.water_density_kg_m3), provenance=provenance,
    )


def build_voyage_spec(rotation: Rotation, call_index: int, departure_port: PortProfile,
                      *, seed: int) -> VoyageSpec:
    """Declare this departure and the immediate next arrival, deterministically.

    An unknown or ambiguous next port fails closed. Explicit archetypes must
    carry the same synthetic LOCODE as the call; no nearest-port substitute is
    inferred. The vessel policy and model derive actual arrival liquid masses
    from the eventual checked departure state, not the instance's onboard state.
    """
    if type(call_index) is not int or not 0 <= call_index < len(rotation.calls):
        raise ValueError("call_index must identify a current call in rotation")
    if call_index + 1 >= len(rotation.calls):
        raise ValueError("voyage declaration requires an immediate next call")
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    departure_call, arrival_call = rotation.calls[call_index:call_index + 2]
    if departure_call.locode != departure_port.locode:
        raise ValueError("departure profile LOCODE must match the current call LOCODE")
    if departure_call.archetype is not None and departure_call.archetype != departure_port.archetype:
        raise ValueError("departure profile archetype must match the current call archetype")
    arrival_port = _arrival_profile(arrival_call)
    departure = _condition(rotation, departure_call, departure_port, "departure", seed=seed)
    arrival = _condition(rotation, arrival_call, arrival_port, "arrival", seed=seed)
    provenance = current_provenance(seed=seed, created_by="generate.random_voyage", parents=[
        "D50: consumption=None selects the vessel policy's default 10% remaining per consumable group.",
        "D50: no voyage ballast operations are declared or permitted.",
        # APPROXIMATION: D50 evaluates endpoints; it does not search the actual
        # worst intermediate consumable/free-surface condition.
        "APPROXIMATION D50: departure/arrival endpoints only; actual worst intermediate condition is not searched.",
        # IS Code Part B 3.4.1.2-.3, data/raw/is-code/msc267-85.pdf, PDF p.41:
        # "with 10 % stores and fuel remaining". The model/vessel policy owns
        # the group-total interpretation and sequential draw-down, not this producer.
        "D50 default rule: data/raw/is-code/msc267-85.pdf Part B 3.4.1.2-.3, PDF p.41.",
    ]).merge_nested(departure, arrival)
    return VoyageSpec(departure=departure, arrival=arrival, consumption=None,
                      ballast_operations=[], provenance=provenance)

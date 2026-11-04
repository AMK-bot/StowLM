"""Activated C5 lift restrictions, shared by replay and prospective callers.

Contract Addendum 1 A3/A4 (D66) uses the primary DG class only. Subsidiary
risks and additional declarations do not trigger these port rules; class 7
has primary-class precedence in IMDG 2.0.3. Passing through a port aboard
without a lift is outside C5's refusal scope.
"""
from __future__ import annotations

from stowage.model import Instance, Move, Violation
from stowage.model.cargo import Container
from stowage.model.port import PortCall, PortProfile
from stowage.ports import resolve_port_call_profile
from stowage.rules.catalogue import rule_freeze_v1_enabled


def profile_resolution_violation(
    call: PortCall, error: ValueError | OSError, *, containers: list[str], move_index: int | None = None,
) -> Violation:
    """Preserve the affected later call and resolver reason in a hard diagnostic."""
    return Violation(
        rule_id="port.call.profile_resolution", severity="hard", call_index=call.index,
        move_index=move_index, containers=sorted(containers),
        message=(f"Cannot resolve port profile for call {call.index} ({call.locode}, "
                 f"archetype={call.archetype!r}): {type(error).__name__}: {error}"),
    )


def refused_primary_class(port: PortProfile, primary: str | None) -> bool:
    """Match configured refusal classes; caller applies the activation switch."""
    if primary is None:
        return False
    primary = primary.upper()
    identifiers = {primary, primary.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"), primary.partition(".")[0]}
    return any(
        ref.rule_id == "port.dg.refused_class"
        and identifiers.intersection(str(value).upper() for value in ref.params.get("classes", ()))
        for ref in port.port_rules
    )


def port_move_violation(
    instance: Instance, move: Move, *, move_index: int | None = None, box: Container | None = None,
) -> Violation | None:
    """Return a C5 rejection for this lift, or None; never mutate the inputs.

    Replay still validates source presence, productive lists and physical
    access. A direct load must come from ``truck`` with no existing location;
    a direct discharge to ``truck`` has no yard destination. APPROXIMATION:
    truck timing is not modelled; a direct-delivery truck is available when
    its lift comes. A productive load is also refused when its first future
    POD call resolves to a profile refusing that primary class (A3).
    This C5 helper has no crossing threshold; physical replay applies A8's
    independent effective yard limit and the unchanged ship-side quay H.
    """
    if not rule_freeze_v1_enabled():
        return None
    cid = getattr(move, "container_id", None)
    if box is None:
        box = next((box for box in instance.containers if box.id == cid), None)
    if box is None:
        return None  # Replay owns unknown-container diagnostics, including covers.
    primary = box.dg.imdg_class if box.dg is not None else None

    def reject(rule: str, message: str) -> Violation:
        refs = [getattr(move, endpoint, None) for endpoint in ("src", "dst")]
        return Violation(
            rule_id=rule, severity="hard", call_index=instance.call_index,
            move_index=move_index, containers=[cid],
            slots=[ref for ref in refs if isinstance(ref, str) and len(ref) == 7 and ref.isdigit()],
            yard_addresses=[ref for ref in refs if isinstance(ref, str) and "-" in ref],
            message=message,
        )

    if move.kind in ("load", "discharge", "restow_off", "restow_on", "restow_shift"):
        refused_port = instance.port if refused_primary_class(instance.port, primary) else None
        if refused_port is None and move.kind == "load":
            destination = next((call for call in instance.rotation.calls[instance.call_index + 1:]
                                if call.locode == box.pod), None)
            if destination is not None:
                try:
                    profile = resolve_port_call_profile(destination, current_port=instance.port)
                except (ValueError, OSError) as error:
                    return profile_resolution_violation(
                        destination, error, containers=[cid], move_index=move_index)
                if profile is not None and refused_primary_class(profile, primary):
                    refused_port = profile
        if refused_port is not None:
            return reject("port.dg.refused_class",
                          f"Primary DG class {primary} is refused for cargo handling at {refused_port.locode}.")

    direct = instance.port.requires_direct_delivery(primary)
    if box.direct_delivery != direct:
        return reject("port.dg.direct_delivery", "The direct-delivery marker must match the current port's primary-class policy.")
    if direct:
        if move.kind in ("relocate", "gate_in", "gate_out"):
            return reject("port.dg.direct_delivery", "A direct-delivery box cannot be placed in or retrieved from the yard.")
        if move.kind in ("restow_off", "restow_on"):
            # The model excludes direct-delivery cargo from YardState.quay_buffer,
            # as well as yard stacks; these moves necessarily use that buffer.
            return reject("port.dg.direct_delivery", "A direct-delivery box cannot use the temporary quay storage buffer.")
        if move.kind == "load" and move.src != "truck":
            return reject("port.dg.direct_delivery", "A direct-delivery export loads in one lift from a ship-side truck.")
        if move.kind == "discharge" and move.dst != "truck":
            return reject("port.dg.direct_delivery", "A direct-delivery import discharges in one lift to a ship-side truck.")
    elif getattr(move, "src", None) == "truck" or getattr(move, "dst", None) == "truck":
        return reject("port.dg.direct_delivery", "Truck endpoints are reserved for current-port direct delivery.")
    return None

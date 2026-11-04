"""Declared H1 shipment/placement policy and SW19 battery classification.

Primary source: IMO MSC.556(108), IMDG 42-24, 7.1.6 H1, 7.3.3.3,
7.1.5 SW19 and 3.3 special provisions 376/377:
https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.556(108).pdf

Generator contract (JSON dictionaries under the existing stowage override's
``containers[container_id]``; additional DG entries must use their own facts):

``h1`` contains ``provenance``, boolean ``ctu_weathertight`` and declared
``packing_condition`` (``dry`` or ``wet``). The declaration describes the CTU's
goods and packing/securing materials at consignment, not an inspection outcome
or a prediction of their condition on arrival. Existing ``ctu_closed`` supplies
the CTU's physical closure classification. No per-slot flags are supplied by
the shipper. The checker supplies ``covered_under_deck`` from hatch geometry.
ASSUMPTION: pending expert review, closed/weathertight CTUs with declared-dry
packing satisfy this conservative H1 placement policy on deck or in a covered
hold. Under-deck addresses alone do not establish covered-hold geometry.
Handling/voyage precautions are instructions from ``operational_precautions``;
their future execution is outside the loading-plan verdict.

``sw19`` contains ``provenance``, ``un_number``, ``battery_condition``
(``intact``, ``damaged_defective`` or ``cannot_be_diagnosed``),
``transport_purpose`` (``normal``, ``disposal`` or ``recycling``),
``special_provision_applicability`` mapping both ``376`` and ``377`` to booleans,
and boolean ``transport_in_accordance``. Applicability here identifies the
transport route actually used, not all candidate SPs printed in DGL column 6.
Damaged/defective and undiagnosable batteries use 376, including when destined
for recycling/disposal (377 expressly redirects them to 376). Intact batteries
may elect 377 for disposal/recycling; purpose alone does not elect that optional
route. Other batteries use neither.
For exceptional SP376/377 routes, ``transport_in_accordance`` is the shipper's
declared use of the prescribed packing/marking/documentation and any required
approval. It is not an inspection outcome or evidence of future execution.
The ordinary neither-SP route requires no such conclusion.
When either route applies, ``voyage`` additionally supplies boolean
``short_international_voyage`` and its own ``provenance``. This is the defined
IMDG voyage classification, not a locally chosen journey-length threshold.
When existing shipment ``special_provisions`` or ``short_international_voyage``
facts are also supplied, their known values must agree with the nested record.
An absent/None existing fact adds no requirement. Incomplete nested evidence
stays unknown before cross-record contradictions are considered; malformed
existing facts also stay unknown rather than being coerced to a clearance.

Every ``provenance`` is resolved by the caller-supplied authoritative scenario
registry through ``provenance_matches`` against the current binding context.
Nonempty strings alone do not authorize a record; absent context, unresolved
IDs and foreign shipment/entry/vessel/voyage/slot bindings remain unknown.
A caller must retain declarations for audit; these functions never fabricate
observed outcomes, manufacture protection flags or modify facts.
Unknown/malformed evidence returns None; an evidenced adverse result is False.
The checker owns legacy fallback and rule activation, outside this module.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from stowage.conventions import parse_slot
from stowage.rules.imdg.dgl import lookup_dgl
from stowage.rules.imdg.scenarios import provenance_matches


def _record(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _provenanced(record: Mapping[str, Any], context, registry) -> bool:
    return provenance_matches(record.get('provenance'), context, registry)


def _boolean(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _all_known(checks: list[bool | None]) -> bool | None:
    if False in checks:
        return False
    return None if None in checks else True


def assess_h1(facts: Mapping[str, Any], *, on_deck: bool | None,
              covered_under_deck: bool | None = None, slot: str | None = None,
              context=None, registry=None) -> bool | None:
    """Apply the labelled declared-facts policy, with actual covered-hold data.

    ``slot`` is retained for caller compatibility; authoritative scope comes
    from context.slot. It must agree when supplied. No outcome fields or
    shipment-selected placements participate in the verdict.
    """
    record = _record(facts.get('h1'))
    if not _provenanced(record, context, registry):
        return None
    if slot is not None and getattr(context, 'slot', None) != slot:
        return None
    if not isinstance(on_deck, bool) or parse_slot(context.slot).on_deck is not on_deck:
        return None
    packing = record.get('packing_condition')
    declared_dry = True if packing == 'dry' else False if packing == 'wet' else None
    # ASSUMPTION: conservative sufficient H1 policy pending expert review.
    # This is a declaration/placement restriction, never a voyage outcome.
    checks = [_boolean(facts.get('ctu_closed')), _boolean(record.get('ctu_weathertight')),
              declared_dry]
    checks.append(True if on_deck is True else _boolean(covered_under_deck)
                  if on_deck is False else None)
    return _all_known(checks)


def operational_precautions(facts: Mapping[str, Any]) -> tuple[str, ...]:
    """Return H1 crew/terminal instructions, independent of plan compliance.

    Presence of H1 produces instructions even when the declaration is unknown;
    receiving instructions is never evidence that operations occurred.
    """
    if 'h1' not in facts:
        return ()
    return (
        'Keep the CTU, goods, securing and packing materials as dry as reasonably practicable.',
        'Protect cargo during loading and discharge from rain and seawater; remove adhering water, snow and ice before packing.',
        'Maintain weathertight closures and manage water ingress and condensation during the voyage.',
    )


@dataclass(frozen=True)
class SW19Assessment:
    """SW19 conclusion and cargo-ship category; category is absent if uncleared.

    ``compliant`` is True for assessed transport, False for contradictory or
    adverse evidence, None for missing evidence. ``category`` is the sampled
    DGL base category or SW19's C when compliant is True. This does not clear
    any other IMDG provision.
    """

    compliant: bool | None
    category: str | None = None


def assess_sw19(facts: Mapping[str, Any], *, un_number: str,
                context=None, registry=None) -> SW19Assessment:
    """Resolve battery-specific applicability before the conditional voyage.

    Requires a sampled DGL SW19 entry. A known UN mismatch
    or inconsistent provision routing fails; absence of required evidence is
    unknown. Ordinary batteries do not require a voyage classification.
    """
    record = _record(facts.get('sw19'))
    if not _provenanced(record, context, registry):
        return SW19Assessment(None)
    number = str(un_number).removeprefix('UN')
    if number != context.un_number:
        return SW19Assessment(None)
    try:
        entry = lookup_dgl(int(number))
    except (KeyError, ValueError, TypeError):
        return SW19Assessment(None)
    if 'SW19' not in entry.source_stowage_codes:
        return SW19Assessment(None)
    shipment_un = record.get('un_number')
    if shipment_un is None:
        return SW19Assessment(None)
    if str(shipment_un).removeprefix('UN') != number:
        return SW19Assessment(False)
    condition, purpose = record.get('battery_condition'), record.get('transport_purpose')
    if (not isinstance(condition, str) or not isinstance(purpose, str)
            or condition not in {'intact', 'damaged_defective', 'cannot_be_diagnosed'}
            or purpose not in {'normal', 'disposal', 'recycling'}):
        return SW19Assessment(None)
    applicability = _record(record.get('special_provision_applicability'))
    actual = tuple(_boolean(applicability.get(code)) for code in ('376', '377'))
    if None in actual:
        return SW19Assessment(None)
    damaged = condition in {'damaged_defective', 'cannot_be_diagnosed'}
    # SP376 is mandatory for identified damaged/defective cells; SP377 offers
    # an optional route for disposal/recycling and redirects damage to SP376.
    if (actual[0] != damaged
            or (actual[1] and (damaged or purpose not in {'disposal', 'recycling'}))):
        return SW19Assessment(False)
    if any(actual):
        compliant = _boolean(record.get('transport_in_accordance'))
        if compliant is not True:
            return SW19Assessment(compliant)
    short = None
    if any(actual) or 'voyage' in record:
        voyage = _record(record.get('voyage'))
        if not _provenanced(voyage, context, registry):
            return SW19Assessment(None)
        short = _boolean(voyage.get('short_international_voyage'))
        if short is None:
            return SW19Assessment(None)
    # The model's SP field records actual applicability, not the DGL inventory.
    # Reconcile only after the necessary nested evidence is complete so that
    # incomplete new records retain their unknown result.
    shipment_sps = facts.get('special_provisions')
    declared = None
    if shipment_sps is not None:
        if (not isinstance(shipment_sps, (list, tuple))
                or any(not isinstance(code, (str, int)) or isinstance(code, bool)
                       or not str(code).isascii() or not str(code).isdecimal()
                       for code in shipment_sps)):
            return SW19Assessment(None)
        declared = tuple(code in set(map(str, shipment_sps)) for code in ('376', '377'))
    shipment_short = facts.get('short_international_voyage')
    if shipment_short is not None and _boolean(shipment_short) is None:
        return SW19Assessment(None)
    if (declared is not None and declared != actual
            or shipment_short is not None and short is not None and shipment_short != short):
        return SW19Assessment(False)
    return SW19Assessment(True, 'C' if any(actual) and short is False else entry.stowage_category)

"""Resolve IMDG evidence references against a shipment scenario registry.

The external shipment/scenario author supplies
``instance.rule_overrides['imdg.stowage.3_2']['assessment_scenarios']``.
It maps each provenance ``scenario_id`` to one record or a nonempty list of
records. Each record contains ``document``, ``clause``, every BindingContext
field except ``slot``, and ``allowed_slots``: an explicit nonempty list of actual
BBBRRTT placements. ``packing_group`` must be present even when its known value
is null. Reusing a shared design ID requires a separate complete record for each
shipment/entry/vessel/voyage/call context. Resolution must match one whole record;
fields and allowed slots are never combined across records. No wildcard or
implicit scope is supported. The checker never registers its own facts.

Registry presence binds an externally supplied evidence record to its declared
scope; it does not replace H1/SW19/SW1/SW2 substantive condition assessments.
This declarative registry is not an authenticator or a claim that an inspection
was observed: the bound shipment/design record still supplies the assessed
facts, and the external source author remains responsible for those facts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
import re

from stowage.conventions import parse_slot


@dataclass(frozen=True)
class BindingContext:
    """Exact expected shipment, entry, vessel, voyage, call and placement."""

    instance_id: str
    container_id: str
    entry_path: str
    un_number: str
    packing_group: str | None
    vessel_id: str
    voyage_id: str
    call_index: int
    slot: str


_BINDING_FIELDS = ('instance_id', 'container_id', 'entry_path', 'un_number',
                   'packing_group', 'vessel_id', 'voyage_id', 'call_index')
_TEXT_FIELDS = ('instance_id', 'container_id', 'vessel_id', 'voyage_id')
_ENTRY_PATH = re.compile(r'primary(?:\.additional\.(?:0|[1-9][0-9]*))*')


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _slot(value):
    if not isinstance(value, str) or re.fullmatch(r'[0-9]{7}', value) is None:
        return False
    try:
        parse_slot(value)
    except ValueError:
        return False
    return True


def _valid_context(context):
    return (isinstance(context, BindingContext)
            and all(_text(getattr(context, field)) for field in _TEXT_FIELDS)
            and isinstance(context.entry_path, str)
            and _ENTRY_PATH.fullmatch(context.entry_path) is not None
            and isinstance(context.un_number, str)
            and re.fullmatch(r'[0-9]{4}', context.un_number) is not None
            and (context.packing_group is None
                 or isinstance(context.packing_group, str) and context.packing_group in {'I', 'II', 'III'})
            and type(context.call_index) is int and context.call_index >= 0
            and _slot(context.slot))


def provenance_matches(provenance, context: BindingContext | None, registry) -> bool:
    """Fail closed unless this evidence resolves to exactly the expected scope.

    Provenance may be a JSON mapping or an evidence object exposing document,
    clause and scenario_id. Mere nonempty strings are insufficient: all three
    must resolve to a registered record, and every scope field must agree in
    that same record. A legacy single record retains its original semantics;
    list/tuple records let several exact contexts share an authored design ID.
    Malformed siblings grant no clearance and do not invalidate a valid row.
    """
    if not _valid_context(context) or not isinstance(registry, Mapping):
        return False
    try:
        values = {key: (provenance.get(key) if isinstance(provenance, Mapping)
                        else getattr(provenance, key, None))
                  for key in ('document', 'clause', 'scenario_id')}
        if not all(_text(value) for value in values.values()):
            return False
        registered = registry.get(values['scenario_id'])
        records = registered if isinstance(registered, (list, tuple)) else (registered,)
        return any(_record_matches(record, values, context) for record in records)
    except (AttributeError, TypeError, ValueError):
        return False


def _record_matches(record, provenance, context):
    """Resolve one indivisible registration, retaining strict legacy validation."""
    if not isinstance(record, Mapping):
        return False
    if any(record.get(key) != provenance[key] for key in ('document', 'clause')):
        return False
    if any(field not in record or record[field] != getattr(context, field)
           for field in _BINDING_FIELDS):
        return False
    # Python's True == 1 must not turn malformed call scope into permission.
    if type(record['call_index']) is not int:
        return False
    slots = record.get('allowed_slots')
    if not isinstance(slots, (list, tuple)) or not slots or not all(_slot(slot) for slot in slots):
        return False
    return len(slots) == len(set(slots)) and context.slot in slots


def voyage_identity(instance, call_index: int | None = None) -> str | None:
    """Hash the actual declared rotation leg and matching voyage condition IDs.

    VoyageSpec has departure/arrival condition identities, not a single voyage
    id. When it is absent, a declared rotation leg still identifies the scope;
    this conveys no short-international-voyage classification. A last call with
    no declared next call cannot establish a leg and remains unknown.
    """
    try:
        call = instance.call_index if call_index is None else call_index
        if type(call) is not int or call < 0 or call + 1 >= len(instance.rotation.calls):
            return None
        calls = instance.rotation.calls
        leg = [calls[index].model_dump(mode='json', exclude={'provenance'}) for index in (call, call + 1)]
        conditions = None
        voyage = instance.voyage
        if voyage is not None and voyage.departure.call_index == call:
            conditions = [condition.model_dump(mode='json', exclude={'provenance'})
                          for condition in (voyage.departure, voyage.arrival)]
        payload = {'rotation_service': instance.rotation.service, 'call_index': call,
                   'leg': leg, 'voyage_conditions': conditions}
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        return 'imdg-voyage-sha256:' + sha256(encoded).hexdigest()
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


def context_for(instance, box, entry_path: str, slot: str,
                call_index: int | None = None) -> BindingContext | None:
    """Build expected scope from actual model facts, never from evidence claims."""
    try:
        if not isinstance(entry_path, str) or _ENTRY_PATH.fullmatch(entry_path) is None:
            return None
        entry = box.dg
        indices = entry_path.split('.')[2::2]
        for index in indices:
            entry = entry.additional[int(index)]
        call = instance.call_index if call_index is None else call_index
        context = BindingContext(
            instance_id=instance.id, container_id=box.id, entry_path=entry_path,
            un_number=entry.un_number, packing_group=entry.packing_group,
            vessel_id=instance.vessel.id, voyage_id=voyage_identity(instance, call),
            call_index=call, slot=slot)
        return context if _valid_context(context) else None
    except (AttributeError, IndexError, TypeError, ValueError):
        return None

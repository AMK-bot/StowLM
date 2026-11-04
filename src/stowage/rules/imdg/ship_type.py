"""Resolve the declared IMDG 7.4.3 containership segregation-table domain.

Model-owner interface: Vessel.imdg_ship_type is a nullable literal with values
``closed_holds`` (cellular containership with hatch covers, table 7.4.3.2) and
``hatchless`` (hatchcoverless containership, table 7.4.3.3). None means unknown.
The fleet loader reads the same top-level parameter name. All three synthetic
feeders are explicitly authored as closed_holds; this reader never infers it
from hatch geometry, CTU closure, a vessel name, or the planner's placement.

Source: IMO MSC.556(108), IMDG Amendment 42-24, 7.4.3.1.2 and 7.4.3.2/3.
Legacy instance overrides remain independently declared input evidence. A
present malformed or conflicting declaration is unknown, not an override that
can relax the other declaration. An absent/None native field retains saved
single-override semantics. No field or provenance record is fabricated here.
"""
from collections.abc import Mapping


_TYPES = ('closed_holds', 'hatchless')


def resolve_ship_type(vessel, legacy_config) -> str | None:
    """Return the agreed table key, or unknown for missing/conflicting facts."""
    native = getattr(vessel, 'imdg_ship_type', None)
    if not isinstance(legacy_config, Mapping):
        return None
    if native is not None:
        if not isinstance(native, str) or native not in _TYPES:
            return None
        if 'ship_type' in legacy_config and legacy_config['ship_type'] != native:
            return None
        return native
    legacy = legacy_config.get('ship_type')
    return legacy if isinstance(legacy, str) and legacy in _TYPES else None

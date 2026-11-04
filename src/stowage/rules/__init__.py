"""Rule catalogue (plan 1.2: ``catalogue.py`` + ``catalogue.toml``) and IMDG data (plan 1.3: ``imdg/``).

Violations quote catalogue entries by stable string id (``rule_id: str``, dotted lower-case, e.g.
``imdg.segregation.7_4_3``). Ids never change once written. Reserved prefixes not in the catalogue file:
``checker.*`` (checker-internal: ``checker.missing_vessel_data``, ``checker.internal_error``), ``objective.*``
(``objective.all_loaded``, D11 level 2) and ``instructions.*`` (plan constraints from the instructions reader).

This ``__init__`` imports nothing so a broken catalogue never breaks ``import stowage``; use
``from stowage.rules import catalogue``.
"""

RESERVED_RULE_PREFIXES: tuple[str, ...] = ("checker.", "objective.", "instructions.")

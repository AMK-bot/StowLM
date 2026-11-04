"""Shared base class and constrained scalar types for the data model (plan 0.9 declares, 1.1 owns).

Conventions (docs/architecture.md "Conventions", ``stowage.conventions``): SI units, unit suffix in field names,
``BBBRRTT`` ship slots, ``BLOCK-BBB-RR-TT`` yard addresses, ISO 6346 container ids, UN/LOCODE ports.

Every model forbids unknown fields (a typo in a fixture fails loudly) and round-trips through JSON with
``model_dump_json()`` / ``model_validate_json()``. Collections that are semantically sets are stored as sorted lists
so JSON output is deterministic (byte-identical for equal objects); task 1.1 adds validators that sort/check them.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from stowage.conventions import is_valid_container_id


def _container_id(value: str) -> str:
    if not is_valid_container_id(value):
        raise ValueError(f"invalid ISO 6346 check digit: {value}")
    return value

ContainerId = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}[UJZ]\d{7}$"), AfterValidator(_container_id)]
Locode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}[A-Z2-9]{3}$")]  # UN/LOCODE
SlotId = Annotated[str, StringConstraints(pattern=r"^\d{7}$")]  # BBBRRTT (parse with conventions.parse_slot)
CellId = SlotId  # an odd-bay BBBRRTT: one physical 20' cell
StackId = Annotated[str, StringConstraints(pattern=r"^\d{5}[DH]$")]  # conventions.stack_id, e.g. "00101D"
YardAddr = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{1,4}-\d{3}-\d{2}-\d{2}$")]  # BLOCK-BBB-RR-TT
YardStackKey = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{1,4}-\d{3}-\d{2}$")]  # BLOCK-BBB-RR
HatchCoverId = Annotated[str, StringConstraints(pattern=r"^HC-[A-Za-z0-9_\-]+$")]  # e.g. "HC-02-P"
RuleId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")]  # catalogue id (1.2)
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.\-]*$")]

Kg = Annotated[float, Field(ge=0.0)]  # a non-negative mass in kg
Metres = float  # signed length in m (coordinates may be negative, e.g. y to starboard)
PosMetres = Annotated[float, Field(gt=0.0)]
PosFloat = Annotated[float, Field(gt=0.0)]  # any strictly positive quantity (unit in the field name)
Fraction = Annotated[float, Field(ge=0.0, le=1.0)]
CallIndex = Annotated[int, Field(ge=0)]  # 0-based index into Rotation.calls


class StowModel(BaseModel):
    """Base for every data-model class."""

    model_config = ConfigDict(extra="forbid", validate_assignment=False, ser_json_inf_nan="strings")

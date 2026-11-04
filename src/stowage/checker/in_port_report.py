"""Activated D74 report helpers using the public typed report contract.

The programme's wire location is parts.in_port.ballast_programme. It belongs to
the public PartReport schema so CheckReport and downstream SFT parsers restore
it without importing checker-specific subclasses. Legacy absent blocks remain
absent; activated explicit null blocks remain null.
"""
from __future__ import annotations

from pydantic import Field, SerializeAsAny, field_validator, model_validator

from stowage.model import CheckReport, PartReport
from stowage.model.results import BallastProgramme


class InPortReport(PartReport):
    @model_validator(mode='before')
    @classmethod
    def explicit_activated_programme(cls, value):
        if isinstance(value, dict) and 'ballast_programme' not in value:
            return {**value, 'ballast_programme': None}
        return value


class InPortCheckReport(CheckReport):
    # Retained compatibility type for existing checker callers. The public
    # PartReport now owns the block, including restoration of its typed value.
    parts: dict[str, SerializeAsAny[PartReport]] = Field(default_factory=dict)

    @field_validator('parts', mode='before')
    @classmethod
    def restore_in_port_part(cls, parts):
        if isinstance(parts, dict):
            return {name: InPortReport.model_validate(part)
                    if isinstance(part, dict) and 'ballast_programme' in part else part
                    for name,part in parts.items()}
        return parts


def attach_programme(report: PartReport, fields: dict) -> InPortReport:
    names = BallastProgramme.model_fields
    programme = BallastProgramme.model_validate({key: value for key, value in fields.items()
                                                if key in names})
    return InPortReport.model_validate({**report.model_dump(), 'ballast_programme': programme})

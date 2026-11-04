"""Conservative text fact grammar. Everything else needs planner confirmation.

Only an entire declarative JSON line or the explicit booking POL/POD line is
machine-authenticated. Finding a number or instruction somewhere in a quote is
not evidence of its cargo identity, field or value. Pictures need confirmation.
"""
import json
import re
from decimal import Decimal

from .provenance import _MISSING, _matches, _path, _strict_object


def text_evidence(document, row_id, field, value, quote):
    text = document.get('text', document.get('content', ''))
    if not isinstance(text, str) or not isinstance(quote, str) or not quote.strip() or quote not in text:
        return False
    for line in text.splitlines():
        if quote not in line:
            continue
        try:
            declaration = json.loads(line, parse_float=Decimal, object_pairs_hook=_strict_object)
        except (ValueError, TypeError):
            declaration = None
        if isinstance(declaration, dict):
            if declaration.get('id') == row_id:
                source = _path(declaration, field)
                if source is not _MISSING and _matches(source, value, field):
                    return True
            # Explicit equipment declarations may distinguish the booking's
            # identity from a suspect id printed on the box.
            if declaration.get('row_id') == row_id and isinstance(declaration.get('fields'), dict):
                source = _path(declaration['fields'], field)
                if source is not _MISSING and _matches(source, value, field):
                    return True
        booking = re.fullmatch(r'\s*([A-Z0-9]+)\s*//\s*POL\s+([A-Z]{5})\s*>\s*POD\s+([A-Z]{5})(?:\s*//\s*ETD\s+[A-Za-z0-9 -]+)?\s*', line)
        if booking and booking[1] == row_id and field in ('id', 'pol', 'pod'):
            source = booking[{'id': 1, 'pol': 2, 'pod': 3}[field]]
            if _matches(source, value, field):
                return True
    return False


def confirmed_value(confirmations, document, row_id, field, value):
    """Authority comes from answer_questions state, never document metadata."""
    confirmation = confirmations.get(document, {})
    if confirmation.get('row_id') != row_id:
        return False
    for name, source in confirmation.get('fields', {}).items():
        if name == field:
            candidate = source
        elif field.startswith(name + '.'):
            candidate = _path(source, field[len(name) + 1:])
        else:
            continue
        if candidate is not _MISSING and _matches(candidate, value, field):
            return True
    return False

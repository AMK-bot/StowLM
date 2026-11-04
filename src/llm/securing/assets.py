"""Validate declared securing inputs even when geometry is already cached."""
from pathlib import PurePosixPath

from stowage.vessel.fleet import load_ship_params
from stowage.vessel.hull_offsets import verified_offset_bytes


class SecuringConfigurationError(RuntimeError):
    """A declared physical input is absent, unreadable, or unverified."""


def validate_securing_assets(instance):
    """Do not turn a broken installation into unknown cargo/instance data.

    Deliberately run on every adapter construction, including supplied screens:
    cached geometry does not establish that its declared source is still valid.
    Missing hull/reference remains an unknown instance attribute, as before.
    """
    hull = getattr(instance.vessel, 'hull', None)
    reference = getattr(hull, 'hull_offsets_ref', None)
    if not reference:
        return
    name = PurePosixPath(reference.split('#')[0]).stem
    try:
        params = load_ship_params(name)
        form = params.get('hull_form', {})
        if form.get('family') == 'stowlm_offsets_v1':
            path = form.get('offsets_path')
            if not isinstance(path, str) or not path:
                raise ValueError('declared offsets_path is missing or invalid')
            digest = form.get('offsets_sha256')
            if (not isinstance(digest, str) or len(digest) != 64 or
                    any(c not in '0123456789abcdefABCDEF' for c in digest)):
                raise ValueError(f'declared offsets_sha256 is missing or invalid: {path}')
            verified_offset_bytes(path, expected_sha256=digest)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SecuringConfigurationError(
            f'Securing configuration error for {reference!r}: {exc}. '
            'Restore the declared checksum-pinned vessel data before decoding.'
        ) from exc

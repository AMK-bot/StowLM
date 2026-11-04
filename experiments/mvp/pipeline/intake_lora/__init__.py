"""Preparation/runtime API for the gated on-device intake experiment."""

__all__ = ['PrivateIntake', 'IntakeResult', 'LocalGlimmerModel']


def __getattr__(name):
    if name == 'IntakeResult':
        from experiments.mvp.pipeline.contract.schema import IntakeResult
        return IntakeResult
    if name in ('PrivateIntake', 'LocalGlimmerModel'):
        from .private_intake import LocalGlimmerModel, PrivateIntake
        return {'PrivateIntake': PrivateIntake, 'LocalGlimmerModel': LocalGlimmerModel}[name]
    raise AttributeError(name)

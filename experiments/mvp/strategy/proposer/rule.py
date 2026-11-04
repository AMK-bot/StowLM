"""Offline D185 rule arm, consuming the shared block-rule policy verbatim.

No planner/checker runs here: every returned permission layout must pass through
proposer's common evaluator and consume its own checked-evaluation budget.
"""
from __future__ import annotations

import hashlib
import importlib
from pathlib import Path

from experiments.mvp.strategy.proposer.format import format_layout
from experiments.mvp.strategy.proposer.providers import Proposal

BLOCKRULE_MODULE = "experiments.mvp.strategy.blockrule.rule"
BLOCKRULE_DIR = Path(__file__).resolve().parent.parent / "blockrule"
POLICY_VERSION = "D185-blockrule-shared-v1"
SCHEDULE = "odd proposals: shared port-blocking rule; even proposals: unrestricted default fallback"


def source_pins(directory: Path | None = None) -> dict[str, str]:
    """Hash shared policy source, excluding outputs and mutable run artifacts."""
    directory = directory or BLOCKRULE_DIR
    sources = [directory / "rule.py"]
    if not all(path.is_file() for path in sources):
        raise RuntimeError("D185 blockrule source is not ready; rule arm cannot run")
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sources}


def policy_registration():
    """Pin the dependency and fallback policy before any evaluation starts."""
    sources = source_pins()
    _load_policy()
    return {"policy_version": POLICY_VERSION, "source_sha256": sources,
            "schedule": SCHEDULE, "fallback_checks_counted": True,
            "certificate_reuse": False, "model_called": False}


def _load_policy():
    try:
        policy = importlib.import_module(BLOCKRULE_MODULE)
    except ModuleNotFoundError as error:
        raise RuntimeError("D185 blockrule policy is not ready; rule arm cannot run") from error
    if not callable(getattr(policy, "port_blocking_layout", None)):
        raise RuntimeError("D185 blockrule policy API is not ready; rule arm cannot run")
    return policy


class RuleProposer:
    """Deterministic policy; no certificate reuse or outcome-selected proposals.

    The shared API must exist at registration. Layout creation is lazy so a
    geometry failure consumes its proposal and can reach the counted fallback.
    """

    def __init__(self, instance, seed=0):
        self.instance = instance
        self.seed = seed
        self.registration = policy_registration()
        self.sources = self.registration["source_sha256"]
        self.policy = _load_policy()
        self.layouts = {}

    def propose(self, attempt):
        if type(attempt) is not int or attempt < 1:
            raise ValueError("rule proposal attempt must be a positive integer")
        if source_pins() != self.sources:
            raise ValueError("blockrule policy source changed during the run")
        kind = "port-blocking" if attempt % 2 else "default-fallback"
        if kind not in self.layouts:
            from experiments.mvp.strategy.headroom.layout import unrestricted_layout
            from stowage.rules.catalogue import rule_freeze_v1
            try:
                with rule_freeze_v1(enabled=False):
                    layout = (self.policy.port_blocking_layout(self.instance) if attempt % 2
                              else unrestricted_layout(self.instance))
                text = format_layout(layout, self.instance)
            except Exception as error:
                # This proposal is wasted; a later fallback remains an entirely
                # separate proposal with its own common-evaluator check.
                raise RuntimeError(f"rule proposal unavailable: {type(error).__name__}: {error}") from error
            self.layouts[kind] = layout, text
        layout, text = self.layouts[kind]
        return Proposal(text, {"provider": "rule", "model_called": False,
            "policy_version": POLICY_VERSION, "source_sha256": dict(self.sources),
            "fingerprint": layout.fingerprint, "layout": layout.to_dict(), "kind": kind,
            "attempt": attempt, "selection": SCHEDULE, "fallback_checks_counted": True})

    def observe(self, proposal, result):
        """Rule choice is independent of checker feedback; checks remain counted."""

"""Module-ownership map: every tier 1–5 task of docs/plan.md → its home paths (plan 0.9).

Rule: a task edits only its own paths. Shared files (``stowage/model/*``, ``stowage/frozen.py``,
``stowage/conventions.py``, ``stowage/versions.py`` — except each owner's own version line —, ``pyproject.toml``)
change only through their owner (X2). Mirrored as a table in docs/architecture.md "Module ownership";
``tests/test_ownership.py`` keeps the two honest and checks every tier 1–5 task id in docs/plan.md appears here.

Paths are repo-relative. A trailing ``/`` means a folder the task owns entirely; ``*`` globs are file families.
"""

from __future__ import annotations

from typing import Final

# task id → (title, owned code/data paths, owned test paths)
TASK_HOMES: Final[dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]]] = {
    # tier 0 (shared infrastructure)
    "0.9": (
        "Repo skeleton and frozen interfaces",
        ("src/stowage/frozen.py", "src/stowage/conventions.py", "src/stowage/versions.py",
         "src/stowage/ownership.py", "src/stowage/interfaces.py", "src/stowage/params.py", "src/stowage/planner.py",
         "src/stowage/planner_resequence.py",
         "src/stowage/__init__.py", "src/stowage/rules/__init__.py", "pyproject.toml", "conftest.py",
         "params/README.md", "src/stowage/cache_warm.py"),
        ("tests/test_conventions.py", "tests/test_frozen_interfaces.py", "tests/test_provenance.py",
         "tests/test_ownership.py", "tests/test_params_loader.py", "tests/test_model_contracts.py",
         "tests/test_planner*.py"),
    ),
    # tier 1
    "1.1": ("Core data model", ("src/stowage/model/", "src/stowage/io.py", "data/fixtures/"), ("tests/model/",)),
    "1.2": ("Rule catalogue", ("src/stowage/rules/catalogue.py", "src/stowage/rules/catalogue.toml"),
            ("tests/rules/test_catalogue*",)),
    "1.3": ("IMDG data", ("src/stowage/rules/imdg/",), ("tests/rules/test_imdg*",)),
    "1.4": ("Cargo distributions", ("params/cargo/",), ("tests/params/test_cargo_params*",)),
    "1.5": ("Port archetypes", ("params/ports/",), ("tests/params/test_ports_params*",)),
    "1.6": ("Synthetic fleet particulars", ("params/fleet/",), ("tests/params/test_fleet_params*",)),
    "1.7": ("Rebase the impact estimate on moves", ("experiments/impact-estimate/", "docs/impact.md"), ()),
    "1.8": ("Muse Spark harness", ("src/llm/spark/__init__.py", "src/llm/spark/client.py", "src/llm/spark/spend.py"),
            ("tests/llm/test_spark_client*", "tests/llm/test_spark_spend*.py")),
    "1.9": ("CUDA venue smoke on Modal", ("experiments/grpo-venue/",), ()),
    # tier 2
    "2.1": ("Hull and hydrostatics", ("src/stowage/vessel/hull*", "src/stowage/vessel/hydrostatics.py",
                                      "src/stowage/vessel/tanks.py"), ("tests/vessel/test_hydrostatics*", "tests/vessel/test_hull*",
                                                       "tests/vessel/test_tanks*")),
    "2.2": ("Vessel instances", ("src/stowage/vessel/__init__.py", "src/stowage/vessel/fleet*",
                                 "src/stowage/vessel/larsen_pacino.py"), ("tests/vessel/test_fleet*",
                                                                       "tests/vessel/test_larsen_pacino*")),
    "2.3": ("Move engine", ("src/stowage/moves/",), ("tests/moves/",)),
    "2.4": ("Stowage rule checker, non-physics part", ("src/stowage/checker/rules*",), ("tests/checker/test_rules*",)),
    "2.5": ("Load-list generator", ("src/stowage/generate/cargo*",), ("tests/generate/test_cargo*",)),
    "2.6": ("Port profiles and yards", ("src/stowage/ports/",), ("tests/ports/",)),
    # tier 3
    "3.1": ("Intact stability, exact", ("src/stowage/checker/stability*",), ("tests/checker/test_stability*",)),
    "3.2": ("Longitudinal strength, exact", ("src/stowage/checker/strength*",), ("tests/checker/test_strength*",)),
    "3.3": ("Visibility and lashing", ("src/stowage/checker/visibility*", "src/stowage/checker/lashing*"),
            ("tests/checker/test_visibility*", "tests/checker/test_lashing*")),
    "3.4": ("Port-state simulator", ("src/stowage/yard/",), ("tests/yard/",)),
    "3.5": ("Random-yard instance generator", ("src/stowage/generate/random*",), ("tests/generate/test_random*",)),
    "3.6": ("Lower bounds on moves", ("src/stowage/bounds/",), ("tests/bounds/",)),
    "3.7": ("Linear stability/strength model + ballast LP", ("src/stowage/linear/",), ("tests/linear/",)),
    "3.8": ("Action space and expander", ("src/stowage/actions/",), ("tests/actions/",)),
    # tier 4
    "4.1": ("Full checker", ("src/stowage/checker/__init__.py", "src/stowage/checker/full.py",
                           "src/stowage/checker/perf.py", "src/stowage/checker/slots.py",
                           "src/stowage/checker/in_port*"),
            ("tests/checker/test_full*",)),
    "4.2": ("Ballast finisher", ("src/stowage/solver/ballast*",), ("tests/solver/test_ballast*",)),
    "4.3": ("Simulated-yard instance generator", ("src/stowage/generate/instances*", "src/stowage/generate/__init__.py",
                                                "src/stowage/generate/background_acceptance.py",
                                                "src/stowage/generate/physics_identity.py"),
            ("tests/generate/test_instances*",)),
    "4.4": ("Incremental feasibility engine", ("src/stowage/feasibility/",), ("tests/feasibility/",)),
    "4.5": ("StowLM serialisation", ("src/llm/stowlm/format*",), ("tests/llm/test_format*",)),
    "4.6": ("Exact solver", ("src/stowage/solver/exact*",), ("tests/solver/test_exact*",)),
    "4.7": ("Scalable solver", ("src/stowage/solver/scalable*", "src/stowage/solver/__init__.py",
                              "src/stowage/solver/securing_geometry.py",
                              "src/stowage/solver/securing_screen.py",
                              "src/stowage/solver/construct*"),
            ("tests/solver/test_scalable*",)),
    "4.8": ("Industry two-step baseline", ("src/stowage/solver/twostep*",), ("tests/solver/test_twostep*",)),
    "4.9": ("Demo UI v1", ("app/",), ("app/tests/",)),
    "4.10": ("Validate on public yard benchmarks", ("src/stowage/bench/",), ("tests/benchmarks/",)),
    "4.11": ("Linear-model qualification against the exact checker", ("experiments/linear-model/epoch_requalification/",), ()),
    # tier 5
    "5.1": ("Feasibility mask", ("src/llm/stowlm/mask*", "src/llm/stowlm/__init__.py", "src/llm/__init__.py"),
            ("tests/llm/test_mask*",)),
    "5.2": ("Evaluation harness", ("src/eval/harness*", "src/eval/__init__.py", "src/eval/snapshot_io.py",
                                  "src/eval/dg_coverage.py", "src/eval/instance_facts.py",
                                  "src/eval/mask_observation.py", "src/eval/plan_diagnostics.py",
                                  "src/eval/sample_reporting.py", "src/eval/training_lineage.py",
                                  "src/eval/yard_metrics.py"), ("tests/eval/test_harness*",)),
    "5.3": ("Data splits", ("src/eval/splits*",), ("tests/eval/test_splits*",)),
    "5.4": ("Solver validation and sizing", ("experiments/solver-validation/", "experiments/campaign-g1/"), ()),
    "5.5": ("Muse Spark zero-shot planner", ("src/llm/spark/planner*", "src/llm/spark/rules_appendix.py"),
            ("tests/llm/test_spark_planner*",)),
    "5.6": ("Plan explainer", ("src/llm/spark/explain*",), ("tests/llm/test_spark_explain*",)),
    "5.7": ("Repair fallback", ("src/stowage/solver/repair*",), ("tests/solver/test_repair*",)),
    "5.8": ("Demo UI v2", ("app/",), ("app/tests/",)),
    "5.9": ("SFT throughput calibration and slim config", ("experiments/glimmer-bench/",), ()),
    "5.10": ("Spark instruction parser", ("src/llm/instructions/",), ("tests/llm/instructions/",)),
}

# Later tiers, for orientation (not required by 0.9's done criterion).
LATER_HOMES: Final[dict[str, tuple[str, ...]]] = {
    "6.1": ("experiments/gold-plans/",),
    "6.4": ("src/llm/rl/",),
    "7.1": ("src/llm/stowlm/sft_data.py",),
    "8.1": ("src/llm/stowlm/train.py", "src/llm/stowlm/planner.py"),
    "10.3": ("src/stowage/generate/layover.py",),
    "19.1": ("src/stowage/generate/two_port.py",),
}

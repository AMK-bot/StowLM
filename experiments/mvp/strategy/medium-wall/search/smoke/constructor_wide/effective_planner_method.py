def plan(self, instance: Instance, *, seed: int = 0, time_limit_s: float | None = None) -> PlannerResult:
    from stowage.solver.twostep_terminal import terminal_plan
    start = monotonic()
    limit = 60.0 if time_limit_s is None else time_limit_s
    valid_budget = math.isfinite(limit) and limit >= 0
    deadline = start + limit if valid_budget else start
    effective_config = self.feasibility_config
    if effective_config is None and _use_fast_prestow(instance):
        from stowage.feasibility import FeasibilityConfig
        effective_config = FeasibilityConfig(extra={"global_screen": 0})
    provenance = current_provenance(created_by="solver.twostep", seed=seed).merge(instance.provenance)
    metrics = {"deterministic": 0, "num_workers": 1, "fixed_slot_failure": 0,
               "preplan_status": "not_started", "terminal_status": "not_started",
               "preplan_method": "bounded_greedy" if _use_fast_prestow(instance) else "dfs",
               "preplan_time_s": 0., "terminal_time_s": 0.,
               "terminal_expansions": 0, "terminal_max_legal_choices": 0,
               "feasibility_adapter": "twostep-scoped-v4-with-public-fallback"}
    plan = None
    try:
        if not valid_budget:
            status, message = "error", "time_limit_s must be finite and nonnegative"
        else:
            metrics["time_budget_s"] = limit
            stage_start = perf_counter()
            try:
                prestow = _preplan(instance, beam_width=self.beam_width, deadline=start + limit * 0.9, feasibility_config=effective_config, seed=seed)
            finally:
                metrics["preplan_time_s"] = perf_counter() - stage_start
            metrics.update(preplan_status=prestow.status, preplan_message=prestow.message,
                preplan_slots=[{"slot": s.slot, "category": s.category,
                                "representative_box": s.representative_box} for s in prestow.slots])
            metrics.update(prestow.search_metrics or {})
            status, message = prestow.status, prestow.message
            if prestow.status == "ok":
                stage_start = perf_counter()
                try:
                    plan, terminal_metrics, status, message = terminal_plan(instance, prestow,
                        deadline=deadline, seed=seed, engine_factory=make_feasibility_engine, feasibility_config=effective_config)
                finally:
                    metrics["terminal_time_s"] = perf_counter() - stage_start
                metrics.update(terminal_metrics)
    except NotImplementedError as exc:
        status, message = "error", f"Shared feasibility checks unavailable: {exc}"
    except Exception as exc:
        status, message = "error", f"{type(exc).__name__}: {exc}"
    if plan is not None:
        plan = plan.model_copy(update={"provenance": plan.provenance.merge(provenance)})
    elapsed = monotonic() - start
    if valid_budget:
        # Public oracle calls are indivisible. Expose their actual overrun
        # rather than presenting a requested budget as a hard wall limit.
        metrics["budget_overrun_s"] = max(0.0, elapsed - limit)
    return PlannerResult(status=status, plan=plan, planner=self.name, planner_version=self.version,
        seed=seed, wall_time_s=elapsed, metrics=metrics, message=message,
        provenance=provenance)

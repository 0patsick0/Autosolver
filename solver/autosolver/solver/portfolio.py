from __future__ import annotations

import time
from dataclasses import replace

from autosolver.core.candidates import generate_candidate_options
from autosolver.core.models import CanonicalInstance, LexicographicScore, SolveConfig, SolveResult
from autosolver.core.objective import better_score
from autosolver.solver.cpsat import cpsat_result
from autosolver.solver.greedy import greedy_result
from autosolver.solver.lns import lns_result


class PortfolioSolver:
    def solve(self, instance: CanonicalInstance, time_budget_ms: int = 10_000, seed: int = 0, config: SolveConfig | None = None) -> SolveResult:
        effective_config = config or SolveConfig(time_budget_ms=time_budget_ms)
        if effective_config.time_budget_ms != time_budget_ms:
            effective_config = replace(effective_config, time_budget_ms=time_budget_ms)

        start_time = time.perf_counter()
        deadline = start_time + effective_config.time_budget_ms / 1000.0
        options = generate_candidate_options(instance, effective_config)
        greedy = greedy_result(instance, options, start_time)
        incumbent = greedy

        if effective_config.use_cpsat and time.perf_counter() < deadline:
            refined = cpsat_result(instance, options, effective_config, start_time, deadline)
            if refined is not None and better_score(refined.objective, incumbent.objective):
                incumbent = refined

        if effective_config.use_lns and time.perf_counter() < deadline:
            incumbent_options = [option for option in options if option.id in incumbent.selected_option_ids]
            improved = lns_result(instance, options, incumbent_options, effective_config, start_time, deadline, seed)
            if improved is not None and better_score(improved.objective, incumbent.objective):
                incumbent = improved

        candidate_breakdown = _option_breakdown(options)
        selected_options = [option for option in options if option.id in incumbent.selected_option_ids]
        selected_breakdown = _option_breakdown(selected_options)

        return SolveResult(
            instance_id=incumbent.instance_id,
            solver_name=f"portfolio[{incumbent.solver_name}]",
            status=incumbent.status,
            objective=LexicographicScore(
                expected_completed_orders=incumbent.objective.expected_completed_orders,
                total_cost=incumbent.objective.total_cost,
            ),
            selected_option_ids=incumbent.selected_option_ids,
            dispatches=incumbent.dispatches,
            unmatched_order_ids=incumbent.unmatched_order_ids,
            elapsed_ms=max(1, int((time.perf_counter() - start_time) * 1000)),
            stats={
                **incumbent.stats,
                "candidate_option_count": len(options),
                "candidate_option_breakdown": candidate_breakdown,
                "selected_option_breakdown": selected_breakdown,
                "selected_bundle_order_count": sum(len(option.order_ids) for option in selected_options if option.kind == "bundle"),
                "portfolio_seed": seed,
            },
        )


def _option_breakdown(options: list[object]) -> dict[str, int]:
    breakdown = {"single": 0, "multi_assign": 0, "bundle": 0}
    for option in options:
        kind = getattr(option, "kind", "")
        if kind in breakdown:
            breakdown[kind] += 1
    return breakdown

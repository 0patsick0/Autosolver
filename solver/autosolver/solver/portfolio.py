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
        require_full_coverage = not instance.constraints.allow_reject
        if require_full_coverage and not _all_orders_coverable(instance, options):
            return _build_infeasible_result(
                instance=instance,
                start_time=start_time,
                stats={"reason": "order_not_coverable", "candidate_option_count": len(options)},
            )

        greedy = greedy_result(instance, options, effective_config, start_time)
        incumbent = greedy
        lns_skipped_reason: str | None = None

        should_run_cpsat = effective_config.use_cpsat or require_full_coverage
        if should_run_cpsat and time.perf_counter() < deadline:
            refined = cpsat_result(
                instance=instance,
                options=options,
                config=effective_config,
                start_time=start_time,
                deadline=deadline,
                incumbent_option_ids=incumbent.selected_option_ids,
                require_full_coverage=require_full_coverage,
            )
            if refined is not None and _prefer_result(
                candidate=refined,
                incumbent=incumbent,
                instance=instance,
                require_full_coverage=require_full_coverage,
            ):
                incumbent = refined

        skip_lns = _should_skip_lns(incumbent)
        if effective_config.use_lns and time.perf_counter() < deadline and not skip_lns:
            incumbent_options = [option for option in options if option.id in incumbent.selected_option_ids]
            improved = lns_result(instance, options, incumbent_options, effective_config, start_time, deadline, seed)
            if improved is not None and _prefer_result(
                candidate=improved,
                incumbent=incumbent,
                instance=instance,
                require_full_coverage=require_full_coverage,
            ):
                incumbent = improved
        elif skip_lns:
            lns_skipped_reason = "cpsat_optimal"
        elif not effective_config.use_lns:
            lns_skipped_reason = "disabled"
        elif time.perf_counter() >= deadline:
            lns_skipped_reason = "budget_exhausted"

        if require_full_coverage and not _result_covers_all_orders(instance, incumbent):
            return _build_infeasible_result(
                instance=instance,
                start_time=start_time,
                stats={
                    "reason": "full_coverage_required",
                    "candidate_option_count": len(options),
                    "best_partial_expected_completed_orders": incumbent.objective.expected_completed_orders,
                },
            )

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
                "capacity_consumption_mode": effective_config.capacity_consumption_mode,
                "generate_bundles_if_missing": effective_config.generate_bundles_if_missing,
                "max_generated_bundles": effective_config.max_generated_bundles,
                "bundle_candidate_pool_size": effective_config.bundle_candidate_pool_size,
                "max_bundle_size": effective_config.max_bundle_size,
                "bundle_distance_threshold": effective_config.bundle_distance_threshold,
                "bundle_discount_factor": effective_config.bundle_discount_factor,
                "bundle_acceptance_scale": effective_config.bundle_acceptance_scale,
                "lns_ran": lns_skipped_reason is None and effective_config.use_lns,
                "lns_skipped_reason": lns_skipped_reason,
            },
        )


def _option_breakdown(options: list[object]) -> dict[str, int]:
    breakdown = {"single": 0, "multi_assign": 0, "bundle": 0}
    for option in options:
        kind = getattr(option, "kind", "")
        if kind in breakdown:
            breakdown[kind] += 1
    return breakdown


def _all_orders_coverable(instance: CanonicalInstance, options: list[object]) -> bool:
    option_orders = {order_id for option in options for order_id in getattr(option, "order_ids", ())}
    return all(order.id in option_orders for order in instance.orders)


def _result_covers_all_orders(instance: CanonicalInstance, result: SolveResult) -> bool:
    covered = {dispatch.order_id for dispatch in result.dispatches}
    return len(covered) == len(instance.orders)


def _prefer_result(candidate: SolveResult, incumbent: SolveResult, instance: CanonicalInstance, require_full_coverage: bool) -> bool:
    if not require_full_coverage:
        return better_score(candidate.objective, incumbent.objective)

    candidate_feasible = _result_covers_all_orders(instance, candidate)
    incumbent_feasible = _result_covers_all_orders(instance, incumbent)
    if candidate_feasible and not incumbent_feasible:
        return True
    if incumbent_feasible and not candidate_feasible:
        return False
    return better_score(candidate.objective, incumbent.objective)


def _build_infeasible_result(instance: CanonicalInstance, start_time: float, stats: dict[str, object]) -> SolveResult:
    return SolveResult(
        instance_id=instance.instance_id,
        solver_name="portfolio[infeasible]",
        status="infeasible",
        objective=LexicographicScore(expected_completed_orders=0.0, total_cost=0.0),
        selected_option_ids=(),
        dispatches=(),
        unmatched_order_ids=tuple(sorted(order.id for order in instance.orders)),
        elapsed_ms=max(1, int((time.perf_counter() - start_time) * 1000)),
        stats=stats,
    )


def _should_skip_lns(result: SolveResult) -> bool:
    if "cpsat" not in result.solver_name:
        return False
    status = result.stats.get("cpsat_status")
    return status == "optimal"

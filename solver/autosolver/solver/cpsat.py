from __future__ import annotations

import time

from autosolver.core.models import CandidateOption, CanonicalInstance, LexicographicScore, SolveConfig
from autosolver.core.objective import better_score
from autosolver.solver.common import build_result, rider_consumption_for_option

try:
    from ortools.sat.python import cp_model
except Exception:  # pragma: no cover - fallback path covered by tests via monkeypatch
    cp_model = None


def cpsat_refine(
    instance: CanonicalInstance,
    options: list[CandidateOption],
    config: SolveConfig,
    start_time: float,
    deadline: float,
    incumbent_option_ids: tuple[str, ...] = (),
    require_full_coverage: bool = False,
    time_limit_seconds: float | None = None,
) -> tuple[list[CandidateOption] | None, int, str]:
    if cp_model is None:
        return None, max(1, int((time.perf_counter() - start_time) * 1000)), "unavailable"

    remaining_seconds = deadline - time.perf_counter()
    allowed_seconds = min(remaining_seconds, time_limit_seconds) if time_limit_seconds is not None else remaining_seconds
    if allowed_seconds <= 0.02:
        return None, max(1, int((time.perf_counter() - start_time) * 1000)), "budget_exhausted"

    model = cp_model.CpModel()
    variables = [model.NewBoolVar(f"x_{index}") for index, _ in enumerate(options)]
    order_to_option_indices: dict[str, list[int]] = {}
    rider_to_weighted_option_indices: dict[str, list[tuple[int, int]]] = {}

    for index, option in enumerate(options):
        for order_id in option.order_ids:
            order_to_option_indices.setdefault(order_id, []).append(index)
        for rider_id, consumed in rider_consumption_for_option(option, config).items():
            rider_to_weighted_option_indices.setdefault(rider_id, []).append((index, consumed))

    for order in instance.orders:
        indices = order_to_option_indices.get(order.id, [])
        if require_full_coverage:
            if not indices:
                return None, max(1, int((time.perf_counter() - start_time) * 1000)), "infeasible"
            model.Add(sum(variables[index] for index in indices) == 1)
        elif indices:
            model.Add(sum(variables[index] for index in indices) <= 1)

    rider_capacities = {rider.id: rider.capacity for rider in instance.riders}
    for rider_id, weighted_indices in rider_to_weighted_option_indices.items():
        model.Add(
            sum(consumed * variables[index] for index, consumed in weighted_indices)
            <= rider_capacities.get(rider_id, 0)
        )

    scaled_costs = [int(round(option.total_cost * 1_000)) for option in options]
    scaled_values = [int(round(option.expected_completed_orders * 1_000_000)) for option in options]
    big_m = sum(scaled_costs) + 1
    model.Maximize(
        sum((scaled_values[index] * big_m - scaled_costs[index]) * variables[index] for index in range(len(options)))
    )

    index_by_option_id = {option.id: index for index, option in enumerate(options)}
    for option_id in incumbent_option_ids:
        index = index_by_option_id.get(option_id)
        if index is not None:
            model.AddHint(variables[index], 1)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.03, allowed_seconds)
    solver.parameters.num_search_workers = 8

    status = solver.Solve(model)
    elapsed_ms = max(1, int((time.perf_counter() - start_time) * 1000))
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, elapsed_ms, "no_solution"

    selected = [option for index, option in enumerate(options) if solver.Value(variables[index])]
    solve_status = "optimal" if status == cp_model.OPTIMAL else "feasible"
    return selected, elapsed_ms, solve_status


def cpsat_result(
    instance: CanonicalInstance,
    options: list[CandidateOption],
    config: SolveConfig,
    start_time: float,
    deadline: float,
    incumbent_option_ids: tuple[str, ...] = (),
    require_full_coverage: bool = False,
):
    best_selected: list[CandidateOption] | None = None
    best_elapsed_ms = max(1, int((time.perf_counter() - start_time) * 1000))
    hint_ids = incumbent_option_ids
    best_status = "none"

    quick_seconds = min(max(0.03, config.cpsat_quick_pass_ms / 1000.0), max(0.0, deadline - time.perf_counter()))
    if quick_seconds > 0.02:
        quick_selected, quick_elapsed_ms, quick_status = cpsat_refine(
            instance=instance,
            options=options,
            config=config,
            start_time=start_time,
            deadline=deadline,
            incumbent_option_ids=hint_ids,
            require_full_coverage=require_full_coverage,
            time_limit_seconds=quick_seconds,
        )
        if quick_selected is not None:
            best_selected = quick_selected
            best_elapsed_ms = quick_elapsed_ms
            hint_ids = tuple(option.id for option in quick_selected)
            best_status = quick_status

    remaining_seconds = max(0.0, deadline - time.perf_counter())
    full_seconds = remaining_seconds * max(0.0, min(1.0, config.cpsat_full_pass_ratio))
    should_run_full_pass = full_seconds > 0.03 and best_status != "optimal"
    if should_run_full_pass:
        full_selected, full_elapsed_ms, full_status = cpsat_refine(
            instance=instance,
            options=options,
            config=config,
            start_time=start_time,
            deadline=deadline,
            incumbent_option_ids=hint_ids,
            require_full_coverage=require_full_coverage,
            time_limit_seconds=full_seconds,
        )
        if full_selected is not None and (
            best_selected is None or better_score(_selected_score(full_selected), _selected_score(best_selected))
        ):
            best_selected = full_selected
            best_elapsed_ms = full_elapsed_ms
            best_status = full_status

    if best_selected is None:
        return None
    return build_result(
        instance=instance,
        solver_name="cpsat",
        status="ok",
        selected_options=best_selected,
        elapsed_ms=best_elapsed_ms,
        stats={
            "strategy": "set_packing_cpsat_multi_pass",
            "options_considered": len(options),
            "warm_start_count": len(incumbent_option_ids),
            "require_full_coverage": require_full_coverage,
            "cpsat_status": best_status,
            "cpsat_full_pass_enabled": should_run_full_pass,
        },
    )


def _selected_score(selected: list[CandidateOption]) -> LexicographicScore:
    return LexicographicScore(
        expected_completed_orders=sum(option.expected_completed_orders for option in selected),
        total_cost=sum(option.total_cost for option in selected),
    )

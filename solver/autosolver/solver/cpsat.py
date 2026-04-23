from __future__ import annotations

import time

from autosolver.core.models import CandidateOption, CanonicalInstance, SolveConfig
from autosolver.solver.common import build_result

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
) -> tuple[list[CandidateOption] | None, int]:
    if cp_model is None:
        return None, max(1, int((time.perf_counter() - start_time) * 1000))
    if len(instance.orders) > config.cpsat_max_orders:
        return None, max(1, int((time.perf_counter() - start_time) * 1000))

    remaining_seconds = deadline - time.perf_counter()
    if remaining_seconds <= 0.02:
        return None, max(1, int((time.perf_counter() - start_time) * 1000))

    model = cp_model.CpModel()
    variables = [model.NewBoolVar(f"x_{index}") for index, _ in enumerate(options)]
    order_to_option_indices: dict[str, list[int]] = {}
    rider_to_option_indices: dict[str, list[int]] = {}

    for index, option in enumerate(options):
        for order_id in option.order_ids:
            order_to_option_indices.setdefault(order_id, []).append(index)
        for rider_id in option.rider_ids:
            rider_to_option_indices.setdefault(rider_id, []).append(index)

    for order_id, indices in order_to_option_indices.items():
        del order_id
        model.Add(sum(variables[index] for index in indices) <= 1)

    rider_capacities = {rider.id: rider.capacity for rider in instance.riders}
    for rider_id, indices in rider_to_option_indices.items():
        model.Add(sum(variables[index] for index in indices) <= rider_capacities.get(rider_id, 0))

    scaled_costs = [int(round(option.total_cost * 1_000)) for option in options]
    scaled_values = [int(round(option.expected_completed_orders * 1_000_000)) for option in options]
    big_m = sum(scaled_costs) + 1
    model.Maximize(
        sum((scaled_values[index] * big_m - scaled_costs[index]) * variables[index] for index in range(len(options)))
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.05, remaining_seconds)
    solver.parameters.num_search_workers = 8

    status = solver.Solve(model)
    elapsed_ms = max(1, int((time.perf_counter() - start_time) * 1000))
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, elapsed_ms

    selected = [option for index, option in enumerate(options) if solver.Value(variables[index])]
    return selected, elapsed_ms


def cpsat_result(
    instance: CanonicalInstance,
    options: list[CandidateOption],
    config: SolveConfig,
    start_time: float,
    deadline: float,
):
    selected, elapsed_ms = cpsat_refine(instance, options, config, start_time, deadline)
    if selected is None:
        return None
    return build_result(
        instance=instance,
        solver_name="cpsat",
        status="ok",
        selected_options=selected,
        elapsed_ms=elapsed_ms,
        stats={"strategy": "set_packing_cpsat", "options_considered": len(options)},
    )

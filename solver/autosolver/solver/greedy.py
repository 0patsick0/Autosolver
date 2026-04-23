from __future__ import annotations

import time

from autosolver.core.models import CandidateOption, CanonicalInstance, SolveConfig
from autosolver.solver.common import apply_option, build_result, can_take_option


def greedy_solve(
    instance: CanonicalInstance,
    options: list[CandidateOption],
    config: SolveConfig,
    start_time: float,
) -> tuple[list[CandidateOption], int]:
    rider_remaining = {rider.id: rider.capacity for rider in instance.riders}
    covered_orders: set[str] = set()
    selected: list[CandidateOption] = []

    ranked_options = sorted(
        options,
        key=lambda option: (
            -option.expected_completed_orders,
            option.total_cost / max(1, len(option.order_ids)),
            len(option.rider_ids),
            option.id,
        ),
    )

    for option in ranked_options:
        if can_take_option(option, covered_orders, rider_remaining, config):
            apply_option(option, covered_orders, rider_remaining, config)
            selected.append(option)

    elapsed_ms = max(1, int((time.perf_counter() - start_time) * 1000))
    return selected, elapsed_ms


def greedy_result(instance: CanonicalInstance, options: list[CandidateOption], config: SolveConfig, start_time: float):
    selected, elapsed_ms = greedy_solve(instance, options, config, start_time)
    return build_result(
        instance=instance,
        solver_name="greedy",
        status="ok",
        selected_options=selected,
        elapsed_ms=elapsed_ms,
        stats={"strategy": "density_greedy", "options_considered": len(options)},
    )

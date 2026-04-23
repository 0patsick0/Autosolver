from __future__ import annotations

import random
import time

from autosolver.core.models import CandidateOption, CanonicalInstance, LexicographicScore, SolveConfig
from autosolver.core.objective import better_score
from autosolver.solver.common import apply_option, build_result, can_take_option


def lns_improve(
    instance: CanonicalInstance,
    options: list[CandidateOption],
    incumbent_options: list[CandidateOption],
    config: SolveConfig,
    start_time: float,
    deadline: float,
    seed: int,
) -> tuple[list[CandidateOption], int]:
    randomizer = random.Random(seed)
    incumbent = list(incumbent_options)
    incumbent_score = LexicographicScore(
        expected_completed_orders=sum(option.expected_completed_orders for option in incumbent),
        total_cost=sum(option.total_cost for option in incumbent),
    )

    for _ in range(config.lns_iterations):
        if time.perf_counter() >= deadline:
            break

        destroy_count = max(1, int(len(instance.orders) * config.lns_destroy_fraction))
        destroyed_orders = set(randomizer.sample([order.id for order in instance.orders], k=min(destroy_count, len(instance.orders))))
        frozen = [option for option in incumbent if destroyed_orders.isdisjoint(option.order_ids)]

        rider_remaining = {rider.id: rider.capacity for rider in instance.riders}
        covered_orders: set[str] = set()
        for option in frozen:
            apply_option(option, covered_orders, rider_remaining)

        repair_pool = sorted(
            [option for option in options if not destroyed_orders.isdisjoint(option.order_ids)],
            key=lambda option: (
                -option.expected_completed_orders,
                option.total_cost / max(1, len(option.order_ids)),
                len(option.rider_ids),
                option.id,
            ),
        )

        candidate = list(frozen)
        for option in repair_pool:
            if can_take_option(option, covered_orders, rider_remaining):
                apply_option(option, covered_orders, rider_remaining)
                candidate.append(option)

        candidate_score = LexicographicScore(
            expected_completed_orders=sum(option.expected_completed_orders for option in candidate),
            total_cost=sum(option.total_cost for option in candidate),
        )
        if better_score(candidate_score, incumbent_score):
            incumbent = candidate
            incumbent_score = candidate_score

    elapsed_ms = max(1, int((time.perf_counter() - start_time) * 1000))
    return incumbent, elapsed_ms


def lns_result(
    instance: CanonicalInstance,
    options: list[CandidateOption],
    incumbent_options: list[CandidateOption],
    config: SolveConfig,
    start_time: float,
    deadline: float,
    seed: int,
):
    improved, elapsed_ms = lns_improve(instance, options, incumbent_options, config, start_time, deadline, seed)
    return build_result(
        instance=instance,
        solver_name="lns",
        status="ok",
        selected_options=improved,
        elapsed_ms=elapsed_ms,
        stats={"strategy": "destroy_repair_greedy", "iterations": config.lns_iterations},
    )

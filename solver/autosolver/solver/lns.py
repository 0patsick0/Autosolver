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
    best = list(incumbent_options)
    best_score = LexicographicScore(
        expected_completed_orders=sum(option.expected_completed_orders for option in best),
        total_cost=sum(option.total_cost for option in best),
    )
    restart_count = max(1, config.lns_restarts)
    iteration_plan = _iteration_plan(config.lns_iterations, restart_count)

    for restart_index in range(restart_count):
        if time.perf_counter() >= deadline:
            break

        strategy = _restart_strategy(restart_index)
        current = list(best)
        current_score = LexicographicScore(
            expected_completed_orders=best_score.expected_completed_orders,
            total_cost=best_score.total_cost,
        )
        iterations = iteration_plan[restart_index]

        for iteration_index in range(iterations):
            if time.perf_counter() >= deadline:
                break

            destroy_count = max(1, int(len(instance.orders) * config.lns_destroy_fraction))
            destroyed_orders = _select_destroyed_orders(
                instance=instance,
                current=current,
                destroy_count=destroy_count,
                strategy=strategy,
                randomizer=randomizer,
                round_offset=restart_index + iteration_index,
            )
            frozen = [option for option in current if destroyed_orders.isdisjoint(option.order_ids)]

            rider_remaining = {rider.id: rider.capacity for rider in instance.riders}
            covered_orders: set[str] = set()
            for option in frozen:
                apply_option(option, covered_orders, rider_remaining, config)

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
                if can_take_option(option, covered_orders, rider_remaining, config):
                    apply_option(option, covered_orders, rider_remaining, config)
                    candidate.append(option)

            candidate_score = LexicographicScore(
                expected_completed_orders=sum(option.expected_completed_orders for option in candidate),
                total_cost=sum(option.total_cost for option in candidate),
            )
            if better_score(candidate_score, current_score):
                current = candidate
                current_score = candidate_score
                if better_score(candidate_score, best_score):
                    best = candidate
                    best_score = candidate_score

    elapsed_ms = max(1, int((time.perf_counter() - start_time) * 1000))
    return best, elapsed_ms


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
        stats={
            "strategy": "destroy_repair_multi_restart",
            "iterations": config.lns_iterations,
            "restarts": config.lns_restarts,
        },
    )


def _iteration_plan(total_iterations: int, restart_count: int) -> list[int]:
    total = max(1, total_iterations)
    base = total // restart_count
    remainder = total % restart_count
    return [base + (1 if index < remainder else 0) for index in range(restart_count)]


def _restart_strategy(restart_index: int) -> str:
    strategies = ("random", "expensive", "uncertain")
    return strategies[restart_index % len(strategies)]


def _select_destroyed_orders(
    instance: CanonicalInstance,
    current: list[CandidateOption],
    destroy_count: int,
    strategy: str,
    randomizer: random.Random,
    round_offset: int,
) -> set[str]:
    all_order_ids = [order.id for order in instance.orders]
    target_size = min(destroy_count, len(all_order_ids))
    if target_size <= 0:
        return set()

    if strategy == "random" or not current:
        return set(randomizer.sample(all_order_ids, k=target_size))

    scored_orders: list[tuple[float, str]] = []
    order_to_option = {}
    for option in current:
        for order_id in option.order_ids:
            order_to_option[order_id] = option

    for order_id in all_order_ids:
        option = order_to_option.get(order_id)
        if option is None:
            scored_orders.append((float("inf"), order_id))
            continue
        if strategy == "expensive":
            score = option.total_cost / max(1e-6, option.expected_completed_orders)
        else:
            score = 1.0 - option.acceptance_prob
        scored_orders.append((score, order_id))

    scored_orders.sort(key=lambda item: (-item[0], item[1]))
    anchor = round_offset % len(scored_orders)
    rotated = scored_orders[anchor:] + scored_orders[:anchor]
    return {order_id for _, order_id in rotated[:target_size]}

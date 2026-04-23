from __future__ import annotations

from autosolver.core.models import CandidateOption, CanonicalInstance, LexicographicScore, OrderDispatch, SolveResult


def build_result(
    instance: CanonicalInstance,
    solver_name: str,
    status: str,
    selected_options: list[CandidateOption],
    elapsed_ms: int,
    stats: dict[str, object] | None = None,
) -> SolveResult:
    selected_option_ids = tuple(option.id for option in selected_options)
    dispatches: list[OrderDispatch] = []
    covered_orders: set[str] = set()

    for option in selected_options:
        order_cost_share = option.total_cost / len(option.order_ids)
        order_probability = option.acceptance_prob
        for order_id in option.order_ids:
            covered_orders.add(order_id)
            dispatches.append(
                OrderDispatch(
                    order_id=order_id,
                    rider_ids=tuple(option.rider_ids),
                    accepted_probability=order_probability,
                    total_cost_share=order_cost_share,
                    option_id=option.id,
                    bundle_id=option.id if option.kind == "bundle" else None,
                )
            )

    unmatched_order_ids = tuple(sorted(order.id for order in instance.orders if order.id not in covered_orders))
    objective = LexicographicScore(
        expected_completed_orders=sum(option.expected_completed_orders for option in selected_options),
        total_cost=sum(option.total_cost for option in selected_options),
    )
    return SolveResult(
        instance_id=instance.instance_id,
        solver_name=solver_name,
        status=status,
        objective=objective,
        selected_option_ids=selected_option_ids,
        dispatches=tuple(sorted(dispatches, key=lambda item: item.order_id)),
        unmatched_order_ids=unmatched_order_ids,
        elapsed_ms=elapsed_ms,
        stats={key: value for key, value in (stats or {}).items()},
    )


def can_take_option(option: CandidateOption, covered_orders: set[str], rider_remaining: dict[str, int]) -> bool:
    if any(order_id in covered_orders for order_id in option.order_ids):
        return False
    for rider_id in option.rider_ids:
        if rider_remaining.get(rider_id, 0) <= 0:
            return False
    return True


def apply_option(option: CandidateOption, covered_orders: set[str], rider_remaining: dict[str, int]) -> None:
    covered_orders.update(option.order_ids)
    for rider_id in option.rider_ids:
        rider_remaining[rider_id] = rider_remaining.get(rider_id, 0) - 1

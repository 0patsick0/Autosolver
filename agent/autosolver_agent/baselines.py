from __future__ import annotations

import json
import time

from autosolver.core.candidates import generate_candidate_options
from autosolver.core.models import CanonicalInstance, CandidateOption, SolveConfig, SolveResult
from autosolver.solver.common import apply_option, build_result, can_take_option
from autosolver_agent.provider import LLMProvider


def solve_small_instance_with_llm(instance: CanonicalInstance, provider: LLMProvider | None, max_orders: int = 8) -> SolveResult | None:
    if provider is None or len(instance.orders) > max_orders:
        return None

    start_time = time.perf_counter()
    options = generate_candidate_options(instance, SolveConfig())
    candidate_payload = [
        {
            "id": option.id,
            "kind": option.kind,
            "order_ids": list(option.order_ids),
            "rider_ids": list(option.rider_ids),
            "expected_completed_orders": option.expected_completed_orders,
            "total_cost": option.total_cost,
        }
        for option in options
    ]
    system_prompt = "Return JSON with a selected_option_ids array. Pick a legal set of non-overlapping options."
    user_prompt = json.dumps(
        {
            "instance_id": instance.instance_id,
            "orders": [order.id for order in instance.orders],
            "riders": [rider.id for rider in instance.riders],
            "candidates": candidate_payload,
        },
        ensure_ascii=False,
    )
    try:
        response = provider.complete_json(system_prompt, user_prompt)
    except Exception:
        return None

    selected_ids = response.get("selected_option_ids")
    if not isinstance(selected_ids, list):
        return None

    selected_lookup = {str(item) for item in selected_ids}
    candidate_selection = [option for option in options if option.id in selected_lookup]
    rider_remaining = {rider.id: rider.capacity for rider in instance.riders}
    covered_orders: set[str] = set()
    selected: list[CandidateOption] = []
    for option in candidate_selection:
        if can_take_option(option, covered_orders, rider_remaining):
            apply_option(option, covered_orders, rider_remaining)
            selected.append(option)
    if not selected:
        return None
    elapsed_ms = max(1, int((time.perf_counter() - start_time) * 1000))
    return build_result(
        instance=instance,
        solver_name="llm_small_baseline",
        status="ok",
        selected_options=selected,
        elapsed_ms=elapsed_ms,
        stats={"candidate_option_count": len(options)},
    )

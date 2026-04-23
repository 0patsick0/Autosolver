from __future__ import annotations

import math
from typing import Any

from autosolver.core.models import CanonicalInstance, LexicographicScore, ValidationIssue, ValidationReport


def validate_solution_payload(instance: CanonicalInstance, payload: dict[str, Any], tolerance: float = 1e-6) -> ValidationReport:
    result = payload["result"] if payload.get("format") == "canonical-v1" and isinstance(payload.get("result"), dict) else payload
    dispatches = result.get("dispatches", [])
    issues: list[ValidationIssue] = []
    order_map = instance.order_map()
    rider_map = instance.rider_map()
    covered_orders: set[str] = set()
    option_to_riders: dict[str, tuple[str, ...]] = {}
    option_to_orders: dict[str, set[str]] = {}
    rider_usage: dict[str, int] = {rider_id: 0 for rider_id in rider_map}
    recomputed_expected = 0.0
    recomputed_cost = 0.0

    if not isinstance(dispatches, (list, tuple)):
        issues.append(_issue("dispatches.invalid_type", "dispatches must be a list or tuple."))
        dispatches = []

    for raw_dispatch in dispatches:
        if not isinstance(raw_dispatch, dict):
            issues.append(_issue("dispatch.invalid_type", "Each dispatch must be a JSON object.", context={"dispatch": str(raw_dispatch)}))
            continue

        order_id = str(raw_dispatch.get("order_id", ""))
        option_id = str(raw_dispatch.get("option_id", ""))
        raw_rider_ids = raw_dispatch.get("rider_ids", [])
        rider_ids = tuple(str(item) for item in raw_rider_ids) if isinstance(raw_rider_ids, (list, tuple)) else ()
        accepted_probability = float(raw_dispatch.get("accepted_probability", 0.0))
        total_cost_share = float(raw_dispatch.get("total_cost_share", 0.0))

        if order_id not in order_map:
            issues.append(_issue("dispatch.order_unknown", f"Unknown order_id: {order_id}", context={"order_id": order_id}))
        if order_id in covered_orders:
            issues.append(_issue("dispatch.order_duplicate", f"Order {order_id} appears more than once.", context={"order_id": order_id}))
        covered_orders.add(order_id)

        if not rider_ids:
            issues.append(_issue("dispatch.riders_missing", f"Order {order_id} has no riders.", context={"order_id": order_id}))
        if len(set(rider_ids)) != len(rider_ids):
            issues.append(_issue("dispatch.riders_duplicate", f"Order {order_id} repeats rider IDs.", context={"order_id": order_id, "rider_ids": list(rider_ids)}))
        if not instance.constraints.allow_multi_assign and len(rider_ids) > 1:
            issues.append(_issue("dispatch.multi_assign_forbidden", f"Order {order_id} uses multi-assign when the instance disallows it.", context={"order_id": order_id}))
        if len(rider_ids) > instance.constraints.max_riders_per_order:
            issues.append(_issue("dispatch.too_many_riders", f"Order {order_id} exceeds max riders per order.", context={"order_id": order_id, "rider_count": len(rider_ids)}))

        for rider_id in rider_ids:
            if rider_id not in rider_map:
                issues.append(_issue("dispatch.rider_unknown", f"Unknown rider_id: {rider_id}", context={"order_id": order_id, "rider_id": rider_id}))

        if option_id:
            option_orders = option_to_orders.setdefault(option_id, set())
            option_orders.add(order_id)
            if option_id in option_to_riders and option_to_riders[option_id] != rider_ids:
                issues.append(
                    _issue(
                        "dispatch.option_riders_mismatch",
                        f"Option {option_id} uses inconsistent rider sets across dispatches.",
                        context={"option_id": option_id},
                    )
                )
            option_to_riders.setdefault(option_id, rider_ids)

        recomputed_expected += accepted_probability
        recomputed_cost += total_cost_share

    for option_id, rider_ids in option_to_riders.items():
        for rider_id in set(rider_ids):
            rider_usage[rider_id] = rider_usage.get(rider_id, 0) + 1
        order_count = len(option_to_orders.get(option_id, set()))
        if order_count > 1 and not instance.constraints.allow_bundles:
            issues.append(_issue("dispatch.bundle_forbidden", f"Option {option_id} acts like a bundle but bundles are disabled.", context={"option_id": option_id}))

    for rider_id, usage in rider_usage.items():
        capacity = rider_map.get(rider_id).capacity if rider_id in rider_map else 0
        if usage > capacity:
            issues.append(
                _issue(
                    "rider.capacity_exceeded",
                    f"Rider {rider_id} exceeds capacity {capacity}.",
                    context={"rider_id": rider_id, "usage": usage, "capacity": capacity},
                )
            )

    unmatched_order_ids = result.get("unmatched_order_ids", [])
    if isinstance(unmatched_order_ids, (list, tuple)):
        expected_unmatched = sorted(order_id for order_id in order_map if order_id not in covered_orders)
        if sorted(str(item) for item in unmatched_order_ids) != expected_unmatched:
            issues.append(
                _issue(
                    "result.unmatched_mismatch",
                    "unmatched_order_ids does not match the orders absent from dispatches.",
                    context={"expected_unmatched": expected_unmatched},
                )
            )

    if not instance.constraints.allow_reject and len(covered_orders) != len(order_map):
        issues.append(_issue("result.reject_forbidden", "The instance disallows rejection but not every order is covered."))

    objective_payload = result.get("objective", {})
    if isinstance(objective_payload, dict):
        expected_objective = float(objective_payload.get("expected_completed_orders", recomputed_expected))
        total_cost = float(objective_payload.get("total_cost", recomputed_cost))
        if not math.isclose(expected_objective, recomputed_expected, rel_tol=tolerance, abs_tol=tolerance):
            issues.append(
                _issue(
                    "objective.expected_mismatch",
                    "Objective expected_completed_orders does not match recomputed dispatch total.",
                    context={"reported": expected_objective, "recomputed": recomputed_expected},
                )
            )
        if not math.isclose(total_cost, recomputed_cost, rel_tol=tolerance, abs_tol=tolerance):
            issues.append(
                _issue(
                    "objective.cost_mismatch",
                    "Objective total_cost does not match recomputed dispatch total.",
                    context={"reported": total_cost, "recomputed": recomputed_cost},
                )
            )

    is_valid = all(issue.severity != "error" for issue in issues)
    return ValidationReport(
        instance_id=instance.instance_id,
        is_valid=is_valid,
        issue_count=len(issues),
        covered_order_count=len(covered_orders),
        rider_usage={key: value for key, value in rider_usage.items() if value > 0},
        recomputed_objective=LexicographicScore(
            expected_completed_orders=recomputed_expected,
            total_cost=recomputed_cost,
        ),
        issues=tuple(issues),
    )


def _issue(code: str, message: str, severity: str = "error", context: dict[str, Any] | None = None) -> ValidationIssue:
    return ValidationIssue(code=code, message=message, severity=severity, context=context or {})

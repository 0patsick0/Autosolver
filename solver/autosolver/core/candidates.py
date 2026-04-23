from __future__ import annotations

import itertools
import math

from autosolver.core.models import BundleCandidate, CandidateOption, CanonicalInstance, GeoPoint, MatchScore, Order, SolveConfig
from autosolver.core.objective import aggregate_acceptance_probability


def generate_candidate_options(instance: CanonicalInstance, config: SolveConfig) -> list[CandidateOption]:
    options: list[CandidateOption] = []
    order_match_map = _matches_by_order(instance.match_scores)
    max_riders = max(1, min(config.top_k_riders_per_order, instance.constraints.max_riders_per_order))

    for order in instance.orders:
        matches = sorted(
            order_match_map.get(order.id, []),
            key=lambda item: (-item.accept_prob, item.cost_score, item.rider_id),
        )[: max_riders + 1]
        if not matches:
            continue

        subset_limit = max_riders if instance.constraints.allow_multi_assign else 1
        for subset_size in range(1, subset_limit + 1):
            for subset in itertools.combinations(matches[: max_riders], subset_size):
                rider_ids = tuple(match.rider_id for match in subset)
                acceptance_prob = aggregate_acceptance_probability([match.accept_prob for match in subset])
                total_cost = sum(match.cost_score for match in subset)
                option_id = f"order::{order.id}::{'-'.join(rider_ids)}"
                options.append(
                    CandidateOption(
                        id=option_id,
                        kind="single" if subset_size == 1 else "multi_assign",
                        order_ids=(order.id,),
                        rider_ids=rider_ids,
                        expected_completed_orders=acceptance_prob,
                        acceptance_prob=acceptance_prob,
                        total_cost=total_cost,
                        source="matches",
                        metadata={
                            "per_rider_prob": [match.accept_prob for match in subset],
                            "per_rider_cost": [match.cost_score for match in subset],
                        },
                    )
                )

    bundle_candidates = list(instance.bundle_candidates)
    if instance.constraints.allow_bundles and config.generate_bundles_if_missing and not bundle_candidates:
        bundle_candidates.extend(generate_bundle_candidates(instance, config))

    for bundle in bundle_candidates[: config.max_generated_bundles]:
        if not bundle.order_ids:
            continue
        options.append(
            CandidateOption(
                id=f"bundle::{bundle.id}",
                kind="bundle",
                order_ids=tuple(bundle.order_ids),
                rider_ids=(bundle.rider_id,),
                expected_completed_orders=bundle.accept_prob * len(bundle.order_ids),
                acceptance_prob=bundle.accept_prob,
                total_cost=bundle.cost_score,
                source="bundle",
                metadata=bundle.metadata,
            )
        )

    return deduplicate_options(options)


def deduplicate_options(options: list[CandidateOption]) -> list[CandidateOption]:
    deduped: dict[tuple[tuple[str, ...], tuple[str, ...]], CandidateOption] = {}
    for option in options:
        key = (tuple(sorted(option.order_ids)), tuple(sorted(option.rider_ids)))
        existing = deduped.get(key)
        if existing is None or (
            option.expected_completed_orders > existing.expected_completed_orders
            or (
                math.isclose(option.expected_completed_orders, existing.expected_completed_orders)
                and option.total_cost < existing.total_cost
            )
        ):
            deduped[key] = option
    return list(deduped.values())


def generate_bundle_candidates(instance: CanonicalInstance, config: SolveConfig) -> list[BundleCandidate]:
    order_map = instance.order_map()
    rider_matches = _matches_by_rider(instance.match_scores)
    candidate_limit = max(1, min(instance.constraints.generated_bundle_limit, config.max_generated_bundles))
    generated: dict[tuple[tuple[str, ...], str], BundleCandidate] = {}

    for rider in instance.riders:
        max_bundle_size = min(max(2, config.max_bundle_size), max(1, rider.capacity))
        if max_bundle_size < 2:
            continue
        matches = sorted(
            rider_matches.get(rider.id, []),
            key=lambda item: (-item.accept_prob, item.cost_score, item.order_id),
        )[: max(2, config.bundle_candidate_pool_size)]

        for bundle_size in range(2, min(len(matches), max_bundle_size) + 1):
            for subset in itertools.combinations(matches, bundle_size):
                if len({match.order_id for match in subset}) != bundle_size:
                    continue

                orders = [order_map.get(match.order_id) for match in subset]
                if any(order is None for order in orders):
                    continue

                metrics = _bundle_group_metrics(tuple(orders), config.bundle_distance_threshold)
                if metrics is None:
                    continue

                order_ids = tuple(sorted(match.order_id for match in subset))
                base_accept = min(match.accept_prob for match in subset)
                compactness_score = float(metrics["compactness_score"])
                accept_prob = _clip_probability(
                    base_accept
                    * (config.bundle_acceptance_scale ** (bundle_size - 1))
                    * (0.9 + 0.12 * compactness_score)
                )
                total_cost = max(
                    0.01,
                    sum(match.cost_score for match in subset)
                    * (config.bundle_discount_factor ** (bundle_size - 1))
                    * (1.0 - min(0.1, compactness_score * 0.08)),
                )
                bundle = BundleCandidate(
                    id=f"gen::{rider.id}::{'-'.join(order_ids)}",
                    order_ids=order_ids,
                    rider_id=rider.id,
                    accept_prob=accept_prob,
                    cost_score=total_cost,
                    metadata={
                        "generated": True,
                        "bundle_size": bundle_size,
                        "compactness_score": compactness_score,
                        "avg_pickup_distance": metrics["avg_pickup_distance"],
                        "avg_dropoff_distance": metrics["avg_dropoff_distance"],
                        "max_pickup_distance": metrics["max_pickup_distance"],
                        "max_dropoff_distance": metrics["max_dropoff_distance"],
                        "ready_spread_seconds": metrics["ready_spread_seconds"],
                    },
                )
                key = (bundle.order_ids, bundle.rider_id)
                existing = generated.get(key)
                if existing is None or _generated_bundle_sort_key(bundle) < _generated_bundle_sort_key(existing):
                    generated[key] = bundle

    return sorted(generated.values(), key=_generated_bundle_sort_key)[:candidate_limit]


def _matches_by_order(matches: tuple[MatchScore, ...]) -> dict[str, list[MatchScore]]:
    result: dict[str, list[MatchScore]] = {}
    for match in matches:
        result.setdefault(match.order_id, []).append(match)
    return result


def _matches_by_rider(matches: tuple[MatchScore, ...]) -> dict[str, list[MatchScore]]:
    result: dict[str, list[MatchScore]] = {}
    for match in matches:
        result.setdefault(match.rider_id, []).append(match)
    return result


def _get_order(orders: tuple[Order, ...], order_id: str) -> Order | None:
    for order in orders:
        if order.id == order_id:
            return order
    return None


def _orders_are_bundle_friendly(first: Order, second: Order, distance_threshold: float) -> bool:
    pickup_distance = _distance(first.pickup, second.pickup)
    dropoff_distance = _distance(first.dropoff, second.dropoff)
    return pickup_distance <= distance_threshold and dropoff_distance <= distance_threshold


def _bundle_group_metrics(orders: tuple[Order | None, ...], distance_threshold: float) -> dict[str, float] | None:
    resolved_orders = [order for order in orders if order is not None]
    if len(resolved_orders) < 2:
        return None

    pickup_distances: list[float] = []
    dropoff_distances: list[float] = []
    for first, second in itertools.combinations(resolved_orders, 2):
        pickup_distance = _distance(first.pickup, second.pickup)
        dropoff_distance = _distance(first.dropoff, second.dropoff)
        if not math.isfinite(pickup_distance) or not math.isfinite(dropoff_distance):
            return None
        pickup_distances.append(pickup_distance)
        dropoff_distances.append(dropoff_distance)

    if not pickup_distances or not dropoff_distances:
        return None

    size = len(resolved_orders)
    allowed_distance = distance_threshold * (1.0 + 0.35 * max(0, size - 2))
    max_pickup_distance = max(pickup_distances)
    max_dropoff_distance = max(dropoff_distances)
    if max_pickup_distance > allowed_distance or max_dropoff_distance > allowed_distance:
        return None

    ready_times = [order.ready_ts for order in resolved_orders if order.ready_ts is not None]
    ready_spread_seconds = max(ready_times) - min(ready_times) if len(ready_times) == len(resolved_orders) and ready_times else 0
    if ready_spread_seconds > 900 * max(1, size - 1):
        return None

    avg_pickup_distance = sum(pickup_distances) / len(pickup_distances)
    avg_dropoff_distance = sum(dropoff_distances) / len(dropoff_distances)
    compactness_score = min(
        1.0,
        max(
            0.25,
            (1.0 / (1.0 + avg_pickup_distance + avg_dropoff_distance + ready_spread_seconds / 1800.0)) * (1.6 + 0.2 * size),
        ),
    )

    return {
        "avg_pickup_distance": avg_pickup_distance,
        "avg_dropoff_distance": avg_dropoff_distance,
        "max_pickup_distance": max_pickup_distance,
        "max_dropoff_distance": max_dropoff_distance,
        "ready_spread_seconds": float(ready_spread_seconds),
        "compactness_score": compactness_score,
    }


def _generated_bundle_sort_key(bundle: BundleCandidate) -> tuple[float, float, float, str]:
    compactness_score = float(bundle.metadata.get("compactness_score", 0.0))
    expected_value = bundle.accept_prob * len(bundle.order_ids)
    cost_per_order = bundle.cost_score / max(1, len(bundle.order_ids))
    return (-expected_value, cost_per_order, -compactness_score, bundle.id)


def _clip_probability(value: float) -> float:
    return min(0.99, max(0.01, value))


def _distance(first: GeoPoint | None, second: GeoPoint | None) -> float:
    if first is None or second is None:
        return float("inf")
    return math.hypot(first.lat - second.lat, first.lng - second.lng)

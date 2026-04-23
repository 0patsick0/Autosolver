from __future__ import annotations

import math
from random import Random

from autosolver.core.models import BusinessConstraints, BundleCandidate, CanonicalInstance, GeoPoint, MatchScore, Order, Rider


def generate_synthetic_instance(
    instance_id: str,
    order_count: int,
    rider_count: int,
    seed: int,
    include_bundle_candidates: bool = True,
) -> CanonicalInstance:
    randomizer = Random(seed)
    cluster_count = max(2, min(5, order_count // 10 + 1))
    clusters = [GeoPoint(lat=randomizer.uniform(0, 12), lng=randomizer.uniform(0, 12)) for _ in range(cluster_count)]

    orders = tuple(_generate_order(randomizer, clusters, index) for index in range(order_count))
    riders = tuple(_generate_rider(randomizer, clusters, index) for index in range(rider_count))
    match_scores = tuple(_generate_match_scores(randomizer, orders, riders))
    bundle_candidates = tuple(_generate_bundle_candidates(randomizer, orders, riders, match_scores)) if include_bundle_candidates else ()

    return CanonicalInstance(
        instance_id=instance_id,
        orders=orders,
        riders=riders,
        match_scores=match_scores,
        bundle_candidates=bundle_candidates,
        constraints=BusinessConstraints(
            allow_reject=True,
            allow_multi_assign=True,
            allow_bundles=True,
            max_riders_per_order=3,
            generated_bundle_limit=128,
        ),
        metadata={"generated": True, "seed": seed},
    )


def generate_synthetic_benchmark(
    benchmark_id: str,
    instance_count: int,
    order_count: int,
    rider_count: int,
    seed: int,
) -> list[CanonicalInstance]:
    return [
        generate_synthetic_instance(
            instance_id=f"{benchmark_id}-{index + 1:03d}",
            order_count=order_count,
            rider_count=rider_count,
            seed=seed + index,
            include_bundle_candidates=True,
        )
        for index in range(instance_count)
    ]


def _generate_order(randomizer: Random, clusters: list[GeoPoint], index: int) -> Order:
    cluster = randomizer.choice(clusters)
    pickup = GeoPoint(
        lat=cluster.lat + randomizer.uniform(-0.45, 0.45),
        lng=cluster.lng + randomizer.uniform(-0.45, 0.45),
    )
    dropoff = GeoPoint(
        lat=cluster.lat + randomizer.uniform(0.4, 1.4),
        lng=cluster.lng + randomizer.uniform(0.4, 1.4),
    )
    ready_ts = index * 60
    due_ts = ready_ts + randomizer.randint(900, 2100)
    return Order(
        id=f"o{index + 1}",
        pickup=pickup,
        dropoff=dropoff,
        ready_ts=ready_ts,
        due_ts=due_ts,
        attributes={"cluster_hint": f"c{clusters.index(cluster) + 1}"},
    )


def _generate_rider(randomizer: Random, clusters: list[GeoPoint], index: int) -> Rider:
    cluster = randomizer.choice(clusters)
    location = GeoPoint(
        lat=cluster.lat + randomizer.uniform(-1.0, 1.0),
        lng=cluster.lng + randomizer.uniform(-1.0, 1.0),
    )
    return Rider(
        id=f"r{index + 1}",
        capacity=randomizer.randint(1, 3),
        location=location,
        attributes={"cluster_hint": f"c{clusters.index(cluster) + 1}"},
    )


def _generate_match_scores(randomizer: Random, orders: tuple[Order, ...], riders: tuple[Rider, ...]) -> list[MatchScore]:
    scores: list[MatchScore] = []
    for order in orders:
        for rider in riders:
            rider_location = rider.location or order.pickup
            pickup_distance = _distance(rider_location, order.pickup)
            trip_distance = _distance(order.pickup, order.dropoff)
            blended_distance = pickup_distance * 0.8 + trip_distance
            accept_prob = max(0.05, min(0.97, 0.98 - blended_distance * 0.08 + randomizer.uniform(-0.06, 0.06)))
            cost_score = max(1.0, 2.0 + blended_distance * 1.6 + randomizer.uniform(-0.4, 0.8))
            scores.append(
                MatchScore(
                    order_id=order.id,
                    rider_id=rider.id,
                    accept_prob=round(accept_prob, 4),
                    cost_score=round(cost_score, 4),
                    metadata={"pickup_distance": round(pickup_distance, 4), "trip_distance": round(trip_distance, 4)},
                )
            )
    return scores


def _generate_bundle_candidates(
    randomizer: Random,
    orders: tuple[Order, ...],
    riders: tuple[Rider, ...],
    match_scores: tuple[MatchScore, ...] | list[MatchScore],
) -> list[BundleCandidate]:
    del riders
    order_map = {order.id: order for order in orders}
    bundle_candidates: list[BundleCandidate] = []
    per_rider: dict[str, list[MatchScore]] = {}
    for match in match_scores:
        per_rider.setdefault(match.rider_id, []).append(match)

    for rider_id, matches in per_rider.items():
        ranked = sorted(matches, key=lambda item: (-item.accept_prob, item.cost_score))[:6]
        for first_index in range(len(ranked)):
            for second_index in range(first_index + 1, len(ranked)):
                first = ranked[first_index]
                second = ranked[second_index]
                first_order = order_map[first.order_id]
                second_order = order_map[second.order_id]
                pickup_distance = _distance(first_order.pickup, second_order.pickup)
                dropoff_distance = _distance(first_order.dropoff, second_order.dropoff)
                if pickup_distance > 1.4 or dropoff_distance > 1.8:
                    continue
                accept_prob = max(0.05, min(first.accept_prob, second.accept_prob) * 0.95)
                cost_score = (first.cost_score + second.cost_score) * randomizer.uniform(0.82, 0.92)
                bundle_candidates.append(
                    BundleCandidate(
                        id=f"b::{rider_id}::{first.order_id}+{second.order_id}",
                        order_ids=(first.order_id, second.order_id),
                        rider_id=rider_id,
                        accept_prob=round(accept_prob, 4),
                        cost_score=round(cost_score, 4),
                        metadata={
                            "generated": True,
                            "pickup_distance": round(pickup_distance, 4),
                            "dropoff_distance": round(dropoff_distance, 4),
                        },
                    )
                )
                if len(bundle_candidates) >= max(4, len(orders) // 2):
                    return bundle_candidates
    return bundle_candidates


def _distance(first: GeoPoint | None, second: GeoPoint | None) -> float:
    if first is None or second is None:
        return 0.0
    return math.hypot(first.lat - second.lat, first.lng - second.lng)

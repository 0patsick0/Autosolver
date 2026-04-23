from __future__ import annotations

import json
from pathlib import Path

from autosolver.core.models import BusinessConstraints, BundleCandidate, CanonicalInstance, GeoPoint, MatchScore, Order, Rider


class CanonicalInstanceAdapter:
    def load(self, path: str | Path) -> CanonicalInstance:
        raise NotImplementedError


class CanonicalJsonAdapter(CanonicalInstanceAdapter):
    def load(self, path: str | Path) -> CanonicalInstance:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        orders = tuple(
            Order(
                id=item["id"],
                pickup=_point_from_raw(item.get("pickup")),
                dropoff=_point_from_raw(item.get("dropoff")),
                ready_ts=item.get("ready_ts"),
                due_ts=item.get("due_ts"),
                attributes=item.get("attributes", {}),
            )
            for item in raw.get("orders", [])
        )
        riders = tuple(
            Rider(
                id=item["id"],
                capacity=item.get("capacity", 1),
                location=_point_from_raw(item.get("location")),
                attributes=item.get("attributes", {}),
            )
            for item in raw.get("riders", [])
        )
        match_scores = tuple(
            MatchScore(
                order_id=item["order_id"],
                rider_id=item["rider_id"],
                accept_prob=float(item["accept_prob"]),
                cost_score=float(item["cost_score"]),
                metadata=item.get("metadata", {}),
            )
            for item in raw.get("match_scores", [])
        )
        bundle_candidates = tuple(
            BundleCandidate(
                id=item["id"],
                order_ids=tuple(item["order_ids"]),
                rider_id=item["rider_id"],
                accept_prob=float(item["accept_prob"]),
                cost_score=float(item["cost_score"]),
                metadata=item.get("metadata", {}),
            )
            for item in raw.get("bundle_candidates", [])
        )
        constraints_raw = raw.get("constraints", {})
        constraints = BusinessConstraints(
            allow_reject=constraints_raw.get("allow_reject", True),
            allow_multi_assign=constraints_raw.get("allow_multi_assign", True),
            allow_bundles=constraints_raw.get("allow_bundles", True),
            max_riders_per_order=constraints_raw.get("max_riders_per_order", 3),
            generated_bundle_limit=constraints_raw.get("generated_bundle_limit", 128),
        )
        return CanonicalInstance(
            instance_id=raw["instance_id"],
            orders=orders,
            riders=riders,
            match_scores=match_scores,
            bundle_candidates=bundle_candidates,
            constraints=constraints,
            metadata=raw.get("metadata", {}),
        )


def _point_from_raw(raw: dict[str, float] | None) -> GeoPoint | None:
    if raw is None:
        return None
    return GeoPoint(lat=float(raw["lat"]), lng=float(raw["lng"]))

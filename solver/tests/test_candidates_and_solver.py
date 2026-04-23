from __future__ import annotations

import itertools

from autosolver.core.candidates import generate_candidate_options
from autosolver.core.models import BusinessConstraints, CanonicalInstance, GeoPoint, LexicographicScore, MatchScore, Order, Rider, SolveConfig
from autosolver.core.objective import better_score
from autosolver.solver.portfolio import PortfolioSolver


class TestCandidatesAndSolver:
    def test_bundle_candidates_are_generated_for_nearby_orders(self, sample_instance):
        config = SolveConfig(max_generated_bundles=10)
        options = generate_candidate_options(sample_instance, config)
        bundle_options = [option for option in options if option.kind == "bundle"]
        assert bundle_options
        assert any(set(option.order_ids) == {"o1", "o2"} for option in bundle_options)

    def test_triple_bundle_candidates_can_be_generated_when_capacity_and_proximity_allow(self):
        instance = CanonicalInstance(
            instance_id="triple-bundle-demo",
            orders=(
                Order(id="o1", pickup=GeoPoint(0.0, 0.0), dropoff=GeoPoint(1.0, 1.0), ready_ts=0),
                Order(id="o2", pickup=GeoPoint(0.2, 0.1), dropoff=GeoPoint(1.1, 1.0), ready_ts=120),
                Order(id="o3", pickup=GeoPoint(0.3, 0.2), dropoff=GeoPoint(1.2, 1.1), ready_ts=240),
            ),
            riders=(Rider(id="r1", capacity=3, location=GeoPoint(0.1, 0.1)),),
            match_scores=(
                MatchScore(order_id="o1", rider_id="r1", accept_prob=0.82, cost_score=5.0),
                MatchScore(order_id="o2", rider_id="r1", accept_prob=0.79, cost_score=5.1),
                MatchScore(order_id="o3", rider_id="r1", accept_prob=0.76, cost_score=5.2),
            ),
            constraints=BusinessConstraints(allow_bundles=True, generated_bundle_limit=12),
        )
        options = generate_candidate_options(
            instance,
            SolveConfig(
                generate_bundles_if_missing=True,
                max_generated_bundles=12,
                bundle_candidate_pool_size=6,
                max_bundle_size=3,
                bundle_distance_threshold=1.2,
            ),
        )

        triple_bundles = [option for option in options if option.kind == "bundle" and len(option.order_ids) == 3]

        assert triple_bundles
        assert triple_bundles[0].expected_completed_orders > 0
        assert triple_bundles[0].metadata["bundle_size"] == 3

    def test_solver_returns_legal_solution_under_tight_budget(self, sample_instance):
        result = PortfolioSolver().solve(sample_instance, time_budget_ms=5, seed=7, config=SolveConfig(time_budget_ms=5))
        order_ids = [dispatch.order_id for dispatch in result.dispatches]
        assert result.elapsed_ms >= 1
        assert len(order_ids) == len(set(order_ids))

    def test_solver_stats_include_candidate_breakdown(self, sample_instance):
        result = PortfolioSolver().solve(sample_instance, time_budget_ms=200, seed=3, config=SolveConfig(time_budget_ms=200, max_generated_bundles=8))

        assert result.stats["candidate_option_count"] >= 1
        assert result.stats["candidate_option_breakdown"]["single"] >= 1
        assert "bundle" in result.stats["candidate_option_breakdown"]
        assert "selected_option_breakdown" in result.stats

    def test_cpsat_matches_bruteforce_on_small_instance(self, sample_instance):
        config = SolveConfig(time_budget_ms=2_000, top_k_riders_per_order=2, max_generated_bundles=4, lns_iterations=4)
        options = generate_candidate_options(sample_instance, config)
        brute_force_score = self._bruteforce_best_score(sample_instance, options)
        result = PortfolioSolver().solve(sample_instance, time_budget_ms=2_000, seed=11, config=config)
        assert not better_score(brute_force_score, result.objective)

    def _bruteforce_best_score(self, sample_instance, options):
        best = LexicographicScore(expected_completed_orders=0.0, total_cost=float("inf"))
        riders = {rider.id: rider.capacity for rider in sample_instance.riders}
        for subset_size in range(len(options) + 1):
            for subset in itertools.combinations(options, subset_size):
                covered_orders: set[str] = set()
                rider_usage = {key: 0 for key in riders}
                feasible = True
                for option in subset:
                    if any(order_id in covered_orders for order_id in option.order_ids):
                        feasible = False
                        break
                    for rider_id in option.rider_ids:
                        rider_usage[rider_id] += 1
                        if rider_usage[rider_id] > riders[rider_id]:
                            feasible = False
                            break
                    covered_orders.update(option.order_ids)
                    if not feasible:
                        break
                if not feasible:
                    continue
                score = LexicographicScore(
                    expected_completed_orders=sum(option.expected_completed_orders for option in subset),
                    total_cost=sum(option.total_cost for option in subset),
                )
                if better_score(score, best):
                    best = score
        return best

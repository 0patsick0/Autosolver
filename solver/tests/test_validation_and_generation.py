from __future__ import annotations

from pathlib import Path

from autosolver.core.models import dataclass_to_dict
from autosolver.eval.validation import validate_solution_payload
from autosolver.io.json_io import load_instance
from autosolver.samples.generator import generate_synthetic_benchmark, generate_synthetic_instance
from autosolver.solver.portfolio import PortfolioSolver


class TestValidationAndGeneration:
    def test_generated_instance_has_orders_riders_and_matches(self):
        instance = generate_synthetic_instance("gen-1", order_count=12, rider_count=5, seed=9)
        assert len(instance.orders) == 12
        assert len(instance.riders) == 5
        assert len(instance.match_scores) == 60

    def test_generated_benchmark_returns_multiple_instances(self):
        instances = generate_synthetic_benchmark("bench", instance_count=3, order_count=10, rider_count=4, seed=3)
        assert len(instances) == 3
        assert instances[0].instance_id == "bench-001"

    def test_validation_accepts_solver_output(self, sample_instance):
        result = PortfolioSolver().solve(sample_instance, time_budget_ms=300, seed=2)
        report = validate_solution_payload(sample_instance, dataclass_to_dict(result))
        assert report.is_valid

    def test_validation_detects_duplicate_order(self):
        instance = load_instance(Path("examples/instances/sample_instance.json"))
        payload = {
            "dispatches": [
                {
                    "order_id": "o1",
                    "rider_ids": ["r1"],
                    "accepted_probability": 0.6,
                    "total_cost_share": 5.0,
                    "option_id": "x1",
                },
                {
                    "order_id": "o1",
                    "rider_ids": ["r2"],
                    "accepted_probability": 0.4,
                    "total_cost_share": 4.0,
                    "option_id": "x2",
                },
            ],
            "objective": {"expected_completed_orders": 1.0, "total_cost": 9.0},
            "unmatched_order_ids": ["o2", "o3"],
        }
        report = validate_solution_payload(instance, payload)
        assert not report.is_valid
        assert any(issue.code == "dispatch.order_duplicate" for issue in report.issues)

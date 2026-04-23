from __future__ import annotations

import argparse
import json
from pathlib import Path

from autosolver.cli import _solve_submit_command, _solve_validate_command
from autosolver.core.candidates import generate_bundle_candidates
from autosolver.core.models import CanonicalInstance, GeoPoint, MatchScore, Order, Rider, SolveConfig, dataclass_to_dict
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

    def test_validation_recomputes_probability_and_flags_mismatch(self):
        instance = load_instance(Path("examples/instances/sample_instance.json"))
        payload = {
            "dispatches": [
                {
                    "order_id": "o1",
                    "rider_ids": ["r1"],
                    "accepted_probability": 0.01,
                    "total_cost_share": 2.0,
                    "option_id": "order::o1::r1",
                }
            ],
            "objective": {"expected_completed_orders": 0.01, "total_cost": 2.0},
            "unmatched_order_ids": ["o2", "o3"],
            "stats": {"capacity_consumption_mode": "dispatch"},
        }
        report = validate_solution_payload(instance, payload)
        assert not report.is_valid
        assert any(issue.code == "objective.accepted_probability_mismatch" for issue in report.issues)
        assert any(issue.code == "objective.expected_mismatch" for issue in report.issues)

    def test_validation_can_recompute_generated_bundle_with_stats(self):
        instance = CanonicalInstance(
            instance_id="generated-bundle-validation",
            orders=(
                Order(id="o1", pickup=GeoPoint(0.0, 0.0), dropoff=GeoPoint(1.0, 1.0), ready_ts=0),
                Order(id="o2", pickup=GeoPoint(0.1, 0.1), dropoff=GeoPoint(1.1, 1.1), ready_ts=120),
            ),
            riders=(Rider(id="r1", capacity=2),),
            match_scores=(
                MatchScore(order_id="o1", rider_id="r1", accept_prob=0.8, cost_score=5.0),
                MatchScore(order_id="o2", rider_id="r1", accept_prob=0.75, cost_score=5.2),
            ),
        )
        config = SolveConfig(
            generate_bundles_if_missing=True,
            max_generated_bundles=16,
            bundle_candidate_pool_size=6,
            max_bundle_size=3,
            bundle_distance_threshold=2.5,
            bundle_discount_factor=0.92,
            bundle_acceptance_scale=0.95,
        )
        generated_bundle = generate_bundle_candidates(instance, config)[0]
        payload = {
            "dispatches": [
                {
                    "order_id": order_id,
                    "rider_ids": [generated_bundle.rider_id],
                    "accepted_probability": generated_bundle.accept_prob,
                    "total_cost_share": generated_bundle.cost_score / len(generated_bundle.order_ids),
                    "option_id": f"bundle::{generated_bundle.id}",
                    "bundle_id": f"bundle::{generated_bundle.id}",
                }
                for order_id in generated_bundle.order_ids
            ],
            "objective": {
                "expected_completed_orders": generated_bundle.accept_prob * len(generated_bundle.order_ids),
                "total_cost": generated_bundle.cost_score,
            },
            "unmatched_order_ids": [],
            "stats": {
                "capacity_consumption_mode": "dispatch",
                "generate_bundles_if_missing": True,
                "max_generated_bundles": config.max_generated_bundles,
                "bundle_candidate_pool_size": config.bundle_candidate_pool_size,
                "max_bundle_size": config.max_bundle_size,
                "bundle_distance_threshold": config.bundle_distance_threshold,
                "bundle_discount_factor": config.bundle_discount_factor,
                "bundle_acceptance_scale": config.bundle_acceptance_scale,
            },
        }
        report = validate_solution_payload(instance, payload)
        assert report.is_valid

    def test_solve_validate_command_writes_valid_report(self, tmp_path: Path):
        solve_output = tmp_path / "solve_result.json"
        submission_output = tmp_path / "submission.json"
        validation_output = tmp_path / "validation_report.json"
        events_output = tmp_path / "solve_validate.jsonl"

        _solve_validate_command(
            argparse.Namespace(
                instance="examples/instances/sample_instance.json",
                output=str(solve_output),
                submission_output=str(submission_output),
                validation_output=str(validation_output),
                events=str(events_output),
                time_budget_ms=400,
                seed=3,
                top_k=None,
                config_source=None,
            )
        )

        validation = json.loads(validation_output.read_text(encoding="utf-8"))
        assert solve_output.exists()
        assert submission_output.exists()
        assert events_output.exists()
        assert validation["is_valid"] is True

    def test_solve_submit_command_writes_submission_snapshot(self, tmp_path: Path):
        output_dir = tmp_path / "submit_bundle"
        _solve_submit_command(
            argparse.Namespace(
                instance="examples/instances/sample_instance.json",
                output_dir=str(output_dir),
                events=None,
                time_budget_ms=400,
                seed=3,
                top_k=None,
                config_source=None,
            )
        )

        snapshot = json.loads((output_dir / "submission_snapshot.json").read_text(encoding="utf-8"))
        assert (output_dir / "solve_result.json").exists()
        assert (output_dir / "submission.json").exists()
        assert (output_dir / "validation_report.json").exists()
        assert snapshot["workflow"] == "solve-submit"

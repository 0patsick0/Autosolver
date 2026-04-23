from __future__ import annotations

from pathlib import Path

from autosolver.core.models import BenchmarkCase, BenchmarkCaseMetric, BenchmarkSummary, CanonicalInstance, SolveConfig
from autosolver.eval.manifest import load_benchmark_cases
from autosolver.io.events import EventWriter
from autosolver.io.json_io import load_instance
from autosolver.solver.portfolio import PortfolioSolver


def benchmark_instances(
    cases: list[BenchmarkCase],
    solver: PortfolioSolver,
    config: SolveConfig,
    seed: int,
    benchmark_id: str,
    event_writer: EventWriter | None = None,
    metadata: dict[str, object] | None = None,
) -> BenchmarkSummary:
    case_metrics: list[BenchmarkCaseMetric] = []
    total_elapsed_ms = 0
    weighted_expected = 0.0
    weighted_cost = 0.0
    total_weight = 0.0

    for case in cases:
        result = solver.solve(case.instance, time_budget_ms=config.time_budget_ms, seed=seed, config=config)
        case_metric = BenchmarkCaseMetric(
            instance_id=case.instance.instance_id,
            expected_completed_orders=result.objective.expected_completed_orders,
            total_cost=result.objective.total_cost,
            elapsed_ms=result.elapsed_ms,
            solver_name=result.solver_name,
            status=result.status,
            case_id=case.case_id,
            source_path=case.source_path,
            weight=case.weight,
            seed=seed,
        )
        case_metrics.append(case_metric)
        total_elapsed_ms += result.elapsed_ms
        weighted_expected += case.weight * case_metric.expected_completed_orders
        weighted_cost += case.weight * case_metric.total_cost
        total_weight += case.weight
        if event_writer is not None:
            event_writer.write(
                "benchmark.case_completed",
                {
                    "benchmark_id": benchmark_id,
                    "case_id": case.case_id,
                    "instance_id": case.instance.instance_id,
                    "source_path": case.source_path,
                    "expected_completed_orders": case_metric.expected_completed_orders,
                    "total_cost": case_metric.total_cost,
                    "elapsed_ms": case_metric.elapsed_ms,
                    "solver_name": case_metric.solver_name,
                    "status": case_metric.status,
                    "weight": case.weight,
                    "stats": result.stats,
                },
            )

    average_expected = weighted_expected / max(1.0, total_weight)
    average_cost = weighted_cost / max(1.0, total_weight)
    summary = BenchmarkSummary(
        benchmark_id=benchmark_id,
        case_metrics=tuple(case_metrics),
        average_expected_completed_orders=average_expected,
        average_total_cost=average_cost,
        total_elapsed_ms=total_elapsed_ms,
        total_weight=total_weight,
        metadata=metadata or {},
    )
    if event_writer is not None:
        event_writer.write(
            "benchmark.completed",
            {
                "benchmark_id": benchmark_id,
                "case_count": len(case_metrics),
                "average_expected_completed_orders": average_expected,
                "average_total_cost": average_cost,
                "total_elapsed_ms": total_elapsed_ms,
                "total_weight": total_weight,
            },
        )
    return summary


def load_benchmark_directory(benchmark_path: str | Path) -> list[CanonicalInstance]:
    del benchmark_path
    raise RuntimeError("load_benchmark_directory is deprecated. Use load_benchmark_cases instead.")

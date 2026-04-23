from __future__ import annotations

import argparse
from pathlib import Path

from autosolver.core.models import SolveConfig, sanitize_json_value
from autosolver.eval.benchmark import benchmark_instances
from autosolver.eval.manifest import load_benchmark_cases
from autosolver.eval.validation import validate_solution_payload
from autosolver.io.events import EventWriter, read_events
from autosolver.io.json_io import (
    load_incumbent_solve_config,
    load_instance,
    load_result_payload,
    write_benchmark_summary,
    write_json,
    write_solve_result,
)
from autosolver.io.replay import build_replay_payload
from autosolver.io.submission import CanonicalSubmissionWriter
from autosolver.samples.generator import generate_synthetic_benchmark, generate_synthetic_instance
from autosolver.solver.portfolio import PortfolioSolver
from autosolver_agent.baselines import solve_small_instance_with_llm
from autosolver_agent.provider import OpenAICompatibleProvider
from autosolver_agent.research import ResearchRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autosolver", description="AutoSolver CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    solve_parser = subparsers.add_parser("solve", help="Solve a single canonical instance")
    solve_parser.add_argument("instance", type=str)
    solve_parser.add_argument("--output", type=str, default="solve_result.json")
    solve_parser.add_argument("--submission-output", type=str, default=None)
    solve_parser.add_argument("--events", type=str, default=None)
    solve_parser.add_argument("--time-budget-ms", type=int, default=None)
    solve_parser.add_argument("--seed", type=int, default=0)
    solve_parser.add_argument("--top-k", type=int, default=None)
    solve_parser.add_argument("--compare-small-llm", action="store_true")
    solve_parser.add_argument("--config-source", type=str, default=None, help="Research summary/state JSON containing an incumbent solver_config")

    benchmark_parser = subparsers.add_parser("benchmark", help="Benchmark a directory, single instance, or manifest JSON")
    benchmark_parser.add_argument("benchmark_path", type=str)
    benchmark_parser.add_argument("--output", type=str, default="benchmark_summary.json")
    benchmark_parser.add_argument("--events", type=str, default=None)
    benchmark_parser.add_argument("--time-budget-ms", type=int, default=10_000)
    benchmark_parser.add_argument("--seed", type=int, default=0)

    research_parser = subparsers.add_parser("research", help="Run a controlled experiment loop")
    research_parser.add_argument("benchmark_path", type=str)
    research_parser.add_argument("--output", type=str, default="research_summary.json")
    research_parser.add_argument("--events", type=str, default="examples/events/research.jsonl")
    research_parser.add_argument("--rounds", type=int, default=4)
    research_parser.add_argument("--time-budget-ms", type=int, default=10_000)
    research_parser.add_argument("--seed", type=int, default=0)
    research_parser.add_argument("--model", type=str, default=None)
    research_parser.add_argument("--base-url", type=str, default=None)
    research_parser.add_argument("--api-key", type=str, default=None)
    research_parser.add_argument("--state", type=str, default=None)
    research_parser.add_argument("--resume", action="store_true")
    research_parser.add_argument("--search-space", type=str, default=None)
    research_parser.add_argument("--dashboard-output", type=str, default="dashboard/public/replay-data.json")
    research_parser.add_argument("--allow-rule-based-fallback", action="store_true")

    replay_parser = subparsers.add_parser("replay", help="Convert JSONL events into dashboard-friendly JSON")
    replay_parser.add_argument("events", type=str)
    replay_parser.add_argument("--output", type=str, default="dashboard/public/replay-data.json")

    smoke_parser = subparsers.add_parser("smoke", help="Run an end-to-end synthetic smoke test")
    smoke_parser.add_argument("output", type=str)
    smoke_parser.add_argument("--instances", type=int, default=3)
    smoke_parser.add_argument("--orders", type=int, default=24)
    smoke_parser.add_argument("--riders", type=int, default=10)
    smoke_parser.add_argument("--rounds", type=int, default=1)
    smoke_parser.add_argument("--time-budget-ms", type=int, default=10_000)
    smoke_parser.add_argument("--seed", type=int, default=0)
    smoke_parser.add_argument("--dashboard-output", type=str, default="dashboard/public/replay-data.json")
    smoke_parser.add_argument("--allow-rule-based-fallback", action="store_true")

    validate_parser = subparsers.add_parser("validate", help="Validate a solve result or submission against an instance")
    validate_parser.add_argument("instance", type=str)
    validate_parser.add_argument("result", type=str)
    validate_parser.add_argument("--output", type=str, default="validation_report.json")

    generate_parser = subparsers.add_parser("generate", help="Generate synthetic canonical instances for local experiments")
    generate_parser.add_argument("output", type=str)
    generate_parser.add_argument("--instances", type=int, default=1)
    generate_parser.add_argument("--orders", type=int, default=24)
    generate_parser.add_argument("--riders", type=int, default=10)
    generate_parser.add_argument("--seed", type=int, default=0)
    generate_parser.add_argument("--benchmark-id", type=str, default="synthetic-benchmark")

    return parser


def solve(instance, time_budget_ms: int = 10_000, seed: int = 0, config: SolveConfig | None = None):
    solver = PortfolioSolver()
    effective_config = config or SolveConfig(time_budget_ms=time_budget_ms)
    return solver.solve(instance, time_budget_ms=time_budget_ms, seed=seed, config=effective_config)


def _solve_command(args: argparse.Namespace) -> None:
    instance = load_instance(args.instance)
    if args.config_source:
        config = load_incumbent_solve_config(args.config_source)
        if args.time_budget_ms is not None:
            config = SolveConfig(**{**config.__dict__, "time_budget_ms": args.time_budget_ms})
        if args.top_k is not None:
            config = SolveConfig(**{**config.__dict__, "top_k_riders_per_order": args.top_k})
    else:
        config = SolveConfig(
            time_budget_ms=args.time_budget_ms if args.time_budget_ms is not None else 10_000,
            top_k_riders_per_order=args.top_k if args.top_k is not None else 3,
        )
    event_writer = EventWriter(args.events) if args.events else None
    if event_writer is not None:
        event_writer.write(
            "solve.started",
            {
                "instance_id": instance.instance_id,
                "time_budget_ms": config.time_budget_ms,
                "config_source": args.config_source,
            },
        )

    result = solve(instance, time_budget_ms=config.time_budget_ms, seed=args.seed, config=config)
    write_solve_result(args.output, result)
    if args.submission_output:
        CanonicalSubmissionWriter().write(result, args.submission_output)

    if args.compare_small_llm:
        provider = OpenAICompatibleProvider.from_environment()
        llm_result = solve_small_instance_with_llm(instance, provider)
        if llm_result is not None:
            write_solve_result(Path(args.output).with_name("solve_result_llm.json"), llm_result)

    if event_writer is not None:
        event_writer.write(
            "solve.completed",
            {
                "instance_id": result.instance_id,
                "solver_name": result.solver_name,
                "expected_completed_orders": result.objective.expected_completed_orders,
                "total_cost": result.objective.total_cost,
                "elapsed_ms": result.elapsed_ms,
                "status": result.status,
                "config_source": args.config_source,
            },
        )


def _benchmark_command(args: argparse.Namespace) -> None:
    benchmark_id, cases, metadata = load_benchmark_cases(args.benchmark_path)
    event_writer = EventWriter(args.events) if args.events else None
    config = SolveConfig(time_budget_ms=args.time_budget_ms)
    summary = benchmark_instances(
        cases=cases,
        solver=PortfolioSolver(),
        config=config,
        seed=args.seed,
        benchmark_id=benchmark_id,
        event_writer=event_writer,
        metadata=metadata,
    )
    write_benchmark_summary(args.output, summary)


def _research_command(args: argparse.Namespace) -> None:
    provider = OpenAICompatibleProvider(
        base_url=args.base_url or OpenAICompatibleProvider.default_base_url(),
        api_key=args.api_key or OpenAICompatibleProvider.default_api_key(),
        model=args.model or OpenAICompatibleProvider.default_model(),
    )
    runner = ResearchRunner(provider=provider)
    summary = runner.run(
        benchmark_path=args.benchmark_path,
        rounds=args.rounds,
        output_path=args.output,
        events_path=args.events,
        time_budget_ms=args.time_budget_ms,
        seed=args.seed,
        state_path=args.state,
        resume=args.resume,
        search_space_path=args.search_space,
        dashboard_output_path=args.dashboard_output,
        allow_rule_based_fallback=args.allow_rule_based_fallback,
    )
    write_json(args.output, summary)


def _replay_command(args: argparse.Namespace) -> None:
    events = read_events(args.events)
    write_json(args.output, build_replay_payload(events))


def _smoke_command(args: argparse.Namespace) -> None:
    output_root = Path(args.output)
    benchmark_dir = output_root / "benchmark"
    benchmark_dir.mkdir(parents=True, exist_ok=True)

    instances = generate_synthetic_benchmark(
        benchmark_id="smoke-benchmark",
        instance_count=args.instances,
        order_count=args.orders,
        rider_count=args.riders,
        seed=args.seed,
    )
    manifest_cases: list[dict[str, object]] = []
    for instance in instances:
        filename = f"{instance.instance_id}.json"
        write_json(benchmark_dir / filename, instance)
        manifest_cases.append({"case_id": instance.instance_id, "instance": filename})

    manifest_path = benchmark_dir / "benchmark_manifest.json"
    write_json(
        manifest_path,
        {
            "benchmark_id": "smoke-benchmark",
            "cases": manifest_cases,
            "metadata": {"generated": True, "purpose": "end-to-end smoke test", "seed": args.seed},
        },
    )

    first_instance_path = benchmark_dir / manifest_cases[0]["instance"]
    solve_output = output_root / "solve_result.json"
    validation_output = output_root / "validation_report.json"
    benchmark_output = output_root / "benchmark_summary.json"
    research_output = output_root / "research_summary.json"
    replay_output = output_root / "replay-data.json"
    tuned_solve_output = output_root / "solve_result_from_research.json"
    tuned_validation_output = output_root / "validation_from_research.json"
    events_path = output_root / "events" / "research.jsonl"

    instance = load_instance(first_instance_path)
    solve_result = solve(instance, time_budget_ms=args.time_budget_ms, seed=args.seed)
    write_solve_result(solve_output, solve_result)
    validation_report = validate_solution_payload(
        instance,
        {"format": "canonical-v1", "result": sanitize_json_value(solve_result)},
    )
    write_json(validation_output, validation_report)

    benchmark_id, cases, metadata = load_benchmark_cases(manifest_path)
    benchmark_summary = benchmark_instances(
        cases=cases,
        solver=PortfolioSolver(),
        config=SolveConfig(time_budget_ms=args.time_budget_ms),
        seed=args.seed,
        benchmark_id=benchmark_id,
        metadata=metadata,
    )
    write_benchmark_summary(benchmark_output, benchmark_summary)

    provider = OpenAICompatibleProvider.from_environment()
    runner = ResearchRunner(provider=provider)
    research_summary = runner.run(
        benchmark_path=str(manifest_path),
        rounds=args.rounds,
        output_path=str(research_output),
        events_path=str(events_path),
        time_budget_ms=args.time_budget_ms,
        seed=args.seed,
        dashboard_output_path=args.dashboard_output,
        allow_rule_based_fallback=args.allow_rule_based_fallback,
    )
    write_json(research_output, research_summary)

    tuned_result = solve(
        instance,
        time_budget_ms=args.time_budget_ms,
        seed=args.seed,
        config=load_incumbent_solve_config(research_output),
    )
    write_solve_result(tuned_solve_output, tuned_result)
    tuned_validation_report = validate_solution_payload(
        instance,
        {"format": "canonical-v1", "result": sanitize_json_value(tuned_result)},
    )
    write_json(tuned_validation_output, tuned_validation_report)

    replay_payload = build_replay_payload(read_events(events_path))
    write_json(replay_output, replay_payload)

    write_json(
        output_root / "smoke_summary.json",
        {
            "output_root": str(output_root),
            "llm_enabled": research_summary["agent"]["llm_enabled"],
            "allow_rule_based_fallback": args.allow_rule_based_fallback,
            "artifacts": {
                "manifest": str(manifest_path),
                "solve_result": str(solve_output),
                "validation_report": str(validation_output),
                "benchmark_summary": str(benchmark_output),
                "research_summary": str(research_output),
                "research_events": str(events_path),
                "replay_data": str(replay_output),
                "dashboard_replay_data": args.dashboard_output,
                "tuned_solve_result": str(tuned_solve_output),
                "tuned_validation_report": str(tuned_validation_output),
            },
            "validation": validation_report,
            "deployment": {
                "baseline": {
                    "expected_completed_orders": solve_result.objective.expected_completed_orders,
                    "total_cost": solve_result.objective.total_cost,
                    "elapsed_ms": solve_result.elapsed_ms,
                },
                "tuned": {
                    "expected_completed_orders": tuned_result.objective.expected_completed_orders,
                    "total_cost": tuned_result.objective.total_cost,
                    "elapsed_ms": tuned_result.elapsed_ms,
                },
                "delta": {
                    "expected_completed_orders": tuned_result.objective.expected_completed_orders - solve_result.objective.expected_completed_orders,
                    "total_cost": tuned_result.objective.total_cost - solve_result.objective.total_cost,
                    "elapsed_ms": tuned_result.elapsed_ms - solve_result.elapsed_ms,
                },
                "tuned_validation": tuned_validation_report,
            },
            "benchmark": {
                "benchmark_id": benchmark_summary.benchmark_id,
                "average_expected_completed_orders": benchmark_summary.average_expected_completed_orders,
                "average_total_cost": benchmark_summary.average_total_cost,
            },
            "research": {
                "benchmark_id": research_summary["benchmark_id"],
                "provider": research_summary["agent"]["provider"],
                "lesson_count": research_summary["agent"]["lesson_count"],
            },
        },
    )


def _validate_command(args: argparse.Namespace) -> None:
    instance = load_instance(args.instance)
    payload = load_result_payload(args.result)
    report = validate_solution_payload(instance, payload)
    write_json(args.output, report)


def _generate_command(args: argparse.Namespace) -> None:
    output_path = Path(args.output)
    if args.instances <= 1 and output_path.suffix == ".json":
        instance = generate_synthetic_instance(
            instance_id=output_path.stem,
            order_count=args.orders,
            rider_count=args.riders,
            seed=args.seed,
            include_bundle_candidates=True,
        )
        write_json(output_path, instance)
        return

    instances = generate_synthetic_benchmark(
        benchmark_id=args.benchmark_id,
        instance_count=args.instances,
        order_count=args.orders,
        rider_count=args.riders,
        seed=args.seed,
    )
    output_path.mkdir(parents=True, exist_ok=True)
    manifest_cases: list[dict[str, object]] = []
    for index, instance in enumerate(instances):
        filename = f"{instance.instance_id}.json"
        write_json(output_path / filename, instance)
        manifest_cases.append({"case_id": instance.instance_id, "instance": filename})
    write_json(
        output_path / "benchmark_manifest.json",
        {
            "benchmark_id": args.benchmark_id,
            "cases": manifest_cases,
            "metadata": {"generated": True, "seed": args.seed},
        },
    )


def _build_replay_payload(events: list[object]) -> dict[str, object]:
    grouped_rounds: list[dict[str, object]] = []
    round_insights_by_experiment: dict[str, dict[str, object]] = {}
    case_aggregates: dict[str, dict[str, object]] = {}
    agent = {
        "provider": "none",
        "llmEnabled": False,
        "fallbackAllowed": False,
        "benchmarkId": None,
        "sessionStartedAt": None,
        "proposalBreakdown": {"llm": 0, "fallback": 0},
    }
    current_round: dict[str, object] | None = None
    current_experiment_id: str | None = None

    for event in events:
        payload = _event_payload(event)
        if event.type == "research.session_started":
            agent = {
                "provider": payload.get("provider", "none"),
                "llmEnabled": bool(payload.get("llm_enabled", False)),
                "fallbackAllowed": bool(payload.get("fallback_allowed", False)),
                "benchmarkId": payload.get("benchmark_id"),
                "sessionStartedAt": event.ts,
                "proposalBreakdown": {"llm": 0, "fallback": 0},
            }

        if event.type in {"research.llm_proposal", "research.fallback_proposal"}:
            experiment_id = str(payload.get("experiment_id", "unknown-exp"))
            proposal_type = "llm" if event.type == "research.llm_proposal" else "fallback"
            proposal_breakdown = agent["proposalBreakdown"]
            proposal_breakdown[proposal_type] = int(proposal_breakdown.get(proposal_type, 0)) + 1
            insight = round_insights_by_experiment.setdefault(
                experiment_id,
                {
                    "experimentId": experiment_id,
                    "hypothesis": str(payload.get("hypothesis", "")),
                    "status": "pending",
                    "proposalType": proposal_type,
                    "averageExpectedCompletedOrders": None,
                    "averageTotalCost": None,
                    "totalElapsedMs": None,
                    "reflectionSummary": None,
                    "keepReason": None,
                    "risks": [],
                    "nextFocus": [],
                    "avoidPatterns": [],
                    "solverConfig": payload.get("solver_config"),
                    "caseMetrics": [],
                },
            )
            insight["proposalType"] = proposal_type
            insight["solverConfig"] = payload.get("solver_config")
            if not insight.get("hypothesis"):
                insight["hypothesis"] = str(payload.get("hypothesis", ""))

        if event.type == "research.round_started":
            current_experiment_id = str(payload.get("experiment_id", "unknown-exp"))
            current_round = {
                "experiment_id": current_experiment_id,
                "hypothesis": payload.get("hypothesis"),
                "events": [],
            }
            grouped_rounds.append(current_round)
            insight = round_insights_by_experiment.setdefault(
                current_experiment_id,
                {
                    "experimentId": current_experiment_id,
                    "hypothesis": str(payload.get("hypothesis", "")),
                    "status": "pending",
                    "proposalType": "unknown",
                    "averageExpectedCompletedOrders": None,
                    "averageTotalCost": None,
                    "totalElapsedMs": None,
                    "reflectionSummary": None,
                    "keepReason": None,
                    "risks": [],
                    "nextFocus": [],
                    "avoidPatterns": [],
                    "solverConfig": payload.get("solver_config"),
                    "caseMetrics": [],
                },
            )
            insight["hypothesis"] = str(payload.get("hypothesis", insight["hypothesis"]))
            if insight.get("solverConfig") is None:
                insight["solverConfig"] = payload.get("solver_config")
        if current_round is not None:
            current_round["events"].append({"ts": event.ts, "type": event.type, "payload": payload})

        if event.type == "benchmark.case_completed":
            case_key = str(payload.get("case_id", payload.get("instance_id", "unknown-case")))
            aggregate = case_aggregates.setdefault(
                case_key,
                {
                    "caseId": payload.get("case_id"),
                    "instanceId": payload.get("instance_id"),
                    "sourcePath": payload.get("source_path"),
                    "runs": 0,
                    "expectedCompletedOrdersTotal": 0.0,
                    "totalCostTotal": 0.0,
                    "elapsedMsTotal": 0,
                    "candidateOptionCountTotal": 0,
                    "bundleOptionCountTotal": 0,
                    "lastSolverName": payload.get("solver_name"),
                    "lastStatus": payload.get("status"),
                },
            )
            aggregate["runs"] = int(aggregate["runs"]) + 1
            aggregate["expectedCompletedOrdersTotal"] = float(aggregate["expectedCompletedOrdersTotal"]) + float(
                payload.get("expected_completed_orders", 0.0)
            )
            aggregate["totalCostTotal"] = float(aggregate["totalCostTotal"]) + float(payload.get("total_cost", 0.0))
            aggregate["elapsedMsTotal"] = int(aggregate["elapsedMsTotal"]) + int(payload.get("elapsed_ms", 0))
            stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
            candidate_breakdown = stats.get("candidate_option_breakdown") if isinstance(stats.get("candidate_option_breakdown"), dict) else {}
            aggregate["candidateOptionCountTotal"] = int(aggregate["candidateOptionCountTotal"]) + int(stats.get("candidate_option_count", 0))
            aggregate["bundleOptionCountTotal"] = int(aggregate["bundleOptionCountTotal"]) + int(candidate_breakdown.get("bundle", 0))
            aggregate["lastSolverName"] = payload.get("solver_name")
            aggregate["lastStatus"] = payload.get("status")

            if current_experiment_id is not None and current_experiment_id in round_insights_by_experiment:
                round_insights_by_experiment[current_experiment_id]["caseMetrics"].append(
                    {
                        "caseId": payload.get("case_id"),
                        "instanceId": payload.get("instance_id"),
                        "expectedCompletedOrders": payload.get("expected_completed_orders"),
                        "totalCost": payload.get("total_cost"),
                        "elapsedMs": payload.get("elapsed_ms"),
                        "solverName": payload.get("solver_name"),
                        "status": payload.get("status"),
                        "weight": payload.get("weight"),
                        "candidateOptionCount": stats.get("candidate_option_count"),
                        "candidateOptionBreakdown": candidate_breakdown,
                    }
                )

        if event.type == "research.round_completed":
            experiment_id = str(payload.get("experiment_id", current_experiment_id or "unknown-exp"))
            insight = round_insights_by_experiment.setdefault(
                experiment_id,
                {
                    "experimentId": experiment_id,
                    "hypothesis": "",
                    "status": "pending",
                    "proposalType": "unknown",
                    "averageExpectedCompletedOrders": None,
                    "averageTotalCost": None,
                    "totalElapsedMs": None,
                    "reflectionSummary": None,
                    "keepReason": None,
                    "risks": [],
                    "nextFocus": [],
                    "avoidPatterns": [],
                    "solverConfig": None,
                    "caseMetrics": [],
                },
            )
            insight["status"] = str(payload.get("status", "unknown"))
            insight["averageExpectedCompletedOrders"] = payload.get("average_expected_completed_orders")
            insight["averageTotalCost"] = payload.get("average_total_cost")
            insight["totalElapsedMs"] = payload.get("total_elapsed_ms")

        if event.type == "research.llm_reflection":
            experiment_id = str(payload.get("experiment_id", current_experiment_id or "unknown-exp"))
            insight = round_insights_by_experiment.setdefault(
                experiment_id,
                {
                    "experimentId": experiment_id,
                    "hypothesis": "",
                    "status": "pending",
                    "proposalType": "unknown",
                    "averageExpectedCompletedOrders": None,
                    "averageTotalCost": None,
                    "totalElapsedMs": None,
                    "reflectionSummary": None,
                    "keepReason": None,
                    "risks": [],
                    "nextFocus": [],
                    "avoidPatterns": [],
                    "solverConfig": None,
                    "caseMetrics": [],
                },
            )
            insight["reflectionSummary"] = payload.get("summary")
            insight["keepReason"] = payload.get("keep_reason")
            insight["risks"] = payload.get("risks", [])
            insight["nextFocus"] = payload.get("next_focus", [])
            insight["avoidPatterns"] = payload.get("avoid_patterns", [])

    chart_points = [
        {
            "ts": event.ts,
            "expectedCompletedOrders": _event_payload(event).get("average_expected_completed_orders"),
            "totalCost": _event_payload(event).get("average_total_cost"),
            "type": event.type,
        }
        for event in events
        if event.type in {"research.round_completed", "research.incumbent_updated"}
    ]

    round_insights = [round_insights_by_experiment[round["experiment_id"]] for round in grouped_rounds if round["experiment_id"] in round_insights_by_experiment]
    for insight in round_insights:
        case_metrics = [metric for metric in insight.get("caseMetrics", []) if isinstance(metric, dict)]
        if not case_metrics:
            continue
        candidate_counts = [int(metric.get("candidateOptionCount", 0)) for metric in case_metrics]
        bundle_counts = [
            int(metric.get("candidateOptionBreakdown", {}).get("bundle", 0))
            for metric in case_metrics
            if isinstance(metric.get("candidateOptionBreakdown"), dict)
        ]
        insight["averageCandidateOptionCount"] = sum(candidate_counts) / max(1, len(candidate_counts))
        insight["averageBundleOptionCount"] = sum(bundle_counts) / max(1, len(bundle_counts)) if bundle_counts else 0.0

    case_leaderboard = [
        {
            "caseId": aggregate["caseId"],
            "instanceId": aggregate["instanceId"],
            "sourcePath": aggregate["sourcePath"],
            "runs": aggregate["runs"],
            "averageExpectedCompletedOrders": float(aggregate["expectedCompletedOrdersTotal"]) / max(1, int(aggregate["runs"])),
            "averageTotalCost": float(aggregate["totalCostTotal"]) / max(1, int(aggregate["runs"])),
            "averageElapsedMs": int(aggregate["elapsedMsTotal"]) / max(1, int(aggregate["runs"])),
            "averageCandidateOptionCount": int(aggregate["candidateOptionCountTotal"]) / max(1, int(aggregate["runs"])),
            "averageBundleOptionCount": int(aggregate["bundleOptionCountTotal"]) / max(1, int(aggregate["runs"])),
            "lastSolverName": aggregate["lastSolverName"],
            "lastStatus": aggregate["lastStatus"],
        }
        for aggregate in case_aggregates.values()
    ]
    case_leaderboard.sort(key=lambda item: (item["averageExpectedCompletedOrders"], -item["averageTotalCost"], item["caseId"] or ""))

    summary = _summarize_replay(events, grouped_rounds, chart_points)
    return {
        "events": [event.__dict__ for event in events],
        "rounds": grouped_rounds,
        "roundInsights": round_insights,
        "chartPoints": chart_points,
        "caseLeaderboard": case_leaderboard,
        "agent": agent,
        "summary": summary,
    }


def _summarize_replay(events: list[object], rounds: list[dict[str, object]], chart_points: list[dict[str, object]]) -> dict[str, object]:
    keep_count = sum(1 for event in events if getattr(event, "payload", {}).get("status") == "keep")
    discard_count = sum(1 for event in events if getattr(event, "payload", {}).get("status") == "discard")
    failure_count = sum(1 for event in events if getattr(event, "type", "") == "research.round_failed")
    benchmark_id = None
    latest_incumbent_experiment_id = None
    best_expected_completed_orders = None
    best_total_cost = None

    for event in events:
        payload = getattr(event, "payload", {})
        if benchmark_id is None and isinstance(payload, dict) and payload.get("benchmark_id") is not None:
            benchmark_id = payload.get("benchmark_id")
        if getattr(event, "type", "") == "research.incumbent_updated":
            latest_incumbent_experiment_id = payload.get("experiment_id")
            best_expected_completed_orders = payload.get("average_expected_completed_orders")
            best_total_cost = payload.get("average_total_cost")

    if best_expected_completed_orders is None and chart_points:
        latest = chart_points[-1]
        best_expected_completed_orders = latest.get("expectedCompletedOrders")
        best_total_cost = latest.get("totalCost")

    return {
        "benchmarkId": benchmark_id,
        "roundCount": len(rounds),
        "eventCount": len(events),
        "keepCount": keep_count,
        "discardCount": discard_count,
        "failureCount": failure_count,
        "latestIncumbentExperimentId": latest_incumbent_experiment_id,
        "bestExpectedCompletedOrders": best_expected_completed_orders,
        "bestTotalCost": best_total_cost,
    }


def _event_payload(event: object) -> dict[str, object]:
    payload = getattr(event, "payload", {})
    return payload if isinstance(payload, dict) else {}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    command_handlers = {
        "solve": _solve_command,
        "benchmark": _benchmark_command,
        "research": _research_command,
        "replay": _replay_command,
        "smoke": _smoke_command,
        "validate": _validate_command,
        "generate": _generate_command,
    }
    command_handlers[args.command](args)

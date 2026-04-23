from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autosolver.core.models import Event
from autosolver.io.json_io import write_json


def load_events_from_path(path: str | Path) -> list[Event]:
    source = Path(path)
    if not source.exists():
        return []

    events: list[Event] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        events.append(Event(ts=raw["ts"], type=raw["type"], payload=raw["payload"]))
    return events


def write_replay_payload_from_event_path(events_path: str | Path, output_path: str | Path) -> dict[str, object]:
    payload = build_replay_payload(load_events_from_path(events_path))
    write_json(output_path, payload)
    return payload


def build_replay_payload(events: list[Event]) -> dict[str, object]:
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
                    "averageCandidateOptionCount": None,
                    "averageBundleOptionCount": None,
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
                    "averageCandidateOptionCount": None,
                    "averageBundleOptionCount": None,
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
                    "averageCandidateOptionCount": None,
                    "averageBundleOptionCount": None,
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
                    "averageCandidateOptionCount": None,
                    "averageBundleOptionCount": None,
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

    summary = summarize_replay(events, grouped_rounds, chart_points)
    return {
        "events": [event.__dict__ for event in events],
        "rounds": grouped_rounds,
        "roundInsights": round_insights,
        "chartPoints": chart_points,
        "caseLeaderboard": case_leaderboard,
        "agent": agent,
        "summary": summary,
    }


def summarize_replay(events: list[Event], rounds: list[dict[str, object]], chart_points: list[dict[str, object]]) -> dict[str, object]:
    keep_count = sum(1 for event in events if _event_payload(event).get("status") == "keep")
    discard_count = sum(1 for event in events if _event_payload(event).get("status") == "discard")
    failure_count = sum(1 for event in events if event.type == "research.round_failed")
    benchmark_id = None
    latest_incumbent_experiment_id = None
    best_expected_completed_orders = None
    best_total_cost = None

    for event in events:
        payload = _event_payload(event)
        if benchmark_id is None and payload.get("benchmark_id") is not None:
            benchmark_id = payload.get("benchmark_id")
        if event.type == "research.incumbent_updated":
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


def _event_payload(event: Event) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}

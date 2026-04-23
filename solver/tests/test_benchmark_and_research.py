from __future__ import annotations

import argparse
import json
from pathlib import Path

from autosolver.cli import _smoke_command
from autosolver.core.models import BenchmarkSummary, ExperimentRecord, SolveConfig
from autosolver.eval.benchmark import benchmark_instances
from autosolver.eval.manifest import load_benchmark_cases
from autosolver.io.events import EventWriter, read_events
from autosolver.solver.portfolio import PortfolioSolver
from autosolver_agent.provider import LLMProvider
from autosolver_agent.research import (
    LLMExperimentProposer,
    ResearchMemory,
    ResearchRunner,
    RuleBasedProposer,
    _extract_parameter_hints,
    _heuristic_reflection,
    _judge,
    _parameter_value_insights,
    _summarize_benchmark_cases,
)


class FakeProvider(LLMProvider):
    def is_configured(self) -> bool:
        return True

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, object]:
        if "critic loop" in system_prompt:
            return {
                "summary": "The round improved coverage but still leaves cost pressure on the table.",
                "keep_reason": "It is a useful incumbent when coverage improves.",
                "risks": ["Bundle search may still be shallow."],
                "next_focus": ["Try a wider bundle pool.", "Toggle CP-SAT only on promising configs."],
                "avoid_patterns": ["Do not repeat identical top_k with identical bundle settings."],
            }
        del user_prompt
        return {
            "experiment_id": "llm-exp-1",
            "name": "llm-exp-1",
            "hypothesis": "Try top_k=2 with bundles enabled.",
            "top_k_riders_per_order": 2,
            "use_cpsat": True,
            "generate_bundles_if_missing": True,
            "bundle_distance_threshold": 2.5,
            "max_generated_bundles": 16,
            "lns_destroy_fraction": 0.2,
            "lns_iterations": 8,
        }


class DuplicateProposalProvider(LLMProvider):
    def is_configured(self) -> bool:
        return True

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, object]:
        del system_prompt, user_prompt
        return {
            "experiment_id": "llm-exp-dup",
            "name": "llm-exp-dup",
            "hypothesis": "Repeat a known weak config for testing.",
            "solver_config": {
                "top_k_riders_per_order": 1,
                "use_cpsat": False,
                "generate_bundles_if_missing": False,
                "bundle_candidate_pool_size": 4,
                "max_bundle_size": 2,
                "bundle_distance_threshold": 2.0,
                "bundle_discount_factor": 0.88,
                "bundle_acceptance_scale": 0.88,
                "max_generated_bundles": 16,
                "lns_destroy_fraction": 0.15,
                "lns_iterations": 8,
            },
        }


class TestBenchmarkAndResearch:
    def test_benchmark_directory_produces_summary(self):
        benchmark_id, cases, metadata = load_benchmark_cases("examples/benchmarks")
        summary = benchmark_instances(
            cases=cases,
            solver=PortfolioSolver(),
            config=SolveConfig(time_budget_ms=500),
            seed=3,
            benchmark_id=benchmark_id,
            metadata=metadata,
        )
        assert isinstance(summary, BenchmarkSummary)
        assert len(summary.case_metrics) == 2
        assert summary.average_expected_completed_orders > 0

    def test_benchmark_manifest_supports_repeat_and_weight(self):
        benchmark_id, cases, metadata = load_benchmark_cases("examples/benchmarks/benchmark_manifest.json")
        summary = benchmark_instances(
            cases=cases,
            solver=PortfolioSolver(),
            config=SolveConfig(time_budget_ms=500),
            seed=4,
            benchmark_id=benchmark_id,
            metadata=metadata,
        )
        assert summary.benchmark_id == "demo-benchmark-manifest"
        assert len(summary.case_metrics) == 3
        assert summary.total_weight > 0

    def test_research_runner_writes_history_and_events(self, tmp_path: Path):
        output_path = tmp_path / "research.json"
        events_path = tmp_path / "research.jsonl"
        state_path = tmp_path / "research.state.json"
        runner = ResearchRunner(provider=FakeProvider(base_url="https://example.com", api_key="demo", model="test-model"))
        summary = runner.run(
            benchmark_path="examples/benchmarks/benchmark_manifest.json",
            rounds=2,
            output_path=str(output_path),
            events_path=str(events_path),
            time_budget_ms=400,
            seed=5,
            state_path=str(state_path),
            resume=False,
            search_space_path="examples/research_search_space.json",
        )
        loaded = json.loads(output_path.read_text(encoding="utf-8"))
        events = read_events(events_path)
        assert summary["history"]
        assert loaded["history"]
        assert summary["benchmark_profile"]["case_count"] == 3
        assert summary["agent"]["llm_enabled"] is True
        assert summary["lessons"]
        assert state_path.exists()
        assert any(event.type == "research.round_completed" for event in events)
        assert any(event.type == "research.llm_proposal" for event in events)
        assert any(event.type == "research.llm_reflection" for event in events)

    def test_research_runner_resume_reuses_state(self, tmp_path: Path):
        output_path = tmp_path / "resume.json"
        events_path = tmp_path / "resume.jsonl"
        state_path = tmp_path / "resume.state.json"
        runner = ResearchRunner(provider=FakeProvider(base_url="https://example.com", api_key="demo", model="test-model"))
        runner.run(
            benchmark_path="examples/benchmarks/benchmark_manifest.json",
            rounds=1,
            output_path=str(output_path),
            events_path=str(events_path),
            time_budget_ms=350,
            seed=7,
            state_path=str(state_path),
            resume=False,
            search_space_path="examples/research_search_space.json",
        )
        resumed = runner.run(
            benchmark_path="examples/benchmarks/benchmark_manifest.json",
            rounds=1,
            output_path=str(output_path),
            events_path=str(events_path),
            time_budget_ms=350,
            seed=7,
            state_path=str(state_path),
            resume=True,
            search_space_path="examples/research_search_space.json",
        )
        assert len(resumed["history"]) >= 2

    def test_research_runner_requires_llm_by_default(self, tmp_path: Path):
        runner = ResearchRunner(provider=None)
        try:
            runner.run(
                benchmark_path="examples/benchmarks/benchmark_manifest.json",
                rounds=1,
                output_path=str(tmp_path / "no-llm.json"),
                events_path=str(tmp_path / "no-llm.jsonl"),
                time_budget_ms=300,
                seed=1,
            )
        except RuntimeError as exc:
            assert "LLM-first" in str(exc)
        else:
            raise AssertionError("Expected research mode to require an LLM provider by default.")

    def test_research_runner_allows_explicit_rule_based_fallback(self, tmp_path: Path):
        runner = ResearchRunner(provider=None)
        summary = runner.run(
            benchmark_path="examples/benchmarks/benchmark_manifest.json",
            rounds=1,
            output_path=str(tmp_path / "offline.json"),
            events_path=str(tmp_path / "offline.jsonl"),
            time_budget_ms=300,
            seed=2,
            allow_rule_based_fallback=True,
        )
        assert summary["agent"]["llm_enabled"] is False
        assert summary["lessons"]

    def test_extract_parameter_hints_from_hypothesis_dict_string(self):
        hints = _extract_parameter_hints(
            {
                "hypothesis": "{'top_k_riders_per_order': 2, 'max_bundle_size': 2, 'bundle_distance_threshold': 2.2, 'use_cpsat': True}",
            }
        )

        assert hints["top_k_riders_per_order"] == 2
        assert hints["max_bundle_size"] == 2
        assert hints["bundle_distance_threshold"] == 2.2
        assert hints["use_cpsat"] is True

    def test_extract_parameter_hints_from_embedded_config_text(self):
        hints = _extract_parameter_hints(
            {
                "notes": "candidate config => {'bundle_candidate_pool_size': 8, 'generate_bundles_if_missing': False}",
                "summary": 'Try {"lns_iterations": 16, "bundle_discount_factor": 0.96} next.',
            }
        )

        assert hints["bundle_candidate_pool_size"] == 8
        assert hints["generate_bundles_if_missing"] is False
        assert hints["lns_iterations"] == 16
        assert hints["bundle_discount_factor"] == 0.96

    def test_rule_based_proposer_mutates_incumbent_with_benchmark_profile(self):
        incumbent_config = SolveConfig(
            time_budget_ms=400,
            top_k_riders_per_order=1,
            use_cpsat=True,
            use_lns=True,
            generate_bundles_if_missing=False,
            max_generated_bundles=16,
            bundle_candidate_pool_size=4,
            max_bundle_size=2,
            bundle_distance_threshold=2.0,
            bundle_discount_factor=0.88,
            bundle_acceptance_scale=0.88,
            lns_destroy_fraction=0.15,
            lns_iterations=8,
        )
        incumbent_record = ExperimentRecord(
            experiment_id="incumbent-1",
            status="keep",
            hypothesis="baseline",
            benchmark_summary=BenchmarkSummary(
                benchmark_id="demo-benchmark-manifest",
                case_metrics=(),
                average_expected_completed_orders=2.0,
                average_total_cost=20.0,
                total_elapsed_ms=20,
            ),
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:00:01+00:00",
            notes="seed incumbent",
            solver_config=incumbent_config,
            config_signature='{"seed":"incumbent"}',
        )
        memory = ResearchMemory(
            seen_signatures={json.dumps(incumbent_config.__dict__, sort_keys=True)},
            history=[incumbent_record],
            lessons=[
                {
                    "summary": "Bundle generation was too conservative.",
                    "next_focus": ["Increase bundle candidate pool size", "Turn bundle generation back on"],
                    "risks": ["Bundle pool is too small."],
                    "avoid_patterns": [],
                }
            ],
        )
        proposer = RuleBasedProposer(
            seed=13,
            search_space=json.loads(json.dumps({"top_k_riders_per_order": [1, 2, 3], "use_cpsat": [True, False], "generate_bundles_if_missing": [True, False], "bundle_candidate_pool_size": [4, 6, 8], "max_bundle_size": [2, 3], "bundle_distance_threshold": [2.0, 2.5, 3.0], "bundle_discount_factor": [0.88, 0.92, 0.96], "bundle_acceptance_scale": [0.88, 0.93, 0.97], "max_generated_bundles": [16, 32, 64], "lns_destroy_fraction": [0.15, 0.2, 0.25, 0.3], "lns_iterations": [8, 12, 16, 24] })),
            benchmark_profile={
                "case_count": 4,
                "avg_orders": 42.0,
                "avg_riders": 12.0,
                "orders_per_rider": 3.5,
                "avg_bundle_candidates": 0.0,
                "avg_match_density": 0.65,
                "weighted_cases": [],
            },
        )

        proposal = proposer.propose(memory, "demo-benchmark-manifest", 400, 0)

        assert proposal.solver_config.generate_bundles_if_missing is True
        assert proposal.solver_config.top_k_riders_per_order >= 2
        assert proposal.solver_config.max_generated_bundles >= 32
        assert json.dumps(proposal.solver_config.__dict__, sort_keys=True) not in memory.seen_signatures

    def test_summarize_benchmark_cases_reports_density(self):
        benchmark_id, cases, metadata = load_benchmark_cases("examples/benchmarks/benchmark_manifest.json")
        del benchmark_id, metadata

        profile = _summarize_benchmark_cases(cases)

        assert profile["case_count"] == 3
        assert profile["avg_orders"] > 0
        assert profile["avg_riders"] > 0
        assert profile["orders_per_rider"] > 0
        assert isinstance(profile["weighted_cases"], list)

    def test_parameter_value_insights_rank_kept_values_first(self):
        keep_config = SolveConfig(time_budget_ms=400, top_k_riders_per_order=2, use_cpsat=True, generate_bundles_if_missing=True)
        discard_config = SolveConfig(time_budget_ms=400, top_k_riders_per_order=1, use_cpsat=False, generate_bundles_if_missing=False)
        history = [
            ExperimentRecord(
                experiment_id="keep-1",
                status="keep",
                hypothesis="good",
                benchmark_summary=BenchmarkSummary(
                    benchmark_id="demo",
                    case_metrics=(),
                    average_expected_completed_orders=3.0,
                    average_total_cost=10.0,
                    total_elapsed_ms=10,
                ),
                started_at="2026-01-01T00:00:00+00:00",
                finished_at="2026-01-01T00:00:01+00:00",
                solver_config=keep_config,
            ),
            ExperimentRecord(
                experiment_id="discard-1",
                status="discard",
                hypothesis="bad",
                benchmark_summary=BenchmarkSummary(
                    benchmark_id="demo",
                    case_metrics=(),
                    average_expected_completed_orders=2.0,
                    average_total_cost=12.0,
                    total_elapsed_ms=12,
                ),
                started_at="2026-01-01T00:00:02+00:00",
                finished_at="2026-01-01T00:00:03+00:00",
                solver_config=discard_config,
            ),
        ]

        insights = _parameter_value_insights(history, {"top_k_riders_per_order": [1, 2, 3], "use_cpsat": [True, False]})

        assert insights["top_k_riders_per_order"][0]["value"] == 2
        assert insights["use_cpsat"][0]["value"] is True

    def test_heuristic_reflection_explains_regression_against_incumbent(self):
        record = ExperimentRecord(
            experiment_id="exp-2",
            status="discard",
            hypothesis="regressed",
            benchmark_summary=BenchmarkSummary(
                benchmark_id="demo",
                case_metrics=(),
                average_expected_completed_orders=2.5,
                average_total_cost=11.0,
                total_elapsed_ms=15,
            ),
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:00:01+00:00",
            solver_config=SolveConfig(time_budget_ms=400),
        )
        incumbent_summary = BenchmarkSummary(
            benchmark_id="demo",
            case_metrics=(),
            average_expected_completed_orders=2.8,
            average_total_cost=10.5,
            total_elapsed_ms=10,
        )

        reflection = _heuristic_reflection(incumbent_summary, record)

        assert "没有超过 incumbent" in reflection["summary"]
        assert reflection["next_focus"]

    def test_judge_keeps_tied_objective_when_candidate_is_faster(self):
        incumbent = BenchmarkSummary(
            benchmark_id="demo",
            case_metrics=(),
            average_expected_completed_orders=3.0,
            average_total_cost=12.0,
            total_elapsed_ms=50,
        )
        candidate = BenchmarkSummary(
            benchmark_id="demo",
            case_metrics=(),
            average_expected_completed_orders=3.0,
            average_total_cost=12.0,
            total_elapsed_ms=35,
        )

        assert _judge(candidate, incumbent) == "keep"

    def test_llm_proposer_repairs_redundant_known_bad_config(self):
        weak_config = SolveConfig(
            time_budget_ms=400,
            top_k_riders_per_order=1,
            use_cpsat=False,
            use_lns=True,
            generate_bundles_if_missing=False,
            max_generated_bundles=16,
            bundle_candidate_pool_size=4,
            max_bundle_size=2,
            bundle_distance_threshold=2.0,
            bundle_discount_factor=0.88,
            bundle_acceptance_scale=0.88,
            lns_destroy_fraction=0.15,
            lns_iterations=8,
        )
        strong_config = SolveConfig(
            time_budget_ms=400,
            top_k_riders_per_order=2,
            use_cpsat=True,
            use_lns=True,
            generate_bundles_if_missing=True,
            max_generated_bundles=32,
            bundle_candidate_pool_size=6,
            max_bundle_size=3,
            bundle_distance_threshold=2.5,
            bundle_discount_factor=0.92,
            bundle_acceptance_scale=0.93,
            lns_destroy_fraction=0.2,
            lns_iterations=12,
        )
        history = [
            ExperimentRecord(
                experiment_id="keep-1",
                status="keep",
                hypothesis="strong",
                benchmark_summary=BenchmarkSummary(
                    benchmark_id="demo",
                    case_metrics=(),
                    average_expected_completed_orders=3.0,
                    average_total_cost=10.0,
                    total_elapsed_ms=20,
                ),
                started_at="2026-01-01T00:00:00+00:00",
                finished_at="2026-01-01T00:00:01+00:00",
                solver_config=strong_config,
                config_signature=json.dumps(strong_config.__dict__, sort_keys=True),
            ),
            ExperimentRecord(
                experiment_id="discard-1",
                status="discard",
                hypothesis="weak",
                benchmark_summary=BenchmarkSummary(
                    benchmark_id="demo",
                    case_metrics=(),
                    average_expected_completed_orders=2.0,
                    average_total_cost=12.0,
                    total_elapsed_ms=22,
                ),
                started_at="2026-01-01T00:00:02+00:00",
                finished_at="2026-01-01T00:00:03+00:00",
                solver_config=weak_config,
                config_signature=json.dumps(weak_config.__dict__, sort_keys=True),
            ),
            ExperimentRecord(
                experiment_id="discard-2",
                status="discard",
                hypothesis="weak-again",
                benchmark_summary=BenchmarkSummary(
                    benchmark_id="demo",
                    case_metrics=(),
                    average_expected_completed_orders=2.1,
                    average_total_cost=12.5,
                    total_elapsed_ms=24,
                ),
                started_at="2026-01-01T00:00:04+00:00",
                finished_at="2026-01-01T00:00:05+00:00",
                solver_config=weak_config,
                config_signature=json.dumps(weak_config.__dict__, sort_keys=True),
            ),
        ]
        memory = ResearchMemory(
            seen_signatures={json.dumps(weak_config.__dict__, sort_keys=True)},
            failed_signatures={json.dumps(weak_config.__dict__, sort_keys=True)},
            history=history,
        )
        proposer = LLMExperimentProposer(
            provider=DuplicateProposalProvider(base_url="https://example.com", api_key="demo", model="test-model"),
            search_space={
                "top_k_riders_per_order": [1, 2, 3],
                "use_cpsat": [True, False],
                "generate_bundles_if_missing": [True, False],
                "bundle_candidate_pool_size": [4, 6, 8],
                "max_bundle_size": [2, 3],
                "bundle_distance_threshold": [2.0, 2.5, 3.0],
                "bundle_discount_factor": [0.88, 0.92, 0.96],
                "bundle_acceptance_scale": [0.88, 0.93, 0.97],
                "max_generated_bundles": [16, 32, 64],
                "lns_destroy_fraction": [0.15, 0.2, 0.25, 0.3],
                "lns_iterations": [8, 12, 16, 24],
            },
            benchmark_profile={
                "case_count": 4,
                "avg_orders": 48.0,
                "avg_riders": 14.0,
                "orders_per_rider": 3.4,
                "avg_bundle_candidates": 0.0,
                "avg_match_density": 0.62,
                "weighted_cases": [],
            },
        )

        proposal = proposer.propose(memory, "demo", 400, 0)

        assert proposal.notes.startswith("llm-proposer:")
        assert json.dumps(proposal.solver_config.__dict__, sort_keys=True) not in memory.seen_signatures
        assert proposal.solver_config.generate_bundles_if_missing is True
        assert proposal.solver_config.top_k_riders_per_order >= 2

    def test_event_writer_can_update_dashboard_replay_payload(self, tmp_path: Path):
        events_path = tmp_path / "live.jsonl"
        replay_path = tmp_path / "replay.json"
        writer = EventWriter(events_path, replay_output_path=replay_path)

        writer.write(
            "research.session_started",
            {
                "benchmark_id": "live-benchmark",
                "llm_enabled": True,
                "provider": "deepseek-v3-1-terminus@ark.cn-beijing.volces.com",
                "fallback_allowed": False,
            },
        )
        writer.write(
            "research.llm_proposal",
            {
                "round_index": 0,
                "experiment_id": "exp-live",
                "hypothesis": "Try a larger bundle pool.",
                "solver_config": {"max_generated_bundles": 24},
            },
        )
        writer.write(
            "research.round_started",
            {
                "round_index": 0,
                "experiment_id": "exp-live",
                "hypothesis": "Try a larger bundle pool.",
                "solver_config": {"max_generated_bundles": 24},
            },
        )
        writer.write(
            "research.round_completed",
            {
                "experiment_id": "exp-live",
                "status": "keep",
                "average_expected_completed_orders": 3.2,
                "average_total_cost": 15.8,
                "total_elapsed_ms": 42,
            },
        )
        writer.write(
            "research.incumbent_updated",
            {
                "experiment_id": "exp-live",
                "average_expected_completed_orders": 3.2,
                "average_total_cost": 15.8,
            },
        )

        replay = json.loads(replay_path.read_text(encoding="utf-8"))

        assert replay["summary"]["roundCount"] == 1
        assert replay["agent"]["provider"] == "deepseek-v3-1-terminus@ark.cn-beijing.volces.com"
        assert replay["roundInsights"][0]["experimentId"] == "exp-live"

    def test_smoke_command_runs_end_to_end_with_explicit_fallback(self, tmp_path: Path, monkeypatch):
        output_dir = tmp_path / "smoke"
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_MODEL", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        _smoke_command(
            argparse.Namespace(
                output=str(output_dir),
                instances=2,
                orders=12,
                riders=6,
                rounds=1,
                time_budget_ms=300,
                seed=9,
                dashboard_output=str(output_dir / "dashboard-replay.json"),
                allow_rule_based_fallback=True,
            )
        )

        summary = json.loads((output_dir / "smoke_summary.json").read_text(encoding="utf-8"))
        validation = json.loads((output_dir / "validation_report.json").read_text(encoding="utf-8"))
        replay = json.loads((output_dir / "replay-data.json").read_text(encoding="utf-8"))

        assert summary["artifacts"]["research_summary"]
        assert summary["artifacts"]["tuned_solve_result"]
        assert summary["deployment"]["tuned_validation"]["is_valid"] is True
        assert validation["is_valid"] is True
        assert replay["summary"]["roundCount"] >= 1
        assert replay["agent"]["llmEnabled"] is False
        assert replay["agent"]["proposalBreakdown"]["fallback"] >= 1
        assert replay["roundInsights"]
        assert replay["caseLeaderboard"]

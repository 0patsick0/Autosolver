from __future__ import annotations

import argparse
import json
from pathlib import Path

from autosolver.cli import _smoke_command
from autosolver.core.models import BenchmarkSummary, SolveConfig
from autosolver.eval.benchmark import benchmark_instances
from autosolver.eval.manifest import load_benchmark_cases
from autosolver.io.events import EventWriter, read_events
from autosolver.solver.portfolio import PortfolioSolver
from autosolver_agent.provider import LLMProvider
from autosolver_agent.research import ResearchRunner, _extract_parameter_hints


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

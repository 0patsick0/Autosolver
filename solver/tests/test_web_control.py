from __future__ import annotations

from pathlib import Path

from autosolver.web_control import ControlDefaults, ControlRuntime, _resolve_repo_relative_path, build_job_spec


class TestWebControl:
    def test_build_research_job_spec_wires_dashboard_and_artifacts(self):
        spec = build_job_spec(
            {
                "kind": "research",
                "benchmarkPath": "examples/benchmarks/benchmark_manifest.json",
                "rounds": 3,
                "timeBudgetMs": 700,
                "seed": 9,
                "allowRuleBasedFallback": True,
            },
            ControlDefaults(),
        )

        assert spec.kind == "research"
        assert "--dashboard-output" in spec.command
        assert "--rounds" in spec.command
        assert spec.dashboard_replay_path == "dashboard/public/replay-data.json"
        assert spec.artifacts["researchSummary"].endswith("research_summary.json")
        assert spec.artifacts["events"].endswith("research.jsonl")

    def test_build_smoke_job_spec_keeps_dashboard_replay_target(self):
        spec = build_job_spec(
            {
                "kind": "smoke",
                "rounds": 2,
                "timeBudgetMs": 800,
                "allowRuleBasedFallback": True,
            },
            ControlDefaults(),
        )

        assert spec.kind == "smoke"
        assert spec.dashboard_replay_path == "dashboard/public/replay-data.json"
        assert "--allow-rule-based-fallback" in spec.command
        assert spec.artifacts["smokeSummary"].endswith("smoke_summary.json")

    def test_build_pytest_job_spec_uses_uv_run_pytest(self):
        spec = build_job_spec({"kind": "pytest"}, ControlDefaults())

        assert spec.kind == "pytest"
        assert spec.command == ["uv", "run", "pytest", "-q"]

    def test_resolve_repo_relative_path_rejects_escape(self):
        safe_path = _resolve_repo_relative_path("README.md")
        assert isinstance(safe_path, Path)
        assert safe_path.name == "README.md"

        try:
            _resolve_repo_relative_path("../outside.txt")
        except ValueError as exc:
            assert "inside the repository" in str(exc)
        else:
            raise AssertionError("Expected path escape to be rejected.")

    def test_runtime_snapshot_uses_configured_endpoint(self):
        runtime = ControlRuntime()
        runtime.configure_endpoint("0.0.0.0", 9999)

        snapshot = runtime.snapshot()

        assert snapshot["apiBase"] == "http://0.0.0.0:9999"

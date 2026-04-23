from __future__ import annotations

import json
import shutil
from pathlib import Path

import autosolver.web_control as web_control
from autosolver.web_control import ControlDefaults, ControlRuntime, _resolve_repo_relative_path, build_job_spec, store_uploaded_file


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

    def test_build_solve_validate_job_spec_wires_validation_artifact(self):
        spec = build_job_spec(
            {
                "kind": "solve-validate",
                "instancePath": "examples/instances/sample_instance.json",
                "timeBudgetMs": 900,
                "seed": 4,
            },
            ControlDefaults(),
        )

        assert spec.kind == "solve-validate"
        assert "solve-validate" in spec.command
        assert spec.artifacts["validationReport"].endswith("validation_report.json")
        assert spec.artifacts["solveResult"].endswith("solve_result.json")

    def test_build_solve_submit_job_spec_wires_submission_bundle_artifacts(self):
        spec = build_job_spec(
            {
                "kind": "solve-submit",
                "instancePath": "examples/instances/sample_instance.json",
                "timeBudgetMs": 900,
                "seed": 4,
            },
            ControlDefaults(),
        )

        assert spec.kind == "solve-submit"
        assert "solve-submit" in spec.command
        assert spec.artifacts["validationReport"].endswith("validation_report.json")
        assert spec.artifacts["submissionSnapshot"].endswith("submission_snapshot.json")

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

    def test_runtime_launch_queues_when_worker_is_active(self, monkeypatch, tmp_path: Path):
        history_path = tmp_path / "control_history.json"
        monkeypatch.setattr(web_control, "CONTROL_HISTORY_PATH", history_path)
        runtime = ControlRuntime()

        active_job = web_control.ControlJob(
            job_id="research-active",
            kind="research",
            status="running",
            command=["uv", "run", "autosolver", "research"],
            started_at="2026-04-23T00:00:00+00:00",
            output_root=None,
            artifacts={},
        )
        runtime._current_job = active_job

        snapshot = runtime.launch({"kind": "pytest"})

        assert snapshot["status"] == "queued"
        assert len(runtime._queued_jobs) == 1
        assert runtime.snapshot()["queuedJobs"][0]["jobId"] == snapshot["jobId"]
        assert history_path.exists()

    def test_runtime_launch_queues_while_current_job_is_cancelling(self, monkeypatch, tmp_path: Path):
        history_path = tmp_path / "control_history.json"
        monkeypatch.setattr(web_control, "CONTROL_HISTORY_PATH", history_path)
        runtime = ControlRuntime()

        runtime._current_job = web_control.ControlJob(
            job_id="research-cancelling",
            kind="research",
            status="cancelling",
            command=["uv", "run", "autosolver", "research"],
            started_at="2026-04-23T00:00:00+00:00",
            output_root=None,
            artifacts={},
        )

        snapshot = runtime.launch({"kind": "pytest"})

        assert snapshot["status"] == "queued"
        assert len(runtime._queued_jobs) == 1

    def test_runtime_load_history_marks_inflight_jobs_cancelled(self, monkeypatch, tmp_path: Path):
        history_path = tmp_path / "control_history.json"
        history_path.write_text(
            json.dumps(
                {
                    "recentJobs": [
                        {
                            "jobId": "research-queued",
                            "kind": "research",
                            "status": "queued",
                            "command": ["uv", "run", "autosolver", "research"],
                            "startedAt": "2026-04-23T00:00:00+00:00",
                            "finishedAt": None,
                            "outputRoot": "examples/web_runs/research-queued",
                            "artifacts": {},
                            "dashboardReplayPath": "dashboard/public/replay-data.json",
                            "exitCode": None,
                            "error": None,
                            "pid": None,
                            "logTail": "[web-control] Queued behind the currently running job.",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(web_control, "CONTROL_HISTORY_PATH", history_path)

        runtime = ControlRuntime()
        snapshot = runtime.snapshot()

        assert snapshot["recentJobs"][0]["status"] == "cancelled"
        assert snapshot["recentJobs"][0]["finishedAt"] is not None
        assert "Recovered after restart" in snapshot["recentJobs"][0]["logTail"]

    def test_store_uploaded_file_writes_repo_relative_upload(self, monkeypatch, tmp_path: Path):
        upload_root = web_control.REPO_ROOT / "examples" / "web_uploads_test"
        monkeypatch.setattr(web_control, "CONTROL_UPLOAD_ROOT", upload_root)

        try:
            relative_path = store_uploaded_file("instance", "../my case.json", '{"instance_id":"demo"}')
            file_path = web_control.REPO_ROOT / relative_path

            assert relative_path.startswith("examples/web_uploads_test/instances/")
            assert file_path.exists()
            assert file_path.read_text(encoding="utf-8") == '{"instance_id":"demo"}'
            assert ".." not in relative_path
        finally:
            shutil.rmtree(upload_root, ignore_errors=True)

    def test_store_uploaded_file_rejects_unknown_target(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(web_control, "CONTROL_UPLOAD_ROOT", tmp_path / "web_uploads")

        try:
            store_uploaded_file("mystery", "demo.json", "{}")
        except ValueError as exc:
            assert "Unsupported upload target" in str(exc)
        else:
            raise AssertionError("Expected unsupported upload target to be rejected.")

from __future__ import annotations

import argparse
import json
import mimetypes
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from autosolver_agent.provider import OpenAICompatibleProvider

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTROL_HOST = "127.0.0.1"
DEFAULT_CONTROL_PORT = 8765
DEFAULT_DASHBOARD_REPLAY_PATH = "dashboard/public/replay-data.json"
MAX_LOG_LINES = 400
MAX_FILE_BYTES = 2_000_000


@dataclass(frozen=True)
class ControlDefaults:
    benchmark_path: str = "examples/benchmarks/benchmark_manifest.json"
    instance_path: str = "examples/instances/sample_instance.json"
    search_space_path: str = "examples/research_search_space.json"
    dashboard_output_path: str = DEFAULT_DASHBOARD_REPLAY_PATH
    rounds: int = 2
    time_budget_ms: int = 10_000
    seed: int = 0
    allow_rule_based_fallback: bool = False


@dataclass(frozen=True)
class JobSpec:
    kind: str
    command: list[str]
    output_root: str | None
    artifacts: dict[str, str]
    dashboard_replay_path: str | None = None


@dataclass
class ControlJob:
    job_id: str
    kind: str
    status: str
    command: list[str]
    started_at: str
    output_root: str | None
    artifacts: dict[str, str]
    dashboard_replay_path: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    error: str | None = None
    pid: int | None = None
    log_lines: list[str] = field(default_factory=list)

    def append_log(self, line: str) -> None:
        clean_line = line.rstrip()
        if not clean_line:
            return
        self.log_lines.append(clean_line)
        if len(self.log_lines) > MAX_LOG_LINES:
            self.log_lines[:] = self.log_lines[-MAX_LOG_LINES:]

    def snapshot(self) -> dict[str, object]:
        return {
            "jobId": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "command": list(self.command),
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "outputRoot": self.output_root,
            "artifacts": dict(self.artifacts),
            "dashboardReplayPath": self.dashboard_replay_path,
            "exitCode": self.exit_code,
            "error": self.error,
            "pid": self.pid,
            "logTail": "\n".join(self.log_lines),
        }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _slug_now() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def _normalize_repo_path(value: str | None, fallback: str) -> str:
    target = value.strip() if isinstance(value, str) and value.strip() else fallback
    path = Path(target)
    if path.is_absolute():
        return str(path)
    return str((REPO_ROOT / path).resolve())


def _relative_to_repo(path: str | Path | None) -> str | None:
    if path is None:
        return None
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(resolved).replace("\\", "/")


def _resolve_repo_relative_path(path: str) -> Path:
    normalized = path.strip().replace("\\", "/")
    if not normalized:
        raise ValueError("Missing path.")
    candidate = (REPO_ROOT / normalized).resolve()
    try:
        candidate.relative_to(REPO_ROOT)
    except ValueError as exc:
        raise ValueError("Path must stay inside the repository.") from exc
    return candidate


def build_job_spec(payload: dict[str, object], defaults: ControlDefaults | None = None) -> JobSpec:
    resolved_defaults = defaults or ControlDefaults()
    kind = str(payload.get("kind", "research")).strip().lower()
    stamp = _slug_now()
    output_root = (REPO_ROOT / "examples" / "web_runs" / f"{kind}-{stamp}").resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    benchmark_path = _normalize_repo_path(payload.get("benchmarkPath") if isinstance(payload.get("benchmarkPath"), str) else None, resolved_defaults.benchmark_path)
    instance_path = _normalize_repo_path(payload.get("instancePath") if isinstance(payload.get("instancePath"), str) else None, resolved_defaults.instance_path)
    search_space_path = _normalize_repo_path(
        payload.get("searchSpacePath") if isinstance(payload.get("searchSpacePath"), str) else None,
        resolved_defaults.search_space_path,
    )
    dashboard_output_path = _normalize_repo_path(
        payload.get("dashboardOutputPath") if isinstance(payload.get("dashboardOutputPath"), str) else None,
        resolved_defaults.dashboard_output_path,
    )

    rounds = int(payload.get("rounds", resolved_defaults.rounds))
    time_budget_ms = int(payload.get("timeBudgetMs", resolved_defaults.time_budget_ms))
    seed = int(payload.get("seed", resolved_defaults.seed))
    allow_rule_based_fallback = bool(payload.get("allowRuleBasedFallback", resolved_defaults.allow_rule_based_fallback))

    if kind == "pytest":
        return JobSpec(
            kind=kind,
            command=["uv", "run", "pytest", "-q"],
            output_root=_relative_to_repo(output_root),
            artifacts={},
        )

    if kind == "smoke":
        smoke_output = output_root / "smoke"
        command = [
            "uv",
            "run",
            "autosolver",
            "smoke",
            str(smoke_output),
            "--rounds",
            str(rounds),
            "--time-budget-ms",
            str(time_budget_ms),
            "--seed",
            str(seed),
            "--dashboard-output",
            dashboard_output_path,
        ]
        if allow_rule_based_fallback:
            command.append("--allow-rule-based-fallback")
        return JobSpec(
            kind=kind,
            command=command,
            output_root=_relative_to_repo(output_root),
            artifacts={
                "smokeSummary": _relative_to_repo(smoke_output / "smoke_summary.json") or "",
                "dashboardReplay": _relative_to_repo(Path(dashboard_output_path)) or "",
            },
            dashboard_replay_path=_relative_to_repo(Path(dashboard_output_path)),
        )

    if kind == "benchmark":
        events_path = output_root / "benchmark.jsonl"
        summary_path = output_root / "benchmark_summary.json"
        return JobSpec(
            kind=kind,
            command=[
                "uv",
                "run",
                "autosolver",
                "benchmark",
                benchmark_path,
                "--output",
                str(summary_path),
                "--events",
                str(events_path),
                "--time-budget-ms",
                str(time_budget_ms),
                "--seed",
                str(seed),
            ],
            output_root=_relative_to_repo(output_root),
            artifacts={
                "benchmarkSummary": _relative_to_repo(summary_path) or "",
                "events": _relative_to_repo(events_path) or "",
            },
        )

    if kind == "solve":
        solve_output = output_root / "solve_result.json"
        submission_output = output_root / "submission.json"
        events_path = output_root / "solve.jsonl"
        return JobSpec(
            kind=kind,
            command=[
                "uv",
                "run",
                "autosolver",
                "solve",
                instance_path,
                "--output",
                str(solve_output),
                "--submission-output",
                str(submission_output),
                "--events",
                str(events_path),
                "--time-budget-ms",
                str(time_budget_ms),
                "--seed",
                str(seed),
            ],
            output_root=_relative_to_repo(output_root),
            artifacts={
                "solveResult": _relative_to_repo(solve_output) or "",
                "submission": _relative_to_repo(submission_output) or "",
                "events": _relative_to_repo(events_path) or "",
            },
        )

    if kind != "research":
        raise ValueError(f"Unsupported control run kind: {kind}")

    events_path = output_root / "research.jsonl"
    summary_path = output_root / "research_summary.json"
    command = [
        "uv",
        "run",
        "autosolver",
        "research",
        benchmark_path,
        "--rounds",
        str(rounds),
        "--events",
        str(events_path),
        "--output",
        str(summary_path),
        "--search-space",
        search_space_path,
        "--dashboard-output",
        dashboard_output_path,
        "--time-budget-ms",
        str(time_budget_ms),
        "--seed",
        str(seed),
    ]
    if allow_rule_based_fallback:
        command.append("--allow-rule-based-fallback")
    return JobSpec(
        kind=kind,
        command=command,
        output_root=_relative_to_repo(output_root),
        artifacts={
            "researchSummary": _relative_to_repo(summary_path) or "",
            "events": _relative_to_repo(events_path) or "",
            "dashboardReplay": _relative_to_repo(Path(dashboard_output_path)) or "",
        },
        dashboard_replay_path=_relative_to_repo(Path(dashboard_output_path)),
    )


class ControlRuntime:
    def __init__(self, defaults: ControlDefaults | None = None):
        self.defaults = defaults or ControlDefaults()
        self.host = DEFAULT_CONTROL_HOST
        self.port = DEFAULT_CONTROL_PORT
        self._lock = threading.Lock()
        self._current_job: ControlJob | None = None
        self._recent_jobs: list[ControlJob] = []
        self._processes: dict[str, subprocess.Popen[str]] = {}

    def configure_endpoint(self, host: str, port: int) -> None:
        self.host = host
        self.port = port

    def snapshot(self) -> dict[str, object]:
        provider = OpenAICompatibleProvider.from_environment()
        with self._lock:
            current_job = self._current_job.snapshot() if self._current_job is not None else None
            recent_jobs = [job.snapshot() for job in self._recent_jobs[:6]]
        return {
            "available": True,
            "repoRoot": str(REPO_ROOT),
            "apiBase": f"http://{self.host}:{self.port}",
            "defaults": {
                "benchmarkPath": self.defaults.benchmark_path,
                "instancePath": self.defaults.instance_path,
                "searchSpacePath": self.defaults.search_space_path,
                "dashboardOutputPath": self.defaults.dashboard_output_path,
                "rounds": self.defaults.rounds,
                "timeBudgetMs": self.defaults.time_budget_ms,
                "seed": self.defaults.seed,
                "allowRuleBasedFallback": self.defaults.allow_rule_based_fallback,
            },
            "provider": {
                "label": provider.provider_label(),
                "llmConfigured": provider.is_configured(),
            },
            "currentJob": current_job,
            "recentJobs": recent_jobs,
        }

    def launch(self, payload: dict[str, object]) -> dict[str, object]:
        spec = build_job_spec(payload, self.defaults)
        with self._lock:
            if self._current_job is not None and self._current_job.status == "running":
                raise RuntimeError("A control job is already running. Please wait for it to finish or cancel it first.")
            job = ControlJob(
                job_id=f"{spec.kind}-{_slug_now()}",
                kind=spec.kind,
                status="running",
                command=spec.command,
                started_at=_utc_now(),
                output_root=spec.output_root,
                artifacts=spec.artifacts,
                dashboard_replay_path=spec.dashboard_replay_path,
            )
            self._current_job = job
            self._recent_jobs.insert(0, job)
            self._recent_jobs[:] = self._recent_jobs[:8]

        worker = threading.Thread(target=self._run_job, args=(job, spec), daemon=True)
        worker.start()
        return job.snapshot()

    def cancel(self, job_id: str) -> dict[str, object]:
        with self._lock:
            job = self._current_job
            process = self._processes.get(job_id)
            if job is None or job.job_id != job_id or process is None or job.status != "running":
                raise RuntimeError("No running job found for this id.")
            job.status = "cancelling"
            job.append_log("[web-control] Received cancel request.")
        process.terminate()
        return job.snapshot()

    def _run_job(self, job: ControlJob, spec: JobSpec) -> None:
        process = subprocess.Popen(
            spec.command,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        with self._lock:
            self._processes[job.job_id] = process
            job.pid = process.pid
            job.append_log(f"[web-control] Running in {REPO_ROOT}")
            job.append_log(f"[web-control] Command: {' '.join(spec.command)}")

        assert process.stdout is not None
        for line in process.stdout:
            with self._lock:
                job.append_log(line)

        exit_code = process.wait()
        with self._lock:
            self._processes.pop(job.job_id, None)
            job.exit_code = exit_code
            job.finished_at = _utc_now()
            if job.status == "cancelling":
                job.status = "cancelled"
            else:
                job.status = "succeeded" if exit_code == 0 else "failed"
            if exit_code != 0 and job.status != "cancelled":
                job.error = f"Process exited with code {exit_code}."
            if spec.dashboard_replay_path:
                replay_path = REPO_ROOT / spec.dashboard_replay_path
                if replay_path.exists():
                    job.append_log(f"[web-control] Dashboard replay refreshed: {spec.dashboard_replay_path}")


RUNTIME = ControlRuntime()


class ControlRequestHandler(BaseHTTPRequestHandler):
    server_version = "AutoSolverWebControl/0.1"

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._write_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/health", "/api/control/status"}:
            self._send_json(HTTPStatus.OK, RUNTIME.snapshot())
            return
        if parsed.path == "/api/control/file":
            query = parse_qs(parsed.query)
            requested_path = query.get("path", [""])[0]
            try:
                file_path = _resolve_repo_relative_path(requested_path)
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            if not file_path.exists() or not file_path.is_file():
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Requested artifact file does not exist."})
                return
            file_size = file_path.stat().st_size
            if file_size > MAX_FILE_BYTES:
                self._send_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": f"Requested artifact is too large to preview in the dashboard ({file_size} bytes)."},
                )
                return
            content_type = mimetypes.guess_type(file_path.name)[0] or "text/plain"
            try:
                payload = file_path.read_bytes()
            except OSError as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Failed to read artifact: {exc}"})
                return
            self.send_response(HTTPStatus.OK)
            self._write_cors_headers()
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/control/run":
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                job = RUNTIME.launch(payload)
            except RuntimeError as exc:
                self._send_json(HTTPStatus.CONFLICT, {"error": str(exc), "state": RUNTIME.snapshot()})
                return
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.ACCEPTED, {"job": job, "state": RUNTIME.snapshot()})
            return

        if self.path.startswith("/api/control/jobs/") and self.path.endswith("/cancel"):
            job_id = self.path.removeprefix("/api/control/jobs/").removesuffix("/cancel").strip("/")
            try:
                job = RUNTIME.cancel(job_id)
            except RuntimeError as exc:
                self._send_json(HTTPStatus.CONFLICT, {"error": str(exc), "state": RUNTIME.snapshot()})
                return
            self._send_json(HTTPStatus.OK, {"job": job, "state": RUNTIME.snapshot()})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def _read_json_body(self) -> dict[str, object] | None:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            content_length = int(raw_length)
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length."})
            return None
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Body must be valid JSON."})
            return None
        if not isinstance(payload, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Body must be a JSON object."})
            return None
        return payload

    def _send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._write_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _write_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="autosolver-web", description="Local control API for the AutoSolver dashboard.")
    parser.add_argument("--host", type=str, default=DEFAULT_CONTROL_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_CONTROL_PORT)
    args = parser.parse_args(argv)

    RUNTIME.configure_endpoint(args.host, args.port)
    server = ThreadingHTTPServer((args.host, args.port), ControlRequestHandler)
    print(f"[autosolver-web] listening on http://{args.host}:{args.port}", flush=True)
    print(f"[autosolver-web] repo root: {REPO_ROOT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[autosolver-web] shutting down", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

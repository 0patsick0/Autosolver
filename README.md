# AutoSolver Agent

AutoSolver Agent is a dual-mode project for the Meitu delivery assignment challenge.

- `solve mode` runs a local anytime solver within a fixed time budget.
- `research mode` runs an LLM-first controlled experiment loop that proposes solver variants, benchmarks them, reflects on results, and keeps or discards them automatically.
- `dashboard` replays JSONL event logs into a lightweight web UI.

## Project Layout

- `solver/` Python core types, candidate generation, portfolio solver, adapters, CLI, and tests
- `agent/` Python research loop, LLM provider adapter, and small-instance LLM baseline
- `dashboard/` Vite + React dashboard for replay artifacts
- `examples/` sample canonical instances and benchmark manifests

## Quick Start

```bash
uv sync --extra dev
uv run autosolver solve examples/instances/sample_instance.json --output examples/solve_result.json
uv run autosolver solve examples/instances/sample_instance.json --config-source examples/nvidia_research_summary.json --output examples/solve_result_from_research.json
uv run autosolver benchmark examples/benchmarks --output examples/benchmark_summary.json
uv run autosolver benchmark examples/benchmarks/benchmark_manifest.json --output examples/benchmark_manifest_summary.json
uv run autosolver validate examples/instances/sample_instance.json examples/solve_result.json --output examples/validation_report.json
uv run autosolver generate examples/generated --instances 4 --orders 32 --riders 12 --seed 7
uv run autosolver research examples/benchmarks/benchmark_manifest.json --resume --state examples/research_state.json --search-space examples/research_search_space.json
uv run autosolver research examples/benchmarks/benchmark_manifest.json --dashboard-output dashboard/public/replay-data.json
uv run autosolver replay examples/events/research.jsonl --output dashboard/public/replay-data.json
uv run autosolver smoke examples/smoke_run
powershell -ExecutionPolicy Bypass -File scripts/full_check.ps1
powershell -ExecutionPolicy Bypass -File scripts/live_dashboard.ps1
start_live_dashboard.cmd
```

## LLM Requirement

- This challenge expects an actual LLM-driven agent, so `autosolver research` now requires a configured LLM by default.
- Supported path today is any OpenAI-compatible chat endpoint:
  - cloud API: set `OPENAI_API_KEY`, optionally `OPENAI_MODEL` and `OPENAI_BASE_URL`
  - local server: point `OPENAI_BASE_URL` to a local OpenAI-compatible endpoint and set `OPENAI_MODEL`
- `--allow-rule-based-fallback` exists only for offline smoke tests and should not be the competition demo path.

### NVIDIA Build Example

```bash
set OPENAI_BASE_URL=https://integrate.api.nvidia.com/v1
set OPENAI_MODEL=meta/llama-3.1-8b-instruct
set OPENAI_API_KEY=<your_nvapi_key>
uv run autosolver research examples/benchmarks/benchmark_manifest.json
```

### Local Secret File

The CLI now auto-loads `OPENAI_BASE_URL`, `OPENAI_MODEL`, and `OPENAI_API_KEY` from `.env.local` in the repo root when shell environment variables are not present. This keeps the key reusable without baking it into tracked code or docs.

```bash
OPENAI_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
OPENAI_MODEL=deepseek-v3-1-terminus
OPENAI_API_KEY=<your_ark_api_key>
```

`.env.local` is ignored by `.gitignore`, so it is the preferred place for machine-local secrets during demos and development.

## Canonical Schema

The canonical schema keeps the solver decoupled from the official competition format:

- orders
- riders
- order-rider match scores
- optional bundle candidates
- business constraints

When official input and output formats arrive, only the adapter and submission writer should need to change.

## New Iteration Workflows

- `benchmark` accepts a directory, a single instance file, or a manifest file with repeated and weighted cases.
- `validate` checks a canonical result or wrapped submission against an instance and recomputes the objective from dispatches.
- `generate` creates synthetic canonical instances plus a ready-to-use benchmark manifest for local search and regression tests.
- `research --resume` keeps history, seen configurations, and LLM reflection notes in a state file so the agent can continue exploring instead of restarting.
- `research --dashboard-output dashboard/public/replay-data.json` now updates the dashboard replay artifact while the agent is still running, so the web UI can follow the process live.
- `solve --config-source path/to/research_summary.json` reuses the incumbent solver configuration discovered by the agent on prior research runs.
- `smoke output_dir` generates a synthetic benchmark, solves one case, validates it, runs research, re-solves with the incumbent config, and writes replay data in one command.
- `scripts/full_check.ps1` runs pytest, the end-to-end smoke flow, research-to-solve deployment validation, and a production dashboard build.
- `scripts/live_dashboard.ps1` starts the Vite dashboard, opens the browser, and launches a live research run that streams replay updates into `dashboard/public/replay-data.json`.
- `start_live_dashboard.cmd` is the one-click Windows launcher. You can double-click it from Explorer or run it from the repo root.

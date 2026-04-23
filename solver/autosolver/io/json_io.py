from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autosolver.core.models import BenchmarkSummary, CanonicalInstance, SolveConfig, SolveResult, sanitize_json_value
from autosolver.io.adapters import CanonicalJsonAdapter


def load_instance(path: str | Path) -> CanonicalInstance:
    return CanonicalJsonAdapter().load(path)


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_result_payload(path: str | Path) -> dict[str, Any]:
    raw = load_json(path)
    if isinstance(raw, dict) and raw.get("format") == "canonical-v1" and isinstance(raw.get("result"), dict):
        return raw["result"]
    if not isinstance(raw, dict):
        raise ValueError(f"Result payload at {path} must be a JSON object.")
    return raw


def load_incumbent_solve_config(path: str | Path) -> SolveConfig:
    raw = load_json(path)
    if not isinstance(raw, dict):
        raise ValueError(f"Config source at {path} must be a JSON object.")

    incumbent = raw.get("incumbent")
    if isinstance(incumbent, dict):
        solver_config = incumbent.get("solver_config")
        if isinstance(solver_config, dict):
            return solve_config_from_raw(solver_config)

    history = raw.get("history")
    if isinstance(history, list) and history:
        best_item = _select_best_history_item(history)
        if best_item is not None and isinstance(best_item.get("solver_config"), dict):
            return solve_config_from_raw(best_item["solver_config"])

    raise ValueError(f"Could not find an incumbent solver_config in {path}.")


def write_json(path: str | Path, payload: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(sanitize_json_value(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def write_solve_result(path: str | Path, result: SolveResult) -> None:
    write_json(path, result)


def write_benchmark_summary(path: str | Path, summary: BenchmarkSummary) -> None:
    write_json(path, summary)


def solve_config_from_raw(raw: dict[str, Any]) -> SolveConfig:
    return SolveConfig(
        time_budget_ms=int(raw.get("time_budget_ms", 10_000)),
        top_k_riders_per_order=int(raw.get("top_k_riders_per_order", 3)),
        use_cpsat=bool(raw.get("use_cpsat", True)),
        use_lns=bool(raw.get("use_lns", True)),
        cpsat_max_orders=int(raw.get("cpsat_max_orders", 120)),
        generate_bundles_if_missing=bool(raw.get("generate_bundles_if_missing", True)),
        max_generated_bundles=int(raw.get("max_generated_bundles", 64)),
        bundle_candidate_pool_size=int(raw.get("bundle_candidate_pool_size", 6)),
        max_bundle_size=int(raw.get("max_bundle_size", 3)),
        bundle_distance_threshold=float(raw.get("bundle_distance_threshold", 2.5)),
        bundle_discount_factor=float(raw.get("bundle_discount_factor", 0.92)),
        bundle_acceptance_scale=float(raw.get("bundle_acceptance_scale", 0.95)),
        lns_destroy_fraction=float(raw.get("lns_destroy_fraction", 0.25)),
        lns_iterations=int(raw.get("lns_iterations", 24)),
        allow_llm_baseline=bool(raw.get("allow_llm_baseline", False)),
    )


def _select_best_history_item(history: list[Any]) -> dict[str, Any] | None:
    best_item: dict[str, Any] | None = None
    best_key: tuple[float, float] | None = None
    for item in history:
        if not isinstance(item, dict):
            continue
        expected_completed_orders = float(item.get("average_expected_completed_orders", 0.0))
        total_cost = float(item.get("average_total_cost", float("inf")))
        key = (-expected_completed_orders, total_cost)
        if best_key is None or key < best_key:
            best_item = item
            best_key = key
    return best_item

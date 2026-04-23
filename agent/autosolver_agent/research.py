from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from random import Random
from typing import Any

from autosolver.core.models import BenchmarkSummary, ExperimentRecord, ExperimentSpec, LexicographicScore, SolveConfig
from autosolver.core.objective import better_score
from autosolver.eval.benchmark import benchmark_instances
from autosolver.eval.manifest import load_benchmark_cases
from autosolver.io.events import EventWriter
from autosolver.io.json_io import load_json, write_json
from autosolver.solver.portfolio import PortfolioSolver
from autosolver_agent.provider import LLMProvider

DEFAULT_SEARCH_SPACE = {
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
}


@dataclass
class ResearchMemory:
    seen_signatures: set[str] = field(default_factory=set)
    failed_signatures: set[str] = field(default_factory=set)
    history: list[ExperimentRecord] = field(default_factory=list)
    lessons: list[dict[str, object]] = field(default_factory=list)


class RuleBasedProposer:
    def __init__(self, seed: int, search_space: dict[str, list[object]]):
        self.randomizer = Random(seed)
        self.search_space = search_space

    def propose(self, memory: ResearchMemory, benchmark_id: str, time_budget_ms: int, round_index: int) -> ExperimentSpec:
        for attempt in range(32):
            offset = round_index + attempt
            solver_config = SolveConfig(
                time_budget_ms=time_budget_ms,
                top_k_riders_per_order=int(_pick(self.search_space, "top_k_riders_per_order", offset, 3)),
                use_cpsat=bool(_pick(self.search_space, "use_cpsat", offset, True)),
                use_lns=True,
                generate_bundles_if_missing=bool(_pick(self.search_space, "generate_bundles_if_missing", offset, True)),
                bundle_candidate_pool_size=int(_pick(self.search_space, "bundle_candidate_pool_size", offset, 6)),
                max_bundle_size=int(_pick(self.search_space, "max_bundle_size", offset, 3)),
                bundle_distance_threshold=float(_pick(self.search_space, "bundle_distance_threshold", offset, 2.5)),
                bundle_discount_factor=float(_pick(self.search_space, "bundle_discount_factor", offset, 0.92)),
                bundle_acceptance_scale=float(_pick(self.search_space, "bundle_acceptance_scale", offset, 0.95)),
                max_generated_bundles=int(_pick(self.search_space, "max_generated_bundles", offset, 64)),
                lns_destroy_fraction=float(_pick(self.search_space, "lns_destroy_fraction", offset, 0.25)),
                lns_iterations=int(_pick(self.search_space, "lns_iterations", offset, 24)),
            )
            if _config_signature(solver_config) not in memory.seen_signatures:
                break
        return ExperimentSpec(
            experiment_id=f"exp-{round_index + 1}",
            name=f"rule-based-{round_index + 1}",
            hypothesis=(
                "Adjust top_k, bundle generation, and local search neighborhood "
                f"for benchmark {benchmark_id} with config {_config_signature(solver_config)}."
            ),
            solver_config=solver_config,
            benchmark_ids=(benchmark_id,),
            notes="fallback-proposer",
        )


class LLMExperimentProposer:
    def __init__(self, provider: LLMProvider, search_space: dict[str, list[object]]):
        self.provider = provider
        self.search_space = search_space
        self.system_prompt = Path(__file__).with_name("prompts").joinpath("research_system.md").read_text(encoding="utf-8")

    def propose(self, memory: ResearchMemory, benchmark_id: str, time_budget_ms: int, round_index: int) -> ExperimentSpec:
        history = [
            {
                "experiment_id": record.experiment_id,
                "status": record.status,
                "hypothesis": record.hypothesis,
                "average_expected_completed_orders": record.benchmark_summary.average_expected_completed_orders,
                "average_total_cost": record.benchmark_summary.average_total_cost,
            }
            for record in memory.history[-5:]
        ]
        response = self.provider.complete_json(
            self.system_prompt,
            json.dumps(
                {
                    "round_index": round_index,
                    "benchmark_id": benchmark_id,
                    "time_budget_ms": time_budget_ms,
                    "search_space": self.search_space,
                    "history": history,
                    "lessons": memory.lessons[-5:],
                    "failed_signatures": sorted(memory.failed_signatures),
                },
                ensure_ascii=False,
            ),
        )
        parameter_hints = _extract_parameter_hints(response)
        resolved_response = {**parameter_hints, **response}
        solver_config = SolveConfig(
            time_budget_ms=time_budget_ms,
            top_k_riders_per_order=int(_coerce_allowed(resolved_response.get("top_k_riders_per_order"), self.search_space["top_k_riders_per_order"], 3)),
            use_cpsat=bool(_coerce_allowed(resolved_response.get("use_cpsat"), self.search_space["use_cpsat"], True)),
            use_lns=True,
            generate_bundles_if_missing=bool(
                _coerce_allowed(resolved_response.get("generate_bundles_if_missing"), self.search_space["generate_bundles_if_missing"], True)
            ),
            bundle_candidate_pool_size=int(
                _coerce_allowed(resolved_response.get("bundle_candidate_pool_size"), self.search_space["bundle_candidate_pool_size"], 6)
            ),
            max_bundle_size=int(_coerce_allowed(resolved_response.get("max_bundle_size"), self.search_space["max_bundle_size"], 3)),
            bundle_distance_threshold=float(
                _coerce_allowed(resolved_response.get("bundle_distance_threshold"), self.search_space["bundle_distance_threshold"], 2.5)
            ),
            bundle_discount_factor=float(
                _coerce_allowed(resolved_response.get("bundle_discount_factor"), self.search_space["bundle_discount_factor"], 0.92)
            ),
            bundle_acceptance_scale=float(
                _coerce_allowed(resolved_response.get("bundle_acceptance_scale"), self.search_space["bundle_acceptance_scale"], 0.95)
            ),
            max_generated_bundles=int(_coerce_allowed(resolved_response.get("max_generated_bundles"), self.search_space["max_generated_bundles"], 64)),
            lns_destroy_fraction=float(
                _coerce_allowed(resolved_response.get("lns_destroy_fraction"), self.search_space["lns_destroy_fraction"], 0.25)
            ),
            lns_iterations=int(_coerce_allowed(resolved_response.get("lns_iterations"), self.search_space["lns_iterations"], 24)),
        )
        return ExperimentSpec(
            experiment_id=str(resolved_response.get("experiment_id", f"llm-exp-{round_index + 1}")),
            name=str(resolved_response.get("name", f"llm-proposal-{round_index + 1}")),
            hypothesis=str(resolved_response.get("hypothesis", "Explore solver parameter combinations.")),
            solver_config=solver_config,
            benchmark_ids=(benchmark_id,),
            notes="llm-proposer",
        )


class LLMExperimentReflector:
    def __init__(self, provider: LLMProvider):
        self.provider = provider
        self.system_prompt = Path(__file__).with_name("prompts").joinpath("research_reflection.md").read_text(encoding="utf-8")

    def reflect(
        self,
        benchmark_id: str,
        round_index: int,
        record: ExperimentRecord,
        incumbent_summary: BenchmarkSummary | None,
        memory: ResearchMemory,
    ) -> dict[str, object]:
        recent_history = [
            {
                "experiment_id": item.experiment_id,
                "status": item.status,
                "average_expected_completed_orders": item.benchmark_summary.average_expected_completed_orders,
                "average_total_cost": item.benchmark_summary.average_total_cost,
            }
            for item in memory.history[-5:]
        ]
        response = self.provider.complete_json(
            self.system_prompt,
            json.dumps(
                {
                    "benchmark_id": benchmark_id,
                    "round_index": round_index,
                    "record": _record_to_payload(record),
                    "incumbent": _benchmark_to_payload(incumbent_summary),
                    "recent_history": recent_history,
                },
                ensure_ascii=False,
            ),
        )
        return {
            "summary": str(response.get("summary", "")),
            "keep_reason": str(response.get("keep_reason", "")),
            "risks": _string_list(response.get("risks")),
            "next_focus": _string_list(response.get("next_focus")),
            "avoid_patterns": _string_list(response.get("avoid_patterns")),
        }


class ResearchRunner:
    def __init__(self, provider: LLMProvider | None = None):
        self.provider = provider

    def run(
        self,
        benchmark_path: str,
        rounds: int,
        output_path: str,
        events_path: str,
        time_budget_ms: int,
        seed: int,
        state_path: str | None = None,
        resume: bool = False,
        search_space_path: str | None = None,
        dashboard_output_path: str | None = None,
        allow_rule_based_fallback: bool = False,
    ) -> dict[str, object]:
        benchmark_id, cases, benchmark_metadata = load_benchmark_cases(benchmark_path)
        if not resume:
            events_source = Path(events_path)
            if events_source.exists():
                events_source.unlink()
        event_writer = EventWriter(events_path, replay_output_path=dashboard_output_path)
        search_space = _load_search_space(search_space_path)
        resolved_state_path = Path(state_path) if state_path else Path(output_path).with_suffix(".state.json")
        memory = _load_research_state(resolved_state_path) if resume else ResearchMemory()
        self._ensure_llm_ready(allow_rule_based_fallback)
        proposer = self._build_proposer(seed, search_space, allow_rule_based_fallback)
        reflector = self._build_reflector()
        incumbent_record = _best_record(memory.history)
        incumbent_summary = incumbent_record.benchmark_summary if incumbent_record is not None else None

        event_writer.write(
            "research.session_started",
            {
                "benchmark_id": benchmark_id,
                "llm_enabled": bool(self.provider and self.provider.is_configured()),
                "provider": self.provider.provider_label() if self.provider is not None else "none",
                "fallback_allowed": allow_rule_based_fallback,
            },
        )

        if resume and memory.history:
            event_writer.write(
                "research.session_resumed",
                {
                    "benchmark_id": benchmark_id,
                    "state_path": str(resolved_state_path),
                    "loaded_history_count": len(memory.history),
                },
            )

        for round_index in range(rounds):
            spec = self._safe_propose(proposer, memory, benchmark_id, time_budget_ms, round_index, seed, search_space, allow_rule_based_fallback)
            signature = _config_signature(spec.solver_config)
            if signature in memory.seen_signatures:
                spec = RuleBasedProposer(seed + round_index + 1, search_space).propose(memory, benchmark_id, time_budget_ms, round_index + 10)
                signature = _config_signature(spec.solver_config)

            memory.seen_signatures.add(signature)
            proposal_event_type = "research.llm_proposal" if spec.notes == "llm-proposer" else "research.fallback_proposal"
            event_writer.write(
                proposal_event_type,
                {
                    "round_index": round_index,
                    "experiment_id": spec.experiment_id,
                    "hypothesis": spec.hypothesis,
                    "solver_config": spec.solver_config.__dict__,
                    "notes": spec.notes,
                },
            )
            event_writer.write(
                "research.round_started",
                {
                    "round_index": round_index,
                    "experiment_id": spec.experiment_id,
                    "hypothesis": spec.hypothesis,
                    "solver_config": spec.solver_config.__dict__,
                },
            )

            started_at = datetime.now(UTC).isoformat()
            try:
                summary = benchmark_instances(
                    cases=cases,
                    solver=PortfolioSolver(),
                    config=spec.solver_config,
                    seed=seed + round_index,
                    benchmark_id=benchmark_id,
                    event_writer=event_writer,
                    metadata=benchmark_metadata,
                )
                status = _judge(summary, incumbent_summary)
            except Exception as exc:
                memory.failed_signatures.add(signature)
                status = "crash"
                summary = BenchmarkSummary(
                    benchmark_id=benchmark_id,
                    case_metrics=(),
                    average_expected_completed_orders=0.0,
                    average_total_cost=float("inf"),
                    total_elapsed_ms=0,
                )
                event_writer.write(
                    "research.round_failed",
                    {
                        "experiment_id": spec.experiment_id,
                        "error": str(exc),
                    },
                )

            finished_at = datetime.now(UTC).isoformat()
            record = ExperimentRecord(
                experiment_id=spec.experiment_id,
                status=status,
                hypothesis=spec.hypothesis,
                benchmark_summary=summary,
                started_at=started_at,
                finished_at=finished_at,
                notes=spec.notes,
                solver_config=spec.solver_config,
                config_signature=signature,
            )
            memory.history.append(record)
            event_writer.write(
                "research.round_completed",
                {
                    "experiment_id": spec.experiment_id,
                    "status": status,
                    "average_expected_completed_orders": summary.average_expected_completed_orders,
                    "average_total_cost": summary.average_total_cost,
                    "total_elapsed_ms": summary.total_elapsed_ms,
                },
            )

            if status == "keep":
                incumbent_summary = summary
                incumbent_record = record
                event_writer.write(
                    "research.incumbent_updated",
                    {
                        "experiment_id": spec.experiment_id,
                        "average_expected_completed_orders": summary.average_expected_completed_orders,
                        "average_total_cost": summary.average_total_cost,
                    },
                )

            reflection = self._safe_reflect(reflector, benchmark_id, round_index, record, incumbent_summary, memory)
            if reflection is not None:
                memory.lessons.append(reflection)
                event_writer.write(
                    "research.llm_reflection",
                    {
                        "round_index": round_index,
                        "experiment_id": spec.experiment_id,
                        "summary": reflection["summary"],
                        "keep_reason": reflection["keep_reason"],
                        "risks": reflection["risks"],
                        "next_focus": reflection["next_focus"],
                        "avoid_patterns": reflection["avoid_patterns"],
                    },
                )

            _write_research_state(resolved_state_path, benchmark_id, memory, search_space)

        payload = {
            "benchmark_id": benchmark_id,
            "benchmark_metadata": benchmark_metadata,
            "search_space": search_space,
            "state_path": str(resolved_state_path),
            "agent": {
                "llm_enabled": bool(self.provider and self.provider.is_configured()),
                "provider": self.provider.provider_label() if self.provider is not None else "none",
                "allow_rule_based_fallback": allow_rule_based_fallback,
                "lesson_count": len(memory.lessons),
            },
            "incumbent": _record_to_payload(incumbent_record),
            "history": [_record_to_payload(record) for record in memory.history],
            "lessons": memory.lessons,
        }
        write_json(output_path, payload)
        return payload

    def _build_proposer(self, seed: int, search_space: dict[str, list[object]], allow_rule_based_fallback: bool):
        if self.provider is None or not self.provider.is_configured():
            if not allow_rule_based_fallback:
                raise RuntimeError("Research mode requires a configured LLM provider unless allow_rule_based_fallback=True.")
            return RuleBasedProposer(seed, search_space)
        return LLMExperimentProposer(self.provider, search_space)

    def _build_reflector(self) -> LLMExperimentReflector | None:
        if self.provider is None or not self.provider.is_configured():
            return None
        return LLMExperimentReflector(self.provider)

    def _ensure_llm_ready(self, allow_rule_based_fallback: bool) -> None:
        if allow_rule_based_fallback:
            return
        if self.provider is None or not self.provider.is_configured():
            raise RuntimeError(
                "Research mode is LLM-first for this challenge. Configure OPENAI_API_KEY, or set OPENAI_BASE_URL to a local OpenAI-compatible model server, or pass allow_rule_based_fallback=True only for offline smoke tests."
            )

    def _safe_propose(
        self,
        proposer,
        memory: ResearchMemory,
        benchmark_id: str,
        time_budget_ms: int,
        round_index: int,
        seed: int,
        search_space: dict[str, list[object]],
        allow_rule_based_fallback: bool,
    ) -> ExperimentSpec:
        try:
            return proposer.propose(memory, benchmark_id, time_budget_ms, round_index)
        except Exception:
            if not allow_rule_based_fallback:
                raise
            return RuleBasedProposer(seed + round_index, search_space).propose(memory, benchmark_id, time_budget_ms, round_index)

    def _safe_reflect(
        self,
        reflector: LLMExperimentReflector | None,
        benchmark_id: str,
        round_index: int,
        record: ExperimentRecord,
        incumbent_summary: BenchmarkSummary | None,
        memory: ResearchMemory,
    ) -> dict[str, object] | None:
        if reflector is None:
            return None
        try:
            return reflector.reflect(benchmark_id, round_index, record, incumbent_summary, memory)
        except Exception:
            return None


def _judge(candidate: BenchmarkSummary, incumbent: BenchmarkSummary | None) -> str:
    if incumbent is None:
        return "keep"
    return "keep" if better_score(
        candidate=LexicographicScore(
            expected_completed_orders=candidate.average_expected_completed_orders,
            total_cost=candidate.average_total_cost,
        ),
        incumbent=LexicographicScore(
            expected_completed_orders=incumbent.average_expected_completed_orders,
            total_cost=incumbent.average_total_cost,
        ),
    ) else "discard"


def _config_signature(config: SolveConfig) -> str:
    return json.dumps(config.__dict__, sort_keys=True)


def _record_to_payload(record: ExperimentRecord | None) -> dict[str, object] | None:
    if record is None:
        return None
    return {
        "experiment_id": record.experiment_id,
        "status": record.status,
        "hypothesis": record.hypothesis,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "average_expected_completed_orders": record.benchmark_summary.average_expected_completed_orders,
        "average_total_cost": record.benchmark_summary.average_total_cost,
        "total_elapsed_ms": record.benchmark_summary.total_elapsed_ms,
        "solver_config": record.solver_config.__dict__ if record.solver_config is not None else None,
        "config_signature": record.config_signature,
    }


def _benchmark_to_payload(summary: BenchmarkSummary | None) -> dict[str, object] | None:
    if summary is None:
        return None
    return {
        "benchmark_id": summary.benchmark_id,
        "average_expected_completed_orders": summary.average_expected_completed_orders,
        "average_total_cost": summary.average_total_cost,
        "total_elapsed_ms": summary.total_elapsed_ms,
    }


def _pick(search_space: dict[str, list[object]], key: str, offset: int, default: object) -> object:
    allowed = search_space.get(key, [])
    if not allowed:
        return default
    return allowed[offset % len(allowed)]


def _coerce_allowed(value: Any, allowed: list[object], default: object) -> object:
    if not allowed:
        return default
    if value in allowed:
        return value
    if isinstance(default, bool):
        return bool(value) if isinstance(value, bool) else default
    try:
        numeric_value = float(value)
        numeric_allowed = [float(item) for item in allowed]
        nearest_index = min(range(len(numeric_allowed)), key=lambda index: abs(numeric_allowed[index] - numeric_value))
        return allowed[nearest_index]
    except Exception:
        return default


def _extract_parameter_hints(response: dict[str, object]) -> dict[str, object]:
    hints: dict[str, object] = {}
    solver_config = response.get("solver_config")
    if isinstance(solver_config, dict):
        hints.update(solver_config)

    hypothesis = response.get("hypothesis")
    if isinstance(hypothesis, str):
        stripped = hypothesis.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = ast.literal_eval(stripped)
            except (SyntaxError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                hints.update(parsed)

    return hints


def _load_search_space(search_space_path: str | None) -> dict[str, list[object]]:
    if not search_space_path:
        return {key: list(values) for key, values in DEFAULT_SEARCH_SPACE.items()}
    raw = load_json(search_space_path)
    if not isinstance(raw, dict):
        raise ValueError(f"Search space at {search_space_path} must be a JSON object.")
    resolved = {key: list(values) for key, values in DEFAULT_SEARCH_SPACE.items()}
    for key, values in raw.items():
        if key in resolved and isinstance(values, list) and values:
            resolved[key] = values
    return resolved


def _load_research_state(path: Path) -> ResearchMemory:
    if not path.exists():
        return ResearchMemory()
    raw = load_json(path)
    if not isinstance(raw, dict):
        return ResearchMemory()

    history: list[ExperimentRecord] = []
    raw_history = raw.get("history", [])
    if isinstance(raw_history, list):
        for item in raw_history:
            if not isinstance(item, dict):
                continue
            history.append(
                ExperimentRecord(
                    experiment_id=str(item.get("experiment_id", "")),
                    status=str(item.get("status", "discard")),
                    hypothesis=str(item.get("hypothesis", "")),
                    benchmark_summary=BenchmarkSummary(
                        benchmark_id=str(raw.get("benchmark_id", "benchmark")),
                        case_metrics=(),
                        average_expected_completed_orders=float(item.get("average_expected_completed_orders", 0.0)),
                        average_total_cost=float(item.get("average_total_cost", 0.0)),
                        total_elapsed_ms=int(item.get("total_elapsed_ms", 0)),
                    ),
                    started_at=str(item.get("started_at", "")),
                    finished_at=str(item.get("finished_at", "")),
                    notes=str(item.get("notes", "")),
                    solver_config=_solver_config_from_raw(item.get("solver_config")),
                    config_signature=str(item.get("config_signature", "")) or None,
                )
            )

    seen_signatures = set(str(item) for item in raw.get("seen_signatures", []) if isinstance(item, str))
    if not seen_signatures:
        seen_signatures = {record.config_signature for record in history if record.config_signature}
    failed_signatures = set(str(item) for item in raw.get("failed_signatures", []) if isinstance(item, str))
    lessons = [item for item in raw.get("lessons", []) if isinstance(item, dict)]
    return ResearchMemory(seen_signatures=seen_signatures, failed_signatures=failed_signatures, history=history, lessons=lessons)


def _write_research_state(path: Path, benchmark_id: str, memory: ResearchMemory, search_space: dict[str, list[object]]) -> None:
    write_json(
        path,
        {
            "benchmark_id": benchmark_id,
            "search_space": search_space,
            "seen_signatures": sorted(memory.seen_signatures),
            "failed_signatures": sorted(memory.failed_signatures),
            "history": [_record_to_payload(record) for record in memory.history],
            "lessons": memory.lessons,
        },
    )


def _solver_config_from_raw(raw: Any) -> SolveConfig | None:
    if not isinstance(raw, dict):
        return None
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


def _best_record(history: list[ExperimentRecord]) -> ExperimentRecord | None:
    incumbent: ExperimentRecord | None = None
    for record in history:
        if record.status == "crash":
            continue
        if incumbent is None:
            incumbent = record
            continue
        candidate_score = LexicographicScore(
            expected_completed_orders=record.benchmark_summary.average_expected_completed_orders,
            total_cost=record.benchmark_summary.average_total_cost,
        )
        incumbent_score = LexicographicScore(
            expected_completed_orders=incumbent.benchmark_summary.average_expected_completed_orders,
            total_cost=incumbent.benchmark_summary.average_total_cost,
        )
        if better_score(candidate_score, incumbent_score):
            incumbent = record
    return incumbent


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]

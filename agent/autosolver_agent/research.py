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

LESSON_KEYWORDS = {
    "bundle": [
        "generate_bundles_if_missing",
        "bundle_candidate_pool_size",
        "max_bundle_size",
        "bundle_distance_threshold",
        "bundle_discount_factor",
        "bundle_acceptance_scale",
        "max_generated_bundles",
    ],
    "pool": ["bundle_candidate_pool_size", "max_generated_bundles"],
    "distance": ["bundle_distance_threshold"],
    "discount": ["bundle_discount_factor"],
    "accept": ["bundle_acceptance_scale"],
    "lns": ["lns_destroy_fraction", "lns_iterations"],
    "destroy": ["lns_destroy_fraction"],
    "iteration": ["lns_iterations"],
    "cp-sat": ["use_cpsat"],
    "cpsat": ["use_cpsat"],
    "top_k": ["top_k_riders_per_order"],
    "top-k": ["top_k_riders_per_order"],
    "rider": ["top_k_riders_per_order"],
}


@dataclass
class ResearchMemory:
    seen_signatures: set[str] = field(default_factory=set)
    failed_signatures: set[str] = field(default_factory=set)
    history: list[ExperimentRecord] = field(default_factory=list)
    lessons: list[dict[str, object]] = field(default_factory=list)


class RuleBasedProposer:
    def __init__(self, seed: int, search_space: dict[str, list[object]], benchmark_profile: dict[str, object] | None = None):
        self.randomizer = Random(seed)
        self.search_space = search_space
        self.benchmark_profile = benchmark_profile or {}

    def propose(self, memory: ResearchMemory, benchmark_id: str, time_budget_ms: int, round_index: int) -> ExperimentSpec:
        strategy_memory = _search_memory_digest(memory, self.search_space, self.benchmark_profile)
        prioritized_keys = _prioritized_search_keys(memory, self.benchmark_profile)
        base_config = _base_solver_config(memory, time_budget_ms)
        preferred_values = _preferred_value_orders(memory.history, self.search_space)
        blocked_values = _blocked_value_orders(memory.history, self.search_space)
        solver_config = base_config

        for attempt in range(48):
            solver_config = _mutated_solver_config(
                base_config=base_config,
                search_space=self.search_space,
                prioritized_keys=prioritized_keys,
                preferred_values=preferred_values,
                blocked_values=blocked_values,
                memory=memory,
                round_index=round_index,
                attempt=attempt,
                benchmark_profile=self.benchmark_profile,
            )
            if not _is_redundant_config(solver_config, memory):
                break
        else:
            for attempt in range(32):
                offset = round_index + attempt
                solver_config = SolveConfig(
                    time_budget_ms=time_budget_ms,
                    top_k_riders_per_order=int(
                        _pick_unblocked(self.search_space, "top_k_riders_per_order", offset, 3, blocked_values)
                    ),
                    use_cpsat=bool(_pick_unblocked(self.search_space, "use_cpsat", offset, True, blocked_values)),
                    use_lns=True,
                    generate_bundles_if_missing=bool(
                        _pick_unblocked(self.search_space, "generate_bundles_if_missing", offset, True, blocked_values)
                    ),
                    bundle_candidate_pool_size=int(
                        _pick_unblocked(self.search_space, "bundle_candidate_pool_size", offset, 6, blocked_values)
                    ),
                    max_bundle_size=int(_pick_unblocked(self.search_space, "max_bundle_size", offset, 3, blocked_values)),
                    bundle_distance_threshold=float(
                        _pick_unblocked(self.search_space, "bundle_distance_threshold", offset, 2.5, blocked_values)
                    ),
                    bundle_discount_factor=float(
                        _pick_unblocked(self.search_space, "bundle_discount_factor", offset, 0.92, blocked_values)
                    ),
                    bundle_acceptance_scale=float(
                        _pick_unblocked(self.search_space, "bundle_acceptance_scale", offset, 0.95, blocked_values)
                    ),
                    max_generated_bundles=int(
                        _pick_unblocked(self.search_space, "max_generated_bundles", offset, 64, blocked_values)
                    ),
                    lns_destroy_fraction=float(
                        _pick_unblocked(self.search_space, "lns_destroy_fraction", offset, 0.25, blocked_values)
                    ),
                    lns_iterations=int(_pick_unblocked(self.search_space, "lns_iterations", offset, 24, blocked_values)),
                )
                if _config_signature(solver_config) not in memory.seen_signatures:
                    break
        return ExperimentSpec(
            experiment_id=f"exp-{round_index + 1}",
            name=f"rule-based-{round_index + 1}",
            hypothesis=(
                "Adjust top_k, bundle generation, and local search neighborhood "
                f"for benchmark {benchmark_id} under regimes {strategy_memory['regime_tags']} "
                f"with config {_config_signature(solver_config)}."
            ),
            solver_config=solver_config,
            benchmark_ids=(benchmark_id,),
            notes="fallback-proposer",
        )


class LLMExperimentProposer:
    def __init__(self, provider: LLMProvider, search_space: dict[str, list[object]], benchmark_profile: dict[str, object]):
        self.provider = provider
        self.search_space = search_space
        self.benchmark_profile = benchmark_profile
        self.system_prompt = Path(__file__).with_name("prompts").joinpath("research_system.md").read_text(encoding="utf-8")

    def propose(self, memory: ResearchMemory, benchmark_id: str, time_budget_ms: int, round_index: int) -> ExperimentSpec:
        strategy_memory = _search_memory_digest(memory, self.search_space, self.benchmark_profile)
        prioritized_keys = _prioritized_search_keys(memory, self.benchmark_profile)
        base_config = _base_solver_config(memory, time_budget_ms)
        history = [
            {
                "experiment_id": record.experiment_id,
                "status": record.status,
                "hypothesis": record.hypothesis,
                "average_expected_completed_orders": record.benchmark_summary.average_expected_completed_orders,
                "average_total_cost": record.benchmark_summary.average_total_cost,
                "solver_config": record.solver_config.__dict__ if record.solver_config is not None else None,
                "config_signature": record.config_signature,
            }
            for record in memory.history[-5:]
        ]
        incumbent_record = _best_record(memory.history)
        parameter_insights = _parameter_value_insights(memory.history, self.search_space)
        preferred_values = _preferred_value_orders(memory.history, self.search_space)
        blocked_values = _blocked_value_orders(memory.history, self.search_space)
        response = self.provider.complete_json(
            self.system_prompt,
            json.dumps(
                {
                    "round_index": round_index,
                    "benchmark_id": benchmark_id,
                    "time_budget_ms": time_budget_ms,
                    "search_space": self.search_space,
                    "benchmark_profile": self.benchmark_profile,
                    "search_hints": {
                        "priority_knobs": prioritized_keys[:6],
                    },
                    "parameter_insights": parameter_insights,
                    "strategy_memory": strategy_memory,
                    "incumbent": _record_to_payload(incumbent_record),
                    "history": history,
                    "lessons": memory.lessons[-5:],
                    "seen_signatures": sorted(memory.seen_signatures)[-8:],
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
        solver_config, repair_reasons = _repair_proposed_solver_config(
            config=solver_config,
            base_config=base_config,
            search_space=self.search_space,
            prioritized_keys=prioritized_keys,
            preferred_values=preferred_values,
            blocked_values=blocked_values,
            memory=memory,
            round_index=round_index,
            benchmark_profile=self.benchmark_profile,
        )
        notes = "llm-proposer"
        if repair_reasons:
            notes = f"{notes}:{'+'.join(repair_reasons)}"
        return ExperimentSpec(
            experiment_id=str(resolved_response.get("experiment_id", f"llm-exp-{round_index + 1}")),
            name=str(resolved_response.get("name", f"llm-proposal-{round_index + 1}")),
            hypothesis=str(resolved_response.get("hypothesis", "Explore solver parameter combinations.")),
            solver_config=solver_config,
            benchmark_ids=(benchmark_id,),
            notes=notes,
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
        benchmark_profile = _summarize_benchmark_cases(cases)
        if not resume:
            events_source = Path(events_path)
            if events_source.exists():
                events_source.unlink()
        event_writer = EventWriter(events_path, replay_output_path=dashboard_output_path)
        search_space = _load_search_space(search_space_path)
        resolved_state_path = Path(state_path) if state_path else Path(output_path).with_suffix(".state.json")
        memory = _load_research_state(resolved_state_path) if resume else ResearchMemory()
        self._ensure_llm_ready(allow_rule_based_fallback)
        proposer = self._build_proposer(seed, search_space, benchmark_profile, allow_rule_based_fallback)
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
                "benchmark_profile": benchmark_profile,
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
            previous_incumbent_summary = incumbent_summary
            spec = self._safe_propose(
                proposer,
                memory,
                benchmark_id,
                time_budget_ms,
                round_index,
                seed,
                search_space,
                benchmark_profile,
                allow_rule_based_fallback,
            )
            signature = _config_signature(spec.solver_config)
            if signature in memory.seen_signatures:
                spec = RuleBasedProposer(seed + round_index + 1, search_space, benchmark_profile).propose(
                    memory,
                    benchmark_id,
                    time_budget_ms,
                    round_index + 10,
                )
                signature = _config_signature(spec.solver_config)

            memory.seen_signatures.add(signature)
            proposal_event_type = _proposal_event_type(spec.notes)
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
                status = _judge(summary, previous_incumbent_summary)
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
            if status != "keep":
                memory.failed_signatures.add(signature)
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

            reflection = self._safe_reflect(reflector, benchmark_id, round_index, record, previous_incumbent_summary, memory)
            if reflection is None:
                reflection = _heuristic_reflection(previous_incumbent_summary, record)
                reflection_event_type = "research.heuristic_reflection"
            else:
                reflection_event_type = "research.llm_reflection"
            if reflection is not None:
                memory.lessons.append(reflection)
                event_writer.write(
                    reflection_event_type,
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
            "benchmark_profile": benchmark_profile,
            "search_space": search_space,
            "strategy_memory": _search_memory_digest(memory, search_space, benchmark_profile),
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

    def _build_proposer(
        self,
        seed: int,
        search_space: dict[str, list[object]],
        benchmark_profile: dict[str, object],
        allow_rule_based_fallback: bool,
    ):
        if self.provider is None or not self.provider.is_configured():
            if not allow_rule_based_fallback:
                raise RuntimeError("Research mode requires a configured LLM provider unless allow_rule_based_fallback=True.")
            return RuleBasedProposer(seed, search_space, benchmark_profile)
        return LLMExperimentProposer(self.provider, search_space, benchmark_profile)

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
        benchmark_profile: dict[str, object],
        allow_rule_based_fallback: bool,
    ) -> ExperimentSpec:
        try:
            return proposer.propose(memory, benchmark_id, time_budget_ms, round_index)
        except Exception:
            if not allow_rule_based_fallback:
                raise
            return RuleBasedProposer(seed + round_index, search_space, benchmark_profile).propose(
                memory,
                benchmark_id,
                time_budget_ms,
                round_index,
            )

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
    candidate_score = LexicographicScore(
        expected_completed_orders=candidate.average_expected_completed_orders,
        total_cost=candidate.average_total_cost,
    )
    incumbent_score = LexicographicScore(
        expected_completed_orders=incumbent.average_expected_completed_orders,
        total_cost=incumbent.average_total_cost,
    )
    if better_score(candidate_score, incumbent_score):
        return "keep"
    if better_score(incumbent_score, candidate_score):
        return "discard"
    elapsed_gap_ms = incumbent.total_elapsed_ms - candidate.total_elapsed_ms
    return "keep" if elapsed_gap_ms >= 10 else "discard"


def _proposal_event_type(notes: str) -> str:
    return "research.llm_proposal" if notes.startswith("llm-proposer") else "research.fallback_proposal"


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
        "notes": record.notes,
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


def _pick_unblocked(
    search_space: dict[str, list[object]],
    key: str,
    offset: int,
    default: object,
    blocked_values: dict[str, list[object]],
) -> object:
    allowed = search_space.get(key, [])
    if not allowed:
        return default
    candidates = [value for value in allowed if value not in blocked_values.get(key, [])]
    if not candidates:
        candidates = allowed
    return candidates[offset % len(candidates)]


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
    for key in ("solver_config", "config", "parameters", "recommended_config"):
        value = response.get(key)
        if isinstance(value, dict):
            hints.update(value)
        elif isinstance(value, str):
            for parsed in _extract_dicts_from_text(value):
                hints.update(parsed)

    for key in ("hypothesis", "notes", "summary"):
        value = response.get(key)
        if isinstance(value, str):
            for parsed in _extract_dicts_from_text(value):
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


def _extract_dicts_from_text(text: str) -> list[dict[str, object]]:
    parsed_dicts: list[dict[str, object]] = []
    stack_depth = 0
    start_index: int | None = None

    for index, char in enumerate(text):
        if char == "{":
            if stack_depth == 0:
                start_index = index
            stack_depth += 1
        elif char == "}":
            if stack_depth == 0 or start_index is None:
                continue
            stack_depth -= 1
            if stack_depth == 0:
                snippet = text[start_index : index + 1]
                parsed = _parse_dict_like_text(snippet)
                if parsed is not None:
                    parsed_dicts.append(parsed)
                start_index = None

    return parsed_dicts


def _parse_dict_like_text(snippet: str) -> dict[str, object] | None:
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(snippet)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _base_solver_config(memory: ResearchMemory, time_budget_ms: int) -> SolveConfig:
    incumbent_record = _best_record(memory.history)
    if incumbent_record is not None and incumbent_record.status == "keep" and incumbent_record.solver_config is not None:
        incumbent_config = incumbent_record.solver_config.__dict__.copy()
        incumbent_config["time_budget_ms"] = time_budget_ms
        return SolveConfig(**incumbent_config)
    return SolveConfig(time_budget_ms=time_budget_ms)


def _prioritized_search_keys(memory: ResearchMemory, benchmark_profile: dict[str, object]) -> list[str]:
    scores = {key: 0 for key in DEFAULT_SEARCH_SPACE}
    recent_lessons = memory.lessons[-5:]

    for lesson in recent_lessons:
        text_parts: list[str] = []
        for key in ("summary", "keep_reason"):
            value = lesson.get(key)
            if isinstance(value, str):
                text_parts.append(value.lower())
        for key in ("risks", "next_focus", "avoid_patterns"):
            value = lesson.get(key)
            if isinstance(value, list):
                text_parts.extend(str(item).lower() for item in value)

        for text in text_parts:
            for keyword, keys in LESSON_KEYWORDS.items():
                if keyword in text:
                    for config_key in keys:
                        scores[config_key] += 2

    avg_orders = float(benchmark_profile.get("avg_orders", 0.0))
    orders_per_rider = float(benchmark_profile.get("orders_per_rider", 0.0))
    avg_bundle_candidates = float(benchmark_profile.get("avg_bundle_candidates", 0.0))

    if orders_per_rider > 2.2:
        for key in ("top_k_riders_per_order", "generate_bundles_if_missing", "bundle_candidate_pool_size", "max_generated_bundles"):
            scores[key] += 2
    if avg_bundle_candidates < 1.0:
        for key in ("generate_bundles_if_missing", "max_generated_bundles", "bundle_candidate_pool_size", "max_bundle_size"):
            scores[key] += 2
    if avg_orders >= 36:
        for key in ("use_cpsat", "lns_iterations", "bundle_candidate_pool_size"):
            scores[key] += 1

    history_with_configs = [record for record in memory.history if record.solver_config is not None]
    for key, allowed in DEFAULT_SEARCH_SPACE.items():
        if not allowed:
            continue
        tried_values = {record.solver_config.__dict__.get(key) for record in history_with_configs}
        missing_count = max(0, len(allowed) - len(tried_values))
        if missing_count == 0:
            continue
        scores[key] += 1 if not tried_values else min(2, missing_count)

    return sorted(DEFAULT_SEARCH_SPACE.keys(), key=lambda key: (-scores[key], key))


def _parameter_value_insights(history: list[ExperimentRecord], search_space: dict[str, list[object]]) -> dict[str, list[dict[str, object]]]:
    insights: dict[str, list[dict[str, object]]] = {}
    for key, allowed in search_space.items():
        rows: list[dict[str, object]] = []
        for value in allowed:
            matching_records = [
                record
                for record in history
                if record.solver_config is not None and record.solver_config.__dict__.get(key) == value
            ]
            if not matching_records:
                continue
            keep_count = sum(1 for record in matching_records if record.status == "keep")
            discard_count = sum(1 for record in matching_records if record.status == "discard")
            crash_count = sum(1 for record in matching_records if record.status == "crash")
            avg_expected = sum(record.benchmark_summary.average_expected_completed_orders for record in matching_records) / len(matching_records)
            avg_cost = sum(record.benchmark_summary.average_total_cost for record in matching_records) / len(matching_records)
            rows.append(
                {
                    "value": value,
                    "runs": len(matching_records),
                    "keep_count": keep_count,
                    "discard_count": discard_count,
                    "crash_count": crash_count,
                    "avg_expected_completed_orders": round(avg_expected, 6),
                    "avg_total_cost": round(avg_cost, 6),
                }
            )
        if rows:
            rows.sort(
                key=lambda row: (
                    -int(row["keep_count"]),
                    int(row["crash_count"]) + int(row["discard_count"]),
                    -float(row["avg_expected_completed_orders"]),
                    float(row["avg_total_cost"]),
                )
            )
            insights[key] = rows
    return insights


def _preferred_value_orders(history: list[ExperimentRecord], search_space: dict[str, list[object]]) -> dict[str, list[object]]:
    insights = _parameter_value_insights(history, search_space)
    return {
        key: [row["value"] for row in rows]
        for key, rows in insights.items()
    }


def _dominant_bad_values(history: list[ExperimentRecord], search_space: dict[str, list[object]]) -> dict[str, list[object]]:
    insights = _parameter_value_insights(history, search_space)
    dominant_bad_values: dict[str, list[object]] = {}
    for key, rows in insights.items():
        losers = [
            row["value"]
            for row in rows
            if int(row["keep_count"]) == 0 and (int(row["discard_count"]) + int(row["crash_count"]) >= 2 or int(row["crash_count"]) >= 1)
        ]
        if losers:
            dominant_bad_values[key] = losers
    return dominant_bad_values


def _blocked_value_orders(history: list[ExperimentRecord], search_space: dict[str, list[object]]) -> dict[str, list[object]]:
    dominant_bad_values = _dominant_bad_values(history, search_space)
    blocked: dict[str, list[object]] = {}
    for key, bad_values in dominant_bad_values.items():
        allowed = search_space.get(key, [])
        alternatives = [value for value in allowed if value not in bad_values]
        if alternatives:
            blocked[key] = bad_values
    return blocked


def _search_memory_digest(
    memory: ResearchMemory,
    search_space: dict[str, list[object]],
    benchmark_profile: dict[str, object],
) -> dict[str, object]:
    insights = _parameter_value_insights(memory.history, search_space)
    prioritized_keys = _prioritized_search_keys(memory, benchmark_profile)
    blocked_values = _blocked_value_orders(memory.history, search_space)
    incumbent = _best_record(memory.history)
    incumbent_raw = incumbent.solver_config.__dict__ if incumbent is not None and incumbent.solver_config is not None else None

    stable_values: list[dict[str, object]] = []
    for key, rows in insights.items():
        top_row = rows[0]
        if int(top_row["keep_count"]) <= 0:
            continue
        stable_values.append(
            {
                "key": key,
                "value": top_row["value"],
                "keep_count": top_row["keep_count"],
                "avg_expected_completed_orders": top_row["avg_expected_completed_orders"],
                "avg_total_cost": top_row["avg_total_cost"],
            }
        )
    stable_values.sort(
        key=lambda row: (
            -int(row["keep_count"]),
            -float(row["avg_expected_completed_orders"]),
            float(row["avg_total_cost"]),
            str(row["key"]),
        )
    )

    risky_values = [
        {
            "key": key,
            "values": list(values),
            "reason": "repeated_discards_or_crashes",
        }
        for key, values in blocked_values.items()
    ]

    exploration_gaps: list[dict[str, object]] = []
    for key, allowed in search_space.items():
        tried_values = []
        for value in allowed:
            if any(record.solver_config is not None and record.solver_config.__dict__.get(key) == value for record in memory.history):
                tried_values.append(value)
        missing_values = [value for value in allowed if value not in tried_values]
        if not missing_values:
            continue
        exploration_gaps.append(
            {
                "key": key,
                "priority_rank": prioritized_keys.index(key) + 1 if key in prioritized_keys else len(prioritized_keys) + 1,
                "tried_count": len(tried_values),
                "total_values": len(allowed),
                "missing_values": missing_values,
            }
        )
    exploration_gaps.sort(key=lambda row: (int(row["priority_rank"]), int(row["tried_count"]), str(row["key"])))

    recent_failures: list[dict[str, object]] = []
    for record in reversed(memory.history[-6:]):
        if record.status == "keep" or record.solver_config is None:
            continue
        changed_keys = []
        if incumbent_raw is not None:
            changed_keys = [
                key
                for key in sorted(record.solver_config.__dict__)
                if record.solver_config.__dict__.get(key) != incumbent_raw.get(key)
            ]
        recent_failures.append(
            {
                "experiment_id": record.experiment_id,
                "status": record.status,
                "changed_keys_vs_incumbent": changed_keys[:6],
                "average_expected_completed_orders": round(record.benchmark_summary.average_expected_completed_orders, 6),
                "average_total_cost": round(record.benchmark_summary.average_total_cost, 6),
            }
        )

    return {
        "regime_tags": _regime_tags(benchmark_profile),
        "priority_knobs": prioritized_keys[:6],
        "stable_values": stable_values[:6],
        "risky_values": risky_values[:6],
        "exploration_gaps": exploration_gaps[:6],
        "recent_failures": recent_failures[:4],
        "stagnating": _high_stagnation(memory),
    }


def _is_redundant_config(config: SolveConfig, memory: ResearchMemory) -> bool:
    signature = _config_signature(config)
    if signature in memory.seen_signatures:
        return True

    if _high_stagnation(memory):
        return False

    candidate_raw = config.__dict__
    for record in reversed(memory.history[-8:]):
        if record.solver_config is None:
            continue
        distance = _config_distance(candidate_raw, record.solver_config.__dict__)
        if record.status != "keep" and distance <= 1:
            return True
        if record.status == "crash" and distance <= 2:
            return True
    return False


def _config_distance(left: dict[str, object], right: dict[str, object]) -> int:
    keys = sorted(set(left) | set(right))
    return sum(1 for key in keys if left.get(key) != right.get(key))


def _high_stagnation(memory: ResearchMemory) -> bool:
    recent = memory.history[-3:]
    if len(recent) < 3:
        return False
    return all(record.status != "keep" for record in recent)


def _repair_proposed_solver_config(
    config: SolveConfig,
    base_config: SolveConfig,
    search_space: dict[str, list[object]],
    prioritized_keys: list[str],
    preferred_values: dict[str, list[object]],
    blocked_values: dict[str, list[object]],
    memory: ResearchMemory,
    round_index: int,
    benchmark_profile: dict[str, object],
) -> tuple[SolveConfig, list[str]]:
    repair_reasons: list[str] = []
    dominant_bad_values = _dominant_bad_values(memory.history, search_space)
    raw = config.__dict__.copy()

    for key, bad_values in dominant_bad_values.items():
        if raw.get(key) in bad_values:
            allowed = search_space.get(key, [])
            raw[key] = _guided_value_choice(
                raw.get(key),
                allowed,
                preferred_values.get(key, []),
                round_index + len(repair_reasons) + 1,
                blocked_values.get(key, []),
            )
            repair_reasons.append(f"avoid_{key}")

    repaired = SolveConfig(**raw)
    if not _is_redundant_config(repaired, memory):
        return repaired, repair_reasons

    repair_reasons.append("novelty")
    focus_keys = _repair_focus_keys(repaired.__dict__, dominant_bad_values, prioritized_keys)
    seed_configs = [repaired, base_config]

    for attempt in range(24):
        seed_config = seed_configs[attempt % len(seed_configs)]
        candidate_raw = seed_config.__dict__.copy()
        width = min(len(focus_keys), 1 + ((round_index + attempt) % 2)) if focus_keys else 1
        rotation = (round_index + attempt) % max(1, len(focus_keys))
        ordered_keys = focus_keys[rotation:] + focus_keys[:rotation] if focus_keys else list(DEFAULT_SEARCH_SPACE.keys())
        for key_index, key in enumerate(ordered_keys[:width]):
            allowed = search_space.get(key, [])
            candidate_raw[key] = _guided_value_choice(
                candidate_raw.get(key),
                allowed,
                preferred_values.get(key, []),
                round_index + attempt + key_index + 1,
                blocked_values.get(key, []),
            )
        _apply_benchmark_biases(candidate_raw, benchmark_profile, memory)
        candidate = SolveConfig(**candidate_raw)
        if not _is_redundant_config(candidate, memory):
            return candidate, repair_reasons

    return repaired, repair_reasons


def _repair_focus_keys(
    raw_config: dict[str, object],
    dominant_bad_values: dict[str, list[object]],
    prioritized_keys: list[str],
) -> list[str]:
    focused = [
        key
        for key, bad_values in dominant_bad_values.items()
        if raw_config.get(key) in bad_values
    ]
    focused.extend(key for key in prioritized_keys if key not in focused)
    if focused:
        return focused
    return list(DEFAULT_SEARCH_SPACE.keys())


def _heuristic_reflection(incumbent_summary: BenchmarkSummary | None, record: ExperimentRecord) -> dict[str, object]:
    summary_score = LexicographicScore(
        expected_completed_orders=record.benchmark_summary.average_expected_completed_orders,
        total_cost=record.benchmark_summary.average_total_cost,
    )
    if incumbent_summary is None:
        return {
            "summary": "首轮实验建立了基线，可继续围绕当前配置做局部搜索。",
            "keep_reason": "当前没有 incumbent，因此这轮结果会成为后续比较基线。",
            "risks": ["当前启发式反思没有使用外部 LLM，描述粒度较粗。"],
            "next_focus": ["围绕当前配置的小范围变异继续探索。"],
            "avoid_patterns": ["避免重复完全相同的配置。"],
        }

    incumbent_score = LexicographicScore(
        expected_completed_orders=incumbent_summary.average_expected_completed_orders,
        total_cost=incumbent_summary.average_total_cost,
    )
    improved = better_score(summary_score, incumbent_score)
    delta_expected = record.benchmark_summary.average_expected_completed_orders - incumbent_summary.average_expected_completed_orders
    delta_cost = record.benchmark_summary.average_total_cost - incumbent_summary.average_total_cost
    if improved:
        return {
            "summary": (
                f"这轮启发式上优于旧 incumbent，预计完单提升 {delta_expected:.3f}，"
                f"成本变化 {delta_cost:.3f}。"
            ),
            "keep_reason": "主目标提升，因此值得保留为新的搜索锚点。",
            "risks": ["收益来源还需要更多轮验证，避免把一次性波动当成稳定趋势。"],
            "next_focus": ["围绕这组参数做 1 到 2 个旋钮的小步变异。"],
            "avoid_patterns": ["不要一下子同时改太多参数，避免难以定位收益来源。"],
        }

    return {
        "summary": (
            f"这轮没有超过 incumbent，预计完单变化 {delta_expected:.3f}，"
            f"成本变化 {delta_cost:.3f}。"
        ),
        "keep_reason": "主目标没有变好，应继续沿其它参数方向探索。",
        "risks": ["如果连续多轮都不提升，当前搜索方向可能已经接近瓶颈。"],
        "next_focus": ["优先调整 lesson 里提到的高影响旋钮。"],
        "avoid_patterns": ["避免和最近被 discard 的配置只差一个低价值参数。"],
    }


def _mutated_solver_config(
    base_config: SolveConfig,
    search_space: dict[str, list[object]],
    prioritized_keys: list[str],
    preferred_values: dict[str, list[object]],
    blocked_values: dict[str, list[object]],
    memory: ResearchMemory,
    round_index: int,
    attempt: int,
    benchmark_profile: dict[str, object],
) -> SolveConfig:
    raw = base_config.__dict__.copy()
    mutation_width = _adaptive_mutation_width(memory, round_index, attempt)
    if prioritized_keys:
        rotation = (round_index * 3 + attempt) % len(prioritized_keys)
        ordered_keys = prioritized_keys[rotation:] + prioritized_keys[:rotation]
    else:
        ordered_keys = list(DEFAULT_SEARCH_SPACE.keys())

    chosen_keys = ordered_keys[:mutation_width]
    for key_index, key in enumerate(chosen_keys):
        allowed = search_space.get(key, [])
        preferred_for_key = preferred_values.get(key, [])
        raw[key] = _guided_value_choice(
            raw.get(key),
            allowed,
            preferred_for_key,
            round_index + attempt + key_index,
            blocked_values.get(key, []),
        )

    _apply_benchmark_biases(raw, benchmark_profile, memory)

    return SolveConfig(**raw)


def _adaptive_mutation_width(memory: ResearchMemory, round_index: int, attempt: int) -> int:
    if _high_stagnation(memory):
        return 3
    if any(record.status == "keep" for record in memory.history):
        return 1 + ((round_index + attempt) % 2)
    return 2 + ((round_index + attempt) % 2)


def _apply_benchmark_biases(raw: dict[str, object], benchmark_profile: dict[str, object], memory: ResearchMemory) -> None:
    orders_per_rider = float(benchmark_profile.get("orders_per_rider", 0.0))
    avg_bundle_candidates = float(benchmark_profile.get("avg_bundle_candidates", 0.0))
    if orders_per_rider > 2.2:
        raw["top_k_riders_per_order"] = max(int(raw.get("top_k_riders_per_order", 1)), 2)
    if avg_bundle_candidates < 1.0:
        raw["generate_bundles_if_missing"] = True
        raw["max_generated_bundles"] = max(int(raw.get("max_generated_bundles", 16)), 32)
    if _high_stagnation(memory):
        raw["use_cpsat"] = True
        raw["lns_iterations"] = max(int(raw.get("lns_iterations", 8)), 16)
        raw["bundle_candidate_pool_size"] = max(int(raw.get("bundle_candidate_pool_size", 4)), 6)


def _regime_tags(benchmark_profile: dict[str, object]) -> list[str]:
    tags: list[str] = []
    avg_orders = float(benchmark_profile.get("avg_orders", 0.0))
    orders_per_rider = float(benchmark_profile.get("orders_per_rider", 0.0))
    avg_bundle_candidates = float(benchmark_profile.get("avg_bundle_candidates", 0.0))
    avg_match_density = float(benchmark_profile.get("avg_match_density", 0.0))

    if orders_per_rider > 2.2:
        tags.append("rider_constrained")
    if avg_bundle_candidates < 1.0:
        tags.append("bundle_sparse")
    elif avg_bundle_candidates >= 8.0:
        tags.append("bundle_rich")
    if avg_match_density < 0.7:
        tags.append("match_sparse")
    elif avg_match_density >= 0.95:
        tags.append("match_dense")
    if avg_orders >= 36:
        tags.append("large_instances")

    return tags or ["balanced"]


def _neighbor_value(current: object, allowed: list[object], offset: int) -> object:
    if not allowed:
        return current
    if current not in allowed:
        return allowed[offset % len(allowed)]
    if len(allowed) == 1:
        return allowed[0]

    current_index = allowed.index(current)
    candidate_indexes: list[int] = []
    for step in range(1, len(allowed)):
        left_index = current_index - step
        right_index = current_index + step
        if left_index >= 0:
            candidate_indexes.append(left_index)
        if right_index < len(allowed):
            candidate_indexes.append(right_index)

    if not candidate_indexes:
        return current
    return allowed[candidate_indexes[offset % len(candidate_indexes)]]


def _guided_value_choice(
    current: object,
    allowed: list[object],
    preferred_values: list[object],
    offset: int,
    blocked_values: list[object] | None = None,
) -> object:
    if not allowed:
        return current

    blocked_set = set(blocked_values or [])
    candidate_pool = [value for value in allowed if value not in blocked_set]
    if not candidate_pool:
        candidate_pool = list(allowed)

    ranked_candidates: list[object] = []
    for value in preferred_values:
        if value in candidate_pool and value != current and value not in ranked_candidates:
            ranked_candidates.append(value)

    if current in candidate_pool:
        ranked_candidates.extend(
            value for value in [_neighbor_value(current, candidate_pool, offset + step) for step in range(len(candidate_pool))]
            if value != current and value not in ranked_candidates
        )

    ranked_candidates.extend(value for value in candidate_pool if value != current and value not in ranked_candidates)
    if not ranked_candidates:
        return current if current in candidate_pool else candidate_pool[offset % len(candidate_pool)]
    return ranked_candidates[offset % len(ranked_candidates)]


def _summarize_benchmark_cases(cases: list[object]) -> dict[str, object]:
    if not cases:
        return _empty_benchmark_profile()

    order_counts: list[int] = []
    rider_counts: list[int] = []
    bundle_counts: list[int] = []
    match_density_values: list[float] = []
    weighted_case_ids: list[dict[str, object]] = []

    for case in cases:
        instance = case.instance
        order_count = len(instance.orders)
        rider_count = len(instance.riders)
        bundle_count = len(instance.bundle_candidates)
        match_count = len(instance.match_scores)
        density_denominator = max(1, order_count * max(1, rider_count))
        match_density = match_count / density_denominator

        order_counts.append(order_count)
        rider_counts.append(rider_count)
        bundle_counts.append(bundle_count)
        match_density_values.append(match_density)
        weighted_case_ids.append({"case_id": case.case_id, "weight": case.weight})

    avg_orders = sum(order_counts) / len(order_counts)
    avg_riders = sum(rider_counts) / len(rider_counts)
    return {
        "case_count": len(cases),
        "avg_orders": round(avg_orders, 3),
        "avg_riders": round(avg_riders, 3),
        "orders_per_rider": round(avg_orders / max(1.0, avg_riders), 3),
        "avg_bundle_candidates": round(sum(bundle_counts) / len(bundle_counts), 3),
        "avg_match_density": round(sum(match_density_values) / len(match_density_values), 4),
        "weighted_cases": weighted_case_ids,
    }


def _empty_benchmark_profile() -> dict[str, object]:
    return {
        "case_count": 0,
        "avg_orders": 0.0,
        "avg_riders": 0.0,
        "orders_per_rider": 0.0,
        "avg_bundle_candidates": 0.0,
        "avg_match_density": 0.0,
        "weighted_cases": [],
    }

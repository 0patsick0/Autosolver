from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

JsonPrimitive = str | int | float | bool | None
JsonValue = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lng: float


@dataclass(frozen=True)
class Order:
    id: str
    pickup: GeoPoint | None = None
    dropoff: GeoPoint | None = None
    ready_ts: int | None = None
    due_ts: int | None = None
    attributes: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class Rider:
    id: str
    capacity: int = 1
    location: GeoPoint | None = None
    attributes: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class MatchScore:
    order_id: str
    rider_id: str
    accept_prob: float
    cost_score: float
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class BundleCandidate:
    id: str
    order_ids: tuple[str, ...]
    rider_id: str
    accept_prob: float
    cost_score: float
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class BusinessConstraints:
    allow_reject: bool = True
    allow_multi_assign: bool = True
    allow_bundles: bool = True
    max_riders_per_order: int = 3
    generated_bundle_limit: int = 128


@dataclass(frozen=True)
class CanonicalInstance:
    instance_id: str
    orders: tuple[Order, ...]
    riders: tuple[Rider, ...]
    match_scores: tuple[MatchScore, ...]
    bundle_candidates: tuple[BundleCandidate, ...] = ()
    constraints: BusinessConstraints = field(default_factory=BusinessConstraints)
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def order_map(self) -> dict[str, Order]:
        return {order.id: order for order in self.orders}

    def rider_map(self) -> dict[str, Rider]:
        return {rider.id: rider for rider in self.riders}

    def match_map(self) -> dict[tuple[str, str], MatchScore]:
        return {(match.order_id, match.rider_id): match for match in self.match_scores}


CandidateKind = Literal["single", "multi_assign", "bundle"]
CapacityConsumptionMode = Literal["dispatch", "orders"]
SolveStatus = Literal["ok", "infeasible", "error"]
ExperimentStatus = Literal["keep", "discard", "crash"]


@dataclass(frozen=True)
class CandidateOption:
    id: str
    kind: CandidateKind
    order_ids: tuple[str, ...]
    rider_ids: tuple[str, ...]
    expected_completed_orders: float
    acceptance_prob: float
    total_cost: float
    source: str
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderDispatch:
    order_id: str
    rider_ids: tuple[str, ...]
    accepted_probability: float
    total_cost_share: float
    option_id: str
    bundle_id: str | None = None


@dataclass(frozen=True)
class LexicographicScore:
    expected_completed_orders: float
    total_cost: float

    def as_tuple(self) -> tuple[float, float]:
        return (self.expected_completed_orders, self.total_cost)


@dataclass(frozen=True)
class SolveConfig:
    time_budget_ms: int = 10_000
    top_k_riders_per_order: int = 3
    use_cpsat: bool = True
    use_lns: bool = True
    cpsat_max_orders: int = 120
    generate_bundles_if_missing: bool = True
    max_generated_bundles: int = 64
    bundle_candidate_pool_size: int = 6
    max_bundle_size: int = 3
    bundle_distance_threshold: float = 2.5
    bundle_discount_factor: float = 0.92
    bundle_acceptance_scale: float = 0.95
    capacity_consumption_mode: CapacityConsumptionMode = "dispatch"
    cpsat_quick_pass_ms: int = 160
    cpsat_full_pass_ratio: float = 0.7
    lns_destroy_fraction: float = 0.25
    lns_iterations: int = 24
    lns_restarts: int = 2
    allow_llm_baseline: bool = False


@dataclass(frozen=True)
class SolveResult:
    instance_id: str
    solver_name: str
    status: SolveStatus
    objective: LexicographicScore
    selected_option_ids: tuple[str, ...]
    dispatches: tuple[OrderDispatch, ...]
    unmatched_order_ids: tuple[str, ...]
    elapsed_ms: int
    stats: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    instance: CanonicalInstance
    source_path: str = ""
    weight: float = 1.0


@dataclass(frozen=True)
class BenchmarkCaseMetric:
    instance_id: str
    expected_completed_orders: float
    total_cost: float
    elapsed_ms: int
    solver_name: str
    status: str
    case_id: str | None = None
    source_path: str | None = None
    weight: float = 1.0
    seed: int | None = None


@dataclass(frozen=True)
class BenchmarkSummary:
    benchmark_id: str
    case_metrics: tuple[BenchmarkCaseMetric, ...]
    average_expected_completed_orders: float
    average_total_cost: float
    total_elapsed_ms: int
    incumbent_experiment_id: str | None = None
    total_weight: float = 0.0
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    name: str
    hypothesis: str
    solver_config: SolveConfig
    benchmark_ids: tuple[str, ...]
    stop_after_rounds: int = 1
    notes: str = ""


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_id: str
    status: ExperimentStatus
    hypothesis: str
    benchmark_summary: BenchmarkSummary
    started_at: str
    finished_at: str
    notes: str = ""
    solver_config: SolveConfig | None = None
    config_signature: str | None = None


@dataclass(frozen=True)
class Event:
    ts: str
    type: str
    payload: dict[str, JsonValue]


ValidationSeverity = Literal["error", "warning"]


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: ValidationSeverity = "error"
    context: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationReport:
    instance_id: str
    is_valid: bool
    issue_count: int
    covered_order_count: int
    rider_usage: dict[str, int]
    recomputed_objective: LexicographicScore
    issues: tuple[ValidationIssue, ...]


def dataclass_to_dict(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, tuple):
        return [dataclass_to_dict(item) for item in value]
    if isinstance(value, list):
        return [dataclass_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: dataclass_to_dict(item) for key, item in value.items()}
    return value


def sanitize_json_value(value: Any) -> Any:
    materialized = dataclass_to_dict(value)
    if isinstance(materialized, float):
        return materialized if math.isfinite(materialized) else None
    if isinstance(materialized, list):
        return [sanitize_json_value(item) for item in materialized]
    if isinstance(materialized, tuple):
        return [sanitize_json_value(item) for item in materialized]
    if isinstance(materialized, dict):
        return {key: sanitize_json_value(item) for key, item in materialized.items()}
    return materialized

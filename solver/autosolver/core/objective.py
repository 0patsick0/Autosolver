from __future__ import annotations

import math
from autosolver.core.models import LexicographicScore


def aggregate_acceptance_probability(probabilities: list[float] | tuple[float, ...]) -> float:
    clipped = [min(1.0, max(0.0, probability)) for probability in probabilities]
    return 1.0 - math.prod(1.0 - probability for probability in clipped)


def better_score(candidate: LexicographicScore, incumbent: LexicographicScore, epsilon: float = 1e-9) -> bool:
    if candidate.expected_completed_orders > incumbent.expected_completed_orders + epsilon:
        return True
    if incumbent.expected_completed_orders > candidate.expected_completed_orders + epsilon:
        return False
    return candidate.total_cost + epsilon < incumbent.total_cost


def objective_key(score: LexicographicScore) -> tuple[float, float]:
    return (-score.expected_completed_orders, score.total_cost)

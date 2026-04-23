from __future__ import annotations

from pathlib import Path

from autosolver.core.objective import aggregate_acceptance_probability, better_score
from autosolver.core.models import LexicographicScore
from autosolver.io.json_io import load_instance


class TestAdapterAndObjective:
    def test_load_instance_from_canonical_json(self):
        instance = load_instance(Path("examples/instances/sample_instance.json"))
        assert instance.instance_id == "sample-city-a"
        assert len(instance.orders) == 3
        assert len(instance.riders) == 3
        assert len(instance.match_scores) == 9

    def test_independent_acceptance_aggregation(self):
        probability = aggregate_acceptance_probability([0.5, 0.4])
        assert round(probability, 3) == 0.7

    def test_lexicographic_comparison(self):
        assert better_score(
            LexicographicScore(expected_completed_orders=2.1, total_cost=100.0),
            LexicographicScore(expected_completed_orders=2.0, total_cost=1.0),
        )
        assert better_score(
            LexicographicScore(expected_completed_orders=2.0, total_cost=5.0),
            LexicographicScore(expected_completed_orders=2.0, total_cost=6.0),
        )

from __future__ import annotations

from autosolver_agent.baselines import solve_small_instance_with_llm
from autosolver_agent.provider import LLMProvider


class BaselineProvider(LLMProvider):
    def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, object]:
        del system_prompt
        del user_prompt
        return {"selected_option_ids": ["order::o1::r1", "order::o2::r2", "order::o3::r3"]}


class TestBaselines:
    def test_small_instance_llm_baseline_returns_result(self, sample_instance):
        provider = BaselineProvider(base_url="https://example.com", api_key="test", model="test")
        result = solve_small_instance_with_llm(sample_instance, provider)
        assert result is not None
        assert result.solver_name == "llm_small_baseline"

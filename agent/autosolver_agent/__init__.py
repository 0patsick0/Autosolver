"""Research-mode agent loop for AutoSolver."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autosolver_agent.baselines import solve_small_instance_with_llm
    from autosolver_agent.provider import LLMProvider, OpenAICompatibleProvider
    from autosolver_agent.research import ResearchRunner

__all__ = ["LLMProvider", "OpenAICompatibleProvider", "ResearchRunner", "solve_small_instance_with_llm"]


def __getattr__(name: str) -> Any:
    if name in {"LLMProvider", "OpenAICompatibleProvider"}:
        from autosolver_agent.provider import LLMProvider, OpenAICompatibleProvider

        return {"LLMProvider": LLMProvider, "OpenAICompatibleProvider": OpenAICompatibleProvider}[name]
    if name == "ResearchRunner":
        from autosolver_agent.research import ResearchRunner

        return ResearchRunner
    if name == "solve_small_instance_with_llm":
        from autosolver_agent.baselines import solve_small_instance_with_llm

        return solve_small_instance_with_llm
    raise AttributeError(name)

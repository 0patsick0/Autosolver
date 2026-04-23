from __future__ import annotations

from pathlib import Path

from autosolver_agent.provider import OpenAICompatibleProvider, _parse_env_file


class TestProviderConfig:
    def test_parse_env_file(self, tmp_path: Path):
        env_file = tmp_path / ".env.local"
        env_file.write_text(
            "\n".join(
                [
                    "# comment",
                    "OPENAI_BASE_URL=https://integrate.api.nvidia.com/v1",
                    "OPENAI_MODEL='meta/llama-3.1-8b-instruct'",
                    'export OPENAI_API_KEY="nvapi-demo"',
                ]
            ),
            encoding="utf-8",
        )

        parsed = _parse_env_file(env_file)

        assert parsed["OPENAI_BASE_URL"] == "https://integrate.api.nvidia.com/v1"
        assert parsed["OPENAI_MODEL"] == "meta/llama-3.1-8b-instruct"
        assert parsed["OPENAI_API_KEY"] == "nvapi-demo"

    def test_parse_env_file_with_utf8_bom(self, tmp_path: Path):
        env_file = tmp_path / ".env.local"
        env_file.write_text(
            "OPENAI_BASE_URL=https://integrate.api.nvidia.com/v1",
            encoding="utf-8-sig",
        )

        parsed = _parse_env_file(env_file)

        assert parsed["OPENAI_BASE_URL"] == "https://integrate.api.nvidia.com/v1"

    def test_provider_reads_dotenv_when_shell_env_missing(self, tmp_path: Path, monkeypatch):
        (tmp_path / ".env.local").write_text(
            "\n".join(
                [
                    "OPENAI_BASE_URL=https://integrate.api.nvidia.com/v1",
                    "OPENAI_MODEL=meta/llama-3.1-8b-instruct",
                    "OPENAI_API_KEY=nvapi-demo",
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_MODEL", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        provider = OpenAICompatibleProvider.from_environment()

        assert provider.base_url == "https://integrate.api.nvidia.com/v1"
        assert provider.model == "meta/llama-3.1-8b-instruct"
        assert provider.api_key == "nvapi-demo"

    def test_shell_env_overrides_dotenv(self, tmp_path: Path, monkeypatch):
        (tmp_path / ".env.local").write_text(
            "OPENAI_MODEL=meta/llama-3.1-8b-instruct",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")

        provider = OpenAICompatibleProvider.from_environment()

        assert provider.model == "gpt-4.1-mini"

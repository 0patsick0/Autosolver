from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx


@dataclass(frozen=True)
class LLMProvider:
    base_url: str
    api_key: str | None
    model: str

    def is_configured(self) -> bool:
        return bool(self.model)

    def provider_label(self) -> str:
        return self.model

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, object]:
        raise NotImplementedError


@dataclass(frozen=True)
class OpenAICompatibleProvider(LLMProvider):
    @staticmethod
    def default_base_url() -> str:
        return _resolve_setting("OPENAI_BASE_URL", "https://api.openai.com/v1")

    @staticmethod
    def default_api_key() -> str | None:
        return _resolve_setting("OPENAI_API_KEY")

    @staticmethod
    def default_model() -> str:
        return _resolve_setting("OPENAI_MODEL", "gpt-4.1-mini")

    @classmethod
    def from_environment(cls) -> "OpenAICompatibleProvider":
        return cls(
            base_url=cls.default_base_url(),
            api_key=cls.default_api_key(),
            model=cls.default_model(),
        )

    def is_configured(self) -> bool:
        if not self.model:
            return False
        if self.api_key:
            return True
        hostname = urlparse(self.base_url).hostname or ""
        return hostname in {"localhost", "127.0.0.1", "::1"}

    def provider_label(self) -> str:
        hostname = urlparse(self.base_url).hostname or "unknown-host"
        return f"{self.model}@{hostname}"

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, object]:
        if not self.is_configured():
            raise RuntimeError("LLM provider is not configured. Set OPENAI_API_KEY or point OPENAI_BASE_URL to a local OpenAI-compatible server.")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": 600,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        with httpx.Client(timeout=45.0) as client:
            raw = None
            for attempt in range(3):
                try:
                    response = client.post(f"{self.base_url.rstrip('/')}/chat/completions", headers=headers, json=payload)
                    response.raise_for_status()
                    raw = response.json()
                    break
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    if attempt >= 2 or status_code < 500 and status_code != 429:
                        raise
                except (httpx.TimeoutException, httpx.TransportError):
                    if attempt >= 2:
                        raise
                time.sleep(0.75 * (attempt + 1))
            if raw is None:
                raise RuntimeError("LLM provider request failed without a response payload.")
        content = raw["choices"][0]["message"]["content"]
        return _parse_json_content(content)


def _parse_json_content(content: str) -> dict[str, object]:
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        parsed = json.loads(stripped[start : end + 1])
        if isinstance(parsed, dict):
            return parsed

    raise json.JSONDecodeError("Failed to parse JSON object from model response", content, 0)


def _resolve_setting(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value:
        return value
    return _read_local_env_settings().get(name, default)


def _read_local_env_settings() -> dict[str, str]:
    settings: dict[str, str] = {}
    for filename in (".env", ".env.local"):
        path = Path.cwd() / filename
        if not path.exists():
            continue
        settings.update(_parse_env_file(path))
    return settings


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values

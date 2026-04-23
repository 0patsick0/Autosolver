from __future__ import annotations

from autosolver_agent.provider import _parse_json_content


class TestProviderParsing:
    def test_parse_plain_json(self):
        parsed = _parse_json_content('{"ok": true, "provider": "nvidia"}')
        assert parsed["ok"] is True

    def test_parse_fenced_json(self):
        parsed = _parse_json_content('```json\n{"summary":"ok","risks":[]}\n```')
        assert parsed["summary"] == "ok"

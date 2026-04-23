from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from autosolver.core.models import Event, sanitize_json_value


class EventWriter:
    def __init__(self, path: str | Path, replay_output_path: str | Path | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.replay_output_path = Path(replay_output_path) if replay_output_path else None

    def write(self, event_type: str, payload: dict[str, object]) -> Event:
        event = Event(
            ts=datetime.now(UTC).isoformat(),
            type=event_type,
            payload=sanitize_json_value({key: value for key, value in payload.items()}),
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        if self.replay_output_path is not None:
            from autosolver.io.replay import write_replay_payload_from_event_path

            write_replay_payload_from_event_path(self.path, self.replay_output_path)
        return event


def read_events(path: str | Path) -> list[Event]:
    source = Path(path)
    if not source.exists():
        return []
    events: list[Event] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        events.append(Event(ts=raw["ts"], type=raw["type"], payload=raw["payload"]))
    return events

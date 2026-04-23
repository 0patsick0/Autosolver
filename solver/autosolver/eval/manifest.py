from __future__ import annotations

from pathlib import Path

from autosolver.core.models import BenchmarkCase, JsonValue
from autosolver.io.json_io import load_instance, load_json


def load_benchmark_cases(target: str | Path) -> tuple[str, list[BenchmarkCase], dict[str, JsonValue]]:
    path = Path(target)
    if path.is_dir():
        cases: list[BenchmarkCase] = []
        for instance_path in sorted(path.glob("*.json")):
            raw = load_json(instance_path)
            if isinstance(raw, dict) and isinstance(raw.get("cases"), list):
                continue
            if not isinstance(raw, dict) or "instance_id" not in raw:
                continue
            cases.append(
                BenchmarkCase(
                    case_id=instance_path.stem,
                    instance=load_instance(instance_path),
                    source_path=str(instance_path),
                    weight=1.0,
                )
            )
        return path.name or "benchmark", cases, {}

    raw = load_json(path)
    if isinstance(raw, dict) and isinstance(raw.get("cases"), list):
        return _load_manifest_cases(path, raw)

    instance = load_instance(path)
    return (
        path.stem,
        [
            BenchmarkCase(
                case_id=instance.instance_id,
                instance=instance,
                source_path=str(path),
                weight=1.0,
            )
        ],
        {},
    )


def _load_manifest_cases(path: Path, raw: dict[str, object]) -> tuple[str, list[BenchmarkCase], dict[str, JsonValue]]:
    benchmark_id = str(raw.get("benchmark_id", path.stem))
    metadata = raw.get("metadata", {})
    resolved_cases: list[BenchmarkCase] = []

    for index, item in enumerate(raw["cases"]):
        if not isinstance(item, dict) or "instance" not in item:
            raise ValueError(f"Benchmark case #{index + 1} in {path} is missing an instance path.")
        relative_instance_path = path.parent / str(item["instance"])
        instance_path = relative_instance_path.resolve()
        repeat = max(1, int(item.get("repeat", 1)))
        weight = float(item.get("weight", 1.0))
        base_case_id = str(item.get("case_id", instance_path.stem))
        for repeat_index in range(repeat):
            case_id = base_case_id if repeat == 1 else f"{base_case_id}#{repeat_index + 1}"
            resolved_cases.append(
                BenchmarkCase(
                    case_id=case_id,
                    instance=load_instance(instance_path),
                    source_path=str(relative_instance_path),
                    weight=weight,
                )
            )

    if not resolved_cases:
        raise ValueError(f"Benchmark manifest at {path} does not contain any cases.")
    return benchmark_id, resolved_cases, metadata if isinstance(metadata, dict) else {}

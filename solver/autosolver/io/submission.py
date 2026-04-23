from __future__ import annotations

from pathlib import Path

from autosolver.core.models import SolveResult, dataclass_to_dict
from autosolver.io.json_io import write_json


class SubmissionWriter:
    def write(self, result: SolveResult, path: str | Path) -> None:
        raise NotImplementedError


class CanonicalSubmissionWriter(SubmissionWriter):
    def write(self, result: SolveResult, path: str | Path) -> None:
        write_json(
            path,
            {
                "format": "canonical-v1",
                "result": dataclass_to_dict(result),
            },
        )

from __future__ import annotations

from pathlib import Path

import pytest

from autosolver.core.models import CanonicalInstance
from autosolver.io.json_io import load_instance


@pytest.fixture
def sample_instance() -> CanonicalInstance:
    return load_instance(Path("examples/instances/sample_instance.json"))

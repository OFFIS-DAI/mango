import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from mango.util.tracing import VIEWER_SCHEMA

from .viewer_sample import TRACE


def test_viewer_core_modules(tmp_path):
    """Runs viewer_core_test.mjs, the unit tests of the viewer's core modules."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"trace": TRACE, "schema": VIEWER_SCHEMA}))
    tests = Path(__file__).with_name("viewer_core_test.mjs")
    result = subprocess.run(
        [node, "--test", str(tests)],
        env={**os.environ, "VIEWER_FIXTURE": str(fixture)},
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr

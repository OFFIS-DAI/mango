"""study.run() in a script without an ``if __name__ == "__main__":`` guard.

Spawned worker processes import the parent's script as ``__mp_main__``; a
top-level ``study.run()`` would start the study again in every worker.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("pandas")

ROOT = Path(__file__).resolve().parents[3]

HEADER = """
from mango import Agent
from mango.scenario import Scenario, Study

class A(Agent):
    def handle_message(self, content, meta):
        pass

scenario = Scenario("ng")
scenario.time(horizon=3, step=1)
scenario.group("a", 2, lambda e: A())

def out(world):
    return 1.0

study = Study(scenario, replications=2, seed=1, outcomes=[out], out="results/ng")
"""


def run_script(tmp_path, body):
    script = tmp_path / "study_script.py"
    script.write_text(HEADER + textwrap.dedent(body), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_a_top_level_run_with_workers_is_refused_before_any_worker_starts(tmp_path):
    done = run_script(tmp_path, "print(study.run(workers=2))\n")
    assert done.returncode == 1
    assert "runs at the top level of the script" in done.stderr
    assert 'if __name__ == "__main__":' in done.stderr
    assert "in use by another study.run()" not in done.stderr
    assert not (tmp_path / "results").exists()  # nothing planned or written


def test_a_worker_importing_the_script_stops_with_one_line(tmp_path):
    # an ``if`` that is no __name__ guard: the parent cannot tell, the workers can
    done = run_script(tmp_path, "if True:\n    report = study.run(workers=2)\n")
    lines = [line for line in done.stderr.splitlines() if line.strip()]
    worker = [line for line in lines if "while a worker process imported" in line]
    assert worker, done.stderr
    assert "Traceback" not in done.stderr
    assert "in use by another study.run()" not in done.stderr


def test_guarded_and_sequential_scripts_run(tmp_path):
    done = run_script(
        tmp_path,
        """
        if __name__ == "__main__":
            assert study.run(workers=2, progress=False).ok
        """,
    )
    assert done.returncode == 0, done.stderr
    # in-process runs never re-import the script: no guard needed
    (tmp_path / "results").rename(tmp_path / "first")
    done = run_script(tmp_path, "assert study.run(progress=False).ok\n")
    assert done.returncode == 0, done.stderr

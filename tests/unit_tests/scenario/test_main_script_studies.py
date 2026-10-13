"""Studies planned by a script that defines its params model and a kit itself.

Spawn workers import the script as ``__mp_main__`` and Slurm array tasks as
``<module>``; the scenario fingerprint must not depend on that module name.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mango.scenario import load_study

ROOT = Path(__file__).resolve().parents[3]

SCRIPT = """
import sys
from pydantic import BaseModel
from mango.scenario import (
    Scenario, ScenarioComponent, ScenarioParams, SlurmExecutor, Study,
)

class Params(ScenarioParams):
    x: float = 1.0

class Field(ScenarioComponent):
    class Params(BaseModel, extra="forbid"):
        n: int = 1

    def record(self, ctx):
        ctx.record_world("n", lambda ctx: ctx.params.field.n * ctx.params.x)

scenario = Scenario("main_demo", params=Params)
scenario.time(horizon=2, step=1)
scenario.use(Field())

if __name__ == "__main__":
    study = Study(scenario, levers={"x": [1.0, 2.0]}, out="pool", format="csv")
    report = study.run(workers=2, progress=False)
    print(report.done, report.failed, [f["error"] for f in report.failures])
    study = Study(scenario, levers={"x": [1.0, 2.0]}, out="slurm", format="csv")
    study.run(executor=SlurmExecutor(submit=False), progress=False)
"""


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_script_defined_params_and_kit_run_in_spawn_workers_and_slurm_tasks(
    tmp_path,
):
    (tmp_path / "mystudy.py").write_text(SCRIPT)
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    done = subprocess.run(
        [sys.executable, "mystudy.py"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[-1] == "2 0 []"
    pending = (tmp_path / "slurm" / "slurm" / "pending.jsonl").read_text()
    assert '"scenario": "mystudy:scenario"' in pending
    task = subprocess.run(
        ["bash", str(tmp_path / "slurm" / "slurm" / "array.sbatch")],
        env=dict(env, SLURM_ARRAY_TASK_ID="0"),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert task.returncode == 0, task.stdout + task.stderr
    assert load_study(tmp_path / "slurm")["status"].tolist() == ["done", "pending"]

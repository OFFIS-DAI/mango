"""SlurmExecutor: job array files, submission, the active-job guard and run_one_from_file."""

import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from mango.scenario import (
    SlurmExecutor,
    Study,
    StudyError,
    load_study,
    run_one_from_file,
)
from mango.scenario.runner import (
    WORKER_ENV,
    active_slurm_job,
    resolve_executor,
    run_specs,
)
from mango.scenario.slurm import _importable_ref

from .study_fixtures import REF

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="fake sbatch/squeue are shell scripts"
)


def _study(out, **kwargs):
    return Study(
        f"{REF}:scenario",
        uncertainties={"ev.n": [1, 2]},
        levers={"algorithm": ["greedy", "proportional"]},
        seed=3,
        outcomes=[f"{REF}:mean_soc"],
        out=out,
        format="csv",
        **kwargs,
    )


def _fake_bin(tmp_path, **scripts):
    """A directory with shell scripts named after the keys; PATH gets only it."""
    folder = tmp_path / "bin"
    folder.mkdir(parents=True, exist_ok=True)
    for name, body in scripts.items():
        path = folder / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    return folder


@pytest.fixture
def no_slurm(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(_fake_bin(tmp_path)))


def _lines(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


# ----------------------------------------------------------------- the batch script


def test_script_content():
    executor = SlurmExecutor(
        partition="short",
        time="00:30:00",
        cpus_per_task=2,
        mem="4G",
        array_parallelism=10,
        extra_sbatch=["--constraint=ib", "#SBATCH --qos=low"],
        python="/opt/py/bin/python",
        submit=False,
    )
    text = executor.script(
        120, Path("/data/my study"), "/home/me/project", job_name="fleet comparison"
    )
    lines = text.splitlines()
    assert lines[0] == "#!/bin/bash"
    directives = [line for line in lines if line.startswith("#SBATCH")]
    assert directives == [
        "#SBATCH --job-name=fleet_comparison",
        "#SBATCH --array=0-119%10",
        "#SBATCH --partition=short",
        "#SBATCH --time=00:30:00",
        "#SBATCH --cpus-per-task=2",
        "#SBATCH --mem=4G",
        '#SBATCH --output="/data/my study/slurm/logs/%A_%a.out"',
        "#SBATCH --constraint=ib",
        "#SBATCH --qos=low",
    ]
    # every directive comes before the first command
    first_command = next(
        i for i, line in enumerate(lines) if line and not line.startswith("#")
    )
    assert all(lines.index(d) < first_command for d in directives)
    for key, value in WORKER_ENV.items():
        assert f'export {key}="${{{key}:-{value}}}"' in lines
    assert "cd /home/me/project" in lines
    assert "TASK=$(( ${MANGO_TASK_OFFSET:-0} + SLURM_ARRAY_TASK_ID ))" in lines
    assert lines[-1] == (
        "/opt/py/bin/python -m mango.scenario.cli run-one "
        "'/data/my study/slurm/pending.jsonl' --line \"$TASK\""
    )


def test_script_defaults_leave_out_unset_options():
    text = SlurmExecutor(time=None).script(3, Path("/r"), "/p")
    assert "#SBATCH --array=0-2\n" in text
    assert "--partition" not in text and "--time" not in text and "--mem" not in text
    assert "#SBATCH --cpus-per-task=1" in text
    assert f"{sys.executable} -m mango.scenario.cli run-one" in text or (
        f"'{sys.executable}' -m" in text
    )
    assert "#SBATCH --time=01:00:00" in SlurmExecutor().script(1, Path("/r"), "/p")


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"partition": "a\nb"}, "partition must be a non-empty single line"),
        ({"time": ""}, "time must be a non-empty single line"),
        ({"cpus_per_task": 0}, "cpus_per_task must be an int >= 1"),
        ({"array_parallelism": 0}, "array_parallelism must be an int >= 1"),
        ({"array_parallelism": True}, "array_parallelism must be an int >= 1"),
        ({"extra_sbatch": ["--a", "--b\n--c"]}, "every extra_sbatch line"),
        ({"submit": "no"}, "submit must be True or False"),
        ({"python": ""}, "python must be a non-empty single line"),
    ],
)
def test_invalid_options(kwargs, message):
    with pytest.raises(StudyError, match=message):
        SlurmExecutor(**kwargs)


def test_executor_resolution():
    choice = resolve_executor("slurm")
    assert choice.kind == "slurm" and isinstance(choice.executor, SlurmExecutor)
    assert not choice.in_process
    mine = SlurmExecutor(partition="short", submit=False)
    assert resolve_executor(mine).executor is mine
    assert (
        resolve_executor(mine).describe()
        == "Slurm array, partition short, not submitted"
    )
    assert repr(SlurmExecutor()) == "<SlurmExecutor: Slurm array>"
    assert SlurmExecutor(extra_sbatch="--qos=low").extra_sbatch == ("--qos=low",)
    with pytest.raises(StudyError, match="cannot be combined with Slurm"):
        resolve_executor("slurm", workers=4)
    with pytest.raises(StudyError, match="Study.run submits them"):
        run_specs([{"run_id": "x"}], choice)


# ----------------------------------------------------------------- study.run


def test_submit_false_writes_pending_runs_only(tmp_path, no_slurm):
    out = tmp_path / "study"
    plan = _study(out).plan()
    # finish two runs first, as an earlier study.run would have
    from mango.scenario import run_one

    for spec in (plan[0], plan[3]):
        assert run_one(spec)["status"] == "done"
    report = _study(out).run(executor=SlurmExecutor(submit=False), progress=False)
    assert report.total == 4 and report.skipped == 2 and report.done == 0
    assert report.submitted == report.pending == 2 and report.job_id is None
    assert not report.ok
    script = out / "slurm" / "array.sbatch"
    assert report.script == script
    assert f"2 pending in {script} (not submitted; run: sbatch {script})" in str(report)
    pending = _lines(out / "slurm" / "pending.jsonl")
    assert [spec["index"] for spec in pending] == [1, 2]
    assert pending == [
        spec for spec in _lines(out / "runs.jsonl") if spec["index"] in (1, 2)
    ]
    assert "#SBATCH --array=0-1\n" in script.read_text()
    assert (out / "slurm" / "logs").is_dir()
    assert not (out / "slurm" / "job.json").exists()
    assert (out / "status.csv").read_text().count("pending") == 2
    assert not (out / ".lock").exists()


def test_nothing_to_submit(tmp_path, no_slurm):
    out = tmp_path / "study"
    _study(out).run(progress=False)
    report = _study(out).run(executor=SlurmExecutor(submit=False), progress=False)
    assert report.skipped == 4 and report.submitted == 0 and report.ok
    assert not (out / "slurm").exists()


def test_missing_sbatch_fails_before_writing(tmp_path, no_slurm):
    out = tmp_path / "study"
    with pytest.raises(StudyError, match=r"sbatch was not found on PATH.*submit=False"):
        _study(out).run(executor="slurm", progress=False)
    assert not out.exists()
    with pytest.raises(StudyError, match="sbatch was not found"):
        SlurmExecutor().dispatch([{"run_id": "a", "out": "x"}], tmp_path)


def test_submit_runs_sbatch_and_records_the_job(tmp_path, monkeypatch):
    calls = tmp_path / "sbatch_calls.txt"
    folder = _fake_bin(
        tmp_path,
        sbatch=f'echo "$PWD $@" >> "{calls}"\necho "4242;cluster"',
        squeue="exit 0",  # the job has finished: no tasks listed
    )
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    out = tmp_path / "study"
    report = _study(out).run(executor=SlurmExecutor(partition="short"), progress=False)
    script = out / "slurm" / "array.sbatch"
    assert report.job_id == "4242" and report.submitted == 4 and report.done == 0
    assert "4 submitted as Slurm job 4242" in str(report)
    assert "(Slurm array, partition short)" in str(report)
    assert f"mango-scenario status {out}" in str(report)
    assert calls.read_text().split() == [os.getcwd(), "--parsable", str(script)]
    job = json.loads((out / "slurm" / "job.json").read_text())
    assert job["job_id"] == "4242" and job["runs"] == 4
    assert "#SBATCH --partition=short" in script.read_text()
    assert active_slurm_job(out) is None
    # the finished job does not block the next run (its 4 runs are still pending)
    assert _study(out).run(executor="slurm", progress=False).submitted == 4


def test_sbatch_failure(tmp_path, monkeypatch):
    folder = _fake_bin(
        tmp_path, sbatch='echo "sbatch: error: invalid partition" >&2\nexit 1'
    )
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(
        StudyError, match=r"failed \(exit 1\): sbatch: error: invalid partition"
    ):
        _study(tmp_path / "study").run(executor="slurm", progress=False)


def test_active_job_blocks_every_executor(tmp_path, monkeypatch):
    out = tmp_path / "study"
    (out / "slurm").mkdir(parents=True)
    (out / "slurm" / "job.json").write_text(json.dumps({"job_id": "77"}))
    folder = _fake_bin(tmp_path, squeue='echo "77_[2-3]"')
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    assert active_slurm_job(out) == "77"
    with pytest.raises(
        StudyError, match=r"Slurm job 77 .* still has queued or running tasks"
    ):
        _study(out).run(progress=False)
    assert not (out / "runs.jsonl").exists()
    # without squeue the job cannot be checked: a warning, no block
    monkeypatch.setenv("PATH", str(_fake_bin(tmp_path / "empty")))
    assert active_slurm_job(out) is None
    assert active_slurm_job(tmp_path / "elsewhere") is None


# ----------------------------------------------------------------- array tasks


def test_run_one_from_file_runs_a_line_and_load_study_sees_it(tmp_path, no_slurm):
    out = tmp_path / "study"
    _study(out).run(executor=SlurmExecutor(submit=False), progress=False)
    pending = out / "slurm" / "pending.jsonl"
    outcome = run_one_from_file(pending, 2)
    spec = _lines(pending)[2]
    assert outcome["status"] == "done" and outcome["run_id"] == spec["run_id"]
    assert (out / "runs" / spec["run_id"] / "manifest.json").exists()
    frame = load_study(out)
    done = frame[frame["status"] == "done"]
    assert done["run_id"].tolist() == [spec["run_id"]]
    assert done["mean_soc"].notna().all()
    assert (frame["status"] == "pending").sum() == 3
    # the next submission holds only the three remaining runs
    report = _study(out).run(executor=SlurmExecutor(submit=False), progress=False)
    assert report.skipped == 1 and report.submitted == 3
    assert spec["run_id"] not in [s["run_id"] for s in _lines(pending)]


def test_run_one_from_file_errors(tmp_path):
    table = tmp_path / "pending.jsonl"
    table.write_text('{"run_id": "a", "out": "x"}\n\nnot json\n[1]\n')
    with pytest.raises(StudyError, match=r"has 3 run specs; line 3 is out of range"):
        run_one_from_file(table, 3)
    with pytest.raises(StudyError, match="line 1 is not valid JSON"):
        run_one_from_file(table, 1)
    with pytest.raises(StudyError, match="line 2 is not a run spec"):
        run_one_from_file(table, 2)
    for line in (-1, True, "0"):
        with pytest.raises(StudyError, match="line must be an int >= 0"):
            run_one_from_file(table, line)
    with pytest.raises(StudyError, match="No run table"):
        run_one_from_file(tmp_path / "missing.jsonl", 0)


def test_main_references_become_importable(tmp_path, monkeypatch):
    script = tmp_path / "studies" / "run.py"
    main = types.ModuleType("__main__")
    main.__file__ = str(script)
    monkeypatch.setitem(sys.modules, "__main__", main)
    assert _importable_ref("__main__:scenario", str(tmp_path)) == "studies.run:scenario"
    assert _importable_ref("__main__.kpi", str(tmp_path)) == "studies.run.kpi"
    assert _importable_ref("pkg.mod:scenario", str(tmp_path)) == "pkg.mod:scenario"
    assert _importable_ref(None, str(tmp_path)) is None
    with pytest.raises(StudyError, match="not a module below the project root"):
        _importable_ref("__main__:scenario", str(tmp_path / "other"))
    main.__spec__ = types.SimpleNamespace(name="studies.fleet")
    assert _importable_ref("__main__:scenario", "/anywhere") == "studies.fleet:scenario"


def test_pending_file_rewrites_main_references(tmp_path, no_slurm, monkeypatch):
    from mango.scenario.slurm import _slurm_spec

    main = types.ModuleType("__main__")
    main.__file__ = str(tmp_path / "run_study.py")
    monkeypatch.setitem(sys.modules, "__main__", main)
    spec = {
        "run_id": "a",
        "scenario": "__main__:scenario",
        "outcomes": {"peak": "__main__:peak", "other": "kpi:other", "none": None},
        "project_root": str(tmp_path),
    }
    assert _slurm_spec(spec) == {
        **spec,
        "scenario": "run_study:scenario",
        "outcomes": {"peak": "run_study:peak", "other": "kpi:other", "none": None},
    }


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_array_task_script_runs_a_line(tmp_path, no_slurm):
    pytest.importorskip("mango.scenario.cli")
    out = tmp_path / "study"
    _study(out).run(executor=SlurmExecutor(submit=False), progress=False)
    env = {**os.environ, "SLURM_ARRAY_TASK_ID": "1", "PATH": os.defpath}
    subprocess.run(
        ["bash", str(out / "slurm" / "array.sbatch")],
        env=env,
        check=True,
        capture_output=True,
        timeout=120,
    )
    frame = load_study(out)
    assert frame["status"].tolist().count("done") == 1
    assert frame.loc[frame["status"] == "done", "index"].tolist() == [1]


# ----------------------------------------------------------------- setup, chunks, splits


def test_setup_lines_account_and_qos():
    executor = SlurmExecutor(
        account="grid",
        qos="short",
        setup=["module load python/3.11", "source .venv/bin/activate"],
        submit=False,
    )
    lines = executor.script(3, Path("/r"), "/p").splitlines()
    assert "#SBATCH --account=grid" in lines and "#SBATCH --qos=short" in lines
    start = lines.index("set +u")
    assert lines[start : start + 4] == [
        "set +u",
        "module load python/3.11",
        "source .venv/bin/activate",
        "set -u",
    ]
    assert lines.index("set -eu") < start < lines.index("cd /p")
    assert SlurmExecutor(setup="module load x").setup == ("module load x",)


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"extra_sbatch": ["module load python"]}, "belong in setup"),
        ({"setup": ["a\nb"]}, "every setup line must be a non-empty single line"),
        ({"runs_per_task": 0}, "runs_per_task must be an int >= 1"),
        ({"max_array_size": 0}, "max_array_size must be an int >= 1"),
        ({"account": ""}, "account must be a non-empty single line"),
    ],
)
def test_invalid_new_options(kwargs, message):
    with pytest.raises(StudyError, match=message):
        SlurmExecutor(**kwargs)


def test_runs_per_task_chunks_the_array():
    executor = SlurmExecutor(runs_per_task=4, submit=False)
    assert executor.tasks(3072) == 768 and executor.tasks(5) == 2
    text = executor.script(10, Path("/r"), "/p")
    assert "#SBATCH --array=0-2\n" in text
    assert "10 pending runs in 3 array tasks" in text
    assert text.rstrip().endswith('--lines "$(( TASK * 4 )):$(( TASK * 4 + 4 ))"')
    assert "4 runs per task" in executor.describe()


def test_large_arrays_are_split_into_several_jobs(tmp_path, monkeypatch):
    calls = tmp_path / "sbatch_calls.txt"
    folder = _fake_bin(
        tmp_path,
        sbatch=f'echo "$@" >> "{calls}"\nn=$(wc -l < "{calls}")\necho "$((4240 + n))"',
        squeue='echo "4242_[1]"',
    )
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    out = tmp_path / "study"
    executor = SlurmExecutor(max_array_size=3, array_parallelism=2)
    report = _study(out).run(executor=executor, progress=False)
    assert report.job_ids == ["4241", "4242"] and report.job_id == "4241"
    assert "4 submitted as Slurm jobs 4241, 4242" in str(report)
    script = out / "slurm" / "array.sbatch"
    assert calls.read_text().splitlines() == [
        f"--parsable --array=0-2%2 --export=ALL,MANGO_TASK_OFFSET=0 {script}",
        f"--parsable --array=0-0%2 --export=ALL,MANGO_TASK_OFFSET=3 {script}",
    ]
    job = json.loads((out / "slurm" / "job.json").read_text())
    assert job["job_ids"] == ["4241", "4242"]
    assert "sbatch --array=0-0%2" in (out / "slurm" / "submit.sh").read_text()
    # the second job still runs: the folder is busy
    assert active_slurm_job(out) == "4242"


def test_submit_false_reports_the_commands_of_a_split_array(tmp_path, no_slurm):
    out = tmp_path / "study"
    executor = SlurmExecutor(max_array_size=2, submit=False)
    report = _study(out).run(executor=executor, progress=False)
    assert report.submitted == 4 and report.job_ids == []
    commands = (out / "slurm" / "submit.sh").read_text()
    assert "MANGO_TASK_OFFSET=0" in commands and "MANGO_TASK_OFFSET=2" in commands


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_array_task_with_an_offset_runs_its_chunk(tmp_path, no_slurm):
    out = tmp_path / "study"
    executor = SlurmExecutor(runs_per_task=2, max_array_size=1, submit=False)
    _study(out).run(executor=executor, progress=False)
    env = {
        **os.environ,
        "SLURM_ARRAY_TASK_ID": "0",
        "MANGO_TASK_OFFSET": "1",
        "PATH": os.defpath,
    }
    subprocess.run(
        ["bash", str(out / "slurm" / "array.sbatch")],
        env=env,
        check=True,
        capture_output=True,
        timeout=120,
    )
    frame = load_study(out)
    assert frame.loc[frame["status"] == "done", "index"].tolist() == [2, 3]
    # a requeued task skips its finished runs
    completed = subprocess.run(
        ["bash", str(out / "slurm" / "array.sbatch")],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0 and completed.stdout.count("already done") == 2


def test_sbatch_array_size_errors_get_a_hint(tmp_path, monkeypatch):
    folder = _fake_bin(
        tmp_path,
        sbatch='echo "sbatch: error: Batch job submission failed: Invalid job array '
        'specification (MaxArraySize)" >&2\nexit 1',
    )
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(StudyError, match="raise runs_per_task"):
        _study(tmp_path / "study").run(executor="slurm", progress=False)

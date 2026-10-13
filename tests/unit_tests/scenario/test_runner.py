import concurrent.futures
import contextlib
import csv
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pd = pytest.importorskip("pandas")

from mango.scenario import (  # noqa: E402
    Explicit,
    Scenario,
    Study,
    StudyError,
    load_run,
    load_study,
    run_one,
)
from mango.scenario import runner as runner_module  # noqa: E402
from mango.scenario.runner import (  # noqa: E402
    WORKER_ENV,
    check_refs,
    compute_outcomes,
    resolve_executor,
    resolve_outcomes,
    worker_environment,
)

from . import study_fixtures as fx  # noqa: E402


def small_study(out, **kwargs):
    options = {
        "levers": {"algorithm": ["greedy", "proportional"]},
        "replications": 1,
        "seed": 7,
        "outcomes": [fx.mean_soc],
        "out": out,
    }
    options.update(kwargs)
    return Study(fx.scenario, **options)


def status_table(out):
    with open(out / "status.csv", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# ----------------------------------------------------------------- executor policy


def test_resolve_executor():
    assert resolve_executor().kind == "sequential"
    assert resolve_executor(workers=1).kind == "sequential"
    assert resolve_executor("sequential").describe() == "sequential"
    pool = resolve_executor(workers=4)
    assert (pool.kind, pool.workers, pool.in_process) == ("process", 4, False)
    assert pool.describe() == "process pool (4 workers)"
    assert resolve_executor("process", 1).describe() == "process pool (1 worker)"
    assert resolve_executor("process").workers == (os.cpu_count() or 1)
    spy = fx.SpyExecutor()
    external = resolve_executor(spy)
    assert (external.kind, external.executor, external.in_process) == (
        "external",
        spy,
        False,
    )
    assert external.describe() == "SpyExecutor (caller-owned)"
    with concurrent.futures.ThreadPoolExecutor(1) as threads:
        assert resolve_executor(threads).in_process


@pytest.mark.parametrize(
    "executor, workers, message",
    [
        (None, 0, "workers must be an int >= 1"),
        (None, 2.5, "workers must be an int >= 1"),
        (None, True, "workers must be an int >= 1"),
        ("sequential", 4, "drop workers=4"),
        ("mpi", None, "Unknown executor 'mpi'"),
        (3, None, "Unknown executor 3"),
    ],
)
def test_resolve_executor_errors(executor, workers, message):
    with pytest.raises(StudyError, match=message):
        resolve_executor(executor, workers)


def test_executor_instance_and_workers_conflict():
    with pytest.raises(
        StudyError, match="cannot be combined with an executor instance"
    ):
        resolve_executor(fx.SpyExecutor(), 2)


def test_worker_environment_sets_only_unset_and_restores(monkeypatch):
    for key in WORKER_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MKL_NUM_THREADS", "3")
    with worker_environment() as added:
        assert os.environ["OMP_NUM_THREADS"] == "1"
        assert os.environ["PYTHONHASHSEED"] == "0"
        assert os.environ["MKL_NUM_THREADS"] == "3"
        assert "MKL_NUM_THREADS" not in added
    assert "OMP_NUM_THREADS" not in os.environ
    assert "PYTHONHASHSEED" not in os.environ
    assert os.environ["MKL_NUM_THREADS"] == "3"


# ----------------------------------------------------------------- references and outcomes


def test_resolve_outcomes_forms():
    table = resolve_outcomes([fx.mean_soc, f"{fx.REF}:first_soc"])
    assert list(table) == ["mean_soc", "first_soc"]
    assert table["mean_soc"] == (fx.mean_soc, f"{fx.REF}:mean_soc")
    assert table["first_soc"][1] == f"{fx.REF}:first_soc"
    single = resolve_outcomes(fx.mean_soc)
    assert list(single) == ["mean_soc"]
    named = resolve_outcomes({"peak": lambda run: 1, "known": None})
    assert named["peak"][1] is None and named["known"] == (None, None)
    assert resolve_outcomes(None) == {}
    with pytest.raises(StudyError, match="must be a dict"):
        resolve_outcomes(3)
    with pytest.raises(StudyError, match="non-empty str"):
        resolve_outcomes({"": fx.mean_soc})


def test_compute_outcomes_captures_errors():
    result = fx.scenario.run(seed=1)
    values, errors = compute_outcomes(
        result,
        resolve_outcomes(
            {
                "mean": fx.mean_soc,
                "bad": fx.broken,
                "odd": lambda run: object(),
                "missing": None,
            }
        ),
    )
    assert values["mean"] == pytest.approx(result.ts("soc").mean())
    assert values["bad"] is None and values["odd"] is None and values["missing"] is None
    assert errors["bad"] == "ZeroDivisionError: no outcome today"
    assert errors["odd"].startswith("TypeError: Cannot convert object")
    assert "has no function" in errors["missing"]
    assert "mean" not in errors


def test_repeated_outcome_errors_share_one_log_line_per_run(caplog, monkeypatch):
    monkeypatch.setattr(runner_module, "_LOGGED_OUTCOME_ERRORS", set())
    result = fx.scenario.run(seed=1)
    table = resolve_outcomes(
        {"bad": fx.broken, "worse": fx.broken, "mean": fx.mean_soc}
    )
    with caplog.at_level("WARNING", logger=runner_module.__name__):
        compute_outcomes(result, table)
        first = [r.getMessage() for r in caplog.records]
        caplog.clear()
        compute_outcomes(result, table)
        again = [r.getMessage() for r in caplog.records]
    # the first run shows each full error
    assert len(first) == 2 and all("no outcome today" in m for m in first)
    # a later run with the same errors gets one short line
    assert again == [
        f"Outcomes 'bad', 'worse' of run {result.run_id} failed with the same "
        "errors as before"
    ]


def test_check_refs_explains_how_to_fix():
    with pytest.raises(StudyError) as info:
        check_refs(None, {})
    assert "define the scenario at module level of an importable module" in str(
        info.value
    )
    assert "'module:attr'" in str(info.value)
    with pytest.raises(StudyError, match=r"outcome\(s\) \['peak'\] have none"):
        check_refs("a:b", resolve_outcomes({"peak": lambda run: 1}))
    check_refs(f"{fx.REF}:scenario", resolve_outcomes([fx.mean_soc]))


# ----------------------------------------------------------------- run_one


def test_run_one_writes_the_run_folder(tmp_path):
    plan = small_study(tmp_path, outcomes=[fx.mean_soc, fx.broken]).plan()
    spec = plan[1]
    outcome = run_one(spec)
    assert list(outcome) == [
        "run_id",
        "index",
        "status",
        "wall_s",
        "cpu_s",
        "load_s",
        "outcomes",
        "error",
        "outcome_errors",
        "warnings",
        "changed_during_run",
    ]
    assert outcome["outcome_errors"] == {
        "broken": "ZeroDivisionError: no outcome today"
    }
    assert outcome["status"] == "done" and outcome["error"] is None
    assert outcome["index"] == 1 and outcome["run_id"] == spec["run_id"]
    folder = tmp_path / "runs" / spec["run_id"]
    names = {path.name for path in folder.iterdir()}
    assert {
        "spec.json",
        "config.resolved.yaml",
        "outcomes.json",
        "manifest.json",
    } <= names
    assert names & {"timeseries.parquet", "timeseries.csv"}
    assert "error.txt" not in names
    assert json.loads((folder / "spec.json").read_text()) == spec
    manifest = json.loads((folder / "manifest.json").read_text())
    for key in (
        "run_id",
        "index",
        "study",
        "status",
        "started",
        "finished",
        "wall_s",
        "seed",
        "scenario",
        "factors",
        "labels",
        "replication",
        "checksum",
        "stats",
        "outcome_errors",
        "provenance",
        "git",
        "pythonhashseed",
        "format",
    ):
        assert key in manifest, key
    assert manifest["status"] == "done" and manifest["index"] == 1
    assert manifest["factors"] == {"algorithm": "proportional"}
    assert manifest["seed"] == spec["seed"]
    assert manifest["outcome_errors"] == {
        "broken": "ZeroDivisionError: no outcome today"
    }
    outcomes = json.loads((folder / "outcomes.json").read_text())
    assert (
        outcomes["broken"] is None
        and outcomes["mean_soc"] == outcome["outcomes"]["mean_soc"]
    )
    loaded = load_run(folder)
    assert loaded.run_id == spec["run_id"]
    assert set(loaded.frame["run_id"]) == {spec["run_id"]}
    replay = fx.scenario.run(spec["params"], seed=spec["seed"])
    assert replay.checksum() == loaded.checksum() == manifest["checksum"]


def test_run_one_adopts_the_study_run_id_for_factory_refs(tmp_path):
    study = Study(
        f"{fx.REF}:make_scenario",
        levers={"algorithm": ["greedy"]},
        seed=3,
        out=tmp_path,
    )
    (spec,) = study.plan()
    assert spec["scenario"] == f"{fx.REF}:make_scenario"
    assert run_one(spec)["status"] == "done"
    loaded = load_run(tmp_path / "runs" / spec["run_id"])
    assert loaded.run_id == spec["run_id"]
    assert loaded.scenario == f"{fx.REF}:make_scenario"
    assert set(loaded.frame["run_id"]) == {spec["run_id"]}


def test_run_one_never_raises(tmp_path):
    spec = dict(small_study(tmp_path).plan()[0], scenario=f"{fx.REF}:nope")
    outcome = run_one(spec)
    assert outcome["status"] == "failed"
    assert "Cannot load the scenario" in outcome["error"]
    folder = tmp_path / "runs" / spec["run_id"]
    assert "Traceback" in (folder / "error.txt").read_text()
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["status"] == "failed" and manifest["error"] == outcome["error"]
    malformed = run_one({"run_id": "x"})
    assert malformed["status"] == "failed" and "KeyError" in malformed["error"]


# ----------------------------------------------------------------- sequential executor


def test_sequential_study_end_to_end(tmp_path, capsys):
    study = small_study(tmp_path, replications=2, outcomes=[fx.mean_soc, fx.broken])
    report = study.run()
    assert (report.total, report.done, report.skipped, report.failed) == (4, 4, 0, 0)
    assert report.ok and report.out == tmp_path and report.executor == "sequential"
    lines = capsys.readouterr().err.strip().splitlines()
    progress = [line for line in lines if line.startswith("[")]
    assert len(progress) == 4 and progress[-1].startswith("[4/4] done")
    assert (tmp_path / "study.yaml").exists() and (tmp_path / "runs.jsonl").exists()
    assert [row["status"] for row in status_table(tmp_path)] == ["done"] * 4
    df = load_study(tmp_path)
    assert df["broken"].isna().all()
    plan = study.plan()
    for spec in plan:
        manifest = json.loads(
            (tmp_path / "runs" / spec["run_id"] / "manifest.json").read_text()
        )
        assert manifest["outcome_errors"]["broken"].startswith("ZeroDivisionError")
        assert manifest["status"] == "done"


def test_failing_variant_is_captured_while_others_finish(tmp_path):
    study = small_study(tmp_path, levers={"fail": [False, True, False]}, replications=1)
    report = study.run(progress=False)
    assert (report.done, report.failed) == (1, 1)
    (failure,) = report.failures
    assert failure["index"] == 1
    assert failure["error"] == "ValueError: ev_0 is configured to fail"
    folder = tmp_path / "runs" / failure["run_id"]
    text = (folder / "error.txt").read_text()
    assert "Traceback" in text and "make_ev" in text
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["status"] == "failed" and manifest["factors"] == {"fail": True}
    rows = status_table(tmp_path)
    assert [row["status"] for row in rows] == ["done", "failed"]
    assert rows[1]["error"] == "ValueError: ev_0 is configured to fail"
    assert "failed run 1" in str(report)


@pytest.mark.asyncio
async def test_sequential_inside_a_running_event_loop(tmp_path):
    report = small_study(tmp_path).run(progress=False)
    assert report.done == 2
    assert len(load_study(tmp_path)) == 2


def test_unreferenceable_scenario_runs_sequentially_only(tmp_path):
    local = Scenario("local_toy", params=fx.Params)
    local.time(horizon="2h", step="1h")
    local.group("ev", "ev.n", lambda e: fx.EV(0.5, e.p.gain))
    local.record("soc", "ev", attr="soc")
    study = Study(
        local,
        levers={"gain": [0.1, 0.2]},
        seed=1,
        outcomes={"peak": lambda run: run.ts("soc").max()},
        out=tmp_path,
    )
    with pytest.raises(StudyError) as info:
        study.run(workers=2, progress=False)
    message = str(info.value)
    assert "no import reference" in message
    assert "define the scenario at module level of an importable module" in message
    assert not (tmp_path / "runs.jsonl").exists()
    with pytest.raises(StudyError, match="no import reference"):
        study.run(executor=fx.SpyExecutor(), progress=False)
    report = study.run(progress=False)
    assert report.done == 2
    df = load_study(tmp_path)
    low, high = df["peak"].tolist()
    assert high - 0.5 == pytest.approx(2 * (low - 0.5))  # same stream, double gain
    assert df["gain"].tolist() == [0.1, 0.2]


def test_unreferenceable_outcome_is_rejected_for_process_executors(tmp_path):
    study = small_study(tmp_path, outcomes={"peak": lambda run: 1.0})
    with pytest.raises(StudyError, match=r"outcome\(s\) \['peak'\]"):
        study.run(executor="process", workers=2, progress=False)


def test_keyboard_interrupt_writes_status_and_reraises(tmp_path):
    calls = []

    def interrupt_second(run):
        calls.append(run.run_id)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return 1.0

    study = small_study(
        tmp_path,
        levers={"algorithm": ["greedy", "proportional", "market"]},
        outcomes={"value": interrupt_second},
    )
    with pytest.raises(KeyboardInterrupt):
        study.run(progress=False)
    rows = status_table(tmp_path)
    assert [row["status"] for row in rows] == ["done", "pending", "pending"]
    partial = tmp_path / "runs" / rows[1]["run_id"]
    assert (partial / "spec.json").exists() and not (partial / "manifest.json").exists()
    report = small_study(
        tmp_path,
        levers={"algorithm": ["greedy", "proportional", "market"]},
        outcomes={"value": lambda run: 2.0},
    ).run(progress=False)
    assert (report.done, report.skipped) == (2, 1)
    assert [row["status"] for row in status_table(tmp_path)] == ["done"] * 3


# ----------------------------------------------------------------- other executors


def test_user_executor_is_used_and_not_shut_down(tmp_path):
    spy = fx.SpyExecutor()
    report = small_study(tmp_path).run(executor=spy, progress=False)
    assert report.done == 2 and report.executor == "SpyExecutor (caller-owned)"
    assert len(spy.submitted) == 2
    assert spy.shutdowns == 0
    (spec,) = spy.submitted[0]
    assert json.loads(json.dumps(spec)) == spec


def test_thread_pool_runs_in_process(tmp_path):
    with concurrent.futures.ThreadPoolExecutor(2) as threads:
        report = small_study(tmp_path, outcomes={"peak": lambda run: 1.0}).run(
            executor=threads, progress=False
        )
        assert report.done == 2
    assert load_study(tmp_path)["peak"].tolist() == [1.0, 1.0]


def test_executor_failures_leave_never_started_runs_pending(tmp_path):
    broken_pool = concurrent.futures.process.BrokenProcessPool("worker died")
    report = small_study(tmp_path).run(
        executor=fx.SpyExecutor(fail_with=broken_pool), progress=False
    )
    assert (report.done, report.failed, report.pending) == (0, 0, 2)
    assert not report.ok and report.failures == []
    error = report.not_started[0]["error"]
    assert error.startswith("not started: the executor broke before the run began")
    assert "BrokenProcessPool: worker died" in error and "workers=N" in error
    assert not (tmp_path / "runs" / report.not_started[0]["run_id"]).exists()
    assert [row["status"] for row in status_table(tmp_path)] == ["pending", "pending"]
    assert "0 failed, 2 not started" in str(report)
    assert "  not started: run 0 (" in str(report)


class SharedBrokenPool(concurrent.futures.Executor):
    """A caller-owned pool whose worker died: run 0 was in flight, the rest not."""

    def submit(self, fn, spec):
        future = concurrent.futures.Future()
        if spec["index"] == 0:
            folder = Path(spec["out"])
            folder.mkdir(parents=True)
            (folder / "spec.json").write_text("{}")
        future.set_exception(
            concurrent.futures.process.BrokenProcessPool("a child process died")
        )
        return future


def test_a_broken_caller_owned_pool_blames_no_run_and_keeps_unstarted_runs_pending(
    tmp_path,
):
    report = three_runs(tmp_path).run(executor=SharedBrokenPool(), progress=False)
    assert (report.done, report.failed, report.pending) == (0, 1, 2)
    (failure,) = report.failures
    assert failure["index"] == 0
    assert failure["error"].startswith("a worker process of the executor died")
    assert "may come from another run" in failure["error"]
    assert [entry["index"] for entry in report.not_started] == [1, 2]
    for entry in report.not_started:
        assert "possibly while running another run" in entry["error"]
        assert "if __name__" not in entry["error"]
    assert [row["status"] for row in status_table(tmp_path)] == [
        "failed",
        "pending",
        "pending",
    ]
    rerun = three_runs(tmp_path).run(progress=False)
    assert (rerun.done, rerun.failed, rerun.pending) == (3, 0, 0) and rerun.ok


class FakePool(concurrent.futures.Executor):
    """One in-process 'worker'; *behaviour(spec)* decides how it ends.

    ``"ok"`` runs the spec, ``"crash"`` starts it and dies, ``"no_start"``
    dies before starting, ``"commit_then_crash"`` finishes the run but its
    result is lost, ``"interrupt"`` raises KeyboardInterrupt.
    """

    log: list = []

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.shutdown_calls = []

    def submit(self, fn, spec):
        mode = self.behaviour(spec)
        self.log.append((spec["index"], mode))
        future = concurrent.futures.Future()
        died = concurrent.futures.process.BrokenProcessPool("a child process died")
        if mode == "ok":
            future.set_result(fn(spec))
        elif mode == "crash":
            folder = Path(spec["out"])
            folder.mkdir(parents=True)
            (folder / "spec.json").write_text("{}")
            future.set_exception(died)
        elif mode == "commit_then_crash":
            fn(spec)
            future.set_exception(died)
        elif mode == "interrupt":
            future.set_exception(KeyboardInterrupt())
        else:
            future.set_exception(died)
        return future

    def shutdown(self, wait=True, *, cancel_futures=False):
        self.shutdown_calls.append((wait, cancel_futures))


def fake_pools(monkeypatch, behaviour):
    pools = []

    def make():
        pools.append(FakePool(behaviour))
        return pools[-1]

    FakePool.log = []
    monkeypatch.setattr(runner_module, "_spawn_pool", make)
    return pools


def three_runs(out):
    return small_study(out, levers={"algorithm": ["greedy", "proportional", "market"]})


def test_a_crashing_worker_fails_only_its_own_run(tmp_path, monkeypatch):
    pools = fake_pools(
        monkeypatch,
        lambda spec: (
            "crash" if spec["factors"]["algorithm"] == "proportional" else "ok"
        ),
    )
    report = three_runs(tmp_path).run(workers=2, progress=False)
    assert (report.done, report.failed) == (2, 1)
    (failure,) = report.failures
    assert failure["index"] == 1
    assert failure["error"].startswith("the worker process died while running this run")
    folder = tmp_path / "runs" / failure["run_id"]
    assert sorted(p.name for p in folder.iterdir()) == [
        "error.txt",
        "manifest.json",
        "spec.json",
    ]
    assert [row["status"] for row in status_table(tmp_path)] == [
        "done",
        "failed",
        "done",
    ]
    assert len(pools) == 3 and all(pool.shutdown_calls for pool in pools)


def test_a_run_that_committed_before_its_worker_died_counts_as_done(
    tmp_path, monkeypatch
):
    fake_pools(
        monkeypatch,
        lambda spec: "commit_then_crash" if spec["index"] == 0 else "ok",
    )
    report = three_runs(tmp_path).run(workers=2, progress=False)
    assert (report.done, report.failed) == (3, 0), report.failures
    assert [row["status"] for row in status_table(tmp_path)] == ["done"] * 3
    assert load_study(tmp_path)["mean_soc"].notna().all()


def test_workers_that_cannot_start_stop_the_study_and_leave_runs_pending(
    tmp_path, monkeypatch
):
    fake_pools(monkeypatch, lambda spec: "no_start")
    report = three_runs(tmp_path).run(executor="process", workers=1, progress=False)
    assert (report.done, report.failed, report.pending) == (0, 0, 3)
    assert FakePool.log == [(0, "no_start")]  # nothing else was started
    assert len({entry["error"] for entry in report.not_started}) == 1
    error = report.not_started[0]["error"]
    assert error.startswith("not started: the worker process exited before the run")
    assert "if __name__ ==" in error
    assert [row["status"] for row in status_table(tmp_path)] == ["pending"] * 3
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())


def test_an_interrupt_waits_for_running_runs_and_starts_no_more(tmp_path, monkeypatch):
    pools = fake_pools(
        monkeypatch,
        lambda spec: "interrupt" if spec["index"] == 1 else "ok",
    )
    study = small_study(
        tmp_path, levers={"algorithm": ["greedy", "proportional", "market"]}
    )
    with pytest.raises(KeyboardInterrupt):
        study.run(workers=2, progress=False)
    assert [index for index, _ in FakePool.log] == [0, 1]
    assert pools[0].shutdown_calls == [(True, False)]
    rows = status_table(tmp_path)
    assert [row["status"] for row in rows] == ["done", "pending", "pending"]


def test_spawn_process_pool_end_to_end(tmp_path, monkeypatch):
    for key in WORKER_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MKL_NUM_THREADS", "3")
    study = small_study(
        tmp_path,
        levers=None,
        design=Explicit(
            [
                {"algorithm": "greedy"},
                {"algorithm": "proportional"},
                {"algorithm": "market", "crash": True},  # its worker calls os._exit
            ]
        ),
        outcomes=[
            fx.mean_soc,
            fx.omp_threads,
            fx.mkl_threads,
            fx.hash_seed,
            fx.worker_pid,
        ],
    )
    report = study.run(workers=3, progress=False)
    assert (report.done, report.failed) == (2, 1), report.failures
    assert report.failures[0]["index"] == 2
    assert "worker process died" in report.failures[0]["error"]
    assert report.executor == "process pool (3 workers)"
    df = load_study(tmp_path)
    assert df["status"].tolist() == ["done", "done", "failed"]
    done = df[df["status"] == "done"]
    assert done["omp_threads"].tolist() == ["1", "1"]
    assert done["mkl_threads"].tolist() == ["3", "3"]
    assert done["hash_seed"].tolist() == ["0", "0"]
    assert os.getpid() not in done["worker_pid"].tolist()
    assert done["worker_pid"].nunique() == 2  # one fresh process per run
    for key in ("OMP_NUM_THREADS", "PYTHONHASHSEED", "OPENBLAS_NUM_THREADS"):
        assert key not in os.environ
    assert os.environ["MKL_NUM_THREADS"] == "3"
    manifest = json.loads(
        (tmp_path / "runs" / df["run_id"][0] / "manifest.json").read_text()
    )
    assert manifest["pythonhashseed"] == "0"
    sequential = load_run(tmp_path / "runs" / df["run_id"][0])
    spec = study.plan()[0]
    assert (
        sequential.checksum()
        == fx.scenario.run(spec["params"], seed=spec["seed"]).checksum()
    )


INTERRUPTED_STUDY = """
import sys
from mango.scenario import Study
from tests.unit_tests.scenario import study_fixtures as fx

if __name__ == "__main__":
    study = Study(fx.scenario, levers={"algorithm": ["greedy", "market"]},
                  overrides={"sleep_s": 0.4}, seed=1, out=sys.argv[1])
    study.run(workers=2, progress=False)
"""


def test_interrupting_only_the_parent_lets_running_runs_commit(tmp_path):
    script = tmp_path / "interrupted.py"
    script.write_text(INTERRUPTED_STUDY)
    out = tmp_path / "out"
    root = Path(fx.__file__).resolve().parents[3]
    child = subprocess.Popen(
        [sys.executable, str(script), str(out)],
        cwd=root,
        env=dict(os.environ, PYTHONPATH=str(root)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while len(list(out.glob("runs/*/spec.json"))) < 2:  # both runs started
            assert child.poll() is None, child.communicate()
            assert time.monotonic() < deadline, "the runs did not start"
            time.sleep(0.02)
        child.send_signal(signal.SIGINT)  # the parent only, not the workers
        _, stderr = child.communicate(timeout=30)
    finally:
        child.kill()
    assert child.returncode != 0 and "KeyboardInterrupt" in stderr
    assert "waiting for 2 running runs to finish" in stderr
    assert [row["status"] for row in status_table(out)] == ["done", "done"]


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_ctrl_c_to_the_process_group_lets_running_runs_commit(tmp_path):
    # a terminal sends Ctrl+C to the whole foreground process group
    script = tmp_path / "interrupted.py"
    script.write_text(INTERRUPTED_STUDY)
    out = tmp_path / "out"
    root = Path(fx.__file__).resolve().parents[3]
    child = subprocess.Popen(
        [sys.executable, str(script), str(out)],
        cwd=root,
        env=dict(os.environ, PYTHONPATH=str(root)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30
        while len(list(out.glob("runs/*/spec.json"))) < 2:
            assert child.poll() is None, child.communicate()
            assert time.monotonic() < deadline, "the runs did not start"
            time.sleep(0.02)
        os.killpg(child.pid, signal.SIGINT)
        _, stderr = child.communicate(timeout=30)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGKILL)
    assert "waiting for 2 running runs to finish" in stderr
    assert stderr.count("KeyboardInterrupt") == 1, stderr  # the parent's only
    assert [row["status"] for row in status_table(out)] == ["done", "done"]


# ----------------------------------------------------------------- references


def test_run_one_fails_when_the_imported_scenario_was_changed(tmp_path):
    spec = small_study(tmp_path).plan()[0]
    spec["scenario_fingerprint"] = "0" * 12  # the parent had other declarations
    outcome = run_one(spec)
    assert outcome["status"] == "failed"
    assert "differs from the one the study was planned with" in outcome["error"]
    assert "workers=1" in (Path(spec["out"]) / "error.txt").read_text()


MAIN_SCRIPT = """
from mango.scenario import Scenario
from tests.unit_tests.scenario.study_fixtures import mean_soc as imported_soc

top = Scenario("top")

def outcome(run):
    return 1.0

if __name__ == "__main__":
    guarded = Scenario("guarded")
"""


def test_main_refs_must_be_defined_outside_the_main_guard(tmp_path, monkeypatch):
    import types

    script = tmp_path / "run_study.py"
    script.write_text(MAIN_SCRIPT)
    main = types.ModuleType("__main__")
    main.__file__ = str(script)
    monkeypatch.setitem(sys.modules, "__main__", main)
    check_refs("__main__:top", {"o": (None, "__main__:outcome")})
    check_refs("__main__:top", {"o": (None, "__main__:imported_soc")})
    with pytest.raises(StudyError, match="not defined at the top level") as info:
        check_refs("__main__:guarded", {})
    assert str(script) in str(info.value) and "move the definition" in str(info.value)
    with pytest.raises(StudyError, match="Outcome 'o' '__main__:missing'"):
        check_refs("__main__:top", {"o": (None, "__main__:missing")})


def test_worker_env_is_documented_in_the_runner_module():
    analyzer = pytest.importorskip("sphinx.pycode").ModuleAnalyzer
    docs = analyzer.for_module("mango.scenario.runner").find_attr_docs()
    assert ("", "WORKER_ENV") in docs  # the API reference lists runner.WORKER_ENV

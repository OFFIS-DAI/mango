"""Study operations: plan history (--set variants), safe outcome recomputation,
agent and outcome errors in reports, executor settings and reused workers."""

import json
import warnings

import pytest

pytest.importorskip("pandas")

from mango import Agent  # noqa: E402
from mango.scenario import (  # noqa: E402
    Scenario,
    ScenarioParams,
    SlurmExecutor,
    Study,
    StudyError,
    load_study,
)
from mango.scenario.runner import resolve_executor  # noqa: E402
from mango.scenario.store import StudyStore  # noqa: E402
from mango.scenario.study import _pool_hints  # noqa: E402

from . import study_fixtures as fx  # noqa: E402


def make_study(out, **kwargs):
    options = {
        "levers": {"algorithm": ["greedy", "proportional"]},
        "seed": 3,
        "outcomes": [fx.mean_soc],
        "out": out,
        "format": "csv",
    }
    options.update(kwargs)
    return Study(fx.scenario, **options)


def run_quietly(study, **kwargs):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        report = study.run(progress=False, **kwargs)
    return report, [str(item.message) for item in caught]


# ----------------------------------------------------------------- plans (--set)


def test_a_variant_keeps_the_previous_plan_reachable(tmp_path):
    report, messages = run_quietly(make_study(tmp_path))
    assert report.done == 2 and not messages
    report, messages = run_quietly(make_study(tmp_path, overrides={"gain": 0.5}))
    assert report.done == 2
    (message,) = messages
    assert "held another plan of study 'study_toy' (overrides {} -> " in message
    assert '{"gain": 0.5}' in message
    assert "2 finished runs of the previous plan are not in the new plan" in message
    assert "plan='all'" in message
    store = StudyStore(tmp_path)
    (plan_id,) = store.plan_ids()
    definition, specs = store.read_plan(plan_id)
    assert definition["overrides"] == {} and len(specs) == 2

    current = load_study(tmp_path)
    assert current["gain"].tolist() == [0.5, 0.5]  # the override is a column
    everything = load_study(tmp_path, plan="all")
    assert len(everything) == 4
    assert everything["plan"].tolist() == ["current"] * 2 + [plan_id] * 2
    assert everything["gain"].tolist() == [0.5, 0.5, 0.05, 0.05]
    assert set(everything["status"]) == {"done"}
    earlier = load_study(tmp_path, plan=plan_id)  # the baseline had no overrides
    assert len(earlier) == 2 and "gain" not in earlier.columns
    with pytest.raises(KeyError, match="No plan 'nope'"):
        load_study(tmp_path, plan="nope")

    # back to the baseline: its runs are done, the variant's plan is kept too
    report, messages = run_quietly(make_study(tmp_path))
    assert report.skipped == 2 and len(store.plan_ids()) == 2
    assert "overrides" in messages[0] and "-> {}" in messages[0]


def test_a_growing_plan_archives_quietly(tmp_path):
    run_quietly(make_study(tmp_path))
    bigger = make_study(
        tmp_path, levers={"algorithm": ["greedy", "proportional", "market"]}
    )
    report, messages = run_quietly(bigger)
    assert (report.done, report.skipped) == (1, 2) and not messages
    assert len(StudyStore(tmp_path).plan_ids()) == 1


def test_overrides_that_factors_cover_are_no_columns(tmp_path):
    run_quietly(make_study(tmp_path, overrides={"algorithm": "market", "ev.n": 1}))
    frame = load_study(tmp_path)
    assert "ev.n" in frame.columns
    assert frame.columns.tolist().count("algorithm") == 1


# ----------------------------------------------------------------- outcomes


def test_recomputation_never_destroys_stored_values(tmp_path):
    run_quietly(make_study(tmp_path))
    stored = load_study(tmp_path)["mean_soc"].tolist()

    def mean_soc(run):
        raise RuntimeError("a helper changed")

    with pytest.warns(
        UserWarning, match="could not be recomputed for 2 of 2"
    ) as caught:
        frame = load_study(tmp_path, outcomes={"mean_soc": mean_soc})
    assert "stored values were kept for 2 runs" in str(caught[0].message)
    assert frame["mean_soc"].tolist() == stored
    assert frame["outcome_errors"].isna().all()
    assert load_study(tmp_path)["mean_soc"].tolist() == stored

    def agents(run):
        return len(run.agent("ev_0").__dict__)

    with pytest.warns(UserWarning, match="can only be computed while the study runs"):
        frame = load_study(tmp_path, outcomes={"agents": agents})
    assert frame["agents"].isna().all()
    assert set(frame["outcome_errors"][0]) == {"agents"}

    def agents_fixed(run):
        return 1

    frame = load_study(tmp_path, outcomes={"agents": agents_fixed})
    assert frame["agents"].tolist() == [1, 1]
    assert frame["outcome_errors"].isna().all()


def test_outcome_errors_are_reported(tmp_path, capsys):
    from .cli_helpers import cli

    study = make_study(tmp_path, outcomes=[fx.mean_soc, fx.broken])
    report, _ = run_quietly(study)
    assert len(report.outcome_errors) == 2
    text = str(report)
    assert "2 runs: 2 done, 0 skipped, 0 failed (2 with outcome errors)" in text
    # the same error of both runs is listed once
    assert (
        "outcome 'broken' failed in 2 runs (0, 1): ZeroDivisionError: no outcome today"
    ) in text
    assert text.count("no outcome today") == 1
    frame = load_study(tmp_path)
    assert frame["outcome_errors"][0] == {
        "broken": "ZeroDivisionError: no outcome today"
    }
    report, _ = run_quietly(make_study(tmp_path, outcomes=[fx.mean_soc, fx.broken]))
    assert report.skipped == 2 and len(report.outcome_errors) == 2  # still listed
    code, out, _ = cli(capsys, "status", tmp_path)
    assert code == 0 and "2 with outcome errors" in out
    assert "outcome 'broken' failed in 2 runs (0, 1)" in out


class Faulty(Agent):
    def handle_message(self, content, meta):
        pass

    def on_ready(self):
        self.schedule_periodic_task(self.tick, delay=3600)

    async def tick(self):
        raise AttributeError("'str' object has no attribute 'aid'")


class FaultyParams(ScenarioParams):
    n: int = 1


faulty = Scenario("faulty", params=FaultyParams)
faulty.time(horizon="3h", step="1h")
faulty.group("agent", "n", lambda e: Faulty())
faulty.record_world("n", lambda ctx: 1.0)


def test_exceptions_in_agent_tasks_fail_the_run(tmp_path):
    study = Study(faulty, levers={"n": [1]}, seed=1, out=tmp_path, format="csv")
    report, _ = run_quietly(study)
    assert (report.done, report.failed) == (0, 1)
    (failure,) = report.failures
    assert "AgentTaskError" in failure["error"] and "Faulty.tick" in failure["error"]
    folder = StudyStore(tmp_path).run_dir(failure["run_id"])
    assert "AttributeError" in (folder / "error.txt").read_text()


# ----------------------------------------------------------------- executors


def test_runs_per_worker_resolution():
    choice = resolve_executor(workers=4, runs_per_worker=20)
    assert (choice.kind, choice.workers, choice.runs_per_worker) == ("process", 4, 20)
    assert choice.describe() == "process pool (4 workers, 20 runs per worker)"
    assert resolve_executor(workers=4, runs_per_worker=1).describe() == (
        "process pool (4 workers)"
    )
    with pytest.raises(StudyError, match="runs_per_worker=5 applies to the managed"):
        resolve_executor(runs_per_worker=5)
    with pytest.raises(StudyError, match="SlurmExecutor\\(runs_per_task=K\\)"):
        resolve_executor("slurm", runs_per_worker=5)
    with pytest.raises(StudyError, match="runs_per_worker must be an int >= 1"):
        resolve_executor(workers=2, runs_per_worker=0)


def test_reused_workers_run_several_runs_each(tmp_path):
    study = make_study(
        tmp_path,
        levers={"ev.n": [1, 2, 3, 4]},
        outcomes=[f"{fx.REF}:worker_pid"],
    )
    report = study.run(workers=2, runs_per_worker=2, progress=False)
    assert report.done == 4 and report.executor.endswith("2 runs per worker)")
    pids = load_study(tmp_path)["worker_pid"].tolist()
    # the first worker to finish takes the next run (a slow one may leave its
    # second run to a fresh worker), so some process ran two runs
    assert max(pids.count(pid) for pid in pids) == 2


def test_short_runs_on_fresh_processes_get_a_hint():
    choice = resolve_executor(workers=2, runs_per_worker=1)
    choice.usage.update(spawn_s=2.0)  # measured by the pool
    sequential, reuse = _pool_hints(choice, [0.05, 0.07])
    assert "sequential would have been faster" in sequential
    assert "0.1 s in all, less than starting 2 worker processes" in sequential
    assert "runs took 0.07 s (median, without the start-up" in reuse
    assert "about 97% of the worker time goes into starting workers" in reuse
    assert "runs_per_worker=200" in reuse and "--runs-per-worker 200" in reuse
    # many runs: no sequential hint, a reuse factor for 10x the start-up
    (hint,) = _pool_hints(choice, [0.5] * 40)
    assert "runs_per_worker=40" in hint
    assert _pool_hints(choice, [30.0, 40.0]) == []
    # start-up a fifth of the time (4.6 s runs, 1.1 s start-up): no hint
    choice.usage.update(spawn_s=1.1)
    assert _pool_hints(choice, [4.58] * 40) == []
    # the default (auto) and enough reuse need no hint
    auto = resolve_executor(workers=2)
    auto.usage.update(spawn_s=2.0)
    assert auto.runs_per_worker == "auto" and _pool_hints(auto, [0.5] * 40) == []
    assert (
        _pool_hints(resolve_executor(workers=2, runs_per_worker=50), [0.5] * 40) == []
    )
    assert _pool_hints(resolve_executor(), [0.1, 0.1]) == []


def test_auto_reuse_measures_start_up_and_run_time(tmp_path):
    study = make_study(
        tmp_path,
        levers={"ev.n": [1, 2, 3, 4, 5, 6]},
        outcomes=[f"{fx.REF}:worker_pid"],
    )
    report = study.run(workers=2, progress=False)  # runs_per_worker='auto'
    assert report.done == 6
    pids = load_study(tmp_path)["worker_pid"].tolist()
    # short runs: the workers are reused instead of a fresh process per run
    assert len(set(pids)) < 6
    assert "workers reused for up to" in report.executor
    assert report.executor.startswith("process pool (2 workers)")
    frame = load_study(tmp_path)
    assert (frame["cpu_s"] > 0).all() and (
        frame["cpu_s"] <= frame["wall_s"] + 0.5
    ).all()


def test_runs_per_worker_one_keeps_a_fresh_process_per_run(tmp_path):
    study = make_study(
        tmp_path, levers={"ev.n": [1, 2, 3]}, outcomes=[f"{fx.REF}:worker_pid"]
    )
    report = study.run(workers=2, runs_per_worker=1, progress=False)
    assert report.done == 3 and report.executor == "process pool (2 workers)"
    assert load_study(tmp_path)["worker_pid"].nunique() == 3


def test_executor_settings_of_a_study(tmp_path):
    study = make_study(tmp_path, executor={"workers": 3, "runs_per_worker": 10})
    assert study._choose_executor(None, None, None).describe() == (
        "process pool (3 workers, 10 runs per worker)"
    )
    # explicit arguments replace the settings
    assert study._choose_executor("sequential", None, None).kind == "sequential"
    assert study._choose_executor(None, 2, None).runs_per_worker == 10
    assert study._choose_executor("process", None, None).workers == 3
    both = make_study(tmp_path, executor={"kind": "slurm", "workers": 4})
    assert both._choose_executor(None, None, None).kind == "slurm"
    assert both._choose_executor(None, 2, None).describe() == "process pool (2 workers)"
    on_slurm = make_study(
        tmp_path,
        executor={"kind": "slurm", "slurm": {"partition": "short", "runs_per_task": 4}},
    )
    choice = on_slurm._choose_executor(None, None, None)
    assert isinstance(choice.executor, SlurmExecutor)
    assert (choice.executor.partition, choice.executor.runs_per_task) == ("short", 4)
    assert make_study(tmp_path, executor="sequential").executor == {
        "kind": "sequential"
    }
    assert (
        make_study(tmp_path).definition(make_study(tmp_path).plan())["executor"] is None
    )


@pytest.mark.parametrize(
    "executor, message",
    [
        ({"worker": 2}, "unknown key 'worker'. Did you mean 'workers'?"),
        ({"kind": "cluster"}, "executor.kind must be one of"),
        ({"workers": 0}, "executor.workers must be an int >= 1"),
        ({"slurm": {"partiton": "x"}}, "executor.slurm: .*partiton"),
        ({"slurm": {"extra_sbatch": ["module load x"]}}, "belong in setup"),
        ({"slurm": "short"}, "executor.slurm must be a mapping"),
        (3, "executor must be 'sequential', 'process', 'slurm' or a mapping"),
    ],
)
def test_invalid_executor_settings(tmp_path, executor, message):
    with pytest.raises(StudyError, match=message):
        make_study(tmp_path, executor=executor)


def test_study_files_take_an_executor_block(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "study.yaml").write_text(
        f"scenario: {fx.REF}:scenario\n"
        "levers: {algorithm: [greedy]}\n"
        "executor: {workers: 2, runs_per_worker: 8, slurm: {partition: short}}\n"
    )
    study = Study.from_yaml(tmp_path / "study.yaml")
    assert study.executor == {
        "workers": 2,
        "runs_per_worker": 8,
        "slurm": {"partition": "short"},
    }
    (tmp_path / "bad.yaml").write_text(
        f"scenario: {fx.REF}:scenario\nexecutor: {{workers: 2, reuse: true}}\n"
    )
    with pytest.raises(StudyError, match="bad.yaml: executor: unknown key 'reuse'"):
        Study.from_yaml(tmp_path / "bad.yaml")


def test_manifest_carries_planning_and_execution_provenance(tmp_path):
    run_quietly(make_study(tmp_path))
    store = StudyStore(tmp_path)
    spec = store.read_runs()[0]
    manifest = store.read_manifest(spec["run_id"])
    for key in (
        "project_root",
        "git",
        "planned_git",
        "code_fingerprint",
        "data_fingerprint",
        "code_files",
        "data_files",
    ):
        assert key in manifest, key
    assert "tests/unit_tests/scenario/study_fixtures.py" in manifest["code_files"]
    assert manifest["planned_git"] == spec["git"]
    assert json.loads(json.dumps(manifest)) == manifest


def test_override_columns_are_not_sobol_outcomes(tmp_path):
    from mango.scenario import Sobol

    study = Study(
        fx.scenario,
        uncertainties={"gain": {"range": [0.01, 0.1]}},
        design=Sobol(4),
        overrides={"ev.n": 1},
        seed=2,
        outcomes=[fx.mean_soc],
        out=tmp_path,
        format="csv",
    )
    run_quietly(study)
    frame = load_study(tmp_path)
    assert frame.override_columns == ("ev.n",)
    assert frame[frame["status"] == "done"].override_columns == ("ev.n",)
    indices = frame.sobol_indices()
    assert set(indices["outcome"]) == {"mean_soc"}

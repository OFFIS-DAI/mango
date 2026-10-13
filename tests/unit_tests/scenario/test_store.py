import csv
import json
import pickle
import shutil
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pd = pytest.importorskip("pandas")

import yaml  # noqa: E402

from mango.scenario import (  # noqa: E402
    RunResult,
    Study,
    StudyError,
    StudyFrame,
    load_study,
)
from mango.scenario.store import (  # noqa: E402
    FRAME_COLUMNS,
    STATUS_COLUMNS,
    StudyStore,
)

from . import study_fixtures as fx  # noqa: E402

LEVERS = {"algorithm": ["greedy", "proportional"]}
UNCERTAINTIES = {"ev.n": [1, 2], "comm.loss_percent": {"ideal": 0.0, "lossy": 0.1}}


def make_study(out, **kwargs):
    options = {
        "uncertainties": UNCERTAINTIES,
        "levers": LEVERS,
        "replications": 2,
        "seed": 11,
        "outcomes": [fx.mean_soc, fx.first_soc],
        "out": out,
    }
    options.update(kwargs)
    return Study(fx.scenario, **options)


@pytest.fixture(scope="module")
def study_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("study")
    report = make_study(out).run(progress=False)
    assert report.done == 16
    return out


@pytest.fixture
def failing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(fx, "FAIL_ENABLED", True)
    study = make_study(
        tmp_path, uncertainties=None, levers={"fail": [False, True]}, replications=1
    )
    report = study.run(progress=False)
    assert (report.done, report.failed) == (1, 1)
    return tmp_path


def statuses(out):
    with open(out / "status.csv", newline="", encoding="utf-8") as handle:
        return [row["status"] for row in csv.DictReader(handle)]


# ----------------------------------------------------------------- resume


def test_second_run_skips_everything_without_submitting(study_dir):
    spy = fx.SpyExecutor()
    report = make_study(study_dir).run(executor=spy, progress=False)
    assert (report.total, report.done, report.skipped, report.failed) == (16, 0, 16, 0)
    assert spy.submitted == [] and spy.shutdowns == 0
    report = make_study(study_dir).run(workers=4, progress=False)
    assert report.skipped == 16 and report.wall_s < 1.0


def test_seed_none_is_persisted_and_reused(tmp_path):
    first = make_study(tmp_path, seed=None, uncertainties=None, replications=1)
    report = first.run(progress=False)
    assert report.done == 2
    persisted = yaml.safe_load((tmp_path / "study.yaml").read_text())["seed"]
    assert isinstance(persisted, int) and persisted == first.plan().seed
    second = make_study(tmp_path, seed=None, uncertainties=None, replications=1)
    assert second.plan().seed == persisted
    spy = fx.SpyExecutor()
    assert second.run(executor=spy, progress=False).skipped == 2
    assert spy.submitted == []
    assert StudyStore(tmp_path).persisted_seed() == persisted


def test_changed_parameters_get_new_folders(study_dir):
    old = {spec["run_id"] for spec in make_study(study_dir).plan()}
    new = make_study(study_dir, base={"gain": 0.07}).plan()
    assert not old & set(new.run_ids)


def test_failed_runs_are_retried(failing_dir, monkeypatch):
    spy = fx.SpyExecutor()
    kept = make_study(
        failing_dir, uncertainties=None, levers={"fail": [False, True]}, replications=1
    ).run(executor=spy, retry_failed=False, progress=False)
    assert (kept.skipped, kept.failed, kept.done) == (1, 1, 0)
    assert kept.failures[0]["error"] == "ValueError: ev_0 is configured to fail"
    assert spy.submitted == []
    assert statuses(failing_dir) == ["done", "failed"]
    monkeypatch.setattr(fx, "FAIL_ENABLED", False)
    retried = make_study(
        failing_dir, uncertainties=None, levers={"fail": [False, True]}, replications=1
    ).run(progress=False)
    assert (retried.skipped, retried.done, retried.failed) == (1, 1, 0)
    assert statuses(failing_dir) == ["done", "done"]
    run_dir = failing_dir / "runs" / retried_run_id(failing_dir, 1)
    assert not (run_dir / "error.txt").exists()


def retried_run_id(out, index):
    return StudyStore(out).read_runs()[index]["run_id"]


def test_partial_folder_is_cleaned_and_rerun(tmp_path):
    study = make_study(tmp_path, uncertainties=None, replications=1)
    plan = study.plan()
    partial = tmp_path / "runs" / plan[0]["run_id"]
    partial.mkdir(parents=True)
    (partial / "junk.tmp").write_text("half-written")
    corrupt = tmp_path / "runs" / plan[1]["run_id"]
    corrupt.mkdir(parents=True)
    (corrupt / "manifest.json").write_text("{not json")
    store = StudyStore(tmp_path)
    assert store.run_state(plan[0]["run_id"]) == "partial"
    assert store.run_state(plan[1]["run_id"]) == "partial"
    report = study.run(progress=False)
    assert report.done == 2
    assert not (partial / "junk.tmp").exists()
    assert store.run_state(plan[0]["run_id"]) == "done"
    assert store.run_state(plan[1]["run_id"]) == "done"


def test_triage_and_states(failing_dir, tmp_path):
    store = StudyStore(failing_dir)
    specs = store.read_runs()
    assert [store.run_state(spec["run_id"]) for spec in specs] == ["done", "failed"]
    assert store.run_state("0123456789ab") == "missing"
    keep = store.triage(specs, retry_failed=False)
    assert ([s["index"] for s in keep.skipped], keep.pending) == ([0], [])
    assert keep.failed[0][1]["status"] == "failed"
    retry = store.triage(specs, retry_failed=True)
    assert [spec["index"] for spec in retry.pending] == [1]
    assert not store.run_dir(specs[1]["run_id"]).exists()
    assert "StudyStore(" in repr(store)


def test_status_csv(failing_dir):
    with open(failing_dir / "status.csv", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert tuple(rows[0]) == STATUS_COLUMNS
    assert [row["index"] for row in rows] == ["0", "1"]
    assert rows[0]["error"] == "" and float(rows[0]["wall_s"]) > 0
    assert rows[1]["error"] == "ValueError: ev_0 is configured to fail"
    store = StudyStore(failing_dir)
    assert [row["status"] for row in store.status_rows()] == ["done", "failed"]


def test_study_files(study_dir):
    definition = yaml.safe_load((study_dir / "study.yaml").read_text())
    assert definition["seed"] == 11 and definition["replications"] == 2
    assert definition["scenario"] == f"{fx.REF}:scenario"
    assert definition["created"] <= definition["updated"]
    lines = (study_dir / "runs.jsonl").read_text().splitlines()
    assert len(lines) == 16
    specs = [json.loads(line) for line in lines]
    assert [spec["index"] for spec in specs] == list(range(16))
    assert specs == make_study(study_dir).plan().specs


def test_read_errors(tmp_path):
    store = StudyStore(tmp_path)
    with pytest.raises(FileNotFoundError, match="is not a study folder"):
        load_study(tmp_path)
    (tmp_path / "runs.jsonl").write_text('{"run_id": "a"}\nnot json\n')
    with pytest.raises(StudyError, match="line 2 is not valid JSON"):
        store.read_runs()
    (tmp_path / "study.yaml").write_text("a: [")
    with pytest.raises(StudyError, match="is not valid YAML"):
        store.read_definition()
    assert store.read_manifest("nope") is None


# ----------------------------------------------------------------- load_study


def test_load_study_columns(study_dir):
    df = load_study(study_dir)
    assert isinstance(df, StudyFrame)
    assert list(df.columns) == [
        *FRAME_COLUMNS,
        "ev.n",
        "comm.loss_percent",
        "comm.loss_percent:label",
        "algorithm",
        "mean_soc",
        "first_soc",
    ]
    assert len(df) == 16
    assert df["index"].tolist() == list(range(16))
    assert set(df["status"]) == {"done"}
    assert df["comm.loss_percent:label"].tolist()[:8] == ["ideal"] * 4 + ["lossy"] * 4
    assert df["comm.loss_percent"].tolist()[4] == 0.1
    assert df["mean_soc"].notna().all() and (df["wall_s"] > 0).all()
    assert df.study_path == study_dir.absolute()
    assert df.study_spec["seed"] == 11
    # common random numbers: replication r starts identically in every variant
    first = df[df["ev.n"] == 1].groupby("replication")["first_soc"].nunique()
    assert (first == 1).all()


def test_study_frame_type_survives_slicing_and_groupby(study_dir):
    df = load_study(study_dir)
    greedy = df[df["algorithm"] == "greedy"]
    assert isinstance(greedy, StudyFrame) and greedy.study_path == df.study_path
    assert isinstance(df.head(3), StudyFrame) and df.head(3).study_path == df.study_path
    assert isinstance(
        df.loc[df["replication"] == 0, ["run_id", "mean_soc"]], StudyFrame
    )
    grouped = df.groupby(["algorithm", "ev.n"])[["mean_soc"]].mean()
    assert isinstance(grouped, StudyFrame)
    assert grouped.shape == (4, 1)
    assert isinstance(df["mean_soc"], pd.Series)
    clone = pickle.loads(pickle.dumps(df))
    assert isinstance(clone, StudyFrame) and clone.study_path == df.study_path


def test_study_frame_run_timeseries_and_failed(study_dir):
    df = load_study(study_dir)
    run = df.run(3)
    assert isinstance(run, RunResult) and run.run_id == df["run_id"][3]
    assert run.params is not None and run.params.ev.n == 1
    assert df.run(df["run_id"][5]).run_id == df["run_id"][5]
    with pytest.raises(KeyError, match="No run with run index 99"):
        df.run(99)
    with pytest.raises(TypeError, match="run id"):
        df.run(1.5)
    assert len(df.failed()) == 0 and isinstance(df.failed(), StudyFrame)
    subset = df[df["algorithm"] == "greedy"]
    ts = subset.timeseries()
    assert set(ts["run_id"]) == set(subset["run_id"])
    assert list(ts.columns)[:2] == ["run_id", "time"]
    soc = df.timeseries(run_ids=df["run_id"][0], variables="soc")
    assert set(soc["variable"]) == {"soc"} and set(soc["run_id"]) == {df["run_id"][0]}
    with pytest.raises(KeyError, match="not in this frame"):
        df.timeseries(run_ids=["nope"])
    with pytest.raises(KeyError, match=r"No variable\(s\) \['sco'\]"):
        df.timeseries(variables=["sco"])
    empty = df[df["status"] == "failed"].timeseries()
    assert len(empty) == 0 and "value" in empty.columns


def test_frame_without_study_path_explains(study_dir):
    df = load_study(study_dir)
    df.study_path = None
    with pytest.raises(StudyError, match="call load_study"):
        df.run(0)


def test_failed_rows_in_frame(failing_dir):
    df = load_study(failing_dir)
    assert df["status"].tolist() == ["done", "failed"]
    failed = df.failed()
    assert failed["index"].tolist() == [1] and failed["fail"].tolist() == [True]
    assert pd.isna(failed["mean_soc"].iloc[0])
    for row in failed.itertuples():  # the docstring example
        assert row.error == "ValueError: ev_0 is configured to fail"
    assert "error" not in df.columns
    with pytest.raises(StudyError, match="has status 'failed'; see .*error.txt"):
        df.run(1)
    with pytest.raises(StudyError, match="not done"):
        df.timeseries(run_ids=df["run_id"].tolist())


def test_pending_rows_in_frame(tmp_path):
    study = make_study(tmp_path, uncertainties=None, replications=1)
    store = StudyStore(tmp_path)
    store.write_runs(study.plan(out=tmp_path).specs)
    df = load_study(tmp_path)
    assert df["status"].tolist() == ["pending", "pending"]
    assert df["wall_s"].isna().all()


def test_outcomes_are_recomputed(study_dir, tmp_path):
    study_dir = Path(shutil.copytree(study_dir, tmp_path / "copy"))
    df = load_study(study_dir, outcomes={"peak_soc": lambda run: run.ts("soc").max()})
    assert df.columns[-1] == "peak_soc"
    assert (df["peak_soc"] >= df["mean_soc"]).all()
    folder = study_dir / "runs" / df["run_id"][0]
    stored = json.loads((folder / "outcomes.json").read_text())
    assert set(stored) == {"mean_soc", "first_soc", "peak_soc"}
    assert load_study(study_dir)["peak_soc"].tolist() == df["peak_soc"].tolist()
    broken = load_study(study_dir, outcomes=[fx.broken])
    assert broken["broken"].isna().all()
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["outcome_errors"] == {
        "broken": "ZeroDivisionError: no outcome today"
    }
    load_study(study_dir, outcomes={"broken": lambda run: 0.0})
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["outcome_errors"] == {}


def test_frame_methods_accept_numpy_run_indices(study_dir):
    df = load_study(study_dir)
    index = df["index"].iloc[2]
    assert type(index).__module__ == "numpy"
    assert df.run(index).run_id == df["run_id"][2]


def test_merge_and_concat_keep_the_study_folder(study_dir):
    df = load_study(study_dir)
    extra = pd.DataFrame({"run_id": df["run_id"], "note": range(len(df))})
    merged = df.merge(extra, on="run_id")
    assert isinstance(merged, StudyFrame) and merged.study_path == df.study_path
    assert merged.run(0).run_id == df["run_id"][0]
    both = pd.concat([df.iloc[:2], df.iloc[2:4]])
    assert isinstance(both, StudyFrame) and both.study_path == df.study_path
    assert len(both.timeseries(variables="soc")["run_id"].unique()) == 4
    other = df.copy()
    other.study_path = Path("/elsewhere")
    assert pd.concat([df, other]).study_path is None  # two folders: no single one


def test_recomputed_outcomes_must_not_clash_with_columns(study_dir, tmp_path):
    study_dir = Path(shutil.copytree(study_dir, tmp_path / "copy"))
    before = (
        study_dir / "runs" / load_study(study_dir)["run_id"][0] / "outcomes.json"
    ).read_text()
    for name in ("status", "algorithm", "comm.loss_percent:label"):
        with pytest.raises(StudyError, match=f"Outcome name '{name}' clashes"):
            load_study(study_dir, outcomes={name: lambda run: 1.0})
    after = (
        study_dir / "runs" / load_study(study_dir)["run_id"][0] / "outcomes.json"
    ).read_text()
    assert after == before

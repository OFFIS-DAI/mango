"""The EV study of ``examples/ev_study`` end to end, on reduced study files.

The example is copied to a temporary folder (it is the project root of its CLI
commands); the tests use short runs (6 households, 2 EVs, an 18 h day with a 6 h
warm-up) so that the whole module stays well below a minute.
"""

import asyncio
import json
import os
import shutil
import sys
import textwrap
from contextlib import contextmanager
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "ev_study"

#: Overrides that shorten every run: start in the evening before the evaluated day.
SHORT = {
    "time.start": "2024-06-01T18:00",
    "time.horizon": "1d",
    "grid.n_households": 6,
}

SMALL_STUDY = """
# 2 comm models x 2 algorithms = 4 short runs
extends: studies/fleet_comparison.yaml
uncertainties: {ev.n: 2}
replications: 1
overrides: {"time.start": "2024-06-01T18:00", "time.horizon": 1d, "grid.n_households": 6}
out: results/small
"""

SOBOL_STUDY = """
# the Sobol follow-up with n = 4: 4 x (2 factors + 2) = 16 short runs
extends: studies/sobol_pv.yaml
uncertainties: {ev.n: 2}
design: {sobol: {n: 4}}
replications: 1
overrides: {"time.start": "2024-06-01T18:00", "time.horizon": 1d, "grid.n_households": 6}
out: results/sobol_small
"""


@contextmanager
def project(folder: Path):
    """Work inside *folder* like the CLI does; undo cwd, sys.path and imports after."""
    from mango.scenario import _util

    cwd, path, modules = os.getcwd(), list(sys.path), set(sys.modules)
    root = _util._PROJECT_ROOT
    _forget_example_modules()
    os.chdir(folder)
    try:
        yield folder
    finally:
        os.chdir(cwd)
        sys.path[:] = path
        for name in set(sys.modules) - modules:
            if name == "ev_study" or name.startswith("ev_study."):
                del sys.modules[name]
        _util.set_project_root(root)


def _forget_example_modules():
    for name in [
        m for m in sys.modules if m == "ev_study" or m.startswith("ev_study.")
    ]:
        del sys.modules[name]


def copy_example(target: Path) -> Path:
    ignore = shutil.ignore_patterns("results", "__pycache__")
    shutil.copytree(EXAMPLE, target, ignore=ignore)
    (target / "studies" / "small.yaml").write_text(textwrap.dedent(SMALL_STUDY))
    (target / "studies" / "sobol_small.yaml").write_text(textwrap.dedent(SOBOL_STUDY))
    return target


def cli(capsys, *args):
    from mango.scenario.cli import main

    code = main([str(arg) for arg in args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def run_ids(out: Path) -> list[str]:
    lines = (out / "runs.jsonl").read_text().splitlines()
    return [json.loads(line)["run_id"] for line in lines if line.strip()]


def example_scenario():
    sys.path.insert(0, os.getcwd())
    from ev_study.scenario import scenario

    return scenario


def short_params(scenario, **changes):
    from mango.scenario import load_params

    overrides = {**SHORT, "ev.n": 2, **changes}
    return load_params(scenario, "presets/base.yaml", overrides=overrides)


@pytest.fixture(scope="module")
def small_study(tmp_path_factory):
    """The small study, run once through the CLI with two worker processes."""
    folder = copy_example(tmp_path_factory.mktemp("ev") / "ev_study")
    with project(folder):
        from mango.scenario.cli import main

        assert main(["run", "studies/small.yaml", "--workers", "2", "-q"]) == 0
    return folder


@pytest.fixture
def example(small_study):
    with project(small_study):
        yield small_study


@pytest.fixture
def fresh_example(tmp_path):
    with project(copy_example(tmp_path / "ev_study")) as folder:
        yield folder


# ----------------------------------------------------------------- validate, plan, run


def test_the_real_study_files_validate_and_plan(example, capsys):
    code, out, err = cli(capsys, "validate", "studies/fleet_comparison.yaml")
    assert code == 0, err
    assert "OK: studies/fleet_comparison.yaml is a valid study: 120 runs" in out

    code, out, err = cli(capsys, "plan", "studies/fleet_comparison.yaml")
    assert code == 0, err
    assert out.splitlines()[0] == "120 runs, 36 to 51 agents each, 2 links"

    code, out, err = cli(capsys, "validate", "studies/small.yaml")
    assert code == 0, err
    code, out, err = cli(capsys, "plan", "studies/small.yaml")
    assert code == 0, err
    assert out.splitlines()[0].startswith("4 runs, 9 agents each, 2 links")


def test_the_study_runs_in_worker_processes(example):
    from mango.scenario import load_study

    frame = load_study("results/small")
    assert len(frame) == 4
    assert set(frame["status"]) == {"done"}
    assert set(frame["algorithm"]) == {"greedy", "proportional"}
    assert set(frame["comm.loss_percent:label"]) == {"ideal", "lossy"}
    for outcome in ("self_consumption", "peak_import_kw", "min_voltage_pu"):
        assert frame[outcome].notna().all(), outcome
    assert (frame["min_voltage_pu"] < 1.0).all()
    assert (frame["peak_import_kw"] > 0.0).all()


def test_resume_skips_finished_runs_without_starting_processes(
    example, capsys, monkeypatch
):
    from mango.scenario import runner

    def no_processes():
        raise AssertionError("a finished study must not start worker processes")

    monkeypatch.setattr(runner, "_spawn_pool", no_processes)
    code, out, err = cli(capsys, "run", "studies/small.yaml", "--workers", "2", "-q")
    assert code == 0, err
    assert "4 runs: 0 done, 4 skipped, 0 failed" in out


def test_rerun_replays_a_run_with_an_identical_checksum(example, capsys):
    code, out, err = cli(capsys, "rerun", "results/small/run_0003")
    assert code == 0, out + err
    assert out.startswith("identical: run ")


def test_the_warmup_is_not_recorded(example):
    from mango.scenario import load_study

    run = load_study("results/small").run(0)
    warmup_end = 6 * 3600  # 2024-06-02T00:00, six hours after the start
    # a flow row at the warm-up end describes its last step: the first recorded
    # flow row closes the first evaluated 15 min step
    assert run.ts("import_kw").index.min() == warmup_end + 900
    # a state (soc_kwh) gets a row at the start of the recorded phase: the
    # level the warm-up left
    assert run.ts("soc_kwh").index.min() == warmup_end
    assert len(run.ts("soc_kwh", entity="ev_0")) == (24 - 6) * 4 + 1
    assert set(run.frame["phase"]) == {"main"}
    assert run.ts("import_kw").index.max() == 24 * 3600
    assert len(run.ts("import_kw")) == (24 - 6) * 4  # one row per evaluated step


def test_the_outcomes_integrate_the_evaluated_steps(example):
    """kpi.py sums energies over the interval rows of the evaluated day only."""
    from mango.scenario import load_study

    sys.path.insert(0, os.getcwd())
    from ev_study import kpi

    run = load_study("results/small").run(0)
    rows = (24 - 6) * 4  # one row per evaluated 15 min step, no warm-up boundary row
    assert len(run.ts("pv_kw")) == rows * 6  # 6 households
    pv_kwh = run.integrate("pv_kw")
    assert pv_kwh == pytest.approx(run.ts("pv_kw").sum() * run.step_h)
    export_kwh = run.ts("import_kw").clip(upper=0).abs().sum() * run.step_h
    assert kpi.self_consumption(run) == pytest.approx(1 - export_kwh / pv_kwh)
    per_ev = run.integrate("ev_power_kw", per_entity=True)
    assert list(per_ev.index) == ["ev_0", "ev_1"]
    assert per_ev.sum() == pytest.approx(run.integrate("ev_power_kw"))


def test_analyze_runs_on_a_finished_study(fresh_example, small_study, capsys):
    """analyze.py works on the study folder layout (here: the small study's results)."""
    import runpy

    shutil.copytree(small_study / "results" / "small", "results/fleet_comparison")
    sys.path.insert(0, os.getcwd())
    runpy.run_path("analyze.py", run_name="__main__")
    out = capsys.readouterr().out
    assert "self_consumption" in out and "peak_import_kw" in out
    assert "proportional" in out  # the paired comparison against greedy
    assert "the coordinator got" in out
    assert "charged per EV on the evaluated day (kWh)" in out


# ----------------------------------------------------------------- changes cost nothing


def test_a_changed_pv_share_gives_new_runs_and_keeps_the_old_folders(
    fresh_example, capsys
):
    out = Path("results/small")
    code, _, err = cli(capsys, "run", "studies/small.yaml", "-q")
    assert code == 0, err
    before = run_ids(out)
    manifests = {
        rid: (out / "runs" / rid / "manifest.json").read_bytes() for rid in before
    }

    code, text, err = cli(
        capsys, "run", "studies/small.yaml", "--set", "grid.pv_share=0.8", "-q"
    )
    assert code == 0, err
    assert "4 runs: 4 done, 0 skipped" in text
    after = run_ids(out)
    assert len(after) == 4
    assert not set(after) & set(before)
    for (
        rid,
        manifest,
    ) in manifests.items():  # the 0.6 results are still there, unchanged
        assert (out / "runs" / rid / "manifest.json").read_bytes() == manifest


def test_adding_a_household_leaves_the_ev_draws_unchanged(example):
    scenario = example_scenario()

    async def ev_draws(n_households):
        params = short_params(scenario, **{"grid.n_households": n_households})
        async with scenario.build(params, seed=3) as run:
            ev = run.entities["ev_0"].agent
            homes = [e.agent.annual_kwh for e in run.ctx.entities("grid.hh")]
            return (ev.arrival_kwh, ev.arrival_h, ev.departure_h), homes

    ev_6, homes_6 = asyncio.run(ev_draws(6))
    ev_7, homes_7 = asyncio.run(ev_draws(7))
    assert ev_6 == ev_7
    assert homes_7[:6] == homes_6  # existing households keep their draws too
    assert ev_6[1] != 16.0  # really drawn from Normal(16, 1.5)


def test_a_changed_pv_share_changes_only_who_has_pv(example):
    scenario = example_scenario()

    async def homes(pv_share):
        params = short_params(scenario, **{"grid.pv_share": pv_share})
        async with scenario.build(params, seed=7) as run:
            agents = [e.agent for e in run.ctx.entities("grid.hh")]
            return [a.annual_kwh for a in agents], [a.pv_peak_kw for a in agents]

    load_a, pv_a = asyncio.run(homes(0.5))
    load_b, pv_b = asyncio.run(homes(0.6))
    assert load_a == load_b  # common random numbers across pv_share variants
    gained = [i for i, (a, b) in enumerate(zip(pv_a, pv_b)) if a != b]
    assert all(pv_a[i] == 0.0 and pv_b[i] > 0.0 for i in gained)


def test_the_outage_drops_every_message(example):
    scenario = example_scenario()

    async def reports_received():
        counts = []
        async with scenario.build(short_params(scenario), seed=1) as run:
            coordinator = run.ctx.agents("coordinator")[0]
            for until in ("2024-06-02T12:00", "2024-06-02T13:45", "2024-06-02T15:00"):
                await run.run_until(until)
                counts.append(coordinator.reports_received)
        return counts

    # comm_outage sets loss_percent 1.0 from 12:00 to 14:00: the reports of a step are
    # sent once the clock reached the step's end, so none arrives from 12:00 to 13:45.
    at_start, before_end, after = asyncio.run(reports_received())
    assert at_start > 0
    assert before_end == at_start
    assert after - before_end == 5 * 6  # steps ending 14:00 ... 15:00 x 6 households


# ----------------------------------------------------------------- Sobol and Slurm


def test_a_tiny_sobol_study_gives_sensitivity_indices(fresh_example, capsys):
    pytest.importorskip("scipy")
    from mango.scenario import load_study

    code, out, err = cli(capsys, "run", "studies/sobol_small.yaml", "-q")
    assert code == 0, err
    assert "16 runs: 16 done" in out
    with pytest.warns(UserWarning, match="base samples"):  # a tiny design
        indices = load_study("results/sobol_small").sobol_indices()
    assert set(indices["factor"]) == {"grid.pv_share", "ev.capacity_kwh"}
    assert set(indices["outcome"]) == {
        "self_consumption",
        "peak_import_kw",
        "min_voltage_pu",
    }
    assert list(indices.columns[-4:]) == ["S1", "S1_conf", "ST", "ST_conf"]


def test_slurm_script_and_a_pending_line(fresh_example, capsys):
    from mango.scenario import load_study

    code, out, err = cli(
        capsys,
        "run",
        "studies/small.yaml",
        "--executor",
        "slurm",
        "--partition",
        "short",
        "--no-submit",
    )
    assert code == 0, err
    assert "4 pending in" in out and "not submitted" in out
    slurm = Path("results/small/slurm")
    script = (slurm / "array.sbatch").read_text()
    assert "#SBATCH --array=0-3" in script
    assert "#SBATCH --partition=short" in script
    assert "-m mango.scenario.cli run-one" in script
    assert f"cd {Path.cwd()}" in script
    assert len((slurm / "pending.jsonl").read_text().splitlines()) == 4

    code, out, err = cli(capsys, "run-one", slurm / "pending.jsonl", "--line", "2")
    assert code == 0, out + err
    frame = load_study("results/small")
    assert list(frame["status"]) == ["pending", "pending", "done", "pending"]

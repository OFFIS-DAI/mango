"""Acceptance test of the scenario layer: the EV tutorial as a scenario.

The tutorial module (``ev_tutorial.py``) is included verbatim in
``docs/source/scenario-tutorial.rst``.  These tests check the promises of
that page: every number lives in the params model, wiring uses links instead
of address patching, results are keyed by scenario names, one seed replays a
run, extra households leave every EV's draws unchanged, and the study runs
sequentially and on a process pool, resumes without re-running and loads as
one row per run.
"""

import ast
import functools
import inspect
import json
import shutil
import sys
from pathlib import Path

import pytest

from mango.scenario import ParamsError, ScenarioParams, load_study
from mango.scenario import runner as runner_module

from . import ev_tutorial as tut
from .rst_blocks import DOCS, normalize_output, read_blocks

EV_NAMES = ["ev_0", "ev_1", "ev_2"]
HOUSEHOLD_NAMES = [f"household_{i}" for i in range(5)]


@pytest.fixture(scope="module")
def result():
    return tut.scenario.run(seed=7)


def start_values(run, variables=("soc_kwh", "x", "y")):
    """Per EV: the recorded values at t=0, i.e. what was drawn when it was built."""
    return {
        name: tuple(float(run.ts(v, entity=name).iloc[0]) for v in variables)
        for name in run.wide("soc_kwh").columns
    }


# -- parameters ---------------------------------------------------------------------


def test_every_number_lives_in_the_params_model():
    assert tut.scenario.params_model is tut.Params
    assert issubclass(tut.Params, ScenarioParams)
    params = tut.scenario.resolve_params()
    homes = [(h.pv_peak_kw, h.load_kw, h.x, h.y) for h in params.households]
    assert homes == [
        (6.0, 2.0, 1.0, 3.0),
        (4.0, 1.5, 1.0, 8.0),
        (7.0, 2.5, 5.0, 2.0),
        (5.0, 1.0, 7.0, 8.0),
        (3.0, 2.0, 9.0, 5.0),
    ]
    ev = params.ev
    assert (ev.n, ev.capacity_kwh, ev.max_power_kw, ev.speed) == (3, 40.0, 3.3, 3.0)
    assert (params.area.width, params.area.height) == (10.0, 10.0)
    assert (params.time.horizon_s, params.time.step_s, params.time.n_steps) == (
        86400.0,
        3600.0,
        24,
    )
    assert params.time.start.isoformat() == "2024-06-21T00:00:00"


def test_agents_are_built_without_positional_literals():
    tree = ast.parse(inspect.getsource(tut))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.endswith("Agent")
    ]
    assert {call.func.id for call in calls} == {
        "HouseholdAgent",
        "EVAgent",
        "CoordinatorAgent",
    }
    for call in calls:
        assert not call.args, f"{call.func.id} is called with positional arguments"
        for keyword in call.keywords:
            assert not isinstance(keyword.value, ast.Constant), (
                f"{call.func.id}({keyword.arg}=...) is a literal, not a parameter"
            )


def test_variants_are_overrides_and_typos_fail_before_the_run():
    plan = tut.scenario.plan(overrides=["ev.n=5", "ev.soc_kwh={uniform: [5, 35]}"])
    assert plan.group("ev").count == 5
    assert plan.group("ev").names == [f"ev_{i}" for i in range(5)]
    assert plan.link("dispatch").edges == 5
    assert plan.params.ev.soc_kwh.high == 35.0
    with pytest.raises(ParamsError, match="capacity_kwh") as info:
        tut.scenario.plan(overrides=["ev.capactiy_kwh=50"])
    assert info.value.path == "ev.capactiy_kwh"
    with pytest.raises(ParamsError, match="households"):
        tut.scenario.plan({"households": [{"pv_peak_kw": 1.0, "load_kw": 1.0}]})


# -- wiring -------------------------------------------------------------------------


def test_coordinator_has_no_addresses_to_patch():
    source = inspect.getsource(tut)
    assert "ev_addresses" not in source
    assert ".addr" not in source
    assert "coordinator_addr" not in source
    assert list(inspect.signature(tut.CoordinatorAgent).parameters) == ["threshold_kw"]
    assert list(inspect.signature(tut.HouseholdAgent).parameters) == [
        "pv_peak_kw",
        "load_kw",
    ]


@pytest.mark.asyncio
async def test_links_and_positions_are_built_by_the_scenario():
    run = tut.scenario.build(seed=7)
    async with run:
        ctx = run.ctx
        (coordinator,) = ctx.agents("coordinator")
        evs = ctx.agents("ev")
        homes = ctx.agents("household")
        assert [a.aid for a in evs] == EV_NAMES
        assert [a.aid for a in homes] == HOUSEHOLD_NAMES
        assert coordinator.neighbors(tid="dispatch") == [ev.addr for ev in evs]
        assert coordinator.neighbors(tid="reports") == [h.addr for h in homes]
        for agent in (*evs, *homes):
            tid = "dispatch" if agent in evs else "reports"
            assert agent.neighbors(tid=tid) == [coordinator.addr]
        space = run.world.environment.space
        expected = [(h.x, h.y) for h in run.params.households]
        assert [(space.location(h).x, space.location(h).y) for h in homes] == expected
        for ev in evs:
            assert space.location(ev) == ev.position
            assert 0.0 <= ev.position.x <= 10.0 and 0.0 <= ev.position.y <= 10.0
        assert run.stats["link_edges"] == {"reports": 5, "dispatch": 3}


# -- results ------------------------------------------------------------------------


def test_results_are_keyed_by_scenario_names(result):
    assert result.groups() == ["coordinator", "household", "ev"]
    assert result.variables() == [
        "soc_kwh",
        "delivered_kwh",
        "x",
        "y",
        "import_kwh",
        "export_kwh",
        "self_consumed_kwh",
    ]
    assert list(result.wide("soc_kwh").columns) == EV_NAMES
    assert list(result.wide("import_kwh").columns) == HOUSEHOLD_NAMES
    assert set(result.frame["entity"]) == {*EV_NAMES, *HOUSEHOLD_NAMES}
    soc = result.ts("soc_kwh", entity="ev_0")
    assert list(soc.index) == [3600.0 * h for h in range(25)]
    first = result.frame["datetime"].iloc[0]
    assert (first.year, first.month, first.day, first.hour) == (2024, 6, 21, 0)
    imports = result.ts("import_kwh", entity="household_2")
    assert imports.is_monotonic_increasing and imports.iloc[-1] > 0


def test_the_coordination_moves_energy(result):
    assert 0.0 < tut.delivered_kwh(result) < 3 * 40.0
    assert 0.0 < tut.local_share(result) < 1.0
    soc = result.wide("soc_kwh")
    assert (soc.iloc[0] != soc.iloc[-1]).any()
    # surplus at noon, deficit at night
    export = result.wide("export_kwh").iloc[-1]
    assert export["household_2"] > 0.0
    assert result.agent("coordinator").reports


# -- seeds --------------------------------------------------------------------------


def test_one_seed_replays_the_run(result):
    again = tut.scenario.run(seed=7)
    assert again.checksum() == result.checksum()
    assert again.run_id == result.run_id
    replay = tut.scenario.run(result.config, seed=result.seed)
    assert replay.checksum() == result.checksum()
    other = tut.scenario.run(seed=8)
    assert other.checksum() != result.checksum()
    assert start_values(other) != start_values(result)


def test_adding_a_household_leaves_every_ev_draw_unchanged(result):
    extra = {"pv_peak_kw": 8.0, "load_kw": 1.0, "x": 3.0, "y": 6.0}
    bigger = tut.scenario.run(seed=7, overrides={"+households": [extra]})
    assert list(bigger.wide("import_kwh").columns) == [*HOUSEHOLD_NAMES, "household_5"]
    assert bigger.checksum() != result.checksum()
    before = start_values(result)
    assert start_values(bigger) == before
    assert len(set(before.values())) == 3  # each EV draws from its own stream
    # households are built before the EVs and draw too (a load factor), so
    # with one shared stream the sixth household would shift every EV draw
    loads = [result.agent(name).load_kw for name in HOUSEHOLD_NAMES]
    nominal = [home.load_kw for home in result.params.households]
    assert all(load != base for load, base in zip(loads, nominal))
    assert [bigger.agent(name).load_kw for name in HOUSEHOLD_NAMES] == loads


# -- the study ----------------------------------------------------------------------


def test_fleet_study_runs_sequentially_and_on_a_process_pool(tmp_path, capsys):
    sequential = tut.fleet_study(tmp_path / "sequential").run(progress=False)
    assert (sequential.total, sequential.done, sequential.failed) == (4, 4, 0)

    # the pool resumes a folder in which replication 0 is done already: it
    # starts no process for those runs and runs replication 1 on two workers
    reference = load_study(tmp_path / "sequential")
    for run_id in reference.loc[reference["replication"] == 0, "run_id"]:
        shutil.copytree(
            tmp_path / "sequential" / "runs" / run_id,
            tmp_path / "pool" / "runs" / run_id,
        )
    pooled = tut.main(out=tmp_path / "pool", workers=2)
    printed = capsys.readouterr().out
    assert "4 runs: 2 done, 2 skipped, 0 failed" in printed
    assert "process pool (2 workers)" in printed
    assert len(pooled) == 4
    assert pooled["status"].tolist() == ["done"] * 4
    assert pooled["ev.n"].tolist() == [2, 2, 3, 3]
    assert pooled["replication"].tolist() == [0, 1, 0, 1]

    assert reference["run_id"].tolist() == pooled["run_id"].tolist()
    columns = ["local_share", "delivered_kwh"]
    assert reference[columns].values.tolist() == pooled[columns].values.tolist()
    for run_id in pooled["run_id"]:
        manifests = [
            json.loads(
                (tmp_path / where / "runs" / run_id / "manifest.json").read_text()
            )
            for where in ("sequential", "pool")
        ]
        assert manifests[0]["checksum"] == manifests[1]["checksum"]
    # common random numbers: replication r draws the same EVs for both fleet sizes
    ev0 = [start_values(pooled.run(i), ("soc_kwh",))["ev_0"] for i in range(4)]
    assert ev0[0] == ev0[2] and ev0[1] == ev0[3] and ev0[0] != ev0[1]
    assert pooled.groupby("ev.n")[columns].mean().shape == (2, 2)


def test_fleet_study_resumes_without_rerunning(tmp_path, monkeypatch):
    first = tut.fleet_study(tmp_path).run(progress=False)
    assert first.done == 4
    manifests = sorted((tmp_path / "runs").glob("*/manifest.json"))
    stamps = [path.stat().st_mtime_ns for path in manifests]

    def no_pool():
        raise AssertionError("finished runs must not start worker processes")

    monkeypatch.setattr(runner_module, "_spawn_pool", no_pool)
    again = tut.fleet_study(tmp_path).run(workers=2, progress=False)
    assert (again.total, again.done, again.skipped, again.failed) == (4, 0, 4, 0)
    assert [path.stat().st_mtime_ns for path in manifests] == stamps
    frame = load_study(tmp_path)
    assert len(frame) == 4
    assert frame["status"].tolist() == ["done"] * 4


# -- the docs page ------------------------------------------------------------------


def test_tutorial_page_includes_this_module():
    page = (DOCS / "scenario-tutorial.rst").read_text(encoding="utf-8")
    included = Path(DOCS, "../../tests/unit_tests/scenario/ev_tutorial.py").resolve()
    assert included == Path(tut.__file__).resolve()
    source = Path(tut.__file__).read_text(encoding="utf-8")
    options = [line.strip().split(":", 2)[1:] for line in page.splitlines()]
    options = [
        (key, value.strip()) for key, value in (o for o in options if len(o) == 2)
    ]
    markers = [v for k, v in options if k in ("start-after", "end-before")]
    assert len(markers) >= 10, "the page should include the module in parts"
    for marker in markers:
        assert source.count(marker) == 1, f"marker {marker!r} must occur exactly once"
    for name in [v for k, v in options if k == "pyobject"]:
        assert inspect.isclass(getattr(tut, name)), name


def test_tutorial_page_snippets_run_and_print_what_the_page_shows(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "ev_tutorial", tut)
    # the page runs the study on a process pool; the test above covers that
    monkeypatch.setattr(tut, "main", functools.partial(tut.main, workers=None))
    blocks = [b for b in read_blocks("scenario-tutorial.rst") if b.kind == "code-block"]
    namespace = {"__name__": "__main__"}
    checked = 0
    for block, following in zip(blocks, [*blocks[1:], None]):
        if block.lang != "python":
            continue
        capsys.readouterr()
        exec(
            compile(block.code, f"scenario-tutorial.rst:{block.line}", "exec"),
            namespace,
        )
        printed = capsys.readouterr().out
        if following is None or following.lang != "text" or "__main__" in block.code:
            continue
        assert normalize_output(printed) == normalize_output(following.code), (
            f"output of the snippet at line {block.line} differs from the page"
        )
        checked += 1
    assert checked >= 5
    assert load_study("results/ev_fleet")["status"].tolist() == ["done"] * 4

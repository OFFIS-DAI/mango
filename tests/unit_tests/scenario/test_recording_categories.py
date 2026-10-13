"""aggregate='count' with declared categories (N37)."""

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from mango import Agent  # noqa: E402
from mango.scenario import Scenario, ScenarioError, load_scenario  # noqa: E402
from mango.scenario.script import generate_script  # noqa: E402


class Person(Agent):
    """S, then I from t=2 for the first person; nobody is ever 'V'."""

    def __init__(self, index):
        super().__init__()
        self.index = index
        self.state = "S"

    def on_step(self, env, clock, step_size_s):
        if self.index == 0 and clock.time >= 1:
            self.state = "I"


def people(**record):
    scenario = Scenario("seir")
    scenario.time(horizon=3, step=1)
    group = scenario.group("person", 3, lambda e: Person(e.index))
    scenario.record("state", group, attr="state", aggregate="count", **record)
    return scenario


def test_declared_categories_get_rows_even_when_absent():
    result = people(categories=["S", "E", "I", "R", "V"]).run(seed=1)
    table = result.wide("state")
    assert table.columns.tolist() == ["S", "E", "I", "R", "V"]  # declared order
    assert table["V"].tolist() == [0, 0, 0, 0]
    assert table["I"].tolist() == [0, 0, 1, 1]
    assert table["S"].tolist() == [3, 3, 2, 2]


def test_undeclared_values_are_appended_after_the_declared_ones():
    result = people(categories=["R", "S"]).run(seed=1)
    assert result.wide("state").columns.tolist() == ["R", "S", "I"]


def test_without_categories_absent_values_have_no_rows():
    result = people().run(seed=1)
    assert result.wide("state").columns.tolist() == ["S", "I"]


def test_categories_with_a_window_are_zero_filled():
    scenario = Scenario("seir")
    scenario.time(horizon=4, step=1)
    group = scenario.group("person", 3, lambda e: Person(e.index))
    scenario.record(
        "state",
        group,
        attr="state",
        aggregate="count",
        categories=[True, "V"],
        every=2,
        window="max",
    )
    table = scenario.run(seed=1).wide("state")
    assert table.columns.tolist() == ["True", "V", "S", "I"]
    assert table["V"].tolist() == [0, 0, 0]


def test_categories_are_checked():
    scenario = Scenario("seir")
    scenario.time(horizon=3, step=1)
    group = scenario.group("person", 3, lambda e: Person(e.index))
    with pytest.raises(ScenarioError, match="needs aggregate='count'"):
        scenario.record("state", group, attr="state", categories=["S"])
    with pytest.raises(ScenarioError, match="needs aggregate='count'"):
        scenario.record("state", group, attr="state", aggregate="sum", categories=["S"])
    with pytest.raises(TypeError, match="list of category values"):
        scenario.record(
            "state", group, attr="state", aggregate="count", categories="SIR"
        )
    with pytest.raises(ScenarioError, match=r"categories \['S'\] are listed twice"):
        scenario.record(
            "state", group, attr="state", aggregate="count", categories=["S", "I", "S"]
        )
    with pytest.raises(ScenarioError, match="categories is empty"):
        scenario.record("state", group, attr="state", aggregate="count", categories=[])


def test_categories_show_in_the_plan_and_change_the_fingerprint():
    declared = people(categories=["S", "I", "V"])
    assert declared.plan().recorders[0].categories == ["S", "I", "V"]
    assert declared.fingerprint() != people().fingerprint()


def test_yaml_recorders_take_categories(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "people_mod.py").write_text(
        "from tests.unit_tests.scenario.test_recording_categories import Person\n"
        "def make(e):\n    return Person(e.index)\n"
    )
    (tmp_path / "people.yaml").write_text(
        "time: {horizon: 3, step: 1}\n"
        "groups:\n  person: {count: 3, factory: 'people_mod:make'}\n"
        "record:\n"
        "  - {name: state, group: person, attr: state, aggregate: count,\n"
        "     categories: [S, I, V]}\n"
    )
    result = load_scenario(tmp_path / "people.yaml").run(seed=1)
    assert result.wide("state").columns.tolist() == ["S", "I", "V"]


def test_to_script_writes_declared_categories(tmp_path):
    from . import script_fixtures as fx

    scenario = people(categories=["S", "I", "V"])
    script = generate_script(scenario, seed=1)
    assert "categories=['S', 'I', 'V']" in script
    rows, _ = fx.run_generated(script, tmp_path)
    assert fx.rows_checksum(rows) == scenario.run(seed=1).checksum()

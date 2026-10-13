"""Per-entity tables keep a row together (FromColumn sample mode), e.row, pattern annotations."""

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("pandas")

from mango import Agent  # noqa: E402
from mango.scenario import (  # noqa: E402
    Dist,
    FromColumn,
    Scenario,
    ScenarioParams,
)

SURVEY = "respondent,opinion,age\n" + "".join(
    f"R{i:03d},{i / 10:.1f},{20 + i}\n" for i in range(10)
)


class Person(Agent):
    def __init__(self, opinion, age, row=None):
        super().__init__()
        self.opinion, self.age, self.row = opinion, age, row


def survey_scenario(path, mode="sample", n=12):
    class Params(ScenarioParams):
        opinion: Dist = FromColumn(str(path), "opinion", mode)
        age: Dist = FromColumn(str(path), "age", mode)

    scenario = Scenario("survey", Params)
    scenario.time(horizon="1d", step="1d")
    scenario.group(
        "person",
        n,
        lambda e: Person(e.draw(e.p.opinion), e.draw(e.p.age), e.attrs["row"]),
        install=lambda e: {"row": e.row(e.p.opinion)},
    )
    return scenario


@pytest.fixture(name="survey")
def survey_fixture(tmp_path):
    path = tmp_path / "survey.csv"
    path.write_text(SURVEY, encoding="utf-8")
    return path


def test_sample_mode_reads_every_column_of_a_table_from_one_row(survey):
    result = survey_scenario(survey).run(seed=3)
    people = [result.agent(f"person_{i}") for i in range(12)]
    for person in people:
        assert person.age == 20 + round(person.opinion * 10)  # the same respondent
        assert person.opinion == pytest.approx(person.row / 10)
    assert len({person.row for person in people}) > 1
    again = survey_scenario(survey).run(seed=3)
    assert [again.agent(f"person_{i}").row for i in range(12)] == [
        p.row for p in people
    ]
    other = survey_scenario(survey).run(seed=4)
    assert [other.agent(f"person_{i}").row for i in range(12)] != [
        p.row for p in people
    ]


def test_index_mode_rows_follow_the_entity_index(survey):
    with pytest.warns(UserWarning, match="rows are reused modulo 10"):
        result = survey_scenario(survey, mode="index", n=12).run(seed=3)
    rows = [result.agent(f"person_{i}").row for i in range(12)]
    assert rows == [*range(10), 0, 1]


def test_row_needs_a_table(survey):
    from mango.scenario import Normal

    scenario = Scenario("bad")
    scenario.group("x", 1, lambda e: Person(e.row(Normal(0, 1)), 0))
    with pytest.raises(TypeError, match="e.row\\(\\) needs a FromColumn"):
        scenario.run(seed=1)


def test_sample_arrays_share_rows_across_columns(survey):
    opinion = FromColumn(str(survey), "opinion", "sample")
    age = FromColumn(str(survey), "age", "sample")
    scenario = Scenario("arrays")
    scenario.group(
        "x", 1, lambda e: Person(e.draw(opinion, 5), e.draw(age, 5)), naming="x{index}"
    )
    agent = scenario.run(seed=1).agent("x0")
    assert list(agent.age) == [20 + round(v * 10) for v in agent.opinion]


def households(src, dst, rng):
    pairs, start, household = [], 0, 0
    while start < len(src):
        size = int(rng.integers(1, 4))
        members = src[start : start + size]
        for member in members:
            member.attrs["household"] = household
        pairs += [(a, b) for i, a in enumerate(members) for b in members[i + 1 :]]
        start, household = start + size, household + 1
    return pairs


def test_a_pattern_may_annotate_entities():
    scenario = Scenario("epidemic")
    scenario.time(horizon="1h", step="1h")
    person = scenario.group("person", 10, lambda e: Agent())
    scenario.link("household", person, pattern=households)
    scenario.record(
        "household", person, lambda a, ctx: ctx.entity(a.aid).attrs["household"]
    )
    seen = {}

    @scenario.on_build
    def check(ctx):
        seen["pairs"] = ctx.link_pairs("household")
        seen["ids"] = {e.name: e.attrs["household"] for e in ctx.entities("person")}

    result = scenario.run(seed=2)
    ids = result.wide("household").iloc[0].tolist()
    assert ids == sorted(ids) and ids[0] == 0 and len(set(ids)) > 1
    assert seen["pairs"]
    assert all(seen["ids"][a] == seen["ids"][b] for a, b in seen["pairs"])

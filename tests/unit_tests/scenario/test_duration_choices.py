"""Durations in distributions: link lead times and choices that mix numbers and durations."""

import collections

import pytest

pytest.importorskip("pydantic")
np = pytest.importorskip("numpy")

from pydantic import BaseModel  # noqa: E402

from mango import Agent  # noqa: E402
from mango.scenario import (  # noqa: E402
    Choice,
    Constant,
    Dist,
    ParamsError,
    Scenario,
    ScenarioParams,
    load_params,
)

WEEK = 7 * 24 * 3600


class _Sender(Agent):
    def __init__(self):
        super().__init__()
        self.got = []

    def on_step(self, env, clock, step_size_s):
        for neighbor in self.neighbors(tid="ship"):
            self.schedule_instant_message(round(clock.time / WEEK), neighbor)

    def handle_message(self, content, meta):
        self.got.append((content, round(self.scheduler.clock.time / WEEK)))


def _lead_times(delay) -> set[int]:
    scenario = Scenario("lead")
    scenario.time(horizon="60w", step="1w")
    stage = scenario.group("stage", 2, lambda e: _Sender())
    scenario.link("ship", stage, pattern="line", directed=True, delay=delay)
    result = scenario.run(seed=1)
    got = result.agent("stage_1").got
    return set(collections.Counter(arrived - sent for sent, arrived in got))


def test_choice_of_durations_gives_the_documented_lead_times():
    # delay = (L - 1) * step: lead times of 1, 2 and 3 weekly steps
    assert _lead_times({"choice": [0, "1w", "2w"]}) == {1, 2, 3}


def test_int_uniform_over_durations_draws_seconds():
    # documented: seconds between one and three weeks, lead time 3 or 4 steps
    assert _lead_times({"int_uniform": ["1w", "3w"]}) == {3, 4}


def test_the_link_docstring_example_gives_lead_times_one_to_three():
    from mango.scenario import builder

    doc = builder.Scenario.link.__doc__
    assert 'delay={"choice": [0, "1w", "2w"]}' in doc
    assert "int_uniform" not in doc.split("Example::")[1]


class _Lead(BaseModel, extra="forbid"):
    a: Dist = Constant(0.0)


class _Params(ScenarioParams):
    lead: _Lead = _Lead()


def test_a_dist_parameter_mixing_numbers_and_durations_is_refused_with_seconds():
    with pytest.raises(ParamsError) as info:
        load_params(_Params, overrides=["lead.a={choice: [0, 1w, 2w]}"])
    text = str(info.value)
    assert "durations are converted to seconds only in delay fields" in text
    assert "[0, 604800, 1209600]" in text


def test_choices_of_labels_and_of_numbers_stay_valid():
    assert Choice(["15min", "1h"]).values == ("15min", "1h")
    assert Choice([0, "5", "red"]).values == (0, "5", "red")
    assert Choice([0, 604800]).values == (0, 604800)


def test_sized_draws_keep_the_types_of_scalar_draws():
    rng = np.random.default_rng(1)
    mixed = Choice([0, "a"]).draw(rng, size=20)
    assert mixed.dtype == object
    assert {type(v) for v in mixed} == {int, str}
    flags = Choice([True, 2]).draw(rng, size=20)
    assert {type(v) for v in flags} == {bool, int}
    labels = Choice(["x", "y"]).draw(rng, size=3)
    assert labels.dtype.kind == "U"
    numbers = Choice([1, 2.5]).draw(rng, size=50)
    assert numbers.dtype == float

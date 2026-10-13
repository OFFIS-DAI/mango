"""The fast paths studies use to resolve design points agree with load_params."""

from typing import Annotated, Literal

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from pydantic import BaseModel, Field  # noqa: E402

from mango.scenario import (  # noqa: E402
    Scenario,
    ScenarioParams,
    Study,
    StudyError,
    Uniform,
    dump_params,
    load_params,
)
from mango.scenario.params import _overlay_dump, _resolve_dump  # noqa: E402


class Greedy(BaseModel, extra="forbid"):
    kind: Literal["greedy"] = "greedy"
    threshold: float = 0.5


class Market(BaseModel, extra="forbid"):
    kind: Literal["market"] = "market"
    price: float = 1.0


class EV(BaseModel, extra="forbid"):
    n: int = 2
    soc: Uniform = Uniform(0.2, 0.8)
    capacity_kwh: float = 60.0


class Params(ScenarioParams):
    ev: EV = EV()
    algorithm: Annotated[Greedy | Market, Field(discriminator="kind")] = Greedy()
    weights: list[float] = [1.0, 2.0]
    tags: dict[str, int] = {}


scenario = Scenario("fast", params=Params)
scenario.time(horizon="2h", step="1h")
scenario.defaults(
    {
        "ev.n": 3,
        "tags": {"a": 1, "b": 2},
        "profiles": {"load": {"values": [1.0, 2.0], "resolution": "1h"}},
    }
)

BASE = dump_params(load_params(scenario, {"ev": {"capacity_kwh": 40.0}}))

POINTS = [
    {"ev.n": 5},
    {"ev.soc.high": 0.9},
    {"ev.soc": {"uniform": [0.1, 0.5]}},
    {"algorithm.threshold": 0.7},
    {"algorithm": {"kind": "market", "price": 2.0}},
    {"algorithm.kind": "market"},
    {"weights": [3.0]},
    {"tags.c": 3},
    {"comm.loss_percent": 0.1, "ev.capacity_kwh": 80},
    {"time.horizon": "3h"},
]


@pytest.mark.parametrize("values", POINTS, ids=[str(p) for p in POINTS])
def test_overlay_of_a_dump_equals_load_params(values):
    fast = _overlay_dump(scenario, BASE, values)
    assert fast is not None
    assert dump_params(fast) == dump_params(
        load_params(scenario, BASE, overrides=values)
    )


@pytest.mark.parametrize(
    "values",
    [{"ev.n": "many"}, {"ev.nn": 3}, {"algorithm.price": 2.0}, {"ev.soc.hgh": 1}],
)
def test_overlay_defers_invalid_points_to_load_params(values):
    assert _overlay_dump(scenario, BASE, values) is None


def test_resolving_a_dump_reuses_it_unless_defaults_add_to_it():
    assert dump_params(_resolve_dump(scenario, BASE)) == dump_params(
        load_params(scenario, BASE)
    )
    without = dict(BASE, tags={"a": 1})  # a preset deleted the default 'b'
    assert _resolve_dump(scenario, without).tags == {"a": 1, "b": 2}  # as a run


def test_invalid_design_points_still_name_the_point():
    study = Study(scenario, uncertainties={"ev.n": ["many"]})
    with pytest.raises(
        StudyError, match="(?s)design point ev.n=many gives invalid.*ev.n: Input"
    ):
        study.plan()

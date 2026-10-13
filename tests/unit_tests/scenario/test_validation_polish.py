"""Small validation and message fixes: distribution-like factor levels, removal
overrides, case-insensitive suggestions, bound hints, off-grid times, kit repr."""

import warnings

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from pydantic import BaseModel, Field  # noqa: E402

from mango.scenario import (  # noqa: E402
    ParamsError,
    Scenario,
    ScenarioComponent,
    ScenarioParams,
    Study,
    StudyError,
    Uniform,
)
from mango.scenario.configfiles import parse_overrides  # noqa: E402
from mango.scenario.designs import Factor  # noqa: E402

from . import script_fixtures as fx  # noqa: E402
from .cli_helpers import isolated_fixture  # noqa: E402, F401


def test_a_distribution_kind_as_the_only_key_is_not_a_labeled_level():
    with pytest.raises(
        StudyError, match="would be one labeled level named 'uniform'"
    ) as info:
        Factor.parse("battery.soc_kwh", {"uniform": [2.0, 4.0]})
    assert '{"value": {"uniform": [2.0, 4.0]}}' in str(info.value)
    fixed = Factor.parse("battery.soc_kwh", {"value": {"uniform": [2.0, 4.0]}})
    assert fixed.kind == "fixed" and fixed.levels == ({"uniform": [2.0, 4.0]},)
    # labeled levels whose values are distributions stay as they are
    labeled = Factor.parse(
        "battery.soc_kwh", {"empty": {"uniform": [0, 2]}, "half": {"uniform": [4, 6]}}
    )
    assert labeled.labels == ("empty", "half")


def test_labels_that_are_distribution_fields_warn():
    with pytest.warns(UserWarning, match="labels are the fields of a uniform") as info:
        factor = Factor.parse("homes.pv_kwp", {"low": 2.0, "high": 6.0})
    assert factor.labels == ("low", "high")  # still labeled levels
    assert '{"value": {"kind": "uniform", "low": 2.0, "high": 6.0}}' in str(
        info[0].message
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert Factor.parse("x", {"levels": {"low": 2.0, "high": 6.0}}).labels
        assert Factor.parse("x", {"ideal": 0.0, "lossy": 0.1}).labels


class _LabelParams(ScenarioParams):
    share: float = 0.5
    spread: Uniform = Uniform(0.0, 1.0)


def test_labels_on_a_field_that_takes_no_distribution_stay_quiet():
    labeled = Scenario("labels", params=_LabelParams)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        study = Study(labeled, levers={"share": {"low": 0.3, "high": 0.7}})
    assert study.factors[0].labels == ("low", "high")
    # the same labels on a distribution field still warn
    with pytest.warns(UserWarning, match="labels are the fields of a uniform"):
        Study(labeled, uncertainties={"spread": {"low": 0.2, "high": 0.9}})
    # and in subexperiments
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        Study(labeled, subexperiments=[{"share": {"low": 0.1, "high": 0.2}}])


def test_the_cli_takes_a_removal_after_set(capsys, isolated):
    from .cli_helpers import cli

    for args in (["--set", "-events"], ["-s", "-events"], ["--set", "-events="]):
        code, out, err = cli(capsys, "validate", f"{fx.__name__}:scenario", *args)
        assert code == 0, err
        assert "expected one argument" not in err
    # an option after --set is still an option (and a usage error)
    code, _, err = cli(capsys, "validate", f"{fx.__name__}:scenario", "-s", "-p")
    assert code == 2 and "expected one argument" in err


def test_removal_overrides_need_no_equals_sign():
    assert parse_overrides(["-events", "time.-step", "-phases="]) == {
        "-events": None,
        "time.-step": None,
        "-phases": None,
    }
    with pytest.raises(ParamsError, match="or a removal such as '-events'"):
        parse_overrides(["events"])
    params = fx.scenario.resolve_params(None, overrides=["-events"])
    assert params.events == []


def test_suggestions_ignore_case():
    with pytest.raises(ParamsError, match="did you mean 'node.n'"):
        fx.scenario.resolve_params(None, overrides=["node.N=3"])
    with pytest.raises(ParamsError, match="did you mean 'boost'"):
        fx.scenario.resolve_params(None, overrides=["Boost=3"])


class Bounded(ScenarioParams):
    share: float = Field(0.5, ge=0, le=1, description="share of informed agents")
    count: int = Field(3, ge=1)


def test_bound_errors_quote_the_field_description():
    with pytest.raises(ParamsError) as info:
        fx.scenario.resolve_params(None, overrides=["comm.loss_percent=10"])
    assert "less than or equal to 1 (got 10); loss_percent: probability" in str(
        info.value
    )
    assert "(10 % is 0.1)" in str(info.value)
    scenario = Scenario("bounded", params=Bounded)
    with pytest.raises(ParamsError, match="share: share of informed agents"):
        scenario.resolve_params(None, overrides=["share=2"])
    with pytest.raises(ParamsError) as info:
        scenario.resolve_params(None, overrides=["count=0"])
    assert str(info.value).rstrip().endswith("(got 0) [from overrides]")


def test_off_grid_warnings_read_naturally():
    scenario = Scenario("grid")
    scenario.time(horizon="10w", step="1w")
    with pytest.warns(UserWarning) as caught:
        scenario.plan({"events": [{"name": "x", "at": "10.5d", "emit": "y"}]})
    assert any(
        "event 'x' at 10d12h is not on the 1w step grid" in str(w.message)
        for w in caught
    )


class Pop(ScenarioComponent):
    class Params(BaseModel, extra="forbid"):
        n: int = 200
        share: Uniform = Uniform(0.1, 0.2)


def test_kit_params_repr_names_the_kit():
    scenario = Scenario("kit_repr")
    scenario.use(Pop(), prefix="pop")
    params = scenario.resolve_params(None)
    assert "pop=Pop.Params(n=200" in repr(params)
    assert Pop.Params.__name__ == "Params"  # the class and its schema are unchanged

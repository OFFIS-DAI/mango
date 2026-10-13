import random
from typing import Literal

import numpy as np
import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

import yaml  # noqa: E402
from pydantic import BaseModel, ValidationError  # noqa: E402

from mango import SeedTree  # noqa: E402
from mango.scenario import (  # noqa: E402
    Bernoulli,
    Choice,
    Constant,
    Dist,
    Distribution,
    IntUniform,
    LogNormal,
    Normal,
    Uniform,
    distribution_kinds,
)

ALL_EXAMPLES = [
    Constant(5.0),
    Uniform(0.2, 0.8),
    IntUniform(1, 6),
    Normal(16.0, 1.5),
    Normal(0.04, 0.01, low=0.0, high=0.05),
    LogNormal(0.0, 0.5),
    Choice(["greedy", "proportional"]),
    Choice([1, 2, 3], p=[0.5, 0.25, 0.25]),
    Bernoulli(0.6),
]


def _rngs(seed=1):
    return [np.random.default_rng(seed), random.Random(seed)]


class EVParams(BaseModel, extra="forbid"):
    soc: Uniform = Uniform(0.2, 0.8)
    arrival_h: Normal = Normal(16.0, 1.5)
    count: IntUniform = IntUniform(1, 3)
    algorithm: Choice = Choice(["greedy"])
    has_pv: Bernoulli = Bernoulli(0.5)
    size: LogNormal = LogNormal(0.0, 0.1)
    delay: Dist = Constant(0.0)
    maybe: Dist | None = None


# ----------------------------------------------------------------- construction


def test_positional_construction():
    assert Uniform(0.2, 0.8) == Uniform(low=0.2, high=0.8)
    assert Normal(16.0, 1.5) == Normal(mean=16, std=1.5)
    assert Normal(16, 1.5, 0, 24) == Normal(16, 1.5, low=0, high=24)
    assert IntUniform(1, 6).high == 6
    assert LogNormal(0, 0.5).sigma == 0.5
    assert Choice(["a", "b"]).values == ("a", "b")
    assert Choice(["a", "b"], [0.3, 0.7]).p == (0.3, 0.7)
    assert Constant("x").value == "x"
    assert Bernoulli(0.25).p == 0.25


def test_positional_construction_errors():
    with pytest.raises(TypeError, match="at most 2 positional"):
        Uniform(1, 2, 3)
    with pytest.raises(TypeError, match="multiple values for 'low'"):
        Uniform(1, 2, low=1)
    with pytest.raises(ValidationError, match="Field required"):
        Uniform(1)


@pytest.mark.parametrize(
    ("factory", "match"),
    [
        (lambda: Uniform(2, 1), "low <= high"),
        (lambda: IntUniform(5, 1), "low <= high"),
        (lambda: IntUniform(1.5, 3), "valid integer"),
        (lambda: Normal(0, -1), "greater than or equal to 0"),
        (lambda: Normal(0, 1, low=2, high=1), "low <= high"),
        (lambda: LogNormal(0, -0.1), "greater than or equal to 0"),
        (lambda: Choice([]), "at least 1"),
        (lambda: Choice([1, 2], p=[1.0]), "one probability per value"),
        (lambda: Choice([1, 2], p=[0.5, 0.6]), "sum to 1"),
        (lambda: Choice([1, 2], p=[1.5, -0.5]), "sum to 1"),
        (lambda: Bernoulli(1.5), "less than or equal to 1"),
        (lambda: Uniform(0, 1, extra=3), "Extra inputs"),
    ],
)
def test_parameter_validation(factory, match):
    with pytest.raises(ValidationError, match=match):
        factory()


def test_distributions_are_frozen_and_hashable():
    u = Uniform(0.2, 0.8)
    with pytest.raises(ValidationError):
        u.low = 0.0
    assert hash(u) == hash(Uniform(0.2, 0.8))
    assert len({Choice(["a", "b"]), Choice(["a", "b"])}) == 1


def test_repr_is_compact():
    assert repr(Uniform(0.2, 0.8)) == "Uniform(0.2, 0.8)"
    assert repr(Normal(16.0, 1.5)) == "Normal(16.0, 1.5)"
    assert repr(Normal(1, 2, high=3)) == "Normal(1.0, 2.0, high=3.0)"
    assert repr(Choice(["a", "b"])) == "Choice(['a', 'b'])"
    assert repr(Choice([1, 2], p=[0.5, 0.5])) == "Choice([1, 2], p=[0.5, 0.5])"
    assert repr(Constant("x")) == "Constant('x')"
    assert repr(Bernoulli(0.3)) == "Bernoulli(0.3)"
    assert str(IntUniform(1, 6)) == "IntUniform(1, 6)"


# ----------------------------------------------------------------- shorthands

YAML_CASES = [
    ("soc: [0.1, 0.9]", "soc", Uniform(0.1, 0.9)),
    ("soc: 0.5", "soc", Uniform(0.5, 0.5)),
    ("soc: {uniform: [3, 8]}", "soc", Uniform(3, 8)),
    ("soc: {uniform: {low: 3, high: 8}}", "soc", Uniform(3, 8)),
    ("soc: {kind: uniform, low: 3, high: 8}", "soc", Uniform(3, 8)),
    ("soc: {low: 3, high: 8}", "soc", Uniform(3, 8)),
    ("arrival_h: [16, 1.5]", "arrival_h", Normal(16, 1.5)),
    ("arrival_h: 17", "arrival_h", Normal(17, 0)),
    ("arrival_h: {normal: [40, 8]}", "arrival_h", Normal(40, 8)),
    ("arrival_h: {normal: [40, 8, 0]}", "arrival_h", Normal(40, 8, low=0)),
    ("count: 4", "count", IntUniform(4, 4)),
    ("count: {int_uniform: [1, 9]}", "count", IntUniform(1, 9)),
    ("algorithm: [a, b]", "algorithm", Choice(["a", "b"])),
    ("algorithm: {choice: [a, b]}", "algorithm", Choice(["a", "b"])),
    ("algorithm: greedy", "algorithm", Choice(["greedy"])),
    (
        "algorithm: {choice: {values: [a, b], p: [0.2, 0.8]}}",
        "algorithm",
        Choice(["a", "b"], p=[0.2, 0.8]),
    ),
    ("has_pv: 0.6", "has_pv", Bernoulli(0.6)),
    ("has_pv: true", "has_pv", Bernoulli(1.0)),
    ("has_pv: {bernoulli: 0.3}", "has_pv", Bernoulli(0.3)),
    ("size: {lognormal: [0, 0.5]}", "size", LogNormal(0, 0.5)),
    ("size: 1.0", "size", LogNormal(0.0, 0.0)),
    ("delay: 0.04", "delay", Constant(0.04)),
    ("delay: fast", "delay", Constant("fast")),
    ("delay: {normal: [0.04, 0.008]}", "delay", Normal(0.04, 0.008)),
    ("delay: {uniform: [3, 8]}", "delay", Uniform(3, 8)),
    ("delay: {choice: [a, b]}", "delay", Choice(["a", "b"])),
    ("delay: {constant: [1, 2]}", "delay", Constant([1, 2])),
    ("delay: {kind: lognormal, mean: 0, sigma: 1}", "delay", LogNormal(0, 1)),
    ("delay: {bernoulli: [0.5]}", "delay", Bernoulli(0.5)),
    ("delay: {int_uniform: [1, 2]}", "delay", IntUniform(1, 2)),
    ("maybe: 3", "maybe", Constant(3)),
    ("maybe: null", "maybe", None),
]


@pytest.mark.parametrize(("text", "field", "expected"), YAML_CASES)
def test_yaml_shorthands(text, field, expected):
    params = EVParams.model_validate(yaml.safe_load(text))
    assert getattr(params, field) == expected


def test_instances_are_accepted_as_they_are():
    soc = Uniform(0.3, 0.4)
    params = EVParams(soc=soc, delay=Normal(1, 2))
    assert params.soc is soc
    assert params.delay == Normal(1, 2)


@pytest.mark.parametrize(
    ("data", "match"),
    [
        (
            {"soc": {"normal": [1, 2]}},
            "Expected a 'uniform' distribution, got 'normal'",
        ),
        ({"soc": [1, 2, 3]}, "at most 2 positional values"),
        ({"soc": "abc"}, "valid dictionary or instance of Uniform"),
        ({"soc": [5, 1]}, "low <= high"),
        ({"soc": {"uniform": "x"}}, "expected a list of positional values"),
        ({"delay": [1, 2]}, "does not say which distribution"),
        ({"delay": {"low": 1}}, "'low' is not a distribution kind"),
        ({"delay": {"kind": "gamma"}}, "does not match any of the expected tags"),
        ({"delay": {"normal": [1, -1]}}, "greater than or equal to 0"),
        ({"size": -1.0}, "valid dictionary or instance of LogNormal"),
    ],
)
def test_invalid_shorthands(data, match):
    with pytest.raises(ValidationError, match=match):
        EVParams.model_validate(data)


def test_dist_coerces_scalars_to_constant():
    class Model(BaseModel):
        value: Dist

    assert Model(value=3).value == Constant(3)
    assert Model(value=2.5).value == Constant(2.5)
    assert Model(value="x").value == Constant("x")
    assert Model(value=True).value == Constant(True)
    assert isinstance(Model(value=Uniform(1, 2)).value, Uniform)


def test_dist_accepts_every_registered_kind():
    class Model(BaseModel):
        value: Dist

    for example in ALL_EXAMPLES:
        assert Model(value=example.model_dump()).value == example
        assert Model(value=example).value == example
    assert set(distribution_kinds()) >= {
        "constant",
        "uniform",
        "int_uniform",
        "normal",
        "lognormal",
        "choice",
        "bernoulli",
    }


def test_dist_json_schema_is_a_discriminated_union():
    class Model(BaseModel):
        value: Dist

    schema = Model.model_json_schema()
    prop = schema["properties"]["value"]
    assert prop["discriminator"]["propertyName"] == "kind"
    assert "uniform" in prop["discriminator"]["mapping"]
    assert len(prop["oneOf"]) == len(distribution_kinds())


def test_custom_distribution_registers_itself():
    class Triangular(Distribution):
        _positional = ("low", "mode", "high")
        kind: Literal["triangular_test"] = "triangular_test"
        low: float
        mode: float
        high: float

        def _draw_numpy(self, rng, size):
            value = rng.triangular(self.low, self.mode, self.high, size)
            return float(value) if size is None else value

        def _draw_stdlib(self, rng):
            return rng.triangular(self.low, self.high, self.mode)

        @property
        def expected_value(self):
            return (self.low + self.mode + self.high) / 3

    class Model(BaseModel):
        value: Dist

    try:
        assert distribution_kinds()["triangular_test"] is Triangular
        model = Model.model_validate({"value": {"triangular_test": [0, 1, 4]}})
        assert model.value == Triangular(0, 1, 4)
        assert 0 <= model.value.draw(np.random.default_rng(1)) <= 4
        assert 0 <= model.value.draw(random.Random(1)) <= 4
    finally:
        from mango.scenario import distributions

        distributions._REGISTRY.pop("triangular_test", None)


def test_subclass_keeps_registered_kind():
    class MyUniform(Uniform):
        pass

    assert distribution_kinds()["uniform"] is Uniform
    assert MyUniform(1, 2).draw(np.random.default_rng(0)) >= 1


# ----------------------------------------------------------------- serialization


@pytest.mark.parametrize("dist", ALL_EXAMPLES, ids=repr)
def test_model_dump_round_trip(dist):
    dumped = dist.model_dump()
    assert dumped["kind"] == dist.kind
    assert type(dist).model_validate(dumped) == dist
    as_json = dist.model_dump(mode="json")
    assert type(dist).model_validate(yaml.safe_load(yaml.safe_dump(as_json))) == dist


def test_model_dump_shape():
    assert Uniform(0.2, 0.8).model_dump() == {
        "kind": "uniform",
        "low": 0.2,
        "high": 0.8,
    }
    assert Normal(16, 1.5).model_dump() == {
        "kind": "normal",
        "mean": 16.0,
        "std": 1.5,
        "low": None,
        "high": None,
    }


def test_exclude_unset_keeps_whole_distribution():
    assert Uniform(0.2, 0.8).model_dump(exclude_unset=True) == {
        "kind": "uniform",
        "low": 0.2,
        "high": 0.8,
    }
    params = EVParams(soc=Uniform(0.1, 0.2))
    assert params.model_dump(exclude_unset=True) == {
        "soc": {"kind": "uniform", "low": 0.1, "high": 0.2}
    }


def test_params_model_round_trip():
    params = EVParams.model_validate(
        {"soc": [0.1, 0.9], "delay": {"normal": [1, 2]}, "algorithm": ["a", "b"]}
    )
    assert EVParams.model_validate(params.model_dump(mode="json")) == params


# ----------------------------------------------------------------- drawing


@pytest.mark.parametrize("dist", ALL_EXAMPLES, ids=repr)
def test_draw_with_numpy_and_stdlib(dist):
    for rng in _rngs():
        value = dist.draw(rng)
        assert not isinstance(value, np.ndarray)
        array = dist.draw(rng, size=5)
        assert isinstance(array, np.ndarray) and array.shape == (5,)
        grid = dist.draw(rng, size=(2, 3))
        assert grid.shape == (2, 3)


def test_draw_types_and_ranges():
    for rng in _rngs():
        u = Uniform(0.2, 0.8).draw(rng)
        assert type(u) is float and 0.2 <= u <= 0.8
        i = IntUniform(1, 6).draw(rng)
        assert type(i) is int and 1 <= i <= 6
        assert type(Normal(16, 1.5).draw(rng)) is float
        clipped = Normal(0, 10, low=-1, high=1).draw(rng, size=200)
        assert clipped.min() >= -1 and clipped.max() <= 1
        assert LogNormal(0, 1).draw(rng) > 0
        assert Choice(["a", "b"]).draw(rng) in ("a", "b")
        assert Choice(["a", "b"], p=[0.0, 1.0]).draw(rng) == "b"
        assert type(Bernoulli(0.5).draw(rng)) is bool
        assert Bernoulli(1.0).draw(rng) is True
        assert Bernoulli(0.0).draw(rng) is False
        assert Constant("x").draw(rng) == "x"


def test_int_uniform_is_inclusive():
    for rng in _rngs():
        draws = IntUniform(1, 3).draw(rng, size=300)
        assert set(np.unique(draws).tolist()) == {1, 2, 3}


@pytest.mark.parametrize(
    "dist",
    [
        Uniform(0.2, 0.8),
        IntUniform(1, 6),
        Normal(16.0, 1.5),
        Normal(0.0, 1.0, low=-0.5),
        Normal(0.0, 1.0, low=-0.5, high=0.2),
        LogNormal(0.0, 0.5),
        Choice([1, 2, 3], p=[0.5, 0.25, 0.25]),
        Bernoulli(0.3),
    ],
    ids=repr,
)
def test_sample_mean_matches_expected_value(dist):
    for rng in _rngs(3):
        draws = np.asarray(dist.draw(rng, size=4000), dtype=float)
        spread = max(draws.std(), 1e-9)
        assert abs(draws.mean() - dist.expected_value) < 5 * spread / np.sqrt(4000)


def test_expected_value_edge_cases():
    assert Constant(3).expected_value == 3
    assert Normal(5, 0, high=4).expected_value == 4
    assert Normal(5, 2).expected_value == 5
    with pytest.raises(TypeError, match="non-numeric"):
        _ = Choice(["a", "b"]).expected_value


def test_draws_are_reproducible_per_stream():
    tree = SeedTree(42)
    first = [Normal(16, 1.5).draw(tree.child("agent", "ev_0").generator()) for _ in "x"]
    again = [Normal(16, 1.5).draw(tree.child("agent", "ev_0").generator()) for _ in "x"]
    assert first == again
    a = Uniform(0, 1).draw(random.Random(5), size=4)
    b = Uniform(0, 1).draw(random.Random(5), size=4)
    assert (a == b).all()


def test_choice_of_lists_keeps_items():
    dist = Choice([[1, 2], [3, 4]])
    for rng in _rngs():
        assert dist.draw(rng) in ([1, 2], [3, 4])
        draws = dist.draw(rng, size=3)
        assert draws.shape == (3,) and draws.dtype == object


def test_draw_rejects_other_rngs():
    with pytest.raises(TypeError, match="numpy.random.Generator or a random.Random"):
        Uniform(0, 1).draw(SeedTree(1))
    with pytest.raises(TypeError):
        Constant(1).draw(None)


def test_draw_with_random_module():
    assert 0 <= Uniform(0, 1).draw(random) <= 1


@pytest.mark.parametrize(
    "make",
    [
        lambda: Uniform(0, float("inf")),
        lambda: Normal(float("nan"), 1.0),
        lambda: Normal(0.0, 1.0, low=float("-inf")),
        lambda: LogNormal(0.0, float("inf")),
    ],
)
def test_parameters_must_be_finite(make):
    with pytest.raises(ValidationError, match="finite number"):
        make()


def test_open_bounds_are_none_not_infinite():
    assert Normal(0.0, 1.0, low=None).low is None

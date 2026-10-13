import math
import warnings

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from mango.scenario import (  # noqa: E402
    Design,
    DesignPoint,
    Explicit,
    Factor,
    FromCsv,
    FullFactorial,
    StudyError,
    Uniform,
)
from mango.scenario.designs import (  # noqa: E402
    SamplingDesign,
    cross,
    design_kinds,
    resolve_design,
    scale_unit,
)
from mango.util.seeding import SeedTree  # noqa: E402

SEEDS = SeedTree(1).child("design")


# ----------------------------------------------------------------- factor specs


def test_list_and_tuple_give_levels():
    factor = Factor.parse("ev.n", [5, 10, 20])
    assert (factor.kind, factor.levels, factor.labels) == ("levels", (5, 10, 20), None)
    assert Factor.parse("ev.n", (5, 10)).levels == (5, 10)
    assert factor.items() == [(None, 5), (None, 10), (None, 20)]
    assert factor.role == "uncertainty" and not factor.is_labeled


def test_labeled_dict_gives_labeled_levels():
    factor = Factor.parse("comm.loss_percent", {"ideal": 0.0, "lossy": 0.1}, "lever")
    assert factor.labels == ("ideal", "lossy")
    assert factor.levels == (0.0, 0.1)
    assert factor.items() == [("ideal", 0.0), ("lossy", 0.1)]
    assert factor.role == "lever" and factor.is_labeled
    assert factor.label_of(0.1) == "lossy" and factor.label_of(0.5) is None


def test_reserved_keys():
    assert Factor.parse("a", {"values": [1, 2]}).levels == (1, 2)
    levels = Factor.parse("a", {"levels": {"low": 1, "high": 2}})
    assert levels.labels == ("low", "high") and levels.levels == (1, 2)
    fixed = Factor.parse("a", {"value": {"x": 1}})
    assert fixed.kind == "fixed" and fixed.levels == ({"x": 1},) and fixed.is_fixed
    assert Factor.parse("a", {"value": [1, 2]}).levels == ([1, 2],)


@pytest.mark.parametrize(
    ("spec", "suggestion"),
    [
        ({"rnage": [0.1, 1.0]}, "range"),
        ({"valeu": 0.5}, "value"),
        ({"int_rnage": [1, 3]}, "int_range"),
        ({"level": {"a": 1}}, "levels"),
    ],
)
def test_misspelt_reserved_keys_get_a_suggestion(spec, suggestion):
    with pytest.raises(StudyError, match=f"did you mean {suggestion!r}"):
        Factor.parse("x", spec)


def test_a_single_label_that_is_not_a_reserved_key_stays_a_labeled_level():
    factor = Factor.parse("comm.loss_percent", {"lossy": 0.1})
    assert factor.items() == [("lossy", 0.1)]


def test_scalar_and_none_are_fixed():
    assert Factor.parse("ev.n", 10).levels == (10,)
    assert Factor.parse("ev.n", 10).kind == "fixed"
    assert Factor.parse("name", "greedy").levels == ("greedy",)
    assert Factor.parse("seed", None).levels == (None,)


def test_ranges():
    factor = Factor.parse("grid.pv_share", {"range": [0.2, 0.9]})
    assert factor.kind == "range" and factor.bounds == (0.2, 0.9)
    assert factor.is_continuous
    assert Factor.parse("x", {"int_range": [1, 10]}).bounds == (1.0, 10.0)
    assert Factor.parse("x", {"log_range": [0.01, 10]}).kind == "log_range"
    with pytest.raises(StudyError, match="has no levels"):
        factor.items()


def test_values_are_normalized_to_plain_data():
    factor = Factor.parse("ev.soc", [Uniform(0.1, 0.5), (1, 2)])
    assert factor.levels == ({"kind": "uniform", "low": 0.1, "high": 0.5}, [1, 2])
    assert Factor.parse("a", {1: "x", 2.5: "y"}).labels == ("1", "2.5")


@pytest.mark.parametrize(
    "spec, message",
    [
        ([], "has no levels"),
        ({}, "has no levels"),
        ({"values": []}, "has no levels"),
        ({"values": 3}, "'values' needs a list"),
        ({"levels": [1, 2]}, "'levels' needs a mapping"),
        ({"values": [1], "other": 2}, "mixes the reserved key 'values'"),
        ({"range": [0.9, 0.2]}, "low < high"),
        ({"range": [0.2]}, r"needs \[low, high\] numbers"),
        ({"range": ["a", "b"]}, r"needs \[low, high\] numbers"),
        ({"range": [True, 2]}, r"needs \[low, high\] numbers"),
        ({"int_range": [0.5, 3]}, "integer bounds"),
        ({"log_range": [0, 3]}, "low > 0"),
        ([object()], "is not plain data"),
    ],
)
def test_invalid_specs(spec, message):
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # the error alone, no misleading warning
        with pytest.raises(StudyError, match=message) as info:
            Factor.parse("ev.n", spec)
    assert "'ev.n'" in str(info.value)


def test_invalid_path_and_role():
    with pytest.raises(StudyError, match="non-empty str"):
        Factor.parse("", [1])
    with pytest.raises(StudyError, match="unknown role 'output'"):
        Factor.parse("a", [1], role="output")


@pytest.mark.parametrize(
    "spec",
    [
        [1, 2],
        {"ideal": 0.0, "lossy": 0.1},
        {"value": {"a": 1}},
        {"value": [1]},
        3,
        "x",
        {"range": [0.2, 0.9]},
        {"int_range": [1, 4]},
        {"log_range": [0.1, 10.0]},
    ],
)
def test_to_spec_round_trips(spec):
    factor = Factor.parse("p", spec, "lever")
    assert Factor.parse("p", factor.to_spec(), "lever") == factor


def test_describe():
    assert Factor.parse("a", [1, 2]).describe() == "1 | 2"
    assert Factor.parse("a", {"lo": 1, "hi": 2}).describe() == "lo=1 | hi=2"
    assert Factor.parse("a", 3).describe() == "fixed 3"
    assert Factor.parse("a", {"range": [0.2, 0.9]}).describe() == "range [0.2, 0.9]"


# ----------------------------------------------------------------- full factorial


def _factors():
    return [
        Factor.parse("ev.n", [5, 10, 20]),
        Factor.parse("comm.loss_percent", {"ideal": 0.0, "lossy": 0.1}),
        Factor.parse("gain", 0.1),
        Factor.parse("algorithm", ["greedy", "proportional"], "lever"),
    ]


def test_full_factorial_order_and_size():
    points = FullFactorial().generate(_factors(), SEEDS)
    assert len(points) == 3 * 2 * 2
    combos = [
        (p.values["ev.n"], p.values["comm.loss_percent"], p.values["algorithm"])
        for p in points
    ]
    assert combos[:5] == [
        (5, 0.0, "greedy"),
        (5, 0.0, "proportional"),
        (5, 0.1, "greedy"),
        (5, 0.1, "proportional"),
        (10, 0.0, "greedy"),
    ]
    first = points[0]
    assert list(first.values) == ["ev.n", "comm.loss_percent", "gain", "algorithm"]
    assert first.values["gain"] == 0.1
    assert first.labels == {"comm.loss_percent": "ideal"}
    assert points[2].labels == {"comm.loss_percent": "lossy"}
    assert all(isinstance(p, DesignPoint) and p.meta == {} for p in points)


def test_full_factorial_without_factors_gives_one_point():
    assert FullFactorial().generate([], SEEDS) == [DesignPoint({}, {}, {})]


def test_full_factorial_rejects_ranges():
    factors = [Factor.parse("grid.pv_share", {"range": [0.2, 0.9]})]
    with pytest.raises(StudyError, match="continuous factor 'grid.pv_share'") as info:
        FullFactorial().generate(factors, SEEDS)
    assert "sampling design" in str(info.value)


def test_cross():
    points = cross(
        [DesignPoint({"x": 1}, {}, {"row": 0}), DesignPoint({"x": 2}, {}, {"row": 1})],
        [Factor.parse("a", {"lo": 0, "hi": 1})],
    )
    assert [(p.values, p.labels, p.meta) for p in points] == [
        ({"x": 1, "a": 0}, {"a": "lo"}, {"row": 0}),
        ({"x": 1, "a": 1}, {"a": "hi"}, {"row": 0}),
        ({"x": 2, "a": 0}, {"a": "lo"}, {"row": 1}),
        ({"x": 2, "a": 1}, {"a": "hi"}, {"row": 1}),
    ]
    with pytest.raises(StudyError, match="continuous"):
        cross([DesignPoint({})], [Factor.parse("r", {"range": [0, 1]})])


# ----------------------------------------------------------------- explicit and csv


def test_explicit_rows_resolve_labels_and_cross_unset_factors():
    design = Explicit(
        [
            {"algorithm": "greedy", "comm.loss_percent": "lossy"},
            {"algorithm": "market", "comm.loss_percent": 0.0, "extra.path": 3},
        ]
    )
    points = design.generate(_factors(), SEEDS)
    assert len(points) == 2 * 3  # each row crossed with ev.n (gain is fixed)
    first = points[0]
    assert first.values == {
        "comm.loss_percent": 0.1,
        "algorithm": "greedy",
        "ev.n": 5,
        "gain": 0.1,
    }
    assert first.labels == {"comm.loss_percent": "lossy"}
    assert first.meta == {"row": 0}
    last = points[-1]
    assert last.values["extra.path"] == 3 and last.values["ev.n"] == 20
    assert last.labels == {"comm.loss_percent": "ideal"}  # reverse lookup of 0.0
    assert last.meta == {"row": 1}


def test_explicit_alone_and_its_spec():
    design = Explicit([{"a": 1}, {"a": (2, 3)}])
    points = design.generate([], SEEDS)
    assert [p.values for p in points] == [{"a": 1}, {"a": [2, 3]}]
    assert design.to_spec() == {"explicit": [{"a": 1}, {"a": [2, 3]}]}
    assert "2 rows" in repr(design)


@pytest.mark.parametrize(
    "rows, message",
    [
        ([], "at least one row"),
        ("a=1", "list of rows"),
        ([{"a": 1}, 3], "row 1 must be a mapping"),
        ([{"": 1}], "row 0 has invalid factor path"),
    ],
)
def test_explicit_invalid_rows(rows, message):
    with pytest.raises(StudyError, match=message):
        Explicit(rows)


def test_explicit_row_must_set_continuous_factors():
    factors = [Factor.parse("x", {"range": [0, 1]})]
    with pytest.raises(StudyError, match=r"row 0 does not set the continuous"):
        Explicit([{"a": 1}]).generate(factors, SEEDS)


def test_from_csv(tmp_path):
    path = tmp_path / "design.csv"
    path.write_text(
        "ev.n,algorithm,comm.loss_percent,tags\n5,greedy,lossy,[a]\n20,market,0.0,[]\n",
        encoding="utf-8",
    )
    design = FromCsv(path)
    points = design.generate(_factors(), SEEDS)
    assert len(points) == 2
    assert points[0].values == {
        "ev.n": 5,
        "comm.loss_percent": 0.1,
        "algorithm": "greedy",
        "gain": 0.1,
        "tags": ["a"],
    }
    assert points[0].labels == {"comm.loss_percent": "lossy"}
    assert points[1].values["tags"] == [] and points[1].meta == {"row": 1}
    assert design.to_spec() == {"csv": str(path)}


@pytest.mark.parametrize(
    "text, message",
    [
        ("", "the file is empty"),
        ("a,b\n", "header but no rows"),
        ("a,a\n1,2\n", r"duplicate header column\(s\) \['a'\]"),
        ("a,\n1,2\n", "empty column name"),
        ("a,b\n1\n", "line 2 has 1 cells, the header has 2"),
        ("a,b\n1,\n", "line 2, column 'b' is empty"),
    ],
)
def test_from_csv_errors(tmp_path, text, message):
    path = tmp_path / "design.csv"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(StudyError, match=message) as info:
        FromCsv(path).generate([], SEEDS)
    assert "design.csv" in str(info.value)


def test_from_csv_missing_file(tmp_path):
    with pytest.raises(StudyError, match="no such file"):
        FromCsv(tmp_path / "nope.csv").generate([], SEEDS)
    with pytest.raises(StudyError, match="needs a file path"):
        FromCsv(3)


# ----------------------------------------------------------------- registry


def test_resolve_design():
    assert isinstance(resolve_design(None), FullFactorial)
    assert isinstance(resolve_design("full_factorial"), FullFactorial)
    csv_design = resolve_design({"csv": "design.csv"})
    assert isinstance(csv_design, FromCsv) and str(csv_design.path) == "design.csv"
    explicit = resolve_design({"explicit": [{"a": 1}]})
    assert explicit.rows == [{"a": 1}]
    design = FullFactorial()
    assert resolve_design(design) is design
    with pytest.raises(StudyError, match="Did you mean 'full_factorial'"):
        resolve_design("full_factorail")
    with pytest.raises(StudyError, match="Invalid arguments for design 'csv'"):
        resolve_design({"csv": {"file": "x.csv"}})
    with pytest.raises(StudyError, match="one-key mapping"):
        resolve_design(3)


def test_design_kinds_and_custom_designs():
    assert {"full_factorial", "explicit", "csv"} <= set(design_kinds())

    class OneAtATime(Design):
        kind = "test_one_at_a_time"

        def generate(self, factors, seeds):
            base = {f.path: f.items()[0][1] for f in factors}
            points = [DesignPoint(dict(base))]
            for f in factors:
                for _, value in f.items()[1:]:
                    points.append(DesignPoint({**base, f.path: value}))
            return points

    assert design_kinds()["test_one_at_a_time"] is OneAtATime
    design = resolve_design("test_one_at_a_time")
    assert design.to_spec() == "test_one_at_a_time"
    assert len(design.generate(_factors(), SEEDS)) == 1 + 2 + 1 + 0 + 1
    assert repr(FullFactorial()) == "FullFactorial()"
    with pytest.raises(NotImplementedError):
        Design().generate([], SEEDS)


# ----------------------------------------------------------------- sampling extension


class _UniformSample(SamplingDesign):
    def __init__(self, n, dims=None):
        self.n = n
        self.dims = dims

    def unit_samples(self, dimensions, seeds):
        rng = seeds.generator()
        width = self.dims or dimensions
        return [
            (row, {"sample": j}) for j, row in enumerate(rng.random((self.n, width)))
        ]


def test_sampling_design_scales_crosses_and_keeps_fixed():
    factors = [
        Factor.parse("x", {"range": [10.0, 20.0]}),
        Factor.parse("k", {"int_range": [1, 3]}),
        Factor.parse("s", {"log_range": [0.01, 100.0]}),
        Factor.parse("gain", 0.5),
        Factor.parse("algorithm", ["a", "b"], "lever"),
    ]
    points = _UniformSample(4).generate(factors, SEEDS)
    assert len(points) == 4 * 2
    assert [p.meta["sample"] for p in points] == [0, 0, 1, 1, 2, 2, 3, 3]
    assert [p.values["algorithm"] for p in points[:2]] == ["a", "b"]
    for p in points:
        assert list(p.values) == ["x", "k", "s", "gain", "algorithm"]
        assert 10.0 <= p.values["x"] <= 20.0
        assert p.values["k"] in (1, 2, 3) and isinstance(p.values["k"], int)
        assert 0.01 <= p.values["s"] <= 100.0
        assert p.values["gain"] == 0.5
    again = _UniformSample(4).generate(factors, SEEDS)
    assert [p.values for p in again] == [p.values for p in points]


def test_sampling_design_errors():
    with pytest.raises(StudyError, match="has none"):
        _UniformSample(2).generate([Factor.parse("a", [1])], SEEDS)
    with pytest.raises(StudyError, match="3 coordinates for 1 continuous"):
        _UniformSample(2, dims=3).generate(
            [Factor.parse("x", {"range": [0, 1]})], SEEDS
        )
    with pytest.raises(NotImplementedError):
        SamplingDesign().unit_samples(1, SEEDS)


def test_scale_unit():
    int_factor = Factor.parse("k", {"int_range": [1, 3]})
    assert [scale_unit(int_factor, u) for u in (0.0, 0.34, 0.67, 0.999, 1.0)] == [
        1,
        2,
        3,
        3,
        3,
    ]
    log_factor = Factor.parse("s", {"log_range": [1.0, 100.0]})
    assert math.isclose(scale_unit(log_factor, 0.5), 10.0)
    assert scale_unit(Factor.parse("x", {"range": [2.0, 4.0]}), 0.25) == 2.5
    with pytest.raises(StudyError, match="not continuous"):
        scale_unit(Factor("a", "levels", (1,), bounds=(0.0, 1.0)), 0.5)

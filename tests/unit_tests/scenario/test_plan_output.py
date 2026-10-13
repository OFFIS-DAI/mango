"""What plan() prints: edge counts of custom patterns, readable pattern names, profiles."""

import functools
import json

import pytest

from mango import Agent
from mango.scenario import Scenario, ScenarioParams, patterns
from mango.scenario.patterns import pattern_name


class Node(Agent):
    def handle_message(self, content, meta):
        pass


class Params(ScenarioParams):
    n: int = 4


def make_chain():
    def pattern(src, dst, rng):
        return [(src[i], dst[i + 1]) for i in range(len(src) - 1)]

    return pattern


def chain_up(src, dst, rng):
    return [(src[i], dst[i + 1]) for i in range(len(src) - 1)]


def every_kth(src, dst, rng, *, k):
    return [(a, b) for a in src for b in dst if (a.index + b.index) % k == 0]


def coin(src, dst, rng):
    return [(a, b) for a in src for b in dst if rng.random() < 0.5]


def same_zone(src, dst, rng):
    return [
        (a, b) for a in src for b in dst if a.attrs.get("zone") == b.attrs.get("zone")
    ]


def needs_agents(src, dst, rng):
    return [(a, b) for a in src for b in dst if a.agent.aid < b.agent.aid]


@pytest.fixture(scope="module")
def scenario():
    sc = Scenario("plan_out", params=Params)
    g = sc.group(
        "stage", "n", lambda e: Node(), install=lambda e: {"zone": e.index % 2}
    )
    sc.link("closure", g, pattern=make_chain(), directed=True)
    sc.link("custom", g, pattern=chain_up, directed=True)
    sc.link("partial", g, pattern=functools.partial(every_kth, k=2))
    sc.link("coin", g, pattern=coin)
    sc.link("zone", g, pattern=same_zone)
    sc.link("by_attr", g, pattern=patterns.by_attr("zone"))
    sc.link("agents", g, pattern=needs_agents)
    sc.link("filtered", g, pattern="complete", where=lambda a, b: a.attrs["zone"] == 0)
    return sc


def test_custom_patterns_get_exact_counts_when_they_use_no_draws_or_attributes(
    scenario,
):
    plan = scenario.plan()
    edges = {link.name: link.edges for link in plan.links}
    assert edges == {
        "closure": 3,
        "custom": 3,
        "partial": 2,  # (0, 2) and (1, 3); (i, i) self-loops are dropped
        "coin": None,  # draws from rng
        "zone": None,  # reads install attributes
        "by_attr": None,
        "agents": None,  # fails on placeholders (no agents yet): left to the run
        "filtered": None,  # the filter reads install attributes
    }
    actual = scenario.run(seed=1).stats["link_edges"]
    assert all(
        actual[name] == count for name, count in edges.items() if count is not None
    )


def test_plan_names_closures_and_partials_readably(scenario):
    text = str(scenario.plan())
    assert "stage -> stage (make_chain.pattern); 3 edges, directed" in text
    assert "(chain_up); 3 edges" in text
    assert "(every_kth(k=2)); 2 edges" in text
    assert "(coin); ? edges" in text
    assert pattern_name(make_chain()) == "make_chain.pattern"
    named = make_chain()
    named.pattern_name = "chain(i -> i+1)"
    assert pattern_name(named) == "chain(i -> i+1)"
    assert pattern_name(functools.partial(every_kth, 2, k=3)) == "every_kth(2, k=3)"


def test_plan_does_not_consume_the_link_streams(scenario):
    plain = Scenario("plain", params=Params)
    g = plain.group("stage", "n", lambda e: Node())
    plain.link("coin", g, pattern=coin)
    before = plain.run(seed=4).stats["link_edges"]
    plain.plan()
    assert plain.run(seed=4).stats["link_edges"] == before


def test_profiles_are_listed(scenario):
    profiles = {
        "demand": {"values": [0.1, 0.2, 0.3], "resolution": "1h"},
        "load": {
            "path": "data/load.csv",
            "column": "h0",
            "resolution": "15min",
            "start": "2024-01-01T00:00:00",
            "scale": 2.0,
            "interpolation": "linear",
            "wrap": False,
        },
    }
    plan = scenario.plan(overrides={"profiles": profiles})
    assert plan.profiles == {
        "demand": {
            "source": "inline",
            "values": 3,
            "resolution": 3600.0,
            "start": "time.start",
        },
        "load": {
            "source": "csv",
            "path": "data/load.csv",
            "column": "h0",
            "resolution": 900.0,
            "start": "2024-01-01T00:00:00",
            "scale": 2.0,
            "interpolation": "linear",
            "wrap": False,
        },
    }
    text = str(plan)
    assert "  profiles:\n" in text
    assert "demand           inline, 3 values, every 1h, from time.start" in text
    assert (
        "load             csv data/load.csv [h0], every 15min, from "
        "2024-01-01T00:00:00, scale 2, linear interpolation, no wrap"
    ) in text
    assert json.loads(json.dumps(plan.to_dict()))["profiles"] == plan.profiles
    assert "profiles:" not in str(scenario.plan())

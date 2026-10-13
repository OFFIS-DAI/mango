import numpy as np
import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from mango import Agent, SeedTree  # noqa: E402
from mango.scenario import Entity, Scenario, ScenarioError, patterns  # noqa: E402
from mango.scenario.patterns import (  # noqa: E402
    all_to_all,
    builtin_patterns,
    by_attr,
    complete,
    is_deterministic,
    is_within_group,
    line,
    many_to_one,
    one_to_many,
    one_to_one,
    pattern_name,
    resolve_pattern,
    ring,
)


class Plain(Agent):
    def __init__(self, **kwargs):
        super().__init__()
        self.__dict__.update(kwargs)

    def handle_message(self, content, meta):
        pass


def entities(group, n, **attrs):
    return [
        Entity(f"{group}_{i}", group, i, attrs={k: v(i) for k, v in attrs.items()})
        for i in range(n)
    ]


def names(pairs):
    return [(a.name, b.name) for a, b in pairs]


def rng(seed=0):
    return np.random.default_rng(seed)


# ----------------------------------------------------------------- built-ins


def test_one_to_one_and_zip():
    src, dst = entities("a", 3), entities("b", 3)
    assert names(one_to_one(src, dst, rng())) == [
        ("a_0", "b_0"),
        ("a_1", "b_1"),
        ("a_2", "b_2"),
    ]
    assert resolve_pattern("zip") is one_to_one
    with pytest.raises(
        ValueError, match="equal size.*source has 3 entities, target has 2"
    ):
        one_to_one(src, dst[:2], rng())


def test_many_to_one_and_one_to_many():
    src, hub = entities("ev", 3), entities("c", 1)
    assert names(many_to_one(src, hub, rng())) == [
        ("ev_0", "c_0"),
        ("ev_1", "c_0"),
        ("ev_2", "c_0"),
    ]
    assert names(one_to_many(hub, src, rng())) == [
        ("c_0", "ev_0"),
        ("c_0", "ev_1"),
        ("c_0", "ev_2"),
    ]
    with pytest.raises(ValueError, match="exactly one target"):
        many_to_one(src, src, rng())
    with pytest.raises(ValueError, match="exactly one source"):
        one_to_many(src, hub, rng())


def test_all_to_all_complete_ring_line():
    a, b = entities("a", 2), entities("b", 2)
    assert len(all_to_all(a, b, rng())) == 4
    three = entities("n", 3)
    assert len(complete(three, three, rng())) == 6
    assert names(ring(three, three, rng())) == [
        ("n_0", "n_1"),
        ("n_1", "n_2"),
        ("n_2", "n_0"),
    ]
    assert names(ring(three[:2], three[:2], rng())) == [("n_0", "n_1"), ("n_1", "n_0")]
    assert ring(three[:1], three[:1], rng()) == [] and ring([], [], rng()) == []
    assert names(line(three, three, rng())) == [("n_0", "n_1"), ("n_1", "n_2")]
    assert line([], [], rng()) == []


def test_pattern_metadata():
    assert sorted(builtin_patterns()) == [
        "all_to_all",
        "complete",
        "line",
        "many_to_one",
        "one_to_many",
        "one_to_one",
        "ring",
        "zip",
    ]
    assert all(is_deterministic(p) for p in builtin_patterns().values())
    assert [is_within_group(p) for p in (complete, ring, line, all_to_all)] == [
        True,
        True,
        True,
        False,
    ]
    assert pattern_name(ring) == "ring"
    assert (
        pattern_name(patterns.random(per_target=(0, 2))) == "random(per_target=(0, 2))"
    )
    assert pattern_name(patterns.random(0.5)) == "random(p=0.5)"
    assert pattern_name(by_attr("bus")) == "by_attr('bus')"

    def custom(src, dst, rng):
        return []

    # a local function shows where it was made (closures of a factory, too)
    assert pattern_name(custom) == "test_pattern_metadata.custom"
    assert not is_deterministic(custom)
    assert not is_deterministic(patterns.random(0.1))


# ----------------------------------------------------------------- random


def test_random_per_target_is_bounded_and_deterministic():
    src, dst = entities("s", 6), entities("d", 4)
    pattern = patterns.random(per_target=(1, 3))
    pairs = pattern(src, dst, rng(7))
    per_target = {d.name: sum(1 for _, t in pairs if t is d) for d in dst}
    assert all(1 <= k <= 3 for k in per_target.values())
    assert len(set(names(pairs))) == len(pairs)  # without replacement
    assert names(pattern(src, dst, rng(7))) == names(pairs)
    assert any(names(pattern(src, dst, rng(s))) != names(pairs) for s in range(8, 20))


def test_random_within_group_never_links_to_self():
    group = entities("n", 5)
    for pattern in (patterns.random(per_target=4), patterns.random(per_source=(4, 4))):
        pairs = pattern(group, group, rng(1))
        assert len(pairs) == 20 and all(a is not b for a, b in pairs)
    # k is capped at the number of candidates
    assert len(patterns.random(per_target=(9, 9))(group, group, rng(1))) == 20


def test_random_per_source_and_probability():
    src, dst = entities("s", 3), entities("d", 5)
    pairs = patterns.random(per_source=2)(src, dst, rng(3))
    assert [sum(1 for s, _ in pairs if s is x) for x in src] == [2, 2, 2]
    assert len(patterns.random(1.0)(src, dst, rng())) == 15
    assert patterns.random(0.0)(src, dst, rng()) == []
    group = entities("n", 4)
    # within a group each unordered pair is drawn once, earlier -> later
    full = patterns.random(1.0)(group, group, rng())
    assert names(full) == [
        (a.name, b.name) for i, a in enumerate(group) for b in group[i + 1 :]
    ]
    assert patterns.random(0.5)([], [], rng()) == []


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({}, "exactly one of p, per_target or per_source, got none"),
        ({"p": 0.1, "per_target": 1}, "got p, per_target"),
        ({"p": 1.5}, "probability"),
        ({"p": True}, "probability"),
        ({"per_target": (3, 1)}, "0 <= low <= high"),
        ({"per_source": "2"}, "int k or a pair"),
        ({"per_target": (-1, 2)}, "0 <= low"),
    ],
)
def test_random_argument_errors(kwargs, message):
    with pytest.raises(ValueError, match=message):
        patterns.random(**kwargs)


# ----------------------------------------------------------------- by_attr


def test_by_attr_uses_install_attrs_then_agent_attributes():
    hh = entities("hh", 4, bus=lambda i: i % 2)
    ev = [
        Entity("ev_0", "ev", 0, agent=Plain(bus=1)),
        Entity("ev_1", "ev", 1, agent=Plain(bus=0)),
    ]
    pairs = by_attr("bus")(hh, ev, rng())
    assert sorted(names(pairs)) == [
        ("hh_0", "ev_1"),
        ("hh_1", "ev_0"),
        ("hh_2", "ev_1"),
        ("hh_3", "ev_0"),
    ]
    with pytest.raises(
        ValueError,
        match="entity 'x_0' has no install attribute or agent attribute 'bus'",
    ):
        by_attr("bus")(entities("x", 1), ev, rng())
    with pytest.raises(ValueError, match="non-empty attribute name"):
        by_attr("")
    # unhashable values compare by repr
    lists = entities("l", 2, key=lambda i: [1])
    assert len(by_attr("key")(lists, lists, rng())) == 4


# ----------------------------------------------------------------- resolve_pattern


def test_resolve_pattern_forms():
    assert resolve_pattern("ring") is ring
    assert (
        pattern_name(resolve_pattern({"random": {"per_target": [0, 2]}}))
        == "random(per_target=(0, 2))"
    )
    assert pattern_name(resolve_pattern({"random": 0.2})) == "random(p=0.2)"
    assert pattern_name(resolve_pattern({"by_attr": "bus"})) == "by_attr('bus')"
    assert resolve_pattern({"ring": None}) is ring

    def custom(src, dst, rng):
        return []

    assert resolve_pattern(custom) is custom


def test_resolve_pattern_errors():
    with pytest.raises(
        ValueError, match="Unknown link pattern 'rnig'. Did you mean 'ring'"
    ):
        resolve_pattern("rnig")
    with pytest.raises(ValueError, match="needs arguments"):
        resolve_pattern("random")
    with pytest.raises(ValueError, match="exactly one key"):
        resolve_pattern({"random": 1, "by_attr": "x"})
    with pytest.raises(ValueError, match="Unknown pattern factory 'nope'"):
        resolve_pattern({"nope": {}})
    with pytest.raises(TypeError, match="name, a callable"):
        resolve_pattern(3)


# ----------------------------------------------------------------- links in a scenario


def neighbor_names(world, aid, tid):
    by_addr = {agent.addr: name for name, agent in world.agents.items()}
    return sorted(by_addr[addr] for addr in world.agents[aid].neighbors(tid=tid))


def scenario_with(n_ev=3, **time):
    scenario = Scenario("links")
    scenario.time(horizon="1h", step="1h")
    coordinator = scenario.group("coordinator", 1, lambda e: Plain())
    ev = scenario.group(
        "ev", n_ev, lambda e: Plain(), install=lambda e: {"bus": e.index % 2}
    )
    return scenario, coordinator, ev


def test_undirected_link_is_seen_from_both_ends():
    scenario, coordinator, ev = scenario_with()
    scenario.link("dispatch", ev, coordinator, pattern="many_to_one")
    world = scenario.run(seed=1).world
    assert neighbor_names(world, "coordinator", "dispatch") == ["ev_0", "ev_1", "ev_2"]
    assert neighbor_names(world, "ev_1", "dispatch") == ["coordinator"]
    assert world.agents["ev_1"].neighbors(tid="other") == []


def test_directed_link_is_seen_from_the_source_only():
    scenario, coordinator, ev = scenario_with()
    scenario.link("reports", ev, coordinator, pattern="many_to_one", directed=True)
    world = scenario.run(seed=1).world
    assert neighbor_names(world, "ev_0", "reports") == ["coordinator"]
    assert neighbor_names(world, "coordinator", "reports") == []


def test_declarations_with_the_same_name_merge_into_one_topology():
    scenario, coordinator, ev = scenario_with()
    scenario.group("hh", 2, lambda e: Plain())
    scenario.link("reports", ev, coordinator, pattern="many_to_one")
    scenario.link("reports", "hh", coordinator, pattern="many_to_one")
    result = scenario.run(seed=1)
    assert neighbor_names(result.world, "coordinator", "reports") == [
        "ev_0",
        "ev_1",
        "ev_2",
        "hh_0",
        "hh_1",
    ]
    assert result.stats["link_edges"] == {"reports": 5}
    assert scenario.plan().link("reports").edges == 5


def test_merged_declarations_must_agree_on_direction():
    scenario, coordinator, ev = scenario_with()
    scenario.link("x", ev, coordinator, pattern="many_to_one")
    scenario.link("x", coordinator, ev, pattern="one_to_many", directed=True)
    with pytest.raises(ScenarioError, match="both directed and undirected"):
        scenario.plan()


def test_link_errors_name_link_groups_and_sizes():
    scenario, coordinator, ev = scenario_with()
    scenario.link("pairs", ev, coordinator, pattern="one_to_one")
    with pytest.raises(
        ScenarioError,
        match="Link 'pairs' from 'ev' \\(3 entities\\) to 'coordinator' \\(1 entities\\), "
        "pattern 'one_to_one': pattern 'one_to_one' needs groups of equal size",
    ):
        scenario.plan()

    scenario, coordinator, ev = scenario_with()
    scenario.link("x", ev, "coordinater", pattern="many_to_one")
    with pytest.raises(
        ScenarioError,
        match="Unknown group 'coordinater' in link\\('x'\\). Did you mean 'coordinator'\\? "
        "Known groups: 'coordinator', 'ev'",
    ):
        scenario.plan()

    scenario, coordinator, ev = scenario_with()
    scenario.link("x", ev, coordinator, pattern="ring")
    with pytest.raises(
        ScenarioError, match="connects entities within one group; omit the target"
    ):
        scenario.plan()

    with pytest.raises(ScenarioError, match="link\\('x'\\): Unknown link pattern"):
        scenario.link("x", ev, pattern="rnig")
    with pytest.raises(TypeError, match="directed"):
        scenario.link("x", ev, pattern="ring", directed="yes")
    with pytest.raises(TypeError, match="source"):
        scenario.link("x", 3)


def test_ring_with_explicit_same_target_is_allowed():
    scenario, coordinator, ev = scenario_with()
    scenario.link("ring", ev, ev, pattern="ring")
    assert scenario.plan().link("ring").edges == 3


def test_random_link_is_deterministic_per_seed_and_independent_of_other_links():
    def edges(seed, extra_link):
        scenario, coordinator, ev = scenario_with(n_ev=8)
        if extra_link:
            scenario.link("noise", ev, pattern=patterns.random(0.5))
        scenario.link("peers", ev, pattern=patterns.random(per_target=(1, 3)))
        world = scenario.run(seed=seed).world
        return {aid: neighbor_names(world, aid, "peers") for aid in world.agents}

    first = edges(4, extra_link=False)
    assert edges(4, extra_link=False) == first
    assert edges(4, extra_link=True) == first
    assert edges(5, extra_link=False) != first
    assert first["coordinator"] == []


def test_where_by_attr_and_custom_patterns():
    scenario, coordinator, ev = scenario_with(n_ev=4)
    scenario.link(
        "odd",
        ev,
        coordinator,
        pattern="many_to_one",
        where=lambda s, d: s.index % 2 == 1,
    )
    scenario.link("bus", ev, pattern=patterns.by_attr("bus"))

    def chain_to_coordinator(src, dst, rng):
        return [(src[0], dst[0]), (dst[0], dst[0])]  # the self-loop is dropped

    scenario.link("custom", ev, coordinator, pattern=chain_to_coordinator)
    result = scenario.run(seed=1)
    world = result.world
    assert neighbor_names(world, "coordinator", "odd") == ["ev_1", "ev_3"]
    assert neighbor_names(world, "ev_0", "bus") == ["ev_2"]
    assert neighbor_names(world, "coordinator", "custom") == ["ev_0"]
    assert result.stats["link_edges"] == {"odd": 2, "bus": 2, "custom": 1}


def test_custom_pattern_must_return_entity_pairs():
    scenario, coordinator, ev = scenario_with()
    scenario.link(
        "bad", ev, coordinator, pattern=lambda src, dst, rng: [("ev_0", "coordinator")]
    )
    with pytest.raises(
        ScenarioError, match="must return \\(source_entity, target_entity\\) pairs"
    ):
        scenario.run(seed=1)


def test_link_rng_is_the_named_link_stream():
    seen = []

    def capture(src, dst, generator):
        seen.append(generator)
        return []

    scenario, coordinator, ev = scenario_with()
    scenario.link("peers", ev, pattern=capture)
    run = scenario.build(seed=3)
    import asyncio

    async def go():
        async with run:
            return run.seeds

    seeds = asyncio.run(go())
    assert seen[0] is seeds.rng("link/peers")
    assert (
        SeedTree(3).rng("link/peers").random()
        == SeedTree(3).rng("link", "peers").random()
    )


def test_star_import_does_not_shadow_the_random_module():
    namespace = {}
    exec("import random\nfrom mango.scenario.patterns import *", namespace)
    assert namespace["random"].__name__ == "random"
    assert "by_attr" not in namespace
    assert callable(patterns.random) and callable(patterns.by_attr)

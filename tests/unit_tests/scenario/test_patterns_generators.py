"""Graph generators, edge lists, reverse, and patterns/structure from parameters."""

import warnings

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from pydantic import BaseModel  # noqa: E402

from mango import Agent, SeedTree  # noqa: E402
from mango.scenario import (  # noqa: E402
    Entity,
    ParamsError,
    Scenario,
    ScenarioComponent,
    ScenarioError,
    ScenarioParams,
    load_params,
    patterns,
)
from mango.scenario.patterns import (  # noqa: E402
    depends_on_params,
    is_deterministic,
    is_within_group,
    pattern_factories,
    pattern_name,
    resolve_pattern,
)


class Plain(Agent):
    def handle_message(self, content, meta):
        pass


def entities(group, n):
    return [Entity(f"{group}_{i}", group, i) for i in range(n)]


def names(pairs):
    return [(a.name, b.name) for a, b in pairs]


def rng(seed=1):
    return SeedTree(seed).rng("link", "x")


def neighbor_names(world, aid, tid):
    by_addr = {agent.addr: name for name, agent in world.agents.items()}
    return sorted(by_addr[addr] for addr in world.agents[aid].neighbors(tid=tid))


# ----------------------------------------------------------------- direction (C33)


def test_line_and_ring_point_in_index_order_and_reverse_turns_them():
    scenario = Scenario("dir")
    scenario.time(horizon="1h", step="1h")
    node = scenario.group("n", 4, lambda e: Plain())
    scenario.link("down", node, pattern="line", directed=True)
    scenario.link("up", node, pattern=patterns.reverse("line"), directed=True)
    scenario.link("ring_up", node, pattern={"reverse": "ring"}, directed=True)
    world = scenario.run(seed=1).world
    assert neighbor_names(world, "n_0", "down") == ["n_1"]
    assert neighbor_names(world, "n_3", "down") == []
    assert neighbor_names(world, "n_0", "up") == []
    assert neighbor_names(world, "n_3", "up") == ["n_2"]
    assert neighbor_names(world, "n_0", "ring_up") == ["n_3"]
    reversed_line = patterns.reverse("line")
    assert pattern_name(reversed_line) == "reverse(line)"
    assert is_within_group(reversed_line) and is_deterministic(reversed_line)


def test_reverse_works_across_groups_and_with_factories():
    src, dst = entities("a", 2), entities("b", 1)
    pairs = patterns.reverse("many_to_one")(src, dst, rng())
    assert names(pairs) == [("b_0", "a_0"), ("b_0", "a_1")]
    inner = resolve_pattern({"reverse": {"random": {"per_target": 1}}})
    assert pattern_name(inner) == "reverse(random(per_target=(1, 1)))"


def test_a_name_with_arguments_points_to_reverse():
    with pytest.raises(ValueError, match="takes no arguments.*'reverse': 'line'"):
        resolve_pattern({"line": {"reverse": True}})


def test_custom_pattern_gets_entity_lists_and_the_link_stream():
    seen = {}

    def probe(src, dst, rng):
        seen.update(same=src is dst, src=type(src[0]).__name__, rng=type(rng).__name__)
        return []

    scenario = Scenario("probe")
    scenario.time(horizon="1h", step="1h")
    scenario.link("x", scenario.group("n", 2, lambda e: Plain()), pattern=probe)
    scenario.run(seed=1)
    assert seen == {"same": True, "src": "Entity", "rng": "Generator"}


# ----------------------------------------------------------------- generators (C30)


def test_factories_are_registered_with_aliases():
    factories = pattern_factories()
    for name in [
        "small_world",
        "watts_strogatz",
        "scale_free",
        "barabasi_albert",
        "grid",
        "lattice",
        "erdos_renyi",
        "networkx",
        "edges",
        "reverse",
    ]:
        assert name in factories
    assert factories["watts_strogatz"] is patterns.small_world


def test_small_world_is_seeded_from_the_link_stream():
    nodes = entities("n", 20)
    pattern = patterns.small_world(k=4, p=0.2)
    first, again = pattern(nodes, nodes, rng(1)), pattern(nodes, nodes, rng(1))
    assert names(first) == names(again) and len(first) == 40
    assert names(pattern(nodes, nodes, rng(2))) != names(first)
    assert all(a.index < b.index for a, b in first)  # lower -> higher index
    regular = patterns.small_world(k=2, p=0.0)
    assert is_deterministic(regular) and not is_deterministic(pattern)
    assert len(regular(nodes, nodes, rng())) == 20
    assert pattern_name(pattern) == "small_world(k=4, p=0.2)"


@pytest.mark.parametrize(
    "call, message",
    [
        (lambda: patterns.small_world(k=3), "k must be even"),
        (lambda: patterns.small_world(k=4, p=2), "probability"),
        (lambda: patterns.scale_free(m=0), "int >= 1"),
        (lambda: patterns.grid(rows=0), "int >= 1"),
        (lambda: patterns.edges(), "exactly one of file and pairs"),
        (lambda: patterns.edges(pairs=[(1, 2, 3)]), "has 3 values"),
        (lambda: patterns.networkx("no_such_graph"), "no graph generator"),
        (lambda: patterns.random(0.1, max_tries=0), "int >= 1"),
    ],
)
def test_generator_argument_errors(call, message):
    with pytest.raises(ValueError, match=message):
        call()


def test_size_errors_name_the_group_size():
    nodes = entities("n", 4)
    with pytest.raises(ValueError, match="k < the number of entities \\(4\\)"):
        patterns.small_world(k=4)(nodes, nodes, rng())
    with pytest.raises(ValueError, match="m < the number of entities"):
        patterns.scale_free(m=4)(nodes, nodes, rng())
    with pytest.raises(ValueError, match="do not form a square"):
        patterns.grid()(entities("n", 5), (), rng())


def test_scale_free_and_networkx_generators():
    nodes = entities("n", 30)
    pairs = patterns.scale_free(m=2)(nodes, nodes, rng())
    assert len(pairs) == 2 * (30 - 2)
    regular = patterns.networkx("random_regular_graph", d=3)
    pairs = regular(nodes, nodes, rng())
    degree = {}
    for a, b in pairs:
        degree[a.name] = degree.get(a.name, 0) + 1
        degree[b.name] = degree.get(b.name, 0) + 1
    assert set(degree.values()) == {3}
    assert not is_deterministic(regular)
    path = patterns.networkx("path_graph")
    assert is_deterministic(path) and len(path(nodes, nodes, rng())) == 29
    with pytest.raises(ValueError, match="34 nodes for 30 entities"):
        patterns.networkx("karate_club_graph")(nodes, nodes, rng())


def test_grid_lattice_shapes():
    nodes = entities("n", 6)
    pairs = names(patterns.grid(cols=3)(nodes, nodes, rng()))
    assert ("n_0", "n_1") in pairs and ("n_0", "n_3") in pairs and len(pairs) == 7
    torus = patterns.grid(rows=3, cols=3, periodic=True)(entities("n", 9), (), rng())
    assert len(torus) == 18
    moore = patterns.grid(diagonal=True)(entities("n", 9), (), rng())
    assert len(moore) == 20


def test_random_connected_redraws_until_connected():
    nodes = entities("n", 30)
    pattern = patterns.random(0.08, connected=True)
    pairs = pattern(nodes, nodes, rng(3))
    reached, frontier = {"n_0"}, ["n_0"]
    adjacency = {}
    for a, b in pairs:
        adjacency.setdefault(a.name, []).append(b.name)
        adjacency.setdefault(b.name, []).append(a.name)
    while frontier:
        for other in adjacency.get(frontier.pop(), []):
            if other not in reached:
                reached.add(other)
                frontier.append(other)
    assert len(reached) == 30
    assert pattern_name(pattern) == "random(p=0.08, connected=True)"
    with pytest.raises(ValueError, match="no connected graph .* in 5 attempts"):
        patterns.random(0.0, connected=True, max_tries=5)(nodes, nodes, rng())
    assert pattern_name(patterns.erdos_renyi(0.1)) == "erdos_renyi(p=0.1)"


def test_edges_from_pairs_and_files(tmp_path):
    nodes = entities("n", 3)
    pattern = patterns.edges(pairs=[("n_0", "n_2"), (1, 2)])
    assert names(pattern(nodes, nodes, rng())) == [("n_0", "n_2"), ("n_1", "n_2")]
    assert is_deterministic(pattern)
    with pytest.raises(ValueError, match="'n_9' is not an entity.*Did you mean"):
        patterns.edges(pairs=[("n_9", "n_0")])(nodes, nodes, rng())
    csv = tmp_path / "net.csv"
    csv.write_text("ego,alter\nn_0,n_1\n2,n_0\n")
    from_file = patterns.edges(csv, source="ego", target="alter")
    assert names(from_file(nodes, nodes, rng())) == [("n_0", "n_1"), ("n_2", "n_0")]
    with pytest.raises(ValueError, match="no column 'source'"):
        patterns.edges(csv)(nodes, nodes, rng())
    a, b = entities("a", 2), entities("b", 2)
    across = patterns.edges(pairs=[("a_1", "b_0")])
    assert names(across(a, b, rng())) == [("a_1", "b_0")]


def test_generators_are_reproducible_in_a_scenario():
    scenario = Scenario("gen")
    scenario.time(horizon="1h", step="1h")
    node = scenario.group("n", 12, lambda e: Plain())
    scenario.link("sw", node, pattern={"watts_strogatz": {"k": 4, "p": 0.3}})
    scenario.link("ba", node, pattern={"barabasi_albert": {"m": 1}})
    scenario.link("grid", node, pattern={"lattice": {"cols": 4}})
    first = scenario.run(seed=5).stats["link_edges"]
    assert first == scenario.run(seed=5).stats["link_edges"]
    assert first["sw"] == 24 and first["ba"] == 11 and first["grid"] == 17
    assert scenario.plan().link("grid").edges == 17
    with pytest.raises(ScenarioError, match="connects entities within one group"):
        bad = Scenario("bad")
        bad.link("x", bad.group("a", 2, lambda e: Plain()), "b", pattern={"grid": {}})
        bad.group("b", 2, lambda e: Plain())
        bad.plan()


# ----------------------------------------------------------------- structure from params (C06)


class Net(BaseModel, extra="forbid"):
    n: int = 6
    topology: str | dict = "ring"
    p: float = 0.3
    k: int = 2
    periods: int = 3
    ticks: int = 4
    edges: list = []


class NetParams(ScenarioParams):
    net: Net = Net()


def net_scenario():
    scenario = Scenario("net", params=NetParams)
    scenario.time(horizon="12s", step="1s")
    return scenario, scenario.group("node", "net.n", lambda e: Plain())


def test_depends_on_params():
    assert not depends_on_params("ring")
    assert not depends_on_params({"random": 0.1})
    assert depends_on_params("net.topology")
    assert depends_on_params({"param": "net.topology"})
    assert depends_on_params({"random": {"p": "$net.p"}})
    assert depends_on_params({"reverse": "net.topology"})
    assert depends_on_params(lambda p: "ring")
    assert not depends_on_params({"by_attr": "$$literal"})


@pytest.mark.parametrize(
    "spec",
    ["net.topology", {"param": "net.topology"}, lambda p: p.net.topology],
)
def test_pattern_from_a_parameter_is_resolved_per_run(spec):
    scenario, node = net_scenario()
    scenario.link("overlay", node, pattern=spec)
    edges = {
        topology: scenario.run(seed=1, overrides=[f"net.topology={topology}"]).stats[
            "link_edges"
        ]["overlay"]
        for topology in ["ring", "line", "complete", "{small_world: {k: 2, p: 0}}"]
    }
    assert edges == {
        "ring": 6,
        "line": 5,
        "complete": 15,
        "{small_world: {k: 2, p: 0}}": 6,
    }
    plan = scenario.plan(overrides=["net.topology=line"])
    assert plan.link("overlay").edges == 5
    assert plan.link("overlay").declarations[0]["pattern"] == "line"


def test_dollar_references_inside_factory_arguments():
    scenario, node = net_scenario()
    scenario.link("rand", node, pattern={"random": {"p": "$net.p"}})
    scenario.link("sw", node, pattern={"small_world": {"k": "$net.k", "p": 0.0}})
    scenario.link("emp", node, pattern={"edges": {"pairs": "$net.edges"}})
    result = scenario.run(
        seed=1, overrides=["net.k=4", "net.p=1.0", "net.edges=[[node_0, node_5]]"]
    )
    assert result.stats["link_edges"] == {"rand": 15, "sw": 12, "emp": 1}


def test_a_static_spec_keeps_the_fingerprint_and_a_parameter_spec_is_named():
    one, node = net_scenario()
    one.link("x", node, pattern="ring")
    two, node2 = net_scenario()
    two.link("x", node2, pattern="ring")
    assert one.fingerprint() == two.fingerprint()
    three, node3 = net_scenario()
    three.link("x", node3, pattern="net.topology")
    assert three.fingerprint() != one.fingerprint()


def test_parameter_pattern_errors_are_early_and_actionable():
    scenario, node = net_scenario()
    with pytest.raises(ScenarioError, match="Did you mean 'ring'"):
        scenario.link("x", node, pattern="rign")
    with pytest.raises(ScenarioError, match="did you mean 'net.topology'"):
        scenario.link("x", node, pattern="net.topologyy")
    with pytest.raises(ScenarioError, match="write '\\$net.p' to use its value"):
        scenario.link("x", node, pattern={"random": "net.p"})
    with pytest.raises(ScenarioError, match="'net.q' is not a parameter path"):
        scenario.link("x", node, pattern={"random": {"p": "$net.q"}})
    scenario.link("bad", node, pattern="net.topology")
    with pytest.raises(ScenarioError, match="net.topology = 'rign': Unknown link"):
        scenario.plan(overrides=["net.topology=rign"])
    with pytest.raises(ValueError, match="needs the run's parameters"):
        resolve_pattern(lambda p: "ring")


def test_module_factory_references_take_arguments():
    pattern = resolve_pattern(
        {"mango.scenario.patterns:small_world": {"k": 2, "p": 0.0}}
    )
    assert pattern_name(pattern) == "small_world(k=2, p=0.0)"
    with pytest.raises(ValueError, match="Cannot import the pattern"):
        resolve_pattern({"no_such_module_x:f": {}})
    with pytest.raises(TypeError, match="signature small_world"):
        resolve_pattern({"small_world": {"kk": 2}})


def test_kit_patterns_read_the_kit_parameters():
    class Overlay(ScenarioComponent):
        class Params(BaseModel, extra="forbid"):
            n: int = 5
            topology: str = "line"

        def build(self, ctx):
            ctx.group("node", ctx.p.n, lambda e: Plain())

        def wire(self, ctx):
            ctx.link("overlay", "node", pattern="topology")

    scenario = Scenario("kit")
    scenario.time(horizon="1h", step="1h")
    scenario.use(Overlay(), prefix="o")
    assert scenario.run(seed=1).stats["link_edges"] == {"overlay": 4}
    edges = scenario.run(seed=1, overrides=["o.topology=ring"]).stats["link_edges"]
    assert edges == {"overlay": 5}


def test_record_every_from_a_parameter_path_or_function():
    scenario, node = net_scenario()
    scenario.record_world("w", lambda: 1.0, every="net.ticks")
    scenario.record("a", node, lambda a: 1.0, every=lambda p: 2 * p.net.ticks)
    result = scenario.run(seed=1)
    assert result.ts("w").index.tolist() == [0.0, 4.0, 8.0, 12.0]
    assert sorted(set(result.frame[result.frame.variable == "a"].time)) == [
        0.0,
        8.0,
    ]
    assert scenario.plan(overrides=["net.ticks=3"]).recorders[0].every == 3.0
    with pytest.raises(ScenarioError, match="neither a duration .* nor a parameter"):
        scenario.record_world("bad", lambda: 1.0, every="net.tick")
    with pytest.raises(
        ScenarioError, match="every 'net.ticks' \\(= 0\\) is not a positive"
    ):
        scenario.plan(overrides=["net.ticks=0"])


def test_stop_when_every_from_a_parameter():
    scenario, node = net_scenario()
    seen = []
    scenario.stop_when(lambda ctx: seen.append(ctx.world.clock.time), every="net.ticks")
    scenario.run(seed=1)
    assert seen == [4.0, 8.0, 12.0]


def test_time_derived_from_parameters():
    scenario = Scenario("derived", params=NetParams)
    scenario.time(step=1, horizon=lambda p: p.net.periods * p.net.ticks)
    scenario.group("node", 1, lambda e: Plain())
    assert scenario.resolve_params().time.horizon == 12.0
    assert scenario.resolve_params(overrides=["net.periods=5"]).time.horizon == 20.0
    assert scenario.run(seed=1, overrides=["net.ticks=2"]).stats["steps"] == 6
    with pytest.raises(ParamsError, match="derived from function .* also set"):
        scenario.resolve_params(overrides=["time.horizon=100s"])
    # an explicit value equal to the derived one (a resolved config) is fine
    config = scenario.resolve_params().model_dump(mode="json")
    assert load_params(scenario, config).time.horizon == 12.0
    by_path = Scenario("path", params=NetParams)
    by_path.time(step=1, horizon="net.ticks")
    assert by_path.resolve_params().time.horizon == 4.0
    by_path.time(horizon="8s")  # a literal replaces the derivation
    assert by_path.resolve_params().time.horizon == 8.0
    with pytest.raises(ParamsError, match="Invalid scenario.time"):
        Scenario("bad", params=NetParams).time(horizon="net.tick")


# ----------------------------------------------------------------- record lists (C31)


def test_record_accepts_a_list_of_groups():
    scenario = Scenario("lists")
    scenario.time(horizon="1h", step="1h")
    red = scenario.group("red", 2, lambda e: Plain())
    scenario.group("blue", 1, lambda e: Plain())
    scenario.record("one", [red, "blue"], lambda a: 1.0)
    plan = scenario.plan()
    assert [(r.name, r.group) for r in plan.recorders] == [
        ("one", "red"),
        ("one", "blue"),
    ]
    assert set(scenario.run(seed=1).wide("one").columns) == {"red_0", "red_1", "blue"}
    with pytest.raises(ScenarioError, match="at least one group"):
        scenario.record("none", [], lambda a: 1.0)
    with pytest.raises(TypeError, match="GroupRef or a group name, got int"):
        scenario.record("bad", [red, 3], lambda a: 1.0)
    twice = Scenario("twice")
    twice.group("red", 1, lambda e: Plain())
    twice.record("x", ["red", "red"], lambda a: 1.0)
    with pytest.raises(ScenarioError, match="declared twice on group 'red'"):
        twice.plan()


def test_no_warnings_for_static_specs():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        scenario, node = net_scenario()
        scenario.link("x", node, pattern="ring")
        scenario.plan()

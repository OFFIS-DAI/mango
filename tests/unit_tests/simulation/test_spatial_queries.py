"""Public neighbour and position queries of Area2D and DefaultEnvironment."""

import pytest

from mango import (
    Agent,
    Area2D,
    Behavior,
    DefaultEnvironment,
    Position2D,
    create_world,
)


class Bird(Agent):
    def __init__(self):
        super().__init__()
        self.seen = []

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        self.seen = [other.aid for other in env.space.neighbors_within(self, 5.0)]


def _world(xs):
    space = Area2D(width=100.0, height=100.0)
    world = create_world(seed=1, environment=DefaultEnvironment(space=space))
    birds = [world.register(Bird(), f"b{i}") for i in range(len(xs))]
    for bird, x in zip(birds, xs):
        space.move(bird, Position2D(x, 0.0))
    return world, space, birds


@pytest.mark.asyncio
async def test_neighbors_within_is_sorted_inclusive_and_excludes_the_agent():
    _, space, birds = _world([0.0, 4.0, 5.0, 2.0, 9.0])
    assert [b.aid for b in space.neighbors_within(birds[0], 5.0)] == ["b3", "b1", "b2"]


@pytest.mark.asyncio
async def test_agents_within_defaults_to_all_placed_agents():
    _, space, birds = _world([0.0, 4.0, 9.0])
    assert space.agents_within(birds[0], 5.0) == [birds[1]]
    assert space.agents_within(birds[0], 10.0, agents=[birds[2]]) == [birds[2]]


@pytest.mark.asyncio
async def test_positions_is_a_copy_keyed_by_aid():
    _, space, birds = _world([1.0, 2.0])
    positions = space.positions()
    assert positions == {"b0": Position2D(1.0, 0.0), "b1": Position2D(2.0, 0.0)}
    positions["b0"] = Position2D(50.0, 50.0)
    assert space.location(birds[0]) == Position2D(1.0, 0.0)


@pytest.mark.asyncio
async def test_space_agents_lists_only_placed_agents():
    space = Area2D()
    world = create_world(seed=1, environment=DefaultEnvironment(space=space))
    placed = world.register(Bird(), "placed")
    unplaced = world.register(Bird(), "unplaced")
    space.move(placed, Position2D(1.0, 1.0))
    assert space.agents == [placed]
    assert world.environment.agents == [placed, unplaced]


@pytest.mark.asyncio
async def test_location_of_an_unplaced_agent_explains_how_to_place_it():
    space = Area2D()
    world = create_world(seed=1, environment=DefaultEnvironment(space=space))
    bird = world.register(Bird(), "b")
    with pytest.raises(KeyError, match="space.move"):
        space.location(bird)


@pytest.mark.asyncio
async def test_agents_query_neighbours_in_on_step():
    world, _, birds = _world([0.0, 3.0, 20.0])
    async with world:
        await world.step(step_size_s=1.0)
    assert birds[0].seen == ["b1"]
    assert birds[2].seen == []


@pytest.mark.asyncio
async def test_randomly_placed_agents_are_queryable_after_start():
    space = Area2D(width=1.0, height=1.0)
    world = create_world(seed=1, environment=DefaultEnvironment(space=space))
    birds = [world.register(Bird(), f"b{i}") for i in range(3)]
    async with world:
        await world.step(step_size_s=1.0)
    assert set(space.positions()) == {"b0", "b1", "b2"}
    # the unit square's diagonal is < 5: everyone sees everyone
    assert sorted(birds[0].seen) == ["b1", "b2"]


@pytest.mark.asyncio
async def test_behaviors_iterate_the_population_through_environment_agents():
    class Drift(Behavior):
        def on_step(self, environment, clock, step_size_s):
            for agent in environment.agents:
                p = environment.space.location(agent)
                environment.space.move(agent, Position2D(p.x + 1.0, p.y))

    space = Area2D(width=100.0, height=100.0)
    world = create_world(
        seed=1, environment=DefaultEnvironment(space=space, behavior=Drift())
    )
    bird = world.register(Bird(), "b")
    space.move(bird, Position2D(0.0, 0.0))
    async with world:
        await world.step(step_size_s=1.0)
    assert space.location(bird) == Position2D(1.0, 0.0)


# point queries: a Position2D as centre (waypoints, static targets)


@pytest.mark.asyncio
async def test_agents_within_a_point_excludes_nothing():
    _, space, birds = _world([9.0, 12.0, 19.0])
    center = Position2D(10.0, 0.0)
    assert space.agents_within(center, 5.0) == [birds[0], birds[1]]
    assert space.agents_within(center, 5.0, agents=[birds[1], birds[2]]) == [birds[1]]


@pytest.mark.asyncio
async def test_neighbors_within_a_point_is_nearest_first():
    _, space, birds = _world([9.0, 12.0, 19.0])
    assert space.neighbors_within(Position2D(11.5, 0.0), 5.0) == [birds[1], birds[0]]


@pytest.mark.asyncio
async def test_distance_and_move_toward_take_agents_and_points():
    _, space, birds = _world([0.0, 3.0])
    assert space.distance(birds[0], Position2D(0.0, 4.0)) == 4.0
    assert space.distance(Position2D(0.0, 4.0), birds[1]) == 5.0
    assert space.distance(birds[0], birds[1]) == 3.0
    space.move_toward(birds[0], Position2D(10.0, 0.0), max_step=2.0)
    assert space.location(birds[0]) == Position2D(2.0, 0.0)


@pytest.mark.asyncio
async def test_wrong_centre_types_raise_a_clear_type_error():
    _, space, birds = _world([0.0])
    with pytest.raises(TypeError, match=r"center must be an agent or a Position2D"):
        space.agents_within("b0", 5.0)
    with pytest.raises(TypeError, match=r"Use Position2D\(1\.0, 2\.0\) for a point"):
        space.neighbors_within((1.0, 2.0), 5.0)
    with pytest.raises(TypeError, match="distance"):
        space.distance(birds[0], 3.0)
    with pytest.raises(TypeError, match="takes an agent"):
        space.location(Position2D(1.0, 1.0))


@pytest.mark.asyncio
async def test_location_of_a_removed_agent_mentions_retirement():
    world, space, birds = _world([0.0])
    space._positions.pop("b0")
    with pytest.raises(KeyError, match="retired or deregistered"):
        space.location(birds[0])

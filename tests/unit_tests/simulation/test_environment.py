from unittest.mock import MagicMock

import pytest

from mango import Area2D, Position2D, distance


def _agent(aid: str):
    a = MagicMock()
    a.aid = aid
    return a


def test_distance_zero():
    p = Position2D(3.0, 4.0)
    assert distance(p, p) == pytest.approx(0.0)


def test_distance_pythagorean():
    assert distance(Position2D(0, 0), Position2D(3, 4)) == pytest.approx(5.0)


def test_distance_negative_coords():
    assert distance(Position2D(-1, -1), Position2D(2, 3)) == pytest.approx(5.0)


def test_area2d_distance_basic():
    space = Area2D()
    a1, a2 = _agent("a1"), _agent("a2")
    space.move(a1, Position2D(0.0, 0.0))
    space.move(a2, Position2D(3.0, 4.0))
    assert space.distance(a1, a2) == pytest.approx(5.0)


def test_area2d_distance_symmetric():
    space = Area2D()
    a1, a2 = _agent("a1"), _agent("a2")
    space.move(a1, Position2D(1.0, 2.0))
    space.move(a2, Position2D(4.0, 6.0))
    assert space.distance(a1, a2) == pytest.approx(space.distance(a2, a1))


def test_agents_within_finds_nearby():
    space = Area2D()
    center = _agent("center")
    near = _agent("near")
    far = _agent("far")
    space.move(center, Position2D(0.0, 0.0))
    space.move(near, Position2D(1.0, 0.0))
    space.move(far, Position2D(10.0, 0.0))
    result = space.agents_within(center, 2.0, [center, near, far])
    assert near in result
    assert far not in result


def test_agents_within_excludes_center():
    space = Area2D()
    center = _agent("center")
    space.move(center, Position2D(0.0, 0.0))
    result = space.agents_within(center, 100.0, [center])
    assert result == []


def test_agents_within_exact_radius():
    space = Area2D()
    center = _agent("c")
    edge = _agent("e")
    space.move(center, Position2D(0.0, 0.0))
    space.move(edge, Position2D(5.0, 0.0))
    result = space.agents_within(center, 5.0, [center, edge])
    assert edge in result


def test_agents_within_skips_no_position():
    space = Area2D()
    center = _agent("c")
    ghost = _agent("ghost")
    space.move(center, Position2D(0.0, 0.0))
    result = space.agents_within(center, 100.0, [center, ghost])
    assert ghost not in result


def test_agents_within_empty_list():
    space = Area2D()
    center = _agent("c")
    space.move(center, Position2D(0.0, 0.0))
    assert space.agents_within(center, 5.0, []) == []


def test_move_toward_moves_partial_distance():
    space = Area2D()
    agent = _agent("a")
    target = _agent("t")
    space.move(agent, Position2D(0.0, 0.0))
    space.move(target, Position2D(10.0, 0.0))
    space.move_toward(agent, target, max_step=3.0)
    pos = space.location(agent)
    assert pos.x == pytest.approx(3.0)
    assert pos.y == pytest.approx(0.0)


def test_move_toward_reaches_target_when_close():
    space = Area2D()
    agent = _agent("a")
    target = _agent("t")
    space.move(agent, Position2D(0.0, 0.0))
    space.move(target, Position2D(2.0, 0.0))
    space.move_toward(agent, target, max_step=10.0)
    pos = space.location(agent)
    assert pos.x == pytest.approx(2.0)
    assert pos.y == pytest.approx(0.0)


def test_move_toward_diagonal():
    space = Area2D()
    agent = _agent("a")
    target = _agent("t")
    space.move(agent, Position2D(0.0, 0.0))
    space.move(target, Position2D(3.0, 4.0))
    space.move_toward(agent, target, max_step=5.0)
    pos = space.location(agent)
    assert pos.x == pytest.approx(3.0)
    assert pos.y == pytest.approx(4.0)


def test_move_toward_position2d_target():
    """Target may be a Position2D directly (not an agent)."""
    space = Area2D()
    agent = _agent("a")
    space.move(agent, Position2D(0.0, 0.0))
    space.move_toward(agent, Position2D(6.0, 0.0), max_step=2.0)
    assert space.location(agent).x == pytest.approx(2.0)


def test_move_toward_already_at_target():
    space = Area2D()
    agent = _agent("a")
    target = _agent("t")
    space.move(agent, Position2D(5.0, 5.0))
    space.move(target, Position2D(5.0, 5.0))
    space.move_toward(agent, target, max_step=1.0)
    pos = space.location(agent)
    assert pos.x == pytest.approx(5.0)
    assert pos.y == pytest.approx(5.0)

"""
Environment system for mango's SimulationWorld.

Provides a spatial environment with pluggable space and behavior models.
"""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mango.util import tracing

if TYPE_CHECKING:
    import numpy

    from mango.util.clock import Clock


class Position:
    """Marker base class for position types."""


@dataclass
class Position2D(Position):
    """A 2-D Cartesian position."""

    x: float
    y: float

    def __repr__(self) -> str:
        return f"Position2D(x={self.x}, y={self.y})"


def distance(pa: Position2D, pb: Position2D) -> float:
    """Return the Euclidean distance between two :class:`Position2D` points.

    :param pa: first position
    :param pb: second position
    :return: Euclidean distance

    Example::

        d = distance(Position2D(0, 0), Position2D(3, 4))  # → 5.0
    """
    return math.sqrt((pa.x - pb.x) ** 2 + (pa.y - pb.y) ** 2)


class Space(ABC):
    """Abstract space in which agents can be placed and moved."""

    @abstractmethod
    def move(self, agent, position: Position) -> None:
        """Move *agent* to *position*."""

    @abstractmethod
    def location(self, agent) -> Position:
        """Return the current position of *agent*."""

    @abstractmethod
    def has_position(self, agent) -> bool:
        """Return ``True`` if *agent* has a registered position."""

    def initialize(self, agents: list, clock: Clock) -> None:
        """Called once before the first simulation step."""

    def install(self, agent, **kwargs) -> None:
        """Install *agent* in the space (called when agent is added to env)."""


class Area2D(Space):
    """A rectangular 2-D space.

    Agents without a predefined position receive a random location within
    [0, width] × [0, height] during :meth:`initialize`, drawn from
    ``rng``.  A world binds its ``"space"`` stream to ``rng`` when it
    is still ``None`` (see :func:`~mango.simulation.world.create_world`); a
    standalone, unbound space falls back to the global :mod:`random` module.

    The ``"space"`` stream is shared: positions are drawn one agent after the
    other in registration order, so registering another agent *before* an
    agent moves that agent's random position (registering it after does not).
    For positions that do not depend on the rest of the scenario, set them
    explicitly with :meth:`move`, e.g. drawn from each agent's own ``rng``.

    Positions live in the space, not on the agents. Agents and behaviors
    query them through the public API: :meth:`location`,
    :meth:`positions` (all positions by aid), :attr:`agents` (all placed
    agents), :meth:`neighbors_within` (nearest first) and
    :meth:`agents_within` (candidate order). The queries and :meth:`distance`
    take an agent or a :class:`Position2D` (a point such as a waypoint or a
    static target). Agents reach the space through the ``env`` argument of
    ``on_step`` (``env.space``).

    :param width: extent in x direction
    :param height: extent in y direction
    :param rng: random generator for initial positions (any object with a
        ``random()`` method, e.g. ``numpy.random.Generator``); ``None`` lets
        the world bind its ``"space"`` stream

    Example::

        space = Area2D(width=100.0, height=100.0)  # the world binds "space"
        world = create_world(seed=42, environment=DefaultEnvironment(space=space))

        class Bird(Agent):
            def on_step(self, env, clock, step_size_s):
                flock = env.space.neighbors_within(self, radius=5.0)
                if flock:
                    env.space.move_toward(self, flock[0], max_step=1.0)
    """

    def __init__(
        self,
        width: float = 10.0,
        height: float = 10.0,
        rng: numpy.random.Generator | random.Random | None = None,
    ):
        self.width = width
        self.height = height
        self.rng = rng
        self._positions: dict[str, Position2D] = {}
        self._agents: dict[str, Any] = {}

    def _agent_index(self) -> dict[str, Any]:
        """aid -> agent, for queries that return agents (created lazily for
        subclasses that do not call ``super().__init__()``)."""
        return self.__dict__.setdefault("_agents", {})

    def move(self, agent, position: Position2D) -> None:
        """Place *agent* at *position*.

        :param agent: the agent to move
        :param position: its new :class:`Position2D`

        Example::

            space.move(agent, Position2D(2.0, 3.5))
        """
        self._agent_index()[agent.aid] = agent
        self._positions[agent.aid] = position

    def location(self, agent) -> Position2D:
        """Return the position of *agent*.

        :param agent: an agent with a position
        :return: its :class:`Position2D`
        :raises KeyError: if the agent has no position yet (positions of
            agents not placed with :meth:`move` are drawn when the world
            starts)

        Example::

            here = env.space.location(self)
        """
        try:
            aid = agent.aid
        except AttributeError:
            raise TypeError(
                f"space.location() takes an agent, got {type(agent).__name__} "
                f"{agent!r}."
            ) from None
        try:
            return self._positions[aid]
        except KeyError:
            raise KeyError(
                f"Agent {aid!r} has no position in this Area2D: place it with "
                "space.move(agent, Position2D(x, y)), or wait until the world has "
                "started (unplaced agents get a random position then). An agent "
                "that was retired or deregistered has no position any more."
            ) from None

    def _point(self, where, what: str) -> Position2D:
        """The position of *where*: an agent's location, or *where* itself as a point."""
        if hasattr(where, "aid"):
            return self.location(where)
        if isinstance(where, Position2D) or (
            hasattr(where, "x") and hasattr(where, "y")
        ):
            return where
        hint = (
            f" Use Position2D{tuple(where)!r} for a point."
            if isinstance(where, tuple) and len(where) == 2
            else ""
        )
        raise TypeError(
            f"{what} must be an agent or a Position2D, got "
            f"{type(where).__name__} {where!r}.{hint}"
        )

    def has_position(self, agent) -> bool:
        return agent.aid in self._positions

    @property
    def agents(self) -> list:
        """All agents that have a position, in placement order.

        Example::

            xs = [env.space.location(a).x for a in env.space.agents]
        """
        index = self._agent_index()
        return [index[aid] for aid in self._positions if aid in index]

    def positions(self) -> dict[str, Position2D]:
        """Return the position of every placed agent, keyed by aid.

        The returned dict is a copy; change positions with :meth:`move`.

        :return: ``{aid: Position2D}`` in placement order

        Example::

            snapshot = env.space.positions()
            mean_x = sum(p.x for p in snapshot.values()) / len(snapshot)
        """
        return dict(self._positions)

    def neighbors_within(self, agent, radius: float) -> list:
        """Return the agents within *radius* of *agent*, nearest first.

        Searches all placed agents (see :attr:`agents`); *agent* itself is
        excluded. Ties keep placement order. The scan is linear in the number
        of placed agents.

        :param agent: the reference agent (must have a position), or a
            :class:`Position2D` for the agents near a point
        :param radius: search radius in space units (inclusive)
        :return: list of agents ordered by distance
        :raises TypeError: if *agent* is neither an agent nor a Position2D

        Example::

            class Bird(Agent):
                def on_step(self, env, clock, step_size_s):
                    for other in env.space.neighbors_within(self, radius=5.0):
                        ...

            near_depot = space.neighbors_within(Position2D(10.0, 10.0), 5.0)
        """
        center = self._point(agent, "neighbors_within(agent, ...): agent")
        index = self._agent_index()
        found = []
        for aid, position in self._positions.items():
            other = index.get(aid)
            if other is None or other is agent:
                continue
            d = distance(center, position)
            if d <= radius:
                found.append((d, other))
        found.sort(key=lambda pair: pair[0])
        return [other for _, other in found]

    def initialize(self, agents: list, clock: Clock) -> None:
        rng = getattr(self, "rng", None)
        if rng is None:
            rng = random
        index = self._agent_index()
        for agent in agents:
            index[agent.aid] = agent
            if agent.aid not in self._positions:
                self._positions[agent.aid] = Position2D(
                    x=float(rng.random()) * self.width,
                    y=float(rng.random()) * self.height,
                )

    def install(self, agent, **kwargs) -> None:
        self._agent_index()[agent.aid] = agent

    def distance(self, agent_a, agent_b) -> float:
        """Return the Euclidean distance between two agents or points.

        Agents must have a registered position.

        :param agent_a: first agent or :class:`Position2D`
        :param agent_b: second agent or :class:`Position2D`
        :return: Euclidean distance in space units
        :raises TypeError: if an argument is neither an agent nor a Position2D

        Example::

            d = space.distance(agent1, agent2)
            to_goal = space.distance(drone, Position2D(50.0, 50.0))
        """
        return distance(
            self._point(agent_a, "distance(a, b): a"),
            self._point(agent_b, "distance(a, b): b"),
        )

    def agents_within(self, center, radius: float, agents=None) -> list:
        """Return the agents within *radius* of *center*, in candidate order.

        *center* is an agent (excluded from the result) or a
        :class:`Position2D` (a point query, e.g. around a waypoint).  Only
        agents that have a registered position are considered. For "who is
        near me" from inside an agent prefer :meth:`neighbors_within`
        (nearest first).

        :param center: the reference agent or point
        :param radius: search radius in space units (inclusive)
        :param agents: candidate agents to search among; ``None`` (default)
            searches all placed agents (:attr:`agents`)
        :return: list of agents within *radius* of *center*
        :raises TypeError: if *center* is neither an agent nor a Position2D

        Example::

            nearby = space.agents_within(my_agent, 5.0)
            red_nearby = space.agents_within(my_agent, 5.0, red_team)
            at_goal = space.agents_within(Position2D(10.0, 10.0), 2.0, drones)
        """
        point = self._point(center, "agents_within(center, ...): center")
        if agents is None:
            agents = self.agents
        result = []
        for agent in agents:
            if agent is center:
                continue
            if (
                self.has_position(agent)
                and distance(point, self.location(agent)) <= radius
            ):
                result.append(agent)
        return result

    def move_toward(
        self,
        agent,
        target: Position2D | object,
        max_step: float,
    ) -> None:
        """Move *agent* toward *target* by at most *max_step* units.

        *target* may be either another agent (with a registered position) or
        a :class:`Position2D` directly.  If the agent is already within
        *max_step* of the target, it is moved exactly to the target position.

        :param agent: the agent to move
        :param target: destination agent or :class:`Position2D`
        :param max_step: maximum distance to travel in one call

        Example::

            space.move_toward(rover, base_station, max_step=1.0)
        """
        pa = self.location(agent)
        pt = self._point(target, "move_toward(agent, target, ...): target")
        dist = math.sqrt((pt.x - pa.x) ** 2 + (pt.y - pa.y) ** 2)
        if dist == 0.0 or dist <= max_step:
            self._positions[agent.aid] = Position2D(x=pt.x, y=pt.y)
        else:
            ratio = max_step / dist
            self._positions[agent.aid] = Position2D(
                x=pa.x + (pt.x - pa.x) * ratio,
                y=pa.y + (pt.y - pa.y) * ratio,
            )


class NoSpace(Space):
    """A space without spatial positioning.

    Use this when agents do not need positions.  Every agent reports no
    position, and attempts to :meth:`move` or :meth:`location` raise a
    :class:`RuntimeError`.  This is the default space for
    :class:`DefaultEnvironment`.

    Example::

        env = DefaultEnvironment()          # uses NoSpace by default
        env = DefaultEnvironment(space=NoSpace())
    """

    def move(self, agent, position: Position) -> None:
        raise RuntimeError(
            "NoSpace has no spatial positioning; use a space such as Area2D "
            "to move agents."
        )

    def location(self, agent) -> Position:
        raise RuntimeError(
            "NoSpace has no spatial positioning; use a space such as Area2D "
            "to locate agents."
        )

    def has_position(self, agent) -> bool:
        return False


class Behavior(ABC):
    """Abstract environment behavior.

    Override :meth:`on_step` to model environment dynamics and
    :meth:`initialize` for one-time setup.

    A behavior that needs randomness declares a public ``rng`` attribute
    initialised to ``None``; the world then binds its ``"behavior"`` stream to
    it (an explicitly passed generator is kept).

    Example::

        class Weather(Behavior):
            def __init__(self, rng=None):
                self.rng = rng

            def on_step(self, environment, clock, step_size_s):
                self.irradiance = self.rng.uniform(0.0, 1000.0)
    """

    def on_step(
        self,
        environment: Environment,
        clock: Clock,
        step_size_s: float,
    ) -> None:
        """Called on every simulation step."""

    def initialize(
        self,
        environment: Environment,
        clock: Clock,
    ) -> None:
        """Called once before the first simulation step."""

    def install(self, agent, **kwargs) -> None:
        """Called when an agent is installed in the environment."""


class WorldObserver(ABC):
    """Observer that can receive global events from the environment."""

    @abstractmethod
    def dispatch_global_event(self, clock: Clock, event: Any) -> None:
        """Dispatch a global event to observers."""


class Environment(ABC):
    """Abstract environment interface.

    An environment that needs randomness declares a public ``rng`` attribute
    initialised to ``None``; the world then binds its ``"environment"`` stream
    to it (an explicitly passed generator is kept).
    """

    @abstractmethod
    def initialize(self, agents: list, clock: Clock) -> None:
        """Initialize the environment with the given agents."""

    @abstractmethod
    def initialized(self) -> bool:
        """Return whether the environment has been initialized."""

    @abstractmethod
    def step(self, clock: Clock, step_size_s: float) -> None:
        """Step the environment forward by *step_size_s* seconds."""

    @abstractmethod
    def emit_global_event(self, event: Any) -> None:
        """Broadcast *event* to all registered observers (and thus agents)."""

    @abstractmethod
    def emit_agent_event(self, event: Any, agent_id: Any) -> None:
        """Deliver *event* to a specific agent identified by *agent_id*."""


class DefaultEnvironment(Environment):
    """Full environment implementation with pluggable space and behavior.

    Example::

        env = DefaultEnvironment(space=Area2D(100, 100))
        world = create_world(start_time=0.0, environment=env)
    """

    def __init__(
        self,
        space: Space | None = None,
        behavior: Behavior | None = None,
    ):
        self._space: Space = space or NoSpace()
        self._behavior: Behavior = behavior or _NoBehavior()
        self._observers: list[WorldObserver] = []
        self._id_to_agent: dict[Any, Any] = {}
        self._initialized: bool = False

    @property
    def agents(self) -> list:
        """All agents installed in this environment, in registration order.

        Behaviors use it to iterate the population (``on_step`` receives the
        environment).

        Example::

            class Drift(Behavior):
                def on_step(self, environment, clock, step_size_s):
                    for agent in environment.agents:
                        ...
        """
        return list(self._id_to_agent.values())

    @property
    def space(self) -> Space:
        return self._space

    @property
    def behavior(self) -> Behavior:
        return self._behavior

    def initialized(self) -> bool:
        return self._initialized

    def initialize(self, agents: list, clock: Clock) -> None:
        self._space.initialize(agents, clock)
        self._behavior.initialize(self, clock)
        self._initialized = True

    def step(self, clock: Clock, step_size_s: float) -> None:
        self._behavior.on_step(self, clock, step_size_s)

    def install(self, agent, agent_id: Any = None, **kwargs) -> None:
        """Register *agent* in the space and behavior.

        :param agent: the agent to install
        :param agent_id: identifier used to retrieve the agent later
        """
        key = agent_id if agent_id is not None else agent.aid
        self._space.install(agent, **kwargs)
        self._behavior.install(agent, **kwargs)
        self._id_to_agent[key] = agent

    def add_observer(self, observer: WorldObserver) -> None:
        """Register an observer to receive global events."""
        self._observers.append(observer)

    def emit_global_event(self, event: Any) -> None:
        """Broadcast *event* to all registered observers."""
        for observer in self._observers:
            observer.dispatch_global_event(None, event)

    def emit_agent_event(self, event: Any, agent_id: Any) -> None:
        """Deliver *event* to the agent registered under *agent_id*."""
        agent = self._id_to_agent.get(agent_id)
        event_id = tracing._event_emitted(event, target=str(agent_id))
        if agent is None:
            return
        with tracing._trigger(
            "agent_event",
            "event",
            agent=agent.aid,
            clock=agent.scheduler.clock,
            cause=event_id,
            content=event,
        ):
            for _cond, _handler in agent._behavior_agent_event_handlers:
                if _cond(event):
                    tracing._handler_called("event", _handler)
                    _handler(agent, event)
            agent.on_agent_event(event)
            if hasattr(agent, "roles"):
                for role in agent.roles:
                    for _cond, _handler in role._behavior_agent_event_handlers:
                        if _cond(event):
                            tracing._handler_called("event", _handler)
                            _handler(role, event)
                    role.on_agent_event(event)


class _NoBehavior(Behavior):
    """Null behavior: does nothing."""

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

if TYPE_CHECKING:
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
    [0, width] × [0, height] during :meth:`initialize`.

    Example::

        space = Area2D(width=100.0, height=100.0)
    """

    def __init__(self, width: float = 10.0, height: float = 10.0):
        self.width = width
        self.height = height
        self._positions: dict[str, Position2D] = {}

    def move(self, agent, position: Position2D) -> None:
        self._positions[agent.aid] = position

    def location(self, agent) -> Position2D:
        return self._positions[agent.aid]

    def has_position(self, agent) -> bool:
        return agent.aid in self._positions

    def initialize(self, agents: list, clock: Clock) -> None:
        for agent in agents:
            if agent.aid not in self._positions:
                self._positions[agent.aid] = Position2D(
                    x=random.random() * self.width,
                    y=random.random() * self.height,
                )

    def install(self, agent, **kwargs) -> None:
        pass

    def distance(self, agent_a, agent_b) -> float:
        """Return the Euclidean distance between two agents.

        Both agents must have a registered position.

        :param agent_a: first agent
        :param agent_b: second agent
        :return: Euclidean distance in space units

        Example::

            d = space.distance(agent1, agent2)
        """
        return distance(self.location(agent_a), self.location(agent_b))

    def agents_within(self, center, radius: float, agents: list) -> list:
        """Return all agents from *agents* within *radius* of *center*.

        *center* itself is excluded from the result.  Only agents that have
        a registered position are considered.

        :param center: the reference agent
        :param radius: search radius in space units
        :param agents: candidate agents to search among
        :return: list of agents within *radius* of *center*

        Example::

            nearby = space.agents_within(my_agent, 5.0, world._agents.values())
        """
        result = []
        for agent in agents:
            if agent is center:
                continue
            if self.has_position(agent) and self.distance(center, agent) <= radius:
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
        pt = self.location(target) if hasattr(target, "aid") else target
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
    """Abstract environment interface."""

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
        if agent is None:
            return
        for _cond, _handler in agent._behavior_agent_event_handlers:
            if _cond(event):
                _handler(agent, event)
        agent.on_agent_event(event)
        if hasattr(agent, "roles"):
            for role in agent.roles:
                for _cond, _handler in role._behavior_agent_event_handlers:
                    if _cond(event):
                        _handler(role, event)
                role.on_agent_event(event)


class _NoBehavior(Behavior):
    """Null behavior – does nothing."""

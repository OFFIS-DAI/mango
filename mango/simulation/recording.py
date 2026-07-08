"""
Recording utilities for :class:`~mango.simulation.world.SimulationWorld`.

Recorders register a collector on the world that is invoked after every
simulation step; the collected values are stored in
:class:`WorldRecording` / :class:`AgentsRecording` instances accessible
via ``world.data_collections`` and ``world.data_agent_collections``.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..agent.core import Agent

if TYPE_CHECKING:
    from .world import SimulationWorld


@dataclass
class WorldRecording:
    """Time-series recording of world-level data."""

    timeseries: list[Any] = field(default_factory=list)
    time: list[float] = field(default_factory=list)


@dataclass
class AgentsRecording:
    """Per-agent time-series recording.

    ``timeseries`` maps each agent AID to a list of recorded values.
    ``agent_time`` maps each agent AID to the elapsed simulation seconds at
    which each of that agent's values was recorded; it stays aligned with
    ``timeseries`` even when agents are recorded sparsely (e.g. registered
    mid-simulation or gated by a filter). ``time`` holds every step's
    timestamp as a shared axis for agents recorded on every step.
    """

    timeseries: dict[str, list[Any]] = field(default_factory=dict)
    agent_time: dict[str, list[float]] = field(default_factory=dict)
    time: list[float] = field(default_factory=list)


def collect_data(
    world: "SimulationWorld",
    key: str,
    collector: Callable[["SimulationWorld", WorldRecording], None],
) -> None:
    """Register a world-level data collector.

    *collector* is called after every simulation step with the world and the
    :class:`WorldRecording` identified by *key*.

    Example::

        collect_data(world, "total_msgs", lambda w, rec: (
            rec.timeseries.append(len(w.recorded_messages)),
            rec.time.append(w.clock.time),
        ))
    """
    recording = world.new_world_recording(key)

    def _run(w: "SimulationWorld") -> None:
        collector(w, recording)

    world.add_data_collector(_run)


def collect_agent_data(
    world: "SimulationWorld",
    key: str,
    collector: Callable[["SimulationWorld", Agent, AgentsRecording], None],
) -> None:
    """Register an agent-level data collector.

    *collector* is called for every agent after every simulation step with
    the world, the agent, and the :class:`AgentsRecording` for *key*.  A
    shared ``time`` entry is appended once per step.

    Example::

        collect_agent_data(world, "state", lambda w, a, rec: (
            rec.timeseries.setdefault(a.aid, []).append(a.some_state),
        ))
    """
    recording = world.new_agents_recording(key)

    def _run(w: "SimulationWorld") -> None:
        for agent in w.agents.values():
            collector(w, agent, recording)
        recording.time.append(w.clock.time)

    world.add_data_collector(_run)


def record_world(
    world: "SimulationWorld",
    key: str,
    recorder: Callable[[], Any],
) -> None:
    """Record a world-level scalar after every step.

    *recorder* is a zero-argument callable whose return value is appended to
    the recording's ``timeseries``.

    Example::

        record_world(world, "agent_count", lambda: len(world.agents))
    """
    recording = world.new_world_recording(key)

    def _run(w: "SimulationWorld") -> None:
        recording.timeseries.append(recorder())
        recording.time.append(w.clock.time)

    world.add_data_collector(_run)


def record_agent(
    world: "SimulationWorld",
    key: str,
    recorder: Callable[[Agent], Any],
    filter_fn: Callable[[Agent], bool] | None = None,
) -> None:
    """Record a per-agent scalar after every step.

    *recorder* receives each agent and returns the value to store.  An
    optional *filter_fn* restricts recording to a subset of agents — pass
    an ``isinstance``-based predicate to record only agents of a particular
    type::

        record_agent(world, "soc", lambda a: a.soc_kwh,
                     filter_fn=lambda a: isinstance(a, EVAgent))

    :param world: the simulation world
    :param key: recording key
    :param recorder: ``(agent) -> value`` callable
    :param filter_fn: optional ``(agent) -> bool`` predicate; ``None`` records
        all registered agents
    """
    recording = world.new_agents_recording(key)

    def _run(w: "SimulationWorld") -> None:
        for agent in w.agents.values():
            if filter_fn is None or filter_fn(agent):
                recording.timeseries.setdefault(agent.aid, []).append(recorder(agent))
                recording.agent_time.setdefault(agent.aid, []).append(w.clock.time)
        recording.time.append(w.clock.time)

    world.add_data_collector(_run)


def record_agent_having(
    world: "SimulationWorld",
    key: str,
    role_type: type,
    recorder: Callable[[Agent], Any],
) -> None:
    """Record a per-agent scalar for agents that carry a specific role type.

    Only agents that have at least one role that is an instance of
    *role_type* are included in the recording.  *recorder* receives the
    agent and returns the value to store.

    :param world: the simulation world
    :param key: recording key
    :param role_type: only record agents that have a role of this type
    :param recorder: ``(agent) -> value`` callable

    Example::

        record_agent_having(world, "energy", EnergyRole, lambda a: a.roles[0].energy)
    """
    recording = world.new_agents_recording(key)

    def _run(w: "SimulationWorld") -> None:
        for agent in w.agents.values():
            if hasattr(agent, "roles") and any(
                isinstance(r, role_type) for r in agent.roles
            ):
                recording.timeseries.setdefault(agent.aid, []).append(recorder(agent))
                recording.agent_time.setdefault(agent.aid, []).append(w.clock.time)
        recording.time.append(w.clock.time)

    world.add_data_collector(_run)


def record_position(
    world: "SimulationWorld",
    key: str = "positions",
    filter_fn: Callable[[Agent], bool] | None = None,
) -> None:
    """Record the spatial position of every agent after each step.

    Only agents that have a position in the world's space are recorded.
    An optional *filter_fn* restricts recording to a subset of agents.

    :param world: the simulation world
    :param key: recording key (default ``"positions"``)
    :param filter_fn: ``(agent) -> bool`` predicate; ``None`` means all agents

    Example::

        record_position(world)
        history = position_history(world)
        # history.timeseries["agent0"]  -> list of Position2D
    """
    recording = world.new_agents_recording(key)

    def _run(w: "SimulationWorld") -> None:
        space = w.environment.space
        for agent in w.agents.values():
            if space.has_position(agent):
                if filter_fn is None or filter_fn(agent):
                    recording.timeseries.setdefault(agent.aid, []).append(
                        space.location(agent)
                    )
                    recording.agent_time.setdefault(agent.aid, []).append(w.clock.time)
        recording.time.append(w.clock.time)

    world.add_data_collector(_run)


def position_history(
    world: "SimulationWorld",
    key: str = "positions",
) -> AgentsRecording:
    """Return the :class:`AgentsRecording` populated by :func:`record_position`.

    :param world: the simulation world
    :param key: recording key (default ``"positions"``)
    :return: the recording
    """
    return world.data_agent_collections.get(key, AgentsRecording())

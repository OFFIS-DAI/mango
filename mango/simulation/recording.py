"""
Recording utilities for :class:`~mango.simulation.world.SimulationWorld`.

Recorders register a collector on the world that is invoked after every
simulation step; the collected values are stored in
:class:`WorldRecording` / :class:`AgentsRecording` instances accessible
via ``world.data_collections`` and ``world.data_agent_collections``.
:func:`recordings_to_records` and :func:`recordings_to_frame` flatten all
recordings into one tidy (long) table with the columns
:data:`~mango.simulation.recording.TIDY_COLUMNS`.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..agent.core import Agent

if TYPE_CHECKING:
    import pandas

    from .world import SimulationWorld

#: Column order of the tidy export (``datetime`` only with a calendar clock).
TIDY_COLUMNS: tuple[str, ...] = (
    "time",
    "datetime",
    "group",
    "entity",
    "variable",
    "value",
)

#: Entity (and group) name of world-level rows in the tidy export.
WORLD_ENTITY = "world"


@dataclass
class WorldRecording:
    """Time-series recording of world-level data.

    ``entity`` names the owner of the values in the tidy export
    (:func:`recordings_to_records`); ``None`` means ``"world"``.
    ``variable`` is the tidy ``variable`` of the rows; ``None`` means the
    recording key (a distinct key lets two entities record one variable).
    """

    timeseries: list[Any] = field(default_factory=list)
    time: list[float] = field(default_factory=list)
    entity: str | None = None
    variable: str | None = None


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
    *,
    entity: str | None = None,
) -> None:
    """Register a world-level data collector.

    *collector* is called after every simulation step with the world and the
    :class:`WorldRecording` identified by *key*.

    :param world: the simulation world
    :param key: recording key
    :param collector: ``(world, recording) -> None`` callable
    :param entity: owner name of the values in the tidy export (default
        ``"world"``)

    Example::

        collect_data(world, "total_msgs", lambda w, rec: (
            rec.timeseries.append(len(w.recorded_messages)),
            rec.time.append(w.clock.time),
        ))
    """
    recording = world.new_world_recording(key, entity=entity)

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
    *,
    entity: str | None = None,
) -> None:
    """Record a world-level scalar after every step.

    *recorder* is a zero-argument callable whose return value is appended to
    the recording's ``timeseries``.

    :param world: the simulation world
    :param key: recording key
    :param recorder: zero-argument callable returning the value
    :param entity: owner name of the values in the tidy export, e.g.
        ``"grid"`` (default ``"world"``)

    Example::

        record_world(world, "agent_count", lambda: len(world.agents))
        record_world(world, "load_kw", lambda: grid.load_kw, entity="grid")
    """
    recording = world.new_world_recording(key, entity=entity)

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
    optional *filter_fn* restricts recording to a subset of agents; pass
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


def _select(collections: Mapping[str, Any], keys: set[str] | None) -> list:
    return [(k, rec) for k, rec in collections.items() if keys is None or k in keys]


def recordings_to_records(
    world: "SimulationWorld",
    *,
    groups: Mapping[str, str] | None = None,
    keys: Iterable[str] | None = None,
    include_world: bool = True,
    include_agents: bool = True,
) -> list[dict]:
    """Flatten the world's recordings into tidy (long-format) rows.

    Every recorded value becomes one ``dict`` with the keys of
    :data:`~mango.simulation.recording.TIDY_COLUMNS`: ``time`` (clock
    seconds), ``datetime`` (only when the world clock has a calendar),
    ``group``, ``entity``, ``variable`` (the recording key) and ``value``.

    - Agent rows: ``entity`` is the aid; ``group`` is ``groups[aid]`` when
      given, else the agent's ``description.category`` while the agent is
      still registered, else ``None``.
    - World rows: ``group`` and ``entity`` are the recording's ``entity``
      (default ``"world"``).

    Rows are ordered deterministically: world recordings in key-insertion
    order, then agent recordings in key-insertion order, aids in insertion
    order, times as recorded.

    :param world: the simulation world
    :param groups: optional ``aid -> group`` mapping
    :param keys: restrict the export to these recording keys
    :param include_world: include world-level recordings
    :param include_agents: include agent-level recordings
    :return: list of row dicts
    :raises KeyError: if *keys* names an unknown recording
    :raises ValueError: if an agent's values cannot be aligned with times

    Example::

        record_agent(world, "soc", lambda a: a.soc)
        ...
        rows = recordings_to_records(world, groups={"ev_0": "ev"})
        # [{"time": 0.0, "group": "ev", "entity": "ev_0", "variable": "soc", "value": 0.5}, ...]
    """
    key_set = None if keys is None else set(keys)
    if key_set is not None:
        known = set(world.data_collections) | set(world.data_agent_collections)
        unknown = sorted(key_set - known)
        if unknown:
            raise KeyError(
                f"Unknown recording key(s) {unknown}; recorded keys are {sorted(known)}."
            )
    clock = world.clock
    to_datetime = clock.to_datetime if getattr(clock, "has_calendar", False) else None
    groups = groups or {}
    rows: list[dict] = []

    def _row(t: float, group: Any, entity: Any, variable: str, value: Any) -> dict:
        row: dict[str, Any] = {"time": t}
        if to_datetime is not None:
            row["datetime"] = to_datetime(t)
        row["group"] = group
        row["entity"] = entity
        row["variable"] = variable
        row["value"] = value
        return row

    if include_world:
        for key, recording in _select(world.data_collections, key_set):
            if len(recording.time) != len(recording.timeseries):
                raise ValueError(
                    f"World recording '{key}' has {len(recording.timeseries)} values "
                    f"but {len(recording.time)} times; append one time per value."
                )
            owner = recording.entity or WORLD_ENTITY
            variable = recording.variable or key
            for t, value in zip(recording.time, recording.timeseries):
                rows.append(_row(t, owner, owner, variable, value))

    if include_agents:
        agents = world.agents
        for key, recording in _select(world.data_agent_collections, key_set):
            for aid, values in recording.timeseries.items():
                times = recording.agent_time.get(aid)
                if times is None:
                    if len(values) != len(recording.time):
                        raise ValueError(
                            f"Cannot align recording '{key}' of agent '{aid}': "
                            f"{len(values)} values but no per-agent times and "
                            f"{len(recording.time)} shared times. Append to "
                            f"recording.agent_time[aid] alongside each value."
                        )
                    times = recording.time
                elif len(times) != len(values):
                    raise ValueError(
                        f"Recording '{key}' of agent '{aid}' has {len(values)} "
                        f"values but {len(times)} times."
                    )
                if aid in groups:
                    group = groups[aid]
                elif aid in agents:
                    group = agents[aid].description.category
                else:
                    group = None
                for t, value in zip(times, values):
                    rows.append(_row(t, group, aid, key, value))

    return rows


def recordings_to_frame(
    world: "SimulationWorld",
    *,
    groups: Mapping[str, str] | None = None,
    keys: Iterable[str] | None = None,
    include_world: bool = True,
    include_agents: bool = True,
) -> "pandas.DataFrame":
    """Return the world's recordings as a tidy ``pandas.DataFrame``.

    Same rows and keyword arguments as :func:`recordings_to_records`; the
    columns follow :data:`~mango.simulation.recording.TIDY_COLUMNS` (without
    ``datetime`` when the world clock has no calendar).

    :param world: the simulation world
    :param groups: see :func:`recordings_to_records`
    :param keys: see :func:`recordings_to_records`
    :param include_world: see :func:`recordings_to_records`
    :param include_agents: see :func:`recordings_to_records`
    :return: the tidy data frame

    Example::

        df = recordings_to_frame(world)
        # one variable per table: entities as columns, one row per time
        soc = df[df.variable == "soc"].pivot(
            index="time", columns="entity", values="value"
        )
        # or every (entity, variable) pair as its own column
        wide = df.pivot_table(
            index="time", columns=["entity", "variable"], values="value"
        )
    """
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            "recordings_to_frame requires pandas; install it with 'pip install pandas'."
        ) from exc

    columns = list(TIDY_COLUMNS)
    if not getattr(world.clock, "has_calendar", False):
        columns.remove("datetime")
    rows = recordings_to_records(
        world,
        groups=groups,
        keys=keys,
        include_world=include_world,
        include_agents=include_agents,
    )
    return pd.DataFrame(rows, columns=columns)

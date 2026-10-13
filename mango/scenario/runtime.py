"""
Runtime of a scenario: build the world in a fixed order and step it.

:class:`ScenarioRun` is what :meth:`Scenario.build
<mango.scenario.Scenario.build>` returns.  Entering it (``async with run:``)
builds and starts the world; :meth:`~ScenarioRun.step`,
:meth:`~ScenarioRun.run_until` and :meth:`~ScenarioRun.run` advance it, and
:meth:`~ScenarioRun.result` collects a :class:`~mango.scenario.RunResult`.

Build order inside ``async with run:``:

1. params, root :class:`~mango.util.seeding.SeedTree` and time are resolved
   (already in ``scenario.build``);
2. the declaration stage (``build`` → ``wire`` → ``record`` passes);
3. communication simulation for the comm settings at t=0 (custom factory or
   ``build_communication(ctx.comm, seeds.rng("comm"))``) and environment (factory, which must install the
   behaviors and space kits added, or ``DefaultEnvironment(space=ctx.space,
   behavior=ctx.behavior)``);
4. ``create_world(seed=<the root SeedTree>, start_datetime=time.start, ...)``,
   so the world shares the run's random streams;
5. groups in declaration order, per entity install → factory → register;
6. explicit positions;
7. link topologies;
8. recorders;
9. ``on_build`` hooks;
10. the world starts (``on_ready``, environment initialisation, samples at t=0).
11. events at t=0 are applied (emits, actions).

Phases and events (:mod:`mango.scenario.events`) are applied while the run
steps: the communication simulation follows the effective comm settings (at
the moment the clock reaches the boundary, keeping the ``"comm"`` stream; see
:meth:`ScenarioRun._sync_comm` for when an instance is reused), recorders
pause for samples of phases with ``record: false``, and emits and actions
run before the first step that starts at or after their time.

Timing within one step from ``t`` to ``t + dt`` (fixed step, or the step to
the next message, task or boundary in event mode):

1. events and phases due at ``t`` are applied (emits, actions; the
   communication settings switch when the clock reaches a boundary);
2. every agent's ``on_step`` (roles included) runs with the clock at ``t``,
   in registration order (group declaration order, then index);
3. the environment steps (behaviors in declaration order), clock still ``t``;
4. the clock moves to ``t + dt``, waking due tasks;
5. messages due at or before ``t + dt`` are delivered, repeatedly until none
   is due: a message is stamped with the clock when its send task runs, which
   for sends from ``on_step`` is already ``t + dt``; so with delay 0 it is
   handled in this step (and so are replies with delay 0), with a delay
   ``0 < d <= dt`` in the next step (for an L-step lead time use
   ``delay = (L - 1) * dt``);
6. the recorders sample at ``t + dt``; stop conditions are checked.

So the row at ``t + dt`` is the state after the step ``[t, t + dt)``; its
``phase`` is the phase active during that step, and ``record: false``
applies to it by that phase.  The row at ``t = 0`` is the initial state.

Example::

    run = scenario.build("presets/base.yaml", seed=7)
    async with run:
        await run.step()
        await run.run_until("12h")
        await run.run()
    result = run.result()
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import math
import time as _time
from array import array as _array
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from mango.agent.core import Agent
from mango.express.topology import auto_assign, graph_topology
from mango.simulation import (
    CommunicationSimulation,
    DefaultEnvironment,
    Environment,
    NoSpace,
    create_world,
)
from mango.util.seeding import SeedTree, name_key

from ._util import (
    add_note,
    call_flexible,
    did_you_mean,
    to_plain,
    utc_now,
)
from .comm import EdgeRule, LayeredCommunication, partition_blocks
from .components import check_environment, kit_params_hint
from .context import RESERVED_ATTRS, BuildContext, EntityContext, RunState
from .distributions import reporting_row_reuse
from .errors import ScenarioError
from .events import emitted_event, selector_matches
from .params import build_communication, dump_params
from .reducers import Window, group_reduce
from .result import (
    LOG_COLUMNS,
    MAIN_PHASE,
    RunResult,
    assemble_frame,
    compute_run_id,
    log_frame,
    recording_series,
)
from .specs import Entity, RecordSpec, compile_link, link_pairs, links_by_name
from .timecfg import _EPS, _tolerance

if TYPE_CHECKING:
    from pydantic import BaseModel

    from mango.simulation.world import SimulationResult, SimulationWorld

    from .builder import Scenario
    from .timecfg import TimeConfig

__all__ = ["ScenarioRun", "run_sync"]

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _append_sample(
    timeseries: dict, agent_time: dict, name: str, value: Any, t: float
) -> None:
    """Append one recorded value of entity *name* at offset *t*.

    Numbers go to an ``array("d")`` per entity (8 bytes per value instead of
    a list entry and a float object, 32); the first other value turns the
    entity's series into a list.  Ints and floats are stored as the floats
    the tidy table holds anyway (:func:`~mango.scenario.result.tidy_value`).
    """
    series = timeseries.get(name)
    if series is None:
        number = type(value) is float or type(value) is int
        series = timeseries[name] = _array("d") if number else []
        agent_time.setdefault(name, [])
    if type(series) is list:
        series.append(value)
    elif type(value) is float or type(value) is int:
        try:
            series.append(value)
        except OverflowError:  # an int beyond the float range
            timeseries[name] = [*series, value]
    else:
        timeseries[name] = [*series, value]
    agent_time[name].append(t)


def _on_grid(k: int, every: float) -> float:
    """Grid point *k* of period *every*, without float noise (``41 * 0.1`` is 4.1).

    Rounded to nanoseconds, so a run with ``every=0.1`` stops and records at
    4.1, not 4.1000000000000005.
    """
    return round(k * every, 9)


def _delay_draw(distribution: Any) -> Callable[[Any], float]:
    def draw(generator: Any) -> float:
        return max(0.0, float(distribution.draw(generator)))

    return draw


def _handles_event(agent: Agent, hook: str) -> bool:
    """Whether *agent* or one of its roles reacts to events of *hook*.

    *hook* is ``"on_agent_event"`` or ``"on_global_event"``: an override of
    the method or a behavior handler registered for it counts.
    """
    from mango.agent.role import Role

    handlers = (
        "_behavior_agent_event_handlers"
        if hook == "on_agent_event"
        else "_behavior_global_event_handlers"
    )
    if getattr(type(agent), hook, None) is not getattr(Agent, hook) or getattr(
        agent, handlers, None
    ):
        return True
    return any(
        getattr(type(role), hook, None) is not getattr(Role, hook)
        or getattr(role, handlers, None)
        for role in getattr(agent, "roles", ()) or ()
    )


def run_sync(coroutine_factory: Callable[[], Awaitable[T]]) -> T:
    """Run ``coroutine_factory()`` to completion from synchronous code.

    Uses :func:`asyncio.run`; when an event loop is already running in this
    thread (e.g. in Jupyter), the coroutine runs in a helper thread with its
    own loop and this call blocks until it is done.

    :param coroutine_factory: zero-argument function returning the coroutine
    :return: the coroutine's result (exceptions propagate)

    Example::

        result = run_sync(lambda: scenario.run_async(seed=7))
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        in_loop = False
    else:
        in_loop = True
    if not in_loop:
        # outside the except block, so run errors are not chained to the
        # "no running event loop" probe
        return asyncio.run(coroutine_factory())
    with concurrent.futures.ThreadPoolExecutor(
        1, thread_name_prefix="mango-scenario"
    ) as pool:
        return pool.submit(lambda: asyncio.run(coroutine_factory())).result()


def _uninstall(environment: Any, agent: Agent) -> None:
    """Remove a retired agent from the environment and its space (best effort).

    Environments may define ``uninstall(agent)``; for the default
    environment the agent is dropped from ``environment.agents`` and from the
    space's index and positions, so behaviors and neighbour queries no longer
    see it.
    """
    uninstall = getattr(environment, "uninstall", None)
    if callable(uninstall):
        uninstall(agent)
        return
    index = getattr(environment, "_id_to_agent", None)
    if isinstance(index, dict):
        for key in [key for key, value in index.items() if value is agent]:
            del index[key]
    space = getattr(environment, "space", None)
    space_uninstall = getattr(space, "uninstall", None)
    if callable(space_uninstall):
        space_uninstall(agent)
        return
    for attribute in ("_positions", "_agents"):
        mapping = getattr(space, attribute, None)
        if isinstance(mapping, dict):
            mapping.pop(agent.aid, None)


class _SampleClock:
    """Interval lengths between the calls of one recorder (weights of window means)."""

    __slots__ = ("last",)

    def __init__(self):
        self.last: float | None = None

    def tick(self, t: float) -> float:
        weight = 0.0 if self.last is None else t - self.last
        self.last = t
        return weight


class _RecorderPart:
    """One group of a per-agent recorder, with its window and aggregation state."""

    __slots__ = ("spec", "members", "window", "labels", "emitted")

    def __init__(self, spec: RecordSpec, members: list[Entity], window: Any):
        self.spec = spec
        #: the group's live member list (ctx.spawn/retire change it)
        self.members = members
        self.window = window
        #: categories of aggregate="count": the declared ones, then in order of appearance
        self.labels: dict[str, None] = dict.fromkeys(spec.categories or ())
        #: times written so far (zero rows for categories that appear later)
        self.emitted: list[float] = []


class ScenarioRun:
    """One run of a scenario: an async context manager around a fresh world.

    Created by :meth:`Scenario.build <mango.scenario.Scenario.build>`.  A run
    is used once.

    Attributes: ``scenario``, ``params``, ``time`` (:class:`TimeConfig`),
    ``seeds`` (root :class:`~mango.util.seeding.SeedTree`), ``seed`` (seed
    dict), ``run_id``; after entering also ``world``, ``ctx`` (the root
    :class:`~mango.scenario.BuildContext`) and ``entities``.

    ``record_messages`` (default ``False``): whether the world keeps every
    delivered message with its content in ``world.recorded_messages``. A
    run only counts them (``stats["messages_delivered"]``,
    ``world.messages_delivered``), so memory does not grow with the number
    of messages; pass ``record_messages=True`` to ``scenario.build`` (or
    ``scenario.run``), or set the attribute before entering the run, to keep
    them.

    Example::

        run = scenario.build(seed=7)
        async with run:
            while await run.step() is not None:
                print(run.elapsed_s, run.ctx.agents("ev")[0].soc_kwh)
        result = run.result()

        # keep the delivered messages, e.g. for show_communication_data
        run = scenario.build(seed=7, record_messages=True)
        async with run:
            await run.run()
            print(len(run.world.recorded_messages))
    """

    def __init__(
        self,
        scenario: Scenario,
        params: BaseModel,
        seeds: SeedTree,
        *,
        trace: Any = None,
        record_messages: bool = False,
    ):
        self.scenario = scenario
        self.params = params
        self.seeds = seeds
        self.trace = trace
        self.record_messages = record_messages
        self._state: RunState | None = None
        self._status = "created"
        self._steps = 0
        self._messages = 0
        self._wall_start: float | None = None
        self._wall_s: float | None = None
        self._cpu_start: float | None = None
        self._cpu_s: float | None = None
        self._started: str | None = None
        self._finished: str | None = None
        self._link_edges: dict[str, int] = {}
        self._result: RunResult | None = None
        #: ``[(start_seconds, phase_name), ...]``; phases append transitions here.
        self._phase_log: list[tuple[float, str]] = [(0.0, MAIN_PHASE)]
        #: Sorted offsets an event-driven run never steps across (horizon;
        #: phases and events add theirs).
        self._boundaries: list[float] = [self.time.horizon_s]
        #: ``every`` periods of recorders (event-driven runs stop at their multiples)
        self._sample_periods: list[float] = []
        #: phases and events of the run (``events.Schedule``), set when entered
        self._schedule: Any = None
        #: event firings (starts and ends of emit windows) in the order applied
        self._firings: list = []
        #: number of firings already applied
        self._fired = 0
        #: key of the comm config the current communication simulation was built from
        self._comm_key: str | None = None
        #: comm key -> the simulation built for it (reused when the settings return)
        self._comm_cache: dict[str, CommunicationSimulation] = {}
        #: the current simulation before partitions and link settings are layered on
        self._comm_inner: CommunicationSimulation | None = None
        #: link name -> (topology, entity name -> node id)
        self._topologies: dict[str, tuple[Any, dict[str, int]]] = {}
        #: (sender, receiver) -> EdgeRule of links with delay/loss (shared with
        #: the LayeredCommunication)
        self._edge_rules: dict[tuple[str, str], EdgeRule] = {}
        #: link name -> its LinkComm (links with delay/loss only)
        self._link_comm: dict[str, Any] = {}
        #: why the run stopped before the horizon (``None`` while it runs on)
        self._stop_reason: str | None = None
        #: entities added with ctx.spawn() / removed with ctx.retire()
        self._spawned = 0
        self._retired = 0
        #: shutdown tasks of retired agents (awaited after the step)
        self._retiring: list[asyncio.Task] = []
        #: link name -> number of recomputations with ctx.relink(name)
        self._recomputed: dict[str, int] = {}
        #: event logs: name -> [(time, phase, fields), ...] (ctx.log)
        self._logs: dict[str, list[tuple[float, str, dict]]] = {}
        #: run-level metrics set with ctx.set_metric
        self._metrics: dict[str, Any] = {}
        #: events whose receivers were checked for the delivery hook
        self._hook_checked: set[str] = set()
        #: rows of aggregated recorders: (variable, group, entity) -> (times, values)
        self._series: dict[tuple[str, str, str], tuple[list, list]] = {}
        phases = params.phases
        if phases:
            self._phase_log = [(0.0, phases[0].name)]

    def __repr__(self) -> str:
        return (
            f"<ScenarioRun {self.scenario.name!r} run_id={self.run_id!r} "
            f"status={self._status}>"
        )

    # -- identity --------------------------------------------------------------------

    @property
    def time(self) -> TimeConfig:
        """The run's :class:`~mango.scenario.TimeConfig`."""
        return self.params.time

    @property
    def seed(self) -> dict:
        """The seed as plain data (``scenario.build(params, seed=run.seed)`` replays it)."""
        return self.seeds.to_dict()

    @property
    def scenario_ref(self) -> str:
        """The scenario reference used in the run id (``"module:attr"`` or the name)."""
        return self.scenario.ref or self.scenario.name

    @property
    def run_id(self) -> str:
        """Content hash of scenario reference, resolved params and seed."""
        return compute_run_id(self.scenario_ref, self.params, self.seed)

    # -- live state ------------------------------------------------------------------

    def _require(self, what: str) -> RunState:
        if self._status != "running":
            hint = {
                "created": "enter the run first: 'async with run:'",
                "closed": "the run is finished; call scenario.build(...) again",
            }.get(self._status, "the run failed to build")
            raise ScenarioError(f"{what} needs a running ScenarioRun; {hint}.")
        return self._state

    @property
    def world(self) -> SimulationWorld | None:
        """The world (``None`` before the run is entered)."""
        return None if self._state is None else self._state.world

    @property
    def ctx(self) -> BuildContext | None:
        """The root :class:`~mango.scenario.BuildContext` (``None`` before entering)."""
        return None if self._state is None else self._state.root

    @property
    def entities(self) -> dict[str, Entity]:
        """Built entities by name (empty before entering)."""
        return {} if self._state is None else dict(self._state.entities)

    @property
    def elapsed_s(self) -> float:
        """Seconds since the scenario start (the world clock)."""
        world = self.world
        return 0.0 if world is None else float(world.clock.time)

    @property
    def done(self) -> bool:
        """True once the horizon is reached or the run was stopped (:meth:`stop`)."""
        if self._stop_reason is not None:
            return True
        if self.time.is_event_driven:
            return self.elapsed_s >= self.time.horizon_s - _tolerance(
                self.time.horizon_s
            )
        return self._steps_done() >= self.time.n_steps

    @property
    def recording_enabled(self) -> bool:
        """Whether a sample at the current time is recorded.

        A sample at ``t > 0`` is the state after the step that ended at
        ``t``, so the phase active during that step decides (``record:
        false`` turns it off); the sample at ``t = 0`` follows the first
        phase.
        """
        schedule = self._schedule
        return schedule is None or schedule.sample_phase(self.elapsed_s).record

    @property
    def _holds_recorded(self) -> bool:
        """Whether a state sampled now is recorded: the phase starting at or before now decides.

        A state recorded at ``t`` holds on ``[t, t_next)``, so the phase
        active from ``t`` on decides (a row at the start of a recorded phase,
        none at the start of a ``record: false`` phase).
        """
        schedule = self._schedule
        return schedule is None or schedule.phase_at(self.elapsed_s).record

    @property
    def phase(self) -> str:
        """The phase active at the current time (``"main"`` without phases)."""
        schedule = self._schedule
        if schedule is None:
            return self._phase_log[0][1]
        return schedule.phase_at(self.elapsed_s).name

    @property
    def stats(self) -> dict:
        """Run statistics: steps, messages, wall and CPU time, agents per group, link edges.

        ``wall_s`` is wall-clock time (it grows with the load of the
        machine), ``cpu_s`` the CPU time of the thread that ran the
        simulation while the run was open (the figure for sizing studies;
        helper threads of numerical libraries, which may spin while idle,
        are not counted).

        ``recorders`` counts the declared recorders.  Also
        ``stopped_early``, ``stop_time_s`` and ``stop_reason`` (``None``
        unless :meth:`stop`, a ``stop_when`` condition or ``until`` ended
        the run before the horizon).  ``agents`` counts the entities built
        with the world; runs that called ``ctx.spawn``/``ctx.retire`` add
        ``spawned``, ``retired`` and ``agents_alive`` (per group).
        """
        state = self._state
        groups = (
            {}
            if state is None
            else {name: spec.count for name, spec in state.groups.items()}
        )
        wall = self._wall_s
        if wall is None and self._wall_start is not None:
            wall = _time.perf_counter() - self._wall_start
        cpu = self._cpu_s
        if cpu is None and self._cpu_start is not None:
            cpu = _time.thread_time() - self._cpu_start
        return {
            "steps": self._steps,
            "messages_delivered": self._messages,
            "sim_time_s": self.elapsed_s,
            "wall_s": wall,
            "cpu_s": cpu,
            "started": self._started,
            "finished": self._finished,
            "agents": sum(groups.values()),
            "agents_per_group": groups,
            "link_edges": dict(self._link_edges),
            "recorders": 0 if state is None else len(state.records),
            "stopped_early": self._stop_reason is not None,
            # a stopped run does not advance any more: its clock is the stop time
            "stop_time_s": None if self._stop_reason is None else self.elapsed_s,
            "stop_reason": self._stop_reason,
            **self._population_stats(),
        }

    def _population_stats(self) -> dict:
        state = self._state
        if state is None or not (self._spawned or self._retired):
            return {}
        return {
            "spawned": self._spawned,
            "retired": self._retired,
            "agents_alive": {
                name: len(members) for name, members in state.by_group.items()
            },
        }

    # -- stopping --------------------------------------------------------------------

    def stop(self, reason: str = "stopped") -> None:
        """End the run before the horizon, after the current step.

        Called inside a step (an agent, a behavior, a recorder), the step
        completes; called by an event action at ``t`` (applied before the
        step that starts at ``t``), the run ends at ``t``.  Afterwards
        :attr:`done` is true and :meth:`step` returns ``None``;
        :meth:`result` holds the rows up to now, and ``stats`` records
        ``stopped_early``, ``stop_time_s`` and *reason*.  Calling it again
        keeps the first reason.  Hooks and behaviors reach it as
        ``ctx.stop(reason)``; declare conditions with
        :meth:`Scenario.stop_when <mango.scenario.Scenario.stop_when>`.

        :param reason: why the run stopped (``stats["stop_reason"]``)

        Example::

            async with run:
                while await run.step() is not None:
                    if converged(run.ctx):
                        run.stop("converged")
            run.result().stats["stop_time_s"]
        """
        if not isinstance(reason, str) or not reason:
            raise ScenarioError(
                f"run.stop(reason) needs a non-empty str, got {reason!r}."
            )
        if self._stop_reason is None:
            self._stop_reason = reason
            logger.debug("Run stop requested at t=%g s: %s", self.elapsed_s, reason)

    def _check_stop_conditions(self) -> None:
        state = self._state
        if not state.stop_conditions or self._stop_reason is not None:
            return
        t = self.elapsed_s
        for condition in state.stop_conditions:
            every = condition.every
            if every is not None:
                multiple = round(t / every)
                if abs(t - multiple * every) > _tolerance(t):
                    continue
            try:
                value = call_flexible(condition.fn, condition.ctx)
            except Exception as exc:
                add_note(
                    exc, f"while checking stop_when {condition.label} at t={t:g} s"
                )
                raise
            if value:
                self.stop(
                    value if isinstance(value, str) else f"stop_when {condition.label}"
                )
                return

    # -- phase-3 extension points ----------------------------------------------------

    def _apply_boundaries(self, t: float) -> None:
        """Apply events due at offset *t* (before a step; idempotent per boundary).

        Called at t=0 before the world starts (communication only), right
        after it started and before every step.  Phases need nothing here:
        :attr:`phase` and :attr:`recording_enabled` look the phase up at the
        clock time, and the communication simulation also follows the clock
        inside a step (see :meth:`_follow_clock`).
        """
        self._sync_comm(t)
        if self._status == "running":
            self._fire_events(t)

    def _fire_events(self, t: float) -> None:
        """Emit and run the actions of the event firings due at *t* not applied yet."""
        firings = self._firings
        state = self._state
        while self._fired < len(firings) and firings[
            self._fired
        ].time <= t + _tolerance(t):
            firing = firings[self._fired]
            spec = firing.spec
            self._fired += 1
            world = state.world
            now = float(world.clock.time)
            what = spec.label + (" (end)" if firing.stage == "end" else "")
            try:
                if firing.emits:
                    moment = (
                        world.clock.datetime
                        if getattr(world.clock, "has_calendar", False)
                        else None
                    )
                    event = emitted_event(spec, now, moment, firing.stage)
                    targets = self._event_targets(spec)
                    self._check_event_hook(spec, targets)
                    if targets is None:
                        world.environment.emit_global_event(event)
                    else:
                        for target in targets:
                            world.environment.emit_agent_event(event, target)
                if firing.acts:
                    call_flexible(spec.action, state.root)
            except Exception as exc:
                add_note(exc, f"while applying {what} at t={now:g} s")
                raise
            logger.debug("Applied %s at t=%g s", what, now)

    def _event_targets(self, spec: Any) -> tuple[str, ...] | None:
        """The entities an emit goes to now (``None``: broadcast).

        Groups stand for their current members (spawned entities included,
        retired ones not); a selector ``{group, by_attr}`` is matched against
        the install attributes of the alive entities.
        """
        if spec.targets is None:
            return None
        state = self._state
        if spec.selector is None:
            groups = state.by_group
            names: list[str] = []
            raw = spec.emit_to
            for item in [raw] if isinstance(raw, str) else list(raw):
                entity = state.entities.get(item)
                if entity is not None:  # entity names first, as when planned
                    members = [item] if entity.alive else []
                else:
                    members = [member.name for member in groups[item]]
                names += [name for name in members if name not in names]
            return tuple(names)
        group = spec.selector.get("group")
        if group is None:
            candidates = [e for e in state.entities.values() if e.alive]
        else:
            candidates = [
                entity
                for name in ([group] if isinstance(group, str) else group)
                for entity in state.by_group[name]
            ]
        return tuple(
            entity.name
            for entity in candidates
            if selector_matches(spec.selector, entity.attrs)
        )

    def _check_event_hook(self, spec: Any, targets: tuple[str, ...] | None) -> None:
        """Warn once per event when no receiver implements the hook it is delivered to."""
        key = spec.label.split(" (occurrence ")[0]
        checked = self._hook_checked
        if key in checked:
            return
        checked.add(key)
        state = self._state
        if targets is None:
            observers = getattr(state.world.environment, "_observers", None)
            if not isinstance(observers, list) or any(
                type(observer).__name__ != "_AgentDispatchObserver"
                for observer in observers
            ):
                return  # other observers may consume broadcasts
            agents = list(state.world.agents.values())
            hook, other = "on_global_event", "on_agent_event"
            what = "all agents"
        else:
            if not targets:
                state.warn(
                    f"{key}: emit_to {spec.emit_to!r} selects no entity at "
                    f"t={self.elapsed_s:g} s, so the event reaches nobody.",
                    stacklevel=4,
                )
                return
            agents = [state.entities[name].agent for name in targets]
            hook, other = "on_agent_event", "on_global_event"
            what = f"emit_to={spec.emit_to!r}"
        if not agents or any(_handles_event(agent, hook) for agent in agents):
            return
        sample = type(agents[0]).__name__
        only_other = any(_handles_event(agent, other) for agent in agents)
        advice = (
            f"; {sample} implements only {other}"
            + (
                " (drop emit_to to broadcast, or implement on_agent_event)"
                if hook == "on_agent_event"
                else " (emit_to sends to on_agent_event)"
            )
            if only_other
            else f"; implement {hook}(self, event) in {sample} or one of its roles"
        )
        receivers = (
            f"its receiver {agents[0].aid!r} does not implement it"
            if len(agents) == 1
            else f"none of its {len(agents)} receivers implements it"
        )
        state.warn(
            f"{key} (to {what}) is delivered through {hook}, but {receivers}, "
            f"so it is ignored{advice}.",
            stacklevel=4,
        )

    def _sync_comm(self, t: float) -> None:
        """Follow the effective comm settings at *t* (phases and events).

        Only a change of the settings (``ctx.comm``) does anything:

        - a simulation with a method ``on_comm_change(comm)`` is kept and
          told the new settings (its state carries over);
        - otherwise the simulation for the new settings is built once (the
          factory is called again with ``ctx.comm`` set) and cached: when
          the settings return to earlier ones (the end of an outage), the
          instance built for them is reused, state included;
        - ``partition`` and link ``delay``/``loss`` are layered on top.
        """
        schedule = self._schedule
        if schedule is None or not schedule.has_comm_changes:
            return
        config, key = schedule.comm_at(t)
        if key == self._comm_key:
            return
        state = self._state
        state.comm = config
        inner = self._comm_inner
        notify = getattr(inner, "on_comm_change", None)
        if callable(notify):
            notify(config)
        else:
            inner = self._comm_cache.get(key)
            if inner is None:
                inner = self._make_communication(state)
                self._comm_cache[key] = inner
        self._comm_inner = inner
        state.world.communication_sim = inner  # binds the "comm" stream if unset
        layered = self._layered(inner, config)
        if layered is not inner:
            state.world.communication_sim = layered
        self._comm_key = key
        logger.debug("Communication settings at t=%g s: %s", t, config)

    def _layered(
        self, inner: CommunicationSimulation, config: Any
    ) -> CommunicationSimulation:
        """*inner* with the partition of *config* and the link settings on top."""
        state = self._state
        partition = getattr(config, "partition", None)
        if not partition and not self._link_comm:
            return inner
        block_of = partition_blocks(
            partition,
            state.entities,
            {
                name: [e.name for e in members]
                for name, members in state.by_group.items()
            },
            {name: entity.attrs for name, entity in state.entities.items()},
        )
        return LayeredCommunication(inner, block_of=block_of, edges=self._edge_rules)

    def _follow_clock(self, world: SimulationWorld) -> None:
        """Switch the communication simulation the moment the clock reaches a boundary.

        Messages are sent while a step settles, after the world moved the
        clock to the step's end; switching between steps would apply the
        previous settings to messages sent exactly at a boundary.
        """
        world.clock.add_time_listener(self._sync_comm)

    def _next_boundary(self, t: float) -> float:
        """The next offset after *t* an event-driven step must stop at.

        Boundaries (horizon; phases and events add theirs) and the sample
        times of recorders with ``every=``.
        """
        nearest = self.time.horizon_s
        for boundary in self._boundaries:
            if boundary > t + _tolerance(t):
                nearest = min(nearest, boundary)
                break
        for every in self._sample_periods:
            k = math.floor(t / every + _EPS) + 1
            nearest = min(nearest, _on_grid(k, every))
        return nearest

    # -- build -----------------------------------------------------------------------

    async def __aenter__(self) -> ScenarioRun:
        if self._status != "created":
            raise ScenarioError(
                "A ScenarioRun runs once; call scenario.build(...) for another run."
            )
        self._status = "building"
        self._wall_start = _time.perf_counter()
        self._cpu_start = _time.thread_time()
        self._started = utc_now()
        try:
            state = self.scenario._declaration_stage(self.params, self.seeds)
            state.run = self
            self._state = state
            self._adopt_schedule(state.schedule)
            world = self._create_world(state)
            state.world = world
            if self._schedule.has_comm_changes:
                self._follow_clock(world)
            with reporting_row_reuse(self._row_reuse_reporter(state)):
                positioned = self._instantiate(state)
                self._apply_positions(state, positioned)
                self._build_links(state)
                # partitions and link settings need the entities and links
                world.communication_sim = self._layered(self._comm_inner, state.comm)
                self._install_recorders(state)
                for hook, ctx in state.build_hooks:
                    hook(ctx)
            self._apply_boundaries(0.0)
        except BaseException:
            self._status = "failed"
            await self._shutdown_quietly()
            raise
        try:
            await world.__aenter__()
            self._status = "running"
            self._apply_boundaries(0.0)  # events at the start, after on_ready
        except BaseException:
            self._status = "failed"
            await self._shutdown_quietly()
            raise
        return self

    @staticmethod
    def _row_reuse_reporter(state: RunState) -> Callable[[tuple, str], None]:
        """Report FromColumn row reuse once per table and column of this run."""
        return lambda key, message: state.warn_once(("row_reuse", *key), message)

    def _adopt_schedule(self, schedule: Any) -> None:
        self._schedule = schedule
        self._firings = schedule.firings
        self._phase_log = schedule.phase_log
        horizon = self.time.horizon_s
        self._boundaries = sorted({*schedule.boundaries, horizon})
        if schedule.has_comm_changes:
            config, key = schedule.comm_at(0.0)
            self._state.comm = config
            self._comm_key = key

    async def _shutdown_quietly(self) -> None:
        """Shut a half-built world down (agent tasks, trace) without masking the error."""
        state = self._state
        world = None if state is None else state.world
        if world is None:
            return
        try:
            await world.shutdown()
        except Exception:
            logger.exception(
                "Shutting down the world of a failed %r run failed", self.scenario.name
            )

    async def __aexit__(self, *exc_info) -> None:
        state = self._state
        try:
            if self._retiring:
                await self._finish_retiring()
            if state is not None and self._status == "running":
                await state.world.__aexit__(*exc_info)
        finally:
            self._status = "closed"
            if self._wall_start is not None:
                self._wall_s = _time.perf_counter() - self._wall_start
            if self._cpu_start is not None:
                self._cpu_s = _time.thread_time() - self._cpu_start
            self._finished = utc_now()

    def _make_communication(self, state: RunState) -> CommunicationSimulation:
        """Build the communication simulation for ``ctx.comm`` (factory or params)."""
        ctx = state.root
        scenario = self.scenario
        if scenario._communication_factory is None:
            return build_communication(ctx.comm, self.seeds.rng("comm"), ctx)
        communication = scenario._communication_factory(ctx)
        if not isinstance(communication, CommunicationSimulation):
            raise TypeError(
                f"The communication factory of scenario {scenario.name!r} must "
                f"return a CommunicationSimulation, got {type(communication).__name__}."
            )
        return communication

    def _create_world(self, state: RunState) -> SimulationWorld:
        ctx = state.root
        scenario = self.scenario
        communication = self._make_communication(state)
        self._comm_inner = communication
        if self._comm_key is not None:
            self._comm_cache[self._comm_key] = communication
        behavior, space = ctx.behavior, ctx.space
        if scenario._environment_factory is not None:
            environment = scenario._environment_factory(ctx)
            if not isinstance(environment, Environment):
                raise TypeError(
                    f"The environment factory of scenario {scenario.name!r} must "
                    f"return a mango Environment, got {type(environment).__name__}."
                )
            check_environment(
                environment, behavior, space, state.behaviors, scenario.name
            )
        else:
            environment = DefaultEnvironment(space=space, behavior=behavior)
        return create_world(
            start_time=0.0,
            communication_sim=communication,
            environment=environment,
            trace=self.trace,
            seed=self.seeds,
            start_datetime=self.time.start,
            record_messages=self.record_messages,
        )

    def _instantiate(
        self, state: RunState
    ) -> list[tuple[EntityContext, Callable | None]]:
        positioned: list[tuple[EntityContext, Callable | None]] = []
        for spec in state.groups.values():
            state.by_group[spec.name] = []
            for index, name in enumerate(spec.names):
                e = self._build_entity(state, spec, index, name, spec.count)
                if spec.position is not None:
                    positioned.append((e, spec.position))
        return positioned

    def _build_entity(
        self,
        state: RunState,
        spec: Any,
        index: int,
        name: str,
        count: int,
        extra: dict | None = None,
    ) -> EntityContext:
        """Install, build, register and file one entity of *spec*; return its ``e``."""
        e = EntityContext(name, spec.name, index, count, spec.ctx)
        try:
            attrs = self._install_attrs(spec, e)
            if extra:
                attrs = {**attrs, **self._checked_attrs(spec, dict(extra))}
            e.attrs = attrs
            agent = spec.factory(e)
            if not isinstance(agent, Agent):
                raise TypeError(
                    f"The factory of group {spec.name!r} returned "
                    f"{type(agent).__name__} for entity {name!r}; it must return a "
                    "mango Agent."
                )
            # registering touches the agent's context: a missing super().__init__()
            # shows up here and gets the entity note too
            state.world.register(agent, suggested_aid=name, **attrs)
        except Exception as exc:
            add_note(exc, f"while building entity {name!r} of group {spec.name!r}")
            hint = kit_params_hint(exc, state.params, spec.ctx.prefix, "e")
            if hint:
                add_note(exc, hint)
            raise
        if agent.aid != name:
            raise ScenarioError(
                f"Entity {name!r} of group {spec.name!r} was registered as "
                f"{agent.aid!r}: mango did not accept the name as aid (already "
                "taken, e.g. by an agent registered in a factory?)."
            )
        if agent.description.category == "agent":
            agent.update_description(category=spec.name)
        e.agent = agent
        entity = Entity(name, spec.name, index, agent, attrs)
        state.by_group[spec.name].append(entity)
        state.entities[name] = entity
        return e

    # -- dynamic populations -------------------------------------------------------

    def _spawn(self, group: str, attrs: dict | None) -> Entity:
        """Add the next entity of *group* (see :meth:`BuildContext.spawn`)."""
        state = self._state
        if attrs is not None and not isinstance(attrs, dict):
            raise TypeError(
                f"ctx.spawn({group!r}) attrs must be a dict, got {type(attrs).__name__}."
            )
        spec = state.groups[group]
        index = len(spec.names)
        name = spec.ctx._spawn_name(spec, index)
        spec.names.append(name)
        state.entity_groups[name] = group
        try:
            with reporting_row_reuse(self._row_reuse_reporter(state)):
                e = self._build_entity(state, spec, index, name, index + 1, attrs)
        except BaseException:
            spec.names.pop()
            state.entity_groups.pop(name, None)
            raise
        if spec.position is not None:
            self._apply_positions(state, [(e, spec.position)])
        self._spawned += 1
        return state.entities[name]

    def _retire(self, item: Any) -> None:
        """Remove an entity from the run (see :meth:`BuildContext.retire`)."""
        state = self._state
        name = self._entity_name(item, "ctx.retire()")
        entity = state.entities[name]
        if not entity.alive:
            raise ScenarioError(f"ctx.retire(): entity {name!r} is already retired.")
        entity.alive = False
        members = state.by_group[entity.group]
        members[:] = [member for member in members if member is not entity]
        for link, (topology, nodes) in self._topologies.items():
            node = nodes.get(name)
            if node is None:
                continue
            graph = topology.graph
            if graph.is_directed():
                incident = [*graph.in_edges(node), *graph.out_edges(node)]
            else:
                incident = list(graph.edges(node))
            if incident:
                graph.remove_edges_from(incident)
                topology.inject()
                self._link_edges[link] = graph.number_of_edges()
                self._set_edge_rules(link)
        agent = entity.agent
        world = state.world
        world.deregister(agent.aid)
        _uninstall(world.environment, agent)
        self._retiring.append(asyncio.get_running_loop().create_task(agent.shutdown()))
        self._retired += 1

    async def _finish_retiring(self) -> None:
        """Await the shutdown of retired agents (``on_stop`` errors fail the run)."""
        tasks, self._retiring = self._retiring, []
        await asyncio.gather(*tasks)

    @classmethod
    def _install_attrs(cls, spec, e: EntityContext) -> dict:
        if spec.install is None:
            return {}
        attrs = spec.install(e)
        if attrs is None:
            return {}
        if not isinstance(attrs, dict):
            try:
                attrs = dict(attrs)
            except (TypeError, ValueError):
                raise TypeError(
                    f"The install function of group {spec.name!r} must return a dict "
                    f"of install attributes, got {type(attrs).__name__}."
                ) from None
        return cls._checked_attrs(spec, attrs)

    @staticmethod
    def _checked_attrs(spec, attrs: dict) -> dict:
        bad = [
            key for key in attrs if not isinstance(key, str) or key in RESERVED_ATTRS
        ]
        if bad:
            raise ScenarioError(
                f"Group {spec.name!r}: install attribute {bad[0]!r} is not allowed "
                f"(keys must be strings other than {sorted(RESERVED_ATTRS)})."
            )
        return attrs

    def _apply_positions(self, state: RunState, positioned) -> None:
        if not positioned:
            return
        space = getattr(state.world.environment, "space", None)
        if space is None or isinstance(space, NoSpace):
            groups = sorted({e.group for e, _ in positioned})
            raise ScenarioError(
                f"Group(s) {groups} declare position=..., but the environment has no "
                "space; set one, e.g. scenario.environment(lambda ctx: "
                "DefaultEnvironment(space=Area2D(10, 10)))."
            )
        for e, position in positioned:
            space.move(e.agent, position(e))

    def _build_links(self, state: RunState) -> None:
        for name, specs in links_by_name(state.links).items():
            graph, participants = compile_link(specs, state.by_group, self.seeds)
            topology = graph_topology(graph, tid=name)
            auto_assign(topology, [entity.agent for entity in participants])
            self._link_edges[name] = graph.number_of_edges()
            nodes = {entity.name: index for index, entity in enumerate(participants)}
            self._topologies[name] = (topology, nodes)
            comm = next((spec.comm for spec in specs if spec.comm is not None), None)
            if comm is not None and comm.active:
                self._link_comm[name] = comm
                self._set_edge_rules(name)

    def _set_edge_rules(self, name: str) -> None:
        """(Re)build the edge rules of link *name* from its current edges."""
        comm = self._link_comm.get(name)
        if comm is None:
            return
        rules = self._edge_rules
        for key in [key for key, rule in rules.items() if rule.link == name]:
            del rules[key]
        topology, nodes = self._topologies[name]
        graph = topology.graph
        names = {node: entity for entity, node in nodes.items()}
        directed = graph.is_directed()
        provider = None if isinstance(comm.delay, int | float) else comm.delay
        delay = float(comm.delay) if provider is None else 0.0
        for a, b in graph.edges:
            pairs = [(names[a], names[b])]
            if not directed:
                pairs.append((names[b], names[a]))
            for src, dst in pairs:
                if (src, dst) in rules:
                    continue  # the first link declaring settings for an edge wins
                rules[(src, dst)] = EdgeRule(
                    link=name,
                    loss=comm.loss,
                    delay=delay,
                    provider=None if provider is None else _delay_draw(provider),
                    rng=self.seeds.rng(
                        "comm-link", name_key(name), name_key(src), name_key(dst)
                    ),
                )

    def _entity_name(self, item: Any, what: str) -> str:
        state = self._state
        if isinstance(item, Entity):
            name = item.name
        elif isinstance(item, Agent):
            name = item.aid
        else:
            name = item
        if not isinstance(name, str) or name not in state.entities:
            hint = did_you_mean(str(name), list(state.entities))
            raise ScenarioError(f"{what}: {item!r} is not an entity of the run.{hint}")
        return name

    def _recomputed_pairs(self, name: str) -> list[tuple[str, str]]:
        """The declared patterns of link *name* applied to the current members."""
        state = self._state
        count = self._recomputed.get(name, 0) + 1
        self._recomputed[name] = count
        rng = self.seeds.rng("link", name_key(name), "relink", count)
        pairs = []
        for spec in links_by_name(state.links)[name]:
            src = state.by_group[spec.source]
            dst = src if spec.target == spec.source else state.by_group[spec.target]
            pairs += [(a.name, b.name) for a, b in link_pairs(spec, src, dst, rng)]
        return pairs

    def _relink(self, name: str, pairs: Any) -> int:
        """Replace the edges of link *name* (see :meth:`BuildContext.relink`)."""
        if name not in self._topologies:
            hint = did_you_mean(name, list(self._topologies))
            raise ScenarioError(
                f"ctx.relink({name!r}): no link of that name.{hint} Links: "
                f"{', '.join(self._topologies) or '(none)'}."
            )
        if pairs is None:
            pairs = self._recomputed_pairs(name)
        topology, nodes = self._topologies[name]
        state = self._state
        edges = []
        for pair in pairs:
            try:
                a, b = pair
            except (TypeError, ValueError):
                raise ScenarioError(
                    f"ctx.relink({name!r}): pairs must be (source, target) tuples, "
                    f"got {pair!r}."
                ) from None
            a = self._entity_name(a, f"ctx.relink({name!r})")
            b = self._entity_name(b, f"ctx.relink({name!r})")
            for end in (a, b):
                if not state.entities[end].alive:
                    raise ScenarioError(
                        f"ctx.relink({name!r}): entity {end!r} is retired."
                    )
            if a != b:
                edges.append((a, b))
        graph = topology.graph
        graph.remove_edges_from(list(graph.edges))
        for a, b in edges:
            for entity in (a, b):
                if entity not in nodes:
                    nodes[entity] = topology.add_node(state.entities[entity].agent)
            topology.add_edge(nodes[a], nodes[b])
        topology.inject()
        self._link_edges[name] = graph.number_of_edges()
        self._set_edge_rules(name)
        return graph.number_of_edges()

    def _link_pairs(self, name: str) -> list[tuple[str, str]]:
        if name not in self._topologies:
            hint = did_you_mean(name, list(self._topologies))
            raise ScenarioError(f"No link {name!r} in the run.{hint}")
        topology, nodes = self._topologies[name]
        names = {node: entity for entity, node in nodes.items()}
        return [(names[a], names[b]) for a, b in topology.graph.edges]

    def _install_recorders(self, state: RunState) -> None:
        world = state.world
        if self.time.is_event_driven:
            self._sample_periods = sorted(
                {spec.every for spec in state.records if spec.every is not None}
                | {c.every for c in state.stop_conditions if c.every is not None}
            )
        agent_records: dict[str, list[RecordSpec]] = {}
        for spec in state.records:
            if spec.kind == "world":
                self._install_world_recorder(world, spec)
            else:
                agent_records.setdefault(spec.name, []).append(spec)
        for name, specs in agent_records.items():
            self._install_agent_recorder(world, state, name, specs)

    def _install_world_recorder(self, world: SimulationWorld, spec: RecordSpec) -> None:
        recording = world.new_world_recording(
            spec.recording_key, entity=spec.entity, variable=spec.name
        )
        fn, ctx = spec.getter, spec.ctx
        what = f"record_world({spec.name!r})"
        state = spec.value_kind == "state"
        holds = spec.holds
        how = None if state and spec.window == "last" else spec.window  # a snapshot
        window = None if how is None else Window(how, what, hold=holds)
        clock = _SampleClock()

        def sample(t: float) -> Any:
            try:
                return call_flexible(fn, ctx)
            except Exception as exc:
                add_note(
                    exc,
                    f"while recording world variable {spec.name!r} (entity "
                    f"{spec.entity!r}) at t={t:g} s",
                )
                raise

        def collect(w: SimulationWorld) -> None:
            t = w.clock.time
            first = clock.last is None
            weight = clock.tick(t)
            due = spec.due(t)
            if holds:  # a state's window: weight each level with the time it held
                on = self._holds_recorded
                value = sample(t) if on else None
                window.add(weight, {None: value} if on else None)
                if not due:
                    return
                if window.empty:  # no recorded time yet: only the initial row
                    if not (first and on):
                        return
                else:
                    value = window.emit()[None]
                recording.timeseries.append(value)
                recording.time.append(t)
                return
            if not (self._holds_recorded if state else self.recording_enabled):
                return
            if window is None and not due:
                return
            value = sample(t)
            if window is not None:
                window.add(weight, {None: value})
                if not due:
                    return
                value = window.emit()[None]
            recording.timeseries.append(value)
            recording.time.append(t)

        world.add_data_collector(collect)

    def _install_agent_recorder(
        self,
        world: SimulationWorld,
        state: RunState,
        name: str,
        specs: list[RecordSpec],
    ) -> None:
        aggregate = specs[0].aggregate
        recording = None if aggregate is not None else world.new_agents_recording(name)
        what = f"record({name!r})"
        is_state = specs[0].value_kind == "state"  # one kind per variable
        parts = [
            _RecorderPart(
                spec,
                state.by_group[spec.group],
                None
                if spec.window is None or (is_state and spec.window == "last")
                else Window(
                    spec.window,
                    what,
                    zero_fill=aggregate == "count",
                    hold=spec.holds,
                ),
            )
            for spec in specs
        ]
        clock = _SampleClock()

        def collect(w: SimulationWorld) -> None:
            t = w.clock.time
            first = clock.last is None
            weight = clock.tick(t)
            on = self._holds_recorded if is_state else self.recording_enabled
            sampled = False
            for part in parts:
                spec = part.spec
                due = spec.due(t)
                if spec.holds:  # a state's window: weight levels with the time held
                    values = self._sample_part(part, t, what) if on else None
                    part.window.add(weight, values)
                    if not due:
                        continue
                    if not part.window.empty:
                        values = part.window.emit()
                    elif not (first and on):  # no recorded time yet
                        continue
                    if recording is None:
                        self._emit_aggregate(name, part, t, values)
                        continue
                    for entity, value in values.items():
                        _append_sample(
                            recording.timeseries, recording.agent_time, entity, value, t
                        )
                    sampled = True
                    continue
                if not on:
                    continue
                if part.window is None and not due:
                    continue
                if recording is not None and part.window is None:
                    self._append_samples(recording, part, t)  # the common case
                    sampled = True
                    continue
                values = self._sample_part(part, t, what)
                if part.window is not None:
                    part.window.add(weight, values)
                    if not due:
                        continue
                    values = part.window.emit()
                if recording is None:
                    self._emit_aggregate(name, part, t, values)
                    continue
                for entity, value in values.items():
                    _append_sample(
                        recording.timeseries, recording.agent_time, entity, value, t
                    )
                sampled = True
            if sampled:
                recording.time.append(t)

        world.add_data_collector(collect)

    @staticmethod
    def _append_samples(recording: Any, part: _RecorderPart, t: float) -> None:
        """Record one value per member (no aggregate, no window)."""
        spec = part.spec
        getter = spec.getter
        timeseries, agent_time = recording.timeseries, recording.agent_time
        for entity in part.members:
            try:
                value = getter(entity.agent)
            except Exception as exc:
                add_note(
                    exc,
                    f"while recording {spec.name!r} of entity {entity.name!r} "
                    f"(group {spec.group!r}) at t={t:g} s",
                )
                raise
            _append_sample(timeseries, agent_time, entity.name, value, t)

    def _probe_recorders(self) -> int:
        """Evaluate every recorder once now and store nothing; return the values computed.

        ``record: false`` phases and ``every=`` are ignored, so a check (such
        as ``mango-scenario validate --build``) reaches every recorder's
        getter, aggregate and window even in a warm-up.  Errors carry the
        same notes as during recording.
        """
        state = self._state
        t = self.elapsed_s
        computed = 0
        for spec in state.records:
            if spec.kind == "world":
                what = f"record_world({spec.name!r})"
                try:
                    value = call_flexible(spec.getter, spec.ctx)
                except Exception as exc:
                    add_note(
                        exc,
                        f"while recording world variable {spec.name!r} (entity "
                        f"{spec.entity!r}) at t={t:g} s",
                    )
                    raise
                values: dict = {None: value}
            else:
                what = f"record({spec.name!r})"
                part = _RecorderPart(spec, state.by_group[spec.group], None)
                values = self._sample_part(part, t, what)
            computed += len(values)
            if spec.window is not None:
                window = Window(spec.window, what, zero_fill=spec.aggregate == "count")
                window.add(1.0, values)
                window.emit()
        return computed

    def _sample_part(self, part: _RecorderPart, t: float, what: str) -> dict:
        """One sample of a recorder part: ``{entity: value}`` or, aggregated, ``{label: value}``."""
        spec = part.spec
        getter = spec.getter
        values: dict = {}
        for entity in part.members:
            try:
                values[entity.name] = getter(entity.agent)
            except Exception as exc:
                add_note(
                    exc,
                    f"while recording {spec.name!r} of entity {entity.name!r} "
                    f"(group {spec.group!r}) at t={t:g} s",
                )
                raise
        if spec.aggregate is None:
            return values
        try:
            reduced = group_reduce(
                spec.aggregate, list(values.values()), list(values), what
            )
        except Exception as exc:
            add_note(
                exc,
                f"while aggregating {spec.name!r} over group {spec.group!r} at t={t:g} s",
            )
            raise
        if spec.aggregate == "count":
            for label in reduced:
                part.labels.setdefault(label, None)
            return {label: reduced.get(label, 0) for label in part.labels}
        if isinstance(reduced, Mapping):
            return {str(label): value for label, value in reduced.items()}
        return {spec.group: reduced}

    def _emit_aggregate(
        self, name: str, part: _RecorderPart, t: float, values: dict
    ) -> None:
        """Append the rows of an aggregated recorder (entity = group or category)."""
        group = part.spec.group
        for entity, value in values.items():
            key = (name, group, entity)
            series = self._series.get(key)
            if series is None:
                # a category seen for the first time counted 0 at the earlier samples
                fill = part.emitted if part.spec.aggregate == "count" else []
                series = self._series[key] = (list(fill), [0] * len(fill))
            series[0].append(t)
            series[1].append(value)
        part.emitted.append(t)

    # -- stepping --------------------------------------------------------------------

    def _steps_done(self) -> int:
        step_s = self.time.step_s
        return round(self.elapsed_s / step_s) if step_s else 0

    async def _world_step(self, **kwargs) -> SimulationResult | None:
        result = await self._state.world.step(**kwargs)
        if self._retiring:
            await self._finish_retiring()
        if result is not None:
            self._steps += 1
            self._messages += result.messages_delivered
        return result

    def _grid_point(self, k: int) -> float:
        """Offset of fixed step boundary *k* (the horizon exactly for the last one)."""
        time = self.time
        return time.horizon_s if k >= time.n_steps else _on_grid(k, time.step_s)

    async def _advance(self, limit: float) -> SimulationResult | None:
        """One step that does not pass offset *limit*; ``None`` when at *limit*."""
        if self._stop_reason is not None:
            return None
        result = await self._advance_world(limit)
        if result is not None:
            self._check_stop_conditions()
        return result

    async def _advance_world(self, limit: float) -> SimulationResult | None:
        t = self.elapsed_s
        if t >= limit - _tolerance(limit):
            return None
        self._apply_boundaries(t)
        if self._stop_reason is not None:  # an event action stopped the run at t
            return None
        if not self.time.is_event_driven:
            # step by grid index, so float error never accumulates: t + (x - t)
            # lands exactly on x because x - t is exact for t >= x / 2
            k = self._steps_done()
            if k >= self.time.n_steps:
                return None
            target = self._grid_point(k + 1)
            if target > limit + _tolerance(limit):
                return None
            return await self._world_step(step_size_s=target - t)
        bound = min(limit, self._next_boundary(t))
        remaining = bound - t
        result = await self._world_step(max_advance_time_s=remaining)
        if result is None:
            # nothing scheduled before the boundary: jump to it
            result = await self._world_step(step_size_s=remaining)
        return result

    async def step(self) -> SimulationResult | None:
        """Advance by one step: the fixed step, or to the next event in event mode.

        In event mode the step never passes the horizon (or, with phases and
        events, the next boundary); without events before it the clock jumps
        there in one step.

        :return: the world's :class:`~mango.simulation.world.SimulationResult`,
            or ``None`` once the horizon is reached

        Example::

            async with run:
                while (result := await run.step()) is not None:
                    ...
        """
        self._require("run.step()")
        return await self._advance(self.time.horizon_s)

    async def run_until(self, time_point: Any) -> None:
        """Step until the clock reaches *time_point* (never past the horizon).

        In fixed-step mode the run stops at the last step boundary at or
        before *time_point*.

        :param time_point: an offset (seconds or ``"12h"``) or an absolute
            datetime/ISO string (needs ``time.start``)
        :raises ScenarioError: for time points beyond the horizon or before now

        Example::

            await run.run_until("12h")
            await run.run_until("2024-06-01T18:00")
        """
        self._require("run.run_until()")
        target = self._until_offset(time_point)
        while await self._advance(target) is not None:
            pass

    def _until_offset(self, time_point: Any) -> float:
        try:
            target = self.time.offset(time_point)
        except ValueError as exc:
            raise ScenarioError(f"run_until({time_point!r}): {exc}") from None
        horizon = self.time.horizon_s
        if target > horizon + _tolerance(horizon):
            raise ScenarioError(
                f"run_until({time_point!r}): offset {target:g} s lies beyond the "
                f"horizon ({horizon:g} s)."
            )
        if target < self.elapsed_s - _tolerance(target):
            raise ScenarioError(
                f"run_until({time_point!r}): offset {target:g} s lies before the "
                f"current time ({self.elapsed_s:g} s)."
            )
        return target

    async def run(self) -> None:
        """Step to the horizon.

        Example::

            async with run:
                await run.run()
        """
        self._require("run.run()")
        while await self._advance(self.time.horizon_s) is not None:
            pass

    async def execute(self, until: Any = None) -> RunResult:
        """Enter, run to the horizon (or *until*), shut down and return the result.

        :param until: stop at this offset or absolute time instead of the
            horizon (a quick test run; validation against the horizon is
            unchanged); the run is marked truncated:
            ``stats["stopped_early"]`` and ``stats["stop_reason"] == "until"``

        Example::

            result = await scenario.build(seed=7).execute()
            smoke = await scenario.build(seed=7).execute(until="1h")
        """
        async with self:
            if until is None:
                await self.run()
            else:
                target = self._until_offset(until)
                while await self._advance(target) is not None:
                    pass
                horizon = self.time.horizon_s
                if self._stop_reason is None and target < horizon - _tolerance(horizon):
                    self.stop("until")
        return self.result()

    # -- event logs and metrics --------------------------------------------------------

    def _log(self, name: str, fields: dict) -> None:
        """Append a row to event log *name* (see :meth:`BuildContext.log`)."""
        reserved = [key for key in fields if key in LOG_COLUMNS]
        if reserved:
            raise ScenarioError(
                f"ctx.log({name!r}): the field {reserved[0]!r} is a column every log "
                f"row gets ({', '.join(LOG_COLUMNS)}); use another field name."
            )
        t = self.elapsed_s
        schedule = self._schedule
        if schedule is not None:
            phase = schedule.phase_at(t)
            if not phase.record:
                return
            phase_name = phase.name
        else:
            phase_name = self._phase_log[0][1]
        self._logs.setdefault(name, []).append((t, phase_name, fields))

    def _set_metric(self, name: str, value: Any) -> None:
        try:
            plain = to_plain(value)
        except TypeError:
            raise ScenarioError(
                f"Metric {name!r}: {type(value).__name__} values cannot be stored; "
                "use a number, bool, string, None or a plain list/dict."
            ) from None
        self._metrics[name] = plain

    def _collect_metrics(self, state: RunState) -> dict:
        metrics = dict(self._metrics)
        for spec in state.metric_specs:
            try:
                value = call_flexible(spec.fn, spec.ctx)
            except Exception as exc:
                add_note(exc, f"while computing metric {spec.name!r}")
                raise
            self._set_metric(spec.name, value)
            metrics[spec.name] = self._metrics[spec.name]
        return metrics

    # -- results ---------------------------------------------------------------------

    def result(self) -> RunResult:
        """Collect the :class:`~mango.scenario.RunResult` (inside or after the run).

        Example::

            async with run:
                await run.run()
            result = run.result()
        """
        if self._status not in ("running", "closed") or self._state is None:
            raise ScenarioError(
                "run.result() needs a run that was entered: 'async with run: ...'."
            )
        if self._result is not None and self._status == "closed":
            return self._result
        state = self._state
        world = state.world
        run_id = self.run_id
        groups = dict(state.entity_groups)
        clock = world.clock
        to_datetime = (
            clock.to_datetime if getattr(clock, "has_calendar", False) else None
        )
        series = recording_series(world, groups)
        series += [
            (group, entity, variable, times, values)
            for (variable, group, entity), (times, values) in self._series.items()
        ]
        kinds = {
            spec.name: spec.row_kind
            for spec in state.records
            if spec.row_kind is not None
        }
        frame = assemble_frame(
            series,
            run_id=run_id,
            phases=self._phase_log,
            to_datetime=to_datetime,
            state_variables={n for n, kind in kinds.items() if kind == "state"},
        )
        logs = {
            name: log_frame(rows, run_id=run_id, to_datetime=to_datetime)
            for name, rows in self._logs.items()
        }
        metrics = self._collect_metrics(state)
        if self._status == "closed" and state.records and not len(frame):
            self._warn_empty()
        result = RunResult(
            run_id=run_id,
            scenario=self.scenario_ref,
            params=self.params,
            config=dump_params(self.params),
            seed=self.seed,
            time=self.time,
            frame=frame,
            stats=self.stats,
            entities=groups,
            world=world,
            factories=self.scenario.factory_refs(self.params),
            logs=logs,
            metrics=metrics,
            warnings=list(state.warnings),
            record_kinds=kinds,
        )
        if self._status == "closed":
            self._result = result
            del series
            self._release_recordings(world)
        return result

    def _release_recordings(self, world: SimulationWorld) -> None:
        """Free the raw recorded values of a finished run (the frame holds them)."""
        for recording in world.data_agent_collections.values():
            recording.timeseries.clear()
            recording.agent_time.clear()
            recording.time.clear()
        for recording in world.data_collections.values():
            recording.time.clear()
            recording.timeseries.clear()
        self._series.clear()

    def _warn_empty(self) -> None:
        """Warn that a run with recorders recorded no values, and say why."""
        from .events import empty_run_reason

        reason = empty_run_reason(
            dump_params(self.params), self.stats, recorders=len(self._state.records)
        )
        self._state.warn(
            f"Run {self.run_id} of scenario {self.scenario.name!r} recorded no values: "
            f"{reason}.",
            stacklevel=None,  # at the user's scenario.run(...) call
        )

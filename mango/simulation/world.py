"""
SimulationWorld: a self-contained simulation world for mango.

Agents registered in a SimulationWorld share an
:class:`~mango.util.clock.ExternalClock` and can be stepped forward in
discrete or fixed-size time increments.

The world is a facade: agent registration and message transport are
handled by a :class:`~mango.simulation.container.SimulationContainer`
subcomponent, while the world drives the simulation loop, the
environment, and the recording infrastructure.

Typical usage::

    async def run():
        world = create_world(start_time=0.0)
        agent = world.register(MyAgent())

        async with world:
            await world.step(step_size_s=1.0)
            await world.step(step_size_s=1.0)

    asyncio.run(run())

Or for a fully automated discrete-event run::

    async def run():
        world = create_world(start_time=0.0)
        agent = world.register(MyAgent())
        async with world:
            await world.step_until(max_advance_time_s=60.0)

    asyncio.run(run())

The module-level :func:`step_simulation` and :func:`discrete_step_until`
are thin aliases for :meth:`SimulationWorld.step` and
:meth:`SimulationWorld.step_until`.
"""

import asyncio
import datetime as _dt
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mango.agent.core import Agent, AgentAddress
from mango.messages.codecs import Codec
from mango.util import tracing
from mango.util.clock import ExternalClock
from mango.util.seeding import SeedTree
from mango.util.termination_detection import tasks_complete_or_sleeping

from .communication import CommunicationSimulation, SimpleCommunicationSimulation
from .container import MessageTransaction, SimulationContainer
from .environment import DefaultEnvironment, Environment, WorldObserver
from .recording import (
    AgentsRecording,
    WorldRecording,
    collect_agent_data,
    collect_data,
    position_history,
    record_agent,
    record_agent_having,
    record_position,
    record_world,
    recordings_to_frame,
    recordings_to_records,
)

if TYPE_CHECKING:
    import numpy
    import pandas

logger = logging.getLogger(__name__)

DISCRETE_EVENT: float = -1.0
AGENT_PREFIX: str = "agent"

#: Stream names the world binds to components whose ``rng`` is ``None``.
COMM_STREAM = "comm"
SPACE_STREAM = "space"
BEHAVIOR_STREAM = "behavior"
ENVIRONMENT_STREAM = "environment"

_MISSING = object()

#: The no-op ``on_step`` every agent inherits unless it overrides it.
_BASE_ON_STEP = Agent.on_step


def _add_note(exc: BaseException, note: str) -> None:
    """``exc.add_note(note)`` (``__notes__`` before Python 3.11)."""
    if hasattr(exc, "add_note"):
        exc.add_note(note)
    else:  # pragma: no cover - Python 3.10
        notes = getattr(exc, "__notes__", None)
        if not isinstance(notes, list):
            notes = []
            try:
                exc.__notes__ = notes
            except AttributeError:
                return
        notes.append(note)


# Attribute a world sets on each component it binds a stream to.  A later
# world rebinds a component whose ``rng`` is still that generator, so a
# component reused across worlds follows the new world's seed, while a
# generator the user assigned is kept.
_BOUND_RNG_ATTR = "_mango_world_bound_rng"


__all__ = [
    "AGENT_PREFIX",
    "AgentError",
    "AgentTaskError",
    "BEHAVIOR_STREAM",
    "COMM_STREAM",
    "ENVIRONMENT_STREAM",
    "SPACE_STREAM",
    "AgentsRecording",
    "DISCRETE_EVENT",
    "MessageTransaction",
    "SimulationContainer",
    "SimulationResult",
    "SimulationWorld",
    "WorldRecording",
    "collect_agent_data",
    "collect_data",
    "create_world",
    "discrete_step_until",
    "position_history",
    "record_agent",
    "record_agent_having",
    "record_position",
    "record_world",
    "recordings_to_frame",
    "recordings_to_records",
    "step_simulation",
]


@dataclass(frozen=True)
class AgentError:
    """An exception that escaped an agent's scheduled task during a run.

    Scheduled tasks include periodic and timestamp tasks, coroutines started
    with ``schedule_instant_task`` (e.g. async work spawned by a message
    handler) and role coroutines. See :attr:`SimulationWorld.agent_errors`.

    :ivar aid: the agent whose scheduler ran the task
    :ivar time: simulation time (s) at which the task raised
    :ivar task: readable name of the task (its coroutine)
    :ivar exception: the exception the task raised
    """

    aid: str
    time: float
    task: str
    exception: BaseException


class AgentTaskError(RuntimeError):
    """Raised at the end of a step in which an agent's scheduled task raised.

    The step itself completed (time advanced, recordings were taken), but
    the run is invalid. :attr:`errors` holds every
    :class:`AgentError` raised in that step, the first one is chained as
    ``__cause__``. Create the world with ``raise_agent_errors=False`` to only
    log task errors (they are still collected in
    :attr:`SimulationWorld.agent_errors`).
    """

    def __init__(self, message: str, errors: "list[AgentError]"):
        super().__init__(message)
        self.errors = errors


def _task_label(task: Any) -> str:
    for attr in ("_coro", "_coroutine_func", "_coroutine", "_coro_func"):
        target = getattr(task, attr, None)
        if target is None:
            continue
        name = getattr(target, "__qualname__", None) or getattr(
            target, "__name__", None
        )
        if name:
            return name
    return type(task).__name__


@dataclass
class SimulationResult:
    """Return value of :meth:`SimulationWorld.step`."""

    time_elapsed_s: float
    step_size_s: float
    messages_delivered: int


class _AgentDispatchObserver(WorldObserver):
    """Forwards global events to every agent in the world."""

    def __init__(self, world: "SimulationWorld"):
        self._world = world

    def dispatch_global_event(self, clock, event: Any) -> None:
        event_id = tracing._event_emitted(event)
        registered = self._world.agents
        # a snapshot: a handler may retire (deregister) or spawn agents; an
        # agent deregistered by an earlier handler gets no event
        for agent in list(registered.values()):
            if registered.get(agent.aid) is not agent:
                continue
            with tracing._trigger(
                "global_event",
                "event",
                agent=agent.aid,
                clock=agent.scheduler.clock,
                cause=event_id,
                content=event,
            ):
                for _cond, _handler in agent._behavior_global_event_handlers:
                    if _cond(event):
                        tracing._handler_called("event", _handler)
                        _handler(agent, event)
                agent.on_global_event(event)
                if hasattr(agent, "roles"):
                    for role in agent.roles:
                        for _cond, _handler in role._behavior_global_event_handlers:
                            if _cond(event):
                                tracing._handler_called("event", _handler)
                                _handler(role, event)
                        role.on_global_event(event)


class SimulationWorld:
    """A local, clock-driven simulation world.

    Do not instantiate directly; use :func:`create_world` instead.

    The world is a facade over a
    :class:`~mango.simulation.container.SimulationContainer` (which agents
    register against and which handles message transport), the
    :class:`~mango.simulation.environment.Environment`, and the recording
    infrastructure.  Stepping the simulation is done via :meth:`step` and
    :meth:`step_until`.
    """

    def __init__(
        self,
        clock: ExternalClock,
        communication_sim: CommunicationSimulation,
        environment: Environment | None = None,
        trace: Any = None,
        seed: "int | SeedTree | Mapping | None" = None,
        raise_agent_errors: bool = True,
        record_messages: bool = True,
    ):
        self.environment: Environment = environment or DefaultEnvironment()
        self._container = SimulationContainer(
            clock=clock,
            communication_sim=communication_sim,
            on_agent_registered=self._install_in_environment,
            seed=seed,
            record_messages=record_messages,
        )
        self._bind_rng(communication_sim, COMM_STREAM)
        self._bind_environment_rngs(self.environment)

        # Recording infrastructure
        self.data_collections: dict[str, WorldRecording] = {}
        self.data_agent_collections: dict[str, AgentsRecording] = {}
        self._data_collectors: list[Callable] = []

        self._initialized: bool = False
        self._trace = tracing._TracedRun(trace, simulated=True)
        # agents whose message handling died: (aid, exception)
        self._failed_agents: list[tuple[str, BaseException | None]] = []
        self._stepper_cache: tuple[int, list] | None = None
        # exceptions escaping scheduled agent tasks (see agent_errors)
        self._raise_agent_errors = raise_agent_errors
        self._agent_errors: list[AgentError] = []
        self._agent_errors_raised = 0

        # Wire environment to agent dispatcher
        self.environment.add_observer(_AgentDispatchObserver(self))

    # ------------------------------------------------------------------
    # Container facade
    # ------------------------------------------------------------------

    @property
    def container(self) -> SimulationContainer:
        return self._container

    @property
    def clock(self) -> ExternalClock:
        return self._container.clock

    @property
    def communication_sim(self) -> CommunicationSimulation:
        return self._container.communication_sim

    @communication_sim.setter
    def communication_sim(self, value: CommunicationSimulation) -> None:
        self._bind_rng(value, COMM_STREAM)
        self._container.communication_sim = value

    # ------------------------------------------------------------------
    # Reproducible randomness and calendar
    # ------------------------------------------------------------------

    @property
    def seed_tree(self) -> SeedTree:
        """The root :class:`~mango.util.seeding.SeedTree` of this world.

        Example::

            world = create_world(seed=42)
            world.seed_tree.child("weather").generator()  # fresh, uncached stream
        """
        return self._container.seed_tree

    @property
    def seed(self) -> dict:
        """The seed identity as plain data, for provenance and replay.

        ``create_world(seed=world.seed)`` rebuilds a world with identical
        random streams.

        Example::

            import json
            from pathlib import Path

            world = create_world()  # entropy drawn from the global random module
            Path("seed.json").write_text(json.dumps(world.seed))
            replay = create_world(seed=json.loads(Path("seed.json").read_text()))
        """
        return self._container.seed_tree.to_dict()

    def rng(self, *names: str | int) -> "numpy.random.Generator":
        """Return the world's random stream *names* (one cached Generator per stream).

        Agents use ``world.rng(f"agent/{aid}")`` (reachable as ``agent.rng``;
        for an aid containing ``/`` it is ``world.rng("agent", name_key(aid))``);
        the world binds ``"comm"``, ``"space"``, ``"behavior"`` and
        ``"environment"`` to components whose ``rng`` is ``None``.

        :param names: ``/``-separated stream name, or several segments (read
            like :meth:`SeedTree.rng <mango.util.seeding.SeedTree.rng>`)
        :return: the stream

        Example::

            world = create_world(seed=42)
            noise = world.rng("weather").normal(0.0, 1.0, size=24)
            world.rng("weather") is world.rng("weather")  # True
        """
        return self._container.rng(*names)

    @property
    def datetime(self) -> _dt.datetime:
        """Current calendar time of the world clock (needs ``start_datetime``).

        Example::

            world = create_world(start_datetime="2024-06-01T00:00")
            world.datetime  # datetime(2024, 6, 1, 0, 0)
        """
        return self.clock.datetime

    def _bind_rng(self, component: Any, stream: str) -> None:
        if component is None:
            return
        current = getattr(component, "rng", _MISSING)
        if current is _MISSING:
            return
        if current is None or getattr(component, _BOUND_RNG_ATTR, None) is current:
            generator = self.rng(stream)
            component.rng = generator
            try:
                setattr(component, _BOUND_RNG_ATTR, generator)
            except AttributeError:  # __slots__ without room for the marker
                pass

    def _bind_environment_rngs(self, environment: Environment) -> None:
        if isinstance(environment, DefaultEnvironment):
            self._bind_rng(environment.space, SPACE_STREAM)
            self._bind_rng(environment.behavior, BEHAVIOR_STREAM)
        self._bind_rng(environment, ENVIRONMENT_STREAM)

    @property
    def addr(self) -> str:
        return self._container.addr

    @property
    def name(self) -> str:
        return self._container.name

    @property
    def codec(self) -> Codec:
        return self._container.codec

    @property
    def agents(self) -> dict[str, Agent]:
        return self._container._agents

    @property
    def running(self) -> bool:
        return self._container.running

    @running.setter
    def running(self, value: bool) -> None:
        self._container.running = value

    @property
    def ready(self) -> bool:
        return self._container.ready

    @property
    def recorded_messages(self) -> list[MessageTransaction]:
        """Every delivered message (sender, receiver, times, content), in delivery order.

        Kept only when the world was created with ``record_messages=True``
        (the default of :func:`create_world`; scenario runs switch it off).
        The list grows with every message and keeps each content alive; for a
        count use :attr:`messages_delivered`.

        :raises RuntimeError: when the world does not keep messages

        Example::

            world = create_world()
            ...
            for m in world.recorded_messages:
                print(m.sender_id, "->", m.receiver_id, m.arriving_time, m.content)
        """
        container = self._container
        if not container.record_messages:
            raise RuntimeError(
                "This world does not keep delivered messages (it was created with "
                "record_messages=False, the default of scenario runs). Count them "
                "with world.messages_delivered, or keep them with "
                "create_world(record_messages=True) (scenario runs: "
                "scenario.run(..., record_messages=True) or "
                "scenario.build(..., record_messages=True))."
            )
        return container.recorded_messages

    @recorded_messages.setter
    def recorded_messages(self, value: list[MessageTransaction]) -> None:
        self._container.recorded_messages = value

    @property
    def messages_delivered(self) -> int:
        """Number of messages delivered so far (counted with and without ``record_messages``).

        Example::

            world = create_world(record_messages=False)
            ...
            record_world(world, "n_msgs", lambda: world.messages_delivered)
        """
        return self._container.messages_delivered

    @property
    def record_messages(self) -> bool:
        """Whether delivered messages are kept in :attr:`recorded_messages`.

        Can be switched at any time; switching it off does not clear the
        messages kept so far.

        Example::

            world = create_world()
            world.record_messages = False  # from now on only count messages
        """
        return self._container.record_messages

    @record_messages.setter
    def record_messages(self, value: bool) -> None:
        self._container.record_messages = bool(value)

    def register(
        self, agent: Agent, suggested_aid: str | None = None, **install_kwargs
    ) -> Agent:
        """Register *agent* with the world and return it.

        :param agent: agent instance to register
        :param suggested_aid: optional preferred agent ID
        :param install_kwargs: forwarded to the environment's ``install`` hook
        :return: the registered agent (same object)
        """
        registered = self._container.register(
            agent, suggested_aid=suggested_aid, **install_kwargs
        )
        registered.scheduler.on_task_error = lambda task, exc, aid=registered.aid: (
            self._on_task_error(aid, task, exc)
        )
        inbox_task = getattr(registered, "_check_inbox_task", None)
        if inbox_task is not None:
            inbox_task.add_done_callback(
                lambda task, agent=registered: self._on_inbox_stopped(agent, task)
            )
            # the world raises a failed message handler (see
            # _raise_agent_failure), so the agent only logs it at debug level
            registered.__dict__["_mango_inbox_failure_reported"] = True
        return registered

    def _on_inbox_stopped(self, agent: Agent, task: asyncio.Task) -> None:
        """Record an agent whose message handling died, and unblock settling.

        The agent's inbox would never be joined again, so stepping would wait
        forever; instead its pending messages are dropped and the next
        settling point raises (see :meth:`_raise_agent_failure`).
        """
        if task.cancelled() or not self._container.running:
            return
        self._failed_agents.append((agent.aid, getattr(agent, "_inbox_failure", None)))
        inbox = agent.inbox
        while not inbox.empty():
            inbox.get_nowait()
            inbox.task_done()
        while True:  # the message that was being handled when it failed
            try:
                inbox.task_done()
            except ValueError:
                break

    @property
    def agent_errors(self) -> list[AgentError]:
        """Every exception that escaped an agent's scheduled task so far.

        By default the world raises :class:`AgentTaskError` at the end of the
        step in which a task raised; with ``create_world(...,
        raise_agent_errors=False)`` the errors are only logged and collected
        here, so a caller can count or report them.

        Example::

            world = create_world(raise_agent_errors=False)
            ...
            await world.step_until(3600.0)
            for error in world.agent_errors:
                print(error.aid, error.time, error.task, repr(error.exception))
        """
        return list(self._agent_errors)

    def _on_task_error(self, aid: str, task: Any, exc: BaseException) -> bool:
        """Scheduler hook: collect *exc*; ``True`` suppresses the log record."""
        if not self._container.running:
            return False
        self._agent_errors.append(
            AgentError(
                aid=aid, time=self.clock.time, task=_task_label(task), exception=exc
            )
        )
        return self._raise_agent_errors

    def _raise_agent_task_errors(self) -> None:
        if not self._raise_agent_errors:
            return
        new = self._agent_errors[self._agent_errors_raised :]
        if not new:
            return
        self._agent_errors_raised = len(self._agent_errors)
        first = new[0]
        more = (
            f" ({len(new) - 1} further task errors in this step)"
            if len(new) > 1
            else ""
        )
        raise AgentTaskError(
            f"Agent {first.aid!r} raised an exception in its scheduled task "
            f"{first.task!r} at t={first.time:g} s: "
            f"{type(first.exception).__name__}: {first.exception}{more}. "
            "The step completed, but the run is invalid. Fix the agent code (see "
            "the chained exception), or create the world with "
            "raise_agent_errors=False to only log task errors.",
            new,
        ) from first.exception

    def _raise_agent_failure(self) -> None:
        if not self._failed_agents:
            return
        aid, exc = self._failed_agents[0]
        # no doubled period when the message ends a sentence itself
        cause = "" if exc is None else f": {type(exc).__name__}: {str(exc).rstrip('.')}"
        same = [
            other
            for other, other_exc in self._failed_agents[1:]
            if exc is not None
            and type(other_exc) is type(exc)
            and str(other_exc) == str(exc)
        ]
        different = len(self._failed_agents) - 1 - len(same)
        more = ""
        if same:
            names = ", ".join(repr(name) for name in same[:3])
            names += ", ..." if len(same) > 3 else ""
            more += (
                f" {len(same)} more agent{'s' if len(same) > 1 else ''} failed the "
                f"same way ({names})."
            )
        if different:
            more += (
                f" {different} more agent{'s' if different > 1 else ''} failed "
                "with other errors."
            )
        hint = ""
        if isinstance(exc, TypeError) and "handle_message()" in str(exc):
            hint = (
                " mango calls handle_message(content, meta) with two positional "
                "arguments: define it as 'def handle_message(self, content, meta)'."
            )
        raise RuntimeError(
            f"Agent {aid!r} stopped processing messages because handling a message "
            f"raised an exception{cause}.{more}{hint} The simulation cannot "
            "continue; fix the agent's message handler (see the chained exception)."
        ) from exc

    def deregister(self, aid: str) -> None:
        self._container.deregister(aid)

    def is_aid_available(self, aid: str) -> bool:
        return self._container.is_aid_available(aid)

    async def send_message(
        self,
        content: Any,
        receiver_addr: AgentAddress,
        sender_id: str | None = None,
        **kwargs,
    ) -> bool:
        """Send a message, applying communication simulation.

        Messages are queued with a delivery time determined by the
        communication simulation.  They are delivered during the next call
        to :meth:`step`.
        """
        return await self._container.send_message(
            content, receiver_addr=receiver_addr, sender_id=sender_id, **kwargs
        )

    async def shutdown(self) -> None:
        """Shut down all agents, and finish the trace if the world is traced."""
        try:
            await self._container.shutdown()
        finally:
            self._trace.finish()

    def __getitem__(self, key: str | int) -> Agent:
        if isinstance(key, int):
            return list(self.agents.values())[key]
        return self.agents[key]

    def _install_in_environment(self, agent: Agent, aid: str, **install_kwargs) -> None:
        install = getattr(self.environment, "install", None)
        if callable(install):
            install(agent, agent_id=aid, **install_kwargs)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def new_world_recording(
        self, key: str, entity: str | None = None, variable: str | None = None
    ) -> WorldRecording:
        """Register a world-level recording under *key*.

        :param key: unique recording key
        :param entity: owner of the values in the tidy export (default ``"world"``)
        :param variable: tidy ``variable`` of the rows (default: *key*)
        :return: the new :class:`WorldRecording`
        :raises ValueError: if *key* is taken

        Example::

            north = world.new_world_recording("north/load_kw", "north", "load_kw")
        """
        if key in self.data_collections:
            raise ValueError(
                f"A recording is already registered for key '{key}'; "
                "use a distinct key per recording."
            )
        recording = WorldRecording(entity=entity, variable=variable)
        self.data_collections[key] = recording
        return recording

    def new_agents_recording(self, key: str) -> AgentsRecording:
        if key in self.data_agent_collections:
            raise ValueError(
                f"A recording is already registered for key '{key}'; "
                "use a distinct key per recording."
            )
        recording = AgentsRecording()
        self.data_agent_collections[key] = recording
        return recording

    def add_data_collector(
        self, collector: Callable[["SimulationWorld"], None]
    ) -> None:
        self._data_collectors.append(collector)

    def _do_recordings(self) -> None:
        for collector in self._data_collectors:
            collector(self)

    def to_records(
        self,
        *,
        groups: Mapping[str, str] | None = None,
        keys: Iterable[str] | None = None,
        include_world: bool = True,
        include_agents: bool = True,
    ) -> list[dict]:
        """Return all recordings as tidy rows.

        See :func:`~mango.simulation.recording.recordings_to_records` for the
        row layout and the parameters.

        Example::

            record_agent(world, "soc", lambda agent: agent.soc)
            ...  # step the world
            rows = world.to_records(keys=["soc"])
            # [{"time": 0.0, "group": "ev", "entity": "ev_0",
            #   "variable": "soc", "value": 0.5}, ...]
        """
        return recordings_to_records(
            self,
            groups=groups,
            keys=keys,
            include_world=include_world,
            include_agents=include_agents,
        )

    def to_frame(
        self,
        *,
        groups: Mapping[str, str] | None = None,
        keys: Iterable[str] | None = None,
        include_world: bool = True,
        include_agents: bool = True,
    ) -> "pandas.DataFrame":
        """Return all recordings as a tidy ``pandas.DataFrame``.

        See :func:`~mango.simulation.recording.recordings_to_frame`.

        Example::

            df = world.to_frame()
            soc = df[df.variable == "soc"].pivot(
                index="time", columns="entity", values="value"
            )
        """
        return recordings_to_frame(
            self,
            groups=groups,
            keys=keys,
            include_world=include_world,
            include_agents=include_agents,
        )

    # ------------------------------------------------------------------
    # Simulation loop
    # ------------------------------------------------------------------

    def _initialize_if_needed(self) -> None:
        if not self._initialized:
            self._trace.start()
            tracing._emit(
                "world.started",
                "run",
                self.clock,
                environment=type(self.environment).__name__,
                communication_sim=type(self._container.communication_sim).__name__,
                agents=sorted(self.agents),
            )
            self._container.on_ready()
            self.environment.initialize(list(self.agents.values()), self.clock)
            self._initialized = True
            self._do_recordings()

    def _steppers(self) -> list[tuple[Agent, bool, list]]:
        """``(agent, own on_step, roles with an on_step)`` for the agents a step calls.

        Agents and roles that keep the no-op base ``on_step`` are left out
        (with many idle agents the dispatch itself would dominate the step).
        The list is rebuilt when agents are registered or deregistered or
        roles are added or removed, so an ``on_step`` must be defined on the
        class or set on the instance before the agent is registered (or the
        role added).
        """
        from mango.agent.role import Role, role_changes

        version = (self._container.agents_version, role_changes())
        cached = self._stepper_cache
        if cached is not None and cached[0] == version:
            return cached[1]
        steppers = []
        for agent in self.agents.values():
            agent_type = type(agent)
            own = agent_type.on_step is not _BASE_ON_STEP or "on_step" in agent.__dict__
            roles = [
                role
                for role in (agent.roles if hasattr(agent_type, "roles") else ())
                if type(role).on_step is not Role.on_step or "on_step" in role.__dict__
            ]
            if own or roles:
                steppers.append((agent, own, roles))
        self._stepper_cache = (version, steppers)
        return steppers

    async def _wait_for_agents(self) -> None:
        self._raise_agent_failure()
        await tasks_complete_or_sleeping(self._container, drain_loop=True)
        self._raise_agent_failure()

    async def step(
        self,
        step_size_s: float = DISCRETE_EVENT,
        max_advance_time_s: float = -1.0,
    ) -> SimulationResult | None:
        """Advance the simulation by *step_size_s* seconds.

        When *step_size_s* is ``DISCRETE_EVENT`` (the default), the step size
        is determined automatically as the time until the next scheduled
        event (message arrival or agent task wakeup).

        :param step_size_s: step size in seconds, or ``DISCRETE_EVENT``
        :param max_advance_time_s: abort if the determined discrete step would
            exceed this value; ``-1`` means no limit
        :return: :class:`SimulationResult`, or ``None`` if there is nothing to do
        :raises AgentTaskError: at the end of the step if an agent's scheduled
            task raised during it (unless the world was created with
            ``raise_agent_errors=False``)
        :raises RuntimeError: if an agent's message handler raised, which
            stops that agent's message processing

        Example::

            result = await world.step(step_size_s=1.0)
            result = await world.step()  # discrete-event
        """
        self._initialize_if_needed()

        # Allow freshly scheduled tasks (e.g. from on_ready / on_start) to reach
        # their first sleeping or done point.  This is necessary so that periodic
        # tasks register their first wakeup with the ExternalClock before we
        # determine the discrete step size.
        await self._wait_for_agents()

        actual_step = step_size_s

        if actual_step == DISCRETE_EVENT:
            actual_step = self._container._determine_next_step_size()
            if actual_step is None:
                self._raise_agent_task_errors()
                return None
            if max_advance_time_s >= 0 and actual_step > max_advance_time_s:
                self._raise_agent_task_errors()
                return None

        new_time = self.clock.time + actual_step

        # ---- call on_step hooks ----
        # (agents that keep the no-op base hook are skipped: with many idle
        # agents the dispatch itself would dominate the step). The list is a
        # snapshot: an agent deregistered by an earlier hook of this step
        # (retired, removed) is skipped, also before its roles.
        environment, clock = self.environment, self.clock
        registered = self._container._agents
        stepping: tuple = (None, None)
        try:
            for agent, own_hook, roles in self._steppers():
                if own_hook:
                    if registered.get(agent.aid) is not agent:
                        continue
                    stepping = (agent, None)
                    agent.on_step(environment, clock, actual_step)
                for role in roles:
                    if registered.get(agent.aid) is not agent:
                        break
                    stepping = (agent, role)
                    role.on_step(environment, clock, actual_step)
        except Exception as exc:
            agent, role = stepping
            if agent is not None:
                _add_note(
                    exc,
                    f"while stepping agent {agent.aid!r}"
                    + ("" if role is None else f" (role {type(role).__name__})")
                    + f" in on_step at t={clock.time:g} s",
                )
            raise

        # ---- step environment ----
        self.environment.step(self.clock, actual_step)

        # ---- advance clock (wakes up sleeping tasks) ----
        self.clock.set_time(new_time)

        # ---- convergence loop: process messages and tasks ----
        total_delivered = 0
        state_changed = True
        while state_changed:
            # Wait for agents to settle
            await self._wait_for_agents()

            # Deliver messages whose delivery_time has now arrived
            delivered = await self._container._deliver_messages_due(new_time)
            total_delivered += delivered
            state_changed = delivered > 0

            # Yield control so delivered messages are processed
            if state_changed:
                await asyncio.sleep(0)

        self._do_recordings()
        # after the recordings, so the failing step is fully recorded
        self._raise_agent_task_errors()

        return SimulationResult(
            time_elapsed_s=actual_step,
            step_size_s=actual_step,
            messages_delivered=total_delivered,
        )

    async def step_until(self, max_advance_time_s: float) -> list[SimulationResult]:
        """Run a discrete-event simulation until *max_advance_time_s* has elapsed.

        The simulation stops when the world clock has advanced by
        *max_advance_time_s* from its current position, or when there are no
        more events to process.

        :param max_advance_time_s: maximum total time to simulate in seconds
        :return: list of :class:`SimulationResult` from each step

        Example::

            results = await world.step_until(max_advance_time_s=3600.0)
        """
        self._initialize_if_needed()
        start_time = self.clock.time
        max_time = start_time + max_advance_time_s
        results: list[SimulationResult] = []

        while self.clock.time < max_time:
            remaining = max_time - self.clock.time
            result = await self.step(
                step_size_s=DISCRETE_EVENT,
                max_advance_time_s=remaining,
            )
            if result is None:
                break
            results.append(result)

        return results

    async def __aenter__(self) -> "SimulationWorld":
        self._initialize_if_needed()
        # Allow all freshly scheduled tasks (e.g. from on_ready) to start and
        # reach their first sleeping/done point before the caller proceeds.
        await self._wait_for_agents()
        self._raise_agent_task_errors()
        return self

    async def __aexit__(self, *_) -> None:
        await self.shutdown()


def create_world(
    start_time: float = 0.0,
    communication_sim: CommunicationSimulation | None = None,
    environment: Environment | None = None,
    trace: Any = None,
    *,
    seed: "int | SeedTree | Mapping | None" = None,
    start_datetime: "_dt.datetime | str | None" = None,
    raise_agent_errors: bool = True,
    record_messages: bool = True,
) -> SimulationWorld:
    """Create a :class:`SimulationWorld`.

    The world owns a :class:`~mango.util.seeding.SeedTree`: ``world.rng(name)``
    returns one reproducible stream per name, agents and roles draw from
    ``self.rng`` (stream ``"agent/<aid>"``), and components whose public
    ``rng`` attribute is ``None`` are bound to a world stream: the
    communication simulation to ``"comm"``, ``DefaultEnvironment.space`` to
    ``"space"``, ``DefaultEnvironment.behavior`` to ``"behavior"`` and any
    other environment to ``"environment"``. Explicitly passed generators are
    kept.

    :param start_time: initial simulation time in seconds (default 0)
    :param communication_sim: communication simulation to use; defaults to
        :class:`~mango.simulation.communication.SimpleCommunicationSimulation`
        with zero delay and no loss
    :param environment: environment to use; defaults to
        :class:`~mango.simulation.environment.DefaultEnvironment`
    :param trace: trace the run (see :ref:`tracing-docs`): ``True`` for the
        defaults, a path for the trace file, or a
        :class:`~mango.util.tracing.TraceConfig`. Tracing starts when the
        world starts and ends when it shuts down; then the viewer is written
        next to the trace.
    :param seed: root seed: an ``int >= 0``, a
        :class:`~mango.util.seeding.SeedTree`, or a mapping such as
        ``world.seed`` of an earlier run; ``None`` draws the root entropy from
        the global :mod:`random` module (recorded in ``world.seed``)
    :param start_datetime: calendar date and time of *start_time* (a
        ``datetime`` or ISO 8601 string); enables ``world.datetime`` and
        ``world.clock.to_datetime``/``to_time``
    :param raise_agent_errors: when an exception escapes an agent's scheduled
        task (periodic task, ``schedule_instant_task`` coroutine, async work of
        a message handler, role coroutine), raise :class:`AgentTaskError` at
        the end of that step (default). ``False`` only logs the error and
        collects it in :attr:`SimulationWorld.agent_errors`.
    :param record_messages: keep every delivered message, content included, in
        :attr:`SimulationWorld.recorded_messages` (default). Memory then grows
        with the number of messages; ``False`` only counts them
        (:attr:`SimulationWorld.messages_delivered`). Scenario runs use
        ``False`` unless they opt in.
    :return: a ready-to-use :class:`SimulationWorld`

    Example::

        world = create_world(
            start_time=0.0,
            communication_sim=SimpleCommunicationSimulation(default_delay_s=0.1),
        )

        # reproducible randomness and calendar time
        world = create_world(seed=42, start_datetime="2024-06-01T00:00")
        replay = create_world(seed=world.seed)

        # writes mango_trace.jsonl and the viewer mango_trace.html
        async with create_world(trace=True) as world:
            ...

        # collect agent task errors instead of failing the step
        world = create_world(raise_agent_errors=False)

        # long runs with many messages: count them, do not keep them
        world = create_world(record_messages=False)
    """
    clock = ExternalClock(start_time=start_time, start_datetime=start_datetime)
    sim = communication_sim or SimpleCommunicationSimulation()
    return SimulationWorld(
        clock=clock,
        communication_sim=sim,
        environment=environment,
        trace=trace,
        seed=seed,
        raise_agent_errors=raise_agent_errors,
        record_messages=record_messages,
    )


async def step_simulation(
    world: SimulationWorld,
    step_size_s: float = DISCRETE_EVENT,
    max_advance_time_s: float = -1.0,
) -> SimulationResult | None:
    """Alias for :meth:`SimulationWorld.step` (which is preferred)."""
    return await world.step(
        step_size_s=step_size_s, max_advance_time_s=max_advance_time_s
    )


async def discrete_step_until(
    world: SimulationWorld,
    max_advance_time_s: float,
) -> list[SimulationResult]:
    """Alias for :meth:`SimulationWorld.step_until` (which is preferred)."""
    return await world.step_until(max_advance_time_s)

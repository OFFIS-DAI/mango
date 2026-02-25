"""
SimulationWorld – a self-contained simulation container for mango.

Mirrors the ``World`` type from Mango.jl.  Agents registered in a
SimulationWorld share an :class:`~mango.util.clock.ExternalClock` and can
be stepped forward in discrete or fixed-size time increments.

Typical usage::

    async def run():
        world = create_world(start_time=0.0)
        agent = world.register(MyAgent())

        async with world:
            await step_simulation(world, step_size_s=1.0)
            await step_simulation(world, step_size_s=1.0)

    asyncio.run(run())

Or for a fully automated discrete-event run::

    async def run():
        world = create_world(start_time=0.0)
        agent = world.register(MyAgent())
        async with world:
            await discrete_step_until(world, max_advance_time_s=60.0)

    asyncio.run(run())
"""

from __future__ import annotations

import asyncio
import bisect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from mango.agent.core import Agent, AgentAddress
from mango.messages.codecs import JSON
from mango.util.clock import ExternalClock

from .communication import (
    CommunicationSimulation,
    MessagePackage,
    SimpleCommunicationSimulation,
)
from .environment import DefaultEnvironment, Environment, WorldObserver

logger = logging.getLogger(__name__)

# Sentinel: use discrete-event stepping (step to next scheduled event).
DISCRETE_EVENT: float = -1.0

AGENT_PREFIX: str = "agent"


# ---------------------------------------------------------------------------
# Recording data structures
# ---------------------------------------------------------------------------


@dataclass
class WorldRecording:
    """Time-series recording of world-level data."""

    timeseries: list[Any] = field(default_factory=list)
    time: list[float] = field(default_factory=list)


@dataclass
class AgentsRecording:
    """Per-agent time-series recording.

    ``timeseries`` maps each agent AID to a list of recorded values.
    ``time`` holds the elapsed simulation seconds at each snapshot (shared
    across all agents).
    """

    timeseries: dict[str, list[Any]] = field(default_factory=dict)
    time: list[float] = field(default_factory=list)


@dataclass
class MessageTransaction:
    """Records a message that was delivered during the simulation."""

    sender_id: str | None
    receiver_id: str
    sent_time: float
    arriving_time: float
    content: Any


# ---------------------------------------------------------------------------
# Simulation result types
# ---------------------------------------------------------------------------


@dataclass
class SimulationResult:
    """Return value of :func:`step_simulation`."""

    time_elapsed_s: float
    step_size_s: float
    messages_delivered: int


# ---------------------------------------------------------------------------
# Internal helper: agent-level world observer
# ---------------------------------------------------------------------------


class _AgentDispatchObserver(WorldObserver):
    """Forwards global events to every agent in the world."""

    def __init__(self, world: "SimulationWorld"):
        self._world = world

    def dispatch_global_event(self, clock, event: Any) -> None:
        for agent in self._world._agents.values():
            agent.on_global_event(event)
            if hasattr(agent, "roles"):
                for role in agent.roles:
                    role.on_global_event(event)


# ---------------------------------------------------------------------------
# SimulationWorld
# ---------------------------------------------------------------------------


class SimulationWorld:
    """A local, clock-driven simulation container.

    Do not instantiate directly; use :func:`create_world` instead.

    The world acts as the *container* that agents register against.  It
    satisfies the minimal container interface expected by mango's
    :func:`~mango.util.termination_detection.tasks_complete_or_sleeping`
    helper (``inbox``, ``_agents``).
    """

    def __init__(
        self,
        clock: ExternalClock,
        communication_sim: CommunicationSimulation,
        environment: Environment | None = None,
    ):
        self.clock: ExternalClock = clock
        self.communication_sim: CommunicationSimulation = communication_sim
        self.environment: Environment = environment or DefaultEnvironment()

        # Container-like state
        self._agents: dict[str, Agent] = {}
        self._aid_counter: int = 0
        self.addr: str = "simulation"
        self.running: bool = True
        self.ready: bool = False
        self.inbox: asyncio.Queue | None = None  # not used but expected by termination util

        # codec is needed by agents that call container.codec; provide a default
        self.codec = JSON()

        # Pending message queue: sorted list of (delivery_time, seq, sent_time, content, meta)
        # seq is a monotonically increasing tie-breaker so bisect.insort never
        # needs to compare content or meta (which may not be orderable).
        self._pending_messages: list[tuple[float, int, float, Any, dict]] = []
        self._msg_seq: int = 0

        # Recording infrastructure
        self.data_collections: dict[str, WorldRecording] = {}
        self.data_agent_collections: dict[str, AgentsRecording] = {}
        self._data_collectors: list[Callable] = []

        # Transaction log
        self.recorded_messages: list[MessageTransaction] = []

        # Internal state
        self._initialized: bool = False

        # Wire environment to agent dispatcher
        observer = _AgentDispatchObserver(self)
        self.environment.add_observer(observer)

    # ------------------------------------------------------------------
    # Container interface (used by agents)
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self.addr

    def register(self, agent: Agent, suggested_aid: str | None = None) -> Agent:
        """Register *agent* with the world and return it.

        :param agent: agent instance to register
        :param suggested_aid: optional preferred agent ID
        :return: the registered agent (same object)
        """
        aid = self._reserve_aid(suggested_aid)
        if agent.context._container:
            raise ValueError("Agent is already registered to a container")
        self._agents[aid] = agent
        agent._do_register(self, aid)
        logger.debug("Registered agent '%s' with world", aid)
        if self.running:
            agent._do_start()
        if self.ready:
            agent.on_ready()
        return agent

    def deregister(self, aid: str) -> None:
        self._agents.pop(aid, None)

    def is_aid_available(self, aid: str) -> bool:
        pattern_clash = aid.startswith(AGENT_PREFIX) and aid[len(AGENT_PREFIX) :].isnumeric()
        return aid not in self._agents and not pattern_clash

    def _reserve_aid(self, suggested_aid: str | None = None) -> str:
        if suggested_aid is not None and self.is_aid_available(suggested_aid):
            return suggested_aid
        while True:
            aid = f"{AGENT_PREFIX}{self._aid_counter}"
            self._aid_counter += 1
            if aid not in self._agents:
                return aid

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
        to :func:`step_simulation`.
        """
        meta: dict[str, Any] = {
            "sender_id": sender_id,
            "sender_addr": self.addr,
            "receiver_id": receiver_addr.aid,
            "receiver_addr": self.addr,
            "network_protocol": "simulation",
        }
        meta.update(kwargs)

        sent_time = self.clock.time
        package = MessagePackage(
            sender_id=sender_id,
            receiver_id=receiver_addr.aid,
            sent_time=sent_time,
            content=(content, meta),
        )
        result = self.communication_sim.calculate_communication(
            current_time=sent_time,
            messages=[package],
        ).package_results[0]

        if not result.reached:
            logger.debug(
                "Message from %s to %s dropped (loss simulation)",
                sender_id,
                receiver_addr.aid,
            )
            return False

        delivery_time = sent_time + result.delay_s
        # Keep list sorted by delivery_time; seq breaks ties without comparing
        # content or meta (which may not be orderable).
        seq = self._msg_seq
        self._msg_seq += 1
        bisect.insort(
            self._pending_messages,
            (delivery_time, seq, sent_time, content, meta),
        )
        return True

    # ------------------------------------------------------------------
    # Simulation stepping helpers (used by step_simulation)
    # ------------------------------------------------------------------

    def _start_agents(self) -> None:
        """Ensure all agents have been started (internal use)."""
        # Agents are started when registered, but we call on_ready here
        self.ready = True
        for agent in self._agents.values():
            agent.on_ready()

    def _initialize_if_needed(self) -> None:
        if not self._initialized:
            self._start_agents()  # call on_ready on all agents
            self.environment.initialize(list(self._agents.values()), self.clock)
            self._initialized = True
            self._do_recordings()

    async def _deliver_messages_due(self, up_to_time: float) -> int:
        """Deliver all pending messages with delivery_time <= up_to_time.

        Returns the number of messages delivered.
        """
        delivered = 0
        remaining: list[tuple[float, int, float, Any, dict]] = []

        for delivery_time, seq, sent_time, content, meta in self._pending_messages:
            if delivery_time <= up_to_time:
                receiver_id = meta.get("receiver_id")
                agent = self._agents.get(receiver_id)
                if agent is not None:
                    await agent.inbox.put((0, content, meta))
                    self.recorded_messages.append(
                        MessageTransaction(
                            sender_id=meta.get("sender_id"),
                            receiver_id=receiver_id,
                            sent_time=sent_time,
                            arriving_time=delivery_time,
                            content=content,
                        )
                    )
                    delivered += 1
                else:
                    logger.warning(
                        "Unknown receiver '%s'; dropping message", receiver_id
                    )
            else:
                remaining.append((delivery_time, seq, sent_time, content, meta))

        self._pending_messages = remaining
        return delivered

    def _determine_next_step_size(self) -> float | None:
        """Return seconds to the next scheduled event, or None if none."""
        candidates: list[float] = []

        # Next message arrival
        if self._pending_messages:
            next_msg_arrival = self._pending_messages[0][0]
            candidates.append(next_msg_arrival - self.clock.time)

        # Next task wakeup
        next_task = self.clock.get_next_activity()
        if next_task is not None:
            candidates.append(max(0.0, next_task - self.clock.time))

        if not candidates:
            return None
        return min(candidates)

    # ------------------------------------------------------------------
    # Data recording
    # ------------------------------------------------------------------

    def _do_recordings(self) -> None:
        for collector in self._data_collectors:
            collector(self)

    def _get_world_recording(self, key: str) -> WorldRecording:
        if key not in self.data_collections:
            self.data_collections[key] = WorldRecording()
        return self.data_collections[key]

    def _get_agents_recording(self, key: str) -> AgentsRecording:
        if key not in self.data_agent_collections:
            self.data_agent_collections[key] = AgentsRecording()
        return self.data_agent_collections[key]

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "SimulationWorld":
        self._initialize_if_needed()
        # Allow all freshly scheduled tasks (e.g. from on_ready) to start and
        # reach their first sleeping/done point before the caller proceeds.
        await _wait_for_agents(self)
        return self

    async def __aexit__(self, *_) -> None:
        await self.shutdown()

    async def shutdown(self) -> None:
        """Shut down all agents."""
        self.running = False
        for agent in list(self._agents.values()):
            try:
                await agent.shutdown()
            except Exception:
                logger.exception("Error shutting down agent '%s'", agent.aid)

    # ------------------------------------------------------------------
    # __getitem__ for convenient agent access
    # ------------------------------------------------------------------

    def __getitem__(self, key: str | int) -> Agent:
        if isinstance(key, int):
            return list(self._agents.values())[key]
        return self._agents[key]


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def create_world(
    start_time: float = 0.0,
    communication_sim: CommunicationSimulation | None = None,
    environment: Environment | None = None,
) -> SimulationWorld:
    """Create a :class:`SimulationWorld`.

    :param start_time: initial simulation time in seconds (default 0)
    :param communication_sim: communication simulation to use; defaults to
        :class:`~mango.simulation.communication.SimpleCommunicationSimulation`
        with zero delay and no loss
    :param environment: environment to use; defaults to
        :class:`~mango.simulation.environment.DefaultEnvironment`
    :return: a ready-to-use :class:`SimulationWorld`

    Example::

        world = create_world(
            start_time=0.0,
            communication_sim=SimpleCommunicationSimulation(default_delay_s=0.1),
        )
    """
    clock = ExternalClock(start_time=start_time)
    sim = communication_sim or SimpleCommunicationSimulation()
    return SimulationWorld(clock=clock, communication_sim=sim, environment=environment)


# ---------------------------------------------------------------------------
# Simulation stepping
# ---------------------------------------------------------------------------


async def _wait_for_agents(world: SimulationWorld) -> None:
    """Wait until all agent tasks are complete or sleeping."""
    from mango.util.termination_detection import tasks_complete_or_sleeping

    await tasks_complete_or_sleeping(world)


async def step_simulation(
    world: SimulationWorld,
    step_size_s: float = DISCRETE_EVENT,
    max_advance_time_s: float = -1.0,
) -> SimulationResult | None:
    """Advance the simulation by *step_size_s* seconds.

    When *step_size_s* is ``DISCRETE_EVENT`` (the default), the step size is
    determined automatically as the time until the next scheduled event
    (message arrival or agent task wakeup).

    :param world: the simulation world to step
    :param step_size_s: step size in seconds, or ``DISCRETE_EVENT``
    :param max_advance_time_s: abort if the determined discrete step would
        exceed this value; ``-1`` means no limit
    :return: :class:`SimulationResult`, or ``None`` if there is nothing to do

    Example::

        result = await step_simulation(world, step_size_s=1.0)
        result = await step_simulation(world)  # discrete-event
    """
    world._initialize_if_needed()

    # Allow freshly scheduled tasks (e.g. from on_ready / on_start) to reach
    # their first sleeping or done point.  This is necessary so that periodic
    # tasks register their first wakeup with the ExternalClock before we
    # determine the discrete step size.
    await _wait_for_agents(world)

    actual_step = step_size_s

    if actual_step == DISCRETE_EVENT:
        actual_step = world._determine_next_step_size()
        if actual_step is None:
            return None
        if max_advance_time_s >= 0 and actual_step > max_advance_time_s:
            return None

    new_time = world.clock.time + actual_step

    # ---- call on_step hooks ----
    for agent in list(world._agents.values()):
        agent.on_step(world.environment, world.clock, actual_step)
        if hasattr(agent, "roles"):
            for role in agent.roles:
                role.on_step(world.environment, world.clock, actual_step)

    # ---- step environment ----
    world.environment.step(world.clock, actual_step)

    # ---- advance clock (wakes up sleeping tasks) ----
    world.clock.set_time(new_time)

    # ---- convergence loop: process messages and tasks ----
    total_delivered = 0
    state_changed = True
    while state_changed:
        # Wait for agents to settle
        await _wait_for_agents(world)

        # Deliver messages whose delivery_time has now arrived
        delivered = await world._deliver_messages_due(new_time)
        total_delivered += delivered
        state_changed = delivered > 0

        # Yield control so delivered messages are processed
        if state_changed:
            await asyncio.sleep(0)

    world._do_recordings()

    return SimulationResult(
        time_elapsed_s=actual_step,
        step_size_s=actual_step,
        messages_delivered=total_delivered,
    )


async def discrete_step_until(
    world: SimulationWorld,
    max_advance_time_s: float,
) -> list[SimulationResult]:
    """Run a discrete-event simulation until *max_advance_time_s* has elapsed.

    The simulation stops when the world clock has advanced by
    *max_advance_time_s* from its current position, or when there are no
    more events to process.

    :param world: the simulation world
    :param max_advance_time_s: maximum total time to simulate in seconds
    :return: list of :class:`SimulationResult` from each step

    Example::

        results = await discrete_step_until(world, max_advance_time_s=3600.0)
    """
    world._initialize_if_needed()
    start_time = world.clock.time
    max_time = start_time + max_advance_time_s
    results: list[SimulationResult] = []

    while world.clock.time < max_time:
        remaining = max_time - world.clock.time
        result = await step_simulation(
            world,
            step_size_s=DISCRETE_EVENT,
            max_advance_time_s=remaining,
        )
        if result is None:
            break
        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Data collection helpers
# ---------------------------------------------------------------------------


def collect_data(
    world: SimulationWorld,
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
    recording = world._get_world_recording(key)

    def _run(w: SimulationWorld) -> None:
        collector(w, recording)

    world._data_collectors.append(_run)


def collect_agent_data(
    world: SimulationWorld,
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
    recording = world._get_agents_recording(key)
    time_appended: list[bool] = [False]

    def _run(w: SimulationWorld) -> None:
        time_appended[0] = False
        for agent in w._agents.values():
            collector(w, agent, recording)
        if not time_appended[0]:
            recording.time.append(w.clock.time)
            time_appended[0] = True

    world._data_collectors.append(_run)


def record_world(
    world: SimulationWorld,
    key: str,
    recorder: Callable[[], Any],
) -> None:
    """Record a world-level scalar after every step.

    *recorder* is a zero-argument callable whose return value is appended to
    the recording's ``timeseries``.

    Example::

        record_world(world, "agent_count", lambda: len(world._agents))
    """
    recording = world._get_world_recording(key)

    def _run(w: SimulationWorld) -> None:
        recording.timeseries.append(recorder())
        recording.time.append(w.clock.time)

    world._data_collectors.append(_run)


def record_agent(
    world: SimulationWorld,
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
    recording = world._get_agents_recording(key)

    def _run(w: SimulationWorld) -> None:
        for agent in w._agents.values():
            if filter_fn is None or filter_fn(agent):
                recording.timeseries.setdefault(agent.aid, []).append(recorder(agent))
        recording.time.append(w.clock.time)

    world._data_collectors.append(_run)


def record_agent_having(
    world: SimulationWorld,
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
    recording = world._get_agents_recording(key)

    def _run(w: SimulationWorld) -> None:
        for agent in w._agents.values():
            if hasattr(agent, "roles") and any(
                isinstance(r, role_type) for r in agent.roles
            ):
                recording.timeseries.setdefault(agent.aid, []).append(recorder(agent))
        recording.time.append(w.clock.time)

    world._data_collectors.append(_run)


def record_position(
    world: SimulationWorld,
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
    recording = world._get_agents_recording(key)

    def _run(w: SimulationWorld) -> None:
        space = w.environment.space
        for agent in w._agents.values():
            if space.has_position(agent):
                if filter_fn is None or filter_fn(agent):
                    recording.timeseries.setdefault(agent.aid, []).append(
                        space.location(agent)
                    )
        recording.time.append(w.clock.time)

    world._data_collectors.append(_run)


def position_history(
    world: SimulationWorld,
    key: str = "positions",
) -> AgentsRecording:
    """Return the :class:`AgentsRecording` populated by :func:`record_position`.

    :param world: the simulation world
    :param key: recording key (default ``"positions"``)
    :return: the recording
    """
    return world._get_agents_recording(key)

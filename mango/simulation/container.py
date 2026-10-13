"""
SimulationContainer: the container implementation backing
:class:`~mango.simulation.world.SimulationWorld`.

Implements the mango container contract for a clock-driven simulation:
``send_message`` queues messages with a delivery time computed by the
communication simulation; the world delivers them while stepping.
"""

import asyncio
import bisect
import logging
import types
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..agent.core import Agent, AgentAddress
from ..container.core import Container
from ..messages.codecs import JSON
from ..util.clock import ExternalClock
from ..util.scheduling import _raise_exceptions
from ..util.seeding import SeedTree
from .communication import CommunicationSimulation, MessagePackage

logger = logging.getLogger(__name__)

SIMULATION_ADDR = "simulation"


def _send_when_due(
    future: asyncio.Future,
    send: Callable[[Any, AgentAddress, dict], bool],
    content: Any,
    receiver_addr: AgentAddress,
    kwargs: dict,
    scheduler: Any,
) -> None:
    """Event-loop callback of :meth:`SimulationContainer._send_soon`."""
    if future.cancelled():
        return
    try:
        result = send(content, receiver_addr, kwargs)
    except Exception as exc:
        future.set_exception(exc)
        # reported like the exception of a scheduled task (the world's
        # on_task_error hook, else a log record)
        owner = getattr(send, "__self__", None)
        label = types.SimpleNamespace(_coro=type(owner).send_message)
        _raise_exceptions(future, scheduler=scheduler, task=label)
    else:
        future.set_result(result)


@dataclass
class MessageTransaction:
    """Records a message that was delivered during the simulation."""

    sender_id: str | None
    receiver_id: str
    sent_time: float
    arriving_time: float
    content: Any


class SimulationContainer(Container):
    """Local container for agents living in a :class:`SimulationWorld`.

    Unlike networked containers there is no explicit ``start()`` phase:
    the container is running from construction on, and messages are held
    in a pending queue until the world steps the clock past their
    delivery time.

    Sending never suspends here, so
    :meth:`~mango.Agent.schedule_instant_message` takes a fast path: instead
    of one asyncio task per message it queues one event-loop callback that
    sends at the moment the task would have run (same order, same send time).

    :param record_messages: keep every delivered message (with its content)
        in :attr:`recorded_messages`; ``False`` only counts them in
        :attr:`messages_delivered`, so memory does not grow with the number of
        messages
    """

    def __init__(
        self,
        clock: ExternalClock,
        communication_sim: CommunicationSimulation,
        on_agent_registered: Callable[..., None] | None = None,
        seed: "int | SeedTree | Mapping | None" = None,
        record_messages: bool = True,
    ):
        super().__init__(
            addr=SIMULATION_ADDR,
            name=SIMULATION_ADDR,
            codec=JSON(),
            clock=clock,
            seed=seed,
        )
        self.communication_sim = communication_sim
        self._on_agent_registered = on_agent_registered
        self.running = True

        # Pending message queue: sorted list of (delivery_time, seq, sent_time, content, meta)
        # seq is a monotonically increasing tie-breaker so bisect.insort never
        # needs to compare content or meta (which may not be orderable).
        self._pending_messages: list[tuple[float, int, float, Any, dict]] = []
        self._msg_seq: int = 0
        #: incremented whenever an agent is registered or deregistered
        self.agents_version: int = 0

        #: whether delivered messages are kept in :attr:`recorded_messages`
        self.record_messages: bool = bool(record_messages)
        #: the delivered messages (only filled with ``record_messages=True``)
        self.recorded_messages: list[MessageTransaction] = []
        #: number of messages delivered so far (always counted)
        self.messages_delivered: int = 0

    def on_register(self, agent: Agent, aid: str, **kwargs) -> None:
        self.agents_version += 1
        if self._on_agent_registered is not None:
            self._on_agent_registered(agent, aid, **kwargs)

    def deregister(self, aid: str) -> None:
        agent = self._agents.pop(aid, None)
        if agent is not None:
            self.agents_version += 1
            self._activity.detach(agent)

    async def send_message(
        self,
        content: Any,
        receiver_addr: AgentAddress,
        sender_id: str | None = None,
        **kwargs,
    ) -> bool:
        """Send a message, applying communication simulation.

        Messages are queued with a delivery time determined by the
        communication simulation.  They are delivered during the next
        simulation step.
        """
        return self._enqueue_message(content, receiver_addr, sender_id, kwargs)

    def _enqueue_message(
        self,
        content: Any,
        receiver_addr: AgentAddress,
        sender_id: str | None,
        kwargs: dict,
    ) -> bool:
        """Queue a message now (the body of :meth:`send_message`, which never suspends)."""
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
        seq = self._msg_seq
        self._msg_seq += 1
        pending = self._pending_messages
        entry = (delivery_time, seq, sent_time, content, meta)
        if not pending or pending[-1][0] <= delivery_time:
            pending.append(entry)  # the common case: due last (seq grows)
        else:
            bisect.insort(pending, entry)
        return True

    def _send_soon(
        self,
        send: Callable[[Any, AgentAddress, dict], bool],
        content: Any,
        receiver_addr: AgentAddress,
        kwargs: dict,
        scheduler: Any,
    ) -> asyncio.Future:
        """Call ``send(content, receiver_addr, kwargs)`` in the next loop turn.

        The fast path of ``schedule_instant_message``: an
        ``schedule_instant_task(send_message(...))`` task would run its whole
        coroutine in its first turn (sending here never suspends), so one
        callback queued at the same point sends the message at the same moment
        and in the same order, without a task, its wrapper and done callbacks.
        An exception is reported through *scheduler* like a task's.

        :return: a future resolved with the result of *send*
        """
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        loop.call_soon(
            _send_when_due, future, send, content, receiver_addr, kwargs, scheduler
        )
        return future

    async def _deliver_messages_due(self, up_to_time: float) -> int:
        """Deliver all pending messages with delivery_time <= up_to_time.

        Returns the number of messages delivered.
        """
        # the queue is sorted by delivery time: take the due head only
        pending = self._pending_messages
        due_count = 0
        while due_count < len(pending) and pending[due_count][0] <= up_to_time:
            due_count += 1
        if due_count == 0:
            return 0
        due = pending[:due_count]
        del pending[:due_count]

        delivered = 0
        agents = self._agents
        recorded = self.recorded_messages if self.record_messages else None
        for delivery_time, _seq, sent_time, content, meta in due:
            receiver_id = meta.get("receiver_id")
            agent = agents.get(receiver_id)
            if agent is not None:
                inbox = agent.inbox
                if inbox.full():  # a bounded queue swapped in by the user
                    await inbox.put((0, content, meta))
                else:  # what ``await put`` does without suspending
                    inbox.put_nowait((0, content, meta))
                if recorded is not None:
                    recorded.append(
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
                logger.warning("Unknown receiver '%s'; dropping message", receiver_id)
        self.messages_delivered += delivered
        return delivered

    def _determine_next_step_size(self) -> float | None:
        """Return seconds to the next scheduled event, or None if none."""
        candidates: list[float] = []

        if self._pending_messages:
            next_msg_arrival = self._pending_messages[0][0]
            candidates.append(max(0.0, next_msg_arrival - self.clock.time))

        next_task = self.clock.get_next_activity()
        if next_task is not None:
            candidates.append(max(0.0, next_task - self.clock.time))

        if not candidates:
            return None
        return min(candidates)

    async def as_agent_process(self, agent_creator, mirror_container_creator=None):
        raise NotImplementedError(
            "Agent subprocesses are not supported in a simulation container"
        )

    def as_agent_process_lazy(self, agent_creator, mirror_container_creator=None):
        raise NotImplementedError(
            "Agent subprocesses are not supported in a simulation container"
        )

    async def shutdown(self) -> None:
        """Shut down all agents."""
        self.running = False
        for agent in list(self._agents.values()):
            try:
                await agent.shutdown()
            except Exception:
                logger.exception("Error shutting down agent '%s'", agent.aid)

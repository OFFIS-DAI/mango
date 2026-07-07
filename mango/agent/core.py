"""
This module implements the base class for agents (:class:`Agent`).

Every agent must live in a container. Containers are responsible for making
 connections to other agents.
"""

import asyncio
import logging
import uuid
from abc import ABC
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..messages.message import AgentAddress
from ..util.clock import Clock
from ..util.scheduling import ScheduledProcessTask, ScheduledTask, Scheduler

logger = logging.getLogger(__name__)


@dataclass
class AgentDescription:
    """Metadata describing an agent (name, category, color, unique ID).

    Mirrors the ``AgentDescription`` type in Mango.jl.
    """

    name: str = ""
    category: str = "agent"
    color: str = "gray"
    uid: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class ForwardingRule:
    """Rule for automatic message forwarding.

    When the agent receives a message from *from_addr* it is automatically
    forwarded to *to_addr*.  If *forward_replies* is ``True``, replies from
    *to_addr* are forwarded back to *from_addr*.
    """

    from_addr: AgentAddress
    to_addr: AgentAddress
    forward_replies: bool = False


class State(Enum):
    NORMAL = 0
    INACTIVE = 1
    BROKEN = 2
    UNKNOWN = 3
    EXT_CONNECTION = 4


class TopologyNeighbor:
    """A neighbor in the topology graph.

    Addresses are resolved lazily so that topologies can be built before
    agents are registered in a container.

    :param agent: the neighbor agent (address resolved at access time)
    :param description: the agent's :class:`AgentDescription`
    :param characteristic: an optional label for the agent's role in its node
        (e.g. ``"leader"``); empty string means no characteristic
    """

    def __init__(
        self,
        agent: Any,
        description: "AgentDescription",
        characteristic: str = "",
    ) -> None:
        self._agent = agent
        self.description = description
        self.characteristic = characteristic

    @property
    def address(self) -> "AgentAddress":
        """The neighbor's current :class:`AgentAddress` (resolved lazily)."""
        return self._agent.addr


class TopologyService:
    """Stores neighborhood data injected by the topology system.

    An instance is attached to each agent via
    :meth:`~mango.agent.core.AgentDelegates.service_of_type`.
    """

    def __init__(self) -> None:
        self._tid_to_state_to_neighbors: dict[
            str, dict[State, list[TopologyNeighbor]]
        ] = {}
        self._tid_to_node_id: dict[str, int] = {}
        self._tid_to_characteristic: dict[str, str] = {}
        self._tid_to_connectors: dict[str, list[tuple[str, TopologyNeighbor]]] = {}
        self._marked_connector_for: list[str] = []

    def neighbors(
        self,
        state: State = State.NORMAL,
        *,
        tid: str = "default",
        has_characteristic: str | None = None,
        include_connectors: tuple[str, ...] | list[str] = (),
        match_func: Any = None,
    ) -> list["AgentAddress"]:
        """Return addresses of neighbors in topology *tid* with edge *state*.

        :param state: only return neighbors reachable via edges in this state
        :param tid: topology identifier
        :param has_characteristic: if given, only neighbors with this characteristic
        :param include_connectors: also include connectors of these connection types
        :param match_func: optional predicate ``(AgentDescription) -> bool``
        """
        state_map = self._tid_to_state_to_neighbors.get(tid, {})
        result: list[AgentAddress] = []
        for n in state_map.get(state, []):
            if (
                has_characteristic is not None
                and n.characteristic != has_characteristic
            ):
                continue
            if match_func is not None and not match_func(n.description):
                continue
            result.append(n.address)
        for conn_type, n in self._tid_to_connectors.get(tid, []):
            if conn_type in include_connectors:
                if match_func is None or match_func(n.description):
                    result.append(n.address)
        return result

    def node_id(self, tid: str = "default") -> int:
        """Return the node ID this agent occupies in topology *tid*."""
        if tid not in self._tid_to_node_id:
            raise KeyError(f"No node ID registered for topology '{tid}'")
        return self._tid_to_node_id[tid]

    def characteristic(self, tid: str = "default") -> str:
        """Return this agent's characteristic label in topology *tid*."""
        return self._tid_to_characteristic.get(tid, "")

    def connectors(
        self,
        tid: str = "default",
        *,
        include_connectors: tuple[str, ...] | list[str] = (),
        match_func: Any = None,
    ) -> list["AgentAddress"]:
        """Return addresses of connector agents for topology *tid*."""
        result: list[AgentAddress] = []
        for conn_type, n in self._tid_to_connectors.get(tid, []):
            if include_connectors and conn_type not in include_connectors:
                continue
            if match_func is not None and not match_func(n.description):
                continue
            result.append(n.address)
        return result

    def connection_types(self, tid: str = "default") -> list[str]:
        """Return the connection type labels for connectors in topology *tid*."""
        return [ct for ct, _ in self._tid_to_connectors.get(tid, [])]


class AgentContext:
    def __init__(self, container) -> None:
        self._container = container
        self._services = {}

    @property
    def current_timestamp(self) -> float:
        """
        Method that returns the current unix timestamp given the clock within the container
        """
        return self._container.clock.time

    @property
    def clock(self) -> Clock:
        return self._container.clock

    @property
    def addr(self):
        return self._container.addr

    def register(self, agent, suggested_aid):
        return self._container.register(agent, suggested_aid=suggested_aid)

    def deregister(self, aid):
        if self._container.running:
            self._container.deregister(aid)

    def service_of_type(self, type: type, default: Any = None) -> Any:
        if type not in self._services:
            self._services[type] = type() if default is None else default
        return self._services[type]

    async def send_message(
        self,
        content,
        receiver_addr: AgentAddress,
        sender_id: None | str = None,
        **kwargs,
    ) -> bool:
        """
        See container.send_message(...)
        """
        return await self._container.send_message(
            content, receiver_addr=receiver_addr, sender_id=sender_id, **kwargs
        )


class AgentDelegates:
    def __init__(self) -> None:
        self.context: AgentContext = None
        self.scheduler: Scheduler = None
        self._aid = None
        self._description: AgentDescription = AgentDescription()
        self._forwarding_rules: list[ForwardingRule] = []
        self._transaction_handlers: dict[str, tuple] = {}
        self._behavior_message_subs: list[tuple] = []
        self._behavior_global_event_handlers: list[tuple] = []
        self._behavior_agent_event_handlers: list[tuple] = []

    def on_start(self):
        """Called when container started in which the agent is contained"""

    def on_ready(self):
        """Called when all container has been started using activate(...)."""

    def on_step(self, env, clock, step_size_s: float) -> None:
        """Called on every simulation step (only in SimulationWorld).

        :param env: the simulation environment
        :param clock: the current simulation clock
        :param step_size_s: seconds advanced in this step
        """

    def on_global_event(self, event: Any) -> None:
        """Called when a global event is emitted from the environment.

        Override to react to environment-wide broadcasts.

        :param event: the event object
        """

    def on_agent_event(self, event: Any) -> None:
        """Called when a targeted agent event is emitted.

        Override to react to events directed at this specific agent.

        :param event: the event object
        """

    # ------------------------------------------------------------------
    # Agent description helpers
    # ------------------------------------------------------------------

    @property
    def description(self) -> AgentDescription:
        """Return the agent's :class:`AgentDescription`."""
        return self._description

    @property
    def name(self) -> str:
        """Human-readable name of this agent."""
        return self._description.name

    @property
    def color(self) -> str:
        """Visual color tag for this agent."""
        return self._description.color

    @property
    def category(self) -> str:
        """Category tag for this agent."""
        return self._description.category

    @property
    def uid(self) -> str:
        """Unique identifier (UUID string) of this agent."""
        return self._description.uid

    def update_description(
        self,
        name: str | None = None,
        color: str | None = None,
        category: str | None = None,
    ) -> None:
        """Update one or more description fields.

        :param name: new human-readable name
        :param color: new color tag
        :param category: new category tag
        """
        if name is not None:
            self._description.name = name
        if color is not None:
            self._description.color = color
        if category is not None:
            self._description.category = category

    # ------------------------------------------------------------------
    # Forwarding rules
    # ------------------------------------------------------------------

    def add_forwarding_rule(
        self,
        from_addr: AgentAddress,
        to_addr: AgentAddress,
        forward_replies: bool = False,
    ) -> None:
        """Add an automatic message-forwarding rule.

        After calling this, every message received from *from_addr* is
        automatically forwarded to *to_addr*.  When *forward_replies* is
        ``True``, replies originating from *to_addr* are forwarded back to
        *from_addr*.

        :param from_addr: source address to match
        :param to_addr: destination to forward to
        :param forward_replies: whether replies should be forwarded back
        """
        self._forwarding_rules.append(
            ForwardingRule(
                from_addr=from_addr, to_addr=to_addr, forward_replies=forward_replies
            )
        )

    def delete_forwarding_rule(
        self,
        from_addr: AgentAddress,
        to_addr: AgentAddress | None = None,
    ) -> None:
        """Remove previously added forwarding rule(s).

        :param from_addr: source address of the rule to remove
        :param to_addr: if given, only remove rules that also match this
            destination; otherwise remove all rules matching *from_addr*
        """
        self._forwarding_rules = [
            r
            for r in self._forwarding_rules
            if not (
                r.from_addr == from_addr and (to_addr is None or r.to_addr == to_addr)
            )
        ]

    # ------------------------------------------------------------------
    # Tracked / reply-to messaging
    # ------------------------------------------------------------------

    async def send_tracked_message(
        self,
        content: Any,
        receiver_addr: AgentAddress,
        response_handler=None,
        **kwargs,
    ):
        """Send a message and optionally register a response handler.

        A ``tracking_id`` is attached to the message so that the reply can
        be matched.  When *response_handler* is provided it will be called as
        ``response_handler(reply_content, reply_meta)`` when the matching
        reply arrives.

        :param content: message content
        :param receiver_addr: target agent address
        :param response_handler: optional ``(content, meta) -> None`` callback
        :return: the asyncio.Task for the sent message
        """
        tracking_id = str(uuid.uuid4())
        if response_handler is not None:
            self._transaction_handlers[tracking_id] = (response_handler,)
        return await self.send_message(
            content,
            receiver_addr=receiver_addr,
            tracking_id=tracking_id,
            **kwargs,
        )

    async def reply_to(
        self,
        content: Any,
        received_meta: dict,
        **kwargs,
    ) -> bool:
        """Convenience helper to reply to a received message.

        Extracts the sender address from *received_meta* and sends *content*
        back, preserving any ``tracking_id`` for transaction matching.

        :param content: reply content
        :param received_meta: the ``meta`` dict from the received message
        :return: result of :meth:`send_message`
        """
        sender_id = received_meta.get("sender_id")
        sender_addr = received_meta.get("sender_addr")
        tracking_id = received_meta.get("tracking_id")
        reply_addr = AgentAddress(protocol_addr=sender_addr, aid=sender_id)
        extra: dict = {"reply": True}
        if tracking_id:
            extra["tracking_id"] = tracking_id
        extra.update(kwargs)
        return await self.send_message(content, receiver_addr=reply_addr, **extra)

    def _handle_tracked_reply(self, content: Any, meta: dict) -> bool:
        """Internal: check if *meta* contains a tracked reply; call handler.

        Returns ``True`` if the message was handled as a tracked reply.
        """
        tracking_id = meta.get("tracking_id")
        if (
            tracking_id
            and meta.get("reply")
            and tracking_id in self._transaction_handlers
        ):
            (handler,) = self._transaction_handlers.pop(tracking_id)
            handler(content, meta)
            return True
        return False

    @property
    def current_timestamp(self) -> float:
        """
        Method that returns the current unix timestamp given the clock within the container
        """
        return self.context.current_timestamp

    @property
    def aid(self):
        return self._aid

    @property
    def addr(self):
        """Return the address of the agent as AgentAddress

        Returns:
            _type_: AgentAddress
        """
        if self.context._container is None:
            return None
        return AgentAddress(self.context.addr, self.aid)

    async def send_message(
        self,
        content,
        receiver_addr: AgentAddress,
        **kwargs,
    ) -> bool:
        """
        See container.send_message(...)
        """
        return await self.context.send_message(
            content, receiver_addr=receiver_addr, sender_id=self.aid, **kwargs
        )

    async def send_messages(
        self,
        content,
        receiver_addrs: list[AgentAddress],
        **kwargs,
    ) -> list[bool]:
        """Send the same content to multiple recipients.

        :param content: message content (sent to every recipient)
        :param receiver_addrs: list of target :class:`AgentAddress` instances
        :return: list of send results (one per recipient, in order)
        """
        results = []
        for addr in receiver_addrs:
            results.append(
                await self.send_message(content, receiver_addr=addr, **kwargs)
            )
        return results

    def schedule_instant_message(
        self,
        content,
        receiver_addr: AgentAddress,
        **kwargs,
    ):
        """
        Schedules sending a message without any delay. This is equivalent to using the schedulers 'schedule_instant_task' with the coroutine created by
        'container.send_message'.

        :param content: The content of the message
        :param receiver_addr: The address passed to the container
        :param kwargs: Additional parameters to provide protocol specific settings
        :returns: asyncio.Task for the scheduled coroutine
        """

        return self.schedule_instant_task(
            self.send_message(content, receiver_addr=receiver_addr, **kwargs)
        )

    def schedule_conditional_process_task(
        self,
        coroutine_creator,
        condition_func,
        lookup_delay=0.1,
        on_stop=None,
        src=None,
    ):
        """Schedule a process task when a specified condition is met.

        :param coroutine_creator: coroutine_creator creating coroutine to be scheduled
        :type coroutine_creator: coroutine_creator
        :param condition_func: function for determining whether the confition is fullfiled
        :type condition_func: lambda () -> bool
        :param lookup_delay: delay between checking the condition
        :type lookup_delay: float
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_conditional_process_task(
            coroutine_creator=coroutine_creator,
            condition_func=condition_func,
            lookup_delay=lookup_delay,
            on_stop=on_stop,
            src=src,
        )

    def schedule_conditional_task(
        self, coroutine, condition_func, lookup_delay=0.1, on_stop=None, src=None
    ):
        """Schedule a task when a specified condition is met.

        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param condition_func: function for determining whether the confition is fullfiled
        :type condition_func: lambda () -> bool
        :param lookup_delay: delay between checking the condition
        :type lookup_delay: float
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_conditional_task(
            coroutine=coroutine,
            condition_func=condition_func,
            lookup_delay=lookup_delay,
            on_stop=on_stop,
            src=src,
        )

    def schedule_timestamp_task(
        self, coroutine, timestamp: float, on_stop=None, src=None
    ):
        """Schedule a task at specified  unix timestamp.

        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param timestamp: timestamp defining when the task should start
        :type timestamp: timestamp
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_timestamp_task(
            coroutine=coroutine, timestamp=timestamp, on_stop=on_stop, src=src
        )

    def schedule_timestamp_process_task(
        self, coroutine_creator, timestamp: float, on_stop=None, src=None
    ):
        """Schedule a task at specified unix timestamp dispatched to another process.

        :param coroutine_creator: coroutine_creator creating coroutine to be scheduled
        :type coroutine_creator: coroutine_creator
        :param timestamp: unix timestamp defining when the task should start
        :type timestamp: float
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_timestamp_process_task(
            coroutine_creator=coroutine_creator,
            timestamp=timestamp,
            on_stop=on_stop,
            src=src,
        )

    def schedule_periodic_process_task(
        self, coroutine_creator, delay, on_stop=None, src=None
    ):
        """Schedule an open end periodically executed task in another process.

        :param coroutine_creator: coroutine function creating coros to be scheduled
        :type coroutine_creator:  Coroutine Function
        :param delay: delay in between the cycles
        :type delay: float
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_periodic_process_task(
            coroutine_creator=coroutine_creator, delay=delay, on_stop=on_stop, src=src
        )

    def schedule_periodic_task(self, coroutine_func, delay, on_stop=None, src=None):
        """Schedule an open end peridocally executed task.

        :param coroutine_func: coroutine function creating coros to be scheduled
        :type coroutine_func:  Coroutine Function
        :param delay: delay in between the cycles
        :type delay: float
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_periodic_task(
            coroutine_func=coroutine_func, delay=delay, on_stop=on_stop, src=src
        )

    def schedule_recurrent_process_task(
        self, coroutine_creator, recurrency, on_stop=None, src=None
    ):
        """Schedule a task using a fine-grained recurrency rule in another process.

        :param coroutine_creator: coroutine function creating coros to be scheduled
        :type coroutine_creator:  Coroutine Function
        :param recurrency: recurrency rule to calculate next event
        :type recurrency: dateutil.rrule.rrule
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_recurrent_process_task(
            coroutine_creator=coroutine_creator,
            recurrency=recurrency,
            on_stop=on_stop,
            src=src,
        )

    def schedule_recurrent_task(
        self, coroutine_func, recurrency, on_stop=None, src=None
    ):
        """Schedule a task using a fine-grained recurrency rule in another process.

        :param coroutine_creator: coroutine function creating coros to be scheduled
        :type coroutine_creator:  Coroutine Function
        :param recurrency: recurrency rule to calculate next event
        :type recurrency: dateutil.rrule.rrule
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_recurrent_task(
            coroutine_func=coroutine_func,
            recurrency=recurrency,
            on_stop=on_stop,
            src=src,
        )

    def schedule_instant_process_task(self, coroutine_creator, on_stop=None, src=None):
        """Schedule an instantly executed task in another processes.

        :param coroutine_creator: coroutine_creator creating coroutine to be scheduled
        :type coroutine_creator:
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_instant_process_task(
            coroutine_creator=coroutine_creator, on_stop=on_stop, src=src
        )

    def schedule_instant_task(self, coroutine, on_stop=None, src=None):
        """Schedule an instantly executed task.

        :param coroutine: coroutine to be scheduled
        :type coroutine:
        :param on_stop: coroutine to run on stop
        :type on_stop: Object
        :param src: creator of the task
        :type src: Object
        """
        return self.scheduler.schedule_instant_task(
            coroutine=coroutine, on_stop=on_stop, src=src
        )

    def schedule_process_task(self, task: ScheduledProcessTask, src=None):
        """Schedule a task with asyncio in another process. When the task is finished, if finite, its automatically
        removed afterwards. For scheduling options see the subclasses of ScheduledScheduledProcessTaskTask.

        :param task: task to be scheduled
        :param src: object, which represents the source of the task (for example the object in which the task got created)
        """
        return self.scheduler.schedule_process_task(task, src=src)

    def schedule_task(self, task: ScheduledTask, src=None):
        """Schedule a task with asyncio. When the task is finished, if finite, its automatically
        removed afterwards. For scheduling options see the subclasses of ScheduledTask.

        :param task: task to be scheduled
        :param src: object, which represents the source of the task (for example the object in which the task got created)
        """
        return self.scheduler.schedule_task(task, src=src)

    async def tasks_complete(self, timeout=1):
        """Wait for all scheduled tasks to complete using a timeout.

        :param timeout: waiting timeout. Defaults to 1.
        """
        await self.scheduler.tasks_complete(timeout=timeout)

    def service_of_type(self, type: type, default: Any = None) -> Any:
        """Return the service registered for ``type``, creating it if absent.

        If no service of ``type`` is registered yet, *default* is registered
        and returned; when *default* is ``None`` a new ``type()`` instance is
        created instead.

        :param type: the type of the service
        :type type: type
        :param default: the value to register if none exists; ``None`` creates
            a ``type()`` instance
        :type default: Any (optional)
        :return: the service
        :rtype: Any
        """
        return self.context.service_of_type(type, default=default)

    def neighbors(
        self,
        state: State = State.NORMAL,
        *,
        tid: str = "default",
        has_characteristic: str | None = None,
        include_connectors: tuple | list = (),
        match_func=None,
    ) -> list[AgentAddress]:
        """Return neighbor addresses from the topology.

        :param state: filter by edge state (default :attr:`~mango.State.NORMAL`)
        :param tid: topology identifier (default ``"default"``)
        :param has_characteristic: only include neighbors with this characteristic
        :param include_connectors: also include connector agents of these types
        :param match_func: optional ``(AgentDescription) -> bool`` predicate
        :return: list of :class:`~mango.AgentAddress`
        """
        svc = self.service_of_type(TopologyService)
        return svc.neighbors(
            state,
            tid=tid,
            has_characteristic=has_characteristic,
            include_connectors=include_connectors,
            match_func=match_func,
        )


class Agent(ABC, AgentDelegates):
    """Base class for all agents."""

    def __init__(
        self,
    ):
        """
        Initialize an agent
        """

        super().__init__()

        self.inbox = asyncio.Queue()
        self.context = AgentContext(None)

    @property
    def observable_tasks(self):
        return self.scheduler.observable

    @observable_tasks.setter
    def observable_tasks(self, value: bool):
        self.scheduler.observable = value

    @property
    def suspendable_tasks(self):
        return self.scheduler.suspendable

    @suspendable_tasks.setter
    def suspendable_tasks(self, value: bool):
        self.scheduler.suspendable = value

    def on_register(self):
        """
        Hook-in to define behavior of the agent directly after it got registered by a container
        """

    def _do_register(self, container, aid):
        self._aid = aid
        self.context._container = container
        self.scheduler = Scheduler(
            suspendable=True, observable=True, clock=container.clock
        )
        # Populate description aid if not yet set
        if not self._description.name:
            self._description.name = aid
        self.on_register()

    def _do_start(self):
        self._check_inbox_task = asyncio.create_task(self._check_inbox())
        self._check_inbox_task.add_done_callback(self._raise_exceptions)
        self._stopped = asyncio.Future()

        self.on_start()

    def _raise_exceptions(self, fut: asyncio.Future):
        """
        Inline function used as a callback to raise exceptions
        :param fut: The Future object of the task
        """
        try:
            if fut.exception() is not None:
                logger.error(
                    "Agent %s: Caught the following exception in _check_inbox: ",
                    self.aid,
                    fut.exception(),
                )
                raise fut.exception()
        except asyncio.CancelledError:
            pass

    async def _check_inbox(self):
        """Task for waiting on new message in the inbox"""

        try:
            logger.debug("Agent %s: Start waiting for messages", self.aid)
            while True:
                # run in infinite loop until it is cancelled from outside
                message = await self.inbox.get()
                logger.debug("Agent %s: Received message;%s", self.aid, message)

                # message should be tuples of (priority, content, meta)
                priority, content, meta = message
                meta["priority"] = priority

                # Check forwarding rules
                forwarded = self._check_forwarding_rules(content, meta)

                if not forwarded:
                    # Check tracked reply handlers
                    if not self._handle_tracked_reply(content, meta):
                        for _cond, _handler, _proc in self._behavior_message_subs:
                            if _cond(content, meta):
                                if _proc is not None:
                                    _proc.handle(
                                        self,
                                        lambda c, m, h=_handler: h(self, c, m),
                                        content,
                                        meta,
                                    )
                                else:
                                    _handler(self, content, meta)
                        self.handle_message(content=content, meta=meta)

                # signal to the Queue that the message is handled
                self.inbox.task_done()
        except Exception:
            logger.exception("The check inbox task of %s failed!", self.aid)

    def _check_forwarding_rules(self, content: Any, meta: dict) -> bool:
        """Apply forwarding rules; returns True if a rule matched."""
        sender_id = meta.get("sender_id")
        sender_addr = meta.get("sender_addr")

        for rule in self._forwarding_rules:
            # Forward if message comes from the rule's from_addr
            if (
                rule.from_addr.aid == sender_id
                and rule.from_addr.protocol_addr == sender_addr
            ):
                self.schedule_instant_task(
                    self.send_message(
                        content,
                        receiver_addr=rule.to_addr,
                        forwarded=True,
                        forwarded_from_id=sender_id,
                        forwarded_from_addr=sender_addr,
                    )
                )
                return True
            # Forward replies back if forward_replies is enabled
            if (
                rule.forward_replies
                and rule.to_addr.aid == sender_id
                and rule.to_addr.protocol_addr == sender_addr
                and meta.get("reply")
                and meta.get("forwarded_from_id")
            ):
                from_id = meta.get("forwarded_from_id")
                from_addr_str = meta.get("forwarded_from_addr")
                orig_addr = AgentAddress(protocol_addr=from_addr_str, aid=from_id)
                self.schedule_instant_task(
                    self.send_message(
                        content,
                        receiver_addr=orig_addr,
                        reply=True,
                        tracking_id=meta.get("tracking_id"),
                    )
                )
                return True
        return False

    def handle_message(self, content, meta: dict[str, Any]):
        """
        Has to be implemented by the user.
        This method is called when a message is received at the agents inbox.
        :param content: The deserialized message object
        :param meta: Meta details of the message. In case of mqtt this dict
        includes at least the field 'topic'
        """
        raise NotImplementedError

    async def on_stop(self):
        """Can be used as lifecycle callback when the agent is stopped"""

    async def shutdown(self):
        """Shutdown all tasks that are running
        and deregister from the container"""
        await self.on_stop()

        if not self._stopped.done():
            self._stopped.set_result(True)
        self.context.deregister(self.aid)
        try:
            # Shutdown reactive inbox task
            self._check_inbox_task.remove_done_callback(self._raise_exceptions)
            self._check_inbox_task.cancel()
            await self._check_inbox_task
        except asyncio.CancelledError:
            pass
        try:
            await self.scheduler.shutdown()
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("Agent %s: Shutdown successful", self.aid)

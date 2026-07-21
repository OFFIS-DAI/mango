"""Multi-hop conversation primitive.

A *conversation* groups a sequence of messages that share a single id
so participants can volley back and forth without each reply being
consumed on receipt (the way ``tracking_id`` is in
:meth:`AgentDelegates.send_tracked_message`).  Use it for protocols
that span many hops — gossip, auctions, holonic ADMM coordination.

The handle is an async context manager that yields a
:class:`Conversation` carrying the id, a user-controlled state dict,
and a receive queue::

    async with self.open_conversation(
        timeout=10.0,
        state={"target": -5.0, "delta": 0.0},
    ) as conv:
        await conv.send(neighbour, GossipStep(...))
        async for msg, meta in conv:
            update(conv.state, msg)
            if conv.state["delta"] >= conv.state["target"]:
                conv.converge()
                continue
            await conv.send(pick_next_hop(), GossipStep(...))

(Inside a role, use ``self.context.open_conversation`` — the API is
available on plain agents and role contexts alike.)

Responders join the existing exchange via the inbound ``meta``::

    @on_message(GossipStep)
    async def on_step(self, msg, meta):
        async with self.context.join_conversation(meta) as conv:
            ...

Every join owns an independent handle with its own ``state`` and
``timeout``; when several handles for the same id are open on one
agent (e.g. an ``@on_message`` handler that re-fires while an earlier
join is still open), each inbound message is delivered to *all* of
them.  Note that a conversation message is also dispatched to matching
``@on_message`` handlers as usual — the iterator receives it *in
addition to*, not instead of, normal dispatch.

Two control methods end the iteration:

* :meth:`Conversation.converge` — graceful: any messages already on
  the queue still deliver, then the iterator exits.
* :meth:`Conversation.cancel` — abrupt: queued messages are discarded
  and the iterator exits on the next pull.  Also called when the
  clock-aware timeout fires and when the context manager exits.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from mango.util.scheduling import sleeping_wait

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mango.agent.core import AgentDelegates
    from mango.messages.message import AgentAddress
    from mango.util.clock import Clock

logger = logging.getLogger(__name__)

# Carried alongside ``tracking_id`` because the latter is consumed by
# the single-shot reply machinery; a conversation message may legitimately
# bear both (e.g. when a participant calls ``reply_to`` inside a session —
# ``reply_to`` echoes the conversation id automatically).
CONVERSATION_ID_KEY = "conversation_id"


class Conversation:
    """A live, agent-owned conversation handle.

    The ``state`` dict is user-controlled — mango never reads it.
    """

    # Queue sentinel that signals end-of-iteration.  A class-level
    # singleton so identity checks are unambiguous.  Once enqueued it is
    # re-enqueued by every consumer that reads it, so it acts as a latch:
    # any number of concurrent or later iterators terminate.
    _END = object()

    def __init__(
        self,
        *,
        owner: AgentDelegates,
        conversation_id: str,
        state: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> None:
        self._owner = owner
        self._conversation_id = conversation_id
        self.state: dict[str, Any] = dict(state) if state else {}
        self._timeout = timeout
        self._queue: asyncio.Queue = asyncio.Queue()
        self._converged = False
        self._cancelled = False

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @property
    def timeout(self) -> float | None:
        """Scheduler-clock timeout this handle was opened with, if any."""
        return self._timeout

    @property
    def closed(self) -> bool:
        return self._converged or self._cancelled

    def converge(self) -> None:
        """Signal graceful end-of-iteration.

        Messages already on the queue still deliver; the iterator exits
        once the queue drains.  Subsequent inbound messages are dropped.
        Idempotent.
        """
        if self.closed:
            return
        self._converged = True
        self._queue.put_nowait((self._END, None))

    def cancel(self) -> None:
        """Signal abrupt end-of-iteration.

        Drops any queued messages and ends the iterator on the next
        pull.  Also called when the timeout fires and when the
        conversation's context manager exits.  Idempotent.
        """
        if self._cancelled:
            return
        self._cancelled = True
        # Drain anything already queued so a consumer blocked inside
        # ``queue.get()`` receives the sentinel next, not a stale message —
        # honouring the "drops queued messages" contract even mid-await.
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._queue.put_nowait((self._END, None))

    async def send(self, receiver_addr: AgentAddress, content: Any, **kwargs) -> bool:
        """Send *content* tagged with this conversation's id.

        The tag always wins over a ``conversation_id`` kwarg.  When
        *content* itself has a ``conversation_id`` attribute (e.g. an
        ACL message), it is stamped too — container transports send such
        content without the surrounding meta, so the id must travel
        inside the message to survive a cross-container hop.
        """
        if hasattr(content, CONVERSATION_ID_KEY):
            try:
                setattr(content, CONVERSATION_ID_KEY, self._conversation_id)
            except AttributeError:
                logger.warning(
                    "Conversation %s: content of type %s has an immutable "
                    "conversation_id attribute; the id travels only in meta "
                    "and will not survive transports that drop it.",
                    self._conversation_id,
                    type(content).__name__,
                )
        return await self._owner.send_message(
            content,
            receiver_addr=receiver_addr,
            **{**kwargs, CONVERSATION_ID_KEY: self._conversation_id},
        )

    async def broadcast(
        self, receivers: Iterable[AgentAddress], content: Any, **kwargs
    ) -> dict[AgentAddress, bool]:
        """Send *content* to every address in *receivers*.

        :return: send result per receiver, keyed by address.
        """
        return {addr: await self.send(addr, content, **kwargs) for addr in receivers}

    # -- agent-facing hook --------------------------------------------
    def _on_inbound(self, content: Any, meta: dict) -> None:
        """Called by the agent's router when a matching id arrives."""
        if self.closed:
            logger.debug(
                "Dropping inbound message for closed conversation %s",
                self._conversation_id,
            )
            return
        self._queue.put_nowait((content, meta))

    # -- iteration ----------------------------------------------------
    def __aiter__(self):
        return self

    async def __anext__(self):
        # cancel() is abrupt: anything still on the queue is dropped.
        if self._cancelled:
            raise StopAsyncIteration
        # Only an inbound message (or converge/cancel/timeout) feeds the
        # queue, so a parked iterator counts as sleeping for stepped
        # simulations — the message can only arrive in a later step.
        with sleeping_wait():
            content, meta = await self._queue.get()
        if content is self._END:
            # Re-enqueue the sentinel so every other consumer — blocked
            # concurrently or arriving later — terminates as well.
            self._queue.put_nowait((self._END, None))
            raise StopAsyncIteration
        return content, meta


class _ConversationContext:
    """Async context manager returned by ``open_conversation`` and
    ``join_conversation`` (see :class:`mango.agent.core.AgentDelegates`).

    Owns the lifecycle of one :class:`Conversation`:

    * ``__aenter__`` registers the handle with the owning agent so
      inbound messages route to it, and arms the optional clock-aware
      timeout.
    * ``__aexit__`` disarms the timeout, unregisters the handle, and
      cancels it — an iterator that escaped the block terminates instead
      of waiting on a queue that can never be fed again.  ``conv.state``
      stays readable after exit.
    """

    def __init__(self, *, owner: AgentDelegates, conv: Conversation) -> None:
        self._owner = owner
        self._conv = conv
        self._timeout_task: asyncio.Task | None = None

    async def __aenter__(self) -> Conversation:
        agent = self._owner._bound_agent("Conversation")
        agent._register_conversation(self._conv)
        # The timeout runs on the agent's scheduler clock so it respects
        # simulation time under an ExternalClock.
        timeout = self._conv.timeout
        if timeout is not None:
            clock = agent.scheduler.clock if agent.scheduler else None
            if clock is None:
                logger.warning(
                    "Conversation %s: timeout=%s requested but the agent has "
                    "no scheduler clock — the timeout will not be enforced.",
                    self._conv.conversation_id,
                    timeout,
                )
            else:
                self._timeout_task = asyncio.ensure_future(
                    self._cancel_after(clock, timeout)
                )
        return self._conv

    async def __aexit__(self, exc_type, exc, tb):
        if self._timeout_task is not None and not self._timeout_task.done():
            self._timeout_task.cancel()
        try:
            agent = self._owner._bound_agent("Conversation")
        except RuntimeError:
            agent = None
        if agent is not None:
            agent._unregister_conversation(self._conv)
        self._conv.cancel()
        return False

    async def _cancel_after(self, clock: Clock, delay: float) -> None:
        try:
            await clock.sleep(delay)
        except asyncio.CancelledError:
            return
        # Timeout == abrupt close: anything still queued is dropped,
        # but conv.state remains readable after the context exits.
        self._conv.cancel()

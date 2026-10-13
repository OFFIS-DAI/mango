"""Detect when the agents of a container have nothing left to do right now.

Stepped simulations (:class:`~mango.simulation.world.SimulationWorld`, the
external-coupling container, the distributed clock) advance time only after
every agent has *settled*: its inbox is processed and each of its scheduled
tasks is either finished or sleeping until the clock or a message wakes it.
:func:`tasks_complete_or_sleeping` waits for that point.

Each container keeps an :class:`AgentActivity`: the agents that may have
work, because a message reached their inbox or one of their scheduled tasks
started running (it was scheduled, or the clock or a message woke it).  A
settle looks only at those agents and drops the ones it finds idle, and each
scheduler keeps its running tasks in a set, so the cost of a settle grows
with the number of agents and tasks that are active, not with the number of
all agents or all scheduled tasks: idle agents with sleeping periodic tasks
cost nothing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mango.container.core import Container

#: Upper bound on the extra event-loop turns a draining settle takes while
#: callbacks are still ready to run although every task is sleeping or done.
#: Only reached when foreign code reschedules itself forever (``while True:
#: await asyncio.sleep(0)``); the settle then returns instead of spinning.
MAX_QUIET_TURNS = 10_000

#: Event-loop turns a settle takes after every task is sleeping or done, so
#: that just-woken coroutines resume and register (also used by a draining
#: settle on loops that do not expose their ready queue, e.g. uvloop).
QUIET_TURNS = 2


def _collect_pending(scheduler, except_sources, pending: list) -> None:
    """Append the observable tasks of *scheduler* that are neither sleeping
    nor done to *pending* (process tasks are not observable)."""
    running = scheduler._running
    if not running:
        return
    for task in list(running):
        if (
            not task._is_sleeping.done()
            and not task._is_done.done()
            and task._src not in except_sources
        ):
            pending.append(task)


class AgentActivity:
    """The agents of one container that may have work right now.

    An agent becomes *active* when a message is put into its
    :class:`~mango.agent.core.AgentInbox` or one of its observable scheduled
    tasks starts running (see :attr:`Scheduler.on_activity
    <mango.util.scheduling.Scheduler.on_activity>`); a settle drops it again
    once its inbox is processed and its tasks sleep or are done.  Agents whose
    inbox is not an ``AgentInbox`` (swapped for a plain ``asyncio.Queue``)
    cannot report messages and are looked at in every settle.

    Containers create one and attach every registered agent.

    Example::

        activity = AgentActivity()
        activity.attach(agent)
        agent.inbox.put_nowait((0, "hi", {}))
        list(activity.candidates()) == [agent]   # True
    """

    __slots__ = ("_active", "_untracked", "_dropped")

    def __init__(self) -> None:
        self._active: dict[int, Any] = {}
        self._untracked: dict[int, Any] = {}
        # entries dropped since _active was last rebuilt (a dict keeps its
        # size after deletions, and iterating it walks the empty slots)
        self._dropped = 0

    def attach(self, agent: Any) -> None:
        """Watch *agent*'s inbox and scheduler (again, after one was swapped)."""
        key = id(agent)
        agent.__dict__["_mango_activity"] = self

        def mark(_self=self, _key=key, _agent=agent) -> None:
            _self._active[_key] = _agent

        inbox = agent.__dict__.get("_mango_inbox")
        if inbox is not None and hasattr(inbox, "on_put"):
            inbox.on_put = mark
            self._untracked.pop(key, None)
        else:
            self._untracked[key] = agent
        scheduler = agent.__dict__.get("_mango_scheduler")
        if scheduler is not None:
            scheduler.on_activity = mark
        self._active[key] = agent

    def detach(self, agent: Any) -> None:
        """Stop watching *agent* (it was deregistered)."""
        key = id(agent)
        if self._active.pop(key, None) is not None:
            self._dropped += 1
        self._untracked.pop(key, None)
        agent.__dict__.pop("_mango_activity", None)
        inbox = agent.__dict__.get("_mango_inbox")
        if inbox is not None and hasattr(inbox, "on_put"):
            inbox.on_put = None
        scheduler = agent.__dict__.get("_mango_scheduler")
        if scheduler is not None:
            scheduler.on_activity = None

    def candidates(self) -> list:
        """The agents a settle must look at: the active and the untracked ones."""
        if self._dropped > 2 * len(self._active) + 64:
            self._active = dict(self._active)  # compact
            self._dropped = 0
        if not self._untracked:
            return list(self._active.values())
        found = dict(self._active)
        found.update(self._untracked)
        return list(found.values())

    def settled(self, agent: Any) -> None:
        """*agent* has nothing to do: drop it until it reports activity again."""
        if self._active.pop(id(agent), None) is not None:
            self._dropped += 1


def _ready_callbacks(loop: asyncio.AbstractEventLoop) -> int | None:
    """Number of callbacks the loop will run next, or ``None`` if unknown."""
    ready = getattr(loop, "_ready", None)
    try:
        return len(ready)
    except TypeError:
        return None


def unfinished_task_count(container: Container):
    """Return how many observable scheduled tasks of *container*'s agents are
    neither sleeping nor done."""
    pending: list = []
    for agent in container._agents.values():
        _collect_pending(agent.scheduler, (), pending)
    return len(pending)


async def settle(
    scan: Callable[[], tuple[Iterable[asyncio.Queue], list]],
    *,
    drain_loop: bool = False,
    quiet_turns: int = QUIET_TURNS,
) -> None:
    """Wait until the agents seen by *scan* are quiescent.

    Quiescent means: every inbox is fully processed and every observable
    scheduled task is sleeping or done, stable over *quiet_turns* event-loop
    turns (so a task that was just woken has resumed). With *drain_loop* the
    settle instead waits until the event loop has no callback left to run at
    all; use it only when the loop belongs to the simulation (a stepped
    world), never next to real-time containers, whose traffic would keep it
    busy.

    :param scan: callable returning ``(inboxes_with_unfinished_messages,
        pending_tasks)``; called once per round, so agents and tasks added
        while settling are seen
    :param drain_loop: wait for the event loop's ready queue to empty
    :param quiet_turns: event-loop turns the state must stay settled before
        returning (without *drain_loop*, or on loops without a visible ready
        queue)
    """
    loop = asyncio.get_running_loop()
    turns = 0
    while True:
        inboxes, pending = scan()
        if inboxes or pending:
            turns = 0
            for inbox in inboxes:
                await inbox.join()
            for task in pending:
                if not (task._is_sleeping.done() or task._is_done.done()):
                    await asyncio.wait(
                        [task._is_sleeping, task._is_done],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
            continue
        ready = _ready_callbacks(loop) if drain_loop else None
        if ready is None:
            if turns >= quiet_turns:
                return
        elif ready == 0 or turns >= MAX_QUIET_TURNS:
            return
        # Callbacks are queued (e.g. a task whose clock future just resolved
        # has not resumed yet): let them run, then look again.
        turns += 1
        await asyncio.sleep(0)


async def tasks_complete_or_sleeping(
    container: Container,
    except_sources=("no_wait",),
    *,
    drain_loop: bool = False,
):
    """Wait until all agents of *container* have settled.

    Returns once every agent inbox is processed and every observable
    scheduled task (except those whose source is in *except_sources*) is
    sleeping or done. Only the agents that reported activity since they were
    last seen idle are looked at (see :class:`AgentActivity`), so the cost
    grows with the number of active agents and running tasks; idle agents
    with sleeping periodic tasks cost nothing.

    :param container: the container whose agents are awaited
    :param except_sources: scheduling sources whose tasks are not awaited
    :param drain_loop: also wait until the event loop has no ready callback
        (used by :class:`~mango.simulation.SimulationWorld`, which owns its
        loop; see :func:`settle`)

    Example::

        await world.step(step_size_s=1.0)  # settles internally
        await tasks_complete_or_sleeping(container)
    """

    activity = getattr(container, "_activity", None)
    agents = container._agents

    def scan():
        inboxes = []
        pending: list = []
        # is None for containers in MirrorContainerProcessManager
        if container.inbox is not None and container.inbox._unfinished_tasks:
            inboxes.append(container.inbox)
        if activity is None:  # a container without activity tracking
            for agent in agents.values():
                inbox = agent.inbox
                if inbox._unfinished_tasks:
                    inboxes.append(inbox)
                _collect_pending(agent.scheduler, except_sources, pending)
            return inboxes, pending
        for agent in activity.candidates():
            if agents.get(agent.aid) is not agent:  # deregistered
                activity.detach(agent)
                continue
            inbox = agent.inbox
            busy = inbox._unfinished_tasks > 0
            if busy:
                inboxes.append(inbox)
            before = len(pending)
            _collect_pending(agent.scheduler, except_sources, pending)
            if not busy and len(pending) == before:
                activity.settled(agent)
        return inboxes, pending

    await settle(scan, drain_loop=drain_loop)

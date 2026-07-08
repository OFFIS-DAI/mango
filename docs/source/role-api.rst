========
Role API
========

Besides subclassing :class:`~mango.Agent` directly, mango provides the *role
system* as a higher-level way to structure agent behaviour.  A *role*
encapsulates one responsibility of an agent — for example coalition membership,
resource monitoring, or a messaging protocol.

Roles promote **reusability**: the same role class can be added to different
agents in different deployments.  They also promote **loose coupling**: roles
interact through a shared context and event API rather than direct references.

.. grid:: 1 2 3 3
   :gutter: 3

   .. grid-item-card:: Lifecycle hooks
      :shadow: sm

      ``setup`` · ``on_start`` · ``on_ready`` · ``on_stop`` · ``on_deactivation``

   .. grid-item-card:: Sharing data
      :shadow: sm

      ``context.data`` for ad-hoc attributes; observable models via
      ``get_or_create_model`` / ``subscribe_model``.

   .. grid-item-card:: Messages
      :shadow: sm

      ``subscribe_message`` with a condition function and optional
      ``preprocessor`` for transformation or serialisation.
      ``subscribe_send`` to observe outgoing messages.

   .. grid-item-card:: Inter-role events
      :shadow: sm

      ``emit_event`` / ``subscribe_event`` — typed, in-process signals
      between roles of the same agent.

   .. grid-item-card:: Dynamic roles
      :shadow: sm

      ``add_role`` · ``remove_role`` · ``get_role`` at any point in the
      agent's lifetime.

   .. grid-item-card:: Activate / deactivate
      :shadow: sm

      Suspend and resume a role together with its tasks and subscriptions.

.. seealso::

    :doc:`agents-container` — agent basics and lifecycle


----

The RoleContext
===============

Every role has access to a :class:`~mango.RoleContext` via ``self.context``.
The context is the role's window into the agent and its environment:

.. list-table::
   :widths: 40 60
   :header-rows: 1

   * - What you need
     - How to get it
   * - Send a message
     - ``await self.context.send_message(content, addr)``
   * - Schedule a task
     - ``self.context.schedule_periodic_task(...)``
   * - Subscribe to messages
     - ``self.context.subscribe_message(self, handler, condition)``
   * - Subscribe to outgoing messages
     - ``self.context.subscribe_send(self, handler)``
   * - Emit an event to other roles
     - ``self.context.emit_event(event, event_source=self)``
   * - Subscribe to events from other roles
     - ``self.context.subscribe_event(self, EventType, handler)``
   * - Share data with other roles
     - ``self.context.data`` or ``self.context.get_or_create_model(cls)``
   * - Look up another role
     - ``self.context.get_role(MyRoleClass)``
   * - Add / remove a role at runtime
     - ``self.context.add_role(role)`` / ``self.context.remove_role(role)``
   * - Current simulation / wall time
     - ``self.context.current_timestamp``
   * - Container address
     - ``self.context.context.addr``
   * - Inbox queue depth
     - ``self.context.inbox_length()``

The context is available from :meth:`~mango.Role.setup` onward (not in
``__init__``).


----

The Role class
==============

Subclass :class:`~mango.Role` and add instances to a :class:`~mango.RoleAgent`
with :meth:`~mango.RoleAgent.add_role`, or use
:func:`~mango.agent_composed_of` as a shortcut:

.. testcode::

    from mango import RoleAgent, Role, agent_composed_of

    class MyRole(Role):
        pass

    # long form
    my_role_agent = RoleAgent()
    my_role_agent.add_role(MyRole())

    # short form
    my_composed_agent = agent_composed_of(MyRole())

    print(type(my_role_agent.roles[0]))
    print(type(my_composed_agent.roles[0]))

.. testoutput::

    <class 'MyRole'>
    <class 'MyRole'>


Lifecycle
---------

The role lifecycle mirrors the agent lifecycle, with one extra step:

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - Hook
     - When it is called
   * - ``__init__``
     - At object creation (before registration).  Configure role parameters
       here; the context is **not** available yet.
   * - :meth:`~mango.Role.setup`
     - When the role is added to an agent.  The context is available from
       this point on.  Schedule tasks and subscribe to messages here.
   * - :meth:`~mango.Role.on_start`
     - When the container starts.
   * - :meth:`~mango.Role.on_ready`
     - When all containers have started.  Safe to send messages.
   * - :meth:`~mango.Role.on_deactivation`
     - When another role (or external code) calls
       ``context.deactivate(this_role)``.  Receives the caller as *src*.
   * - :meth:`~mango.Role.on_stop`
     - When the container shuts down or the role is removed with
       ``context.remove_role``.

.. testcode::

    import asyncio
    from mango import Role, agent_composed_of, run_with_tcp

    class LifecycleRole(Role):
        def __init__(self):
            print("Init")
        def setup(self):
            print("Setup")
        def on_start(self):
            print("Start")
        def on_ready(self):
            print("Ready")
        async def on_stop(self):
            print("Stop")

    async def show_lifecycle():
        async with run_with_tcp(1, agent_composed_of(LifecycleRole())):
            pass

    asyncio.run(show_lifecycle())

.. testoutput::

    Init
    Setup
    Start
    Ready
    Stop

.. note::
    Once a role has been stopped it **must not** be reused.  To temporarily
    suspend a role use :meth:`~mango.RoleContext.deactivate` /
    :meth:`~mango.RoleContext.activate` instead.


----

Sharing data between roles
==========================

Two patterns are available for roles to share state within the same agent.

**Simple shared container** — attach arbitrary attributes to
:attr:`~mango.RoleContext.data`:

.. testcode::

    from mango import Role, agent_composed_of

    class WriterRole(Role):
        def setup(self):
            self.context.data.shared_value = "hello"

    class ReaderRole(Role):
        def setup(self):
            # readable by any other role in the same agent
            print(self.context.data.get("shared_value", "not set yet"))

    agent = agent_composed_of(WriterRole(), ReaderRole())

.. testoutput::

    hello

**Observable model** — create a typed model and subscribe to its changes:

.. testcode::

    from mango import Role, agent_composed_of

    class CounterModel:
        def __init__(self):
            self.count = 0

    class IncrementRole(Role):
        def setup(self):
            model = self.context.get_or_create_model(CounterModel)
            model.count += 1
            self.context.update(model)  # notify subscribers

    class DisplayRole(Role):
        def setup(self):
            self.context.subscribe_model(self, CounterModel)

        def on_change_model(self, model):
            print(f"Count is now {model.count}")

    agent = agent_composed_of(DisplayRole(), IncrementRole())

.. testoutput::

    Count is now 1

The :meth:`~mango.Role.on_change_model` method is called on a role whenever
:meth:`~mango.RoleContext.update` is called with a model that the role has
subscribed to via :meth:`~mango.RoleContext.subscribe_model`.

.. tip::

    ``get_or_create_model`` returns the **same instance** every time for a
    given type within one agent.  Multiple roles can safely call it and share
    the model without coordination.


----

Handling messages
=================

Use :meth:`~mango.RoleContext.subscribe_message` to register a handler for a
specific message type.  The *condition* function filters incoming messages —
only messages for which it returns ``True`` are forwarded to the handler:

.. testcode::

    import asyncio
    from mango import Role, agent_composed_of, run_with_tcp

    class Ping:
        pass

    class PingRole(Role):
        def setup(self):
            self.context.subscribe_message(
                self,
                self.handle_ping,
                lambda content, meta: isinstance(content, Ping),
            )

        def handle_ping(self, content, meta):
            print("Ping received!")

    async def show_handle_sub():
        my_agent = agent_composed_of(PingRole())
        async with run_with_tcp(1, my_agent) as container:
            await container.send_message(Ping(), my_agent.addr)
            await asyncio.sleep(0.05)

    asyncio.run(show_handle_sub())

.. testoutput::

    Ping received!

The optional ``priority`` parameter controls dispatch order when multiple
subscriptions match (lower number = called earlier, default = ``0``).

**Fallback: ``handle_message``** — if a role overrides
:meth:`~mango.Role.handle_message`, it receives *every* message that was
**not already claimed** by a subscription.  Use this as a catch-all handler
without registering a condition:

.. code-block:: python

    class LoggingRole(Role):
        """Print every message the agent receives that no other role handled."""

        def handle_message(self, content, meta):
            print(f"Unhandled message: {content}")

.. note::

    ``handle_message`` on a role is only called when no subscription in the
    entire agent matches the message.  If *any* subscription fires,
    ``handle_message`` is **not** called for that message.


Message preprocessors
----------------------

A :class:`~mango.MessagePreprocessor` sits between the inbox and the handler.
It is registered alongside the handler in :meth:`~mango.RoleContext.subscribe_message`
and can **transform**, **gate**, or **rate-limit** messages before they reach
the role.

Implement :meth:`~mango.MessagePreprocessor.handle` and call
``handler(content, meta)`` inside it to deliver the (optionally transformed)
message.  Override :meth:`~mango.MessagePreprocessor.process` to rewrite
content or metadata before passing it on:

.. code-block:: python

    from mango import MessagePreprocessor, Role

    class UpperCasePreprocessor(MessagePreprocessor):
        """Uppercases any string message before it reaches the handler."""

        def handle(self, role, handler, content, meta):
            content, meta = self.process(content, meta)
            handler(content, meta)

        def process(self, content, meta):
            if isinstance(content, str):
                content = content.upper()
            return content, meta

    class GreeterRole(Role):
        def setup(self):
            self.context.subscribe_message(
                self,
                self.on_greeting,
                lambda content, meta: isinstance(content, str),
                preprocessor=UpperCasePreprocessor(),
            )

        def on_greeting(self, content, meta):
            print(f"Received: {content}")   # always upper-case


``WaitingMessagePreprocessor``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:class:`~mango.WaitingMessagePreprocessor` is a built-in preprocessor that
**serialises message delivery** — the next message is only dispatched once the
handler for the current message has returned (or its coroutine completed).

This prevents race conditions when a role's handler performs async work that
depends on exclusive access to its own state:

.. code-block:: python

    import asyncio
    from mango import Role, WaitingMessagePreprocessor

    class SafeProcessorRole(Role):
        """Handles one update at a time, even if messages arrive in bursts."""

        def setup(self):
            self.context.subscribe_message(
                self,
                self.on_update,
                lambda content, meta: True,
                preprocessor=WaitingMessagePreprocessor(),
            )

        async def on_update(self, content, meta):
            await asyncio.sleep(0.1)       # I/O or heavy computation
            print(f"Processed: {content}") # always sequential

Without the preprocessor, overlapping messages could interleave the
``await asyncio.sleep`` calls and make the processing order unpredictable.

.. note::

    :class:`~mango.WaitingMessagePreprocessor` schedules delivery via
    ``asyncio.get_event_loop().create_task``.  It is designed for use inside
    running event loops (i.e. inside ``async with`` container blocks or inside
    a :class:`~mango.SimulationWorld`).


Observing outgoing messages
---------------------------

:meth:`~mango.RoleContext.subscribe_send` lets a role intercept every message
sent by the agent — useful for logging, auditing, or protocol tracing:

.. code-block:: python

    class AuditRole(Role):
        def setup(self):
            self.context.subscribe_send(self, self.on_send)

        def on_send(self, content, receiver_addr, **kwargs):
            print(f"→ {receiver_addr.aid}: {content!r}")

The handler is called **synchronously** before the message is actually sent.
It receives the same ``content``, ``receiver_addr``, and ``kwargs`` that were
passed to ``send_message``.

.. note::

    ``subscribe_send`` observes — it cannot block or modify the message.  For
    full interception you would need to override ``send_message`` on a custom
    ``RoleAgent`` subclass.


----

Inter-role events
=================

Roles within the same agent can communicate without message-passing using a
typed event bus.  One role *emits* an event object; any role that has
*subscribed* to that event type receives it immediately (synchronously,
in-process).

This is lighter-weight than sending a message and avoids the overhead of
serialisation and the asyncio inbox.

.. testcode::

    import asyncio
    from mango import Role, agent_composed_of, run_with_tcp

    # --- event type ---
    class TargetReached:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    # --- emitter ---
    class NavigationRole(Role):
        def setup(self):
            self.context.subscribe_message(
                self,
                self.on_move,
                lambda content, meta: isinstance(content, tuple),
            )

        def on_move(self, content, meta):
            x, y = content
            # emit an event so other roles react without being coupled to
            # NavigationRole directly
            self.context.emit_event(TargetReached(x, y), event_source=self)

    # --- listener ---
    class LoggingRole(Role):
        def setup(self):
            self.context.subscribe_event(self, TargetReached, self.on_target)

        def on_target(self, event: TargetReached, source):
            print(f"Target reached: ({event.x}, {event.y})")

    async def show_events():
        agent = agent_composed_of(NavigationRole(), LoggingRole())
        async with run_with_tcp(1, agent) as container:
            await container.send_message((3, 7), agent.addr)
            await asyncio.sleep(0.05)

    asyncio.run(show_events())

.. testoutput::

    Target reached: (3, 7)

The *event type* is the class of the object passed to
:meth:`~mango.RoleContext.emit_event`.  Subscribers register for a specific
type and are only called when that exact type (or a subclass) is emitted.

The *event_source* parameter is passed as the second argument to the handler
(``source`` above).  Pass ``self`` to let listeners know which role raised
the event — useful when multiple roles can emit the same event type.

.. note::

    Events are delivered **synchronously** in subscription order.  The
    emitting role's ``emit_event`` call does not return until all handlers
    have run.  Avoid long-running or awaiting logic inside event handlers.


----

Declarative dispatch with decorators
====================================

The subscription and scheduling calls above (:meth:`~mango.RoleContext.subscribe_message`,
:meth:`~mango.RoleContext.subscribe_event`, periodic tasks) can all be written
declaratively by decorating the handler method directly.  A role that uses the
decorators often needs no ``setup`` at all — the wiring is read from the class
at bind time:

.. list-table::
   :widths: 26 74
   :header-rows: 1

   * - Decorator
     - Replaces
   * - :func:`~mango.on_message`
     - a :meth:`~mango.RoleContext.subscribe_message` call.
   * - :func:`~mango.on_event`
     - a :meth:`~mango.RoleContext.subscribe_event` call.
   * - :func:`~mango.periodic`
     - a periodic ``schedule_periodic_task`` call.

.. code-block:: python

    from mango import Role, on_message, on_event, periodic, sender_addr

    class Worker(Role):
        @on_message(Task)
        async def on_task(self, content, meta):
            await self.context.reply_to(Result(...), meta)

        @on_event(ConfigChanged)
        def on_config(self, event, source):
            self.config = event.config

        @periodic(every=1.0)
        async def heartbeat(self):
            await self.context.send_message(Beat(), self.leader)

The equivalent hand-written ``setup`` would register three callbacks; the
decorated form keeps each handler next to its trigger and removes the
boilerplate.

on_message
----------

:func:`~mango.on_message` delivers messages where
``isinstance(content, message_type)`` is true.  The handler is called as
``handler(self, content, meta)``.  **Async handlers are supported directly** —
they are scheduled as instant tasks automatically (no manual coroutine shim).

Two keyword options refine the subscription:

* ``where`` — an extra predicate ``where(self, content, meta) -> bool``.  It
  receives ``self``, so it can read role state instead of capturing it in a
  class-time closure.
* ``priority`` — dispatch order when several handlers match (lower runs first,
  default ``0``), mirroring :meth:`~mango.RoleContext.subscribe_message`.

.. code-block:: python

    class Router(Role):
        @on_message(Packet, where=lambda self, c, m: c.ttl > 0, priority=0)
        async def forward(self, content, meta):
            ...

.. note::

   Each async ``@on_message`` invocation runs as an independent task, so
   handlers for different messages may run concurrently and out of order.  If a
   handler mutates role state and must not interleave, register it the
   imperative way with a
   :class:`~mango.WaitingMessagePreprocessor` (see `Message preprocessors`_).

on_event
--------

:func:`~mango.on_event` subscribes to a co-located inter-role event (see
`Inter-role events`_).  The handler is ``handler(self, event, source)``
and runs **synchronously** inside :meth:`~mango.RoleContext.emit_event`, so it
must be a plain (non-async) method — decorating an ``async def`` raises a
``TypeError`` at class-definition time rather than silently never running.

periodic
--------

:func:`~mango.periodic` schedules an async method to run on a fixed period.
``every`` is either a number of seconds or the *name* of an instance attribute
read at attach time (handy for per-instance periods).  The optional ``only_if``
predicate gates each firing:

.. code-block:: python

    class Poller(Role):
        poll_period_s = 0.5

        @periodic(every="poll_period_s", only_if=lambda self: self.is_leader)
        async def poll(self):
            await self.context.gather(Ping(), self.peers)

When ``only_if(self)`` is false the scheduled task still fires but returns
early, replacing the ``if not leader: return`` guard by hand.

.. tip::

   Decorators and ``setup`` compose: decorator wiring is applied **before**
   ``setup`` runs, so ``setup`` can add further subscriptions.  There is no
   unsubscribe — ``setup`` extends the declarative wiring, it cannot remove it.
   Stacking decorators on one method (e.g. two ``@on_message``) is supported,
   and decorated handlers on a base ``Role`` are inherited by subclasses.


----

Deactivating and activating roles
==================================

Sometimes you want to suspend an entire role temporarily — for example, stop
accepting coalition invitations while already in one.  Use
:meth:`~mango.RoleContext.deactivate` / :meth:`~mango.RoleContext.activate`:

When a role is **deactivated**:

1. Incoming messages no longer reach its handlers.
2. Model change notifications are suppressed.
3. All scheduled tasks are suspended.

Everything is fully reversed when the role is **activated** again.

.. code-block:: python

    class CoalitionRole(Role):
        def setup(self):
            self.context.subscribe_message(self, self.on_invite, ...)

        def on_invite(self, content, meta):
            # join the coalition and stop accepting new invites
            self.context.deactivate(self)

        def leave_coalition(self):
            self.context.activate(self)

.. note::
    Task suspension intercepts ``__await__`` and may not take effect
    immediately if a task is currently executing.

The :meth:`~mango.Role.on_deactivation` hook is called on the role being
suspended and receives the caller (``src``) as its argument:

.. code-block:: python

    class SuspendableRole(Role):
        def on_deactivation(self, src):
            # src is the role or object that called context.deactivate(self)
            print(f"Suspended by {type(src).__name__}")


----

Dynamic role management
========================

Roles can be added or removed at any point during the agent's lifetime —
not just at construction time.  This is useful for loading roles on demand,
implementing *strategy patterns*, or tearing down protocol roles after a
negotiation completes.


Adding roles at runtime
-----------------------

:meth:`~mango.RoleContext.add_role` triggers the full lifecycle: the new
role's :meth:`~mango.Role.setup` method is called immediately, and if the
container is already running, ``on_start`` and ``on_ready`` are called in
sequence.

.. code-block:: python

    class BootstrapRole(Role):
        def setup(self):
            self.context.subscribe_message(
                self,
                self.on_join_request,
                lambda content, meta: content == "join",
            )

        def on_join_request(self, content, meta):
            # dynamically load a protocol role when a peer connects
            self.context.add_role(NegotiationRole(peer_addr=sender_addr(meta)))


Removing roles at runtime
--------------------------

:meth:`~mango.RoleContext.remove_role` permanently removes a role and calls
its :meth:`~mango.Role.on_stop` for clean-up.  After removal, the role
instance must not be used again.

.. code-block:: python

    class NegotiationRole(Role):
        def __init__(self, peer_addr):
            self._peer = peer_addr

        def setup(self):
            self.context.subscribe_message(
                self, self.on_final, lambda c, m: c == "done"
            )

        def on_final(self, content, meta):
            # negotiation complete — tear this role down
            self.context.remove_role(self)


Looking up a role by type
--------------------------

:meth:`~mango.RoleContext.get_role` returns the first role of the given class
currently registered in the agent, or ``None`` if no such role exists.  Use
it to build explicit dependencies between roles:

.. code-block:: python

    class ControlRole(Role):
        def setup(self):
            monitor = self.context.get_role(MonitorRole)
            if monitor is not None:
                print(f"Monitor is active, threshold={monitor.threshold}")
            else:
                print("No monitor role installed.")

.. tip::

    Prefer :ref:`inter-role events <inter-role-events>` or shared models over
    direct ``get_role`` lookups where possible — they keep roles decoupled and
    make it easier to swap implementations.


Inspecting the inbox
---------------------

:meth:`~mango.RoleContext.inbox_length` returns the current number of messages
waiting in the agent's inbox queue.  Use it to detect backpressure or to make
scheduling decisions:

.. code-block:: python

    class BackpressureRole(Role):
        def setup(self):
            self.context.schedule_periodic_task(self._check, delay=1.0)

        async def _check(self):
            depth = self.context.inbox_length()
            if depth > 20:
                print(f"Warning: inbox has {depth} pending messages")


----

.. _inter-role-events:

.. seealso::

    :doc:`simulation` — roles also support ``on_step``, ``on_global_event``,
    and ``on_agent_event`` hooks, as well as message preprocessors
    (:class:`~mango.MessagePreprocessor`, :class:`~mango.WaitingMessagePreprocessor`),
    when running inside a :class:`~mango.SimulationWorld`.

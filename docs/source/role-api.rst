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

   .. grid-item-card:: Declarative wiring
      :shadow: sm

      ``@on_message`` · ``@on_event`` · ``@periodic`` — declare handlers on
      the class instead of registering them in ``setup``.

   .. grid-item-card:: Messages
      :shadow: sm

      ``@on_message`` with a type and an optional ``where`` filter, or
      ``subscribe_message`` with a condition function and optional
      ``preprocessor``.  ``subscribe_send`` to observe outgoing messages.

   .. grid-item-card:: Inter-role events
      :shadow: sm

      ``emit_event`` / ``@on_event`` — typed, in-process signals
      between roles of the same agent.

   .. grid-item-card:: Sharing data
      :shadow: sm

      ``context.data`` for ad-hoc attributes; observable models via
      ``get_or_create_model`` / ``subscribe_model``.

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
   * - Handle incoming messages
     - ``@on_message(MessageType)`` on a handler method, or
       ``self.context.subscribe_message(self, handler, condition)``
   * - Run a task periodically
     - ``@periodic(every=seconds)`` on a coroutine method, or
       ``self.context.schedule_periodic_task(...)``
   * - Subscribe to outgoing messages
     - ``self.context.subscribe_send(self, handler)``
   * - Emit an event to other roles
     - ``self.context.emit_event(event, event_source=self)``
   * - Subscribe to events from other roles
     - ``@on_event(EventType)`` on a handler method, or
       ``self.context.subscribe_event(self, EventType, handler)``
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
       this point on.  ``@on_message`` / ``@on_event`` handlers are already
       registered when ``setup`` runs, so it only needs to cover what the
       decorators cannot express.
   * - :meth:`~mango.Role.on_start`
     - When the container starts.
   * - :meth:`~mango.Role.on_ready`
     - When all containers have started.  Safe to send messages.
       ``@periodic`` tasks are started at this point.
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

.. note::
    A role added while the agent is already running (see `Dynamic role
    management`_) goes through the same sequence without waiting:
    ``setup`` is followed immediately by ``on_start`` and ``on_ready``.


.. _role-decorators:

Wiring a role: decorators or ``setup``
--------------------------------------

Everything a role reacts to — messages, events, and the clock — can be
declared directly on the handler method.  A role that uses the decorators
usually needs no ``setup`` at all; the wiring is read from the class when the
role is added to an agent:

.. list-table::
   :widths: 26 74
   :header-rows: 1

   * - Decorator
     - Replaces
   * - :func:`~mango.on_message`
     - a :meth:`~mango.RoleContext.subscribe_message` call in ``setup``.
   * - :func:`~mango.on_event`
     - a :meth:`~mango.RoleContext.subscribe_event` call in ``setup``.
   * - :func:`~mango.periodic`
     - a :meth:`~mango.RoleContext.schedule_periodic_task` call in
       ``on_ready``.

.. code-block:: python

    from mango import Role, on_message, on_event, periodic

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

The equivalent hand-written role would register the same three callbacks in
``setup`` and ``on_ready``; the decorated form keeps each handler next to its
trigger and removes the boilerplate.  The sections below describe both forms
side by side.


When to use which
~~~~~~~~~~~~~~~~~

Use the decorators by default.  They cover the common case: the handler is a
method of the role, the trigger is a message type, an event type, or a fixed
period, and any instance-specific filtering can be expressed with the
``where`` predicate (which receives ``self``) or with an attribute name passed
to ``every``.

Fall back to the explicit calls in ``setup`` / ``on_ready`` when

* the handler needs a :class:`~mango.MessagePreprocessor` — for example a
  :class:`~mango.WaitingMessagePreprocessor` to serialise delivery — since
  ``@on_message`` does not take one;
* the message type, or whether to subscribe at all, depends on constructor
  arguments or configuration;
* the handler is not a method of this role (a closure, another object);
* you need the task handle that
  :meth:`~mango.RoleContext.schedule_periodic_task` returns, e.g. to cancel
  the task later; or
* you need :meth:`~mango.RoleContext.subscribe_send` or
  :meth:`~mango.RoleContext.subscribe_model`, which have no decorator.

Both forms compose freely on one role.  Decorator wiring is applied **before**
``setup`` runs, so ``setup`` extends it — it cannot remove a decorated
handler.  Neither form offers an unsubscribe: to stop a role from reacting,
:meth:`~mango.RoleContext.deactivate` it or
:meth:`~mango.RoleContext.remove_role` it (see below).

.. tip::

   Stacking decorators on one method (e.g. two ``@on_message`` for two
   types) is supported, and decorated handlers on a base ``Role`` are
   inherited by subclasses.


----

Handling messages
=================

Decorate a method with :func:`~mango.on_message` to receive every message
whose content is an instance of the given type.  The handler is called as
``handler(self, content, meta)``; ``async def`` handlers are scheduled as
instant tasks automatically:

.. testcode::

    import asyncio
    from mango import Role, agent_composed_of, run_with_tcp, on_message

    class Ping:
        pass

    class PingRole(Role):
        @on_message(Ping)
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

Two keyword options refine the subscription:

* ``where`` — an extra predicate ``where(self, content, meta) -> bool``.  It
  receives ``self``, so it can read role state instead of capturing it in a
  class-time closure.
* ``priority`` — dispatch order when several handlers match (lower runs first,
  default ``0``).

.. code-block:: python

    class Router(Role):
        @on_message(Packet, where=lambda self, c, m: c.ttl > 0, priority=0)
        async def forward(self, content, meta):
            ...

.. note::

   A message of the right type that fails ``where`` is simply not delivered
   to *this* handler.  It is not dropped: every other matching subscription
   still fires, and the role's ``handle_message`` fallback (below) still sees
   it.  If a role needs to observe every message of a type — say, to count
   arrivals for a timeout — while acting only on some of them, add a second
   ``@on_message(Type)`` handler without ``where``, or use
   ``handle_message``.

.. note::

   Each async ``@on_message`` invocation runs as an independent task, so
   handlers for different messages may run concurrently and out of order.  If a
   handler mutates role state and must not interleave, register it with
   :meth:`~mango.RoleContext.subscribe_message` and a
   :class:`~mango.WaitingMessagePreprocessor` (see `Message preprocessors`_).


Subscribing in ``setup``
------------------------

:meth:`~mango.RoleContext.subscribe_message` is the explicit form of the
decorator.  It takes the role, a handler, and a *condition* function — only
messages for which the condition returns ``True`` are forwarded:

.. testcode::

    class PingRole(Role):
        def setup(self):
            self.context.subscribe_message(
                self,
                self.handle_ping,
                lambda content, meta: isinstance(content, Ping),
            )

        def handle_ping(self, content, meta):
            print("Ping received!")

    asyncio.run(show_handle_sub())

.. testoutput::

    Ping received!

The optional ``priority`` parameter controls dispatch order when multiple
subscriptions match (lower number = called earlier, default = ``0``); the
optional ``preprocessor`` is described below.  Two things differ from the
decorator: the condition receives no ``self``, so capture what it needs in
the closure, and the callback contract is synchronous — an ``async`` handler
registered this way has to be scheduled explicitly, e.g. with
``self.context.schedule_instant_task(self.handler(content, meta))``.

**Fallback: ``handle_message``** — a role may also override
:meth:`~mango.Role.handle_message`.  It receives **every** message the agent
receives, whether or not a subscription or decorated handler already handled
it, and is called after those handlers.  Use it as a catch-all or observer:

.. code-block:: python

    class LoggingRole(Role):
        """Print every message the agent receives."""

        def handle_message(self, content, meta):
            print(f"Message: {content}")

.. note::

    ``handle_message`` is *not* filtered by subscriptions: a message that an
    ``@on_message`` handler of the same or another role has handled still
    reaches every role's ``handle_message``.  (If a subscription's handler is
    itself a ``handle_message`` method, the fallback pass is skipped so it is
    not called twice.)


Message preprocessors
----------------------

A :class:`~mango.MessagePreprocessor` sits between the inbox and the handler.
It is registered alongside the handler in :meth:`~mango.RoleContext.subscribe_message`
and can **transform**, **gate**, or **rate-limit** messages before they reach
the role.  Preprocessors are only available through ``subscribe_message`` —
``@on_message`` does not take one.

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

.. _inter-role-events:

Inter-role events
=================

Roles within the same agent can communicate without message-passing using a
typed event bus.  One role *emits* an event object; any role that has
*subscribed* to that event type receives it immediately (synchronously,
in-process).

This is lighter-weight than sending a message and avoids the overhead of
serialisation and the asyncio inbox.  Subscribe with :func:`~mango.on_event`:

.. testcode::

    import asyncio
    from mango import Role, agent_composed_of, run_with_tcp, on_message, on_event

    # --- event type ---
    class TargetReached:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    # --- emitter ---
    class NavigationRole(Role):
        @on_message(tuple)
        def on_move(self, content, meta):
            x, y = content
            # emit an event so other roles react without being coupled to
            # NavigationRole directly
            self.context.emit_event(TargetReached(x, y), event_source=self)

    # --- listener ---
    class LoggingRole(Role):
        @on_event(TargetReached)
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

The handler runs **synchronously** inside
:meth:`~mango.RoleContext.emit_event`, so it must be a plain (non-async)
method — decorating an ``async def`` with ``@on_event`` raises a ``TypeError``
at class-definition time rather than silently never running.

The explicit form is :meth:`~mango.RoleContext.subscribe_event` in ``setup``:

.. code-block:: python

    class LoggingRole(Role):
        def setup(self):
            self.context.subscribe_event(self, TargetReached, self.on_target)

        def on_target(self, event, source):
            print(f"Target reached: ({event.x}, {event.y})")

.. note::

    Events are delivered **synchronously** in subscription order.  The
    emitting role's ``emit_event`` call does not return until all handlers
    have run.  Avoid long-running or awaiting logic inside event handlers.


----

.. _role-periodic:

Periodic tasks
==============

:func:`~mango.periodic` runs an ``async`` method of the role on a fixed
period.  The task is started when the role reaches ``on_ready`` — the same
moment at which you would call
:meth:`~mango.RoleContext.schedule_periodic_task` by hand — runs the method
once immediately, and then again after every ``every`` seconds.

``every`` is either a number of seconds or the *name* of an instance
attribute, read when the task is started (handy for per-instance periods).
The optional ``only_if`` predicate gates each firing:

.. code-block:: python

    class Poller(Role):
        def __init__(self, poll_period_s):
            super().__init__()
            self.poll_period_s = poll_period_s
            self.is_leader = False

        @periodic(every="poll_period_s", only_if=lambda self: self.is_leader)
        async def poll(self):
            await self.context.gather(Ping(), self.peers)

When ``only_if(self)`` is false the scheduled task still fires but returns
early, replacing the ``if not leader: return`` guard by hand.

**Periods follow the clock.**  The period is measured on the agent's
scheduler clock, not on wall time.  Under the default
:class:`~mango.AsyncioClock` that is the same thing; under an
:class:`~mango.ExternalClock` — and therefore inside a
:class:`~mango.SimulationWorld` — the task advances only when simulation time
does:

.. testcode::

    import asyncio
    from mango import Role, agent_composed_of, create_tcp_container, activate
    from mango import periodic, ExternalClock

    class Heartbeat(Role):
        @periodic(every=10.0)
        async def beat(self):
            print(f"beat at t={self.context.current_timestamp:.0f}")

    async def show_periodic_with_clock():
        clock = ExternalClock(start_time=0)
        container = create_tcp_container(addr=("127.0.0.1", 5555), clock=clock)
        agent_composed_of(Heartbeat(), register_in=container)

        async with activate(container):
            await asyncio.sleep(0.05)     # first run happens at on_ready
            for t in (10, 20, 30):
                clock.set_time(t)         # each step releases one beat
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.2)      # wall time alone releases nothing

    asyncio.run(show_periodic_with_clock())

.. testoutput::

    beat at t=0
    beat at t=10
    beat at t=20
    beat at t=30

The explicit form is :meth:`~mango.RoleContext.schedule_periodic_task`,
called from ``on_ready``.  It returns the task handle, which the decorator
does not expose:

.. code-block:: python

    class Heartbeat(Role):
        def on_ready(self):
            self._task = self.context.schedule_periodic_task(self.beat, delay=10.0)

        async def beat(self):
            ...

Decorated periodic tasks belong to the role:
:meth:`~mango.RoleContext.deactivate` suspends them along with the role's
other tasks, and :meth:`~mango.RoleContext.activate` resumes them.

.. seealso::

    :doc:`scheduling` — all task types, clocks, and process-based tasks.


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

Deactivating and activating roles
==================================

Sometimes you want to suspend an entire role temporarily — for example, stop
accepting coalition invitations while already in one.  Use
:meth:`~mango.RoleContext.deactivate` / :meth:`~mango.RoleContext.activate`:

When a role is **deactivated**:

1. Incoming messages no longer reach its handlers.
2. Model change notifications are suppressed.
3. All scheduled tasks are suspended, including ``@periodic`` tasks.

Everything is fully reversed when the role is **activated** again.

.. code-block:: python

    class CoalitionRole(Role):
        @on_message(Invite)
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
role's decorated handlers are registered and its :meth:`~mango.Role.setup`
is called immediately.  If the container is already running, ``on_start``
and ``on_ready`` follow at once, which also starts the role's ``@periodic``
tasks.

.. code-block:: python

    class BootstrapRole(Role):
        @on_message(str, where=lambda self, c, m: c == "join")
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
            super().__init__()
            self._peer = peer_addr

        @on_message(str, where=lambda self, c, m: c == "done")
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
        @periodic(every=1.0)
        async def check(self):
            depth = self.context.inbox_length()
            if depth > 20:
                print(f"Warning: inbox has {depth} pending messages")


----

.. seealso::

    :doc:`simulation` — roles also support ``on_step``, ``on_global_event``,
    and ``on_agent_event`` hooks, as well as message preprocessors
    (:class:`~mango.MessagePreprocessor`, :class:`~mango.WaitingMessagePreprocessor`),
    when running inside a :class:`~mango.SimulationWorld`.

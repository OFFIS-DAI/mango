========
Role API
========

Besides subclassing :class:`~mango.Agent` directly, mango provides the *role
system* as a higher-level way to structure agent behaviour.  A *role*
encapsulates one responsibility of an agent — for example coalition membership,
resource monitoring, or a messaging protocol.

Roles promote **reusability**: the same role class can be added to different
agents in different deployments.  They also promote **loose coupling**: roles
interact through a shared context and model API rather than direct references.

.. seealso::

    :doc:`agents-container` — agent basics and lifecycle


The RoleContext
===============

Every role has access to a :class:`~mango.RoleContext` via ``self.context``.
The context is the role's window into the agent and its environment:

* Send messages: ``self.context.send_message(...)``
* Schedule tasks: ``self.context.schedule_periodic_task(...)``
* Share data with other roles: ``self.context.data`` or
  ``self.context.get_or_create_model(...)``
* Subscribe to messages: ``self.context.subscribe_message(...)``
* Read the current timestamp: ``self.context.current_timestamp``

The context is available from :meth:`~mango.Role.setup` onward (not in
``__init__``).


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
   * - :meth:`~mango.Role.on_stop`
     - When the container shuts down or the role is removed.

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

.. seealso::

    :doc:`simulation` — roles also support ``on_step``, ``on_global_event``,
    and ``on_agent_event`` hooks when running inside a
    :class:`~mango.SimulationWorld`.

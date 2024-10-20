========
Role-API
========
Besides inheriting from the :class:`Agent`-class there is another option to integrate features into an agent: the role API.
The idea of using roles is to divide the functionality of an agent by responsibility in a structured way. The objective of this API is to increase the reusability and the maintainability of the agents components. To achieve this the role API works with orchestration rather than inheriting to extend the agents features.


***************
The RoleContext
***************
The role context is the API to the environment of the role.
It provides functionality to interact with other roles, to send messages to other
agents or simply to fetch some meta data.


********
The Role
********
To implement a role you have to extend the abstract class :meth:`mango.Role`. Concrete instances of implementations
can be assigned to the general :class:`mango.RoleAgent` with :meth:`mango.RoleAgent.add_role`. However, the
faster way to create an Agent with a set of roles is to use the API :meth:`mango.agent_composed_of`.

.. testcode::

    from mango import RoleAgent, Role, agent_composed_of

    class MyRole(Role):
        pass

    # first way
    my_role_agent = RoleAgent()
    my_role_agent.add_role(MyRole())

    # second way
    my_composed_agent = agent_composed_of(MyRole())

    print(type(my_role_agent.roles[0]))
    print(type(my_composed_agent.roles[0]))

.. testoutput::

    <class 'MyRole'>
    <class 'MyRole'>

Lifecycle
*********
The first step in the roles life is the instantiation via ``__init__``.
This is done by the user itself and can be used to configure the roles behavior.

The next step is adding the role to a RoleAgent using ``add_role`` or ``agent_composed_of``.
The role will get notified by this through the method ``setup``. After adding the role the ``RoleContext``
is available, which represents the environment (container, agent, other roles).

The next lifecycle hook-in is :meth:`mango.Role.on_start`, which is called when the container in which
the agent of the role lives is started. After, :meth:`mango.Role.on_ready` is called, when all
containers of the ``activate`` statement have been started.

When the role or agent got removed, or the container shut down, the hook-method ``on_stop`` will be called, so you can do some cleanup or send last messages before the life ends.

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
    After a shutdown or removal a role is **not** supposed to be reused! If you want to deactivate a role temporarily use the methods ``activate`` and ``deactivate`` of the RoleContext.

Sharing Data
************
There are two possible ways to share data between the roles.

1. Using the data container in the RoleContext :meth:`mango.RoleContext.data`
2. Creating explicit models using the model API of the RoleContext :meth:`mango.RoleContext.get_or_create_model`

The first way is pretty straightforward. For example:

.. testcode::

    from mango import Role, agent_composed_of

    class MyRole(Role):
        def setup(self):
            self.context.data.my_item = "hello"

    agent = agent_composed_of(MyRole())
    print(agent.roles[0].context.data.my_item)

.. testoutput::

    hello

The stored entry ``my_item`` can be used in every other role of the same agent now.

The second way needs a bit more preparations. First we need to define a model as python class.
The class object will be used as key, so every model-type can be stored exactly once.


.. testcode::

    class MyModel:
        def __init__(self):
            self.my_item = ""

    class MyRole(Role):
        def setup(self):
            mymodel = self.context.get_or_create_model(MyModel)
            mymodel.my_item = 'hello'

    agent = agent_composed_of(MyRole())
    print(agent.roles[0].context.get_or_create_model(MyModel).my_item)

.. testoutput::

    hello


One advantage of this approach is that a model is subscribable using the method :meth:`mango.RoleContext.subscribe_model`.
To make use of this every time the models changed :meth:`mango.RoleContext.update` has to be called.


Handle Messages
***************
As in a normal agent implementation, roles can handle incoming messages.
To add a message handler you can use :meth:`mango.RoleContext.subscribe_message`.
This method expects, besides the role and a handle method, a message condition function.
The handle method must have exactly two arguments (excl. ``self``) ``content`` and ``meta``.
The condition function must have exactly one argument ``content``.
The idea of the condition function is to allow to define a condition filtering incoming messages,
so you only handle one type of message per handler.
Furthermore you can define a ``priority`` of the message subscription, this will be used to
determine the message dispatch order (lower number = earlier execution, default=0).

.. testcode::

    from mango import Role

    class Ping:
        pass

    class MyRole(Role):
        def setup(self):
            self.context.subscribe_message(self,
                self.handle_ping,
                lambda content, meta: isinstance(content, Ping)
            )

        def handle_ping(self, content, meta):
            print('Ping received!')

    async def show_handle_sub():
        my_composed_agent = agent_composed_of(MyRole())
        async with run_with_tcp(1, my_composed_agent) as container:
            await container.send_message(Ping(), my_composed_agent.addr)

    asyncio.run(show_handle_sub())

.. testoutput::

    Ping received!

Deactivate/Activate other Roles
*******************************

Sometimes you might want to deactivate the functionality of a whole role, for example when
you entered a new coalition you don't want to accept new coalition invites. It would of course
be possible to manage this case with shared data and controlling flags, but this requires a lot
of additional code and might lead to errors when implementing it. Furthermore, it increases the
complexity of the implemented roles. To tackle this scenario a native deactivation/activation of
roles is possible in mango. To deactivate a role the method :meth:`mango.RoleContext.deactivate`
can be used. To activate it again, use :meth:`RoleContext.activate`. When a role is deactivated

1. it is not possible to handle messages anymore
2. the role will not get updates on shared models anymore
3. all scheduled tasks get suspended.

When a role activated again all three point are completely reverted.

.. note::
    Suspending of tasks might not work immediately, as it intercepts ``__await__``.

========
Role-API
========
Besides inheriting from the ``Agent``-class there is another option to integrate features into an agent: the role API.
The idea of using roles is to divide the functionality of an agent by responsibility in a structured way. The target of this API is to increase the reusability and the maintainability of the agents components. To achieve this the role API works with orchestration rather than inheriting to extend the agents features.


***************
The RoleContext
***************
The role context is the API to the environment of the role. It provides functionality to interact with other roles, to send messages to other agents or simply to fetch some meta data.


********
The Role
********
To implement a role you have to extend the abstract class ``mango.Role``. Concrete instances of implementations can be assigned to the general ``mango.RoleAgent``.

Lifecycle
*********
The first step in the roles life is the instantiation via ``__init__``. This is done by the user itself and can be used to configure the roles behavior. The next step is adding the role to a ``RoleAgent`` using ``add_role``. The role will get notified by this through the method ``setup``. After adding the role the ``RoleContext`` is available, which represents the environment (container, agent, other roles). When the role or agent got removed, or the container shut down, the hook-method ``on_stop`` will be called, so you can do some cleanup or send last messages before the life ends.

.. note::
    After a shutdown or removal a role is **not** supposed to be reused! When you want to deactivate a role temporarily use the methods ``activate`` and ``deactivate`` of the RoleContext.

Sharing Data
************
There are two possible was to share data between the roles.

1. Using the data container in the RoleContext (``RoleContext.data``)
2. Creating explicit models using the model API of the RoleContext(``RoleContext.get_or_create_model``)

The first way is pretty straightforward. For example:

.. code-block:: python3

    ...
    class MyRole(Role):
        def setup(self):
            self.context.data.my_item = "hello"

The stored entry ``my_item`` can be used in every other role of the same agent now.

The second way needs a bit more preparations. First we need to define a model as python class. The class object will be used as key, so every model-type can be stored exactly once.


.. code-block:: python3

    class MyModel:
        def __init__(self):
            self.my_item = ""
    ...
    class MyRole(Role):
        def setup(self):
            mymodel = self.context.get_or_create_model(MyModel)
            mymodel.my_item = 'hello'

One advantage of this approach is that a model is subscribable using the method ``RoleContext.subscribe_model``. To make use of this every time the models changed ``RoleContext.update`` has to be called.


Handle Messages
***************
As in a normal agent implementation, roles can handle incoming messages. To add a message handler you can use ``RoleContext.subscribe_message``. This method expects, besides the role and a handle method, a message condition function. The handle method must have exactly two arguments (excl. ``self``) ``content`` and ``meta``. The condition function must have exactly one argument ``content``. The idea of the condition function is to allow to define a condition filtering incoming messages, so you only handle one type of message per handler. Furthermore you can define a ``priority`` of the message subscription, this will be used to determine the message dispatch order (lower number = earlier execution, default=0).

.. code-block:: python3

    ...
    class MyRole(Role):
        def setup(self):
            self.context.subscribe_message(self, self.handle_ping, lambda content: isinstance(content, Ping))

        def handle_ping(self, content, meta):
            print('Ping received!')


Deactivate/Activate other Roles
*******************************
Sometimes you might want to deactivate the functionality of a whole role, for example when you entered a new coalition you don't want to accept new coalition invites. It would of course be possible to manage this case with shared data and controlling flags, but this requires a lot of additional code and might lead to errors when implementing it. Furthermore, it increases the complexity of the implemented roles. To tackle this scenario a native deactivation/activation of roles is possible in mango. To deactivate a role the method ``RoleContext.deactivate`` can be used. To activate it again, use ``RoleContext.activate``. When a role is deactivated

1. it is not possible to handle messages anymore
2. the role will not get updates on shared models anymore
3. all scheduled tasks get suspended.

When a role activated again all three point are completely reverted.

.. note::
    Suspending of tasks might not work immediately, as it intercepts ``__await__``.

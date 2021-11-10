"""
API classes for using the role system. The role system is based on the idea, that
everything an agent can do, is described as role/responsibility and is implemented in
one separate class. For example participating in a coalition would be a separate role,
monitoring grid voltage another one.

A role is part of a :class:`RoleAgent` which inherits from :class:`Agent`.
Depending on what you need there are different role classes:
* SimpleReactiveRole: handling a specific message (e.g. pong-Role)
* ProactiveRole: for time dependent not reactive work loads (e.g. monitoring)
* Role: generic interface for all possible behavioral styles

There are essentially two APIs for acting resp reacting:
* [Reacting] :func:`RoleContext.subscribe_message`, which allows you to subscribe to
             certain message types and lets you handle the message
* [Acting] :func:`RoleContext.schedule_task`, this allows you to schedule a task with
            delay/repeating/...

To interact with the environment an instance of the role context is provided. This context
provides methods to share data with other roles and to communicate with other agents. 

A message can be send using the method :func:`RoleContext.send_message`.

There are often dependencies between different parts of an agent, there are options to
interact with other roles: Roles have the possibility to use shared models and to act on
changes of these models. A role can subscribe specific data that another role provides.
To set this up, a model has to be created via
:func:`RoleContext.get_or_create_model`. To notify other roles
:func:`RoleContext.update` has to be called. In order to let a Role subscribe to a model you can use
:func:`subscribe_model`.
If you prefer a lightweight variant you can use :func:`RoleContext.data` to assign/access shared data.

Furthermore there are two lifecycle methods to know about:
* :func:`Role.setup` is called when the Role is added to the agent, so its the perfect place
                     for initialization and scheduling of tasks
* :func:`Role.on_stop` is called when the container the agent lives in, is shut down
"""
from abc import ABC, abstractmethod
from typing import Type, Union, Tuple, Optional, Any, Dict, TypeVar
from datetime import datetime

from mango.util.scheduling import ScheduledTask

T = TypeVar('T')


class RoleContext(ABC):
    """Abstract class RoleContext. The context can be seen as the bridge to the agent and the
    container the agent lives in. Every interaction with the environment or other roles will
    happen through the context.
    """

    def __init__(self) -> None:
        super().__init__()
        self.data = self._create_container()

    @abstractmethod
    def _get_container(self):
        pass

    @property
    def data(self):
        """Return data container of the agent

        :return: the data container
        :rtype: DataContainer
        """
        return self._get_container()

    @abstractmethod
    def get_or_create_model(self, cls: Type[T]) -> T:
        """Returns (or creates) a model of the given type `cls`. The type must have an empty
        constructor. When using this method a managed model will be accessed/created, this
        allows you to observe the model in other roles as well. There is always exactly one instance
        per class.


        :param cls: type of the model you want to get/create

        :return: role model
        """

    @abstractmethod
    def update(self, role_model):
        """Notifies the agent that the role_model parameter has been updated. Every role which
        subscribed to the model type will get notified about the change.

        :param role_model: the role model, which got updated
        """

    @abstractmethod
    def subscribe_model(self, role, role_model_type):
        """Subscribe the `role` to a model type. When the model is update with `update`, the role
        will get notified with invoking :func:`Role.on_change_model`

        :param role: the role which want to subscribe
        :param role_model_type: type of the role model
        """

    @abstractmethod
    def subscribe_message(self, role, method, message_condition):
        """Subscribe to a specific message, given by `message_condition`.

        :param role: the role
        :param method: the method, which should get invoked, have to match the correct
                             signature (content, meta)
        :param message_condition: the condition which have to be fulfilled to receive the
                                        message. (Object) -> bool
        """

    @abstractmethod
    def subscribe_send(self, role, method):
        """Subscribe to all messages sent in the agent.

        :param role: the role
        :param method: method, which should get called, when a message is sent (must match the
                             signature of :func:RoleContext.send_message)
        """

    @abstractmethod
    def schedule_conditional_task(self, coroutine, condition_func, lookup_delay=0.1, src = None):
        """Schedule a task when a specified condition is met.

        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param condition_func: function for determining whether the confition is fullfiled
        :type confition_func: lambda () -> bool
        :param lookup_delay: delay between checking the condition
        :type lookup_delay: float
        :param src: creator of the task
        :type src: Object
        """

    @abstractmethod
    def schedule_datetime_task(self, coroutine, date_time: datetime, src = None):
        """Schedule a task at specified datetime.

        :param coroutine: coroutine to be scheduled
        :type coroutine: Coroutine
        :param date_time: datetime defining when the task should start
        :type date_time: datetime
        :param src: creator of the task
        :type src: Object
        """

    @abstractmethod
    def schedule_periodic_task(self, coroutine_func, delay, src = None):
        """Schedule an open end peridocally executed task.

        :param coroutine_func: coroutine function creating coros to be scheduled
        :type coroutine_func:  Coroutine Function
        :param delay: delay in between the cycles
        :type dealy: float
        :param src: creator of the task
        :type src: Object
        """

    @abstractmethod
    def schedule_instant_task(self, coroutine, src = None):
        """Schedule an instantly executed task.

        :param coroutine: coroutine to be scheduled
        :type coroutine: 
        :param src: creator of the task
        :type src: Object
        """

    @abstractmethod
    def schedule_task(self, task: ScheduledTask, src=None):
        """Schedule a task using the agents scheduler.

        :param task: task to be scheduled
        :type task: ScheduledTask
        :param src: creator of the task
        :type: Object
        """

    @abstractmethod
    async def send_message(self, content,
                           receiver_addr: Union[str, Tuple[str, int]], *,
                           receiver_id: Optional[str] = None,
                           create_acl: bool = False,
                           acl_metadata: Optional[Dict[str, Any]] = None,
                           mqtt_kwargs: Dict[str, Any] = None,
                           ):
        """Delegate to :func:`Container.send_message`.

        :param content: the content
        :param receiver_addr: the address of the receiver
        :param receiver_id: id of the receiver
        :param create_acl: whether you want to wrap the message in an ACL
        :param acl_metadata: the ACL-metadata
        :param mqtt_kwargs: kwargs for MQTT
        """

    @abstractmethod
    def addr(self) -> Union[str, Tuple[str, int]]:
        """Return the address of the agent, the role is running in

        :return: the address tuple (IP, PORT) or in MQTT (TOPIC)
        """

    @abstractmethod
    def aid(self) -> str:
        """Return the id of the agent, the role is running in

        :return: the id as string
        """

    @abstractmethod
    def inbox_length(self) -> int:
        """return the overall inbox length of the agent

        :return: inbox_length of the agent
        """

    @abstractmethod
    def deactivate(self, role) -> None:
        """Deactivate the given role. As a consequence, the roles task will be paused and
        no messages will be handled any more.

        :param role: the role to deactivate
        :type role: Role
        """

    @abstractmethod
    def activate(self, role) -> None:
        """Activate the given role.

        :param role: the role to activate
        :type role: Role
        """

class Role(ABC):
    """General role class, defining the API every role can use. A role implements one responsibility
    of an agent.

    Every role
    must be added to a :class:`RoleAgent` and is defined by some lifecycle methods:
    * :func:`Role.setup` is called when the Role is added to the agent, so its the perfect place for
                         initialization and scheduling of tasks
    * :func:`Role.on_stop` is called when the container the agent lives in, is shut down

    To interact with the environment you have to use the context, accessible via :func:Role.context.
    """

    def __init__(self) -> None:
        """Initialize the roles internals.
        !!Care!! the role context is unknown at this point!
        """
        self._context = None

    def bind(self, context: RoleContext) -> None:
        """Method used internal to set the context, do not override!

        :param context: the role context
        """
        self._context = context

    @property
    def context(self) -> RoleContext:
        """Return the context of the role. This context can be send as bridge to the agent.

        :return: the context of the role
        """
        return self._context

    def setup(self) -> None:
        """Lifecycle hook in, which will be called on adding the role to agent. The role context
        is known from hereon.
        """

    def on_change_model(self, model) -> None:
        """Will be invoked when a subscribed model changes via :func:`RoleContext.update`.

        :param model: the model
        """

    def on_deactivation(self, src) -> None:
        """Hook in, which will be called when another role deactivates this instance (temporarily)
        """

    async def on_stop(self) -> None:
        """Lifecycle hook in, which will be called when the container is shut down or if the role got removed.
        """


class SimpleReactiveRole(Role):
    """Special role for implementing a simple reactive behavior. In opposite to the normal role,
    you don't have to subscribe to message here, you can just override
    :func:`SimpleReactiveRole.handle_msg` and you will receive every message. When you want
    to filter those messages, you cant just override :func:`SimpleReactiveRole.is_applicable`.

    The role is made for reacting to messages in a simple uniform way, when you need to react to
    multiple different message, then the general generic Role class might be the better choice.
    """

    def setup(self):
        self.context.subscribe_message(
            self, self.handle_msg, self.is_applicable)

    @abstractmethod
    def handle_msg(self, content, meta: Dict[str, Any]) -> None:
        """Handle a message. The type of the messages is defined
        by :func:`SimpleReactiveRole.is_applicable`

        :param content: the content
        :param meta: the meta-dict.
        """

    def is_applicable(self, content, meta: Dict[str, Any]) -> bool:
        """Defines which messages can be handled by the role.

        :param content: the content of the message
        :param meta: the meta of the message

        :return: True, when the message should be handled, False otherwise
        """
        return True


class ProactiveRole(Role):
    """Proactive role, which marks a role as pure active role, generally without reactive
    elements (its not checked technically!).

    The only difference to the generic Role is that you are forced to override :func:`Role.setup`,
    so the class is more of documentary nature.
    """

    @abstractmethod
    def setup(self) -> None:
        pass

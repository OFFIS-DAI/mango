

========
Agents and container in general
========
You can think of agents as independent pieces of software running in parallel. Agents are able to perceive their environment, receive some input and create some output. In order to speed up message exchange between agents that run on the same physical hardware, agents usually live in ``container``. This structure allows agents from the same container to excahnge messages without having to send it thorugh the network. A container is usually responsible for everything regarding the message distribution between agents.

========
mango container
========

========
mango agents
========
In mango agents can be implemented by inheriting from the abstract class ``mango.core.agent.Agent``. This class provides
basic functionality such as to register the agent at the container or to constantly check the inbox for incoming messages. Every agent lives in exactly one container and therefore an instance of a container has to be provided.
Custom agents that inherit from the ``Agent`` class are able to receive and messages from other agents via the method ``handle_message``. Hence this method has to be overwirtten. The structure of this method looks like this:
.. code-block:: python3
        @abstractmethod
    def handle_msg(self, content, meta: Dict[str, Any]):

        raise NotImplementedError

When an agent 
When creating an agent, an instance of a container has to be provided

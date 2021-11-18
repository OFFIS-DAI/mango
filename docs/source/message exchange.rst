========
Message exchange
========

***************
Receiving messages
***************
Custom agents that inherit from the ``Agent`` class are able to receive messages from other agents via the method ``handle_message``. Hence this method has to be overwritten. The structure of this method looks like this:

.. code-block:: python3

    @abstractmethod
    def handle_msg(self, content, meta: Dict[str, Any]):

        raise NotImplementedError

Once a message arrives at a container, the container is responsible to deserialize the message and to split the content from all meta information. While the meta information may include e. g.  information about the sender of the message or about the performative, the content parameter holds the actual content of the message. The exact structure of the ``ACL-messages`` that are exchanged within mango is described here ZZZ.

A simple agent, that just prints the content and meta information of incoming messages could look like this:

.. code-block:: python3

    from mango.core.agent import Agent

    class SimpleReceivingAgent(Agent):
        def __init__(self, container):
            super().__init__(container)

        def handle_msg(self, content, meta):
            print(f'{self._aid} received a message with content {content} and'
                f'meta {meta}')


***************
Sending messages
***************

Agents are able to send messages to other agents via the container method send_message:

.. code-block:: python3

    async def send_message(self, content,
                            receiver_addr: Union[str, Tuple[str, int]], *,
                            receiver_id: Optional[str] = None,
                            create_acl: bool = False,
                            acl_metadata: Optional[Dict[str, Any]] = None,
                            mqtt_kwargs: Dict[str, Any] = None,
                            ) -> bool:

To send a tcp_message, the receiver address and receiver id (the agent id of the receiving agent) has to be provided. This is how a simple 'hello world' message could be within an agent:

.. code-block:: python3

    self.container.send_message(content='hello world', receiver_addr=('localhost',5556), receiver_id='agent_0, create_acl=True)

By setting the parameter ``create_acl`` the container autmatically wraps all provided information into an acl message.






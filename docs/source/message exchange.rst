========
Message exchange
========

***************
Receiving messages
***************
Custom agents that inherit from the ``Agent`` class are able to receive messages from
other agents via the method ``handle_message``.
Hence this method has to be overwritten. The structure of this method looks like this:

.. code-block:: python3

    @abstractmethod
    def handle_message(self, content, meta: Dict[str, Any]):

        raise NotImplementedError

Once a message arrives at a container,
the container is responsible to deserialize the message and
to split the content from all meta information.
While the meta information may include e. g.
information about the sender of the message or about the performative,
the content parameter holds the actual content of the message.

..
    **COMMENT**
    The exact structure of the ``ACL-messages`` that are exchanged within
    mango is described here ZZZ. **TODO**

A simple agent, that just prints the content and meta information of incoming messages could look like this:

.. code-block:: python3

    from mango import Agent

    class SimpleReceivingAgent(Agent):
        def __init__(self, container):
            super().__init__(container)

        def handle_message(self, content, meta):
            print(f'{self.aid} received a message with content {content} and'
                f'meta {meta}')


***************
Sending messages
***************

Agents are able to send messages to other agents via the container method send_message:

.. code-block:: python3

    async def send_message(self, content,
                            receiver_addr: Union[str, Tuple[str, int]], *,
                            receiver_id: Optional[str] = None,
                            **kwargs) -> bool:


To send a tcp message, the receiver address and receiver id (the agent id of the receiving agent)
has to be provided.
`content` defines the content of the message.
This will appear as the `content` argument at the receivers handle_message() method.


If you want to send an ACL-message use the method ``container.send_acl_message``, which will wrap the content in a ACLMessage using ``create_acl`` internally.

.. code-block:: python3

    async def send_acl_message(self, content,
                            receiver_addr: Union[str, Tuple[str, int]], *,
                            receiver_id: Optional[str] = None,
                            acl_metadata: Optional[Dict[str, Any]] = None,
                            **kwargs) -> bool:

The argument ``acl_metadata`` enables to set all meta information of an acl message.
It expects a dictionary with the field name as string as a key and the field value as key.
For example:

.. code-block:: python3

    from mango.messages.message import Performatives

    example_acl_metadata = {
        'performative': Performatives.inform,
        'sender_id': 'agent0',
        'sender_addr': ('localhost', 5555),
        'conversation_id': 'conversation01'
    }

The argument ``kwargs`` can be used to set specific configs, if the container is connected via MQTT
to a message broker.

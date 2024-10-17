================
Message exchange
================

******************
Receiving messages
******************
Custom agents that inherit from the :class:`mango.Agent` class are able to receive messages from
other agents via the method ``handle_message``.
Hence this method has to be overwritten. The structure of this method looks like this:

.. code-block:: python3

    @abstractmethod
    def handle_message(self, content, meta: Dict[str, Any]):
        raise NotImplementedError

Once a message arrives at a container, the container is responsible to deserialize the message and
to split the content from all meta information.

While the meta information may include e. g.
information about the sender of the message or about the performative,
the content parameter holds the actual content of the message.

There are two entries always present.

* "sender_addr": protocol address of the sending agent
* "sender_id": aid of the sending agent

As mango will usually expect an instance of :meth:`mango.AgentAddress` for describing addresses of agents, we
recommend to use :meth:`mango.sender_addr` to retreive the sender information from the meta data.

A simple agent, that just prints the content and meta information of incoming messages could look like this:

.. testcode::

    from mango import Agent

    class SimpleReceivingAgent(Agent):
        def __init__(self):
            super().__init__()

        def handle_message(self, content, meta):
            print(f'{self.aid} received a message with content {content} and '
                f'meta {meta}')


****************
Sending messages
****************

Agents are able to send messages to other agents via the container method send_message:

.. code-block:: python3

    async def send_message(self,
        content,
        receiver_addr: AgentAddress,
        **kwargs,
    ) -> bool


To send a tcp message, two parameters need to be set, ``content``, which defines the content of the message, and ``receiver_addr``, which describes the destination. The ``receiver_addr``
needs to be provided as :class:`mango.AgentAddress`. In most cases this can be created using several convenience functions (:meth:`mango.sender_addr`, :meth:`mango.Agent.addr`), if that
is not possible it should be created with :meth:`mango.addr`.

If you want to send an ACL-message use the method ``create_acl`` to create the ACL content and send it with the regular ``send_message``-method internally.

The argument ``kwargs`` can be used to put custom key,value pairs in the metadata of the message. For some protocols it might be possible that these metadata is additionally
interpreted internally.

With this knowledge, we can now send a message to the ``SimpleReceivingAgent``:

.. testcode::

    import asyncio
    from mango import run_with_tcp

    async def send_to_receiving():
        receiving_agent = SimpleReceivingAgent()
        sending_agent = SimpleReceivingAgent()

        async with run_with_tcp(1, receiving_agent, sending_agent) as cl:
            await sending_agent.send_message("Hey!", receiving_agent.addr)
            await asyncio.sleep(0.1)

    asyncio.run(send_to_receiving())

.. testoutput::

    agent0 received a message with content Hey! and meta {'sender_id': 'agent1', 'sender_addr': ('127.0.0.1', 5555), 'receiver_id': 'agent0', 'network_protocol': 'tcp', 'priority': 0}

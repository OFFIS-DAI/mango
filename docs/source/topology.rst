================
Topologies
================

The framework provides out of the box support for creating and distributing topologies to the agents.
This can be done using either :meth:`mango.create_topology` or :meth:`mango.per_node`. With the first method
a topology has to be created from scratch, and for every node you need to assign the agents.

.. testcode::

    import asyncio
    from typing import Any

    from mango import Agent, run_with_tcp, create_topology

    class TopAgent(Agent):
        counter: int = 0

        def handle_message(self, content, meta: dict[str, Any]):
            self.counter += 1

    async def start_example():
        agents = [TopAgent(), TopAgent(), TopAgent()]
        with create_topology() as topology:
            id_1 = topology.add_node(agents[0])
            id_2 = topology.add_node(agents[1])
            id_3 = topology.add_node(agents[2])
            topology.add_edge(id_1, id_2)
            topology.add_edge(id_1, id_3)

        async with run_with_tcp(1, *agents):
            for neighbor in agents[0].neighbors():
                await agents[0].send_message("hello neighbors", neighbor)
            await asyncio.sleep(0.1)

        print(agents[1].counter)
        print(agents[2].counter)

    asyncio.run(start_example())

.. testoutput::

    1
    1

The other method would be to use :meth:`mango.per_node`. Here you want to create a topology beforehand, this can be done using :meth:`mango.custom_topology` by providing
a networkx Graph.

.. testcode::

    import asyncio
    from typing import Any

    from mango import Agent, run_with_tcp, per_node, complete_topology

    class TopAgent(Agent):
        counter: int = 0

        def handle_message(self, content, meta: dict[str, Any]):
            self.counter += 1

    async def start_example():
        topology = complete_topology(3)
        for node in per_node(topology):
            agent = TopAgent()
            node.add(agent)

        async with run_with_tcp(1, *topology.agents):
            for neighbor in topology.agents[0].neighbors():
                await topology.agents[0].send_message("hello neighbors", neighbor)
            await asyncio.sleep(0.1)

        print(topology.agents[1].counter)
        print(topology.agents[2].counter)

    asyncio.run(start_example())

.. testoutput::

    1
    1

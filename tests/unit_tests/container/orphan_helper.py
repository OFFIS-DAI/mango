"""Helper process for the orphaned-agent-process test.

Run as a script, it starts an agent process and then kills itself the way a
SIGKILL or a crash would: without running any interpreter shutdown, so nothing
gets the chance to signal the agent process. The agent registered inside that
process writes a marker file from ``on_stop``, which is how the test sees that
the agent process noticed and shut itself down.
"""

import asyncio
import os
import pathlib
import sys

from mango import Agent, activate, create_tcp_container

MARKER_ENV = "MANGO_ORPHAN_MARKER"


class OrphanAgent(Agent):
    def handle_message(self, content, meta):
        pass

    async def on_stop(self):
        marker = os.environ.get(MARKER_ENV)
        if marker:
            with open(marker, "w") as f:
                f.write(str(os.getpid()))


def creator(container):
    return [container.register(OrphanAgent(), suggested_aid="orphan_probe")]


async def _main(pidfile, agent_creator):
    container = create_tcp_container(
        addr=("127.0.0.1", 16151), copy_internal_messages=False
    )
    handle = await container.as_agent_process(agent_creator=agent_creator)
    with open(pidfile, "w") as f:
        f.write(str(handle.pid))
    async with activate(container):
        await asyncio.sleep(0.5)
        os._exit(7)


if __name__ == "__main__":
    # The agent process has to import the creator, and a function defined in
    # __main__ does not survive that. Importing this file again under its real
    # module name gives the child something it can resolve.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import orphan_helper

    asyncio.run(_main(sys.argv[1], orphan_helper.creator))

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

from mango.container.core import Container

from ..messages.codecs import Codec
from ..util.clock import ExternalClock

logger = logging.getLogger(__name__)


@dataclass
class MosaikAgentMessage:
    message: bytes
    time: float
    receiver: str


@dataclass
class MosaikContainerOutput:
    duration: float
    messages: List[MosaikAgentMessage]
    next_activity: Optional[float]


class MosaikContainer(Container):
    """ """

    clock: ExternalClock  # type hint

    def __init__(
        self,
        *,
        addr: str,
        codec: Codec,
        loop: asyncio.AbstractEventLoop,
    ):
        """
        Initializes a MosaikContainer. Do not directly call this method but use
        the factory method of **Container** instead
        :param addr: The container sid / eid respectively
        :param codec: The codec to use
        :param loop: Current event loop
        proto as codec
        """

        clock = ExternalClock()
        super().__init__(
            addr=addr,
            codec=codec,
            loop=loop,
            name=addr,
            clock=clock,
        )

        self.running = True
        self.current_start_time_of_step = time.time()
        self._new_internal_message: bool = False
        self.message_buffer = []

    async def send_message(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        *,
        receiver_id: Optional[str] = None,
        **kwargs,
    ) -> bool:
        """
        The Container sends a message to an agent according the container protocol.

        :param content: The content of the message
        :param receiver_addr: Address if the receiving container
        :param receiver_id: The agent id of the receiver
        :param kwargs: Additional parameters to provide protocol specific settings
        """
        message = content

        if receiver_addr == self.addr:
            if not receiver_id:
                receiver_id = message.receiver_id
            # internal message
            receiver = self._agents.get(receiver_id, None)
            if receiver is None:
                logger.warning(
                    f"Sending internal message not successful, receiver id unknown;{receiver_id}"
                )
                return False
            self._new_internal_message = True
            default_meta = {"network_protocol": "mosaik"}
            success = self._send_internal_message(
                message=message,
                default_meta=default_meta,
                target_inbox_overwrite=receiver.inbox,
            )
        else:
            success = await self._send_external_message(receiver_addr, message)

        return success

    async def _send_external_message(self, addr, message) -> bool:
        """
        Sends an external message to another container
        :param addr: The address of the receiving container
        :param message: The non-encoded message
        :return: Success or not
        """
        encoded_msg = self.codec.encode(message)
        # store message in the buffer, which will be emptied in step
        self.message_buffer.append(
            MosaikAgentMessage(
                time=time.time() - self.current_start_time_of_step + self.clock.time,
                receiver=addr,
                message=encoded_msg,
            )
        )
        return True

    async def step(
        self, simulation_time: float, incoming_messages: List[bytes]
    ) -> MosaikContainerOutput:

        if self.message_buffer:
            logger.warning(
                "There are messages in teh message buffer to be sent, at the start when step was called."
            )

        self.current_start_time_of_step = time.time()

        self.clock.set_time(simulation_time)

        # now we will decode and distribute the incoming messages
        for encoded_msg in incoming_messages:
            message = self.codec.decode(encoded_msg)

            content, acl_meta = message.split_content_and_meta()
            acl_meta["network_protocol"] = "mosaik"

            await self.inbox.put((0, content, acl_meta))

        # now wait for the msg_queue to be empty
        await self.inbox.join()

        # now wait for all agents to terminate
        # we need to loop here, because we might need to join the agents inbox another times in case we send internal
        # messages
        while True:
            self._new_internal_message = False
            for agent in self._agents.values():
                await agent.inbox.join()  # make sure inbox of agent is empty and all messages are processed
                # TODO In the following we should also be able to recognize manual sleeps (maybe)
                await agent._scheduler.tasks_complete_or_sleeping()  # wait until agent is done with all tasks
            if not self._new_internal_message:
                # if there have
                break
        # now all agents should be done
        end_time = time.time()

        messages_this_step, self.message_buffer = self.message_buffer, []

        return MosaikContainerOutput(
            duration=end_time - self.current_start_time_of_step,
            messages=messages_this_step,
            next_activity=self.clock.get_next_activity(),
        )

    async def shutdown(self):
        """
        calls shutdown() from super class Container
        """
        await super().shutdown()

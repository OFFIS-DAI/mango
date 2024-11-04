import logging
import time
from dataclasses import dataclass

from mango.agent.core import AgentAddress
from mango.container.core import Container
from mango.container.mp import ContainerMirrorData

from ..messages.codecs import Codec
from ..messages.message import MangoMessage
from ..util.clock import ExternalClock
from ..util.termination_detection import tasks_complete_or_sleeping

logger = logging.getLogger(__name__)


@dataclass
class ExternalAgentMessage:
    message: bytes
    time: float
    receiver: str


@dataclass
class ExternalSchedulingContainerOutput:
    duration: float
    messages: list[ExternalAgentMessage]
    next_activity: None | float


def ext_mirror_container_creator(
    container_data, loop, message_pipe, main_queue, event_pipe, terminate_event
):
    return ExternalSchedulingContainer(
        addr=container_data.addr,
        codec=container_data.codec,
        clock=container_data.clock,
        loop=loop,
        mirror_data=ContainerMirrorData(
            message_pipe=message_pipe,
            event_pipe=event_pipe,
            terminate_event=terminate_event,
            main_queue=main_queue,
        ),
        **container_data.kwargs,
    )


class ExternalSchedulingContainer(Container):
    """ """

    def __init__(
        self,
        *,
        addr: str,
        codec: Codec,
        clock: ExternalClock = None,
        **kwargs,
    ):
        """
        Initializes a ExternalSchedulingContainer. Do not directly call this method but use
        the factory method of **Container** instead
        :param addr: The container sid / eid respectively
        :param codec: The codec to use
        :param loop: Current event loop
        proto as codec
        """
        if not clock:
            clock = ExternalClock()

        super().__init__(
            addr=addr,
            name=addr,
            codec=codec,
            clock=clock,
            **kwargs,
        )

        self.current_start_time_of_step = time.time()
        self._new_internal_message: bool = False
        self.message_buffer = []

    async def send_message(
        self,
        content,
        receiver_addr: AgentAddress,
        sender_id: None | str = None,
        **kwargs,
    ) -> bool:
        """
        The Container sends a message to an agent according the container protocol.

        :param content: The content of the message
        :param receiver_addr: Address of the receiving container
        :param kwargs: Additional parameters to provide protocol specific settings
        """
        message = content

        meta = {}
        for key, value in kwargs.items():
            meta[key] = value
        meta["sender_id"] = sender_id
        meta["sender_addr"] = self.addr
        meta["receiver_id"] = receiver_addr.aid
        meta["receiver_addr"] = receiver_addr.protocol_addr

        if receiver_addr.protocol_addr == self.addr:
            receiver_id = receiver_addr.aid
            meta.update({"network_protocol": "external_connection"})
            success = self._send_internal_message(
                message=message, receiver_id=receiver_id, default_meta=meta
            )
            self._new_internal_message = True
        else:
            if not hasattr(content, "split_content_and_meta"):
                message = MangoMessage(content, meta)
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
            ExternalAgentMessage(
                time=time.time() - self.current_start_time_of_step + self.clock.time,
                receiver=addr.protocol_addr,
                message=encoded_msg,
            )
        )
        return True

    async def step(
        self, simulation_time: float, incoming_messages: list[bytes]
    ) -> ExternalSchedulingContainerOutput:
        if self.message_buffer:
            logger.warning(
                "There are messages in the message buffer to be sent, at the start when step was called."
            )

        self.current_start_time_of_step = time.time()

        self.clock.set_time(simulation_time)

        # now we will decode and distribute the incoming messages
        for encoded_msg in incoming_messages:
            message = self.codec.decode(encoded_msg)

            content, meta = message.split_content_and_meta()
            meta["network_protocol"] = "external_connection"

            await self.inbox.put((0, content, meta))

        # now wait for the msg_queue to be empty
        await self.inbox.join()

        # now wait for all agents to terminate
        # we need to loop here, because we might need to join the agents inbox another times in case we send internal
        # messages
        while True:
            self._new_internal_message = False
            await tasks_complete_or_sleeping(self)
            # wait until all agents are done with their tasks
            if not self._new_internal_message:
                # if there have
                break
        # now all agents in this container should be done
        end_time = time.time()

        messages_this_step, self.message_buffer = self.message_buffer, []

        return ExternalSchedulingContainerOutput(
            duration=end_time - self.current_start_time_of_step,
            messages=messages_this_step,
            next_activity=self.clock.get_next_activity(),
        )

    def _create_mirror_container(self):
        return ext_mirror_container_creator

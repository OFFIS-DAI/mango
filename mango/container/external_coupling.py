import asyncio
import logging
import time
import h5py
import uuid
import numpy as np
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union

from mango.container.core import Container, ContainerMirrorData

from ..messages.codecs import Codec
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
    messages: List[ExternalAgentMessage]
    next_activity: Optional[float]


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

    clock: ExternalClock  # type hint

    def __init__(
            self,
            *,
            addr: str,
            codec: Codec,
            loop: asyncio.AbstractEventLoop,
            attack_scenario: int = 0,
            manipulation_id: int = '',
            clock: ExternalClock = None,
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
            codec=codec,
            loop=loop,
            name=addr,
            clock=clock,
        )

        self.running = True
        self.current_start_time_of_step = time.time()
        self._new_internal_message: bool = False
        self.message_buffer = []
        self._manipulated_agent = manipulation_id
        self._attack_scenario = attack_scenario
        self._msg_counter = 0
        self._manipulation_number = 0
        if self._manipulated_agent == self.addr and self._attack_scenario == 5:
            self._manipulation_number = 5  # random.randint(4, 20)
        elif self._manipulated_agent == self.addr and self._attack_scenario == 6:
            self._manipulation_number = random.randint(4, 20)
        elif self._manipulated_agent == self.addr and self._attack_scenario == 9:
            self._manipulation_number = random.randint(4, 20)
        elif self._manipulated_agent == self.addr and self._attack_scenario == 10:
            self._manipulation_number = random.randint(4, 20)
        self.excluded_neighbor = None
        self.db_file = str(self.addr) + '_msg.h5'
        with h5py.File(self.db_file, "w") as f:
            f.close()

    def store_msg(self, content, m_id):
        try:
            current_time = self.clock.time
            self._hf = h5py.File(self.db_file, 'a')
            try:
                general_group = self._hf.create_group(f'{current_time}')
            except ValueError:
                general_group = self._hf.create_group(f'{current_time}_{str(uuid.uuid4())}')
            self._hf.attrs['content'] = str(type(content))
            general_group.attrs['content'] = str(type(content))
            self._hf.attrs['m_id'] = str(m_id)
            general_group.attrs['m_id'] = str(m_id)
            general_group.create_dataset('time', data=np.float64(current_time))
            self._hf.close()
        except Exception as exc:
            print('Message storing not possible: ', exc)

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
        if self._attack_scenario == 5 and self._msg_counter >= self._manipulation_number and self._manipulated_agent == self.addr:
            # manipulated to not send any more messages
            return True
        elif self._attack_scenario == 6 and self._manipulated_agent == self.addr:
            if self.excluded_neighbor is None:
                if receiver_addr != 'generation_agent_0' and receiver_addr != 'generation_agent_2' and receiver_addr != 'aggregator_agent':
                    self.excluded_neighbor = receiver_addr
            if self._msg_counter >= self._manipulation_number and message.receiver_id == self.excluded_neighbor:
                # ignore connection
                return True
        elif self._attack_scenario == 9 and self._msg_counter >= self._manipulation_number and self._manipulated_agent == self.addr:
            # manipulated to not send any more messages
            return True
        self.store_msg(message.content, message.conversation_id)
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
            default_meta = {"network_protocol": "external_connection"}
            success = self._send_internal_message(
                message=message,
                default_meta=default_meta,
                target_inbox_overwrite=receiver.inbox,
            )
        else:
            success = await self._send_external_message(receiver_addr, message)
        self._msg_counter += 1
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
                receiver=addr,
                message=encoded_msg,
            )
        )
        return True

    async def step(
            self, simulation_time: float, incoming_messages: List[bytes]
    ) -> ExternalSchedulingContainerOutput:
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
            acl_meta["network_protocol"] = "external_connection"

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

        if self.addr == self._manipulated_agent and self._msg_counter >= self._manipulation_number and self._attack_scenario == 10:
            messages_this_step = []

        return ExternalSchedulingContainerOutput(
            duration=end_time - self.current_start_time_of_step,
            messages=messages_this_step,
            next_activity=self.clock.get_next_activity(),
        )

    async def shutdown(self):
        """
        calls shutdown() from super class Container
        """
        await super().shutdown()


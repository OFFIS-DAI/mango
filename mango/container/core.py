import asyncio
import copy
import logging
import warnings
from dataclasses import dataclass
from multiprocessing import Process, Queue, Event, Pipe
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple, Union

from ..messages.codecs import ACLMessage, Codec
from ..util.clock import Clock

import dill  # do not remove! Necessary for the auto loaded pickle reg extensions

logger = logging.getLogger(__name__)

AGENT_PATTERN_NAME_PRE = "agent"
PROCESS_POLL_DELAY = 0.1


class IPCEventType(enumerate):
    AIDS = 1


@dataclass
class IPCEvent:
    type: IPCEventType
    data: object
    pid: int


@dataclass
class ContainerMirrorData:
    addr: object
    codec: Codec
    clock: Clock


def create_agent_process_environment(
    agent_creator,
    mirror_container_creator,
    from_subprocess_queue,
    to_subprocess_queue,
    event_pipe,
    terminate_event,
):
    asyncio.set_event_loop(asyncio.new_event_loop())

    async def start_agent_loop():
        container = await mirror_container_creator(
            asyncio.get_event_loop(),
            from_subprocess_queue,
            to_subprocess_queue,
            event_pipe,
            terminate_event,
        )
        agent_creator(container)

    asyncio.run(start_agent_loop)


class Container(ABC):
    """Superclass for a mango container"""

    def __init__(
        self,
        *,
        addr,
        name: str,
        codec,
        loop,
        clock: Clock,
        copy_internal_messages=False,
    ):
        self.name: str = name
        self.addr = addr
        self.clock = clock
        self._copy_internal_messages = copy_internal_messages

        self.codec: Codec = codec
        self.loop: asyncio.AbstractEventLoop = loop

        # dict of agents. aid: agent instance
        self._agents: Dict = {}
        self._aid_counter: int = 0  # counter for aids

        self.running: bool = True  # True until self.shutdown() is called
        self._no_agents_running: asyncio.Future = asyncio.Future()
        self._no_agents_running.set_result(
            True
        )  # signals that currently no agent lives in this container

        # inbox for all incoming messages
        self.inbox: asyncio.Queue = asyncio.Queue()

        # task that processes the inbox.
        self._check_inbox_task: asyncio.Task = asyncio.create_task(self._check_inbox())

        # For agent multiprocessing support
        self._agent_processes = []
        self._terminate_sub_processes = Event()
        self._to_queues_sub_processes = {}
        self._internal_message_queue = Queue()
        self._pid_to_pipe = {}
        self._pid_to_aids = {}
        self._handle_process_events_task: asyncio.Task = asyncio.create_task(
            self._handle_process_events()
        )
        self._handle_sp_messages_task: asyncio.Task = asyncio.create_task(
            self._handle_process_messages()
        )

    def _all_aids(self):
        all_aids = list(self._agents.keys())
        for _, aids in self._pid_to_aids:
            all_aids += aids
        return set(all_aids)

    def is_aid_available(self, aid):
        """
        Check if the aid is available and allowed.
        It is not possible to register aids matching the regular pattern "agentX".
        :param aid: the aid you want to check
        :return True if the aid is available, False if it is not
        """
        return aid not in self._all_aids and not self.__check_agent_aid_pattern_match(
            aid
        )

    def __check_agent_aid_pattern_match(self, aid):
        return (
            aid.startswith(AGENT_PATTERN_NAME_PRE)
            and aid[len(AGENT_PATTERN_NAME_PRE) :].isnumeric()
        )

    def _reserve_aid(self, suggested_aid=None):
        if suggested_aid is None or not self.is_aid_available(suggested_aid):
            aid = f"{AGENT_PATTERN_NAME_PRE}{self._aid_counter}"
            self._aid_counter += 1
        else:
            aid = suggested_aid
        return aid

    def register_agent(self, agent, suggested_aid: str = None):
        """
        Register *agent* and return the agent id
        :param agent: The agent instance
        :param suggested_aid: (Optional) suggested aid, if the aid is already taken, a generated aid is used.
                              Using the generated aid-style ("agentX") is not allowed.
        :return The agent ID
        """
        if not self._no_agents_running or self._no_agents_running.done():
            self._no_agents_running = asyncio.Future()
        aid = self._reserve_aid(suggested_aid)
        self._agents[aid] = agent
        logger.debug("Successfully registered agent;%s", aid)
        return aid

    def deregister_agent(self, aid):
        """
        Deregister an agent
        :param aid:
        :return:

        """
        del self._agents[aid]
        if len(self._agents) == 0:
            self._no_agents_running.set_result(True)

    @abstractmethod
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
        :param receiver_addr: In case of TCP this is a tuple of host, port
            In case of MQTT this is the topic to publish to.
        :param receiver_id: The agent id of the receiver
        :param kwargs: Additional parameters to provide protocol specific settings
        """
        raise NotImplementedError

    async def send_acl_message(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        *,
        receiver_id: Optional[str] = None,
        acl_metadata: Optional[Dict[str, Any]] = None,
        is_anonymous_acl=False,
        **kwargs,
    ) -> bool:
        """
        The Container sends a message, wrapped in an ACL message, to an agent according the container protocol.

        :param content: The content of the message
        :param receiver_addr: In case of TCP this is a tuple of host, port
        In case of MQTT this is the topic to publish to.
        :param receiver_id: The agent id of the receiver
        :param acl_metadata: metadata for the acl_header.
        :param is_anonymous_acl: If set to True, the sender information won't be written in the ACL header
        :param kwargs: Additional parameters to provide protocol specific settings
        """
        return await self.send_message(
            self._create_acl(
                content,
                receiver_addr=receiver_addr,
                receiver_id=receiver_id,
                acl_metadata=acl_metadata,
                is_anonymous_acl=is_anonymous_acl,
            ),
            receiver_addr=receiver_addr,
            receiver_id=receiver_id,
            **kwargs,
        )

    def _create_acl(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        receiver_id: Optional[str] = None,
        acl_metadata: Optional[Dict[str, Any]] = None,
        is_anonymous_acl=False,
    ):
        """
        :param content:
        :param receiver_addr:
        :param receiver_id:
        :param acl_metadata:
        :return:
        """
        acl_metadata = {} if acl_metadata is None else acl_metadata.copy()
        # analyse and complete acl_metadata
        if "receiver_addr" not in acl_metadata.keys():
            acl_metadata["receiver_addr"] = receiver_addr
        elif acl_metadata["receiver_addr"] != receiver_addr:
            warnings.warn(
                f"The argument receiver_addr ({receiver_addr}) is not equal to "
                f"acl_metadata['receiver_addr'] ({acl_metadata['receiver_addr']}). \
                            For consistency, the value in acl_metadata['receiver_addr'] "
                f"was overwritten with receiver_addr.",
                UserWarning,
            )
            acl_metadata["receiver_addr"] = receiver_addr
        if receiver_id:
            if "receiver_id" not in acl_metadata.keys():
                acl_metadata["receiver_id"] = receiver_id
            elif acl_metadata["receiver_id"] != receiver_id:
                warnings.warn(
                    f"The argument receiver_id ({receiver_id}) is not equal to "
                    f"acl_metadata['receiver_id'] ({acl_metadata['receiver_id']}). \
                               For consistency, the value in acl_metadata['receiver_id'] "
                    f"was overwritten with receiver_id.",
                    UserWarning,
                )
                acl_metadata["receiver_id"] = receiver_id
        # add sender_addr if not defined and not anonymous
        if not is_anonymous_acl:
            if "sender_addr" not in acl_metadata.keys() and self.addr is not None:
                acl_metadata["sender_addr"] = self.addr

        message = ACLMessage()
        message.content = content

        for key, value in acl_metadata.items():
            setattr(message, key, value)
        return message

    def _find_sp_queue(self, aid):
        for pid, aids in self._pid_to_aids:
            if aid in aids:
                return self._to_queues_sub_processes[pid]
        raise ValueError("There is not aid in any subprocess.")

    def _send_internal_message(
        self,
        message,
        receiver_id,
        priority=0,
        default_meta=None,
        inbox=None,
    ) -> bool:
        target_inbox = inbox
        if receiver_id not in self._agents:
            target_inbox = self._find_sp_queue(receiver_id)

        meta = {}
        message_to_send = (
            copy.deepcopy(message) if self._copy_internal_messages else message
        )

        if target_inbox is None:
            receiver = self._agents.get(receiver_id, None)
            if receiver is None:
                logger.warning(
                    "Sending internal message not successful, receiver id unknown;%s",
                    receiver_id,
                )
                return False
            target_inbox = receiver.inbox

        if hasattr(message_to_send, "split_content_and_meta"):
            content, meta = message_to_send.split_content_and_meta()
        else:
            content = message_to_send
        meta.update(default_meta)

        target_inbox.put_nowait((priority, content, meta))
        return True

    async def _check_inbox(self):
        """
        Task that checks, if there is a message in inbox and then creates a
        task to handle message
        """

        def raise_exceptions(result):
            """
            Inline function used as a callback to tasks to raise exceptions
            :param result: result object of the task
            """
            exception = result.exception()
            if exception is not None:
                logger.warning("Exception in _check_inbox_task.")
                raise exception

        while True:
            data = await self.inbox.get()
            priority, content, meta = data
            task = asyncio.create_task(
                self._handle_message(priority=priority, content=content, meta=meta)
            )
            task.add_done_callback(raise_exceptions)
            self.inbox.task_done()  # signals that the queue object is
            # processed

    async def _handle_process_events(self):
        for _, pipe in self._pid_to_pipe:
            if pipe.poll():
                event: IPCEvent = pipe.recv()
                if event.type == IPCEventType.AIDS:
                    aid = self._reserve_aid(event.data)
                    if event.pid not in self._pid_to_aids:
                        self._pid_to_aids[event.pid] = set()
                    self._pid_to_aids[event.pid].add(aid)
                    pipe.send(aid)
            else:
                await asyncio.sleep(PROCESS_POLL_DELAY)

    async def _handle_process_messages(self):
        if not self._internal_message_queue.empty():
            message, receiver_id, prio, meta = self._internal_message_queue.get()
            await self._send_internal_message(
                message=message,
                receiver_id=receiver_id,
                priority=prio,
                default_meta=meta,
            )
        else:
            await asyncio.sleep(PROCESS_POLL_DELAY)

    async def _handle_message(self, *, priority: int, content, meta: Dict[str, Any]):
        """
        This is called as a separate task for every message that is read
        :param priority: priority of the message
        :param content: Deserialized content of the message
        :param meta: Dict with additional information (e.g. topic)
        """

        logger.debug(
            f"Received message with content and meta;{str(content)};{str(meta)}"
        )
        receiver_id = meta.get("receiver_id", None)
        if receiver_id and receiver_id in self._agents.keys():
            receiver = self._agents[receiver_id]
            await receiver.inbox.put((priority, content, meta))
        else:
            sp_queue_of_agent = self._find_sp_queue(receiver_id)
            if sp_queue_of_agent is None:
                logger.warning(
                    "Received a message for an unknown receiver;%s", receiver_id
                )
            sp_queue_of_agent.put((priority, content, meta))

    def as_agent_process(self, agent_creator, mirror_container_creator):
        to_subprocess_message_queue = Queue()
        from_pipe, to_pipe = Pipe()
        agent_process = Process(
            target=create_agent_process_environment,
            args=(
                agent_creator,
                mirror_container_creator,
                self._internal_message_queue,
                to_subprocess_message_queue,
                to_pipe,
                self._terminate_sub_processes,
            ),
        )
        agent_process.daemon = True
        self._agent_processes.append(agent_process)
        agent_process.start()
        self._to_queues_sub_processes[agent_process.pid] = to_subprocess_message_queue
        self._pid_to_pipe[agent_process.pid] = from_pipe

    async def _cancel_and_wait_for_task(self, task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def shutdown(self):
        """Shutdown all agents in the container and the container itself"""
        self.running = False
        futs = []
        for agent in self._agents.values():
            # shutdown all running agents
            futs.append(agent.shutdown())
        await asyncio.gather(*futs)

        # send a signal to all sub processes to terminate their message feed in's
        self._terminate_sub_processes.set(True)

        # wait for and tidy up processes
        for process in self._agent_processes:
            process.join()
            process.terminate()
            process.close()

        self._cancel_and_wait_for_task(self._handle_process_events_task)
        self._cancel_and_wait_for_task(self._handle_sp_messages_task)

        # cancel check inbox task
        if self._check_inbox_task is not None:
            self._cancel_and_wait_for_task(self._check_inbox_task)

        logger.info("Successfully shutdown")

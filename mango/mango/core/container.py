"""
This module contains the abstract Container class and the subclasses
TCPContainer and MQTTContainer
"""
from abc import ABC, abstractmethod
import asyncio
import logging
from typing import Optional, Union, Tuple, Dict, Any, Set
import paho.mqtt.client as paho
from .agent import Agent
from .container_protocols import ContainerProtocol
from ..messages.message import ACLMessage as json_ACLMessage
from ..messages.acl_message_pb2 import ACLMessage as proto_ACLMessage
from ..util import m_util as ut


class Container(ABC):
    """Superclass for a mango container"""
    @classmethod
    async def factory(cls, *, connection_type: str = 'tcp', codec: str = 'json',
                      addr: Optional[Union[str, Tuple[str, int]]] = None,
                      log_level: int = logging.DEBUG,
                      logging_kwargs: Dict[str, Any] = None,
                      proto_msgs_module=None,
                      mqtt_kwargs: Dict[str, Any] = None):
        """
        This method is called to instantiate a container instance, either
        a TCPContainer or a MQTTCOntainer, depending on the parameter
        connection_type.
        :param connection_type: Defines the connection type. So far only 'tcp'
        or 'mqtt' are allowed
        :param codec: Defines the codec to use. So far only 'json' or
        'protobuf' are allowed
        :param addr: the address to use. If connection_type == 'tcp': it has
        to be a tuple of (host, port). If connection_type == 'mqtt' this can
        optionally define an inbox_topic that is used similarly than
        a tcp address.
        :param log_level: The log level to use
        :param logging_kwargs: Dictionary, in which logging details can be
        specified. Kwargs are passed to m_util.configure_logger(). Possible
        kwargs are 'log_file': str, 'csv_format': bool, 'log_file_mode': str
        :param mqtt_kwargs: If connection_type == 'mqtt':
        Dictionary of keyword arguments for connection to the broker. At least
        the keys 'broker_addr' and 'client_id' have to be included.
        Ignored if connection_type == 'tcp'
        :return: The instance of a MQTTContainer or a TCPContainer
        """
        connection_type = connection_type.lower()
        if connection_type not in ['tcp', 'mqtt']:
            raise ValueError(f'Unknown connection type {connection_type}')

        if codec not in ['json', 'protobuf']:
            raise ValueError(f'Unknown codec {codec}')
        if codec == 'protobuf' and not proto_msgs_module:
            raise ValueError(f'proto_msgs_module for message definitions in'
                             ' protobuf must be provided')

        loop = asyncio.get_running_loop()

        if connection_type == 'tcp':
            # initialize TCPcontainer
            container = TCPContainer(
                addr=addr, codec=codec, loop=loop, log_level=log_level,
                logging_kwargs=logging_kwargs,
                proto_msgs_module=proto_msgs_module
            )

            # create a TCP server bound to host and port that uses the
            # specified protocol
            container.server = await loop.create_server(
                lambda: ContainerProtocol(container=container, loop=loop,
                                          codec=codec),
                addr[0], addr[1])
            return container

        if connection_type == 'mqtt':

            # get and check relevant kwargs from mqtt_kwargs
            # client_id
            client_id = mqtt_kwargs.pop('client_id', None)
            if not client_id:
                raise ValueError('client_id is requested within mqtt_kwargs')

            # broker_addr
            broker_addr = mqtt_kwargs.pop('broker_addr', None)
            if not broker_addr:
                raise ValueError('broker_addr is requested within mqtt_kwargs')

            # get parameters for Client.init()
            init_kwargs = {}
            possible_init_kwargs = ('clean_session', 'userdata', 'protocol',
                                    'transport')
            for possible_kwarg in possible_init_kwargs:
                if possible_kwarg in mqtt_kwargs.keys():
                    init_kwargs[possible_kwarg] = \
                        mqtt_kwargs.pop(possible_kwarg)


            # check if addr is a valid topic without wildcards
            if addr is not None and \
                    (not isinstance(addr, str) or '#' in addr or '+' in addr):
                raise ValueError('addr is not set correctly. It is used as '
                                 'inbox topic and must be a  string without '
                                 'any wildcards (\'#\' or \'+\')')

            # create paho.Client object for mqtt communication
            mqtt_messenger: paho.Client = paho.Client(
                client_id=client_id,
                **init_kwargs)

            # set TLS options if provided
            # expected as a dict:
            # {ca_certs, certfile, keyfile, cert_eqs, tls_version, ciphers}
            tls_kwargs = mqtt_kwargs.pop('tls_kwargs', None)
            if tls_kwargs:
                mqtt_messenger.tls_set(**tls_kwargs)

            # Future that is triggered, on successful connection
            connected = asyncio.Future()
            # callbacks to check for successful connection
            def on_con(client, userdata, flags, returncode):
                print(f'Connection Callback with the following flags: {flags}')
                loop.call_soon_threadsafe(connected.set_result, returncode)
            mqtt_messenger.on_connect = on_con

            # check broker_addr input and connect
            if isinstance(broker_addr, tuple):
                if not 0 < len(broker_addr) < 4:
                    raise ValueError(f'Invalid broker address')
                if len(broker_addr) > 0 and not isinstance(broker_addr[0],
                                                           str):
                    raise ValueError('Invalid broker address')
                if len(broker_addr) > 1 and not isinstance(broker_addr[1],
                                                           int):
                    raise ValueError('Invalid broker address')
                if len(broker_addr) > 2 and not isinstance(broker_addr[2],
                                                           int):
                    raise ValueError('Invalid broker address')
                mqtt_messenger.connect(*broker_addr, **mqtt_kwargs)

            elif isinstance(broker_addr, dict):
                if not 'hostname' in broker_addr.keys():
                    raise ValueError('Invalid broker address')
                mqtt_messenger.connect(**broker_addr, **mqtt_kwargs)

            else:
                if not isinstance(broker_addr, str):
                    raise ValueError('Invalid broker address')
                mqtt_messenger.connect(broker_addr, **mqtt_kwargs)

            print(f'[{client_id}]: Going to connect to broker '
                  f'at {broker_addr}... ', end='')

            counter = 0
            # process MQTT messages for maximum of 10 seconds to
            # receive connection callback
            while not connected.done() and counter < 100:
                mqtt_messenger.loop()
                # wait for the thread to trigger the future
                await asyncio.sleep(0.1)
                counter += 1

            if not connected.done():
                # timeout
                raise ConnectionError(
                    f'Connection to {broker_addr} could not be '
                    f'established after {counter * 0.1} seconds')
            if connected.result() != 0:
                raise ConnectionError(
                    f'Connection to {broker_addr} could not be '
                    f'set up. Callback returner error code '
                    f'{connected.result()}')

            print('done.')
            if addr is not None:
                # connection has been set up, subscribe to inbox topic now
                print(f'[{client_id}]: Going to subscribe to {addr} '
                      f'as inbox topic... ', end='')

                # create Future that is triggered on successful subscription
                subscribed = asyncio.Future()
                # set up subscription callback
                def on_sub(*args):
                    loop.call_soon_threadsafe(subscribed.set_result, True)
                mqtt_messenger.on_subscribe = on_sub

                # subscribe topic
                result, _ = mqtt_messenger.subscribe(addr, 2)
                if result != paho.MQTT_ERR_SUCCESS:
                    # subscription to inbox topic was not successful
                    mqtt_messenger.disconnect()
                    raise ConnectionError(
                        f'Subscription request to {addr} at {broker_addr} '
                        f'returned error code: {result}')

                counter = 0
                while not subscribed.done() and counter < 100:
                    # wait for subscription
                    mqtt_messenger.loop(timeout=0.1)
                    await asyncio.sleep(0.1)
                    counter += 1
                if not subscribed.done():
                    raise ConnectionError(
                        f'Subscription request to {addr} at {broker_addr} '
                        f'did not succeed after {counter * 0.1} seconds.')
                print('done.')

            # connection and subscription is sucessful, remove callbacks
            mqtt_messenger.on_subscribe = None
            mqtt_messenger.on_connect = None


            return MQTTContainer(client_id=client_id, addr=addr,
                                 log_level=log_level,
                                 logging_kwargs=logging_kwargs, loop=loop,
                                 mqtt_client=mqtt_messenger, codec=codec,
                                 proto_msgs_module=proto_msgs_module)




    def __init__(self, *, addr, log_level, logging_kwargs, name, codec,
                 proto_msgs_module=None, loop):
        self.name = name
        if logging_kwargs is None:
            logging_kwargs = {}
        self.logger = ut.configure_logger(f'{name}', log_level,
                                          **logging_kwargs)
        self.addr = addr
        self.log_level: int = log_level
        self.codec: str = codec.lower()
        if codec == 'protobuf':
            self.other_msgs = proto_msgs_module
        self.loop: asyncio.AbstractEventLoop = loop

        # dict of agents. aid: agent instance
        self._agents: Dict[str: Agent] = {}
        self._aid_counter: int = 0  # counter for aids

        # inbox for all incoming messages
        self.inbox: asyncio.Queue = asyncio.Queue()

        # task that processes the inbox. Will be started in run()
        self._check_inbox_task: Optional[asyncio.Task] = None
        self.running: bool = True  # True until self.shutdown() is called
        self._no_agents_running: asyncio.Future = asyncio.Future().set_result(
            True)  # signals that currently no agent lives in this container

        self._check_inbox_task = asyncio.create_task(self._check_inbox())

    def register_agent(self, agent):
        """
        Register *agent* and return the agent id
        :param agent: The agent instance
        :return The agent ID
        """
        if not self._no_agents_running or self._no_agents_running.done():
            self._no_agents_running = asyncio.Future()
        # self.logger.debug('got a register request')
        aid = f'agent{self._aid_counter}'
        self._aid_counter += 1
        self.logger.info(f'{aid} is registered now')
        self._agents[aid] = agent
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
    async def send_message(self, content,
                           receiver_addr: Union[str, Tuple[str, int]], *,
                           receiver_id: Optional[str] = None,
                           create_acl: bool = False,
                           acl_metadata: Optional[Dict[str, Any]] = None,
                           mqtt_kwargs: Dict[str, Any] = None,
                           ):
        """
        container sends the message of one of its own agents to a specific topic
        :param content: The content of the message
        :param receiver_addr: In case of TCP this is a tuple of host, port
        In case of MQTT this is the topic to publish to.
        :param receiver_id: The agent id of the receiver
        :param create_acl: True if an acl message shall be created around the
        content.
        :param acl_metadata: metadata for the acl_header.
        Ignored if create_acl == False
        """
        raise NotImplementedError

    def _create_acl(self,
                    content, receiver_addr: Union[str, Tuple[str, int]],
                    receiver_id: Optional[str] = None,
                    acl_metadata: Optional[Dict[str, Any]] = None):
        """
        :param content:
        :param receiver_addr:
        :param receiver_id:
        :param acl_metadata:
        :return:
        """
        acl_metadata = {} if acl_metadata is None else acl_metadata
        # analyse and complete acl_metadata
        if 'receiver_addr' not in acl_metadata.keys():
            acl_metadata['receiver_addr'] = receiver_addr
        if 'receiver_id' not in acl_metadata.keys() and receiver_id:
            acl_metadata['receiver_id'] = receiver_id
        # add sender_addr if not defined
        if 'sender_addr' not in acl_metadata.keys() and self.addr is not None:
            acl_metadata['sender_addr'] = self.addr

        if self.codec == 'json':
            # create json message
            message = json_ACLMessage()
            message.content = content

        elif self.codec == 'protobuf':
            #create protobuf message
            message = proto_ACLMessage()
            receiver_meta = acl_metadata['receiver_addr']
            if isinstance(receiver_meta, (tuple, list)):
                acl_metadata['receiver_addr'] = \
                    f'{receiver_meta[0]}:{receiver_meta[1]}'
            sender_meta = acl_metadata.get('sender_addr', None)
            if isinstance(sender_meta, (tuple, list)):
                acl_metadata['sender_addr'] = \
                    f'{sender_meta[0]}:{sender_meta[1]}'

            message.content_class = type(content).__name__
            message.content = content.SerializeToString()
        else:
            raise ValueError('Unknown Encoding')

        for key, value in acl_metadata.items():
            setattr(message, key, value)
        return message

    def split_content_and_meta_from_acl(
            self, acl_message: Union[json_ACLMessage, proto_ACLMessage],):
        """
        This function takes the content and meta information from an
        acl message.
        :param acl_message: either a json or prot ACL_message
        :return: Tuple of content, meta
        """

        if self.codec == 'json':
            # Use extract meta method from json acl_message
            meta = acl_message.extract_meta()
            content = acl_message.content
        elif self.codec == 'protobuf':
            # get string of class definition of content message
            content_class = getattr(self.other_msgs, acl_message.content_class)
            # deserialize the content message
            content = content_class()
            content.ParseFromString(acl_message.content)
            # get meta
            meta = {}
            for field in acl_message.DESCRIPTOR.fields:
                if field.name != 'content':
                    meta[field.name] = getattr(acl_message, field.name)
        else:
            raise ValueError(f'Unknown Encoding {self.codec}')

        return content, meta

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
                self.logger.warning('Exception in _check_inbox_task.')
                raise exception

        # self.logger.debug(f'Start waiting for messages')

        while True:
            data = await self.inbox.get()
            # self.logger.debug(f'Received {data}')
            priority, msg_content, meta = data
            # self.logger.debug(f'Received a message {msg}')
            task = asyncio.create_task(
                self._handle_msg(priority=priority, msg_content=msg_content,
                                 meta=meta))
            task.add_done_callback(raise_exceptions)
            self.inbox.task_done()  # signals that the queue object is
            # processed

    @abstractmethod
    async def _handle_msg(self, *,
                          priority: int, msg_content, meta: Dict[str, Any]):
        """
        This is called as a separate task for every message that is read
        :param priority: priority of the msg
        :param msg_content: Deserialized content of the message
        :param meta: Dict with additional information (e.g. topic)
        """
        raise NotImplementedError

    async def shutdown(self):
        """Shutdown all agents in the container and the container itself"""
        self.running = False
        futs = []
        for agent in self._agents.values():
            # shutdown all running agents
            futs.append(agent.shutdown())
        await asyncio.gather(*futs)

        # cancel check inbox task
        if self._check_inbox_task is not None:
            self.logger.debug('check inbox task will be cancelled')
            self._check_inbox_task.cancel()
            try:
                await self._check_inbox_task
            except asyncio.CancelledError:
                pass


class MQTTContainer(Container):
    """
    Container for agents.

       The container allows its agents to send messages to specific topics
       (via :meth:`send_message()`).
    """

    def __init__(self, *, client_id: str, addr: Optional[str],
                 log_level: int, logging_kwargs: Dict[str, Any] = None,
                 loop: asyncio.AbstractEventLoop,
                 mqtt_client: paho.Client, codec: str = 'json',
                 proto_msgs_module=None):
        """
        Initializes a container. Do not directly call this method but use
        the factory method instead
        :param client_id: The ID that the container should use when connecting
        to the broker
        :param addr: A string of the unique inbox topic to use.
        No wildcards are allowed. If None, no inbox topic will be set
        :param log_level: The log level to use
        :param logging_kwargs: Dictionary, in which logging details can be
        specified. Kwargs are passed to m_util.configure_logger(). Possible
        kwargs are 'log_file': str, 'csv_format': bool, 'log_file_mode': str
        :param mqtt_client: The paho.Client object that is used for the
        communication with the broker
        :param codec: The codec to use. Currently only 'json' or 'protobuf' are
         allowed
        :param proto_msgs: The compiled python module where the
         additional proto msgs are defined
        """
        super().__init__(log_level=log_level, logging_kwargs=logging_kwargs,
                         codec=codec, addr=addr,
                         proto_msgs_module=proto_msgs_module, loop=loop,
                         name=client_id)

        self.client_id: str = client_id
        # the configured and connected paho client
        self.mqtt_client: paho.Client = mqtt_client
        self.inbox_topic: Optional[str] = addr
        # dict mapping additionally subscribed topics to a set of aids
        self.additional_subscriptions: Dict[str, Set[str]] = {}
        # dict mapping subscribed topics to the expected class
        self.subscriptions_to_class: Dict[str, Any] = {}
        # Future for pending sub requests
        self.pending_sub_request: asyncio.Future = None

        # set the callbacks
        self._set_mqtt_callbacks()

        # start the mqtt client
        self.mqtt_client.loop_start()

    def _set_mqtt_callbacks(self):
        """
        Sets the callbacks for the mqtt paho client
        """

        def on_con(client, userdata, flags, rc):
            if rc != 0:
                self.logger.info('Connection attempt to broker failed')
            else:
                self.logger.debug('Successfully reconnected to broker.')

        self.mqtt_client.on_connect = on_con

        def on_discon(client, userdata, rc):
            if rc != 0:
                self.logger.warning('Unexpected disconnect from broker.'
                                 'Trying to reconnect')
            else:
                self.logger.debug('Successfully disconnected from broker.')

        self.mqtt_client.on_disconnect = on_discon

        def on_sub(client, userdata, mid, granted_qos):
            self.loop.call_soon_threadsafe(
                self.pending_sub_request.set_result, 0)

        self.mqtt_client.on_subscribe = on_sub

        def on_msg(client, userdata, message):
            # extract the meta information first
            meta = {
                'network_protocol': 'mqtt',
                'topic': message.topic,
                'qos': message.qos,
                'retain': message.retain,
            }
            # decode message and extract msg_content and meta
            msg_content, msg_meta = self.decode_mqtt_msg(
                payload=message.payload, topic=message.topic
            )
            # update meta dict
            meta.update(msg_meta)

            # put information to inbox
            if msg_content is not None:
                self.loop.call_soon_threadsafe(
                    self.inbox.put_nowait, (0, msg_content, meta))

        self.mqtt_client.on_message = on_msg

        self.mqtt_client.enable_logger(self.logger)

    async def shutdown(self):
        """
        Shutdown container, disconnect from broker and stop mqtt thread
        """
        await super().shutdown()
        # disconnect to broker
        self.mqtt_client.disconnect()
        self.mqtt_client.loop_stop()

    def decode_mqtt_msg(self, *, topic, payload):
        """
        deserializes a mqtt msg.
        Checks if for the topic a special class is defined, otherwise assumes
        an ACLMessage
        :param topic: the topic on which the message arrived
        :param payload: the serialized message
        :return: content and meta
        """
        meta = {}
        content = None

        # check if there is a class definition for the topic
        for sub, sub_class in self.subscriptions_to_class.items():
            if paho.topic_matches_sub(sub, topic):
                # instantiate the provided class
                content = sub_class()
                break

        if self.codec == 'json':
            if content:
                # Json message should have the method decode()
                content.decode(payload)
            else:
                # We expect an ACL Message as no specific class is defined
                acl_msg: json_ACLMessage = json_ACLMessage()
                acl_msg.decode(payload)
                content = acl_msg.content
                meta = acl_msg.extract_meta()

        elif self.codec == 'protobuf':
            if content:
                content.ParseFromString(payload)
                # empty meta on non-acl class
            else:
                # We expect an ACL Message as no specific class is defined
                acl_msg: proto_ACLMessage = proto_ACLMessage()
                acl_msg.ParseFromString(payload)
                if acl_msg.content_class:
                    content_class = getattr(self.other_msgs, acl_msg.content_class)
                    content = content_class()
                    content.ParseFromString(acl_msg.content)
                    for field in acl_msg.DESCRIPTOR.fields:
                        if field.name != 'content':
                            meta[field.name] = getattr(acl_msg, field.name)
                else:
                    self.logger.warning(f'got message {acl_msg} with undefined'
                                        f' content class from topic {topic}')
        else:
            raise ValueError('Unknown Codec')

        return content, meta

    async def _handle_msg(self, *,
                          priority: int, msg_content, meta: Dict[str, Any]):
        """
        This is called as a seperate task for every message that is read
        :param priority: priority of the msg
        :param msg_content: Deserialized content of the message
        :param meta: Dict with additional information (e.g. topic)

        """

        topic = meta['topic']
        self.logger.debug(f"recived message {msg_content} with meta {meta}")
        if topic == self.inbox_topic:
            # General inbox topic, so no receiver is speciefied by the topic
            # try to find the receiver from meta
            receiver_id = meta.get('receiver_id', None)
            if receiver_id and receiver_id in self._agents.keys():
                receiver = self._agents[receiver_id]
                await receiver.inbox.put((priority, msg_content, meta))
            else:
                self.logger.warning(f'Receiver ID {receiver_id} is unknown')
        else:
            # no inbox topic. Check who has subscribed the topic.
            receivers = set()
            for sub, rec in self.additional_subscriptions.items():
                if paho.topic_matches_sub(sub, topic):
                    receivers.update(rec)
            if not receivers:
                self.logger.warning(
                    f'Received a message at {topic} but there is no agent '
                    f'that subscribed this topic.')
            else:
                for receiver_id in receivers:
                    # self.logger.debug(
                    # f'Going to put msg to inbox of {receiver_id}')
                    receiver = self._agents[receiver_id]

                    await receiver.inbox.put((priority, msg_content, meta))

    async def send_message(self, content,
                           receiver_addr: Union[str, Tuple[str, int]], *,
                           receiver_id: Optional[str] = None,
                           create_acl: bool = False,
                           acl_metadata: Optional[Dict[str, Any]] = None,
                           mqtt_kwargs: Dict[str, Any] = None,
                          ):
        """
        container sends the message of one of its own agents to a specific
        topic
        :param content: The content of the message
        :param receiver_addr: The topic that the message should be published to
        :param receiver_id: The agent id of the receiver
        :param create_acl: True if the content is
        only part of an acl message object that is yet to be created.
        :param acl_metadata: metadata for the acl_header. Is only interpreted
        if add_acl_header == True
        :param mqtt_kwargs: Dict with possible kwargs for publishing to the
        mqtt broker. Possible fields:
            qos: The quality of service to use for publishing
            retain: Indicates, weather the retain flag should be set
        """

        if create_acl:
            message = self._create_acl(
                content=content, receiver_addr=receiver_addr,
                receiver_id=receiver_id, acl_metadata=acl_metadata)
        else:
            # the message is already complete
            message = content

        # internal message first (if retain Flag is set, it has to be send to
        # the broker
        mqtt_kwargs = {} if mqtt_kwargs is None else mqtt_kwargs
        if self.addr and receiver_addr == self.addr and \
                not mqtt_kwargs.get('retain', False):
            # self.logger.debug(f'Going to forward internal message at {topic}')
            meta = {'topic': self.addr,
                    'qos': mqtt_kwargs.get('qos', 0),
                    'retain': False,
                    'network_protocol': 'mqtt'
                    }

            content, msg_meta = self.split_content_and_meta_from_acl(message)
            meta.update(msg_meta)
            self.inbox.put_nowait((0, content, meta))

        else:
            self._send_external_message(topic=receiver_addr, message=message)

    def _send_external_message(self, *, topic: str, message):
        """

        :param topic: MQTT topic
        :param message: The ACL message
        :return:
        """
        if self.codec == 'json':
            encoded_msg = message.encode()
        elif self.codec == 'protobuf':
            encoded_msg = message.SerializeToString()
        else:
            raise ValueError('Unknown codec')
        self.logger.debug(f'Sending {message} to the broker')
        self.mqtt_client.publish(topic, encoded_msg)

    async def subscribe_for_agent(self, *, aid: str, topic: str, qos: int = 0,
                                  expected_class=None) -> bool:
        """

        :param aid: aid of the corresponding agent
        :param topic: topic to subscribe (wildcards are allowed)
        :param qos: The quality of service for the subscription
        :param expected_class: The class to expect from the topic, defaults
        to ACL
        :return: A boolean signaling if subscription was true or not
        """
        if not aid in self._agents.keys():
            raise ValueError('Given aid is not known')
        if expected_class:
            self.subscriptions_to_class[topic] = expected_class

        if topic in self.additional_subscriptions.keys():
            self.additional_subscriptions[topic].add(aid)
            return True

        self.additional_subscriptions[topic] = {aid}
        self.pending_sub_request = asyncio.Future()
        result, _ = self.mqtt_client.subscribe(topic, qos=qos)

        if result != paho.MQTT_ERR_SUCCESS:
            self.pending_sub_request.set_result(False)
            return False

        await self.pending_sub_request
        return True

    def set_expected_class(self, *, topic: str, expected_class):
        """
        Sets an expected class to a subscription
        wildcards are allowed here
        :param topic: The subscription
        :param expected_class: The expected class
        :return:
        """
        if self.codec == 'json':
            if not getattr(expected_class(), 'decode', None):
                self.logger.warning('Class {expected_class} does not'
                                    'provide the method decode(), which is'
                                    'needed for json decoding..')
        self.subscriptions_to_class[topic] = expected_class
        self.logger.debug(f'Expected class updated {self.subscriptions_to_class}')

    def deregister_agent(self, aid):
        """

        :param aid:
        :return:
        """
        super().deregister_agent(aid)
        empty_subscriptions = []
        for subscription, aid_set in self.additional_subscriptions.items():
            if aid in aid_set:
                aid_set.remove(aid)
            if len(aid_set) == 0:
                empty_subscriptions.append(subscription)

        for subscription in empty_subscriptions:
            self.additional_subscriptions.pop(subscription)
            self.mqtt_client.unsubscribe(topic=subscription)

class TCPContainer(Container):
    """
    This is a container that communicate directly with other containers
    via tcp
    """
    def __init__(self, *, addr: Tuple[str, int], codec: str,
                 loop: asyncio.AbstractEventLoop, log_level: int,
                 logging_kwargs: Dict[str, Any] = None,
                 proto_msgs_module=None):
        """
        Initializes a TCP container. Do not directly call this method but use
        the factory method of **Container** instead
        :param addr: The container address
        :param codec: The codec to use
        :param loop: Current event loop
        :param log_level: The log level to use
        :param logging_kwargs: Dictionary, in which logging details can be
        specified. Kwargs are passed to m_util.configure_logger(). Possible
        kwargs are 'log_file': str, 'csv_format': bool, 'log_file_mode': str
        :param proto_msgs_module: The module for proto msgs in case of
        proto as codec
        """
        super().__init__(addr=addr, log_level=log_level,
                         logging_kwargs=logging_kwargs, codec=codec,
                         proto_msgs_module=proto_msgs_module, loop=loop,
                         name=f'{addr[0]}:{addr[1]}')

        self.server = None  # will be set within the factory method

        self.logger.debug(f'Start running...')
        self._check_inbox_task = asyncio.create_task(self._check_inbox())
        self.running = True


    async def _handle_msg(self, *,
                          priority: int, msg_content, meta: Dict[str, Any]):
        """

        :param priority:
        :param msg_content:
        :param meta:
        :return:
        """
        # self.logger.debug(f'going to handle {msg_content}')
        self.logger.debug(f'Received msg with meta: {meta}')
        receiver_id = meta.get('receiver_id', None)
        if receiver_id and receiver_id in self._agents.keys():
            receiver = self._agents[receiver_id]
            await receiver.inbox.put((priority, msg_content, meta))
        else:
            self.logger.warning(f'Received a message for an unknown '
                                f'receiver {receiver_id}')

    async def send_message(self, content,
                           receiver_addr: Union[str, Tuple[str, int]], *,
                           receiver_id: Optional[str] = None,
                           create_acl: bool = False,
                           acl_metadata: Optional[Dict[str, Any]] = None,
                           mqtt_kwargs: Dict[str, Any] = None,
                           ):
        """
        container sends the message of one of its own agents to a specific topic
        :param content: The content of the message
        :param receiver_addr: In case of TCP this is a tuple of host, port
        :param receiver_id: The agent id of the receiver
        :param create_acl: True if an acl message shall be created around the
        content.
        :param acl_metadata: metadata for the acl_header.
        Ignored if create_acl == False
        :param mqtt_kwargs: Ignored in this class
        """
        if isinstance(receiver_addr, str):
            if ':' not in receiver_addr:
                raise ValueError(f'addr must be tuple of (host, port)'
                                 f' or a string \'host:port\', '
                                 f'but is {receiver_addr}.')
            receiver_addr = receiver_addr.split(':')
        elif not isinstance(receiver_addr, (tuple, list)):
            raise TypeError(f'{receiver_addr} is not a valid adress')
        receiver_addr = tuple(receiver_addr)

        if create_acl:
            message = self._create_acl(content=content,
                                       receiver_addr=receiver_addr,
                                       receiver_id=receiver_id,
                                       acl_metadata=acl_metadata)
        else:
            message = content

        if receiver_addr == self.addr:
            if not receiver_id:
                receiver_id = message.receiver_id
            # internal message
            self._send_internal_message(receiver_id, message)
        else:
            await self._send_external_message(receiver_addr, message)

    def _send_internal_message(self, receiver_id, message):
        """
        Sends a message to an agent that lives in the same container
        :param receiver_id: ID of the reciever
        :param message:
        :return:
        """

        receiver = self._agents.get(receiver_id, None)
        if receiver is None:
            raise KeyError(
                f'Receiver ID {receiver_id} is not known to the container '
                f'at {self.addr}')
        # TODO priority assignment could be specified here,
        priority = 0
        # self.logger.debug(f'Container will send an internal message')
        content, meta = self.split_content_and_meta_from_acl(message)
        meta['network_protocol'] = 'tcp'
        receiver.inbox.put_nowait((priority, content, meta))

    async def _send_external_message(self, addr, message):
        """
        Sends *message* to another container at *addr*
        :param addr: Tuple of (host, port)
        :param message: The message
        :return:
        """
        if addr is None or not isinstance(addr, (tuple, list)) \
                or len(addr) != 2:
            raise TypeError(f'addr must be a tuple '
                            f'of (host, port) but is {addr},')

        try:
            transport, protocol = await self.loop.create_connection(
                lambda: ContainerProtocol(container=self, loop=self.loop,
                                          codec=self.codec),
                addr[0],
                addr[1])
            # self.logger.debug('Connection established')
            if self.codec == 'json':
                protocol.write(message.encode())
            elif self.codec == 'protobuf':
                protocol.write(message.SerializeToString())

            # self.logger.debug('message sent')
            await protocol.shutdown()
            # self.logger.debug('protocol shutdown complete')
        except Exception:
            print(f'address {addr} is not valid')
            # TODO: handle properly (send notification to agent?)

    async def shutdown(self):
        """
        calles shutdown() from super class Container and closes the server
        """
        await super().shutdown()
        self.server.close()
        await self.server.wait_closed()

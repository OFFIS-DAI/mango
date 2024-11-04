import asyncio
import logging
from functools import partial
from typing import Any

import paho.mqtt.client as paho

from mango.container.core import AgentAddress, Container
from mango.container.mp import ContainerMirrorData

from ..messages.codecs import Codec
from ..messages.message import MangoMessage
from ..util.clock import Clock

logger = logging.getLogger(__name__)


def mqtt_mirror_container_creator(
    client_id,
    inbox_topic,
    container_data,
    loop,
    message_pipe,
    main_queue,
    event_pipe,
    terminate_event,
):
    return MQTTContainer(
        client_id=client_id,
        inbox_topic=inbox_topic,
        broker_addr=container_data.addr,
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


class MQTTContainer(Container):
    """
    Container for agents.

       The container allows its agents to send messages to specific topics
       (via :meth:`send_message()`).
    """

    def __init__(
        self,
        *,
        client_id: str,
        broker_addr: tuple | dict | str,
        clock: Clock,
        codec: Codec,
        inbox_topic: None | str = None,
        **kwargs,
    ):
        """
        Initializes a container. Do not directly call this method but use
        the factory method instead
        :param client_id: The ID that the container should use when connecting
        to the broker
        :param addr: A string of the unique inbox topic to use.
        No wildcards are allowed. If None, no inbox topic will be set
        :param mqtt_client: The paho.Client object that is used for the
        communication with the broker
        :param codec: The codec to use. Currently only 'json' or 'protobuf' are
         allowed
        """
        super().__init__(
            codec=codec,
            addr=inbox_topic or client_id,
            clock=clock,
            name=client_id,
            **kwargs,
        )

        self.client_id: str = client_id
        # the client will be created on start.
        self.mqtt_client: paho.Client = None
        self.inbox_topic: None | str = inbox_topic or client_id
        self.broker_addr = broker_addr
        # dict mapping additionally subscribed topics to a set of aids
        self.additional_subscriptions: dict[str, set[str]] = {}
        # Future for pending sub requests
        self.pending_sub_request: None | asyncio.Future = None

    async def start(self):
        self._loop = asyncio.get_event_loop()
        if not self.client_id:
            raise ValueError("client_id is required!")
        if not self.broker_addr:
            raise ValueError("broker_addr is required!")

        # get parameters for Client.init()
        init_kwargs = {}
        possible_init_kwargs = (
            "clean_session",
            "userdata",
            "protocol",
            "transport",
        )
        for possible_kwarg in possible_init_kwargs:
            if possible_kwarg in self._kwargs.keys():
                init_kwargs[possible_kwarg] = self._kwargs.pop(possible_kwarg)

        # check if addr is a valid topic without wildcards
        if self.inbox_topic is not None and (
            not isinstance(self.inbox_topic, str)
            or "#" in self.inbox_topic
            or "+" in self.inbox_topic
        ):
            raise ValueError(
                "inbox topic is not set correctly. It must be a string without any wildcards ('#' or '+')!"
            )

        # create paho.Client object for mqtt communication
        mqtt_messenger: paho.Client = paho.Client(
            paho.CallbackAPIVersion.VERSION2, client_id=self.client_id, **init_kwargs
        )

        # set TLS options if provided
        # expected as a dict:
        # {ca_certs, certfile, keyfile, cert_eqs, tls_version, ciphers}
        tls_kwargs = self._kwargs.pop("tls_kwargs", None)
        if tls_kwargs:
            mqtt_messenger.tls_set(**tls_kwargs)

        # Future that is triggered, on successful connection
        connected = asyncio.Future()

        # callbacks to check for successful connection
        def on_con(client, userdata, flags, reason_code, properties):
            logger.info("Connection Callback with the following flags: %s", flags)
            self._loop.call_soon_threadsafe(connected.set_result, reason_code)

        mqtt_messenger.on_connect = on_con

        # check broker_addr input and connect
        if isinstance(self.broker_addr, tuple):
            if not 0 < len(self.broker_addr) < 4:
                raise ValueError("Invalid broker address argument count")
            if len(self.broker_addr) > 0 and not isinstance(self.broker_addr[0], str):
                raise ValueError("Invalid broker address - host must be str")
            if len(self.broker_addr) > 1 and not isinstance(self.broker_addr[1], int):
                raise ValueError("Invalid broker address - port must be int")
            if len(self.broker_addr) > 2 and not isinstance(self.broker_addr[2], int):
                raise ValueError("Invalid broker address - keepalive must be int")
            mqtt_messenger.connect(*self.broker_addr, **self._kwargs)

        elif isinstance(self.broker_addr, dict):
            if "hostname" not in self.broker_addr.keys():
                raise ValueError("Invalid broker address - host not given")
            mqtt_messenger.connect(**self.broker_addr, **self._kwargs)

        else:
            if not isinstance(self.broker_addr, str):
                raise ValueError("Invalid broker address")
            mqtt_messenger.connect(self.broker_addr, **self._kwargs)

        logger.info(
            "[%s]: Going to connect to broker at %s..", self.client_id, self.broker_addr
        )

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
                f"Connection to {self.broker_addr} could not be "
                f"established after {counter * 0.1} seconds"
            )
        if connected.result() != 0:
            raise ConnectionError(
                f"Connection to {self.broker_addr} could not be "
                f"set up. Callback returner error code "
                f"{connected.result()}"
            )

        logger.info("sucessfully connected to mqtt broker")
        if self.inbox_topic is not None:
            # connection has been set up, subscribe to inbox topic now
            logger.info(
                "[%s]: Going to subscribe to %s as inbox topic..",
                self.client_id,
                self.inbox_topic,
            )

            # create Future that is triggered on successful subscription
            subscribed = asyncio.Future()

            # set up subscription callback
            def on_sub(client, userdata, mid, reason_code_list, properties):
                self._loop.call_soon_threadsafe(subscribed.set_result, True)

            mqtt_messenger.on_subscribe = on_sub

            # subscribe topic
            result, _ = mqtt_messenger.subscribe(self.inbox_topic, 2)
            if result != paho.MQTT_ERR_SUCCESS:
                # subscription to inbox topic was not successful
                mqtt_messenger.disconnect()
                raise ConnectionError(
                    f"Subscription request to {self.inbox_topic} at {self.broker_addr} "
                    f"returned error code: {result}"
                )

            counter = 0
            while not subscribed.done() and counter < 100:
                # wait for subscription
                mqtt_messenger.loop(timeout=0.1)
                await asyncio.sleep(0.1)
                counter += 1
            if not subscribed.done():
                raise ConnectionError(
                    f"Subscription request to {self.inbox_topic} at {self.broker_addr} "
                    f"did not succeed after {counter * 0.1} seconds."
                )
            logger.info("successfully subscribed to topic")

        # connection and subscription is successful, remove callbacks
        mqtt_messenger.on_subscribe = None
        mqtt_messenger.on_connect = None

        self.mqtt_client = mqtt_messenger
        # set the callbacks
        self._set_mqtt_callbacks()
        # start the mqtt client
        self.mqtt_client.loop_start()

        await super().start()

    def _set_mqtt_callbacks(self):
        """
        Sets the callbacks for the mqtt paho client
        """

        def on_con(client, userdata, flags, reason_code, properties):
            if reason_code != 0:
                logger.info("Connection attempt to broker failed")
            else:
                logger.debug("Successfully reconnected to broker.")

        self.mqtt_client.on_connect = on_con

        def on_discon(client, userdata, disconnect_flags, reason_code, properties):
            if reason_code != 0:
                logger.warning("Unexpected disconnect from broker. Trying to reconnect")
            else:
                logger.debug("Successfully disconnected from broker.")

        self.mqtt_client.on_disconnect = on_discon

        def on_sub(client, userdata, mid, reason_code_list, properties):
            self._loop.call_soon_threadsafe(self.pending_sub_request.set_result, 0)

        self.mqtt_client.on_subscribe = on_sub

        def on_message(client, userdata, message):
            # extract the meta information first
            meta = {
                "network_protocol": "mqtt",
                "topic": message.topic,
                "qos": message.qos,
                "retain": message.retain,
            }
            # decode message and extract content and meta
            content, message_meta = self.decode_mqtt_message(
                payload=message.payload, topic=message.topic
            )
            # update meta dict
            meta.update(message_meta)
            # put information to inbox
            self._loop.call_soon_threadsafe(self.inbox.put_nowait, (0, content, meta))

        self.mqtt_client.on_message = on_message
        self.mqtt_client.enable_logger(logger)

    def decode_mqtt_message(self, *, topic, payload):
        """
        deserializes a mqtt message.
        Checks if for the topic a special class is defined, otherwise assumes
        an ACLMessage
        :param topic: the topic on which the message arrived
        :param payload: the serialized message
        :return: content and meta
        """
        meta = {}
        content = None

        decoded = self.codec.decode(payload)
        if hasattr(decoded, "split_content_and_meta"):
            content, meta = decoded.split_content_and_meta()
        else:
            content = decoded

        return content, meta

    async def _handle_message(self, *, priority: int, content, meta: dict[str, Any]):
        """
        This is called as a separate task for every message that is read
        :param priority: priority of the message
        :param content: Deserialized content of the message
        :param meta: Dict with additional information (e.g. topic)
        """
        topic = meta["topic"]
        logger.debug("Received message with content and meta;%s;%s", content, meta)
        if topic == self.inbox_topic:
            # General inbox topic, so no receiver is specified by the topic
            # try to find the receiver from meta
            receiver_id = meta.get("receiver_id", None)
            if receiver_id and receiver_id in self._agents.keys():
                receiver = self._agents[receiver_id]
                await receiver.inbox.put((priority, content, meta))
            else:
                # receiver might exist in mirrored container
                logger.debug("Receiver ID is unknown;%s", receiver_id)
        else:
            # no inbox topic. Check who has subscribed the topic.
            receivers = set()
            for sub, rec in self.additional_subscriptions.items():
                if paho.topic_matches_sub(sub, topic):
                    receivers.update(rec)
            if not receivers:
                logger.warning(
                    "Received a message at a topic which no agent subscribed;%s", topic
                )
            else:
                for receiver_id in receivers:
                    receiver = self._agents[receiver_id]
                    await receiver.inbox.put((priority, content, meta))

    async def send_message(
        self,
        content: Any,
        receiver_addr: AgentAddress,
        sender_id: None | str = None,
        **kwargs,
    ):
        """
        The container sends the message of one of its own agents to a specific topic.

        :param content: The content of the message
        :param receiver_addr: The topic to publish to.
        :param sender_id: The sender aid
        :param kwargs: Additional parameters to provide protocol specific settings
            Possible fields:
            qos: The quality of service to use for publishing
            retain: Indicates, weather the retain flag should be set
            Ignored if connection_type != 'mqtt'

        """
        # internal message first (if retain Flag is set, it has to be sent to
        # the broker
        meta = {}
        for key, value in kwargs.items():
            meta[key] = value
        meta["sender_id"] = sender_id
        meta["sender_addr"] = self.inbox_topic
        meta["receiver_id"] = receiver_addr.aid

        actual_mqtt_kwargs = {} if kwargs is None else kwargs
        if (
            self.inbox_topic
            and receiver_addr.protocol_addr == self.inbox_topic
            and not actual_mqtt_kwargs.get("retain", False)
        ):
            meta.update(
                {
                    "topic": self.inbox_topic,
                    "qos": actual_mqtt_kwargs.get("qos", 0),
                    "retain": False,
                    "network_protocol": "mqtt",
                }
            )
            return self._send_internal_message(
                content, receiver_addr.aid, default_meta=meta, inbox=self.inbox
            )

        else:
            message = content
            if not hasattr(content, "split_content_and_meta"):
                message = MangoMessage(content, meta)
            self._send_external_message(
                topic=receiver_addr.protocol_addr, message=message
            )
            return True

    def _send_external_message(self, *, topic: str, message):
        """

        :param topic: MQTT topic
        :param message: The ACL message
        :return:
        """
        encoded_message = self.codec.encode(message)
        logger.debug("Sending message;%s;%s", message, topic)
        self.mqtt_client.publish(topic, encoded_message)

    async def subscribe_for_agent(self, *, aid: str, topic: str, qos: int = 0) -> bool:
        """

        :param aid: aid of the corresponding agent
        :param topic: topic to subscribe (wildcards are allowed)
        :param qos: The quality of service for the subscription
        :return: A boolean signaling if subscription was true or not
        """
        if aid not in self._agents.keys():
            raise ValueError("Given aid is not known")

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

    def deregister(self, aid):
        """

        :param aid:
        :return:
        """
        super().deregister(aid)
        empty_subscriptions = []
        for subscription, aid_set in self.additional_subscriptions.items():
            if aid in aid_set:
                aid_set.remove(aid)
            if len(aid_set) == 0:
                empty_subscriptions.append(subscription)

        for subscription in empty_subscriptions:
            self.additional_subscriptions.pop(subscription)
            self.mqtt_client.unsubscribe(topic=subscription)

    def _create_mirror_container(self):
        return partial(
            mqtt_mirror_container_creator,
            self.client_id,
            self.inbox_topic,
        )

    async def shutdown(self):
        """
        Shutdown container, disconnect from broker and stop mqtt thread
        """
        await super().shutdown()
        # disconnect to broker
        self.mqtt_client.disconnect()
        self.mqtt_client.loop_stop()

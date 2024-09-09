import asyncio
import logging
from functools import partial
from typing import Any, Dict, Optional, Set, Tuple, Union

import paho.mqtt.client as paho

from mango.container.core import Container, ContainerMirrorData

from ..messages.codecs import ACLMessage, Codec
from ..util.clock import Clock

logger = logging.getLogger(__name__)


def mqtt_mirror_container_creator(
    client_id,
    mqtt_client,
    container_data,
    loop,
    message_pipe,
    main_queue,
    event_pipe,
    terminate_event,
):
    return MQTTContainer(
        client_id=client_id,
        addr=container_data.addr,
        codec=container_data.codec,
        clock=container_data.clock,
        loop=loop,
        mqtt_client=mqtt_client,
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
        addr: Optional[str],
        loop: asyncio.AbstractEventLoop,
        clock: Clock,
        mqtt_client: paho.Client,
        codec: Codec,
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
            codec=codec, addr=addr, loop=loop, clock=clock, name=client_id, **kwargs
        )

        self.client_id: str = client_id
        # the configured and connected paho client
        self.mqtt_client: paho.Client = mqtt_client
        self.inbox_topic: Optional[str] = addr
        # dict mapping additionally subscribed topics to a set of aids
        self.additional_subscriptions: Dict[str, Set[str]] = {}
        # Future for pending sub requests
        self.pending_sub_request: Optional[asyncio.Future] = None

        # set the callbacks
        self._set_mqtt_callbacks()

        # start the mqtt client
        self.mqtt_client.loop_start()

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
            self.loop.call_soon_threadsafe(self.pending_sub_request.set_result, 0)

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
            self.loop.call_soon_threadsafe(self.inbox.put_nowait, (0, content, meta))

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
        if isinstance(decoded, ACLMessage):
            content, meta = decoded.split_content_and_meta()
        else:
            content = decoded

        return content, meta

    async def _handle_message(self, *, priority: int, content, meta: Dict[str, Any]):
        """
        This is called as a separate task for every message that is read
        :param priority: priority of the message
        :param content: Deserialized content of the message
        :param meta: Dict with additional information (e.g. topic)
        """
        topic = meta["topic"]
        logger.debug(
            f"Received message with content and meta;{str(content)};{str(meta)}"
        )
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
                    f"Received a message at a topic which no agent subscribed;{topic}"
                )
            else:
                for receiver_id in receivers:
                    receiver = self._agents[receiver_id]
                    await receiver.inbox.put((priority, content, meta))

    async def send_message(
        self,
        content,
        receiver_addr: Union[str, Tuple[str, int]],
        *,
        receiver_id: Optional[str] = None,
        **kwargs,
    ):
        """
        The container sends the message of one of its own agents to a specific topic.

        :param content: The content of the message
        :param receiver_addr: The topic to publish to.
        :param receiver_id: The agent id of the receiver
        :param kwargs: Additional parameters to provide protocol specific settings
            Possible fields:
            qos: The quality of service to use for publishing
            retain: Indicates, weather the retain flag should be set
            Ignored if connection_type != 'mqtt'

        """
        # the message is already complete
        message = content

        # internal message first (if retain Flag is set, it has to be sent to
        # the broker
        actual_mqtt_kwargs = {} if kwargs is None else kwargs
        if (
            self.addr
            and receiver_addr == self.addr
            and not actual_mqtt_kwargs.get("retain", False)
        ):
            meta = {
                "topic": self.addr,
                "qos": actual_mqtt_kwargs.get("qos", 0),
                "retain": False,
                "network_protocol": "mqtt",
            }
            return self._send_internal_message(
                message, receiver_id, default_meta=meta, inbox=self.inbox
            )

        else:
            self._send_external_message(topic=receiver_addr, message=message)
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

    def as_agent_process(
        self,
        agent_creator,
        mirror_container_creator=None,
    ):
        if not mirror_container_creator:
            mirror_container_creator = partial(
                mqtt_mirror_container_creator,
                self.client_id,
                self.mqtt_client,
            )
        return super().as_agent_process(
            agent_creator=agent_creator,
            mirror_container_creator=mirror_container_creator,
        )

    async def shutdown(self):
        """
        Shutdown container, disconnect from broker and stop mqtt thread
        """
        await super().shutdown()
        # disconnect to broker
        self.mqtt_client.disconnect()
        self.mqtt_client.loop_stop()

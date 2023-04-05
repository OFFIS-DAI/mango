import asyncio
import logging
from typing import Any, Dict, Optional, Tuple, Union

import paho.mqtt.client as paho

from mango.container.core import Container
from mango.container.mosaik import MosaikContainer
from mango.container.mqtt import MQTTContainer
from mango.container.protocol import ContainerProtocol
from mango.container.tcp import TCPContainer
from mango.messages.codecs import JSON, PROTOBUF

from ..messages.codecs import Codec
from ..util.clock import AsyncioClock, Clock

logger = logging.getLogger(__name__)

TCP_CONNECTION = "tcp"
MQTT_CONNECTION = "mqtt"
MOSAIK_CONNECTION = "mosaik"


async def create(
    *,
    connection_type: str = "tcp",
    codec: Codec = None,
    clock: Clock = None,
    addr: Optional[Union[str, Tuple[str, int]]] = None,
    copy_internal_messages=True,
    mqtt_kwargs: Dict[str, Any] = None,
) -> Container:
    """
    This method is called to instantiate a container instance, either
    a TCPContainer or a MQTTContainer, depending on the parameter
    connection_type.
    :param connection_type: Defines the connection type. So far only 'tcp'
    or 'mqtt' are allowed
    :param codec: Defines the codec to use. Defaults to JSON
    :param clock: The clock that the scheduler of the agent should be based on. Defaults to the AsyncioClock
    :param addr: the address to use. If connection_type == 'tcp': it has
    to be a tuple of (host, port). If connection_type == 'mqtt' this can
    optionally define an inbox_topic that is used similarly than
    a tcp address.
    :param mqtt_kwargs: Dictionary of keyword arguments for connection to a mqtt broker. At least
    the keys 'broker_addr' and 'client_id' have to be provided.
    Ignored if connection_type != 'mqtt'
    :return: The instance of a MQTTContainer or a TCPContainer
    """
    connection_type = connection_type.lower()
    if connection_type not in [TCP_CONNECTION, MQTT_CONNECTION, MOSAIK_CONNECTION]:
        raise ValueError(f"Unknown connection type {connection_type}")

    loop = asyncio.get_running_loop()

    # not initialized by the default parameter because then
    # containers could unexpectedly share the same codec object
    if codec is None:
        codec = JSON()

    if clock is None:
        clock = AsyncioClock()

    if connection_type == TCP_CONNECTION:
        # initialize TCPContainer
        container = TCPContainer(
            addr=addr,
            codec=codec,
            loop=loop,
            clock=clock,
            copy_internal_messages=copy_internal_messages,
        )

        # create a TCP server bound to host and port that uses the
        # specified protocol
        container.server = await loop.create_server(
            lambda: ContainerProtocol(container=container, loop=loop, codec=codec),
            addr[0],
            addr[1],
        )
        return container

    if connection_type == MOSAIK_CONNECTION:
        return MosaikContainer(addr=addr, loop=loop, codec=codec)

    if connection_type == MQTT_CONNECTION:
        # get and check relevant kwargs from mqtt_kwargs
        # client_id
        client_id = mqtt_kwargs.pop("client_id", None)
        if not client_id:
            raise ValueError("client_id is requested within mqtt_kwargs")

        # broker_addr
        broker_addr = mqtt_kwargs.pop("broker_addr", None)
        if not broker_addr:
            raise ValueError("broker_addr is requested within mqtt_kwargs")

        # get parameters for Client.init()
        init_kwargs = {}
        possible_init_kwargs = (
            "clean_session",
            "userdata",
            "protocol",
            "transport",
        )
        for possible_kwarg in possible_init_kwargs:
            if possible_kwarg in mqtt_kwargs.keys():
                init_kwargs[possible_kwarg] = mqtt_kwargs.pop(possible_kwarg)

        # check if addr is a valid topic without wildcards
        if addr is not None and (
            not isinstance(addr, str) or "#" in addr or "+" in addr
        ):
            raise ValueError(
                "addr is not set correctly. It is used as "
                "inbox topic and must be a  string without "
                "any wildcards ('#' or '+')"
            )

        # create paho.Client object for mqtt communication
        mqtt_messenger: paho.Client = paho.Client(client_id=client_id, **init_kwargs)

        # set TLS options if provided
        # expected as a dict:
        # {ca_certs, certfile, keyfile, cert_eqs, tls_version, ciphers}
        tls_kwargs = mqtt_kwargs.pop("tls_kwargs", None)
        if tls_kwargs:
            mqtt_messenger.tls_set(**tls_kwargs)

        # Future that is triggered, on successful connection
        connected = asyncio.Future()

        # callbacks to check for successful connection
        def on_con(client, userdata, flags, returncode):
            logger.info(f"Connection Callback with the following flags: {flags}")
            loop.call_soon_threadsafe(connected.set_result, returncode)

        mqtt_messenger.on_connect = on_con

        # check broker_addr input and connect
        if isinstance(broker_addr, tuple):
            if not 0 < len(broker_addr) < 4:
                raise ValueError(f"Invalid broker address")
            if len(broker_addr) > 0 and not isinstance(broker_addr[0], str):
                raise ValueError("Invalid broker address")
            if len(broker_addr) > 1 and not isinstance(broker_addr[1], int):
                raise ValueError("Invalid broker address")
            if len(broker_addr) > 2 and not isinstance(broker_addr[2], int):
                raise ValueError("Invalid broker address")
            mqtt_messenger.connect(*broker_addr, **mqtt_kwargs)

        elif isinstance(broker_addr, dict):
            if "hostname" not in broker_addr.keys():
                raise ValueError("Invalid broker address")
            mqtt_messenger.connect(**broker_addr, **mqtt_kwargs)

        else:
            if not isinstance(broker_addr, str):
                raise ValueError("Invalid broker address")
            mqtt_messenger.connect(broker_addr, **mqtt_kwargs)

        logger.info(f"[{client_id}]: Going to connect to broker at {broker_addr}..")

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
                f"Connection to {broker_addr} could not be "
                f"established after {counter * 0.1} seconds"
            )
        if connected.result() != 0:
            raise ConnectionError(
                f"Connection to {broker_addr} could not be "
                f"set up. Callback returner error code "
                f"{connected.result()}"
            )

        logger.info("sucessfully connected to mqtt broker")
        if addr is not None:
            # connection has been set up, subscribe to inbox topic now
            logger.info(
                f"[{client_id}]: Going to subscribe to {addr} " f"as inbox topic.."
            )

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
                    f"Subscription request to {addr} at {broker_addr} "
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
                    f"Subscription request to {addr} at {broker_addr} "
                    f"did not succeed after {counter * 0.1} seconds."
                )
            logger.info("successfully susbsribed to topic")

        # connection and subscription is successful, remove callbacks
        mqtt_messenger.on_subscribe = None
        mqtt_messenger.on_connect = None

        return MQTTContainer(
            client_id=client_id,
            addr=addr,
            loop=loop,
            clock=clock,
            mqtt_client=mqtt_messenger,
            codec=codec,
            copy_internal_messages=copy_internal_messages,
        )

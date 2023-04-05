"""
TODO
"""
import logging
from functools import partial

import paho.mqtt.client as paho


class MQTTModule:
    """
    Module wrapper for a paho mqtt client used in the mango base module.
    """

    def __init__(self, *, name: str, subscr_topics, pub_topics, broker):
        """

        :param name: name of the module (str)
        :param subscr_topics: List of string and integer tuples for subscribed
        topics:[ (topic, qos)] e.g.[("my/topic", 0), ("another/topic", 2)]
        :param pub_topics: List of string and integer tuples for publishing
        topics:[ (topic, qos)]
        :param broker: MQTT broker
        """
        self.client_id = name
        self.client = paho.Client(client_id=name)
        self.client.on_connect = self.conn
        self.client.on_message = self.on_mqtt_msg
        self.client.on_disconnect = self.on_disconnect
        self.client.connect(broker[0], broker[1], broker[3])
        self.subscr_topics = subscr_topics
        self.client.subscribe(subscr_topics)  # subscribe
        self.pub_topics = pub_topics

    def add_message_callback(self, topic, fkt):
        """
        Add a callback method in the mqtt client
        :param topic: the topic to bind the method to
        :param fkt: the method to bind
        :return: None
        """
        # bind function to general callback signature
        partial_function = partial(self.bind_callback, fkt)
        self.client.message_callback_add(topic, partial_function)

    def start_mq_thread(self):
        """
        Start the message listening thread
        :return: None
        """
        self.client.loop_start()

    def end_mq_thread(self):
        """
        Stop the message listening thread and disconnect the client
        :return:
        """
        self.client.loop_stop()
        self.client.disconnect()

    def publish_mq_message(self, topic, payload, retain=False):
        """
        Publish a message to the mqtt broker.
        :param topic: the topic to publish on
        :param payload: the message to publish
        :param retain: flag indicating if the message should be retained on
        shutdown
        :return: None
        """
        self.client.publish(topic, payload, retain=retain)

    @staticmethod
    def bind_callback(func, client, userdata, message):
        # pylint: disable=unused-argument
        """
        Function to call our generic callback function that has only two
        parameters from the framework specific callback with four parameters by
        binding it as a partial function.
        :param func: The wanted callback function
        :param client: the messaging client (unused)
        :param userdata: the userdata object (unused)
        :param message: the message payload
        :return: None
        """
        func(topic=message.topic, payload=message.payload)

    def conn(self, client, userdata, flags, return_code):
        # pylint: disable=unused-argument
        """
        Callback method on broker connection on paho mqtt framework
        :param client: the connecting client
        :param userdata: userdata object
        :param flags: returned flags
        :param return_code: return code
        :return: None
        """

    def on_disconnect(self, client, userdata, return_code):
        # pylint: disable=unused-argument
        """
        Callback method on broker disconnect on paho mqtt framework
        :param client: the connecting client
        :param userdata: userdata object
        :param return_code: return code
        :return: None
        """

    def log(self, client, userdata, level, buf):
        # pylint: disable=unused-argument
        """
        Client log method
        :param client: the mqtt client
        :param userdata: userdata object
        :param level: log level
        :param buf: data buffer
        :return: None
        """

    def on_mqtt_msg(self, client, userdata, message):
        """
        Each module has to implement this to handle all messages it receives
         in subscribed topics
        :param client:
        :param userdata:
        :param message:
        :return:
        """

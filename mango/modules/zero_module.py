import pickle
import threading
import time
from abc import ABC

import zmq


class ZeroModule(ABC):
    def __init__(self, *, name: str, subscr_topics, pub_topics, broker):
        """

        :param name: name of the module (str)
        :param subscr_topics: List of string and integer tuples for subscribed topics:[ (topic, qos)]
        e.g.[("my/topic", 0), ("another/topic", 2)]
        :param pub_topics: List of string and integer tuples for publishing topics:[ (topic, qos)]
        :param broker: MQTT broker
        :param log_level

        """
        super().__init__()
        self.name = name

        self.subscr_topics = subscr_topics
        self.pub_topics = pub_topics

        self.thread_active = False
        self.mq_thread = threading.Thread(target=self.run_mq)

        # set up pub
        self.pub_context = zmq.Context()
        self.pub_socket = self.pub_context.socket(zmq.PUB)
        self.pub_socket.connect(f"tcp://{broker[0]}:{broker[1]}")

        # set up sub
        self.sub_context = zmq.Context()
        self.sub_socket = self.sub_context.socket(zmq.SUB)
        self.sub_socket.connect(f"tcp://{broker[0]}:{broker[2]}")

        for topic in self.subscr_topics:
            self.sub_socket.setsockopt_string(zmq.SUBSCRIBE, topic[0])

        self.known_callbacks = []

    ### The following methods might be changed, in case we use another MQ paradigme such as rabbitMQ, zeroMQ...)
    def add_message_callback(self, topic, fkt):
        # bind function to general callback signature
        self.known_callbacks.append((topic, fkt))

    def start_mq_thread(self):
        self.thread_active = True
        self.mq_thread.start()

    def end_mq_thread(self):
        self.thread_active = False
        # self.sub_context.destroy()
        self.mq_thread.join()

    def publish_mq_message(self, topic, payload):
        pl = pickle.dumps(payload)
        self.pub_socket.send_multipart([topic.encode("utf-8"), pl])

    # the actual binding to whatever signature the frameworks callbacks have happens here
    def bind_callback(self, func, client, userdata, message):
        pass

    def run_mq(self):
        got_message = False

        while self.thread_active:
            # TODO there probably is a clean way to terminate this
            try:
                [topic, msg] = self.sub_socket.recv_multipart(flags=zmq.NOBLOCK)
                got_message = True
            except:
                # didnt get a message
                got_message = False

            if not got_message:
                time.sleep(0.5)
                continue

            topic = topic.decode("utf-8")
            msg = pickle.loads(msg)

            for callback in self.known_callbacks:
                if callback[0] == topic:
                    callback[1](topic=topic, payload=msg)

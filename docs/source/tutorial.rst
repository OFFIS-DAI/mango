==============
mango tutorial
==============

***************
Introduction
***************

This tutorial gives an overview of the basic functions of mango agents and containers. It consists of four
parts building a scenario of two PV plants, operated by an their respective agents being directed by a remote
controller. 

Each part comes with a standalone executable file. Subsequent parts either extend the functionality or simplify 
some concept in the previous part. 

As a whole, this tutorial covers:
    - container and agent creation
    - message passing within a container
    - message passing between containers
    - codecs
    - scheduling
    - roles


*****************************
1. Setup and Message Passing
*****************************

Corresponding file: `v1_basic_setup_and_message_passing.py`

For your first mango tutorial you will learn the fundamentals of creating mango agents and containers as well
as making them communicate with each other.

This example covers:
    - container
    - agent creation
    - basic message passing
    - clean shutdown of containers

*********************************
1. Messaging between Containers
*********************************

Corresponding file: `v2_inter_container_messaging_and_basic_functionality.py`

In the previous example you learned how to create mango agents and containers and how to send basic messages between them.
In this example you expand upon this. We introduce a controller agent that asks the current feed_in of our PV agents and
subsequently limits the output of both to the minimum of the two.

This example covers:
    - message passing between different containers
    - basic task scheduling
    - use of ACL metadata


*******************************************
1. Using Codecs to simplity Message Types
*******************************************

Corresponding file: `v3_codecs_and_typing.py`

In example 2 you created some basic agent functionality and established inter-container communication.
Message types were distinguished by a corresponding field in the content dictionary. This approach is 
tedious and prone to error. A better way is to use dedicated message objects and using their types to distinguish
messages. Objects can be encoded for messaging between agents by mangos codecs. To make a new object type
known to a codec it needs to provide a serialization and a deserialization method. The object type together
with these methods is then passed to the codec which in turn is passed to a container. The container will then
automatically use these methods when it encounters an object of this type as the content of a message.

This example covers:
    - message classes
    - codec basics
    - the json_serializable decorator


*************************
1. Scheduling and Roles
*************************

Corresponding file: `v4_scheduling_and_roles.py`

In example 3 you restructured your code to use codecs for easier handling of typed message objects.
Now it is time to expand the functionality of our controller. In addition to setting the maximum feed_in 
of the pv agents, the controller should now also periodically check if the pv agents are still reachable.

To achieve this, the controller should seend a regular "ping" message to each pv agent that is in turn answered
by a corresponding "pong". Periodic tasks can be handled for you by mangos scheduling API.
Additionally, to serparate different responsibilities within agents, mango has a role system where each role 
covers the functionalities of a responsibility.

A role is a python object that can be assigned to a RoleAgent. The two main functions each role implements are:
    - __init__ - where you do the initial object setup
    - setup - which is called when the role is assigned to an agent

This distinction is relevant because only within `setup` the RoleContext (i.e. access to the parent agent and container) exist.
Thus, things like message handlers that require container knowledge are introduced there.

This example covers:
    - scheduling and periodic tasks
    - role API basics
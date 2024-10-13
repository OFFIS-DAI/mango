Migration
=========

Some mango versions will break the API; in this case, we may provide instructions on the migration on this page.

********************
mango 1.2.X to 2.0.0
********************

* The methods send_acl_message and schedule_instant_acl_message have been removed, use send_message/schedule_instant_message with create_acl instead if you explicitly need an ACLMessage, otherwise you can just replace it with send_message/schedule_instant_message. 
  * Background: In the past, mango relied on ACL messages for message routing, in 2.0.0 mango introduces an internal message container for all types of content, which provides necessary routing information. That way all mango messages can always be routed to the correct target. 
* The container factory method create_container has been removed and instead there is one factory method per container type now: create_tcp_container, create_mqtt_container, create_external_container
* The container factory methods are not async anymore! 
* The startup for the TCP-server/MQTT-client has been moved to `container.start()`.
  * Use the async context manager provided by `activate(containers)` as described in the documentation
  * Background: This has the advantage that calling shutdown is not necessary anymore, in that way it is not possible to forget and ressource leaks are avoided at any time 
* Consider to use the async context manager provided by run_with_X, these managers will provide suitable containers internally, that way the user does not need to handle container at all
* Agents are created without container now
  * This implies that Agents need to be registered explicitly using `register`
  * Background: This is necessary to provide the run_with_X methods as in this case the Agents need to be created before the Containers. Furthermore it generally provides more flexibility for the user and for us to add new features in the future.
* The main way to route messages is now to use AgentAddress to specific the destination. Just replace receiver_addr and receiver_id with the convenience methods agent.addr or sender_addr(agent) (from mango).
* The signature methods send_message and schedule_instant_message have been changed, you can omit specifying sender_id and sender_addr most of the time now (if you use convenience methods of Agent or Role).
  Further the method now requires an AgentAddress
* 

********************
mango 0.4.0 to 1.0.0
********************

* The module core and role have been restructured/moved: Agent, Container, all Role-related classes, and the container factory are now available using the top-level mango module, f.e. 'from mango import Agent, create_container'
* The methods 'handle_msg' in Agent and Role have been removed: Use 'handle_message' instead
* The parameters create_acl and acl_metadata from 'send_message' has been removed: use send_acl_message instead
* The parameter mqtt_kwargs from 'send_message' has been removed: use kwargs instead
* The DateTimeScheduledTask has been removed: use TimestampScheduledTask instead
* The context and scheduler of an agent are no longer public: use the convenience methods for sending/scheduling or _context, _scheduler from within the agent

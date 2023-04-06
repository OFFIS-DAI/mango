Migration
========

Some mango versions will break the API; in this case, we may provide instructions on the migration on this page.



********************
mango 0.4.0 to 1.0.0
********************

* The module core and role have been restructured/moved: Agent, Container, all Role-related classes, and the container factory are now available using the top-level mango module, f.e. 'from mango import Agent, create_container'
* The methods 'handle_msg' in Agent and Role have been removed: Use 'handle_message' instead
* The parameters create_acl and acl_metadata from 'send_message' has been removed: use send_acl_message instead
* The parameter mqtt_kwargs from 'send_message' has been removed: use kwargs instead
* The DateTimeScheduledTask has been removed: use TimestampScheduledTask instead
* The context and scheduler of an agent are no longer public: use the convenience methods for sending/scheduling or _context, _scheduler from within the agent
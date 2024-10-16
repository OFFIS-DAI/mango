=============
API reference
=============

The API reference provides detailed descriptions of the mango's classes and
functions.

.. automodule:: mango
   :members:
   :undoc-members:
   :imported-members:
   :inherited-members:
   :exclude-members: PrintingAgent, DistributedClockManager, DistributedClockAgent

.. autoclass:: mango.PrintingAgent
   :members:

.. autoclass:: mango.DistributedClockManager
   :members:

.. autoclass:: mango.DistributedClockAgent
   :members:



.. note::
   Note that, most classes and functions described in the API reference
   should be imported using `from mango import ...`, as the stable and public API
   generally will be available by using `mango` and the internal module structure
   might change, even in minor releases.

By subpackages
---------------

.. toctree::
   :maxdepth: 2

   mango.express
   mango.agent
   mango.container
   mango.messages
   mango.util

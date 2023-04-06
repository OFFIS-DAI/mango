.. mango documentation master file, created by
   sphinx-quickstart on Wed Aug 11 12:52:05 2021.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

Welcome to mango's documentation!
=================================
*mango* (modular python agent framework) is a python library for *multi-agent systems (MAS)*. It is written on top of `asyncio <https://docs.python.org/3/library/asyncio.html>`_ and is releassed under the MIT license.

*mango* allows the user to create simple agents with little effort and in the same
time offers options to structure agents with complex behaviour.
The main features of mango are listed below.

Features
=================================
- Container mechanism to speedup local message exchange
- Message definition based on the FIPA ACL standard
- Structuring complex agents with loose coupling and agent roles
- Built-in codecs: `JSON <https://www.json.org>`_ and `protobuf <https://developers.google.com/protocol-buffers>`_
- Supports communication between agents directly via TCP or via an external MQTT broker in the middle

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   installation
   getting_started
   migration
   tutorial
   agents-container
   message exchange
   scheduling
   ACL messages
   codecs
   role-api
   development
   api_ref/index
   privacy
   legals
   datenschutz
   impressum



Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

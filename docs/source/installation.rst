Installation
============
*mango* requires Python >= 3.8 and runs on Linux, OSX and Windows.
For installation of mango you could use
virtualenv__ which can create isolated Python environments for different projects.

It is also recommended to install virtualenvwrapper__ which makes it easier to manage
different virtual environments.

__ https://virtualenv.pypa.io/en/latest/#
__ https://virtualenvwrapper.readthedocs.io/en/latest/index.html

Installation with pip
---------------------
Once you have created a virtual environment you can just run pip__ to install it:

.. code-block:: console

    $ pip install mango-agents

__ https://pip.pypa.io/en/stable/


Installation from source
------------------------
To install from source, simply check out this repository and install in editable mode using pip:

.. code-block:: console

    $ pip install -e .

Using a local message broker
----------------------------
If you want to make use of the functional mqtt modules to modularize your agent,
you must have a local message broker running on your system.
We recommend Mosquitto__. On Debian/Ubuntu it can be installed as follows:

.. code-block:: console

    $ sudo apt-get install mosquitto

__ https://mosquitto.org/


..
    Using protobuf
    -----------------------
    The protobuf codec is an optional feature that you need to explicity install if you need it.

    **TODO: make protobuf optional**



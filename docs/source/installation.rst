Installation
============
*mango* requires Python >= 3.10 and runs on Linux, OSX and Windows.
For installation of mango you could use
virtualenv__ which can create isolated Python environments for different projects.

__ https://virtualenv.pypa.io/en/latest/#

Installation with pip
---------------------
Once you have created a virtual environment you can just run pip__ to install it:

.. code-block:: console

    $ pip install mango-agents

__ https://pip.pypa.io/en/stable/

Optional extras
---------------
Some features need additional packages, which pip installs as *extras*:

.. code-block:: console

    $ pip install "mango-agents[scenario]"      # scenarios and studies: pydantic, PyYAML, pandas
    $ pip install "mango-agents[scenario-all]"  # plus pyarrow (Parquet results) and scipy (LHS, Sobol)
    $ pip install "mango-agents[fastjson]"      # msgspec for the fast JSON codec

The ``scenario`` extra enables :doc:`scenario` (``mango.scenario``) and the
``mango-scenario`` command; without it, importing ``mango.scenario`` raises
an ``ImportError`` naming the extra.  The ``scenario-all`` extra adds the
optional packages of the scenario layer:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Package
     - Enables
   * - pyarrow
     - run results as Parquet files (without it, results are written as CSV)
   * - scipy
     - the sampling designs ``Lhs`` and ``Sobol`` and ``StudyFrame.sobol_indices()``
       with its bootstrap intervals (``RandomSample`` works without it, and
       ``StudyFrame.summary()``/``paired()`` use an accurate approximation of
       the t quantiles instead)

Second-order Sobol indices (``Sobol(n, second_order=True)``) also need
SALib, which no extra installs: ``pip install SALib``.  A feature whose
package is missing raises an ``ImportError`` that names the package.


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


Note that mosquitto requires to have a config since v2.x for it to be accessible from outside your machine.
It may as well be desired to set the option `set_tcp_nodelay=true` in the `mosquitto.conf` to improve round-trip time.
Using the default QoS setting of 0 is recommended.

__ https://mosquitto.org/


..
    Using protobuf
    -----------------------
    The protobuf codec is an optional feature that you need to explicity install if you need it.

    **TODO: make protobuf optional**

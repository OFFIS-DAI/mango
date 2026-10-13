Scenario layer
==============

.. automodule:: mango.scenario

The names in the first sections are importable from :mod:`mango.scenario`
directly (``from mango.scenario import Scenario, Study, load_study``), unless
they are shown with their module path.  The module sections at the end list
the helpers that are only available from their modules.  See :doc:`../scenario` for the user guide.

.. currentmodule:: mango.scenario

Scenario builder
----------------

.. autoclass:: Scenario
   :members:

.. autoclass:: BuildContext
   :members:

.. autoclass:: EntityContext
   :members:

.. autoclass:: Entity
   :members:

.. autoclass:: GroupRef
   :members:

.. autoclass:: LinkSpec
   :members:

.. autoclass:: RecordSpec
   :members:

.. autoclass:: ScenarioPlan
   :members:

.. autoclass:: KitPlan
   :members:

.. autoclass:: mango.scenario.plan.GroupPlan
   :members:

.. autoclass:: mango.scenario.plan.LinkPlan
   :members:

.. autoclass:: mango.scenario.plan.RecordPlan
   :members:

Kits
----

.. autoclass:: ScenarioComponent
   :members:

.. autoclass:: ComponentContext
   :members:
   :show-inheritance:

.. autoclass:: CompositeBehavior
   :members:
   :show-inheritance:

.. autofunction:: register_component

.. autofunction:: resolve_component

.. autofunction:: component_kinds

.. autodata:: mango.scenario.components.ENTRY_POINT_GROUP

Built-in kits
~~~~~~~~~~~~~

.. autoclass:: AgentPopulation
   :members:
   :show-inheritance:

.. autoclass:: CommunicationPreset
   :members:
   :show-inheritance:

.. autodata:: mango.scenario.kits.COMM_PRESETS
   :no-value:

Runs and results
----------------

.. autoclass:: ScenarioRun
   :members:

.. autoclass:: RunResult
   :members:

.. autofunction:: load_run

Parameters
----------

.. autoclass:: ScenarioParams
   :members:
   :show-inheritance:

.. autoclass:: CommConfig
   :members:

.. autofunction:: load_params

.. autofunction:: check_path

.. autofunction:: deep_merge

.. autofunction:: read_config_file

.. autofunction:: parse_overrides

.. autofunction:: set_path

.. autofunction:: get_path

.. autofunction:: params_schema

.. autofunction:: dump_params

.. autofunction:: params_to_yaml

.. autofunction:: build_communication

.. autoclass:: LinkComm
   :members:

.. autoclass:: LayeredCommunication
   :show-inheritance:

.. autofunction:: mango.scenario.params.kind_switch_rule

.. autofunction:: mango.scenario.params.admits_distribution

.. autodata:: mango.scenario.configfiles.FLAT_KIND_KEYS
   :no-value:

.. autofunction:: mango.scenario.configfiles.generic_kind_switch

Distributions
-------------

.. autoclass:: Distribution
   :members:

.. autoclass:: Constant
   :show-inheritance:

.. autoclass:: Uniform
   :show-inheritance:

.. autoclass:: IntUniform
   :show-inheritance:

.. autoclass:: Normal
   :show-inheritance:

.. autoclass:: LogNormal
   :show-inheritance:

.. autoclass:: Choice
   :show-inheritance:

.. autoclass:: Bernoulli
   :show-inheritance:

.. autoclass:: FromColumn
   :members: draw, column_values, expected_value
   :show-inheritance:

.. autodata:: Dist
   :no-value:

.. autofunction:: distribution_kinds

Profiles
--------

.. automodule:: mango.scenario.profiles
   :no-members:

.. currentmodule:: mango.scenario

.. autoclass:: ProfileSpec
   :members: columns

.. autofunction:: mango.scenario.profiles.effective_wrap

.. autofunction:: mango.scenario.profiles.profile_warnings

.. autoclass:: Profile
   :members:

.. autofunction:: load_profile

.. autofunction:: read_column

.. autofunction:: clear_profile_cache

.. autodata:: mango.scenario.profiles.ProfileName
   :no-value:

Events and phases
-----------------

.. automodule:: mango.scenario.events
   :no-members:

.. currentmodule:: mango.scenario

.. autoclass:: Phase
   :members:

.. autoclass:: Event
   :members:

.. autoclass:: ScenarioEvent
   :members:

.. autoclass:: mango.scenario.events.PhasePlan
   :members:

.. autoclass:: mango.scenario.events.EventPlan
   :members:

.. autodata:: mango.scenario.events.MAIN_PHASE

.. autofunction:: mango.scenario.events.sample_phase_index

Time
----

.. autoclass:: TimeConfig
   :members:

.. autodata:: Duration
   :no-value:

.. autodata:: TimePoint
   :no-value:

.. autofunction:: parse_duration

.. autofunction:: format_duration

.. autofunction:: parse_time_point

Studies
-------

.. autoclass:: Study
   :members:

.. autodata:: mango.scenario.study.CRN_MODES

.. autoclass:: StudyPlan
   :members:

.. autoclass:: StudyReport
   :members:

.. autoclass:: Factor
   :members:

.. autoclass:: DesignPoint
   :members:

.. autoclass:: Design
   :members:

.. autoclass:: FullFactorial
   :members:

.. autoclass:: Explicit
   :members:

.. autoclass:: FromCsv
   :members:

.. autoclass:: Lhs
   :members:
   :show-inheritance:

.. autoclass:: RandomSample
   :members:
   :show-inheritance:

.. autoclass:: Sobol
   :members:
   :show-inheritance:

.. autofunction:: run_one

.. autofunction:: run_one_from_file

.. autoclass:: SlurmExecutor
   :members:

.. autofunction:: load_study

.. py:class:: StudyFrame

   A ``pandas.DataFrame`` of a study, one row per planned run; returned by
   :func:`load_study`.  The class is created on first access, so that
   ``import mango.scenario`` does not import pandas.

   .. autoattribute:: mango.scenario.StudyFrame.factor_columns
      :no-value:

   .. autoattribute:: mango.scenario.StudyFrame.override_columns
      :no-value:

   .. autoproperty:: mango.scenario.StudyFrame.removed_overrides

   .. automethod:: mango.scenario.StudyFrame.summary

   .. automethod:: mango.scenario.StudyFrame.paired

   .. automethod:: mango.scenario.StudyFrame.replications_needed

   .. automethod:: mango.scenario.StudyFrame.expand

   .. automethod:: mango.scenario.StudyFrame.timeseries

   .. automethod:: mango.scenario.StudyFrame.log

   .. automethod:: mango.scenario.StudyFrame.run

   .. automethod:: mango.scenario.StudyFrame.failed

   .. automethod:: mango.scenario.StudyFrame.sobol_indices

Scenario and study files
------------------------

.. autofunction:: load_scenario

.. autofunction:: load_study_file

.. autofunction:: scenario_file_schema

.. autofunction:: study_file_schema

.. automodule:: mango.scenario.yaml_scenario
   :members:
   :exclude-members: load_scenario, load_study_file, scenario_file_schema, study_file_schema

.. currentmodule:: mango.scenario

Tooling
-------

.. autofunction:: params_form

The ``mango-scenario`` command line: ``mango.scenario.cli``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Commands, options and examples are described in :ref:`scenario-cli`.

.. automodule:: mango.scenario.cli
   :members:

Plain scripts: ``mango.scenario.script``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: mango.scenario.script
   :members:

Project templates: ``mango.scenario.templates``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: mango.scenario.templates
   :members:

.. currentmodule:: mango.scenario

Errors
------

.. autoexception:: ScenarioError

.. autoexception:: ParamsError

.. autoexception:: StudyError

.. autoexception:: RunWarning

Link patterns: ``mango.scenario.patterns``
------------------------------------------

Use the module as ``patterns.<name>`` (``from mango.scenario import
patterns``); the factories :func:`~mango.scenario.patterns.random` and
:func:`~mango.scenario.patterns.by_attr` are not part of ``from ... import *``
because ``random`` would shadow the standard library module.

.. automodule:: mango.scenario.patterns
   :members:

.. autofunction:: mango.scenario.patterns.random

.. autofunction:: mango.scenario.patterns.by_attr

.. autofunction:: mango.scenario.patterns.parameter_paths

Module helpers
--------------

.. automodule:: mango.scenario.timecfg
   :members: format_datetime

.. automodule:: mango.scenario.designs
   :members:
   :exclude-members: Factor, DesignPoint, Design, FullFactorial, Explicit, FromCsv, Lhs, Sobol, RandomSample

.. automodule:: mango.scenario.result
   :members:
   :exclude-members: RunResult, load_run, MAIN_PHASE

.. automodule:: mango.scenario.runner
   :members:
   :exclude-members: run_one, run_one_from_file, SlurmExecutor, SlurmSubmission, active_slurm_job

.. automodule:: mango.scenario.slurm
   :members:
   :exclude-members: SlurmExecutor

.. automodule:: mango.scenario.store
   :members:
   :exclude-members: load_study, StudyFrame

.. automodule:: mango.scenario.freshness
   :members:

.. automodule:: mango.scenario.preflight
   :members:

.. automodule:: mango.scenario.reducers
   :members:

.. automodule:: mango.scenario.analysis
   :members: t_quantile, mean_interval, SUMMARY_COLUMNS

.. automodule:: mango.scenario.plan
   :members: count_edges

.. automodule:: mango.scenario.sobol
   :members: SOBOL_MIN_N, SOBOL_TOLERANCE

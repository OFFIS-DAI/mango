"""
Scenario layer: declare a simulation once (groups, links, recorders), run it
with typed parameters and one seed, and get tidy, replayable results.

Needs the optional dependencies: ``pip install "mango-agents[scenario]"``.

Example::

    from pydantic import BaseModel
    from mango.scenario import (Normal, Scenario, ScenarioParams, Study, Uniform,
                                load_params, load_study)

    class EVParams(BaseModel, extra="forbid"):
        n: int = 10
        soc: Uniform = Uniform(0.2, 0.8)
        arrival_h: Normal = Normal(16.0, 1.5)

    class Params(ScenarioParams):
        ev: EVParams = EVParams()

    params = load_params(Params, "presets/base.yaml", overrides=["ev.n=20"])

    scenario = Scenario("ev_feeder", params=Params)
    coordinator = scenario.group("coordinator", 1, lambda e: CoordinatorAgent())
    ev = scenario.group("ev", "ev.n", lambda e: EVAgent(soc=e.p.ev.soc.draw(e.rng)))
    scenario.link("dispatch", ev, coordinator, pattern="many_to_one")
    scenario.record("soc", ev, attr="soc")
    result = scenario.run(params, seed=7)
    result.wide("soc")

    # a study: factors x replications, run in parallel, resumable, one folder per run
    study = Study(scenario, base="presets/base.yaml", uncertainties={"ev.n": [5, 10, 20]},
                  replications=3, seed=2026, outcomes={"mean_soc": lambda r: r.ts("soc").mean()},
                  out="results/ev_sizes")
    study.run()       # workers=8 runs a process pool (module-level scenario and outcomes)
    load_study("results/ev_sizes").groupby("ev.n")["mean_soc"].mean()
"""

_INSTALL_HINT = (
    "mango.scenario needs the optional dependencies: "
    "pip install 'mango-agents[scenario]'"
)

try:
    import pydantic as _pydantic
    import yaml as _yaml  # noqa: F401
except ImportError as _exc:
    raise ImportError(_INSTALL_HINT) from _exc

if int(_pydantic.VERSION.split(".", 1)[0]) < 2:
    raise ImportError(
        f"{_INSTALL_HINT} (found pydantic {_pydantic.VERSION}, needs >= 2)"
    )

from . import patterns  # noqa: E402
from .builder import (  # noqa: E402
    BuildContext,
    Entity,
    EntityContext,
    GroupPlan,
    GroupRef,
    KitPlan,
    LinkPlan,
    LinkSpec,
    RecordPlan,
    RecordSpec,
    Scenario,
    ScenarioPlan,
)
from .components import (  # noqa: E402
    ComponentContext,
    CompositeBehavior,
    ScenarioComponent,
    component_kinds,
    register_component,
    resolve_component,
)
from .designs import (  # noqa: E402
    Design,
    DesignPoint,
    Explicit,
    Factor,
    FromCsv,
    FullFactorial,
    Lhs,
    RandomSample,
    Sobol,
)
from .distributions import (  # noqa: E402
    Bernoulli,
    Choice,
    Constant,
    Dist,
    Distribution,
    FromColumn,
    IntUniform,
    LogNormal,
    Normal,
    Uniform,
    distribution_kinds,
)
from .comm import LayeredCommunication, LinkComm  # noqa: E402
from .errors import ParamsError, RunWarning, ScenarioError, StudyError  # noqa: E402
from .events import Event, EventPlan, Phase, PhasePlan, ScenarioEvent  # noqa: E402
from .kits import AgentPopulation, CommunicationPreset  # noqa: E402
from .params import (  # noqa: E402
    CommConfig,
    ScenarioParams,
    build_communication,
    check_path,
    deep_merge,
    dump_params,
    get_path,
    load_params,
    params_schema,
    params_to_yaml,
    parse_overrides,
    read_config_file,
    set_path,
)
from .profiles import (  # noqa: E402
    Profile,
    ProfileSpec,
    clear_profile_cache,
    load_profile,
    read_column,
)
from .result import RunResult, load_run  # noqa: E402
from .runner import run_one, run_one_from_file  # noqa: E402
from .runtime import ScenarioRun  # noqa: E402
from .slurm import SlurmExecutor, SlurmSubmission  # noqa: E402
from .store import load_study  # noqa: E402
from .study import Study, StudyPlan, StudyReport  # noqa: E402
from .timecfg import (  # noqa: E402
    Duration,
    TimeConfig,
    TimePoint,
    format_duration,
    parse_duration,
    parse_time_point,
)
from .yaml_scenario import (  # noqa: E402
    FileScenario,
    load_scenario,
    load_study_file,
    scenario_file_schema,
    study_file_schema,
)
from .form import params_form  # noqa: E402
from mango.util.seeding import SeedTree  # noqa: E402

__all__ = [
    # errors
    "ScenarioError",
    "RunWarning",
    "ParamsError",
    "StudyError",
    # time
    "Duration",
    "TimePoint",
    "TimeConfig",
    "parse_duration",
    "format_duration",
    "parse_time_point",
    # distributions
    "Distribution",
    "Constant",
    "Uniform",
    "IntUniform",
    "Normal",
    "LogNormal",
    "Choice",
    "Bernoulli",
    "FromColumn",
    "Dist",
    "distribution_kinds",
    # parameters
    "ScenarioParams",
    "CommConfig",
    "build_communication",
    "LayeredCommunication",
    "LinkComm",
    "load_params",
    "check_path",
    "deep_merge",
    "read_config_file",
    "parse_overrides",
    "set_path",
    "get_path",
    "params_schema",
    "dump_params",
    "params_to_yaml",
    # builder, runtime and results
    "Scenario",
    "GroupRef",
    "Entity",
    "EntityContext",
    "BuildContext",
    "LinkSpec",
    "RecordSpec",
    "ScenarioPlan",
    "KitPlan",
    "RecordPlan",
    "LinkPlan",
    "GroupPlan",
    "ScenarioRun",
    "RunResult",
    "load_run",
    "patterns",
    # events and phases
    "Phase",
    "Event",
    "ScenarioEvent",
    "EventPlan",
    "PhasePlan",
    # kits
    "ScenarioComponent",
    "ComponentContext",
    "CompositeBehavior",
    "register_component",
    "resolve_component",
    "component_kinds",
    "AgentPopulation",
    "CommunicationPreset",
    # profiles
    "ProfileSpec",
    "Profile",
    "load_profile",
    "read_column",
    "clear_profile_cache",
    # studies
    "Factor",
    "DesignPoint",
    "Design",
    "FullFactorial",
    "Explicit",
    "FromCsv",
    "Lhs",
    "Sobol",
    "RandomSample",
    "Study",
    "StudyPlan",
    "StudyReport",
    "StudyFrame",
    "load_study",
    "run_one",
    "run_one_from_file",
    "SlurmExecutor",
    "SlurmSubmission",
    "SeedTree",
    # scenario and study files
    "load_scenario",
    "FileScenario",
    "load_study_file",
    "scenario_file_schema",
    "study_file_schema",
    # tooling (Scenario.to_script, the mango-scenario CLI in mango.scenario.cli)
    "params_form",
]


def __getattr__(name: str):
    # StudyFrame subclasses pandas.DataFrame; it is created on first use so that
    # importing mango.scenario does not import pandas.
    if name == "StudyFrame":
        from .store import _study_frame_class

        return _study_frame_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), "StudyFrame"})

"""
The ``mango-scenario`` command line tool.

Check, plan and run scenario and study files, replay single runs, inspect
study folders, and generate schemas, plain scripts, forms and new projects.
Also runs as ``python -m mango.scenario`` (or ``python -m
mango.scenario.cli``).  The working directory is put first on ``sys.path``,
so ``module:attr`` references of the project resolve.

Exit codes: 0 success, 1 failure (invalid input, failed runs, a replay that
differs), 2 wrong usage, 130 interrupted.  Errors are printed without a
traceback; ``--debug`` shows it.

Example::

    mango-scenario validate studies/my_study.yaml
    mango-scenario plan     studies/my_study.yaml
    mango-scenario run      studies/my_study.yaml --workers 16
    mango-scenario rerun    results/my_study/run_0037

    from mango.scenario.cli import main
    exit_code = main(["plan", "studies/my_study.yaml"])
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import time as _time
import traceback
import warnings
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .configfiles import CONFIG_SUFFIXES

__all__ = ["main", "build_parser"]

PROG = "mango-scenario"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130


class UsageError(Exception):
    """Wrong command line usage (exit code 2)."""


# --------------------------------------------------------------------------- output


class _StdoutClosed(Exception):
    """The reader of stdout went away (e.g. ``| head``)."""


def _out(text: str = "") -> None:
    try:
        print(text, file=sys.stdout, flush=True)
    except BrokenPipeError:
        raise _StdoutClosed from None


def _err(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


# --------------------------------------------------------------------------- targets


def _is_file_ref(target: str) -> bool:
    return target.strip().lower().endswith(CONFIG_SUFFIXES)


def _read_keys(path: Path) -> set[str]:
    """Top-level keys of a YAML/JSON file, including those of the files it extends."""
    from .params import read_config_file

    try:
        data = read_config_file(path)
    except Exception:
        import yaml

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            data = None
    return set(data) if isinstance(data, dict) else set()


def file_kind(path: str | os.PathLike) -> str:
    """Classify a file as ``"study"``, ``"scenario"`` or ``"preset"`` by its keys.

    Study files have keys such as ``scenario``, ``uncertainties`` or
    ``design``; scenario files have ``groups``, ``links``, ``components``,
    ``record``, ``params``, ``name`` and the like; a file with parameter keys
    only is a preset.

    :param path: the file
    :return: the kind
    :raises UsageError: if the file does not exist

    Example::

        file_kind("studies/my_study.yaml")    # "study"
    """
    from .yaml_scenario import document_kind

    file = Path(path)
    if not file.is_file():
        raise UsageError(f"No such file: {path} (resolved to {file.absolute()}).")
    return document_kind(_read_keys(file))


def _load_scenario(target: str) -> Any:
    """A scenario from a scenario file, a study file (its scenario) or a module ref."""
    from .runner import load_scenario_ref

    if _is_file_ref(target):
        kind = file_kind(target)
        if kind == "study":
            from .yaml_scenario import read_study_file

            return load_scenario_ref(read_study_file(target)["scenario"])
        if kind == "preset":
            raise UsageError(
                f"{target} holds parameter values only (a preset), not a scenario; "
                "pass a scenario file, a study file or a 'module:attr' reference."
            )
    elif ":" not in target and "." not in target:
        raise UsageError(
            f"{target!r} is neither a scenario file (.yaml, .yml, .json) nor an import "
            "reference such as 'my_study.scenario:scenario'."
        )
    return load_scenario_ref(target)


def _is_study_file(target: str) -> bool:
    return _is_file_ref(target) and file_kind(target) == "study"


def _list_or_none(values: Sequence[str] | None) -> list[str] | None:
    return list(values) if values else None


@contextlib.contextmanager
def _collect_warnings():
    """Collect UserWarnings (e.g. misaligned event times) to print them once."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", UserWarning)
        yield caught


def _print_warnings(caught: list, skip: Sequence[str] = ()) -> None:
    seen: set[str] = set(skip)
    for item in caught:
        text = str(item.message)
        if issubclass(item.category, UserWarning) and text not in seen:
            seen.add(text)
            _err(f"warning: {text}")


@contextlib.contextmanager
def _live_warnings():
    """Print each distinct UserWarning when it is issued (e.g. stale runs before dispatch).

    A long study prints its warnings before its first run starts, so the user
    can stop it; repeated messages are printed once.
    """
    seen: set[str] = set()
    with warnings.catch_warnings():
        warnings.simplefilter("always", UserWarning)
        original = warnings.showwarning

        def show(message, category, filename, lineno, file=None, line=None):
            if not issubclass(category, UserWarning):
                original(message, category, filename, lineno, file, line)
                return
            text = str(message)
            if text not in seen:
                seen.add(text)
                _err(f"warning: {text}")

        warnings.showwarning = show
        yield


def _study(path: str, args: argparse.Namespace, **changes: Any) -> Any:
    """Load a study file, merging --set overrides into the file's overrides."""
    with _collect_warnings() as caught:  # e.g. a Sobol n rounded up
        study = _load_study(path, args, **changes)
    _print_warnings(caught)
    return study


def _load_study(path: str, args: argparse.Namespace, **changes: Any) -> Any:
    from .params import parse_overrides
    from .yaml_scenario import load_study_file, read_study_file

    sets = getattr(args, "set", None)
    if not sets:
        return load_study_file(path, **changes)
    current = read_study_file(path).get("overrides") or {}
    merged = dict(parse_overrides(current))
    given = parse_overrides(list(sets))
    merged.update(given)
    changes["overrides"] = merged
    study = load_study_file(path, **changes)
    factors = [factor.path for factor in study.factors]
    for key in given:
        clean = key.lstrip("+-")
        hidden = [
            f
            for f in factors
            if f == clean or f.startswith(clean + ".") or clean.startswith(f + ".")
        ]
        if hidden:
            _err(
                f"warning: --set {key} has no effect where the study varies "
                f"{', '.join(hidden)}: factor values replace overrides."
            )
    return study


def _agent_summary(
    study: Any, plan: Any, checked: list | None = None
) -> tuple[str, list]:
    """``"36 to 51 agents each, 2 links"`` over the distinct params of a study plan.

    *checked* receives ``(params, resolved params, scenario plan)`` per
    distinct parameter set.
    """
    from ._util import canonical_json
    from .params import _resolve_dump

    seen: dict[str, Any] = {}
    for spec in plan:
        key = canonical_json(spec["params"])
        if key not in seen:
            seen[key] = spec["params"]
    agents: list[int] = []
    links: list[int] = []
    scenario = study.scenario
    defaults = scenario.param_defaults() or {}
    with _collect_warnings() as caught:
        for params in seen.values():
            # the run specs hold complete, validated params
            resolved = _resolve_dump(scenario, params, defaults)
            scenario_plan = scenario._plan_resolved(resolved)
            agents.append(scenario_plan.total_agents)
            links.append(len(scenario_plan.links))
            if checked is not None:
                checked.append((params, resolved, scenario_plan))
    if not agents:
        return "no runs", caught

    def span(values: list[int], word: str, suffix: str = "") -> str:
        low, high = min(values), max(values)
        if low == high:
            return f"{_plural(low, word)}{suffix}"
        return f"{low} to {high} {word}s{suffix}"

    return f"{span(agents, 'agent', ' each')}, {span(links, 'link')}", caught


def _plan_scenario(
    scenario: Any, params: Any, sets: Sequence[str] | None, skip: Sequence[str] = ()
) -> Any:
    """``scenario.plan(...)``, printing the warnings it issued (except *skip*)."""
    with _collect_warnings() as caught:
        plan = scenario.plan(params, overrides=_list_or_none(sets))
    _print_warnings(caught, skip)
    return plan


def _plan_study(
    target: str, args: argparse.Namespace, checked: list | None = None
) -> tuple[Any, Any, str]:
    """The study, its plan and agent summary, printing the warnings issued.

    With *checked* (see :func:`_agent_summary`) the profile coverage
    problems are not printed: ``validate`` reports them as errors.
    """
    study = _study(target, args)
    plan = study.plan()
    summary, caught = _agent_summary(study, plan, checked)
    skip: list[str] = []
    if checked is not None:
        from .profiles import coverage_problems

        for _, resolved, _ in checked:
            skip += coverage_problems(resolved)
    _print_warnings(caught, skip)
    return study, plan, summary


# --------------------------------------------------------------------------- commands


def _check_data(params_list: Sequence[Any], checked: Sequence[tuple] = ()) -> int:
    """Print the data problems of the runs' parameters; return the number of errors.

    Errors: files that do not load, and dated, non-wrapping profiles that
    do not cover the run (the run is certain to fail).  Warnings: a
    :class:`~mango.scenario.FromColumn` in index mode with fewer rows than a
    group has entities (rows are reused).  *checked* holds ``(params,
    resolved params, scenario plan)`` per distinct parameter set.
    """
    from .preflight import check_data_files
    from .profiles import coverage_problems

    problems = check_data_files(params_list)
    for problem in problems:
        _err(f"error: data file: {problem}")
    errors = len(problems)
    seen: set[str] = set()
    for _, resolved, _ in checked:
        for message in coverage_problems(resolved):
            if message not in seen:
                seen.add(message)
                errors += 1
                _err(f"error: {message}")
    for message in _column_reuse_warnings(checked):
        _err(f"warning: {message}")
    return errors


def _column_reuse_warnings(checked: Sequence[tuple]) -> list[str]:
    """FromColumn tables in index mode (``wrap`` unset) shorter than a group.

    The entity that reads a table is not known before the agents are built,
    so the table is compared with the group named like the first part of
    its parameter path, else with the largest group (``validate --build``
    confirms it).
    """
    from ._util import canonical_json
    from .distributions import FromColumn
    from .preflight import data_references

    largest: dict[str, tuple[int, str, int, str, dict]] = {}
    for params, _, scenario_plan in checked:
        groups = {group.name: group.count for group in scenario_plan.groups}
        if not groups:
            continue
        for kind, path, spec in data_references(params):
            if kind != "column" or spec.get("mode", "index") != "index":
                continue
            if spec.get("wrap") is not None:
                continue
            key = canonical_json([path, spec])
            try:
                rows = len(FromColumn.model_validate(spec).column_values())
            except Exception:  # noqa: BLE001 - check_data_files reports it
                continue
            head = path.partition(".")[0]
            name = head if head in groups else max(groups, key=groups.get)
            count = groups[name]
            if count > rows and count > largest.get(key, (0,))[0]:
                largest[key] = (count, name, rows, path, spec)
    return [
        f"{path}: FromColumn({spec.get('path')!r}, {spec.get('column')!r}) has {rows} "
        f"row{'s' * (rows != 1)}, but group {name!r} has {count} entities: if its "
        "entities read it by index, rows are reused modulo the table (set wrap: true "
        "to reuse them on purpose, wrap: false to make it an error, or mode: sample to "
        "draw rows)"
        for count, name, rows, path, spec in largest.values()
    ]


def _build_once(scenario: Any, params: Any, seed: Any) -> str:
    """Build one run, take one step and record: ``"built 41 agents, 1 step"``."""
    from .params import dump_params
    from .runtime import run_sync

    if not isinstance(params, str | Mapping | list | type(None)):
        # a params instance: build exactly the complete values (as run does),
        # so removal overrides (-key) are not undone by the scenario defaults
        params = dump_params(params)
    run = scenario.build(params, seed=seed)

    async def smoke():
        async with run:
            await run.step()
            # every recorder once, also those a record: false warm-up skips
            probed = run._probe_recorders()
            return run.result(), probed

    with _collect_warnings() as caught:
        result, probed = run_sync(smoke)
    _print_warnings(caught)
    agents = result.stats.get("agents")
    recorders = len(run._state.records)
    checked = ""
    if recorders:
        checked = (
            f"; {_plural(recorders, 'recorder')} evaluated ({_plural(probed, 'value')})"
        )
        if not len(result.frame):
            checked += " although the first step records nothing (record: false)"
    return (
        f"run built ({_plural(agents, 'agent') if isinstance(agents, int) else 'agents'}"
        f"), one step taken and {_plural(len(result.frame), 'value')} recorded"
        f"{checked}"
    )


def _coverage_texts(params: Any) -> list[str]:
    from .profiles import coverage_problems

    return coverage_problems(params)


def cmd_validate(args: argparse.Namespace) -> int:
    from ._util import canonical_json
    from .params import dump_params

    target = args.target
    if args.scenario:
        kind = "preset"
    elif _is_file_ref(target):
        kind = file_kind(target)
    else:
        kind = "scenario"  # a 'module:attr' reference
    if kind == "study":
        from .runner import check_refs
        from .study import StudyError

        checked: list = []
        study, plan, summary = _plan_study(target, args, checked)
        notes = []
        try:
            check_refs(study.scenario_ref, study._outcomes)
        except StudyError as exc:
            notes.append(f"note: worker processes cannot run it: {exc}")
        distinct = {canonical_json(spec["params"]): spec["params"] for spec in plan}
        problems = _check_data([spec["params"] for spec in plan], checked)
        if problems:
            _err(
                f"{target}: {_plural(problems, 'data file problem')} would fail runs; "
                "fix them before running the study."
            )
            return EXIT_FAILURE
        built = ""
        if args.build and len(plan):
            first = plan[0]
            built = "; " + _build_once(study.scenario, first["params"], first["seed"])
        _out(
            f"OK: {target} is a valid study: {_plural(len(plan), 'run')} "
            f"({summary}), scenario {study.scenario_key}; parameters, declarations "
            f"and data files of {_plural(len(distinct), 'distinct parameter set')} "
            f"checked{built}"
        )
        for note in notes:
            _out(note)
        if not args.build:
            _out(
                "note: agents were not built; --build builds run 0 and takes one step "
                "(checks factories, profile columns picked at run time and recorder "
                "attributes)"
            )
        return EXIT_OK
    if kind == "preset":
        if not args.scenario:
            raise UsageError(
                f"{target} holds parameter values only (a preset); pass --scenario REF "
                "to validate it against a scenario's parameters."
            )
        scenario = _load_scenario(args.scenario)
        presets = [target, *(args.params or [])]
        params = scenario.resolve_params(presets, overrides=_list_or_none(args.set))
        skip = _coverage_texts(params)
        plan = _plan_scenario(scenario, presets, args.set, skip)
        if _check_data([dump_params(params)], [(dump_params(params), params, plan)]):
            return EXIT_FAILURE
        built = "; " + _build_once(scenario, params, None) if args.build else ""
        _out(
            f"OK: {target} is a valid preset for {scenario.ref or scenario.name}: "
            f"{_plural(plan.total_agents, 'agent')}, {_plural(len(plan.links), 'link')}"
            f"{built}"
        )
        return EXIT_OK
    scenario = _load_scenario(target)
    params = scenario.resolve_params(
        _list_or_none(args.params), overrides=_list_or_none(args.set)
    )
    skip = _coverage_texts(params)
    plan = _plan_scenario(scenario, _list_or_none(args.params), args.set, skip)
    if _check_data([dump_params(params)], [(dump_params(params), params, plan)]):
        return EXIT_FAILURE
    built = "; " + _build_once(scenario, params, None) if args.build else ""
    _out(
        f"OK: {target} is a valid scenario {scenario.name!r}: "
        f"{_plural(plan.total_agents, 'agent')} in {_plural(len(plan.groups), 'group')}, "
        f"{_plural(len(plan.links), 'link')}, {_plural(len(plan.recorders), 'recorder')}"
        f"{built}"
    )
    return EXIT_OK


def cmd_plan(args: argparse.Namespace) -> int:
    target = args.target
    if _is_study_file(target):
        if args.params:
            raise UsageError(
                "--params applies to scenarios; a study file sets its presets with 'base:'."
            )
        study, plan, summary = _plan_study(target, args)
        if args.json:
            frame = plan.to_frame()
            _out(
                json.dumps(
                    {
                        "study": study.name,
                        "runs": len(plan),
                        "summary": summary,
                        "seed": plan.seed,
                        "run_table": json.loads(frame.to_json(orient="records")),
                    },
                    indent=2,
                )
            )
            return EXIT_OK
        _out(f"{_plural(len(plan), 'run')}, {summary}")
        _out(str(plan))
        if plan.out is not None and Path(plan.out).is_dir():
            from .store import StudyStore

            store = StudyStore(plan.out)
            done = sum(
                1 for run_id in plan.run_ids if store.run_state(run_id) == "done"
            )
            _out(
                f"{done} of the {_plural(len(plan), 'planned run')} are done in "
                f"{plan.out} (finished runs are skipped; stale ones run again)"
            )
        return EXIT_OK
    plan = _plan_scenario(_load_scenario(target), _list_or_none(args.params), args.set)
    if args.json:
        _out(json.dumps(plan.to_dict(), indent=2, default=str))
    else:
        _out(str(plan))
    return EXIT_OK


#: ``run`` options (argparse dests) passed on as ``SlurmExecutor`` settings.
_SLURM_SETTINGS = (
    "partition",
    "time",
    "mem",
    "array_parallelism",
    "cpus_per_task",
    "account",
    "qos",
    "extra_sbatch",
    "setup",
    "runs_per_task",
    "max_array_size",
    "python",
)
#: ``run`` options (argparse dests) that only apply to ``--executor slurm``.
_SLURM_OPTIONS = (*_SLURM_SETTINGS, "no_submit")
#: ``run`` options (argparse dests) that only apply to studies.
_STUDY_OPTIONS = (
    "workers",
    "executor",
    "runs_per_worker",
    "no_retry_failed",
    "keep_stale",
    "require_clean",
    *_SLURM_OPTIONS,
)


def _given(args: argparse.Namespace, dests: Sequence[str]) -> list[str]:
    """The flags (``--array-parallelism``) of the options in *dests* that were given."""
    return [
        "--" + dest.replace("_", "-")
        for dest in dests
        if getattr(args, dest) is not None and getattr(args, dest) is not False
    ]


def _slurm_executor(args: argparse.Namespace, study: Any) -> Any:
    """A SlurmExecutor from the study file's ``executor.slurm`` settings and the flags."""
    from .slurm import SlurmExecutor

    options = dict((study.executor or {}).get("slurm") or {})
    options.update(
        {
            dest: getattr(args, dest)
            for dest in _SLURM_SETTINGS
            if getattr(args, dest) is not None
        }
    )
    if args.no_submit:
        options["submit"] = False
    return SlurmExecutor(**options)


def _run_study(args: argparse.Namespace) -> int:
    if args.until is not None:
        raise UsageError(
            "--until stops a single scenario run early for a quick test; studies always "
            "run to the horizon (try the scenario with the study's base: "
            f"{PROG} run <scenario> -p <preset> --until 1h)."
        )
    for flag, value in (("--seed", args.seed), ("--params", args.params)):
        if value is not None:
            raise UsageError(
                f"{flag} applies to single scenario runs; a study file sets "
                + (
                    "its seed with 'seed:'."
                    if flag == "--seed"
                    else "presets with 'base:'."
                )
            )
    changes = {}
    if args.out is not None:
        changes["out"] = args.out
    if args.format is not None:
        changes["format"] = args.format
    study = _study(args.target, args, **changes)
    slurm_flags = _given(args, _SLURM_OPTIONS)
    executor: Any = args.executor
    on_slurm = executor == "slurm" or (
        executor is None
        and args.workers is None
        and (study.executor or {}).get("kind") == "slurm"
    )
    if on_slurm:
        if args.workers is not None or args.runs_per_worker is not None:
            raise UsageError(
                "--workers and --runs-per-worker do not apply to --executor slurm "
                "(use --runs-per-task for several runs per array task)."
            )
        executor = _slurm_executor(args, study)
    elif slurm_flags:
        raise UsageError(
            f"{', '.join(slurm_flags)} "
            f"{'only apply' if len(slurm_flags) > 1 else 'only applies'} to --executor "
            "slurm."
        )
    with _live_warnings():  # stale runs and a replaced plan before the first run
        report = study.run(
            executor=executor,
            workers=args.workers,
            retry_failed=not args.no_retry_failed,
            progress=not args.quiet,
            runs_per_worker=args.runs_per_worker,
            stale="keep" if args.keep_stale else "rerun",
            require_clean=args.require_clean,
        )
    _out(str(report))
    if report.ok:
        return EXIT_OK
    submitted_only = (
        report.submitted and report.failed == 0 and report.pending == report.submitted
    )
    return EXIT_OK if submitted_only else EXIT_FAILURE


def _run_scenario(args: argparse.Namespace) -> int:
    from ._util import git_info
    from .builder import _resolve_seed
    from .params import dump_params
    from .result import ERROR_FILE, MANIFEST_FILE, compute_run_id
    from .runner import _execute
    from .study import SPEC_VERSION

    study_flags = _given(args, _STUDY_OPTIONS)
    if study_flags:
        raise UsageError(
            f"{study_flags[0]} applies to study files, not to single runs."
        )
    scenario = _load_scenario(args.target)
    params = scenario.resolve_params(
        _list_or_none(args.params), overrides=_list_or_none(args.set)
    )
    seed = _resolve_seed(args.seed, params).to_dict()
    key = scenario.ref or (args.target if not _is_file_ref(args.target) else None)
    key = key or scenario.name
    config = dump_params(params)
    run_id = compute_run_id(key, config, seed)
    until_s = _until_seconds(args.until, params) if args.until is not None else None
    name = run_id if until_s is None else f"{run_id}_until_{until_s:g}s"
    folder = Path(args.out) if args.out else Path("results") / scenario.name / name
    spec = {
        "spec_version": SPEC_VERSION,
        "study": None,
        "index": 0,
        "run_id": run_id,
        "scenario": key,
        "scenario_fingerprint": scenario.fingerprint(),
        "params": config,
        "seed": seed,
        "factors": {},
        "labels": {},
        "replication": 0,
        "design": {},
        "outcomes": {},
        "out": str(folder.absolute()),
        "project_root": str(Path.cwd()),
        "git": git_info(),
        "format": args.format or "auto",
    }
    if until_s is not None:
        spec["until"] = args.until
    started = _time.perf_counter()
    with _live_warnings():  # "warning: <text>", not a library source line
        outcome = _execute(spec, scenario=scenario, outcomes={})
    wall = _time.perf_counter() - started
    if outcome["status"] != "done":
        _err(f"error: run {run_id} failed: {outcome['error']}")
        if outcome.get("error_context"):
            _err(f"  ({outcome['error_context']})")
        _err(f"  traceback: {folder / ERROR_FILE}")
        return EXIT_FAILURE
    manifest = json.loads((folder / MANIFEST_FILE).read_text(encoding="utf-8"))
    _out(f"run {run_id} of scenario {scenario.name!r} done in {wall:.1f} s -> {folder}")
    stats = manifest.get("stats") or {}
    if until_s is not None and stats.get("stop_reason") == "until":
        horizon = params.time.horizon_s
        _out(
            f"  truncated: stopped at {args.until} ({until_s:g} s) of the "
            f"{horizon:g} s horizon (a quick test run, not a full one)"
        )
    from .events import describe_stop, empty_run_reason

    stopped = describe_stop(config, stats)
    if stopped and stats.get("stop_reason") != "until":
        _out(f"  {stopped}")
    if not manifest.get("rows"):
        _out(f"  nothing recorded: {empty_run_reason(config, stats)}")
    _out(
        f"  {_plural(manifest.get('rows', 0), 'value')} recorded, checksum "
        f"{str(manifest.get('checksum'))[:16]}; replay: {PROG} rerun {folder}"
    )
    return EXIT_OK


def _until_seconds(text: str, params: Any) -> float:
    """The offset of ``--until`` (a duration or an absolute time) in seconds."""
    time = params.time
    try:
        target = float(time.offset(text))
    except ValueError as exc:
        raise UsageError(f"--until {text}: {exc}") from None
    if target <= 0 or target > time.horizon_s * (1 + 1e-9):
        raise UsageError(
            f"--until {text} is {target:g} s; it must lie after the start and at most at "
            f"the horizon ({time.horizon_s:g} s)."
        )
    return target


def cmd_run(args: argparse.Namespace) -> int:
    if _is_study_file(args.target):
        return _run_study(args)
    return _run_scenario(args)


def _runs_per_worker(text: str) -> int | str:
    """``--runs-per-worker``: ``auto`` or an int >= 1."""
    if text.strip().lower() == "auto":
        return "auto"
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError(
            f"expected 'auto' or an int >= 1, got {text!r}"
        )
    return value


def _line_range(text: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", text or "")
    if not match:
        raise UsageError(
            f"--lines takes START:STOP (0-based, STOP exclusive), e.g. --lines 8:12; "
            f"got {text!r}."
        )
    start, stop = int(match.group(1)), int(match.group(2))
    if stop <= start:
        raise UsageError(f"--lines {text}: STOP must be greater than START.")
    return start, stop


def cmd_run_one(args: argparse.Namespace) -> int:
    from .runner import run_lines_from_file, run_one_from_file

    if args.lines is not None:
        start, stop = _line_range(args.lines)
        outcomes = run_lines_from_file(args.file, start, stop, force=args.force)
    else:
        outcomes = [run_one_from_file(args.file, args.line, force=args.force)]
    failed = 0
    for outcome in outcomes:
        name = f"run {outcome['index']} ({outcome['run_id']})"
        if outcome.get("skipped"):
            _out(f"{name} already done; skipped (--force runs it again)")
        elif outcome["status"] == "done":
            stale = " (it was stale; manifest.prev.json keeps the old manifest)"
            cpu = outcome.get("cpu_s")
            _out(
                f"{name} done in {outcome['wall_s']:.1f} s"
                + (f" (cpu {cpu:.1f} s)" if isinstance(cpu, int | float) else "")
                + (stale if outcome.get("stale") else "")
            )
            for outcome_name, error in (outcome.get("outcome_errors") or {}).items():
                _err(f"warning: {name}: outcome {outcome_name!r} failed: {error}")
            for text in outcome.get("warnings") or ():
                _err(f"warning: {name}: {text}")
        else:
            failed += 1
            _err(f"error: {name} failed: {outcome['error']}")
    return EXIT_FAILURE if failed else EXIT_OK


def _resolve_run_folder(path: str, which: str | None) -> Path:
    """The run folder named by *path* (and *which*: a run index or run id)."""
    from .result import MANIFEST_FILE, SPEC_FILE
    from .store import StudyStore

    folder = Path(path)
    if which is None and (
        (folder / SPEC_FILE).is_file() or (folder / MANIFEST_FILE).is_file()
    ):
        return folder
    study_dir, selector = (
        (folder, which) if which is not None else (folder.parent, folder.name)
    )
    store = StudyStore(study_dir)
    if not store.runs_file.is_file():
        if which is None and folder.is_dir():
            raise UsageError(
                f"{folder} is not a run folder (no spec.json or manifest.json); pass a "
                f"run folder, or a study folder and a run index: {PROG} rerun "
                "results/<study> 137."
            )
        raise UsageError(
            f"No run folder {folder} and no study folder {study_dir} to look the run up "
            "in."
        )
    return store.run_dir(
        _select(store.read_runs(), selector, f"Study {study_dir}")["run_id"]
    )


def _select(specs: Sequence[Mapping], selector: str, where: str) -> dict:
    """:func:`~mango.scenario.store.select_run`, with its errors as usage errors."""
    from .errors import StudyError
    from .store import select_run

    try:
        return select_run(specs, selector, where=where)
    except StudyError as exc:
        raise UsageError(str(exc)) from None


def _replay_spec(folder: Path) -> tuple[dict, dict]:
    from .result import MANIFEST_FILE, SPEC_FILE, load_run

    manifest_file = folder / MANIFEST_FILE
    if not manifest_file.is_file():
        raise UsageError(
            f"Run folder {folder} has no manifest.json: the run never finished; "
            "nothing to compare."
        )
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("status") != "done":
        raise UsageError(
            f"Run {manifest.get('run_id')} in {folder} is {manifest.get('status')!r} "
            f"(error: {manifest.get('error')}); only done runs can be compared."
        )
    spec_file = folder / SPEC_FILE
    if spec_file.is_file():
        spec = json.loads(spec_file.read_text(encoding="utf-8"))
    else:
        stored = load_run(folder)
        spec = {
            "scenario": manifest.get("scenario"),
            "params": stored.config,
            "seed": manifest.get("seed"),
            "scenario_fingerprint": manifest.get("scenario_fingerprint"),
            "project_root": None,
        }
    return spec, manifest


def _scenario_entries(ref: Any) -> list[str]:
    """Names whose presence marks the folder a scenario reference resolves in."""
    if not isinstance(ref, str) or not ref.strip():
        return []
    ref = ref.strip()
    if _is_file_ref(ref):
        return [ref]
    top = ref.partition(":")[0].partition(".")[0]
    return [top, f"{top}.py"] if top.isidentifier() else []


def _rerun_root(folder: Path, spec: Mapping) -> str | None:
    """The project root of a replay.

    The recorded root when it (still) holds the scenario; else nothing when
    the working directory holds it; else the nearest ancestor of the run
    folder that holds it (the project was moved or copied).
    """
    recorded = spec.get("project_root")
    recorded = (
        recorded if isinstance(recorded, str) and Path(recorded).is_dir() else None
    )
    entries = _scenario_entries(spec.get("scenario"))

    def holds(path: Path) -> bool:
        return any((path / name).exists() for name in entries)

    if not entries or (recorded is not None and holds(Path(recorded))):
        return recorded
    if holds(Path.cwd()):
        return None
    for ancestor in folder.absolute().parents:
        if holds(ancestor):
            _err(
                f"note: the recorded project root {spec.get('project_root')} does not "
                f"hold {spec.get('scenario')}; using {ancestor}, which does"
            )
            return str(ancestor)
    return recorded


def cmd_rerun(args: argparse.Namespace) -> int:
    from .freshness import describe_stale, stale_reasons
    from .result import frame_checksum, load_run, log_and_metric_differences
    from .runner import _add_project_root, _check_fingerprint, load_scenario_ref
    from .study import StudyError

    folder = _resolve_run_folder(args.run, args.which)
    spec, manifest = _replay_spec(folder)
    root = _rerun_root(folder, spec)
    if root is not None:
        _add_project_root(root)
    scenario = load_scenario_ref(spec["scenario"])
    try:
        _check_fingerprint(scenario, {**spec, **manifest}, replay=True)
    except StudyError as exc:
        _err(f"warning: {exc}")
    roots = [root, spec.get("project_root"), manifest.get("project_root")]
    reasons = stale_reasons(
        manifest,
        roots=[r for r in roots if r],
        scenario_fingerprint=scenario.fingerprint(),
    )
    if reasons:
        _err(f"note: changed since the run: {describe_stale([reasons])}")
    if manifest.get("changed_during_run"):
        _err(
            "note: files changed while the run executed: "
            + ", ".join(manifest["changed_during_run"])
        )
    started = _time.perf_counter()
    result = scenario.run(spec["params"], seed=spec["seed"], until=spec.get("until"))
    wall = _time.perf_counter() - started
    stored = manifest.get("checksum")
    replayed = result.checksum()
    run_id = manifest.get("run_id")
    if stored == replayed:
        _out(
            f"identical: run {run_id} replayed in {wall:.1f} s, checksum "
            f"{replayed[:16]} ({_plural(len(result.frame), 'value')})"
        )
        if args.certify:
            _certify_replayed(folder, manifest, scenario, replayed)
        elif reasons or manifest.get("changed_during_run"):
            _err(
                "note: the run is stale, but the current code replays it identically; "
                f"'{PROG} rerun ... --certify' records that it is still valid"
            )
        return EXIT_OK
    if args.certify:
        _err("note: not certified: the replay differs")
    _out(f"differs: run {run_id} replayed in {wall:.1f} s")
    _out(f"  stored checksum   {stored}")
    _out(f"  replayed checksum {replayed}")
    stored_run = load_run(folder)
    lines = _frame_differences(stored_run.frame, result.frame)
    extra = log_and_metric_differences(stored_run, result)
    if extra and frame_checksum(stored_run.frame) == frame_checksum(result.frame):
        lines = extra  # the time series are equal
    else:
        lines += extra
    for line in lines:
        _out(f"  {line}")
    return EXIT_FAILURE


def _certify_replayed(
    folder: Path, manifest: Mapping, scenario: Any, checksum: str
) -> None:
    """Certify a stale run that replayed identically (``rerun --certify``)."""
    from .store import StudyStore

    if manifest.get("study") is None or folder.parent.name != "runs":
        _err("note: not certified: only runs of a study folder can be certified")
        return
    store = StudyStore(folder.parent.parent)
    done = store.certify(
        [manifest["run_id"]],
        reason=f"replayed identically (checksum {checksum[:16]}) with the current code",
        scenario_fingerprint=scenario.fingerprint(),
    )
    if done:
        _out(
            f"certified: run {manifest['run_id']} is current again (manifest.prev.json keeps the old manifest)"
        )
    else:
        _out(f"run {manifest['run_id']} is up to date; nothing to certify")


def cmd_certify(args: argparse.Namespace) -> int:
    from .runner import _add_project_root, load_scenario_ref
    from .store import StudyStore

    store = StudyStore(args.out)
    try:
        specs = store.read_runs()
    except FileNotFoundError:
        raise UsageError(
            f"{args.out} is not a study folder (no runs.jsonl); pass the 'out' folder "
            "of a study."
        ) from None
    run_ids = None
    if args.runs:
        run_ids = [
            _select(specs, run, f"Study {args.out}")["run_id"] for run in args.runs
        ]
    definition = store.read_definition() or {}
    fingerprint = None
    root = next(
        (spec.get("project_root") for spec in specs if spec.get("project_root")), None
    )
    try:
        if root and Path(root).is_dir():
            _add_project_root(root)
        fingerprint = load_scenario_ref(definition["scenario"]).fingerprint()
    except Exception as exc:  # noqa: BLE001 - certify the files anyway
        _err(
            f"note: cannot load the scenario {definition.get('scenario')!r} ({exc}); "
            "only code and data files are certified, a changed scenario definition stays "
            "stale"
        )
    certified = store.certify(
        run_ids, reason=args.reason, scenario_fingerprint=fingerprint
    )
    if not certified:
        _out(f"{args.out}: no stale done runs to certify")
        return EXIT_OK
    _out(
        f"certified {_plural(len(certified), 'run')} of {args.out} ({args.reason}); "
        "each manifest records the reason, manifest.prev.json keeps the old one"
    )
    return EXIT_OK


def _frame_differences(stored: Any, replayed: Any, limit: int = 10) -> list[str]:
    """Per-variable summary of where two tidy tables differ."""
    columns = ["time", "phase", "group", "entity", "variable", "value"]
    lines: list[str] = []
    if len(stored) != len(replayed):
        lines.append(f"rows: stored {len(stored)}, replayed {len(replayed)}")
    variables = list(
        dict.fromkeys([*stored["variable"].tolist(), *replayed["variable"].tolist()])
    )
    for variable in variables:
        a = stored[stored["variable"] == variable][columns].reset_index(drop=True)
        b = replayed[replayed["variable"] == variable][columns].reset_index(drop=True)
        rows_a = [tuple(map(repr, row)) for row in a.itertuples(index=False)]
        rows_b = [tuple(map(repr, row)) for row in b.itertuples(index=False)]
        if rows_a == rows_b:
            continue
        first = next(
            (i for i, (x, y) in enumerate(zip(rows_a, rows_b)) if x != y),
            min(len(rows_a), len(rows_b)),
        )
        where = ""
        if first < len(rows_a):
            row = a.iloc[first]
            where = f", first at t={row['time']:g} s ({row['entity']})"
        lines.append(
            f"{variable}: {len(rows_a)} stored vs {len(rows_b)} replayed values{where}"
        )
        if len(lines) >= limit:
            lines.append("...")
            break
    return lines or ["(the values are equal; the stored table may have been edited)"]


#: ``study.yaml`` keys that are arguments of :class:`~mango.scenario.Study`.
_DEFINITION_ARGS = (
    "name",
    "base",
    "overrides",
    "uncertainties",
    "levers",
    "design",
    "replications",
    "seed",
    "common_random_numbers",
    "outcomes",
    "subexperiments",
    "format",
)


def _replan(store: Any, specs: Sequence[Mapping]) -> list[dict] | None:
    """Plan the stored ``study.yaml`` again with today's code (``None`` if impossible).

    Runs in the project root of the runs, as the study did; any failure
    (the scenario no longer imports, a preset moved) gives ``None``.
    """
    from .study import Study

    definition = store.read_definition() or {}
    root = next(
        (spec.get("project_root") for spec in specs if spec.get("project_root")), None
    )
    if not definition.get("scenario") or not root or not Path(root).is_dir():
        return None
    previous = os.getcwd()
    try:
        os.chdir(root)
        if root not in sys.path:
            sys.path.insert(0, root)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            study = Study(
                definition["scenario"],
                out=store.out,
                **{
                    key: definition[key]
                    for key in _DEFINITION_ARGS
                    if definition.get(key) is not None
                },
            )
            return list(study.plan(out=store.out))
    except Exception:  # noqa: BLE001 - a prediction only
        return None
    finally:
        os.chdir(previous)


def cmd_status(args: argparse.Namespace) -> int:
    from .freshness import describe_stale
    from .slurm import active_slurm_job
    from .store import StudyStore
    from .study import _params_changes

    store = StudyStore(args.out)
    try:
        specs = store.read_runs()
    except FileNotFoundError:
        raise UsageError(
            f"{args.out} is not a study folder (no runs.jsonl); pass the 'out' folder "
            "of a study."
        ) from None
    rows = store.status_rows(specs, details=True)
    counts = {"done": 0, "failed": 0, "pending": 0}
    for row in rows:
        counts[row["status"]] += 1
    started = sum(
        1
        for row in rows
        if row["status"] == "pending" and store.run_state(row["run_id"]) == "partial"
    )
    definition = store.read_definition() or {}
    name = definition.get("name") or Path(args.out).name
    pending = f"{counts['pending']} pending" + (
        f" ({started} started: running or interrupted)" if started else ""
    )
    stale = [row for row in rows if row["stale"]]
    with_errors = [row for row in rows if row["outcome_errors"]]
    with_warnings = [row for row in rows if row["warning_texts"]]
    during = [row for row in rows if row["changed_during_run"] and row["stale"]]
    certified = [row for row in rows if row["certified"]]
    extras = ""
    if stale:
        extras += f", {len(stale)} stale"
    if with_errors:
        extras += f", {len(with_errors)} with outcome errors"
    if with_warnings:
        extras += f", {len(with_warnings)} with warnings"
    _out(
        f"{name}: {_plural(len(rows), 'run')}: {counts['done']} done, "
        f"{counts['failed']} failed, {pending}{extras}"
    )
    job = active_slurm_job(args.out)
    if job is not None:
        _out(f"  Slurm job {job} has queued or running tasks")
    cpu = [row["cpu_s"] for row in rows if isinstance(row["cpu_s"], int | float)]
    wall = [row["wall_s"] for row in rows if isinstance(row["wall_s"], int | float)]
    if cpu:
        _out(
            f"  time: {sum(wall):.1f} s wall, {sum(cpu):.1f} s CPU over "
            f"{_plural(len(cpu), 'finished run')} ({sum(cpu) / len(cpu):.2f} s CPU per "
            "run on average)"
        )
    planned = _replan(store, specs)
    planned_ids = [spec["run_id"] for spec in planned] if planned is not None else None
    same_plan = planned_ids is None or set(planned_ids) == {
        spec["run_id"] for spec in specs
    }
    if stale:
        again = (
            f"; '{PROG} run <study file>' runs them again, '{PROG} certify "
            f"{args.out} --reason ...' keeps them"
            if same_plan
            else ""
        )
        _out(
            f"  {_plural(len(stale), 'done run')} {'is' if len(stale) == 1 else 'are'} "
            f"stale ({describe_stale(row['stale'] for row in stale)}){again}"
        )
    if during:
        _out(
            f"  {_plural(len(during), 'run')} used code or data that changed while the "
            "study ran: "
            + ", ".join(
                dict.fromkeys(f for row in during for f in row["changed_during_run"])
            )
        )
    if certified:
        last = certified[-1]["certified"][-1]
        _out(
            f"  {_plural(len(certified), 'run')} certified as current after changes "
            f"(latest: {last.get('reason')!r})"
        )
    if not same_plan:
        on_disk = sum(
            1 for run_id in planned_ids if store.run_state(run_id) in ("done", "failed")
        )
        changes = _params_changes(specs, planned)
        _out(
            f"  the study definition now plans {_plural(len(planned_ids), 'run')} "
            f"({on_disk} of them on disk) with today's code"
            + (f": {changes}" if changes else "")
            + f"; '{PROG} run <study file>' runs the {len(planned_ids) - on_disk} "
            "others (run ids hash the resolved parameters, defaults included)"
        )
    commits: dict[str, int] = {}
    dirty = 0
    for row in rows:
        git = row["git"] if row["status"] == "done" else None
        if isinstance(git, dict):
            if git.get("commit"):
                commits[git["commit"]] = commits.get(git["commit"], 0) + 1
            dirty += bool(git.get("dirty"))
    if len(commits) > 1 or dirty:
        _out(
            f"  git: the done runs come from {_plural(len(commits), 'commit')} ("
            + ", ".join(f"{commit[:8]}: {n}" for commit, n in commits.items())
            + ")"
            + (
                f", {dirty} from a working tree with uncommitted changes (diffs in "
                f"{Path(args.out) / 'git'})"
                if dirty
                else ""
            )
        )
    earlier = store.plan_ids()
    if earlier:
        _out(
            f"  {_plural(len(earlier), 'earlier plan')} of this study in "
            f"{store.plans_dir.name}/ (other overrides, factors or parameters); their "
            f"runs: load_study({str(args.out)!r}, plan='all')"
        )
    failed = [row for row in rows if row["status"] == "failed"]
    shown = failed if args.failed else failed[:10]
    for row in shown:
        _out(f"  failed run {row['index']} ({row['run_id']}): {row['error']}")
    if len(shown) < len(failed):
        _out(f"  ... and {len(failed) - len(shown)} more (--failed lists all)")
    from .study import outcome_error_lines

    for line in outcome_error_lines(
        (row["index"], row["run_id"], row["outcome_errors"]) for row in with_errors
    ):
        _out(line)
    if with_warnings:
        counts_by_text: dict[str, list[int]] = {}
        for row in with_warnings:
            for text in row["warning_texts"]:
                counts_by_text.setdefault(text, []).append(row["index"])
        _out(f"  warnings of {_plural(len(with_warnings), 'run')}:")
        for text, indices in list(counts_by_text.items())[:5]:
            runs = ", ".join(map(str, indices[:5])) + (
                " ..." if len(indices) > 5 else ""
            )
            _out(f"    run{'s' * (len(indices) != 1)} {runs}: {text}")
        if len(counts_by_text) > 5:
            _out(
                f"    ... and {len(counts_by_text) - 5} more distinct warnings "
                "(manifest.json 'warnings' of each run)"
            )
    with contextlib.suppress(OSError):  # a read-only results folder
        store.write_status(specs)  # status.csv is a snapshot; refresh it
    return EXIT_FAILURE if failed else EXIT_OK


def _write_or_print(text: str, output: str | None, what: str) -> None:
    if output is None or output == "-":
        try:
            sys.stdout.write(text if text.endswith("\n") else text + "\n")
            sys.stdout.flush()
        except BrokenPipeError:
            raise _StdoutClosed from None
        return
    path = Path(output)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _err(f"wrote {what} to {path}")


def cmd_schema(args: argparse.Namespace) -> int:
    from .params import params_schema
    from .yaml_scenario import scenario_file_schema, study_file_schema

    if args.kind == "study-file":
        if args.target:
            raise UsageError("--kind study-file takes no target.")
        schema = study_file_schema()
    elif args.kind == "scenario-file":
        schema = scenario_file_schema(
            _load_scenario(args.target) if args.target else None
        )
    else:
        if not args.target:
            raise UsageError(
                "schema needs a scenario (file or 'module:attr'), or --kind study-file / "
                "--kind scenario-file."
            )
        schema = params_schema(_load_scenario(args.target))
    _write_or_print(
        json.dumps(schema, indent=2), args.output, f"the {args.kind} schema"
    )
    return EXIT_OK


def _study_params(target: str, args: argparse.Namespace) -> tuple[Any, Any]:
    """The params and overrides of a study file's base run, then ``--params``/``--set``."""
    from .params import parse_overrides
    from .yaml_scenario import read_study_file

    kwargs = read_study_file(target)
    base = kwargs.get("base")
    presets = [] if base is None else list(base) if isinstance(base, list) else [base]
    presets += list(args.params or [])
    overrides = parse_overrides(kwargs.get("overrides"))
    overrides.update(parse_overrides(_list_or_none(args.set)))
    return presets or None, overrides or None


def _factor_example(target: str) -> str:
    """``" (e.g. --set field.n=3)"`` for the study's first factor, else ``""``."""
    from .yaml_scenario import read_study_file

    kwargs = read_study_file(target)
    fallback = ""
    for key in ("levers", "uncertainties"):
        factors = kwargs.get(key)
        if not isinstance(factors, dict):
            continue
        for path, levels in factors.items():
            if isinstance(levels, list | tuple) and levels:
                level = levels[0]
                if isinstance(level, bool | int | float | str):
                    text = str(level).lower() if isinstance(level, bool) else level
                    return f" (e.g. --set {path}={text})"
            fallback = fallback or f" (e.g. --set {path}=<value>)"
    return fallback


def _seed_arg(text: str) -> Any:
    """A run seed from the command line: an int, or a seed dict as JSON."""
    try:
        return int(text)
    except ValueError:
        pass
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = None
    if isinstance(value, dict) and "entropy" in value:
        return value
    raise argparse.ArgumentTypeError(
        f'expected an int or a seed dict as JSON, e.g. \'{{"entropy": 2026, '
        f'"spawn_key": [3]}}\' (the "seed" of a spec.json); got {text!r}'
    )


def _is_run_target(target: str) -> bool:
    """True for a run folder, ``<study folder>/run_<N>`` or a study folder."""
    if _is_file_ref(target) or ":" in target:
        return False
    path = Path(target)
    if path.is_dir():
        return True
    return bool(re.fullmatch(r"run[_-]?\d+", path.name)) and path.parent.is_dir()


def _run_spec(folder: Path) -> dict:
    """The spec of a run folder (its spec.json, else rebuilt from the finished run)."""
    from .result import SPEC_FILE

    spec_file = folder / SPEC_FILE
    if spec_file.is_file():
        return json.loads(spec_file.read_text(encoding="utf-8"))
    return _replay_spec(folder)[0]


def _script_of_spec(spec: Mapping, args: argparse.Namespace, what: str) -> Any:
    from .runner import load_scenario_ref

    if args.params or args.seed is not None:
        raise UsageError(
            f"--params and --seed do not apply to {what}: the run fixes its parameters "
            "and seed (--set still changes single values)."
        )
    scenario = load_scenario_ref(spec["scenario"])
    _err(f"note: the script replays {what} (its parameters and seed)")
    if spec.get("until") is not None:
        _err(
            f"note: the run stopped early (--until {spec['until']}); the script runs "
            "to the horizon"
        )
    return scenario.to_script(
        spec["params"],
        seed=spec["seed"],
        overrides=_list_or_none(args.set),
        lean=args.lean,
    )


def _planned_spec(target: str, which: str, args: argparse.Namespace) -> dict:
    """Run *which* (an index or run id prefix) of a study file's plan."""
    plan = _study(target, args).plan()
    return _select(list(plan), which, f"The plan of {target}")


def cmd_to_script(args: argparse.Namespace) -> int:
    from .runner import _add_project_root

    target, which = args.target, args.run
    root = None  # the project folder the scenario's modules come from (default: here)
    if target is None:
        if which is None:
            raise UsageError(
                "to-script needs a scenario, study file, run folder or --run RUN_FOLDER."
            )
        target, which = which, None
    if _is_run_target(target):
        folder = _resolve_run_folder(target, which)
        spec = _run_spec(folder)
        root = _rerun_root(folder, spec)
        if root is not None:
            _add_project_root(root)
        index = spec.get("index")
        what = (
            f"run {index} ({spec.get('run_id')})" if index is not None else str(folder)
        )
        script = _script_of_spec(spec, args, what)
    elif which is not None:
        if not _is_study_file(target):
            raise UsageError(
                "--run picks a run of a study file or a study folder; "
                f"{target} is neither."
            )
        spec = _planned_spec(target, which, args)
        script = _script_of_spec(spec, args, f"run {spec['index']} of {target}")
    else:
        scenario = _load_scenario(target)
        params, overrides = _list_or_none(args.params), _list_or_none(args.set)
        if _is_study_file(target):
            params, overrides = _study_params(target, args)
            _err(
                "note: the script runs the study's base presets and overrides, not a "
                f"study run; --run N writes run N of the plan, or set factor values "
                f"with --set{_factor_example(target)}."
            )
        script = scenario.to_script(
            params, seed=args.seed, overrides=overrides, lean=args.lean
        )
    if args.output is None or args.output == "-":
        _write_or_print(script, args.output, "the script")
    else:  # outside the project folder the script puts that folder on sys.path
        path = script.write(args.output, project_root=root)
        _err(f"wrote the script to {path}")
    placeholders = getattr(script, "placeholders", ())
    if placeholders:
        _err(
            f"note: {_plural(len(placeholders), 'placeholder')} for code that could not "
            "be written out (listed in the script's docstring):"
        )
        for what in placeholders:
            _err(f"  - {what}")
    return EXIT_OK


def cmd_form(args: argparse.Namespace) -> int:
    from .form import params_form

    scenario = _load_scenario(args.target)
    params, overrides = _list_or_none(args.params), _list_or_none(args.set)
    note = None
    if _is_study_file(args.target):
        params, overrides = _study_params(args.target, args)
        note = (
            f"This form edits the base preset of the study {args.target}: it starts "
            "from the study's base presets and overrides. The study file sets its "
            "factors (levers, uncertainties) and replications; a downloaded preset "
            "does not change them."
        )
        _err(
            "note: the form starts from the study's base presets and overrides; the "
            "study file sets its factors and replications"
        )
    page = params_form(
        scenario, params=params, overrides=overrides, title=args.title, note=note
    )
    output = args.output or f"{scenario.name}_form.html"
    _write_or_print(page, output, "the form")
    if output != "-":
        _err("open it in a browser; it needs no network access")
    return EXIT_OK


def cmd_new(args: argparse.Namespace) -> int:
    from .templates import create_project, study_file

    try:
        created = create_project(
            args.dir, name=args.name, template=args.template, force=args.force
        )
    except (FileExistsError, ValueError) as exc:
        raise UsageError(str(exc)) from None
    target = Path(args.dir)
    name = args.name if args.name is not None else target.resolve().name
    study = study_file(args.template, name)
    _out(f"Created {len(created)} files in {target}/ (template {args.template!r}):")
    for path in created:
        _out(f"  {path.relative_to(target).as_posix()}")
    _out("")
    _out("Next steps:")
    _out(f"  cd {target}")
    if args.template == "minimal":
        _out("  python model.py                # one run")
    _out(f"  {PROG} validate {study}")
    _out(f"  {PROG} plan {study}")
    _out(f"  {PROG} run {study}")
    _out(
        "  (sequential is fastest for a few short runs; for many runs use --workers "
        "N, which reuses worker processes for short runs; --runs-per-worker 1 gives "
        "every run a fresh process)"
    )
    return EXIT_OK


# --------------------------------------------------------------------------- parser

_DESCRIPTION = """\
Check, plan and run mango scenarios and studies.

Targets are study files, scenario files (.yaml, .yml, .json) or import
references 'module:attr' to a Scenario (or a function returning one). The
working directory is put on sys.path, so run the tool from your project root.
"""

_EPILOG = """\
examples:
  mango-scenario validate studies/my_study.yaml
  mango-scenario plan     studies/my_study.yaml --set agents.n=20
  mango-scenario run      studies/my_study.yaml --workers 16
  mango-scenario run      studies/my_study.yaml --executor slurm --partition short
  mango-scenario run      scenario.yaml --params presets/base.yaml --seed 7
  mango-scenario rerun    results/my_study/run_0037
  mango-scenario status   results/my_study
  mango-scenario new      my_study

exit codes: 0 success, 1 failure, 2 wrong usage, 130 interrupted
"""


def _add_params_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--params",
        "-p",
        action="append",
        metavar="PRESET",
        help="preset file merged into the parameters (repeatable, later wins)",
    )
    parser.add_argument(
        "--set",
        "-s",
        action="append",
        metavar="PATH=VALUE",
        help="override one parameter, e.g. --set agents.n=20; --set -events removes "
        "a key (repeatable)",
    )


def _command(
    sub: Any, name: str, fn: Callable, help: str, description: str, epilog: str
) -> argparse.ArgumentParser:
    parser = sub.add_parser(
        name,
        help=help,
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.set_defaults(handler=fn)
    parser.add_argument(  # also after the command; SUPPRESS keeps a leading --debug
        "--debug",
        action="store_true",
        default=argparse.SUPPRESS,
        help="show tracebacks of errors",
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of ``mango-scenario``.

    Example::

        build_parser().parse_args(["plan", "studies/x.yaml"])
    """
    from mango.scenario._util import _distribution_version

    parser = argparse.ArgumentParser(
        prog=PROG,
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--debug", action="store_true", help="show tracebacks of errors"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s (mango-agents {_distribution_version('mango-agents')})",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = _command(
        sub,
        "validate",
        cmd_validate,
        "check a study, scenario, preset or scenario reference before running it",
        "Check without running. A study file is planned completely: its schema,\n"
        "factor paths and values (the parameters of every run are resolved and\n"
        "validated), the scenario declarations and the importability of the scenario\n"
        "and outcomes. A scenario (file or module:attr) is compiled and planned; a\n"
        "preset is checked against --scenario. Every profile and FromColumn table the\n"
        "parameters name is loaded (file, columns, time column). --build also builds\n"
        "run 0, takes one step and evaluates every recorder once (also in a record:\n"
        "false warm-up), which checks the agent factories, profile columns picked at\n"
        "run time and recorder attributes.",
        "examples:\n  mango-scenario validate studies/my_study.yaml\n"
        "  mango-scenario validate studies/my_study.yaml --build\n"
        "  mango-scenario validate scenario.yaml --params presets/base.yaml\n"
        "  mango-scenario validate my_model:scenario --set agents.n=3\n"
        "  mango-scenario validate presets/base.yaml --scenario my_model:scenario",
    )
    p.add_argument(
        "target", help="study file, scenario file, preset or module:attr reference"
    )
    p.add_argument(
        "--build",
        action="store_true",
        help="also build run 0 and take one step (factories, recorders)",
    )
    p.add_argument(
        "--scenario",
        metavar="REF",
        help="validate FILE as a preset of this scenario (file or module:attr)",
    )
    _add_params_options(p)

    p = _command(
        sub,
        "plan",
        cmd_plan,
        "show what a study or scenario would run",
        "Show the run table of a study (run count, agents per run, links, factors) or\n"
        "the plan of one scenario run (groups, links, recorders, phases, events),\n"
        "without running anything.",
        "examples:\n  mango-scenario plan studies/my_study.yaml\n"
        "  mango-scenario plan scenario.yaml --params presets/base.yaml --set agents.n=20\n"
        "  mango-scenario plan my_model:scenario --json",
    )
    p.add_argument("target", help="study file, scenario file or module:attr")
    _add_params_options(p)
    p.add_argument("--json", action="store_true", help="print JSON")

    p = _command(
        sub,
        "run",
        cmd_run,
        "run a study, or one run of a scenario",
        "Run a study file (finished runs are skipped, stale and failed ones run again),\n"
        "or one run of a scenario, which writes one run folder (replay it with 'rerun').\n"
        "A finished run is stale when the project code (comments and formatting aside)\n"
        "or a data file it used changed since; --keep-stale keeps such runs. The study\n"
        "file's 'executor:' block sets defaults for --workers, --runs-per-worker and\n"
        "the Slurm options.",
        "examples:\n  mango-scenario run studies/my_study.yaml --workers 16\n"
        "  mango-scenario run studies/my_study.yaml --workers 4 "
        "--runs-per-worker 20\n"
        "  mango-scenario run studies/my_study.yaml --executor slurm "
        "--partition short\n"
        "  mango-scenario run studies/sensitivity.yaml --executor slurm --account my_group "
        "--runs-per-task 4 --setup 'module load python/3.11'\n"
        "  mango-scenario run scenario.yaml --params presets/base.yaml --seed 7 "
        "--out results/try\n"
        "  mango-scenario run scenario.yaml --params presets/base.yaml --until 2h  "
        "# quick test",
    )
    p.add_argument("target", help="study file, scenario file or module:attr")
    group = p.add_argument_group("studies")
    group.add_argument(
        "--workers", "-w", type=int, help="worker processes (default: run in-process)"
    )
    group.add_argument(
        "--executor",
        choices=["sequential", "process", "slurm"],
        help="where runs execute (default: sequential, or process with --workers > 1)",
    )
    group.add_argument(
        "--runs-per-worker",
        type=_runs_per_worker,
        metavar="K",
        help="runs a worker process executes before a fresh one replaces it: 'auto' "
        "(default: reuse workers for short runs; starting one costs about 1.5-3 s of "
        "CPU) or a number (1: a fresh process per run)",
    )
    group.add_argument(
        "--require-clean",
        action="store_true",
        help="refuse to start when tracked files of the git work tree have "
        "uncommitted changes",
    )
    group.add_argument(
        "--no-retry-failed",
        action="store_true",
        help="keep failed runs instead of running them again",
    )
    group.add_argument(
        "--keep-stale",
        action="store_true",
        help="keep finished runs whose code or data files changed since (default: "
        "run them again)",
    )
    group.add_argument(
        "--quiet", "-q", action="store_true", help="no progress line per run"
    )
    slurm = p.add_argument_group(
        "slurm (with --executor slurm; replace the study file's executor.slurm)"
    )
    slurm.add_argument("--partition", help="Slurm partition")
    slurm.add_argument("--account", help="Slurm account (--account)")
    slurm.add_argument("--qos", help="Slurm quality of service (--qos)")
    slurm.add_argument("--time", help="time limit per array task, e.g. 01:00:00")
    slurm.add_argument("--mem", help="memory per array task, e.g. 4G")
    slurm.add_argument("--cpus-per-task", type=int, help="CPUs per array task")
    slurm.add_argument(
        "--array-parallelism", type=int, help="maximum array tasks running at once"
    )
    slurm.add_argument(
        "--runs-per-task",
        type=int,
        metavar="K",
        help="runs per array task (default 1); the time limit must cover K runs",
    )
    slurm.add_argument(
        "--max-array-size",
        type=int,
        metavar="N",
        help="most array tasks per job (default 1000, Slurm's MaxArraySize); larger "
        "arrays are split into several jobs",
    )
    slurm.add_argument(
        "--extra-sbatch",
        action="append",
        metavar="OPTION",
        help="further sbatch option, e.g. --extra-sbatch=--constraint=ib (repeatable)",
    )
    slurm.add_argument(
        "--setup",
        action="append",
        metavar="LINE",
        help="shell line run before the runs, e.g. --setup 'module load python/3.11' "
        "(repeatable)",
    )
    slurm.add_argument(
        "--python",
        metavar="PYTHON",
        help="interpreter of the array tasks (default: 'python' when --setup lines "
        "are given, so a venv they activate is used; else the current interpreter)",
    )
    slurm.add_argument(
        "--no-submit",
        action="store_true",
        help="write the batch script without calling sbatch",
    )
    p.add_argument(
        "--set",
        "-s",
        action="append",
        metavar="PATH=VALUE",
        help="override one parameter, e.g. --set agents.n=20; --set -events removes "
        "a key (repeatable; for a study it is added to the study's overrides)",
    )
    single = p.add_argument_group("single runs")
    single.add_argument(
        "--params",
        "-p",
        action="append",
        metavar="PRESET",
        help="preset file merged into the parameters (repeatable, later wins)",
    )
    single.add_argument(
        "--seed", type=int, help="run seed (default: params.seed or fresh)"
    )
    single.add_argument(
        "--until",
        metavar="TIME",
        help="stop early for a quick test, e.g. --until 2h or --until "
        "2024-06-01T06:00; the horizon (and the check of events and phases against "
        "it) stays as it is, later events never fire, the run is marked truncated",
    )
    p.add_argument(
        "--out",
        "-o",
        help="study folder (default: the study's 'out'), or the run folder of a single "
        "run (default: results/<scenario>/<run id>)",
    )
    p.add_argument(
        "--format",
        choices=["auto", "parquet", "csv"],
        help="time series file format (default: parquet when pyarrow is installed)",
    )

    p = _command(
        sub,
        "run-one",
        cmd_run_one,
        "run one spec of a run table (the Slurm array entry point)",
        "Run line N (0-based, blank lines skipped) of a run table such as\n"
        "<out>/slurm/pending.jsonl or <out>/runs.jsonl and write its run folder.\n"
        "A run that is already done and up to date is skipped (a requeued task);\n"
        "a done run whose code or data files changed since (stale) runs again.",
        "examples:\n  mango-scenario run-one results/my_study/slurm/pending.jsonl --line "
        "$SLURM_ARRAY_TASK_ID\n"
        "  mango-scenario run-one results/my_study/runs.jsonl --lines 8:12\n"
        "  mango-scenario run-one results/my_study/runs.jsonl --line 3 --force",
    )
    p.add_argument("file", help="run table file (one JSON run spec per line)")
    lines = p.add_mutually_exclusive_group(required=True)
    lines.add_argument("--line", type=int, help="0-based line index")
    lines.add_argument(
        "--lines",
        metavar="START:STOP",
        help="run lines START to STOP-1 one after another (STOP may lie beyond the "
        "end); used by Slurm tasks with several runs",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="run a run again although it is done and up to date (its old manifest "
        "is kept as manifest.prev.json)",
    )

    p = _command(
        sub,
        "rerun",
        cmd_rerun,
        "replay one finished run and compare checksums",
        "Replay a finished run in this process from its spec.json (parameters, seed,\n"
        "scenario) and compare the checksum of the recorded values: 'identical' or\n"
        "'differs' (exit code 1). The run folder is not changed.",
        "examples:\n  mango-scenario rerun results/my_study/runs/3f2a9c1b0d4e\n"
        "  mango-scenario rerun results/my_study/run_0037     # by run index\n"
        "  mango-scenario rerun results/my_study 137",
    )
    p.add_argument(
        "run",
        help="run folder, or <study folder>/run_<index>, or a study folder with WHICH",
    )
    p.add_argument(
        "which",
        nargs="?",
        help="run index or run id (prefix) inside the study folder (run_<n> is always "
        "an index; a number with a leading zero is read as a run id prefix first)",
    )
    p.add_argument(
        "--certify",
        action="store_true",
        help="when the replay is identical, mark the stale run as current again (the "
        "manifest records it; manifest.prev.json keeps the old one)",
    )

    p = _command(
        sub,
        "certify",
        cmd_certify,
        "mark stale runs of a study folder as current, with a reason",
        "A done run is stale when project code, a data file or the scenario definition\n"
        "changed since it was made (or while it ran), and 'run' repeats it. When the\n"
        "change does not affect the results (a refactoring, an unused helper; 'rerun'\n"
        "replays a run identically), certify the runs instead: their manifests record\n"
        "the current file digests and the reason ('certified'); manifest.prev.json\n"
        "keeps the old manifest.",
        "examples:\n  mango-scenario certify results/my_study --reason 'docstring edits'\n"
        "  mango-scenario certify results/my_study 3 run_7 --reason 'rerun identical'",
    )
    p.add_argument("out", help="the study folder ('out' of the study)")
    p.add_argument(
        "runs",
        nargs="*",
        metavar="RUN",
        help="run indices or run id prefixes (default: every stale run)",
    )
    p.add_argument(
        "--reason",
        required=True,
        help="why the runs are still valid (kept in the manifest)",
    )

    p = _command(
        sub,
        "status",
        cmd_status,
        "count done, failed and pending runs of a study folder",
        "Count the runs of a study folder by state, from the run folders (also while a\n"
        "Slurm job or another process is running the study), and show stale runs (their\n"
        "code or data files changed since), outcome errors, run warnings, CPU time, the\n"
        "git commits of the done runs and how many runs the study file plans with\n"
        "today's code. Rewrites the status.csv snapshot. Exit code 1 when runs failed.",
        "example:\n  mango-scenario status results/my_study",
    )
    p.add_argument("out", help="the study folder ('out' of the study)")
    p.add_argument("--failed", action="store_true", help="list every failed run")
    p.add_argument(
        "--write",
        action="store_true",
        help="(kept for compatibility: status always rewrites status.csv)",
    )

    p = _command(
        sub,
        "schema",
        cmd_schema,
        "print the JSON Schema of a scenario's parameters (or of the file formats)",
        "Print a JSON Schema for editor autocompletion and validation: the parameters\n"
        "of a scenario (for presets), or the scenario file or study file format.",
        "examples:\n  mango-scenario schema my_model:scenario -o presets/schema.json\n"
        "  mango-scenario schema --kind study-file -o studies/schema.json\n"
        "  mango-scenario schema scenario.yaml --kind scenario-file",
    )
    p.add_argument("target", nargs="?", help="scenario file, study file or module:attr")
    p.add_argument(
        "--kind",
        choices=["params", "scenario-file", "study-file"],
        default="params",
        help="which schema (default: params)",
    )
    p.add_argument("--output", "-o", help="write to this file (default: stdout)")

    p = _command(
        sub,
        "new",
        cmd_new,
        "create a project from a template",
        "Create a runnable project. Templates:\n"
        "  minimal  one model.py (agent, parameters, scenario, outcomes), a preset, a\n"
        "           study file and a README; domain-neutral (the default)\n"
        "  full     a sensor-field example meant to be adapted: agents/ and kits/\n"
        "           packages, a scenario file, a CSV profile, calendar time, a warm-up\n"
        "           phase, outcomes, studies/<name>.yaml",
        "examples:\n  mango-scenario new my_study && cd my_study && mango-scenario run "
        "study.yaml\n"
        "  mango-scenario new my_study --template full",
    )
    p.add_argument("dir", help="the project folder (created; must be empty)")
    p.add_argument(
        "--template",
        "-t",
        choices=["minimal", "full"],
        default="minimal",
        help="the project template (default: minimal)",
    )
    p.add_argument(
        "--name", help="project name used in the files (default: folder name)"
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="write into a non-empty folder, overwriting files of the same name",
    )

    p = _command(
        sub,
        "to-script",
        cmd_to_script,
        "write the plain mango script of one scenario run",
        "Write a plain mango script (create_world, registration loops, explicit\n"
        "topology edges, recorders, step loop) that records the same values as one\n"
        "scenario run, for debugging and teaching. The run is a scenario with presets,\n"
        "or a run of a study: a run folder, a study folder with --run, or a study file\n"
        "with --run (run N of its plan). The lean form (the default when the scenario\n"
        "has no kits, phases, events, communication changes, link delay/loss, stop\n"
        "conditions or ctx.spawn/retire/relink calls) leaves out the machinery for\n"
        "those. Code that cannot be written out becomes a marked Placeholder. The\n"
        "script imports the scenario's modules; written outside the project folder\n"
        "(-o archive/run.py) it puts that folder on the import path itself.",
        "examples:\n  mango-scenario to-script my_model:scenario --seed 7 -o plain.py\n"
        "  mango-scenario to-script scenario.yaml -p presets/base.yaml --full\n"
        "  mango-scenario to-script results/my_study/run_0022 -o run22.py\n"
        "  mango-scenario to-script results/my_study --run 22\n"
        "  mango-scenario to-script studies/my_study.yaml --run 22\n"
        "  mango-scenario to-script my_model:scenario --seed "
        '\'{"entropy": 2026, "spawn_key": [22]}\'',
    )
    p.add_argument(
        "target",
        nargs="?",
        help="scenario file, module:attr, study file (its base run, or --run N), run "
        "folder or study folder (with --run)",
    )
    p.add_argument(
        "--run",
        metavar="RUN",
        help="the run of a study file or study folder: an index (22, run_0022) or a "
        "run id prefix; or a run folder when no target is given",
    )
    _add_params_options(p)
    p.add_argument(
        "--seed",
        type=_seed_arg,
        help="run seed: an int or a seed dict as JSON (default: params.seed or fresh)",
    )
    form = p.add_mutually_exclusive_group()
    form.add_argument(
        "--lean",
        dest="lean",
        action="store_const",
        const=True,
        default=None,
        help="require the lean form (fails when the scenario needs the full one)",
    )
    form.add_argument(
        "--full",
        dest="lean",
        action="store_const",
        const=False,
        help="write the full form (phases, events, comm switches, kit scoping) even "
        "when the lean form suffices",
    )
    p.add_argument(
        "--output",
        "-o",
        help="write to this file (default: stdout); outside the project folder the "
        "script adds that folder to the import path, so it runs from anywhere",
    )

    p = _command(
        sub,
        "form",
        cmd_form,
        "write an HTML form that creates presets",
        "Write a self-contained HTML page (no network access needed) with a form for\n"
        "the scenario's parameters; it shows and downloads the YAML preset (by default\n"
        "the values that differ from the scenario's defaults). The model's own\n"
        "parameters come first, the run settings (seed, time, comm, profiles, phases,\n"
        "events) after them. Field(description=..., ge=..., le=...) in the params\n"
        "model become help texts and bounds. The form starts from the defaults, from\n"
        "--params/--set, or from a study file's base presets and overrides.",
        "examples:\n  mango-scenario form my_model:scenario -o my_form.html\n"
        "  mango-scenario form scenario.yaml -p presets/base.yaml\n"
        "  mango-scenario form studies/my_study.yaml",
    )
    p.add_argument("target", help="scenario file, study file or module:attr")
    _add_params_options(p)
    p.add_argument(
        "--output", "-o", help="the HTML file (default: <scenario>_form.html)"
    )
    p.add_argument("--title", help="page title")
    return parser


def _with_cwd_on_path() -> None:
    cwd = os.getcwd()
    if not sys.path or sys.path[0] != cwd:
        sys.path.insert(0, cwd)


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``mango-scenario`` with *argv* (default: ``sys.argv[1:]``).

    :param argv: the arguments
    :return: the exit code: 0 success, 1 failure, 2 wrong usage, 130
        interrupted

    Example::

        main(["validate", "studies/my_study.yaml"])   # 0
    """
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    try:
        args = parser.parse_args(_attach_removals(parser, argv))
    except SystemExit as exc:  # --help, --version and usage errors
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    if args.command is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    _with_cwd_on_path()
    try:
        return args.handler(args)
    except _StdoutClosed:  # e.g. `mango-scenario plan ... | head`
        _silence_stdout()
        return EXIT_OK
    except UsageError as exc:
        if args.debug:
            traceback.print_exc()
        _err(f"{PROG} {args.command}: error: {exc}")
        return EXIT_USAGE
    except KeyboardInterrupt:
        _err("interrupted")
        return EXIT_INTERRUPTED
    except Exception as exc:
        from .errors import ScenarioError

        if args.debug:
            traceback.print_exc()
        _err(f"error: {_error_text(exc)}")
        if not args.debug and not isinstance(exc, ScenarioError):
            _err(f"(run '{PROG} --debug {args.command} ...' for the traceback)")
        return EXIT_FAILURE


_SET_FLAGS = ("--set", "-s")
_REMOVAL = re.compile(r"-[A-Za-z_][A-Za-z0-9_.\-]*(=.*)?")


def _option_strings(parser: argparse.ArgumentParser) -> set[str]:
    """Every option string of *parser* and its subcommands."""
    found = set(parser._option_string_actions)
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                found |= _option_strings(sub)
    return found


def _attach_removals(parser: argparse.ArgumentParser, argv: Sequence[str]) -> list:
    """Let ``--set -path`` remove a key, as ``--set=-path`` does.

    argparse reads a value that starts with ``-`` as the next option and
    stops with "expected one argument"; a removal override (``-events``)
    after ``--set``/``-s`` that is not itself an option is attached to it.
    """
    argv = list(argv)
    if not any(token in _SET_FLAGS for token in argv):
        return argv
    options = _option_strings(parser)
    out: list = []
    index = 0
    while index < len(argv):
        token = argv[index]
        following = argv[index + 1] if index + 1 < len(argv) else None
        if (
            token in _SET_FLAGS
            and isinstance(following, str)
            and _REMOVAL.fullmatch(following)
            and following.split("=", 1)[0] not in options
        ):
            out.append(f"{token}={following}")
            index += 2
            continue
        out.append(token)
        index += 1
    return out


def _silence_stdout() -> None:
    """Send further output to devnull so the interpreter's final flush stays quiet."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):  # no real stdout (e.g. captured)
        pass


def _exception_line(exc: BaseException, notes: Sequence[str]) -> str:
    """``Type: message`` as Python prints it, with its "Did you mean" hint, without notes."""
    try:
        lines = list(
            traceback.TracebackException.from_exception(exc).format_exception_only()
        )
    except Exception:  # pragma: no cover - formatting must never hide the error
        return f"{type(exc).__name__}: {exc}"
    note_lines = {line for note in notes for line in str(note).splitlines()}
    kept = [
        line.rstrip("\n")
        for line in "".join(lines).splitlines(keepends=True)
        if line.rstrip("\n") not in note_lines
    ]
    text = "\n".join(kept).strip() or f"{type(exc).__name__}: {exc}"
    kind = type(exc)
    qualified = f"{kind.__module__}.{kind.__qualname__}"
    if text.startswith(qualified):  # Python names non-builtin types with their module
        text = kind.__name__ + text[len(qualified) :]
    return text


def _error_text(exc: BaseException) -> str:
    from ._util import user_frame
    from .errors import ScenarioError

    notes = getattr(exc, "__notes__", None) or []
    if isinstance(exc, ScenarioError):
        text = str(exc)
    else:
        text = _exception_line(exc, notes)
        where = user_frame(exc)
        if where is not None:
            notes = [*notes, f"at {where}"]
    return "\n".join([text, *(f"  ({note})" for note in notes)])


if __name__ == "__main__":
    sys.exit(main())

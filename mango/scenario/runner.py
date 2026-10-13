"""
Execute study runs: the worker function and the executor policy.

The unit of work is a *run spec*, a plain-data dict (see
:class:`~mango.scenario.StudyPlan`).  :func:`~mango.scenario.run_one` executes one spec in
the current process: it imports the scenario by reference, runs it in a
fresh event loop, computes the outcomes and writes the run folder
(``manifest.json`` last).  It never raises; failures are written to the run
folder (``error.txt`` plus a ``failed`` manifest) and returned as data.

Executors (see :func:`resolve_executor`):

- ``None`` / ``"sequential"`` (with ``workers`` ``None`` or 1): in-process,
  one run after another, each in a fresh event loop;
- ``workers > 1`` or ``"process"``: managed ``spawn`` worker processes,
  at most ``workers`` at a time, each in its own single-process
  ``ProcessPoolExecutor``, so a crashing run cannot take others down.  A
  worker process executes ``runs_per_worker`` runs one after another before
  a fresh one replaces it: by default (``"auto"``) as many as make the
  measured start-up cost of a worker (importing Python, mango and the
  project: about 1.5-3 s of CPU time) small against the run time, so short
  runs share workers and long runs get a fresh process each;
  ``runs_per_worker=1`` gives every run its own process.  While they run,
  ``OMP_NUM_THREADS``, ``OPENBLAS_NUM_THREADS``, ``MKL_NUM_THREADS``,
  ``NUMEXPR_NUM_THREADS`` (``"1"``) and ``PYTHONHASHSEED`` (``"0"``) are set
  for the workers when they are not set already;
- any ``concurrent.futures.Executor`` instance: used as is; the caller owns
  its lifecycle;
- ``"slurm"`` or a :class:`~mango.scenario.SlurmExecutor`: the pending runs
  become a Slurm job array (one task per run, or ``runs_per_task`` runs per
  task; :func:`~mango.scenario.run_one_from_file` per line of
  ``<out>/slurm/pending.jsonl``); the study run returns once it is submitted.

Example::

    from mango.scenario.runner import run_one

    outcome = run_one(json.loads(Path("results/study/runs.jsonl").read_text().splitlines()[3]))
    outcome["status"]        # "done" or "failed"
"""

from __future__ import annotations

import ast
import collections
import concurrent.futures
import concurrent.futures.process
import contextlib
import json
import logging
import math
import multiprocessing
import os
import signal
import sys
import threading
import time as _time
import traceback
import warnings
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._util import (
    git_info,
    import_ref,
    import_ref_or_raise,
    is_positive_int,
    notes_text,
    object_ref,
    provenance,
    read_json,
    set_project_root,
    to_plain,
    using_project_root,
    utc_now,
    write_environment_file,
    write_git_diff,
    write_json_atomic,
)
from .configfiles import CONFIG_SUFFIXES
from .errors import StudyError
from .freshness import (
    _code_files_both,
    data_files,
    files_fingerprint,
    stale_reasons,
)
from .result import (
    ERROR_FILE,
    MANIFEST_FILE,
    OUTCOMES_FILE,
    SPEC_FILE,
    set_run_id,
)
from .runtime import run_sync
from .slurm import SlurmExecutor, SlurmSubmission, active_slurm_job

__all__ = [
    "run_one",
    "run_specs",
    "resolve_executor",
    "ExecutorChoice",
    "load_scenario_ref",
    "resolve_outcomes",
    "compute_outcomes",
    "write_failure",
    "check_refs",
    "worker_environment",
    "WORKER_ENV",
    "run_one_from_file",
    "run_lines_from_file",
    "PREVIOUS_MANIFEST_FILE",
    "SlurmExecutor",
    "SlurmSubmission",
    "active_slurm_job",
    "SPEC_FILE",
    "MANIFEST_FILE",
    "ERROR_FILE",
    "OUTCOMES_FILE",
]

logger = logging.getLogger(__name__)

#: Environment set for managed worker processes (only where unset).
WORKER_ENV: dict[str, str] = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "PYTHONHASHSEED": "0",
}

_REF_HINT = (
    "define the scenario at module level of an importable module or pass 'module:attr'"
)


def _error_line(exc: BaseException) -> str:
    text = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    return text.splitlines()[0] if text else type(exc).__name__


def _error_context(exc: BaseException) -> str:
    """Where an error happened: its notes and the innermost user frame, ``"; "``-joined.

    E.g. ``"while stepping agent 'a_0' in on_step at t=1 s; at model.py:9 in
    on_step"`` (empty when there is nothing to add); the CLI prints it below
    the one-line error, so a failed run points at user code without the
    traceback.
    """
    from ._util import user_frame
    from .errors import ScenarioError

    parts = [str(note) for note in getattr(exc, "__notes__", None) or []]
    if not isinstance(exc, ScenarioError):
        where = user_frame(exc)
        if where is not None:
            parts.append(f"at {where}")
    return "; ".join(parts)


# --------------------------------------------------------------------------- references


def _is_scenario(obj: Any) -> bool:
    return (
        callable(getattr(obj, "run_async", None))
        and getattr(obj, "params_model", None) is not None
        and callable(getattr(obj, "param_defaults", None))
    )


def load_scenario_ref(ref: str) -> Any:
    """Import the scenario an import reference points to, or load a scenario file.

    The reference may name a scenario object or a zero-argument function
    returning one, or be the path of a scenario file (``.yaml``, ``.yml``,
    ``.json``; see :func:`~mango.scenario.load_scenario`), which is looked up
    relative to the working directory, then to the study's project root.

    :param ref: ``"module:attr"`` (or ``"module.attr"``) or a scenario file path
    :return: the scenario
    :raises StudyError: if the reference cannot be imported or does not give
        a scenario, or the scenario file is missing or invalid

    Example::

        scenario = load_scenario_ref("studies.ev:scenario")
        scenario = load_scenario_ref("scenarios/ev.yaml")
    """
    if not isinstance(ref, str) or not ref.strip():
        raise StudyError(f"A scenario reference must be a non-empty str, got {ref!r}.")
    if ref.strip().lower().endswith(CONFIG_SUFFIXES):
        from .errors import ScenarioError
        from .yaml_scenario import load_scenario

        try:
            return load_scenario(ref.strip())
        except ScenarioError as exc:
            raise StudyError(f"Cannot load the scenario file {ref!r}: {exc}") from exc
    try:
        obj = import_ref(ref)
    except (ImportError, AttributeError, ValueError) as exc:
        hint = f"; {_REF_HINT}" if ref.strip().startswith("__main__") else ""
        raise StudyError(f"Cannot load the scenario {ref!r}: {exc}{hint}") from exc
    if _is_scenario(obj):
        return obj
    if callable(obj):
        try:
            made = obj()
        except TypeError as exc:
            raise StudyError(
                f"{ref!r} is a callable but not a zero-argument scenario factory: {exc}"
            ) from exc
        if _is_scenario(made):
            return made
        raise StudyError(
            f"The factory {ref!r} returned {type(made).__name__}, not a Scenario."
        )
    raise StudyError(
        f"{ref!r} is a {type(obj).__name__}, not a Scenario or a zero-argument "
        "function returning one."
    )


def _add_project_root(root: Any) -> None:
    """Make the study's project root importable and the base of relative paths (for good)."""
    if isinstance(root, str) and root and root not in sys.path:
        sys.path.insert(0, root)
    if isinstance(root, str) and root:
        set_project_root(root)


def _is_main_guard(test: ast.expr) -> bool:
    """True for ``__name__ == "__main__"`` (either operand order)."""
    if not (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
    ):
        return False
    sides = [test.left, test.comparators[0]]
    names = [s.id for s in sides if isinstance(s, ast.Name)]
    texts = [s.value for s in sides if isinstance(s, ast.Constant)]
    return names == ["__name__"] and texts == ["__main__"]


def _top_level_names(body: list[ast.stmt]) -> set[str]:
    """Names a module defines when imported (``if __name__ == "__main__":`` skipped)."""
    names: set[str] = set()
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                names.update(
                    item.id for item in ast.walk(target) if isinstance(item, ast.Name)
                )
        elif isinstance(node, ast.Import | ast.ImportFrom):
            names.update(
                (alias.asname or alias.name).partition(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.If):
            if not _is_main_guard(node.test):
                names |= _top_level_names(node.body)
            names |= _top_level_names(node.orelse)
        elif isinstance(node, ast.Try):
            for block in (node.body, node.orelse, node.finalbody):
                names |= _top_level_names(block)
            for handler in node.handlers:
                names |= _top_level_names(handler.body)
        elif isinstance(node, ast.With | ast.For | ast.While):
            names |= _top_level_names(node.body)
    return names


def _check_main_ref(ref: str, what: str) -> None:
    module, _, attr = ref.partition(":") if ":" in ref else ref.rpartition(".")
    if module != "__main__":
        return
    main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    if not main_file:
        raise StudyError(
            f"{what} {ref!r} lives in an interactive session (notebook or REPL), "
            f"which worker processes cannot import; {_REF_HINT}."
        )
    try:
        tree = ast.parse(Path(main_file).read_text(encoding="utf-8"), main_file)
    except (OSError, SyntaxError, ValueError):
        return  # cannot tell; the workers report a failing import
    name = attr.partition(".")[0]
    if name not in _top_level_names(tree.body):
        raise StudyError(
            f"{what} {ref!r} is not defined at the top level of {main_file} (inside "
            "'if __name__ == \"__main__\":' or a function?). Worker processes "
            "import that file without running the main block; move the definition "
            f"above the guard, {_REF_HINT}."
        )


def resolve_outcomes(outcomes: Any) -> dict[str, tuple[Callable | None, str | None]]:
    """Normalize outcome declarations to ``{name: (function, reference)}``.

    Accepts ``None``, a mapping ``{name: function or "module:fn"}`` or a list
    of functions/references (named after the function).  References are
    imported; functions get their reference where one exists.  In a mapping a
    reference may also be ``None`` (an outcome known only by name).

    :param outcomes: the declarations
    :return: ``{name: (callable or None, reference or None)}`` in order
    :raises StudyError: for unnamed lambdas in lists, duplicate names,
        non-callables and references that cannot be imported

    Example::

        resolve_outcomes([kpi.peak_import_kw, "ev_study.kpi:min_voltage_pu"])
        # {"peak_import_kw": (fn, "ev_study.kpi:peak_import_kw"), "min_voltage_pu": (...)}
    """
    if outcomes is None:
        return {}
    if isinstance(outcomes, str | bytes) or callable(outcomes):
        outcomes = [outcomes]
    if isinstance(outcomes, Mapping):
        items = list(outcomes.items())
    elif isinstance(outcomes, Sequence):
        items = []
        for entry in outcomes:
            if isinstance(entry, str):
                name = entry.rpartition(":")[2].rpartition(".")[2]
            else:
                name = getattr(entry, "__name__", None)
                if not callable(entry) or not name or name == "<lambda>":
                    raise StudyError(
                        f"Cannot name the outcome {entry!r}; pass outcomes as a dict "
                        "{name: function} or use named module-level functions."
                    )
            items.append((name, entry))
    else:
        raise StudyError(
            "Outcomes must be a dict {name: function or 'module:fn'} or a list of "
            f"functions/references, got {type(outcomes).__name__}."
        )
    table: dict[str, tuple[Callable | None, str | None]] = {}
    for name, entry in items:
        if not isinstance(name, str) or not name.strip():
            raise StudyError(f"Outcome names must be non-empty str, got {name!r}.")
        if name in table:
            raise StudyError(f"Duplicate outcome name {name!r}.")
        if entry is None:
            table[name] = (None, None)
        elif isinstance(entry, str):
            fn = import_ref_or_raise(entry, f"Cannot load outcome {name!r}", StudyError)
            if not callable(fn):
                raise StudyError(
                    f"Outcome {name!r}: {entry!r} is a {type(fn).__name__}, not a function."
                )
            table[name] = (fn, entry)
        elif callable(entry):
            table[name] = (entry, object_ref(entry))
        else:
            raise StudyError(
                f"Outcome {name!r} must be a function or a 'module:fn' reference, got "
                f"{entry!r}."
            )
    return table


def compute_outcomes(
    result: Any, outcomes: Mapping[str, tuple], *, log: bool = True
) -> tuple[dict, dict]:
    """Evaluate outcome functions on a run result.

    An outcome that raises (or returns something that is not plain data)
    gets the value ``None``; its error is reported instead.

    :param result: the :class:`~mango.scenario.RunResult`
    :param outcomes: ``{name: (function, reference)}`` from :func:`resolve_outcomes`
    :param log: log failing outcomes as warnings: the full error once per
        outcome and error text in a process; outcomes of later runs that
        repeat an earlier error share one short line per run
    :return: ``(values, errors)``: ``{name: value}`` and ``{name: "Error: message"}``

    Example::

        values, errors = compute_outcomes(result, resolve_outcomes([peak_import_kw]))
    """
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    repeated: list[str] = []
    for name, (fn, ref) in outcomes.items():
        try:
            if fn is None:
                raise StudyError(
                    f"outcome {name!r} has no function (reference {ref!r})"
                )
            values[name] = to_plain(fn(result))
        except Exception as exc:
            values[name] = None
            errors[name] = _error_line(exc)
            if log:
                key = (name, errors[name])
                if key in _LOGGED_OUTCOME_ERRORS:
                    repeated.append(name)
                else:
                    if len(_LOGGED_OUTCOME_ERRORS) < 1000:
                        _LOGGED_OUTCOME_ERRORS.add(key)
                    logger.warning(
                        "Outcome %r of run %s failed: %s", name, result.run_id, exc
                    )
    if repeated:
        logger.warning(
            "%s of run %s failed with the same %s as before",
            _outcome_list(repeated),
            result.run_id,
            "error" if len(repeated) == 1 else "errors",
        )
    return values, errors


def _outcome_list(names: list[str]) -> str:
    """``"Outcome 'a'"`` or ``"Outcomes 'a', 'b'"`` for a log line."""
    if len(names) == 1:
        return f"Outcome {names[0]!r}"
    return "Outcomes " + ", ".join(repr(n) for n in names)


#: ``(outcome, error text)`` already logged in full by this process
_LOGGED_OUTCOME_ERRORS: set[tuple[str, str]] = set()


# --------------------------------------------------------------------------- worker


#: The previous manifest of a run that is executed again (``run-one --force``,
#: stale runs of a resumed study).
PREVIOUS_MANIFEST_FILE = "manifest.prev.json"


def _spec_manifest(spec: Mapping) -> dict:
    return {
        "run_id": spec.get("run_id"),
        "index": spec.get("index"),
        "study": spec.get("study"),
        "replication": spec.get("replication"),
        "scenario": spec.get("scenario"),
        "scenario_fingerprint": spec.get("scenario_fingerprint"),
        "seed": spec.get("seed"),
        "factors": spec.get("factors", {}),
        "labels": spec.get("labels", {}),
        "design": spec.get("design", {}),
        "project_root": spec.get("project_root"),
        "git": spec.get("git"),
        "planned_git": spec.get("git"),
        "pythonhashseed": os.environ.get("PYTHONHASHSEED"),
        # a quick test run stopped early (mango-scenario run --until)
        **({"until": spec["until"]} if spec.get("until") is not None else {}),
    }


_EXECUTION_GIT: dict[str, Any] = {}
_EXECUTION_GIT_LOCK = threading.Lock()


def _execution_git(root: Any) -> dict | None:
    """The git state of *root* when this process first imported the project.

    A process imports the project's modules once, so the git state at its
    first run describes the code every later run of the process executes.
    """
    if not isinstance(root, str) or not root:
        return None
    with _EXECUTION_GIT_LOCK:
        if root not in _EXECUTION_GIT:
            _EXECUTION_GIT[root] = git_info(root)
        return _EXECUTION_GIT[root]


def _study_folder(spec: Mapping) -> Path | None:
    out = spec.get("out")
    if not out:
        return None
    folder = Path(out)
    return folder.parent.parent if spec.get("study") is not None else folder.parent


def _scenario_file(scenario: Any) -> list[Path]:
    path = getattr(scenario, "path", None)
    if isinstance(path, Path) and str(path).lower().endswith(CONFIG_SUFFIXES):
        return [path]
    return []


def _digests(
    spec: Mapping,
    scenario: Any,
    outcomes: Mapping[str, tuple],
    *,
    fresh: bool,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """``(code on disk now, code as imported, data files)`` of a run: ``{file: digest}`` each."""
    root = spec.get("project_root")
    root = root if isinstance(root, str) and root else None
    now, imported = _code_files_both(
        root,
        [scenario, *(fn for fn, _ in outcomes.values())],
        fresh,
        _scenario_file(scenario),
        [folder for folder in [_study_folder(spec)] if folder is not None],
    )
    return now, imported, data_files(spec.get("params") or {}, root)


def _fingerprints(
    before: tuple[dict, dict] | None,
    spec: Mapping,
    scenario: Any,
    outcomes: Mapping[str, tuple],
    *,
    fresh: bool,
) -> dict:
    """The ``code_files``/``data_files`` manifest entries and their fingerprints.

    *before* holds the digests taken when the run started.  A file records
    the digest of what the run used: a module as this process imported it,
    a scenario or data file as it was at the start (files first seen at the
    end, e.g. modules imported lazily, as they are then).  Files whose
    content on disk at the end differs are listed as ``changed_during_run``,
    so such a run counts as stale.
    """
    code_now, code_used, data_now = _digests(spec, scenario, outcomes, fresh=fresh)
    code_before, data_before = before if before is not None else ({}, {})
    code = dict(sorted({**code_used, **code_before}.items()))
    data = dict(sorted({**data_now, **data_before}.items()))
    changed = sorted(
        key
        for recorded, now in ((code, code_now), (data, data_now))
        for key, digest in recorded.items()
        if key in now and now[key] != digest
    )
    entries = {
        "code_fingerprint": files_fingerprint(code),
        "data_fingerprint": files_fingerprint(data),
        "code_files": code,
        "data_files": data,
    }
    if changed:
        entries["changed_during_run"] = changed
    return entries


@contextlib.contextmanager
def _run_warnings() -> Iterator[list]:
    """Record the UserWarnings a run issues (each run anew), to store them with the run.

    Only in the main thread (``warnings.catch_warnings`` is process-wide);
    elsewhere the list stays empty.  The caller issues the recorded
    warnings again afterwards, so they still reach the console and the
    caller's filters.
    """
    if threading.current_thread() is not threading.main_thread():
        yield []
        return
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", UserWarning)
        yield caught


def _reissue(caught: Sequence[Any]) -> None:
    """Issue recorded warnings again (outside the recording context)."""
    for item in caught:
        warnings.warn_explicit(
            item.message, item.category, item.filename, item.lineno, source=item.source
        )


def _warning_texts(caught: Sequence[Any], result: Any = None) -> list[str]:
    """The distinct UserWarning texts of a run, in order (also those the result kept)."""
    texts: list[str] = []
    kept = getattr(result, "warnings", None) if result is not None else None
    for item in list(kept or ()):
        text = str(getattr(item, "message", item))
        if text not in texts:
            texts.append(text)
    for item in caught:
        if issubclass(item.category, UserWarning):
            text = str(item.message)
            if text not in texts:
                texts.append(text)
    return texts


def _cpu_clock() -> Callable[[], float]:
    """CPU time of this run: the CPU time of the thread that executes it.

    Not the process's: helper threads of numerical libraries (BLAS) start
    on the first import and may spin while idle, which made the first run
    of a process report several times its wall time.
    """
    return _time.thread_time


def write_failure(
    spec: Mapping,
    error: str,
    traceback_text: str,
    *,
    started: str | None = None,
    wall_s: float | None = None,
    cpu_s: float | None = None,
    warnings: Sequence[str] = (),
) -> Path:
    """Write ``error.txt`` and a ``failed`` manifest into the run folder.

    :param spec: the run spec
    :param error: the one-line error (``"ValueError: boom"``)
    :param traceback_text: the full traceback for ``error.txt``
    :param started: UTC ISO start time
    :param wall_s: wall time until the failure
    :param cpu_s: CPU time until the failure
    :param warnings: the warnings the run issued before it failed
    :return: the run folder

    Example::

        write_failure(spec, "BrokenProcessPool: worker died", traceback.format_exc())
    """
    folder = Path(spec["out"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ERROR_FILE).write_text(traceback_text, encoding="utf-8")
    manifest = _spec_manifest(spec)
    manifest.update(
        status="failed",
        error=error,
        started=started,
        finished=utc_now(),
        wall_s=wall_s,
        cpu_s=cpu_s,
        outcome_errors={},
        provenance=provenance(spec.get("scenario")),
    )
    if warnings:
        manifest["warnings"] = list(warnings)
    write_json_atomic(folder / MANIFEST_FILE, manifest)
    return folder


def _prepare_folder(
    folder: Path, spec: Mapping, *, keep_previous: bool = False
) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    manifest = folder / MANIFEST_FILE
    if keep_previous and manifest.exists():
        manifest.replace(folder / PREVIOUS_MANIFEST_FILE)
    manifest.unlink(missing_ok=True)
    (folder / ERROR_FILE).unlink(missing_ok=True)
    (folder / SPEC_FILE).write_text(
        json.dumps(dict(spec), indent=2, default=str), encoding="utf-8"
    )


def _check_fingerprint(scenario: Any, spec: Mapping, *, replay: bool = False) -> None:
    """Fail when the imported scenario is not the one the study planned with.

    With *replay* (``mango-scenario rerun``) the message says that the
    definition changed since the run was made.
    """
    expected = spec.get("scenario_fingerprint")
    if expected is None or not callable(getattr(scenario, "fingerprint", None)):
        return
    current = scenario.fingerprint()
    if current != expected:
        ref = spec.get("scenario")
        if replay:
            raise StudyError(
                f"the scenario {ref!r} differs from the definition run "
                f"{spec.get('run_id')} was made with (fingerprint {expected} != "
                f"{current}): its groups, links, recorders, factories or file content "
                "changed since, so the replay will probably differ."
            )
        if str(ref).lower().endswith(CONFIG_SUFFIXES):
            raise StudyError(
                f"The scenario file {ref!r} differs from the one the study was planned "
                "with (the file, a file it extends or a referenced module changed "
                "since); plan and run the study again, or use a new 'out' folder."
            )
        module = str(ref).partition(":")[0]
        raise StudyError(
            f"The scenario {ref!r} imported in this worker process differs from the "
            "one the study was planned with (its groups, links, recorders or "
            "factories were changed after the module was imported, e.g. by the "
            f"script that runs the study). Worker processes import {module!r} anew; "
            "make all declarations in that module, or run the study sequentially "
            "(workers=1)."
        )


def _adopt_identity(result: Any, spec: Mapping) -> None:
    """Give the result the study's run id and scenario reference."""
    run_id = spec["run_id"]
    if result.run_id != run_id:
        set_run_id(result, run_id)
    result.scenario = spec.get("scenario", result.scenario)


def _outcome(
    spec: Mapping,
    status: str,
    *,
    wall_s: float | None = None,
    outcomes: dict | None = None,
    error: str | None = None,
    outcome_errors: dict | None = None,
    cpu_s: float | None = None,
    load_s: float | None = None,
    warnings: Sequence[str] = (),
    changed_during_run: Sequence[str] = (),
) -> dict:
    """The outcome record of a run, as executors report it."""
    known = isinstance(spec, Mapping)
    return {
        "run_id": spec.get("run_id") if known else None,
        "index": spec.get("index") if known else None,
        "status": status,
        "wall_s": wall_s,
        "cpu_s": cpu_s,
        "load_s": load_s,
        "outcomes": {} if outcomes is None else outcomes,
        "error": error,
        "outcome_errors": {} if outcome_errors is None else outcome_errors,
        "warnings": list(warnings),
        "changed_during_run": list(changed_during_run),
    }


def _execute(
    spec: Mapping,
    scenario: Any = None,
    outcomes: Mapping[str, tuple] | None = None,
    *,
    keep_previous: bool = False,
) -> dict:
    """Run one spec in this process (with live objects when given); never raises Exceptions.

    A scenario imported here (not given) is recorded with the git state of
    this process's first import; a live scenario with the spec's (planning)
    git state, which is when the planning process imported it.

    Timing: ``wall_s`` and ``cpu_s`` cover the whole call, ``load_s`` the
    import of the scenario and outcomes (the cold import of the project in
    a fresh worker process), so ``wall_s - load_s`` is the run itself.
    """
    started = utc_now()
    t0 = _time.perf_counter()
    cpu = _cpu_clock()
    c0 = cpu()
    load_s = 0.0
    run_id = spec.get("run_id") if isinstance(spec, Mapping) else None
    fresh = scenario is None
    caught: list = []
    try:
        folder = Path(spec["out"])
        _prepare_folder(folder, spec, keep_previous=keep_previous)
        root = spec.get("project_root")
        with using_project_root(root if isinstance(root, str) else None):
            if scenario is None:
                scenario = load_scenario_ref(spec["scenario"])
                _check_fingerprint(scenario, spec)
            if outcomes is None:
                outcomes = resolve_outcomes(spec.get("outcomes") or {})
            load_s = _time.perf_counter() - t0
            # the git state (and diff) of the code this run executes: taken
            # before the run, so an edit during the run does not show in it
            git_state = _run_git(spec, root, fresh=fresh)
            _, code_start, data_start = _digests(spec, scenario, outcomes, fresh=fresh)
            before = (code_start, data_start)
            with _run_warnings() as caught:
                result = run_sync(
                    lambda: scenario.run_async(
                        spec["params"], seed=spec["seed"], until=spec.get("until")
                    )
                )
                _adopt_identity(result, spec)
                values, errors = compute_outcomes(result, outcomes)
            _reissue(caught)
            fingerprints = _fingerprints(before, spec, scenario, outcomes, fresh=fresh)
        texts = _warning_texts(caught, result)
        kept = getattr(result, "warnings", None)
        if isinstance(kept, list):
            kept.extend(text for text in texts if text not in kept)
        wall_s = _time.perf_counter() - t0
        cpu_s = cpu() - c0
        manifest = _spec_manifest(spec)
        manifest.update(
            started=started,
            finished=utc_now(),
            wall_s=wall_s,
            cpu_s=cpu_s,
            load_s=load_s,
            outcome_errors=errors,
            provenance=provenance(spec.get("scenario")),
            **fingerprints,
        )
        if texts:
            manifest["warnings"] = texts
        manifest["git"] = git_state
        _record_environment_file(spec)
        result.outcomes = values
        result.manifest = manifest
        result.save(folder, format=spec.get("format") or "auto")
        return _outcome(
            spec,
            "done",
            wall_s=wall_s,
            cpu_s=cpu_s,
            load_s=load_s,
            outcomes=values,
            outcome_errors=errors,
            warnings=texts,
            changed_during_run=fingerprints.get("changed_during_run", ()),
        )
    except Exception as exc:
        error = _error_line(exc)
        wall_s = _time.perf_counter() - t0
        cpu_s = cpu() - c0
        texts = _warning_texts(caught)
        try:
            write_failure(
                spec,
                error,
                traceback.format_exc() + notes_text(exc),
                started=started,
                wall_s=wall_s,
                cpu_s=cpu_s,
                warnings=texts,
            )
        except Exception:
            logger.exception("Could not record the failure of run %s", run_id)
        failed = _outcome(
            spec,
            "failed",
            wall_s=wall_s,
            cpu_s=cpu_s,
            load_s=load_s,
            error=error,
            warnings=texts,
        )
        context = _error_context(exc)
        if context:
            failed["error_context"] = context
        return failed


def _record_environment_file(spec: Mapping) -> None:
    """Write the environment's package list into the study folder."""
    folder = _study_folder(spec)
    if folder is None or spec.get("study") is None:
        return
    with contextlib.suppress(Exception):  # provenance extras never fail a run
        write_environment_file(folder)


def _run_git(spec: Mapping, root: Any, *, fresh: bool) -> dict | None:
    """The git state of the executed code, with a dirty tree's stored diff (studies).

    A scenario imported here (*fresh*) runs the code of this process's first
    import; a live one the code imported when the study was planned.
    """
    git = _execution_git(root) if fresh else spec.get("git")
    folder = _study_folder(spec)
    if folder is None or spec.get("study") is None:
        return git
    if isinstance(git, dict) and git.get("dirty"):
        with contextlib.suppress(Exception):  # provenance extras never fail a run
            diff = _execution_diff(root, folder, git)
            if diff is not None:
                return {**git, "diff": diff}
    return git


_EXECUTION_DIFF: dict[tuple, str | None] = {}


def _execution_diff(root: Any, folder: Path, git: Mapping) -> str | None:
    """``git diff HEAD`` of a dirty tree, stored once per process and study folder."""
    if not isinstance(root, str) or not root:
        return None
    key = (root, str(folder), git.get("commit"))
    with _EXECUTION_GIT_LOCK:
        if key not in _EXECUTION_DIFF:
            _EXECUTION_DIFF[key] = write_git_diff(root, folder, git)
        return _EXECUTION_DIFF[key]


def run_one(spec: Mapping) -> dict:
    """Execute one run spec in this process and write its run folder.

    Prepends ``spec["project_root"]`` to ``sys.path``, imports the scenario
    and the outcome functions by reference, runs the scenario with the
    spec's params and seed in a fresh event loop, computes the outcomes and
    writes ``<out>/spec.json``, the time series, ``config.resolved.yaml``,
    ``outcomes.json`` and, last, ``manifest.json``.  An outcome that raises
    is ``null`` and listed under ``outcome_errors`` in the manifest.  A
    failing run writes ``error.txt`` and a ``failed`` manifest.

    The function is top-level and picklable, so any executor can run it.

    :param spec: the run spec (plain data, e.g. one line of ``runs.jsonl``)
    :return: ``{"run_id", "index", "status", "wall_s", "cpu_s", "load_s",
        "outcomes", "error", "outcome_errors", "warnings",
        "changed_during_run"}``: ``cpu_s`` is the CPU time of the run,
        ``load_s`` the part of ``wall_s`` spent importing the scenario and
        outcomes, ``warnings`` the distinct UserWarnings the run issued (also
        in ``manifest.json``), ``changed_during_run`` the code and data files
        whose content on disk at the end differs from what the run used (an
        edit while it, or the study, ran; such a run is stale);
        it never raises for errors of the run itself

    Example::

        with ProcessPoolExecutor(4) as pool:
            for outcome in pool.map(run_one, plan):
                print(outcome["run_id"], outcome["status"])
    """
    return _execute(spec)


# --------------------------------------------------------------------------- executors


@dataclass(frozen=True)
class ExecutorChoice:
    """The resolved executor policy of a study run.

    :param kind: ``"sequential"``, ``"process"`` (managed pool),
        ``"external"`` (a caller-owned executor) or ``"slurm"`` (a
        :class:`~mango.scenario.SlurmExecutor`)
    :param workers: worker processes of a managed pool (1 otherwise)
    :param executor: the caller's executor for ``"external"``, the
        :class:`~mango.scenario.SlurmExecutor` for ``"slurm"``
    :param runs_per_worker: runs each worker process of a managed pool
        executes before it is replaced by a fresh one (1: a fresh process
        per run; ``"auto"``: chosen from the measured cost of starting a
        worker and the run time, see :data:`AUTO`)
    :param usage: filled while a managed pool runs: ``{"runs_per_worker":
        largest number of runs one worker executed, "spawn_s": median start-up
        cost of a worker, "run_s": median run time without the start-up}``
    """

    kind: str
    workers: int = 1
    executor: Any = None
    runs_per_worker: int | str = 1
    usage: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def in_process(self) -> bool:
        """True when runs execute in this process (live objects allowed)."""
        return self.kind == "sequential" or isinstance(
            self.executor, concurrent.futures.ThreadPoolExecutor
        )

    def describe(self) -> str:
        """Human-readable form, e.g. ``"process pool (4 workers)"``."""
        if self.kind == "sequential":
            return "sequential"
        if self.kind == "process":
            reuse = (
                f", {self.runs_per_worker} runs per worker"
                if self.runs_per_worker != AUTO and self.runs_per_worker > 1
                else ""
            )
            text = f"process pool ({self.workers} worker{'s' * (self.workers != 1)}{reuse})"
            used = self.usage.get("runs_per_worker") or 1
            if self.runs_per_worker == AUTO and used > 1:
                text += f", workers reused for up to {used} runs each (auto)"
            return text
        if self.kind == "slurm":
            return self.executor.describe()
        return f"{type(self.executor).__name__} (caller-owned)"


#: ``runs_per_worker="auto"`` (the default of the managed pool): every worker
#: process runs as many runs as it takes to make its start-up cost (import of
#: Python, mango and the project, measured per study) small against the run
#: time: K = ceil(10 * start-up / run time), between 1 and :data:`AUTO_MAX_RUNS`.
#: Runs that take longer than about ten start-ups get a fresh process each.
AUTO = "auto"
#: Upper bound of the worker reuse ``runs_per_worker="auto"`` chooses.
AUTO_MAX_RUNS = 200
#: ``runs_per_worker="auto"`` aims at K * run time = this factor * start-up cost.
AUTO_SPAWN_FACTOR = 10


def _check_workers(workers: Any) -> int | None:
    if workers is None:
        return None
    if not is_positive_int(workers):
        raise StudyError(f"workers must be an int >= 1 or None, got {workers!r}.")
    return workers


def resolve_executor(
    executor: Any = None, workers: Any = None, runs_per_worker: Any = None
) -> ExecutorChoice:
    """Resolve the ``executor``/``workers``/``runs_per_worker`` arguments of :meth:`~mango.scenario.Study.run`.

    :param executor: ``None``, ``"sequential"``, ``"process"``, ``"slurm"``
        (a default :class:`~mango.scenario.SlurmExecutor`), a
        :class:`~mango.scenario.SlurmExecutor` or a
        ``concurrent.futures.Executor`` instance
    :param workers: ``None`` or an int >= 1
    :param runs_per_worker: ``None``, ``"auto"`` or an int >= 1: runs a
        worker process of the managed pool executes before a fresh one
        replaces it (only for the managed process pool).  ``None`` means
        ``"auto"`` (see :data:`AUTO`): short runs reuse workers, long runs
        get a fresh process each; ``1`` gives every run its own process
        (the strictest isolation)
    :return: the :class:`ExecutorChoice`
    :raises StudyError: for unknown executors and contradicting arguments

    Example::

        resolve_executor(workers=4).describe()      # "process pool (4 workers)"
        resolve_executor(workers=4).runs_per_worker # "auto"
        resolve_executor(workers=4, runs_per_worker=20).describe()
        # "process pool (4 workers, 20 runs per worker)"
        resolve_executor().describe()               # "sequential"
    """
    if (
        runs_per_worker is not None
        and runs_per_worker != AUTO
        and not is_positive_int(runs_per_worker)
    ):
        raise StudyError(
            "runs_per_worker must be an int >= 1, 'auto' or None, got "
            f"{runs_per_worker!r}."
        )
    choice = _resolve_executor(executor, workers)
    if runs_per_worker is None:
        return choice
    if choice.kind != "process" and runs_per_worker in (1, AUTO):
        return choice  # nothing to reuse: a no-op elsewhere
    if choice.kind != "process":
        raise StudyError(
            f"runs_per_worker={runs_per_worker} applies to the managed process pool "
            f"(workers=N or executor='process'), not to {choice.describe()}"
            + (
                "; a Slurm array task runs several runs with "
                "SlurmExecutor(runs_per_task=K)."
                if choice.kind == "slurm"
                else "."
            )
        )
    return ExecutorChoice("process", choice.workers, runs_per_worker=runs_per_worker)


def _resolve_executor(executor: Any, workers: Any) -> ExecutorChoice:
    workers = _check_workers(workers)
    if isinstance(executor, SlurmExecutor) or executor == "slurm":
        if workers is not None:
            raise StudyError(
                f"workers={workers} cannot be combined with Slurm; every run is its own "
                "array task (limit concurrent tasks with "
                "SlurmExecutor(array_parallelism=N))."
            )
        return ExecutorChoice(
            "slurm",
            1,
            executor if isinstance(executor, SlurmExecutor) else SlurmExecutor(),
        )
    if isinstance(executor, concurrent.futures.Executor):
        if workers is not None:
            raise StudyError(
                f"workers={workers} cannot be combined with an executor instance; the "
                "executor decides its own parallelism."
            )
        return ExecutorChoice("external", 1, executor)
    if executor is None:
        if workers is None or workers == 1:
            return ExecutorChoice("sequential")
        return ExecutorChoice("process", workers, runs_per_worker=AUTO)
    if executor == "sequential":
        if workers not in (None, 1):
            raise StudyError(
                f"executor='sequential' runs one run at a time; drop workers={workers} "
                "or use executor='process'."
            )
        return ExecutorChoice("sequential")
    if executor == "process":
        return ExecutorChoice(
            "process", workers or os.cpu_count() or 1, runs_per_worker=AUTO
        )
    raise StudyError(
        f"Unknown executor {executor!r}; use None or 'sequential', 'process' (with "
        "workers=N), 'slurm', a SlurmExecutor or a concurrent.futures.Executor "
        "instance."
    )


def check_refs(scenario_ref: str | None, outcomes: Mapping[str, tuple]) -> None:
    """Check that worker processes can import the scenario and the outcomes.

    :raises StudyError: explaining how to make them importable
    """
    if scenario_ref is None:
        raise StudyError(
            "Process-based executors import the scenario in each worker, but this "
            f"scenario has no import reference (built in a function or a lambda?); "
            f"{_REF_HINT}, or run with executor='sequential'."
        )
    _check_main_ref(scenario_ref, "The scenario")
    missing = [name for name, (_, ref) in outcomes.items() if ref is None]
    if missing:
        raise StudyError(
            f"Process-based executors import outcome functions by reference, but "
            f"outcome(s) {missing} have none (lambdas or local functions); define them "
            "at module level of an importable module or pass 'module:fn', or run with "
            "executor='sequential'."
        )
    for name, (_, ref) in outcomes.items():
        _check_main_ref(ref, f"Outcome {name!r}")


@contextlib.contextmanager
def worker_environment() -> Iterator[dict[str, str]]:
    """Set :data:`WORKER_ENV` in ``os.environ`` where unset; restore on exit.

    Spawned worker processes inherit the variables; the parent's environment
    is unchanged afterwards.

    :return: (as context value) the variables that were set

    Example::

        with worker_environment():
            pool = ProcessPoolExecutor(4, mp_context=get_context("spawn"))
    """
    added = {key: value for key, value in WORKER_ENV.items() if key not in os.environ}
    os.environ.update(added)
    try:
        yield added
    finally:
        for key in added:
            os.environ.pop(key, None)


def _spawn_pool() -> concurrent.futures.ProcessPoolExecutor:
    """A pool with one fresh ``spawn`` worker process (used for ``runs_per_worker`` runs)."""
    return concurrent.futures.ProcessPoolExecutor(
        1, mp_context=multiprocessing.get_context("spawn")
    )


@contextlib.contextmanager
def _children_ignore_sigint() -> Iterator[None]:
    """Start processes inside the block with SIGINT ignored (POSIX, main thread).

    Ctrl+C in a terminal signals the whole process group; workers that
    inherit the ignored SIGINT keep running, so the parent alone decides
    (finish and commit, or terminate them on a second Ctrl+C).  Python keeps
    an inherited ignored SIGINT instead of installing KeyboardInterrupt.
    """
    if os.name != "posix" or threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def _committed(spec: Mapping) -> dict | None:
    """The outcome of a run whose folder already holds its ``done`` manifest."""
    out = spec.get("out")
    if not out:
        return None
    folder = Path(out)
    manifest = read_json(folder / MANIFEST_FILE)
    if (
        not isinstance(manifest, dict)
        or manifest.get("run_id") != spec.get("run_id")
        or manifest.get("status") != "done"
    ):
        return None
    outcomes = read_json(folder / OUTCOMES_FILE)
    errors = manifest.get("outcome_errors")
    return _outcome(
        spec,
        "done",
        wall_s=manifest.get("wall_s"),
        outcomes=outcomes if isinstance(outcomes, dict) else {},
        outcome_errors=errors if isinstance(errors, dict) else {},
    )


def _started(spec: Mapping) -> bool:
    """True when a worker began the run (``run_one`` writes ``spec.json`` first)."""
    out = spec.get("out")
    return bool(out) and (Path(out) / SPEC_FILE).exists()


def _not_started(spec: Mapping, reason: str) -> dict:
    """The outcome of a run that never began: nothing is written, it stays pending."""
    return _outcome(spec, "pending", error=reason)


def _record_failure(spec: Mapping, error: str, text: str) -> dict:
    """Write a failed run folder unless the run committed meanwhile; return the outcome."""
    committed = _committed(spec)
    if committed is not None:  # finished before its result could be delivered
        return committed
    try:
        write_failure(spec, error, text)
    except Exception:
        logger.exception("Could not record the failure of run %s", spec.get("run_id"))
    return _outcome(spec, "failed", error=error)


def _executor_failure(spec: Mapping, exc: BaseException) -> dict:
    """Report an executor error for *spec* (never overwriting a committed run)."""
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return _record_failure(spec, _error_line(exc), text)


_DIED = (
    "the worker process died while running this run (a crash in native code, "
    "running out of memory, os._exit or a kill signal): {error}"
)
_NOT_STARTED = (
    "not started: the worker process exited before the run began ({error}); "
    "workers die before running anything when the script starts the study without "
    "an 'if __name__ == \"__main__\":' guard or cannot import the project. The run "
    "stays pending."
)
# a caller-owned pool shares its workers: one dying worker breaks every run in it
_SHARED_DIED = (
    "a worker process of the executor died while this run was in flight ({error}); "
    "the crash may come from another run, because a broken process pool fails all "
    "its runs (workers=N gives every run its own process)"
)
_SHARED_NOT_STARTED = (
    "not started: the executor broke before the run began ({error}); a worker "
    "process died, possibly while running another run. The run stays pending; run "
    "the study again, or use workers=N, which gives every run its own process."
)


def _lost_run(spec: Mapping, exc: BaseException, *, shared: bool = False) -> dict:
    """Report a run whose worker process died (``BrokenExecutor``).

    A run that began is recorded as failed, one that committed counts as
    done and one that never began stays pending.  *shared* is true for a
    caller-owned executor, whose workers serve several runs, so the dead
    worker may have been running another run.
    """
    if _started(spec):
        error = (_SHARED_DIED if shared else _DIED).format(error=_error_line(exc))
        return _record_failure(spec, error, error + "\n")
    committed = _committed(spec)
    if committed is not None:
        return committed
    reason = _SHARED_NOT_STARTED if shared else _NOT_STARTED
    return _not_started(spec, reason.format(error=_error_line(exc)))


def _outcome_of(
    future: concurrent.futures.Future, spec: Mapping, *, shared: bool = False
) -> dict:
    """The run outcome a finished future stands for (``BaseException`` propagates)."""
    try:
        outcome = future.result()
        if not isinstance(outcome, Mapping) or "status" not in outcome:
            raise StudyError(
                f"The executor returned {outcome!r} instead of a run outcome."
            )
    except concurrent.futures.BrokenExecutor as exc:
        return _lost_run(spec, exc, shared=shared)
    except Exception as exc:
        return _executor_failure(spec, exc)
    return dict(outcome)


def _drain(
    executor: concurrent.futures.Executor,
    specs: Sequence[Mapping],
    task: Callable,
    report: Callable[[dict, Mapping], None],
) -> None:
    """Run *specs* on a caller-owned *executor* and report each outcome."""
    futures: dict[concurrent.futures.Future, Mapping] = {}
    try:
        for spec in specs:
            try:
                futures[executor.submit(task, spec)] = spec
            except concurrent.futures.BrokenExecutor as exc:
                report(_lost_run(spec, exc, shared=True), spec)
        for future in concurrent.futures.as_completed(futures):
            spec = futures[future]
            report(_outcome_of(future, spec, shared=True), spec)
    except BaseException:
        for future in futures:
            future.cancel()
        raise


def _terminate(pool: concurrent.futures.ProcessPoolExecutor) -> None:
    terminate = getattr(pool, "terminate_workers", None)  # Python >= 3.14
    if callable(terminate):
        with contextlib.suppress(Exception):
            terminate()
        return
    for process in list((getattr(pool, "_processes", None) or {}).values()):
        with contextlib.suppress(Exception):
            process.terminate()
    pool.shutdown(wait=False, cancel_futures=True)


def _stop_pools(running: Mapping[concurrent.futures.Future, tuple]) -> None:
    """After an interrupt: let running runs finish and commit; a second one kills them."""
    pools = [slot.pool for _, slot in running.values()]
    if not pools:
        return
    print(
        f"Interrupted: waiting for {len(pools)} running run"
        f"{'s' * (len(pools) != 1)} to finish and commit (press Ctrl+C again to "
        "abort them).",
        file=sys.stderr,
        flush=True,
    )
    try:
        for pool in pools:
            pool.shutdown(wait=True, cancel_futures=True)
    except BaseException:
        for pool in pools:
            _terminate(pool)
        raise


@dataclass
class _Slot:
    """One worker process of the managed pool and the runs it executed."""

    pool: concurrent.futures.ProcessPoolExecutor
    runs: int = 0


def _median(values: Sequence[float]) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


class _Reuse:
    """How many runs a worker process executes; measures start-up and run times.

    The start-up cost of a worker is what its first run took in the parent
    (submit to result) minus a typical run (the median of ``wall_s -
    load_s`` measured in the workers), so imports that happen lazily during
    a worker's first run count as start-up, too.
    """

    def __init__(self, setting: int | str, usage: dict):
        self.setting = setting
        self.usage = usage
        self.first: list[float] = []
        self.run: list[float] = []

    def observe(self, outcome: Mapping, latency: float, first: bool) -> None:
        wall, load = outcome.get("wall_s"), outcome.get("load_s")
        if outcome.get("status") != "done" or not isinstance(wall, int | float):
            return
        run_s = max(0.0, wall - (load if isinstance(load, int | float) else 0.0))
        self.run.append(run_s)
        if first:
            self.first.append(latency)
        self.usage.update(spawn_s=self.spawn_s(), run_s=_median(self.run))

    def spawn_s(self) -> float | None:
        first, run = _median(self.first), _median(self.run)
        if first is None or run is None:
            return None
        return max(0.0, first - run)

    def limit(self) -> int:
        """Runs per worker process (re-evaluated as measurements arrive)."""
        if self.setting != AUTO:
            return int(self.setting)
        spawn, run = self.spawn_s(), _median(self.run)
        if spawn is None or run is None:
            return 1
        wanted = math.ceil(AUTO_SPAWN_FACTOR * spawn / max(run, 1e-3))
        return max(1, min(AUTO_MAX_RUNS, wanted))

    def used(self, runs: int) -> None:
        self.usage["runs_per_worker"] = max(self.usage.get("runs_per_worker", 1), runs)


def _run_isolated(
    specs: Sequence[Mapping],
    workers: int,
    report: Callable[[dict, Mapping], None],
    runs_per_worker: int | str = 1,
    usage: dict | None = None,
) -> None:
    """Run the specs in at most *workers* single-process pools at a time.

    Each pool's worker process executes up to *runs_per_worker* runs one
    after another (``"auto"``: as many as make its start-up cost small, see
    :data:`AUTO`) and is then replaced by a fresh one.  A worker that dies
    (crash, out of memory) breaks only its own pool, so exactly its run
    fails and every other run is unaffected.  When workers die before any
    run has started, the remaining runs are not started (they stay pending)
    and reported with a hint.  *usage* receives the measurements (see
    :class:`ExecutorChoice`).
    """
    queue = collections.deque(specs)
    running: dict[concurrent.futures.Future, tuple[Mapping, Any]] = {}
    submitted: dict[concurrent.futures.Future, float] = {}
    idle: list[_Slot] = []
    any_started = False
    abort: str | None = None
    reuse = _Reuse(runs_per_worker, {} if usage is None else usage)
    try:
        while queue or running:
            while queue and abort is None and len(running) < workers:
                spec = queue.popleft()
                slot = idle.pop() if idle else _Slot(_spawn_pool())
                with _children_ignore_sigint():  # the pool starts its worker here
                    try:
                        future = slot.pool.submit(run_one, spec)
                    except concurrent.futures.BrokenExecutor:  # died while idle
                        slot.pool.shutdown(wait=False)
                        slot = _Slot(_spawn_pool())
                        future = slot.pool.submit(run_one, spec)
                slot.runs += 1
                reuse.used(slot.runs)
                running[future] = (spec, slot)
                submitted[future] = _time.perf_counter()
            if abort is not None:
                while queue:
                    spec = queue.popleft()
                    report(_not_started(spec, abort), spec)
                if not running:
                    break
            done, _ = concurrent.futures.wait(
                running, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in sorted(done, key=lambda f: running[f][0].get("index", 0)):
                spec, slot = running.pop(future)
                latency = _time.perf_counter() - submitted.pop(future)
                broken = isinstance(
                    future.exception(), concurrent.futures.BrokenExecutor
                )
                outcome = _outcome_of(future, spec)
                reuse.observe(outcome, latency, first=slot.runs == 1)
                if broken or slot.runs >= reuse.limit() or not queue:
                    slot.pool.shutdown(wait=True)
                else:
                    idle.append(slot)
                started = outcome["status"] == "done" or _started(spec)
                any_started = any_started or started
                if not started and not any_started and abort is None:
                    abort = outcome["error"]
                report(outcome, spec)
    except BaseException:
        _stop_pools(running)
        raise
    finally:
        for slot in idle:
            slot.pool.shutdown(wait=True)


def run_specs(
    specs: Sequence[Mapping],
    choice: ExecutorChoice,
    *,
    scenario: Any = None,
    outcomes: Mapping[str, tuple] | None = None,
    on_result: Callable[[dict, Mapping], None] | None = None,
) -> list[dict]:
    """Execute run specs with the chosen executor.

    In-process executors run the given live *scenario* and *outcomes*
    (so unreferenceable scenarios work); other executors get
    :func:`~mango.scenario.run_one` and the plain spec.  Nothing is started when *specs* is
    empty.

    The managed process executor runs every spec in a single-process pool
    (a worker process executes ``choice.runs_per_worker`` runs one after
    another before it is replaced; ``"auto"`` picks that number from the
    measured start-up and run times), so a worker that dies (crash, out of
    memory) fails exactly its own run.  A run that committed before
    its result arrived always counts as done.  A run whose worker died
    before it began is reported with the status ``"pending"`` and written
    nowhere, so it stays pending.  On a caller-owned process pool one dying
    worker breaks the whole pool: every run in flight is recorded as failed
    (the culprit may be any of them) and the rest stay pending.
    ``KeyboardInterrupt`` starts no further runs, lets the running ones
    finish and commit (a second interrupt kills them) and propagates.

    :param specs: the run specs to execute
    :param choice: from :func:`resolve_executor`
    :param scenario: the scenario object for in-process runs (else imported)
    :param outcomes: ``{name: (function, ref)}`` for in-process runs
    :param on_result: called as ``on_result(outcome, spec)`` after each run
    :return: the run outcomes in completion order, each with the status
        ``"done"``, ``"failed"`` or ``"pending"`` (not started)

    Example::

        run_specs(plan.specs, resolve_executor(workers=4))
    """
    results: list[dict] = []
    if choice.kind == "slurm":
        raise StudyError(
            "Slurm runs are not executed here; Study.run submits them with "
            "SlurmExecutor.dispatch(specs, out)."
        )
    if not specs:
        return results

    def report(outcome: dict, spec: Mapping) -> None:
        results.append(outcome)
        if on_result is not None:
            on_result(outcome, spec)

    if choice.kind == "sequential":
        for spec in specs:
            report(_execute(spec, scenario, outcomes), spec)
        return results
    if choice.kind == "process":
        with worker_environment():
            _run_isolated(
                specs, choice.workers, report, choice.runs_per_worker, choice.usage
            )
        return results
    if choice.in_process:

        def task(spec):
            return _execute(spec, scenario, outcomes)

        _drain(choice.executor, specs, task, report)
    else:
        _drain(choice.executor, specs, run_one, report)
    return results


# --------------------------------------------------------------------------- run tables


def _read_run_table(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise StudyError(
            f"No run table {path} (resolved to {path.absolute()})."
        ) from None
    return [entry for entry in text.splitlines() if entry.strip()]


def _table_spec(path: Path, lines: Sequence[str], line: int) -> dict:
    try:
        spec = json.loads(lines[line])
    except ValueError as exc:
        raise StudyError(f"{path}, line {line} is not valid JSON: {exc}") from None
    if not isinstance(spec, dict) or "run_id" not in spec or "out" not in spec:
        raise StudyError(f"{path}, line {line} is not a run spec (no run_id/out).")
    return spec


def _check_line(line: Any, what: str = "line") -> int:
    if isinstance(line, bool) or not isinstance(line, int) or line < 0:
        raise StudyError(f"{what} must be an int >= 0 (0-based), got {line!r}.")
    return line


def run_one_from_file(
    path: str | os.PathLike, line: int, *, force: bool = False
) -> dict:
    """Execute one run spec of a run table file (``runs.jsonl``, ``pending.jsonl``).

    The entry point of a Slurm array task: ``mango-scenario run-one
    <out>/slurm/pending.jsonl --line $SLURM_ARRAY_TASK_ID`` calls it.  Lines
    are counted from 0, blank lines are skipped.  The run itself never
    raises (see :func:`~mango.scenario.run_one`); its run folder records the
    outcome.

    A run whose folder already holds a ``done`` manifest is not executed
    again (a requeued Slurm task, a repeated command): the outcome has the
    status ``"done"`` and ``"skipped": True``.  It is executed again when it
    is stale (its code or data files changed since, see
    :func:`~mango.scenario.freshness.stale_reasons`) or with *force*; the
    previous manifest is then kept as ``manifest.prev.json``.

    :param path: the run table file, one JSON run spec per line
    :param line: the 0-based index of the spec in the file
    :param force: execute the run even when it is done and up to date
    :return: the run outcome ``{"run_id", "index", "status", "wall_s",
        "outcomes", "error"}`` (plus ``"skipped"``/``"stale"`` for a done
        run that was skipped or executed again because it was stale)
    :raises StudyError: if the file is missing, *line* is out of range or
        the line is not a JSON run spec

    Example::

        outcome = run_one_from_file("results/fleet/slurm/pending.jsonl", 3)
        outcome["status"]           # "done" or "failed"
    """
    _check_line(line)
    path = Path(path)
    lines = _read_run_table(path)
    if line >= len(lines):
        raise StudyError(
            f"{path} has {len(lines)} run spec{'s' * (len(lines) != 1)}; line {line} is "
            f"out of range (lines count from 0)."
        )
    return _run_table_spec(_table_spec(path, lines, line), force=force)


def run_lines_from_file(
    path: str | os.PathLike, start: int, stop: int, *, force: bool = False
) -> list[dict]:
    """Execute the run specs ``start <= line < stop`` of a run table file, one by one.

    The entry point of a Slurm array task that runs several runs
    (``SlurmExecutor(runs_per_task=K)``): ``mango-scenario run-one
    <out>/slurm/pending.jsonl --lines START:STOP``.  *stop* may lie beyond
    the end of the file (it is clamped).  A failing run does not stop the
    others.  Each run behaves as in :func:`run_one_from_file`.

    :param path: the run table file
    :param start: the first line (0-based)
    :param stop: the line after the last one
    :param force: execute done, up-to-date runs again
    :return: the outcomes in line order
    :raises StudyError: as :func:`run_one_from_file`, or when *start* is not
        a line of the file or *stop* is not after *start*

    Example::

        outcomes = run_lines_from_file("results/fleet/slurm/pending.jsonl", 8, 12)
        [outcome["status"] for outcome in outcomes]      # ["done", "done", "failed", "done"]
    """
    _check_line(start, "start")
    _check_line(stop, "stop")
    if stop <= start:
        raise StudyError(f"stop ({stop}) must be greater than start ({start}).")
    path = Path(path)
    lines = _read_run_table(path)
    if start >= len(lines):
        raise StudyError(
            f"{path} has {len(lines)} run spec{'s' * (len(lines) != 1)}; line {start} is "
            f"out of range (lines count from 0)."
        )
    specs = [
        _table_spec(path, lines, line) for line in range(start, min(stop, len(lines)))
    ]
    return [_run_table_spec(spec, force=force) for spec in specs]


def _run_table_spec(spec: dict, *, force: bool) -> dict:
    committed = _committed(spec)
    if committed is None:
        return run_one(spec)
    if not force:
        manifest = read_json(Path(spec["out"]) / MANIFEST_FILE) or {}
        root = spec.get("project_root")
        reasons = stale_reasons(manifest, roots=[root] if root else [])
        if not reasons:
            return {**committed, "skipped": True}
        logger.info(
            "Run %s is stale (%s); executing it again",
            spec.get("run_id"),
            ", ".join(f"{r.kind} {r.file or ''}".strip() for r in reasons),
        )
        return {**_execute(spec, keep_previous=True), "stale": True}
    return _execute(spec, keep_previous=True)

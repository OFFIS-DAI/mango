"""
Studies: a scenario, factors, a design and replications turned into an
explicit run table, executed with any executor and stored per run.

Example::

    from mango.scenario import FullFactorial, Study, load_study

    study = Study(
        "studies.ev:scenario",
        base="presets/base.yaml",
        uncertainties={"ev.n": [5, 10, 20], "comm.loss_percent": {"ideal": 0.0, "lossy": 0.1}},
        # algorithm is a str field here; vary a tagged union by its tag:
        # levers={"algorithm.kind": [...]} (variant settings: subexperiments)
        levers={"algorithm": ["greedy", "proportional"]},
        design=FullFactorial(),
        replications=10, seed=2026, common_random_numbers=True,
        outcomes=["ev_study.kpi:self_consumption", "ev_study.kpi:peak_import_kw"],
        out="results/fleet_comparison",
    )
    print(study.plan())                    # 120 runs = 12 points x 10 replications
    report = study.run(workers=8)          # finished runs are skipped on a rerun
    df = load_study("results/fleet_comparison")
"""

from __future__ import annotations

import concurrent.futures
import copy
import logging
import math
import os
import random
import sys
import time as _time
import warnings
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from mango.util.seeding import SeedTree

from ._util import (
    canonical_json,
    content_hash,
    did_you_mean,
    git_dirty_files,
    git_info,
    is_positive_int,
    model_json_data,
    object_ref,
    provenance,
    require_pandas,
    to_plain,
    warn_user,
)
from .designs import (
    REPLICATIONS_COLUMN,
    DesignPoint,
    Factor,
    FromCsv,
    SamplingDesign,
    Sobol,
    _short,
    check_replications,
    resolve_design,
)
from .errors import ParamsError, StudyError
from .freshness import (
    changed_since_import,
    code_files,
    data_files,
    describe_stale,
    files_fingerprint,
    stale_reasons,
)
from .params import (
    _overlay_dump,
    admits_distribution,
    check_path,
    dump_params,
    load_params,
    parse_overrides,
)
from .result import compute_run_id
from .runner import (
    _is_scenario,
    _scenario_file,
    check_refs,
    load_scenario_ref,
    resolve_executor,
    resolve_outcomes,
    run_specs,
)
from .slurm import SlurmExecutor, active_slurm_job
from .store import (
    STUDY_PLANS_DIR,
    StudyStore,
    _design_cells,
    _factor_cells,
    _factor_columns,
    check_outcome_names,
    select_run,
)

if TYPE_CHECKING:
    import pandas

__all__ = ["Study", "StudyPlan", "StudyReport", "SPEC_VERSION"]

logger = logging.getLogger(__name__)

#: Version of the run spec format (``spec_version`` of every run spec).
SPEC_VERSION = 1

_FORMATS = ("auto", "parquet", "csv")


def _non_finite(value: Any) -> bool:
    """True when plain data *value* holds ``inf`` or ``nan`` (not valid JSON)."""
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, Mapping):
        return any(_non_finite(item) for item in value.values())
    if isinstance(value, list):
        return any(_non_finite(item) for item in value)
    return False


def _describe_point(values: Mapping) -> str:
    return (
        ", ".join(f"{path}={_short(value, 30)}" for path, value in values.items())
        or "(no factors)"
    )


_GUARD = 'if __name__ == "__main__":'
_WORKER_IMPORT = (
    "study.run() was called while a worker process imported your script "
    "{file}; put study.run() under '" + _GUARD + "' (worker processes "
    "import the script and must not start the study again)."
)


def _module_frames(name: str) -> Iterator[Any]:
    """Frames on the stack that run the module-level code of module *name*."""
    frame = sys._getframe(1)
    while frame is not None:
        if (
            frame.f_code.co_name == "<module>"
            and frame.f_globals.get("__name__") == name
        ):
            yield frame
        frame = frame.f_back


def _worker_import_file() -> str | None:
    """The script a spawned worker is importing while it calls ``study.run()``.

    ``multiprocessing`` workers started with ``spawn`` run the parent's main
    script as ``__mp_main__``; a ``study.run()`` at its top level (no
    ``__name__`` guard) would start the study again inside every worker.
    """
    for frame in _module_frames("__mp_main__"):
        return frame.f_code.co_filename
    return None


def _unguarded_main_line(frame: Any) -> bool:
    """True when *frame* (module-level code of ``__main__``) runs outside any ``if``.

    Conservative: any enclosing ``if`` counts as a guard, unreadable source
    counts as guarded.
    """
    import ast

    try:
        source = Path(frame.f_code.co_filename).read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, ValueError):
        return False
    line = frame.f_lineno
    body = tree.body
    while True:
        node = next(
            (
                item
                for item in body
                if item.lineno <= line <= (item.end_lineno or item.lineno)
            ),
            None,
        )
        if node is None:
            return False
        if isinstance(node, ast.If):
            return False
        inner = [
            child
            for name in ("body", "orelse", "finalbody", "handlers")
            for child in getattr(node, name, None) or []
            if isinstance(child, ast.stmt) or isinstance(child, ast.ExceptHandler)
        ]
        if not inner:
            return True
        body = inner


def _check_main_guard(choice: Any) -> None:
    """Refuse process-pool runs started at the top level of an unguarded script."""
    worker_file = _worker_import_file()
    if worker_file is not None:
        # inside a spawned worker: stop it with one line instead of a traceback
        raise SystemExit(_WORKER_IMPORT.format(file=worker_file))
    spawned = choice.kind == "process"
    if choice.kind == "external" and isinstance(
        choice.executor, concurrent.futures.ProcessPoolExecutor
    ):
        context = getattr(choice.executor, "_mp_context", None)
        method = context.get_start_method() if context is not None else None
        spawned = method in ("spawn", "forkserver")
    if not spawned:
        return
    main = sys.modules.get("__main__")
    main_file = getattr(main, "__file__", None)
    if not main_file:  # interactive sessions and notebooks are not re-imported
        return
    for frame in _module_frames("__main__"):
        if Path(frame.f_code.co_filename).resolve() != Path(main_file).resolve():
            continue
        if _unguarded_main_line(frame):
            raise StudyError(
                f"study.run() at line {frame.f_lineno} of {main_file} runs at the "
                f"top level of the script, outside an '{_GUARD}' guard. Worker "
                "processes import the script, so each would start the study again. "
                "Move study.run() under the guard:\n\n"
                f"    {_GUARD}\n        study.run(...)"
            )


def _admits(scenario: Any, path: Any) -> bool | None:
    """Whether the scenario's parameter at *path* accepts a distribution (None: unknown)."""
    if not isinstance(path, str):
        return None
    try:
        return admits_distribution(scenario, path.strip())
    except Exception:  # no params model, malformed path: checked later
        return None


def _parse_factors(scenario: Any, specs: Any, role: str) -> dict[str, Factor]:
    if specs is None:
        return {}
    where = "uncertainties" if role == "uncertainty" else "levers"
    if not isinstance(specs, Mapping):
        raise StudyError(
            f"Study {where} must be a mapping {{path: levels}}, got "
            f"{type(specs).__name__}."
        )
    factors = {}
    for path, spec in specs.items():
        factor = Factor.parse(
            path, spec, role, admits_distribution=_admits(scenario, path)
        )
        if factor.path in factors:
            raise StudyError(f"Factor {factor.path!r} appears twice in the {where}.")
        factors[factor.path] = factor
    return factors


def _check_overlaps(paths: Sequence[str], what: str) -> None:
    for i, first in enumerate(paths):
        for second in paths[i + 1 :]:
            if second.startswith(first + ".") or first.startswith(second + "."):
                raise StudyError(
                    f"{what}: the factors {first!r} and {second!r} overlap; vary either "
                    "the whole value or its fields."
                )


def _plain_source(source: Any) -> Any:
    if isinstance(source, str | os.PathLike):
        return str(source)
    if isinstance(source, BaseModel):
        return model_json_data(source, exclude_unset=True)
    if isinstance(source, list | tuple):
        return [_plain_source(item) for item in source]
    if source is None:
        return None
    return to_plain(source, default=repr)


_EXECUTOR_KINDS = ("sequential", "process", "slurm")
_EXECUTOR_KEYS = ("kind", "workers", "runs_per_worker", "slurm")


def _executor_settings(value: Any) -> dict:
    """Normalize the ``executor`` setting of a study (see :class:`Study`)."""
    if value is None:
        return {}
    if isinstance(value, str):
        value = {"kind": value}
    if not isinstance(value, Mapping):
        raise StudyError(
            "executor must be 'sequential', 'process', 'slurm' or a mapping {kind, "
            f"workers, runs_per_worker, slurm}}, got {type(value).__name__}."
        )
    unknown = [key for key in value if key not in _EXECUTOR_KEYS]
    if unknown:
        hint = did_you_mean(str(unknown[0]), _EXECUTOR_KEYS)
        raise StudyError(
            f"executor: unknown key {unknown[0]!r}.{hint} Keys: "
            f"{', '.join(_EXECUTOR_KEYS)}."
        )
    settings = dict(value)
    kind = settings.get("kind")
    if kind is not None and kind not in _EXECUTOR_KINDS:
        raise StudyError(
            f"executor.kind must be one of {list(_EXECUTOR_KINDS)}, got {kind!r}."
        )
    for key in ("workers", "runs_per_worker"):
        value = settings.get(key)
        if key == "runs_per_worker" and value == "auto":
            continue
        if value is not None and not is_positive_int(value):
            raise StudyError(
                f"executor.{key} must be an int >= 1"
                + (" or 'auto'" if key == "runs_per_worker" else "")
                + f", got {value!r}."
            )
    slurm = settings.get("slurm")
    if slurm is not None:
        if not isinstance(slurm, Mapping):
            raise StudyError(
                "executor.slurm must be a mapping of SlurmExecutor options, e.g. "
                f"{{partition: short, time: '00:30:00'}}, got {slurm!r}."
            )
        try:
            SlurmExecutor(**{**slurm, "submit": False})  # check the options now
        except TypeError as exc:
            raise StudyError(f"executor.slurm: {exc}") from None
        except StudyError as exc:
            raise StudyError(f"executor.slurm: {exc}") from None
        settings["slurm"] = dict(slurm)
    return settings


#: Values of ``common_random_numbers`` (``None`` picks one from the design).
CRN_MODES: tuple[bool | str, ...] = (True, False, "levers")

#: Values of ``run_order`` of :class:`Study`.
RUN_ORDERS: tuple[str, ...] = ("point", "replication")

#: A :class:`~mango.scenario.FromCsv` design with more points than this
#: (over the columns that are not levers) warns under common random numbers.
CRN_CSV_POINTS = 8


def _samples_points(design: Any) -> bool:
    """True for designs whose points are random draws (Lhs, RandomSample, ...), not Sobol."""
    return isinstance(design, SamplingDesign) and not isinstance(design, Sobol)


def _crn_mode(value: Any, design: Any) -> bool | str:
    """Resolve ``common_random_numbers`` (see :class:`Study`)."""
    if value is None:
        return "levers" if _samples_points(design) else True
    if value is True or value is False or value == "levers":
        return value
    raise StudyError(
        "common_random_numbers must be True (one seed per replication, shared by "
        "every design point), 'levers' (shared across lever levels only, every "
        "uncertainty point its own seed), False (every run its own seed) or None "
        f"(choose by the design), got {value!r}."
    )


def _crn_text(mode: bool | str) -> str:
    if mode == "levers":
        return "common random numbers across levers"
    return "common random numbers" if mode else "independent seeds"


# --------------------------------------------------------------------------- plan


class StudyPlan:
    """The run table of a study: one plain-data run spec per run, in run order.

    Created by :meth:`Study.plan`.  Iterating yields the run specs (dicts):
    ``spec_version, study, index, run_id, scenario, scenario_fingerprint,
    code_fingerprint, data_fingerprint, params, seed, factors, labels,
    replication, design, outcomes, out, project_root, git, format``.
    ``params`` are the resolved parameters with ``seed`` null: the run seed
    is ``seed``, derived from the study seed.  ``code_fingerprint`` and
    ``data_fingerprint`` hash the project code and the data files when the
    study was planned (see :mod:`mango.scenario.freshness`; the manifest of
    a run records the files it actually used).

    Attributes: ``study``, ``specs``, ``seed`` (the study seed), ``out``
    (the study folder or ``None``), ``blocks`` (factors per block),
    ``points`` (design points before replication), ``dropped`` (duplicate
    runs removed).

    Example::

        plan = study.plan()
        len(plan)                        # 120
        plan[0]["factors"]               # {"ev.n": 5, "comm.loss_percent": 0.0, "algorithm": "greedy"}
        plan.to_frame().groupby("algorithm").size()
    """

    def __init__(
        self,
        *,
        study: Study,
        specs: list[dict],
        seed: int,
        out: Path | None,
        blocks: list[list[Factor]],
        points: int,
        dropped: int,
    ):
        self.study = study
        self.specs = specs
        self.seed = seed
        self.out = out
        self.blocks = blocks
        self.points = points
        self.dropped = dropped

    def __len__(self) -> int:
        return len(self.specs)

    def __iter__(self) -> Iterator[dict]:
        return iter(self.specs)

    def __getitem__(self, index: int) -> dict:
        return self.specs[index]

    def __repr__(self) -> str:
        return f"<StudyPlan {self.study.name!r}: {len(self)} runs>"

    @property
    def run_ids(self) -> list[str]:
        """Run ids in run order."""
        return [spec["run_id"] for spec in self.specs]

    def to_frame(self) -> pandas.DataFrame:
        """Return the run table as a ``pandas.DataFrame``.

        Columns: ``index, run_id, replication, point`` (the design point,
        shared by its replications), ``subexperiment`` and ``sample`` when
        the design has them, one column per factor (dict and list values as
        canonical JSON text) and a ``"<factor>:label"`` column after each
        labeled factor: the columns :func:`~mango.scenario.load_study` starts
        with.

        Example::

            study.plan().to_frame().head()
        """
        pd = require_pandas("StudyPlan.to_frame needs")
        paths, labeled, factor_columns = _factor_columns(self.specs)
        design_columns, design_cells = _design_cells(self.specs)
        rows = [
            {
                "index": spec["index"],
                "run_id": spec["run_id"],
                "replication": spec["replication"],
                **design,
                **_factor_cells(spec, paths, labeled),
            }
            for spec, design in zip(self.specs, design_cells)
        ]
        columns = [
            "index",
            "run_id",
            "replication",
            "point",
            *design_columns,
            *factor_columns,
        ]
        return pd.DataFrame(rows, columns=columns)

    def _replication_counts(self) -> list[int]:
        """Runs per distinct design point (they differ with ``replications`` rows)."""
        counts: dict[str, int] = {}
        for spec in self.specs:
            key = canonical_json(
                [spec["factors"], (spec.get("design") or {}).get("subexperiment")]
            )
            counts[key] = counts.get(key, 0) + 1
        return list(counts.values())

    def __str__(self) -> str:
        study = self.study
        replications = study.replications
        lines = [f"Study {study.name!r}: {len(self)} runs"]
        detail = (
            f"{self.points} design points x {replications} replication"
            f"{'s' * (replications != 1)}"
        )
        counts = self._replication_counts()
        if len(set(counts)) > 1:
            detail = (
                f"{self.points} design points x {min(counts)} to {max(counts)} "
                f"replications (study default {replications})"
            )
        if len(self.blocks) > 1:
            detail += f" in {len(self.blocks)} subexperiments"
        if self.dropped:
            distinct = len({canonical_json(spec["factors"]) for spec in self.specs})
            if distinct < self.points:
                detail = detail.replace(
                    f"{self.points} design points",
                    f"{self.points} design points ({distinct} distinct)",
                    1,
                )
            detail += f", {self.dropped} duplicate runs dropped"
        if getattr(study, "run_order", "point") == "replication":
            detail += ", run order by replication"
        seeds = len({canonical_json(spec["seed"]) for spec in self.specs})
        rows = [
            ("scenario", study.scenario_key),
            ("design", f"{_design_name(study.design)}, {detail}"),
            (
                "seeds",
                f"seed {self.seed}, {_crn_text(study.common_random_numbers)}: "
                f"{len(self)} runs on {seeds} seed{'s' * (seeds != 1)}",
            ),
        ]
        for role, title in (("uncertainty", "uncertainties"), ("lever", "levers")):
            factors = [f for f in study.factors if f.role == role]
            for i, factor in enumerate(factors):
                rows.append(
                    (title if i == 0 else "", f"{factor.path}: {factor.describe()}")
                )
        for b, sub in enumerate(study._subexperiments or []):
            text = "; ".join(f"{f.path}: {f.describe()}" for f in sub.values())
            rows.append(("subexperiment", f"{b}: {text or '(the study factors)'}"))
        if study.outcomes:
            rows.append(("outcomes", ", ".join(study.outcomes)))
        rows.append(("out", str(self.out) if self.out is not None else "(not set)"))
        lines += [f"  {key:<14}{value}" for key, value in rows]
        return "\n".join(lines)


def _design_name(design: Any) -> str:
    spec = design.to_spec() if callable(getattr(design, "to_spec", None)) else None
    if isinstance(spec, str):
        return spec.replace("_", " ")
    return repr(design)


@dataclass
class StudyReport:
    """What :meth:`~mango.scenario.Study.run` did.

    :param total: runs in the plan
    :param done: runs finished successfully in this call
    :param skipped: runs already done before (not started)
    :param failed: failed runs (in this call, or kept with ``retry_failed=False``)
    :param failures: ``[{"run_id", "index", "error"}, ...]``
    :param pending: runs this call did not start because their worker
        process died or the executor broke first (written nowhere; the next
        ``study.run()`` executes them), so ``done + skipped + failed +
        pending == total``
    :param not_started: ``[{"run_id", "index", "error"}, ...]`` for the
        *pending* runs, ``error`` saying why they were not started
    :param out: the study folder
    :param wall_s: wall time of the call
    :param executor: the executor description
    :param submitted: runs handed to Slurm (:class:`~mango.scenario.SlurmExecutor`):
        they are counted as *pending* and run after the call returns
    :param job_id: the Slurm job id (``None`` when not submitted; the first
        job when the array was split, see ``job_ids``)
    :param script: the Slurm batch script (``<out>/slurm/array.sbatch``)
    :param outcome_errors: ``[{"run_id", "index", "errors"}, ...]`` for done
        runs (of this call or skipped) whose outcomes raised: ``errors`` is
        ``{outcome: "Error: message"}`` and the outcome's value is ``None``
    :param stale: finished runs that were stale (their code, data or
        scenario definition changed since) and were run again (they count as
        done or failed); with ``stale="keep"`` the stale runs that were kept
        (they count as skipped)
    :param stale_kept: true when the stale runs were kept (``stale="keep"``)
    :param stale_reasons: what changed, e.g. ``"code changed in model.py"``
    :param job_ids: every Slurm job id (several when a large array was split
        to respect the cluster's ``MaxArraySize``)
    :param hints: advice about the run, e.g. for short runs on a process pool
    :param changed_during_run: runs of this call whose code or data files
        on disk differed, when they finished, from what they used (an edit
        while the study ran; they are stale: the next
        ``study.run()`` repeats them, :meth:`Study.certify` keeps them)
    :param changed_files: those files, e.g. ``"model.py"``
    :param warnings: ``[{"run_id", "index", "warnings"}, ...]`` for runs of
        this call that issued warnings (``warnings`` is the list of distinct
        messages, also stored in the run's ``manifest.json``)

    Example::

        report = study.run(workers=4)
        print(report)               # "120 runs: 118 done, 0 skipped, 2 failed ..."
        assert report.ok, report.failures or report.not_started
        assert not report.outcome_errors, report.outcome_errors
    """

    total: int
    done: int
    skipped: int
    failed: int
    failures: list[dict] = field(default_factory=list)
    out: Path | None = None
    wall_s: float = 0.0
    executor: str = "sequential"
    pending: int = 0
    not_started: list[dict] = field(default_factory=list)
    submitted: int = 0
    job_id: str | None = None
    script: Path | None = None
    outcome_errors: list[dict] = field(default_factory=list)
    stale: int = 0
    stale_kept: bool = False
    stale_reasons: str = ""
    job_ids: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    changed_during_run: int = 0
    changed_files: str = ""
    warnings: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when every run of the plan is done (none failed or left pending)."""
        return self.failed == 0 and self.pending == 0

    def __str__(self) -> str:
        if self.submitted and len(self.job_ids) > 1:
            state = (
                f", {self.submitted} submitted as Slurm jobs {', '.join(self.job_ids)}"
            )
        elif self.submitted and self.job_id is not None:
            state = f", {self.submitted} submitted as Slurm job {self.job_id}"
        elif self.submitted:
            state = (
                f", {self.submitted} pending in {self.script} (not submitted; run: "
                f"sbatch {self.script})"
            )
        else:
            state = ""
        unstarted = self.pending - self.submitted
        with_errors = (
            f" ({len(self.outcome_errors)} with outcome errors)"
            if self.outcome_errors
            else ""
        )
        lines = [
            f"{self.total} runs: {self.done} done, {self.skipped} skipped, "
            f"{self.failed} failed"
            + state
            + (f", {unstarted} not started" if unstarted else "")
            + with_errors
            + f" in {self.wall_s:.1f} s ({self.executor}) -> {self.out}"
        ]
        if self.stale:
            what = (
                "kept (stale='keep'); they may not match the current code or data"
                if self.stale_kept
                else "run again"
            )
            lines.append(
                f"  {self.stale} stale run{'s' * (self.stale != 1)} "
                f"({self.stale_reasons}) {what}"
            )
        if self.changed_during_run:
            n = self.changed_during_run
            lines.append(
                f"  {n} run{'s' * (n != 1)} used code or data that changed while the "
                f"study ran ({self.changed_files}); "
                f"{'they are' if n != 1 else 'it is'} stale: run the study again to "
                "repeat them, or certify them (mango-scenario certify) if the change "
                "does not matter"
            )
        if self.warnings:
            lines += _warning_lines(self.warnings)
        if self.job_id is not None:
            lines.append(
                f"  progress: load_study({str(self.out)!r}) or mango-scenario status "
                f"{self.out}"
            )
        entries = [("failed", entry) for entry in self.failures]
        entries += [("not started:", entry) for entry in self.not_started]
        for what, entry in entries[:10]:
            error = entry["error"]
            if what != "failed":
                error = error.removeprefix("not started: ")
            lines.append(f"  {what} run {entry['index']} ({entry['run_id']}): {error}")
        if len(entries) > 10:
            lines.append(f"  ... and {len(entries) - 10} more (see status.csv)")
        lines += outcome_error_lines(
            (entry["index"], entry["run_id"], entry["errors"])
            for entry in self.outcome_errors
        )
        lines += [f"  hint: {hint}" for hint in self.hints]
        return "\n".join(lines)


def outcome_error_lines(
    entries: Iterable[tuple[int, str, Mapping[str, str]]], limit: int = 5
) -> list[str]:
    """Report lines for outcome errors, one per distinct outcome and error text.

    The same error of many runs (e.g. an outcome that does not fit the
    scenario) is printed once with the runs it occurred in.

    :param entries: ``(run index, run id, {outcome: error})`` per run
    :param limit: distinct errors shown
    :return: indented lines

    Example::

        outcome_error_lines([(0, "ab12", {"total": "ValueError: x"})])
        # ["  outcome 'total' failed in run 0 (ab12): ValueError: x"]
    """
    grouped: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for index, run_id, errors in entries:
        for name, error in (errors or {}).items():
            grouped.setdefault((name, str(error)), []).append((index, run_id))
    lines = []
    for (name, error), runs in list(grouped.items())[:limit]:
        if len(runs) == 1:
            where = f"run {runs[0][0]} ({runs[0][1]})"
        else:
            where = f"{len(runs)} runs ({_listed([str(index) for index, _ in runs])})"
        lines.append(f"  outcome {name!r} failed in {where}: {error}")
    if len(grouped) > limit:
        lines.append(
            f"  ... and {len(grouped) - limit} more distinct outcome errors "
            "(load_study(...)['outcome_errors'])"
        )
    return lines


def _listed(items: Sequence[str], limit: int = 5) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


def _warning_lines(entries: Sequence[Mapping], limit: int = 3) -> list[str]:
    """``N runs with warnings`` and the most frequent distinct messages."""
    counts: dict[str, int] = {}
    for entry in entries:
        for text in entry.get("warnings") or ():
            counts[text] = counts.get(text, 0) + 1
    n = len(entries)
    lines = [
        f"  {n} run{'s' * (n != 1)} with warnings (stored in manifest.json; "
        "mango-scenario status lists them):"
    ]
    ranked = sorted(counts.items(), key=lambda item: -item[1])
    for text, count in ranked[:limit]:
        lines.append(f"    {count}x {_short(text, 200)}")
    if len(ranked) > limit:
        lines.append(f"    ... and {len(ranked) - limit} more distinct warnings")
    return lines


# --------------------------------------------------------------------------- study


class Study:
    """A study: a scenario run over factor combinations and replications.

    :param scenario: a scenario object, an import reference
        ``"module:attr"`` to a scenario or a zero-argument function returning
        one, or the path of a scenario file (``"scenarios/ev.yaml"``, see
        :func:`~mango.scenario.load_scenario`)
    :param base: params source(s) applied to every run: a preset path, a
        dict, a params instance or a list of these
    :param overrides: constants for every run: ``["a.b=1"]`` or ``{"a.b": 1}``
    :param uncertainties: factors describing the world ``{path: spec}``
    :param levers: factors describing what is compared ``{path: spec}``;
        a factor spec is a list of levels, ``{label: value}`` labeled levels,
        ``{"values": [...]}``, ``{"levels": {...}}``, ``{"value": x}``,
        ``{"range": [lo, hi]}`` (sampling designs) or a fixed value
    :param design: :class:`~mango.scenario.FullFactorial` (default),
        :class:`~mango.scenario.Explicit`, :class:`~mango.scenario.FromCsv`,
        the sampling designs :class:`~mango.scenario.Lhs`,
        :class:`~mango.scenario.Sobol` and :class:`~mango.scenario.RandomSample`
        (for ``range`` factors), or a design spec such as
        ``{"csv": "design.csv"}`` or ``{"sobol": {"n": 256}}``
    :param replications: runs per design point (replication 0..R-1)
    :param seed: the study seed; ``None`` reuses the seed recorded in
        ``<out>/study.yaml``, or draws one (and records it on run)
    :param common_random_numbers: how run seeds are shared (see
        :ref:`scenario-crn`): ``True``: replication *r* uses the same seed in
        every design point (paired comparisons across every factor);
        ``"levers"``: runs that differ only in lever values share the seed,
        every combination of the other factors (each sample of a sampling
        design) gets its own; ``False``: every run its own seed; ``None``
        (default): ``"levers"`` for :class:`~mango.scenario.Lhs` and
        :class:`~mango.scenario.RandomSample` (their samples are independent
        draws), else ``True``.  ``True`` with ``Lhs``/``RandomSample`` warns
        about pseudo-replication
    :param outcomes: ``{name: function or "module:fn"}`` or a list of
        functions/references (named after the function); each gets the
        :class:`~mango.scenario.RunResult` and returns a value
    :param subexperiments: a list of factor dicts; each is merged over the
        study factors and forms one block of the run table (only the blocks
        run; add ``{}`` to include the plain study factors).  The reserved
        key ``replications`` sets the replications of that block (rows of
        an :class:`~mango.scenario.Explicit`/``FromCsv`` design can set
        their own with a ``replications`` column)
    :param name: the study name (default: the scenario name)
    :param out: the study folder (can also be given to :meth:`run`)
    :param format: time series format of the run folders: ``"auto"``
        (Parquet when pyarrow is installed, else CSV), ``"parquet"``,
        ``"csv"``
    :param executor: default executor settings of :meth:`run` (the
        ``executor:`` block of a study file): ``"sequential"``,
        ``"process"``, ``"slurm"`` or a mapping ``{kind, workers,
        runs_per_worker, slurm}`` where ``slurm`` holds
        :class:`~mango.scenario.SlurmExecutor` options used whenever the
        study runs on Slurm; arguments of :meth:`run` replace them
    :param run_order: the order of the run table and of dispatch:
        ``"point"`` (default: all replications of a design point, then the
        next point) or ``"replication"`` (replication 0 of every point,
        then replication 1, ...), so an interrupted study keeps its levels
        balanced for :meth:`StudyFrame.paired
        <mango.scenario.StudyFrame.paired>`; run ids do not depend on it
    :raises StudyError: for invalid factor specs, designs, outcomes or
        arguments

    Example::

        study = Study(scenario, base="presets/base.yaml",
                      uncertainties={"ev.n": [5, 10, 20]},
                      # a str field; a tagged union is varied as "algorithm.kind"
                      levers={"algorithm": ["greedy", "proportional"]},
                      replications=10, seed=2026,
                      outcomes={"peak_kw": lambda run: run.ts("import_kw").max()},
                      out="results/fleet")
        report = study.run()
    """

    def __init__(
        self,
        scenario: Any,
        *,
        base: Any = None,
        overrides: Any = None,
        uncertainties: Mapping[str, Any] | None = None,
        levers: Mapping[str, Any] | None = None,
        design: Any = None,
        replications: int = 1,
        seed: int | None = None,
        common_random_numbers: bool | str | None = None,
        outcomes: Any = None,
        subexperiments: Sequence[Mapping[str, Any]] | None = None,
        name: str | None = None,
        out: str | os.PathLike | None = None,
        format: str = "auto",
        executor: Any = None,
        run_order: str = "point",
    ):
        if isinstance(scenario, str):
            self.scenario = load_scenario_ref(scenario)
            own = getattr(self.scenario, "ref", None) or object_ref(self.scenario)
            self.scenario_ref: str | None = own or scenario.strip()
        elif _is_scenario(scenario):
            self.scenario = scenario
            self.scenario_ref = getattr(scenario, "ref", None) or object_ref(scenario)
        else:
            raise StudyError(
                f"A study needs a Scenario or a 'module:attr' reference, got "
                f"{type(scenario).__name__}."
            )
        self.name = (
            name if name is not None else getattr(self.scenario, "name", "study")
        )
        if not isinstance(self.name, str) or not self.name.strip():
            raise StudyError(
                f"A study name must be a non-empty str, got {self.name!r}."
            )
        self.base = base
        self.overrides = parse_overrides(overrides)
        uncertain = _parse_factors(self.scenario, uncertainties, "uncertainty")
        lever = _parse_factors(self.scenario, levers, "lever")
        both = sorted(set(uncertain) & set(lever))
        if both:
            raise StudyError(
                f"Factor(s) {both} are declared as uncertainty and as lever; a factor has "
                "one role."
            )
        self._factors: dict[str, Factor] = {**uncertain, **lever}
        _check_overlaps(list(self._factors), f"Study {self.name!r}")
        self.design = resolve_design(design)
        if (
            isinstance(replications, bool)
            or not isinstance(replications, int)
            or replications < 1
        ):
            raise StudyError(f"replications must be an int >= 1, got {replications!r}.")
        self.replications = replications
        if seed is not None and (
            isinstance(seed, bool) or not isinstance(seed, int) or seed < 0
        ):
            raise StudyError(f"seed must be an int >= 0 or None, got {seed!r}.")
        self.seed = seed
        #: the resolved mode: ``True``, ``False`` or ``"levers"``
        self.common_random_numbers: bool | str = _crn_mode(
            common_random_numbers, self.design
        )
        self._outcomes = resolve_outcomes(outcomes)
        self._subexperiments = self._parse_subexperiments(subexperiments)
        self._check_outcome_names()
        self.out = None if out is None else Path(out)
        if format not in _FORMATS:
            raise StudyError(f"format must be one of {list(_FORMATS)}, got {format!r}.")
        self.format = format
        self.executor = _executor_settings(executor)
        if run_order not in RUN_ORDERS:
            raise StudyError(
                f"run_order must be 'point' (replications of a point together) or "
                f"'replication' (every point of a replication together), got "
                f"{run_order!r}."
            )
        self.run_order = run_order
        self._drawn_seed: int | None = None

    @classmethod
    def from_yaml(cls, path: str | os.PathLike, **changes: Any) -> Study:
        """Create a study from a study file (see :func:`~mango.scenario.load_study_file`).

        :param path: the study file (YAML or JSON)
        :param changes: constructor arguments replacing the file's values
        :return: the study
        :raises StudyError: for invalid study files (naming the file and key)

        Example::

            study = Study.from_yaml("studies/fleet_comparison.yaml", out="results/test")
            print(study.plan())
        """
        from .yaml_scenario import load_study_file

        return load_study_file(path, **changes)

    def __repr__(self) -> str:
        return (
            f"Study({self.name!r}, scenario={self.scenario_key!r}, "
            f"factors={list(self._factors)}, replications={self.replications})"
        )

    # -- views -----------------------------------------------------------------------

    @property
    def scenario_key(self) -> str:
        """The scenario reference used in run specs and run ids (the name if unreferenceable)."""
        return self.scenario_ref or self.scenario.name

    @property
    def factors(self) -> list[Factor]:
        """The study factors: uncertainties first, then levers."""
        return list(self._factors.values())

    @property
    def outcomes(self) -> dict[str, str | None]:
        """Outcome names and their import references (``None`` if unreferenceable)."""
        return {name: ref for name, (_, ref) in self._outcomes.items()}

    @property
    def subexperiments(self) -> list[dict[str, Factor]] | None:
        """The parsed subexperiments (``None`` without)."""
        return None if self._subexperiments is None else list(self._subexperiments)

    def _parse_subexperiments(
        self, subexperiments: Any
    ) -> list[dict[str, Factor]] | None:
        if subexperiments is None:
            return None
        if isinstance(subexperiments, str | bytes | Mapping) or not isinstance(
            subexperiments, Sequence
        ):
            raise StudyError(
                "subexperiments must be a list of factor dicts {path: spec}, got "
                f"{type(subexperiments).__name__}."
            )
        if not subexperiments:
            raise StudyError(
                "subexperiments is empty; drop it or add {} to run the study factors."
            )
        parsed = []
        self._sub_replications: list[int | None] = []
        for i, given in enumerate(subexperiments):
            if not isinstance(given, Mapping):
                raise StudyError(
                    f"Subexperiment {i} must be a mapping {{path: spec}}, got {given!r}."
                )
            sub = dict(given)
            count = None
            if REPLICATIONS_COLUMN in sub and REPLICATIONS_COLUMN not in self._factors:
                count = check_replications(
                    sub.pop(REPLICATIONS_COLUMN), f"Subexperiment {i}"
                )
            self._sub_replications.append(count)
            factors = {}
            for path, spec in sub.items():
                known = self._factors.get(str(path).strip())
                factor = Factor.parse(
                    path,
                    spec,
                    known.role if known else "lever",
                    admits_distribution=_admits(self.scenario, path),
                )
                factors[factor.path] = factor
            parsed.append(factors)
        return parsed

    def _check_outcome_names(self) -> None:
        paths = set(self._factors)
        for sub in self._subexperiments or []:
            paths.update(sub)
        check_outcome_names(self._outcomes, paths)

    # -- planning ----------------------------------------------------------------------

    def _out(self, out: Any) -> Path | None:
        chosen = out if out is not None else self.out
        return None if chosen is None else Path(chosen).absolute()

    def _seed_for(self, out: Path | None, base_seed: int | None = None) -> int:
        if self.seed is not None:
            return self.seed
        if base_seed is not None:
            return base_seed
        if out is not None:
            persisted = StudyStore(out).persisted_seed()
            if persisted is not None:
                return persisted
        if self._drawn_seed is None:
            self._drawn_seed = random.getrandbits(63)
        return self._drawn_seed

    def _point_replications(self, block: int, meta: Mapping) -> int:
        """Replications of a design point: its row's, its subexperiment's or the study's."""
        count = meta.get(REPLICATIONS_COLUMN)
        if count is None and self._subexperiments is not None:
            count = self._sub_replications[block]
        return self.replications if count is None else count

    def _blocks(self) -> list[list[Factor]]:
        if self._subexperiments is None:
            return [self.factors]
        blocks = []
        for i, sub in enumerate(self._subexperiments):
            merged = dict(self._factors)
            merged.update(sub)
            _check_overlaps(list(merged), f"Subexperiment {i}")
            blocks.append(list(merged.values()))
        return blocks

    def _check_paths(self, entries: list[tuple[str, str]]) -> None:
        problems = []
        seen = set()
        for where, path in entries:
            if (where, path) in seen:
                continue
            seen.add((where, path))
            if path == "seed":
                problems.append(
                    f"  {where}: 'seed' cannot be a factor: every run's seed is derived "
                    "from the study seed. Use replications=N for N seeds per design "
                    "point and Study(seed=...) for another set of seeds."
                )
                continue
            try:
                check_path(self.scenario, path)
            except ParamsError as exc:
                problems.append(f"  {where}: {exc}")
        if problems:
            raise StudyError(
                f"Study {self.name!r} has unknown factor paths for "
                f"{self.scenario.params_model.__name__}:\n" + "\n".join(problems)
            )

    def _declared_paths(self) -> list[tuple[str, str]]:
        entries = [
            ("uncertainties" if f.role == "uncertainty" else "levers", f.path)
            for f in self.factors
        ]
        for i, sub in enumerate(self._subexperiments or []):
            entries += [(f"subexperiment {i}", path) for path in sub]
        return entries

    def _generate(self, factors: list[Factor], seeds: SeedTree, block: str) -> list:
        points = self.design.generate(factors, seeds)
        if not isinstance(points, list) or not all(
            isinstance(point, DesignPoint) for point in points
        ):
            raise StudyError(
                f"{self.design!r}.generate must return a list of DesignPoint objects."
            )
        known = {f.path for f in factors}
        extra = []
        for point in points:
            extra += [
                ("design", path)
                for path in point.values
                if path not in known and ("design", path) not in extra
            ]
        self._check_paths(extra)
        if not points:
            raise StudyError(f"{self.design!r} produced no design points{block}.")
        return points

    def _resolve_point(self, base: dict, values: dict) -> dict:
        params = _overlay_dump(self.scenario, base, values)
        if params is not None:
            return dump_params(params)
        try:  # the full path, for the precise error
            params = load_params(self.scenario, base, overrides=values)
        except ParamsError as exc:
            problems = "\n".join(
                f"  {entry['path'] or '(root)'}: {entry['message']}"
                for entry in exc.errors
            )
            raise StudyError(
                f"Study {self.name!r}: the design point {_describe_point(values)} "
                f"gives invalid parameters:\n{problems}"
            ) from None
        return dump_params(params)

    def _run_seed(
        self,
        root: SeedTree,
        values: Mapping,
        replication: int,
        levers: frozenset[str] = frozenset(),
    ) -> SeedTree:
        """The seed of one run (see ``common_random_numbers`` of :class:`Study`).

        *levers* are the lever paths of the point's block; with ``"levers"``
        the seed depends on the other values only (every path a design sets
        without declaring it as a lever counts as an uncertainty).
        """
        mode = self.common_random_numbers
        if mode == "levers":
            point = {path: v for path, v in values.items() if path not in levers}
            if not point:
                return root.child("replication", replication)
            return root.child("sample", int(content_hash(point, 16), 16), replication)
        if mode:
            return root.child("replication", replication)
        variant = int(content_hash(values, 16), 16)
        return root.child("variant", variant, replication)

    def _warn_pseudo_replication(self, specs: Sequence[Mapping]) -> None:
        """Warn when ``True`` makes every sample of a sampling design share its seed.

        An imported design (:class:`~mango.scenario.FromCsv`) whose columns
        that are not levers hold many points (more than
        :data:`CRN_CSV_POINTS`, e.g. an exported Latin hypercube) gets a
        warning too: its points share the seeds of their replication.
        """
        if self.common_random_numbers is not True:
            return
        if isinstance(self.design, FromCsv):
            self._warn_shared_csv_seeds(specs)
            return
        if not _samples_points(self.design):
            return
        seeds = {canonical_json(spec["seed"]) for spec in specs}
        if len(seeds) >= len(specs) / max(self.replications, 1) or len(specs) < 2:
            return
        warnings.warn(
            f"Study {self.name!r}: common_random_numbers=True with the sampling design "
            f"{_design_name(self.design)} gives every sample of replication r the same "
            f"seed, so {len(specs)} runs use only {len(seeds)} distinct seed"
            f"{'s' * (len(seeds) != 1)}: the samples are not independent "
            "replicates (pseudo-replication), and statistics over samples are "
            "conditional on these noise realisations. Use "
            "common_random_numbers='levers' (the default for this design) to pair "
            "runs only across lever levels and give every sample its own seed.",
            UserWarning,
            stacklevel=3,
        )

    def _warn_shared_csv_seeds(self, specs: Sequence[Mapping]) -> None:
        levers = {f.path for f in self.factors if f.role == "lever"}
        points = {
            canonical_json(
                {p: v for p, v in spec["factors"].items() if p not in levers}
            )
            for spec in specs
        }
        if len(points) <= CRN_CSV_POINTS:
            return
        seeds = {canonical_json(spec["seed"]) for spec in specs}
        warn_user(
            f"Study {self.name!r}: common_random_numbers=True with "
            f"{_design_name(self.design)} gives its {len(points)} points (of the "
            f"columns that are not levers) the seeds of their replication: "
            f"{len(specs)} runs on {len(seeds)} seed{'s' * (len(seeds) != 1)}. For a "
            "space-filling or regression design, block on replication in "
            "OLS/ANOVA (C(replication)), or use common_random_numbers='levers' to "
            "give every point its own seed, as Lhs and RandomSample do."
        )

    def plan(self, out: str | os.PathLike | None = None) -> StudyPlan:
        """Build the run table without running anything.

        Factor paths are checked against the scenario's params model and the
        params of every design point are resolved and validated, so typos and
        invalid values fail here.

        :param out: the study folder (default: the study's ``out``); used to
            fill ``out`` of the run specs and to find a recorded seed
        :return: the :class:`StudyPlan`
        :raises StudyError: for unknown factor paths (with suggestions),
            invalid design points and design errors
        :raises ParamsError: for invalid base parameters

        Example::

            plan = study.plan()
            print(plan)
            plan[0]["params"]["ev"]["n"]
        """
        out_dir = self._out(out)
        self._check_paths(self._declared_paths())
        base = dump_params(
            self.scenario.resolve_params(self.base, overrides=self.overrides)
        )
        # inside a study every run's seed derives from the study seed, so a
        # seed in the base parameters seeds the study and is null in the runs
        seed = self._seed_for(out_dir, base.pop("seed", None))
        base["seed"] = None
        root = SeedTree(seed)
        fingerprint = self.scenario.fingerprint()
        project_root = str(Path.cwd())
        git = git_info(project_root)
        code_fingerprint = files_fingerprint(self._code_files(project_root, out_dir))
        data_cache: dict[str, str | None] = {}
        store = StudyStore(out_dir) if out_dir is not None else None
        blocks = self._blocks()
        params_cache: dict[str, dict] = {}
        specs: list[dict] = []
        kept: dict[str, dict] = {}
        points_total = 0
        dropped = 0
        for b, factors in enumerate(blocks):
            subexperiment = self._subexperiments is not None
            design_seeds = (
                root.child("design", b) if subexperiment else root.child("design")
            )
            block = f" for subexperiment {b}" if subexperiment else ""
            points = self._generate(factors, design_seeds, block)
            points_total += len(points)
            levers = frozenset(f.path for f in factors if f.role == "lever")
            for point in points:
                values = to_plain(dict(point.values))
                bad = [path for path, value in values.items() if _non_finite(value)]
                if bad:
                    raise StudyError(
                        f"Study {self.name!r}: the design point "
                        f"{_describe_point(values)} has a value that is not finite for "
                        f"{bad}; JSON run files cannot store inf or nan, use a large "
                        "finite number instead."
                    )
                key = canonical_json(values)
                if key not in params_cache:
                    params_cache[key] = self._resolve_point(base, values)
                    data_cache[key] = files_fingerprint(
                        data_files(params_cache[key], project_root)
                    )
                params = params_cache[key]
                meta = to_plain(dict(point.meta))
                if subexperiment:
                    meta["subexperiment"] = b
                for replication in range(self._point_replications(b, meta)):
                    seed_dict = self._run_seed(
                        root, values, replication, levers
                    ).to_dict()
                    run_id = compute_run_id(self.scenario_key, params, seed_dict)
                    if run_id in kept:
                        # same params and seed: the kept run stands for this
                        # point too (sobol_indices needs every Saltelli point)
                        if meta:
                            kept[run_id]["design"].setdefault("duplicates", [])
                            kept[run_id]["design"]["duplicates"].append(
                                copy.deepcopy(meta)
                            )
                        dropped += 1
                        continue
                    kept[run_id] = spec = {
                        "spec_version": SPEC_VERSION,
                        "study": self.name,
                        "index": len(specs),
                        "run_id": run_id,
                        "scenario": self.scenario_key,
                        "scenario_fingerprint": fingerprint,
                        "code_fingerprint": code_fingerprint,
                        "data_fingerprint": data_cache[key],
                        "params": copy.deepcopy(params),
                        "seed": seed_dict,
                        "factors": copy.deepcopy(values),
                        "labels": to_plain(dict(point.labels)),
                        "replication": replication,
                        "design": copy.deepcopy(meta),
                        "outcomes": self.outcomes,
                        "out": None if store is None else str(store.run_dir(run_id)),
                        "project_root": project_root,
                        "git": copy.deepcopy(git),
                        "format": self.format,
                    }
                    specs.append(spec)
        if self.run_order == "replication":  # interleave: balanced when interrupted
            specs.sort(key=lambda spec: spec["replication"])
            for index, spec in enumerate(specs):
                spec["index"] = index
        self._warn_pseudo_replication(specs)
        return StudyPlan(
            study=self,
            specs=specs,
            seed=seed,
            out=out_dir,
            blocks=blocks,
            points=points_total,
            dropped=dropped,
        )

    def _code_files(self, root: str, out_dir: Path | None) -> dict[str, str]:
        """The project code of the scenario and the outcomes (see :mod:`~mango.scenario.freshness`)."""
        return code_files(
            root,
            objects=[self.scenario, *(fn for fn, _ in self._outcomes.values())],
            files=_scenario_file(self.scenario),
            exclude=[out_dir] if out_dir is not None else [],
        )

    def definition(self, plan: StudyPlan) -> dict:
        """Return the resolved study definition written to ``study.yaml``.

        Example::

            study.definition(study.plan())["seed"]
        """
        design = self.design
        return {
            "name": self.name,
            "scenario": self.scenario_key,
            "base": _plain_source(self.base),
            "overrides": to_plain(dict(self.overrides)),
            "uncertainties": {
                f.path: f.to_spec() for f in self.factors if f.role == "uncertainty"
            },
            "levers": {f.path: f.to_spec() for f in self.factors if f.role == "lever"},
            "design": design.to_spec()
            if callable(getattr(design, "to_spec", None))
            else repr(design),
            "replications": self.replications,
            "run_order": self.run_order,
            "common_random_numbers": self.common_random_numbers,
            "seed": plan.seed,
            "outcomes": self.outcomes,
            "subexperiments": None
            if self._subexperiments is None
            else [
                {
                    **{path: f.to_spec() for path, f in sub.items()},
                    **({REPLICATIONS_COLUMN: count} if count is not None else {}),
                }
                for sub, count in zip(self._subexperiments, self._sub_replications)
            ],
            "format": self.format,
            "executor": to_plain(self.executor) or None,
            "runs": len(plan),
            "mango_version": provenance()["versions"]["mango-agents"],
        }

    # -- running -----------------------------------------------------------------------

    def run(
        self,
        executor: Any = None,
        workers: int | None = None,
        out: str | os.PathLike | None = None,
        retry_failed: bool = True,
        progress: bool = True,
        *,
        runs_per_worker: int | str | None = None,
        stale: str = "rerun",
        require_clean: bool = False,
    ) -> StudyReport:
        """Plan the study, write the study folder and execute the pending runs.

        Runs whose folder holds a ``done`` manifest are skipped before
        anything starts (outcomes they lack are computed from their stored
        time series), unless they are *stale*: the project code or a data
        file they used changed since, or the scenario definition did (see
        :mod:`mango.scenario.freshness`).  Stale runs are run again by
        default (their previous manifest is kept as ``manifest.prev.json``);
        a warning names the changed files.  Failed runs are re-run when
        *retry_failed*; partial folders (no manifest) are deleted and
        re-run.  ``status.csv`` is rewritten at the end, also after
        ``KeyboardInterrupt`` (which starts no further runs, lets the
        running ones finish and propagates).  The study folder is locked
        while the study runs, so a second ``study.run()`` on it fails
        instead of repeating the same runs.

        When the folder holds another plan of this study (other overrides,
        factors, design, base or seed), ``study.yaml`` and ``runs.jsonl``
        describe the new plan afterwards; the previous plan is kept in
        ``plans/<plan id>/`` and a warning says how many finished runs it
        leaves out of the new plan (``load_study(out, plan="all")`` shows
        the runs of every plan).

        Without *executor* and *workers* the study's ``executor`` settings
        apply (the ``executor:`` block of a study file).

        :param executor: ``None``/``"sequential"`` (in-process), ``"process"``
            (a managed spawn process pool), ``"slurm"`` or a
            :class:`~mango.scenario.SlurmExecutor` (writes and submits a job
            array, then returns at once) or a ``concurrent.futures.Executor``
            (used as is, not shut down)
        :param workers: worker processes; ``workers > 1`` without *executor*
            means a managed process pool
        :param out: the study folder (default: the study's ``out``)
        :param retry_failed: re-run runs that failed before
        :param progress: print one line per finished run to stderr
        :param runs_per_worker: runs a worker process of the managed pool
            executes before a fresh process replaces it: ``"auto"`` (the
            default) measures the start-up cost of a worker (about 1.5-3 s of
            CPU time) and the run time and reuses workers for short runs
            (K = ceil(10 * start-up / run time), at most 200); ``1`` gives
            every run a fresh process (the strictest isolation); an int K
            fixes the number
        :param stale: ``"rerun"`` (default) runs stale finished runs again,
            ``"keep"`` keeps them (with a warning; ``load_study`` marks them
            in its ``stale`` column)
        :param require_clean: refuse to start when tracked files of the
            project's git work tree have uncommitted changes (without it such
            a study warns, and each run stores the diff in ``<out>/git/``)
        :return: the :class:`StudyReport`
        :raises StudyError: without an output folder, for unknown executors,
            when a process-based executor cannot import the scenario or an
            outcome, when the folder holds a study of another name, when
            another ``study.run()`` uses the folder, when a
            Slurm job of the folder still has queued or running tasks, when
            ``sbatch`` is missing or fails, and when a spawned process pool
            is started from the top level of a script without an
            ``if __name__ == "__main__":`` guard (a worker that imports the
            script and reaches ``study.run()`` exits with one line instead)

        Example::

            report = study.run(workers=8, out="results/fleet")
            report = study.run(workers=8, runs_per_worker=1)   # a fresh process per run
            print(report)
        """
        if stale not in ("rerun", "keep"):
            raise StudyError(f"stale must be 'rerun' or 'keep', got {stale!r}.")
        choice = self._choose_executor(executor, workers, runs_per_worker)
        _check_main_guard(choice)
        out_dir = self._out(out)
        if out_dir is None:
            raise StudyError(
                f"Study {self.name!r} needs an output folder: Study(..., out='results/"
                f"{self.name}') or study.run(out=...)."
            )
        if require_clean:
            self._check_clean_tree(require=True)
        if not choice.in_process:
            check_refs(self.scenario_ref, self._outcomes)
        else:
            self._check_loaded_code(out_dir)
        if choice.kind == "slurm":
            choice.executor.check()
        t0 = _time.perf_counter()
        plan = self.plan(out=out_dir)
        self._check_clean_tree(plan=plan)
        store = StudyStore(out_dir)
        with store.lock():
            return self._run_locked(
                plan,
                store,
                choice,
                retry_failed=retry_failed,
                progress=progress,
                t0=t0,
                stale=stale,
            )

    def certify(
        self,
        runs: Sequence[int | str] | None = None,
        *,
        reason: str,
        out: str | os.PathLike | None = None,
    ) -> list[str]:
        """Mark stale finished runs as current, recording why they are still valid.

        A finished run is stale when the project code, a data file or the
        scenario definition changed since it was made, or while it ran;
        ``study.run()`` repeats such runs.  When the change does not affect
        the results (a refactoring, a new unused helper; ``mango-scenario
        rerun`` replays a run identically), certify them instead: their
        manifests record the current file digests and scenario fingerprint
        plus the reason (``manifest["certified"]``), the old manifest stays
        in ``manifest.prev.json``.  ``mango-scenario certify`` does the same.

        :param runs: run indices or run ids (prefixes) to certify (default:
            every stale run of the study folder)
        :param reason: why the runs are still valid (required)
        :param out: the study folder (default: the study's ``out``)
        :return: the certified run ids
        :raises StudyError: without a reason or study folder, or for unknown runs

        Example::

            study.certify(reason="added an unused helper; rerun of run 0 identical")
        """
        out_dir = self._out(out)
        if out_dir is None:
            raise StudyError(
                f"Study {self.name!r} has no output folder; pass out=... to certify."
            )
        store = StudyStore(out_dir)
        try:
            specs = store.read_runs()
        except FileNotFoundError as exc:
            raise StudyError(str(exc)) from None
        run_ids = None
        if runs is not None:
            run_ids = [
                select_run(specs, run, where=str(out_dir))["run_id"] for run in runs
            ]
        return store.certify(
            run_ids, reason=reason, scenario_fingerprint=self.scenario.fingerprint()
        )

    def _check_clean_tree(self, *, require: bool = False, plan: Any = None) -> None:
        """Refuse (*require*) or warn about uncommitted changes of the project.

        *require* refuses any uncommitted change of a tracked file; the
        warning names only changed files the runs use (the project code of the
        scenario and outcomes, the base presets and the data files of the
        first run), since the manifests record the whole tree as dirty anyway.
        """
        root = Path.cwd()
        if require:
            changed = git_dirty_files(root)
            if changed:
                raise StudyError(
                    f"Study {self.name!r}: the git work tree has uncommitted changes "
                    f"({_listed(changed)}); commit them first (require_clean / "
                    "--require-clean)."
                )
            return
        changed = git_dirty_files(root, relative=True)
        if not changed or plan is None or not len(plan):
            return
        used = set(self._code_files(str(root), plan.out))
        used |= set(data_files(plan[0]["params"], str(root)))
        bases = self.base if isinstance(self.base, list | tuple) else [self.base]
        used |= {
            Path(base).as_posix()
            for base in bases
            if isinstance(base, str | os.PathLike)
        }
        relevant = [path for path in changed if path in used]
        if not relevant:
            return
        warn_user(  # at the user's study.run(...) call
            f"Study {self.name!r} runs code or data with uncommitted changes "
            f"({_listed(relevant)}); each run records the commit and stores the diff in "
            "<out>/git/. Commit first for a clean provenance (require_clean=True / "
            "--require-clean refuses to start)."
        )

    def _check_loaded_code(self, out_dir: Path) -> None:
        """Refuse in-process runs when project modules changed after this process imported them."""
        changed = changed_since_import(
            Path.cwd(),
            objects=[self.scenario, *(fn for fn, _ in self._outcomes.values())],
            exclude=[out_dir],
        )
        if changed:
            raise StudyError(
                f"Study {self.name!r}: {', '.join(changed)} changed after this process "
                "imported "
                + ("it" if len(changed) == 1 else "them")
                + "; runs in this process would execute the old code while their "
                "manifests record the new files. Restart the Python process (or the "
                "notebook kernel) and run the study again, or run it in fresh worker "
                "processes (workers=N)."
            )

    def _choose_executor(
        self, executor: Any, workers: Any, runs_per_worker: Any
    ) -> Any:
        """Resolve the executor from the arguments and the study's ``executor`` settings."""
        settings = self.executor
        if executor is None and workers is None:
            executor = settings.get("kind")
        if workers is None and (executor is None or executor == "process"):
            workers = settings.get("workers")
        if isinstance(executor, str) and executor == "slurm" and settings.get("slurm"):
            executor = SlurmExecutor(**settings["slurm"])
        if runs_per_worker is None and settings.get("runs_per_worker") is not None:
            if resolve_executor(executor, workers).kind == "process":
                runs_per_worker = settings["runs_per_worker"]
        return resolve_executor(executor, workers, runs_per_worker)

    def _stale_runs(
        self, store: StudyStore, skipped: list[dict]
    ) -> list[tuple[dict, list]]:
        """The finished runs whose code, data or scenario definition changed since."""
        fingerprint = self.scenario.fingerprint()
        found = []
        for spec in skipped:
            manifest = store.read_manifest(spec["run_id"]) or {}
            roots = [spec.get("project_root"), manifest.get("project_root")]
            reasons = stale_reasons(
                manifest, roots=roots, scenario_fingerprint=fingerprint
            )
            if reasons:
                found.append((spec, reasons))
        return found

    def _complete_skipped(self, store: StudyStore, skipped: list[dict]) -> None:
        """Compute outcomes declared after finished runs were made, from their stored time series."""
        missing: dict[tuple[str, ...], list[dict]] = {}
        for spec in skipped:
            values = store.read_outcomes(spec["run_id"])
            names = tuple(
                name
                for name, (fn, _) in self._outcomes.items()
                if fn is not None and name not in values
            )
            if names:
                missing.setdefault(names, []).append(spec)
        for names, specs in missing.items():
            logger.info(
                "Study %r: computing outcome(s) %s of %d finished run(s) from their "
                "stored time series",
                self.name,
                list(names),
                len(specs),
            )
            table = {name: self._outcomes[name][0] for name in names}
            store.recompute_outcomes(table, specs, scenario=self.scenario)

    def _replace_plan(self, store: StudyStore, plan: StudyPlan, existing: dict) -> None:
        """Keep the folder's previous plan in ``plans/`` and say what the new one leaves out."""
        try:
            old_specs = store.read_runs() if store.runs_file.exists() else []
        except StudyError:
            old_specs = []
        old_ids = [spec.get("run_id") for spec in old_specs]
        if not old_specs or set(old_ids) == set(plan.run_ids):
            return
        plan_id = store.archive_plan()
        new_ids = set(plan.run_ids)
        dropped = [run_id for run_id in old_ids if run_id not in new_ids]
        finished = [
            run_id
            for run_id in dropped
            if store.run_state(run_id) in ("done", "failed")
        ]
        if not finished:
            logger.info(
                "Study %r: the previous plan of %s is kept in %s",
                self.name,
                store.out,
                store.plans_dir / str(plan_id),
            )
            return
        changes = _definition_changes(existing, self.definition(plan))
        shown = _display_path(store.out)
        kept = (
            f"{len(finished)} finished run{'s' * (len(finished) != 1)} of the previous "
            f"plan {'are' if len(finished) != 1 else 'is'} not in the new plan: "
            f"{'they stay' if len(finished) != 1 else 'it stays'} in runs/, and the "
            f"previous plan is kept in {Path(STUDY_PLANS_DIR) / str(plan_id)}; "
            f"load_study({shown!r}, plan='all') shows the runs of every plan"
        )
        if not changes:
            params = _params_changes(old_specs, plan.specs)
            new_ids = len(set(plan.run_ids) - set(old_ids))
            warn_user(
                f"Study {self.name!r}: the resolved parameters of its runs changed "
                f"({params or 'the scenario or its parameter model changed'}), and run "
                "ids hash the resolved parameters, defaults included: the study now "
                f"plans {len(plan)} runs, {new_ids} of them with new ids. {kept}. They "
                "are not stale runs of this plan, so --keep-stale cannot keep them."
            )
            return
        warn_user(
            f"The study folder {shown} held another plan of study {self.name!r}"
            f" ({changes}); study.yaml and runs.jsonl now describe the new plan. "
            f"{kept} (with the overrides as columns). Use another 'out' folder (--out) "
            "to keep variants apart."
        )

    def _run_locked(
        self,
        plan: StudyPlan,
        store: StudyStore,
        choice: Any,
        *,
        retry_failed: bool,
        progress: bool,
        t0: float,
        stale: str = "rerun",
    ) -> StudyReport:
        out_dir = store.out
        job = active_slurm_job(out_dir)
        if job is not None:
            raise StudyError(
                f"Slurm job {job} of the study folder {out_dir} still has queued or "
                f"running tasks; wait until it finishes (squeue --jobs={job}) or "
                f"cancel it (scancel {job}) before running the study again."
            )
        existing = store.read_definition() or {}
        owner = existing.get("name")
        if owner is not None and owner != self.name:
            raise StudyError(
                f"The folder {out_dir} holds the study {owner!r}; running study "
                f"{self.name!r} there would replace its run table. Use another 'out' "
                f"folder, e.g. {str(out_dir.parent / self.name)!r}."
            )
        self._replace_plan(store, plan, existing)
        store.write_definition(self.definition(plan))
        store.write_runs(plan.specs)
        triage = store.triage(plan.specs, retry_failed=retry_failed)
        stale_runs = self._stale_runs(store, triage.skipped)
        stale_text = describe_stale(reasons for _, reasons in stale_runs)
        if stale_runs:
            count = len(stale_runs)
            runs = f"{count} finished run{'s' * (count != 1)}"
            if stale == "keep":
                warnings.warn(
                    f"Study {self.name!r}: {runs} {'are' if count != 1 else 'is'} "
                    f"stale ({stale_text}) and kept (stale='keep'); their results may "
                    "not match the current code or data. load_study() marks them in "
                    "its 'stale' column; 'mango-scenario certify' (or "
                    "study.certify()) records that they are still valid.",
                    UserWarning,
                    stacklevel=3,
                )
            else:
                warnings.warn(
                    f"Study {self.name!r}: {runs} {'are' if count != 1 else 'is'} "
                    f"stale ({stale_text}); running {'them' if count != 1 else 'it'} "
                    "again (stale='keep' / --keep-stale keeps them; 'mango-scenario "
                    "certify' marks runs as still valid).",
                    UserWarning,
                    stacklevel=3,
                )
                rerun = {spec["run_id"] for spec, _ in stale_runs}
                for spec, _ in stale_runs:
                    store.reset_run(spec["run_id"])
                triage.skipped = [
                    spec for spec in triage.skipped if spec["run_id"] not in rerun
                ]
                triage.pending = sorted(
                    triage.pending + [spec for spec, _ in stale_runs],
                    key=lambda spec: spec["index"],
                )
        self._complete_skipped(store, triage.skipped)
        self._log_git_mix(store, triage.skipped)
        failures = [
            {
                "run_id": spec["run_id"],
                "index": spec["index"],
                "error": manifest.get("error"),
            }
            for spec, manifest in triage.failed
        ]
        outcome_errors = []
        for spec in triage.skipped:
            errors = (store.read_manifest(spec["run_id"]) or {}).get("outcome_errors")
            if errors:
                outcome_errors.append(
                    {"run_id": spec["run_id"], "index": spec["index"], "errors": errors}
                )
        counts = {"done": 0, "failed": len(triage.failed), "pending": 0}
        not_started: list[dict] = []
        run_times: list[float] = []
        run_warnings: list[dict] = []
        changed: dict[str, None] = {}
        changed_runs = [0]
        pending = len(triage.pending)
        width = len(str(pending))
        if triage.skipped:
            logger.info(
                "Study %r: %d of %d runs already done",
                self.name,
                len(triage.skipped),
                len(plan),
            )

        def on_result(outcome: dict, spec: Mapping) -> None:
            status = outcome.get("status")
            entry = {
                "run_id": spec["run_id"],
                "index": spec["index"],
                "error": outcome.get("error"),
            }
            if outcome.get("warnings"):
                run_warnings.append(
                    {
                        "run_id": spec["run_id"],
                        "index": spec["index"],
                        "warnings": list(outcome["warnings"]),
                    }
                )
            if outcome.get("changed_during_run"):
                changed_runs[0] += 1
                changed.update(dict.fromkeys(outcome["changed_during_run"]))
            if status == "done":
                counts["done"] += 1
                run_s = _run_time(outcome)
                if run_s is not None:
                    run_times.append(run_s)
                if outcome.get("outcome_errors"):
                    outcome_errors.append(
                        {
                            "run_id": spec["run_id"],
                            "index": spec["index"],
                            "errors": dict(outcome["outcome_errors"]),
                        }
                    )
            elif status == "pending":
                counts["pending"] += 1
                not_started.append(entry)
            else:
                counts["failed"] += 1
                failures.append(entry)
            finished = sum(counts.values()) - len(triage.failed)
            wall, cpu = outcome.get("wall_s"), outcome.get("cpu_s")
            line = (
                f"[{finished:>{width}}/{pending}] {status:<6} run {spec['index']} "
                f"({spec['run_id']})"
                + (f" in {wall:.2f} s" if isinstance(wall, int | float) else "")
                + (f" (cpu {cpu:.2f} s)" if isinstance(cpu, int | float) else "")
                + (f": {outcome.get('error')}" if status != "done" else "")
                + (
                    f" ({len(outcome['outcome_errors'])} outcome error"
                    f"{'s' * (len(outcome['outcome_errors']) != 1)})"
                    if status == "done" and outcome.get("outcome_errors")
                    else ""
                )
            )
            logger.info("Study %r: %s", self.name, line)
            if progress:
                print(line, file=sys.stderr, flush=True)

        stale_info = {
            "stale": len(stale_runs),
            "stale_kept": stale == "keep" and bool(stale_runs),
            "stale_reasons": stale_text,
        }
        if choice.kind == "slurm":
            return self._submit_slurm(
                plan, store, choice, triage, failures, t0, outcome_errors, stale_info
            )
        try:
            run_specs(
                triage.pending,
                choice,
                scenario=self.scenario if choice.in_process else None,
                outcomes=self._outcomes if choice.in_process else None,
                on_result=on_result,
            )
        finally:
            store.write_status(plan.specs)
        failures.sort(key=lambda failure: failure["index"])
        not_started.sort(key=lambda entry: entry["index"])
        outcome_errors.sort(key=lambda entry: entry["index"])
        run_warnings.sort(key=lambda entry: entry["index"])
        return StudyReport(
            total=len(plan),
            done=counts["done"],
            skipped=len(triage.skipped),
            failed=counts["failed"],
            failures=failures,
            pending=counts["pending"],
            not_started=not_started,
            out=out_dir,
            wall_s=_time.perf_counter() - t0,
            executor=choice.describe(),
            outcome_errors=outcome_errors,
            hints=_pool_hints(choice, run_times),
            changed_during_run=changed_runs[0],
            changed_files=", ".join(list(changed)[:5])
            + (f" and {len(changed) - 5} more" if len(changed) > 5 else ""),
            warnings=run_warnings,
            **stale_info,
        )

    def _log_git_mix(self, store: StudyStore, skipped: list[dict]) -> None:
        commits: dict[str, int] = {}
        dirty = 0
        for spec in skipped:
            git = (store.read_manifest(spec["run_id"]) or {}).get("git") or {}
            if git.get("commit"):
                commits[git["commit"]] = commits.get(git["commit"], 0) + 1
            dirty += bool(git.get("dirty"))
        if len(commits) > 1 or dirty:
            logger.info(
                "Study %r: the finished runs come from %d git commit(s) (%s)%s; their "
                "code and data fingerprints match the current files",
                self.name,
                len(commits),
                ", ".join(commit[:8] for commit in commits),
                f", {dirty} from a working tree with uncommitted changes"
                if dirty
                else "",
            )

    def _submit_slurm(
        self,
        plan: StudyPlan,
        store: StudyStore,
        choice: Any,
        triage: Any,
        failures: list[dict],
        t0: float,
        outcome_errors: list[dict],
        stale_info: dict,
    ) -> StudyReport:
        try:
            submission = choice.executor.dispatch(
                triage.pending, store.out, job_name=self.name
            )
        finally:
            store.write_status(plan.specs)
        if submission.runs:
            logger.info(
                "Study %r: %d run(s) %s",
                self.name,
                submission.runs,
                f"submitted as Slurm job {', '.join(submission.job_ids)}"
                if submission.job_id is not None
                else f"written to {submission.script}",
            )
        failures.sort(key=lambda failure: failure["index"])
        return StudyReport(
            total=len(plan),
            done=0,
            skipped=len(triage.skipped),
            failed=len(triage.failed),
            failures=failures,
            pending=submission.runs,
            out=store.out,
            wall_s=_time.perf_counter() - t0,
            executor=choice.describe(),
            submitted=submission.runs,
            job_id=submission.job_id,
            script=submission.script,
            job_ids=list(submission.job_ids),
            outcome_errors=outcome_errors,
            **stale_info,
        )


def _display_path(path: Path) -> str:
    """*path* relative to the working directory when it lies below it."""
    try:
        return str(Path(path).relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _run_time(outcome: Mapping) -> float | None:
    """The time of a run without the import of the scenario (``wall_s - load_s``)."""
    wall, load = outcome.get("wall_s"), outcome.get("load_s")
    if not isinstance(wall, int | float):
        return None
    return max(0.0, wall - (load if isinstance(load, int | float) else 0.0))


#: Start-up cost of a worker process assumed when the pool did not measure it.
_ASSUMED_SPAWN_S = 2.0


def _pool_hints(choice: Any, run_times: Sequence[float]) -> list[str]:
    """Advice for a managed pool from the measured run and worker start-up times.

    *run_times* are the run times without the scenario import (see
    :func:`_run_time`); the start-up cost comes from ``choice.usage``.
    """
    if choice.kind != "process" or len(run_times) < 2:
        return []
    ordered = sorted(run_times)
    run_s = ordered[len(ordered) // 2]
    spawn_s = choice.usage.get("spawn_s") or _ASSUMED_SPAWN_S
    hints = []
    total = sum(run_times)
    if total < choice.workers * spawn_s and run_s < spawn_s:
        hints.append(
            f"sequential would have been faster: the {len(run_times)} runs took "
            f"{total:.1f} s in all, less than starting {choice.workers} worker "
            f"processes (about {spawn_s:.1f} s each); run short studies without "
            "--workers"
        )
    setting = choice.runs_per_worker
    # the share of a worker's time spent starting it, with *setting* runs each
    share = spawn_s / (spawn_s + setting * run_s) if isinstance(setting, int) else 0.0
    if share >= 1 / 3:
        suggested = max(10, min(200, math.ceil(10 * spawn_s / max(run_s, 1e-3))))
        hints.append(
            f"runs took {run_s:.2f} s (median, without the start-up of a worker "
            f"process, about {spawn_s:.1f} s); with runs_per_worker={setting} about "
            f"{share:.0%} of the worker time goes into starting workers: reuse them "
            "with "
            f"runs_per_worker={suggested} (--runs-per-worker {suggested}, or "
            f"'executor: {{runs_per_worker: {suggested}}}' in the study file) or "
            "'auto' (the default)"
        )
    return hints


def _leaves(data: Any, prefix: str = "") -> dict[str, Any]:
    """``{dotted path: value}`` of the leaves of plain params (a dict holds its fields)."""
    if isinstance(data, Mapping) and data:
        found: dict[str, Any] = {}
        for key, value in data.items():
            found.update(_leaves(value, f"{prefix}.{key}" if prefix else str(key)))
        return found
    return {prefix: data} if prefix else {}


def _params_changes(
    old_specs: Sequence[Mapping], new_specs: Sequence[Mapping], limit: int = 5
) -> str:
    """What changed in the resolved params between two plans of the same definition.

    Runs are matched by their factor values and replication; reports new and
    removed parameter paths and changed values, e.g. ``"new fields
    battery.kind; battery.charge_kw 1.0 -> 2.0"``.
    """

    def key(spec: Mapping) -> str:
        return canonical_json([spec.get("factors"), spec.get("replication")])

    old_by_key = {key(spec): spec for spec in old_specs}
    old_paths: dict[str, None] = {}
    new_paths: dict[str, None] = {}
    changed: dict[str, str] = {}
    for spec in old_specs:
        old_paths.update(dict.fromkeys(_leaves(spec.get("params") or {})))
    for spec in new_specs:
        leaves = _leaves(spec.get("params") or {})
        new_paths.update(dict.fromkeys(leaves))
        before = old_by_key.get(key(spec))
        if before is None:
            continue
        old_leaves = _leaves(before.get("params") or {})
        for path, value in leaves.items():
            if path in old_leaves and path not in changed:
                if canonical_json(old_leaves[path]) != canonical_json(value):
                    changed[path] = (
                        f"{path} {_short(old_leaves[path], 30)} -> {_short(value, 30)}"
                    )

    def listed(paths: Sequence[str]) -> str:
        shown = ", ".join(paths[:limit])
        return shown + (f" and {len(paths) - limit} more" if len(paths) > limit else "")

    added = [path for path in new_paths if path not in old_paths]
    removed = [path for path in old_paths if path not in new_paths]
    parts = []
    if added:
        parts.append(f"new field{'s' * (len(added) != 1)} {listed(added)}")
    if removed:
        parts.append(f"removed field{'s' * (len(removed) != 1)} {listed(removed)}")
    if changed:
        parts.append(f"changed values {listed(list(changed.values()))}")
    return "; ".join(parts)


#: Keys of ``study.yaml`` that define the plan (compared when a plan is replaced).
_PLAN_KEYS = (
    "scenario",
    "base",
    "overrides",
    "uncertainties",
    "levers",
    "design",
    "replications",
    "common_random_numbers",
    "seed",
    "subexperiments",
)


def _definition_changes(old: Mapping, new: Mapping) -> str:
    """``"overrides {} -> {'town.radius': 2.5}"`` for the plan keys that differ."""
    changes = []
    for key in _PLAN_KEYS:
        before, after = old.get(key), new.get(key)
        if canonical_json(to_plain(before, default=repr)) != canonical_json(
            to_plain(after, default=repr)
        ):
            changes.append(f"{key} {_short(before, 60)} -> {_short(after, 60)}")
    return ", ".join(changes)

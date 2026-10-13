"""
The study folder: layout, run status, resume checks and loading.

Layout of a study folder::

    <out>/study.yaml                 resolved study definition (scenario, factors, design,
                                     seed, replications, outcomes, mango version, created)
    <out>/runs.jsonl                 the run table: one fully resolved run spec per line
    <out>/status.csv                 derived index: index, run_id, status, wall_s, cpu_s,
                                     warnings, error (a snapshot: study.run() and
                                     mango-scenario status rewrite it)
    <out>/environments/<hash>.txt    installed packages of each environment runs used
    <out>/git/<commit>-<sha>.diff    uncommitted changes of runs made from a dirty tree
    <out>/.lock                      held while a study.run() uses the folder
    <out>/plans/<plan_id>/           earlier plans of the study (study.yaml, runs.jsonl)
    <out>/runs/<run_id>/spec.json
    <out>/runs/<run_id>/config.resolved.yaml
    <out>/runs/<run_id>/timeseries.parquet | timeseries.csv
    <out>/runs/<run_id>/logs/<name>.parquet | .csv   event logs (ctx.log), if any
    <out>/runs/<run_id>/metrics.json     run-level metrics, if any
    <out>/runs/<run_id>/outcomes.json
    <out>/runs/<run_id>/manifest.json    written last, atomically (the commit marker)
    <out>/runs/<run_id>/manifest.prev.json  the manifest before a stale or forced re-run
    <out>/runs/<run_id>/error.txt        only for failed runs

Run folders are named by the content-hash run id, so a changed parameter or
seed never reuses an old folder.  A run is *done* when its manifest says so,
*failed* when the manifest records a failure and *partial* when the folder
has no manifest (an interrupted run).  A done run is *stale* when the code or
data files its manifest lists changed since (see
:mod:`mango.scenario.freshness`).

Example::

    df = load_study("results/fleet_comparison")
    df.groupby(["algorithm", "ev.n"])[["self_consumption"]].mean()
    df.failed()                       # rows of failed runs
    df.run(0).ts("import_kw")         # the RunResult of run 0
    df.timeseries(variables=["soc_kwh"])
    df.log("trades")                  # the event log "trades" of every done run
    load_study("results/sobol_pv").sobol_indices()   # S1 and ST of a Sobol study
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import logging
import numbers
import os
import re
import shutil
import socket
import time as _time
import warnings
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ._util import (
    canonical_json,
    content_hash,
    did_you_mean,
    read_json,
    require_pandas,
    utc_now,
    write_json_atomic,
    write_text_atomic,
)
from .errors import StudyError
from .freshness import stale_reasons
from .result import (
    METRICS_FILE,
    concat_frames,
    load_run,
    read_run_frame,
    read_run_log,
    read_run_variables,
)
from .runner import (
    ERROR_FILE,
    MANIFEST_FILE,
    OUTCOMES_FILE,
    PREVIOUS_MANIFEST_FILE,
    compute_outcomes,
    load_scenario_ref,
    resolve_outcomes,
)

if TYPE_CHECKING:
    import pandas

    from .result import RunResult

__all__ = [
    "StudyStore",
    "Triage",
    "select_run",
    "load_study",
    "StudyFrame",  # noqa: F822 (created on first access, see __getattr__)
    "STUDY_FILE",
    "RUNS_FILE",
    "STATUS_FILE",
    "RUNS_DIR",
    "LOCK_FILE",
    "STUDY_PLANS_DIR",
    "check_outcome_names",
    "STATUS_COLUMNS",
    "FRAME_COLUMNS",
    "DESIGN_COLUMNS",
]

logger = logging.getLogger(__name__)

STUDY_FILE = "study.yaml"
RUNS_FILE = "runs.jsonl"
STATUS_FILE = "status.csv"
RUNS_DIR = "runs"
LOCK_FILE = ".lock"
STUDY_PLANS_DIR = "plans"

#: Columns of ``status.csv``.
STATUS_COLUMNS: tuple[str, ...] = (
    "index",
    "run_id",
    "status",
    "wall_s",
    "cpu_s",
    "warnings",
    "error",
)

#: Leading columns of a :class:`~mango.scenario.StudyFrame` (design columns,
#: factors, labels, override columns and outcomes follow).
FRAME_COLUMNS: tuple[str, ...] = (
    "index",
    "run_id",
    "replication",
    "point",
    "status",
    "wall_s",
    "cpu_s",
    "stale",
    "outcome_errors",
    "git_commit",
    "git_dirty",
)

#: Columns a :class:`~mango.scenario.StudyFrame` has after :data:`FRAME_COLUMNS`
#: when the design uses them: the subexperiment (block) of a run and the
#: sample number of a sampling design.
DESIGN_COLUMNS: tuple[str, ...] = ("subexperiment", "sample")


def _padded(frames: list[pandas.DataFrame]) -> list[pandas.DataFrame]:
    """Extend each run's series to the later times of the others (``timeseries(pad=True)``)."""
    import numpy as np

    pd = _pandas()
    time_columns = ["time"] + (
        ["datetime"] if all("datetime" in frame.columns for frame in frames) else []
    )
    grid = (
        pd.concat(
            [
                frame[time_columns].assign(_variable=frame["variable"].astype(str))
                for frame in frames
            ],
            ignore_index=True,
        )
        .drop_duplicates(["_variable", "time"])
        .sort_values("time", kind="stable")
    )
    keys = ["variable", "group", "entity"]
    padded = []
    for given in frames:
        frame = given.reset_index(drop=True)
        if not len(frame):
            padded.append(frame.assign(padded=np.zeros(0, dtype=bool)))
            continue
        name = frame["variable"].astype(str)
        end = frame["time"].groupby(name).transform("max")
        last = frame.loc[
            frame.groupby(keys, observed=True, sort=False)["time"].idxmax()
        ]
        last = last[last["time"].to_numpy() >= end.loc[last.index].to_numpy()]
        later = (
            last.drop(columns=time_columns)
            .assign(_variable=name.loc[last.index], _end=last["time"].to_numpy())
            .merge(grid, on="_variable", how="inner")
        )
        later = later[later["time"] > later["_end"]]
        later = later.drop(columns=["_variable", "_end"])[list(frame.columns)]
        for column in frame.columns:
            if isinstance(frame[column].dtype, pd.CategoricalDtype):
                later[column] = pd.Categorical(
                    later[column], categories=frame[column].cat.categories
                )
        if len(later):
            combined = pd.concat(
                [frame.assign(padded=False), later.assign(padded=True)],
                ignore_index=True,
            ).sort_values("time", kind="stable", ignore_index=True)
        else:
            combined = frame.assign(padded=False)
        padded.append(combined)
    return padded


def _pandas():
    return require_pandas("Loading studies needs")


def check_outcome_names(names: Iterable[str], factor_paths: Iterable[str]) -> None:
    """Raise when an outcome name would clash with a column of the study table.

    :param names: the outcome names
    :param factor_paths: the dotted factor paths of the study
    :raises StudyError: naming the clash
    """
    paths = set(factor_paths)
    for name in names:
        if (
            name in FRAME_COLUMNS
            or name in DESIGN_COLUMNS
            or name == "plan"
            or name in paths
            or name.endswith(":label")
        ):
            raise StudyError(
                f"Outcome name {name!r} clashes with a column of the study table "
                f"({list(FRAME_COLUMNS + DESIGN_COLUMNS)}, factor paths, "
                "'<factor>:label'); rename it."
            )


def _pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or os.name == "nt":  # no safe check on Windows
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


_RUN_INDEX = re.compile(r"run[_-]?0*(\d+)", re.IGNORECASE)


def select_run(
    specs: Sequence[Mapping], selector: int | str, *, where: str = "the study"
) -> dict:
    """The spec a run selector names: a run index or a run id prefix.

    Rules: an int, ``run_N``, ``run-N`` or ``runN`` is always the index N; a
    bare number without a leading zero (``"7"``) is an index when the plan
    has it, else a run id prefix; a bare number with a leading zero
    (``"07"``, ``"0777"``: run ids are hex) is a run id prefix when one
    matches, else an index; anything else is a run id prefix.

    :param specs: the run specs (plan order)
    :param selector: the selector
    :param where: names the study in messages
    :return: the run spec
    :raises StudyError: when nothing or several runs match, naming both
        readings of a number

    Example::

        select_run(specs, "run_7")["index"]     # 7
        select_run(specs, "0777")["run_id"]     # "0777efee6df2"
    """
    by_index = {spec.get("index"): spec for spec in specs}
    last = f"runs 0..{len(specs) - 1}" if specs else "no runs"
    if isinstance(selector, bool) or not isinstance(selector, int | str):
        raise StudyError(f"A run is selected by index or run id, got {selector!r}.")
    if isinstance(selector, int):
        if selector in by_index:
            return dict(by_index[selector])
        raise StudyError(f"{where} has no run {selector} ({last}).")
    text = selector.strip()
    named = _RUN_INDEX.fullmatch(text)
    if named:
        index = int(named.group(1))
        if index in by_index:
            return dict(by_index[index])
        raise StudyError(f"{where} has no run {index} ({last}).")
    matches = [spec for spec in specs if str(spec.get("run_id", "")).startswith(text)]
    digits = text.isdigit()
    index = int(text) if digits else None
    padded = digits and len(text) > 1 and text.startswith("0")
    if digits and not padded and index in by_index:
        return dict(by_index[index])
    if len(matches) == 1:
        return dict(matches[0])
    if len(matches) > 1:
        raise StudyError(
            f"Run id prefix {text!r} is ambiguous in {where} ({len(matches)} runs); give "
            "more characters."
        )
    if padded and index in by_index:
        return dict(by_index[index])
    if digits:
        raise StudyError(
            f"{where} has no run {index} ({last}) and no run id starting with "
            f"{text!r}; run_<n> selects by index."
        )
    raise StudyError(f"{where} has no run {text!r} (no run id starts with it).")


@dataclass
class Triage:
    """Run specs sorted by what a study run has to do with them.

    :param pending: specs to execute (partial and retried folders are cleaned)
    :param skipped: specs whose runs are already done
    :param failed: ``(spec, manifest)`` of failed runs that are kept
        (``retry_failed=False``)
    """

    pending: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    failed: list[tuple[dict, dict]] = field(default_factory=list)


class StudyStore:
    """Read and write the files of one study folder.

    :param out: the study folder

    Example::

        store = StudyStore("results/fleet_comparison")
        store.run_state("3f2a9c1b7d4e")      # "done", "failed", "partial" or "missing"
        store.write_status()
    """

    def __init__(self, out: str | os.PathLike):
        self.out = Path(out).absolute()

    def __repr__(self) -> str:
        return f"StudyStore({str(self.out)!r})"

    @property
    def study_file(self) -> Path:
        return self.out / STUDY_FILE

    @property
    def runs_file(self) -> Path:
        return self.out / RUNS_FILE

    @property
    def status_file(self) -> Path:
        return self.out / STATUS_FILE

    @property
    def runs_dir(self) -> Path:
        return self.out / RUNS_DIR

    @property
    def plans_dir(self) -> Path:
        return self.out / STUDY_PLANS_DIR

    def run_dir(self, run_id: str) -> Path:
        """Return the folder of run *run_id*."""
        return self.runs_dir / run_id

    @contextlib.contextmanager
    def lock(self) -> Iterator[Path]:
        """Hold the folder's lock file ``.lock`` for the duration of a study run.

        A lock left behind by a process that no longer runs on this host is
        taken over.

        :raises StudyError: when another process holds the lock (the message
            names the process, its host and since when)

        Example::

            with store.lock():
                ...   # run the pending runs
        """
        self.out.mkdir(parents=True, exist_ok=True)
        path = self.out / LOCK_FILE
        holder = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "since": utc_now(),
        }
        for attempt in range(2):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if attempt == 0 and self._lock_is_stale(path):
                    logger.warning("Taking over the stale study lock %s", path)
                    path.unlink(missing_ok=True)
                    continue
                other = read_json(path) or {}
                raise StudyError(
                    f"The study folder {self.out} is in use by another study.run() "
                    f"(process {other.get('pid', '?')} on {other.get('host', '?')} "
                    f"since {other.get('since', '?')}). Wait until it finishes; if "
                    f"that process no longer runs, delete {path}."
                ) from None
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(holder, handle)
            break
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)

    @staticmethod
    def _lock_is_stale(path: Path) -> bool:
        other = read_json(path)
        if not isinstance(other, dict):  # unreadable: maybe being written right now
            try:
                age = _time.time() - path.stat().st_mtime
            except OSError:
                return True
            return age > 10.0
        return other.get("host") == socket.gethostname() and not _pid_alive(
            other.get("pid")
        )

    # -- study definition and run table ----------------------------------------------

    def read_definition(self) -> dict | None:
        """Return the contents of ``study.yaml`` (``None`` if missing).

        :raises StudyError: if the file is not valid YAML
        """
        if not self.study_file.exists():
            return None
        try:
            data = yaml.safe_load(self.study_file.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise StudyError(f"{self.study_file} is not valid YAML: {exc}") from None
        return data if isinstance(data, dict) else None

    def persisted_seed(self) -> int | None:
        """Return the seed recorded in ``study.yaml`` (``None`` if there is none)."""
        definition = self.read_definition() or {}
        seed = definition.get("seed")
        return seed if isinstance(seed, int) and not isinstance(seed, bool) else None

    def write_definition(self, definition: Mapping) -> Path:
        """Write ``study.yaml``, keeping the ``created`` time of an existing file."""
        now = utc_now()
        try:
            previous = self.read_definition() or {}
        except StudyError:
            previous = {}
        data = dict(definition)
        data["created"] = previous.get("created", now)
        data["updated"] = now
        write_text_atomic(
            self.study_file,
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        )
        return self.study_file

    def write_runs(self, specs: Iterable[Mapping]) -> Path:
        """Write the run table ``runs.jsonl`` (one spec per line, plan order)."""
        lines = [json.dumps(dict(spec), ensure_ascii=False) for spec in specs]
        write_text_atomic(self.runs_file, "".join(line + "\n" for line in lines))
        return self.runs_file

    def read_runs(self) -> list[dict]:
        """Read ``runs.jsonl``.

        :raises FileNotFoundError: if the folder has no run table
        """
        if not self.runs_file.exists():
            if self.out.suffix.lower() in (".yaml", ".yml", ".json") and (
                self.out.is_file()
            ):
                raise FileNotFoundError(
                    f"{self.out} is a file, not a study folder: load_study() reads "
                    "the 'out' folder a study wrote (e.g. results/<study name>); "
                    "load a study file with Study.from_yaml()."
                )
            raise FileNotFoundError(
                f"{self.out} is not a study folder: {RUNS_FILE} is missing. Run the "
                "study first (study.run(out=...))."
            )
        specs = []
        text = self.runs_file.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                specs.append(json.loads(line))
            except ValueError as exc:
                raise StudyError(
                    f"{self.runs_file}, line {number} is not valid JSON: {exc}"
                ) from None
        return specs

    # -- earlier plans -------------------------------------------------------------------

    def archive_plan(self) -> str | None:
        """Copy the current ``study.yaml`` and ``runs.jsonl`` to ``plans/<plan id>/``.

        The plan id hashes the run ids of the run table, so archiving the
        same plan twice keeps one copy.

        :return: the plan id (``None`` when the folder has no run table)

        Example::

            store.archive_plan()          # "4b1f0c2d9e3a"
        """
        if not self.runs_file.exists():
            return None
        specs = self.read_runs()
        plan_id = content_hash([spec.get("run_id") for spec in specs])
        folder = self.plans_dir / plan_id
        folder.mkdir(parents=True, exist_ok=True)
        write_text_atomic(
            folder / RUNS_FILE, self.runs_file.read_text(encoding="utf-8")
        )
        if self.study_file.exists():
            write_text_atomic(
                folder / STUDY_FILE, self.study_file.read_text(encoding="utf-8")
            )
        return plan_id

    def plan_ids(self) -> list[str]:
        """Return the ids of the earlier plans in ``plans/``, oldest first.

        Example::

            store.plan_ids()              # ["4b1f0c2d9e3a"]
        """
        if not self.plans_dir.is_dir():
            return []
        folders = [
            folder
            for folder in self.plans_dir.iterdir()
            if (folder / RUNS_FILE).is_file()
        ]
        folders.sort(key=lambda folder: (folder / RUNS_FILE).stat().st_mtime_ns)
        return [folder.name for folder in folders]

    def read_plan(self, plan_id: str) -> tuple[dict, list[dict]]:
        """Return ``(definition, specs)`` of an earlier plan.

        :raises KeyError: for unknown plan ids (listing the known ones)
        """
        folder = self.plans_dir / str(plan_id)
        if not (folder / RUNS_FILE).is_file():
            raise KeyError(
                f"No plan {plan_id!r} in {self.plans_dir}; plans: {self.plan_ids()}."
            )
        return StudyStore(folder).read_definition() or {}, StudyStore(
            folder
        ).read_runs()

    # -- run folders ---------------------------------------------------------------------

    def reset_run(self, run_id: str) -> None:
        """Clear a run folder for a new execution, keeping its manifest as ``manifest.prev.json``.

        Example::

            store.reset_run("3f2a9c1b7d4e")       # a stale run is run again
        """
        folder = self.run_dir(run_id)
        manifest = folder / MANIFEST_FILE
        previous = manifest.read_text(encoding="utf-8") if manifest.exists() else None
        shutil.rmtree(folder, ignore_errors=True)
        if previous is not None:
            folder.mkdir(parents=True, exist_ok=True)
            write_text_atomic(folder / PREVIOUS_MANIFEST_FILE, previous)

    def read_manifest(self, run_id: str) -> dict | None:
        """Return the manifest of a run (``None`` if missing or unreadable)."""
        data = read_json(self.run_dir(run_id) / MANIFEST_FILE)
        return data if isinstance(data, dict) else None

    def read_metrics(self, run_id: str) -> dict:
        """The run-level metrics of a run (``metrics.json``; empty when it has none)."""
        data = read_json(self.run_dir(run_id) / METRICS_FILE)
        return data if isinstance(data, dict) else {}

    def read_outcomes(self, run_id: str) -> dict:
        """Return the stored outcomes of a run (``{}`` if missing or unreadable)."""
        data = read_json(self.run_dir(run_id) / OUTCOMES_FILE)
        return data if isinstance(data, dict) else {}

    def run_state(self, run_id: str) -> str:
        """Return ``"done"``, ``"failed"``, ``"partial"`` or ``"missing"``.

        A folder whose manifest is unreadable or names another run counts as
        partial.
        """
        folder = self.run_dir(run_id)
        if not folder.exists():
            return "missing"
        manifest = self.read_manifest(run_id)
        if manifest is None or manifest.get("run_id") != run_id:
            return "partial"
        status = manifest.get("status")
        return status if status in ("done", "failed") else "partial"

    def triage(self, specs: Iterable[Mapping], *, retry_failed: bool = True) -> Triage:
        """Decide per spec whether it runs, and clean folders that will be re-run.

        Done runs are skipped; partial folders (no manifest) are deleted and
        re-run; failed runs are deleted and re-run when *retry_failed*, else
        kept as failed.

        :param specs: the run specs
        :param retry_failed: re-run failed runs
        :return: the :class:`Triage`
        """
        triage = Triage()
        for spec in specs:
            run_id = spec["run_id"]
            state = self.run_state(run_id)
            if state == "done":
                triage.skipped.append(dict(spec))
                continue
            if state == "failed" and not retry_failed:
                triage.failed.append((dict(spec), self.read_manifest(run_id) or {}))
                continue
            if state in ("failed", "partial"):
                logger.info("Removing %s run folder %s", state, self.run_dir(run_id))
                shutil.rmtree(self.run_dir(run_id), ignore_errors=True)
            triage.pending.append(dict(spec))
        return triage

    # -- status index --------------------------------------------------------------------

    def status_rows(
        self, specs: Sequence[Mapping] | None = None, *, details: bool = False
    ) -> list[dict]:
        """Return one status row per spec (default: the run table), from the manifests.

        :param specs: the run specs (default: the run table)
        :param details: add ``outcome_errors`` (``{outcome: error}`` of a done
            run, else ``None``), ``stale`` (the
            :class:`~mango.scenario.freshness.StaleReason` list of a done run
            whose code or data files changed since, else ``[]``), ``git``
            (the git state the run recorded), ``warning_texts`` (the
            distinct warnings the run issued), ``changed_during_run`` (files
            that changed while it ran) and ``certified`` (its
            re-certifications, see :meth:`certify`) to each row
        :return: rows with ``index, run_id, status, wall_s, cpu_s, warnings``
            (the number of distinct warnings the run issued) and ``error``

        Example::

            [row["status"] for row in store.status_rows()]   # ["done", "failed", "pending"]
        """
        specs = self.read_runs() if specs is None else specs
        rows = []
        for spec in specs:
            manifest = self.read_manifest(spec["run_id"]) or {}
            status = manifest.get("status")
            if manifest.get("run_id") != spec["run_id"] or status not in (
                "done",
                "failed",
            ):
                status = "pending"
            known = status != "pending"
            texts = manifest.get("warnings") if known else None
            texts = [str(text) for text in texts] if isinstance(texts, list) else []
            row = {
                "index": spec.get("index"),
                "run_id": spec["run_id"],
                "status": status,
                "wall_s": manifest.get("wall_s") if known else None,
                "cpu_s": manifest.get("cpu_s") if known else None,
                "warnings": len(texts) if known else None,
                "error": manifest.get("error") if status == "failed" else None,
            }
            if details:
                done = status == "done"
                errors = manifest.get("outcome_errors") if done else None
                roots = [spec.get("project_root"), manifest.get("project_root")]
                row["outcome_errors"] = dict(errors) if errors else None
                row["stale"] = stale_reasons(manifest, roots=roots) if done else []
                row["git"] = manifest.get("git") if known else None
                row["warning_texts"] = texts
                row["changed_during_run"] = list(
                    manifest.get("changed_during_run") or ()
                )
                row["certified"] = list(manifest.get("certified") or ())
            rows.append(row)
        return rows

    def write_status(self, specs: Sequence[Mapping] | None = None) -> Path:
        """Rewrite ``status.csv`` from the run folders.

        Example::

            store.write_status(plan.specs)
        """
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=STATUS_COLUMNS,
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in self.status_rows(specs):
            writer.writerow(
                {key: ("" if value is None else value) for key, value in row.items()}
            )
        write_text_atomic(self.status_file, buffer.getvalue())
        return self.status_file

    # -- re-certification ----------------------------------------------------------------

    def certify(
        self,
        run_ids: Iterable[str] | None = None,
        *,
        reason: str,
        scenario_fingerprint: str | None = None,
    ) -> list[str]:
        """Mark stale done runs as current: record today's file digests and the reason.

        A run is stale when code or data files it used changed since (see
        :func:`~mango.scenario.freshness.stale_reasons`).  When you know the
        change does not affect it (a refactoring, a new helper; a replay with
        ``mango-scenario rerun`` gave an identical checksum), certifying the
        run updates ``code_files``/``data_files`` (and the scenario
        fingerprint, when given) to the current state, so ``study.run()``
        no longer repeats it.  The old manifest is kept as
        ``manifest.prev.json``; ``manifest["certified"]`` lists every
        certification with its time, reason and the files that had changed.

        :param run_ids: the runs to certify (default: every run of the run
            table); runs that are not done or not stale are left unchanged
        :param reason: why the runs are still valid (required, kept in the
            manifest)
        :param scenario_fingerprint: the current scenario fingerprint
            (:meth:`~mango.scenario.Scenario.fingerprint`), which also clears
            a changed scenario definition
        :return: the run ids that were certified
        :raises StudyError: without a reason

        Example::

            store.certify(reason="refactored helpers; rerun of run 3 identical")
        """
        from .freshness import current_entries, describe_stale

        if not isinstance(reason, str) or not reason.strip():
            raise StudyError(
                "certify needs a reason (e.g. 'docstring edits only' or 'rerun "
                "identical'); it is kept in each run's manifest."
            )
        specs = {spec["run_id"]: spec for spec in self.read_runs()}
        wanted = list(specs) if run_ids is None else list(run_ids)
        certified = []
        for run_id in wanted:
            if self.run_state(run_id) != "done":
                continue
            manifest = self.read_manifest(run_id) or {}
            spec = specs.get(run_id, {})
            roots = [spec.get("project_root"), manifest.get("project_root")]
            reasons = stale_reasons(
                manifest, roots=roots, scenario_fingerprint=scenario_fingerprint
            )
            if not reasons and not manifest.get("changed_during_run"):
                continue
            folder = self.run_dir(run_id)
            write_text_atomic(
                folder / PREVIOUS_MANIFEST_FILE,
                (folder / MANIFEST_FILE).read_text(encoding="utf-8"),
            )
            updated = dict(manifest)
            updated.update(current_entries(manifest, roots=roots))
            updated.pop("changed_during_run", None)
            if scenario_fingerprint is not None:
                updated["scenario_fingerprint"] = scenario_fingerprint
            updated["certified"] = [
                *(manifest.get("certified") or []),
                {
                    "at": utc_now(),
                    "reason": reason.strip(),
                    "changes": describe_stale([reasons])
                    or "code or data changed while the run executed",
                },
            ]
            write_json_atomic(folder / MANIFEST_FILE, updated)
            certified.append(run_id)
        return certified

    # -- outcomes ------------------------------------------------------------------------

    def recompute_outcomes(
        self,
        outcomes: Any,
        specs: Sequence[Mapping] | None = None,
        *,
        scenario: Any = None,
    ) -> int:
        """Recompute outcomes of done runs from their stored time series.

        The recomputation sees what a loaded run offers: the time series,
        the resolved config and params, the manifest; ``run.agent()`` and
        ``run.world`` are not available (outcomes that use them can only be
        computed while the study runs).  For each done run, the values that
        were computed replace the stored ones in ``outcomes.json`` and clear
        their ``outcome_errors`` entries in the manifest.  A recomputation
        that raises never destroys a stored value: the stored value and its
        manifest entry stay as they were; only an outcome without a stored
        value gets ``None`` and its error in ``outcome_errors``.  One warning
        per outcome summarises the failures.

        :param outcomes: as for :class:`~mango.scenario.Study` ``outcomes``
        :param specs: the runs (default: the run table)
        :param scenario: the scenario used to rebuild typed ``run.params``
            (default: imported from ``study.yaml`` when possible)
        :return: the number of runs updated

        Example::

            store.recompute_outcomes({"peak_kw": peak_kw})      # 120
        """
        table = resolve_outcomes(outcomes)
        specs = self.read_runs() if specs is None else specs
        if scenario is None:
            scenario = self._scenario()
        updated = 0
        failures: dict[str, list[str]] = {}
        kept_values: dict[str, int] = {}
        for spec in specs:
            run_id = spec["run_id"]
            if self.run_state(run_id) != "done":
                continue
            folder = self.run_dir(run_id)
            result = _load_result(folder, scenario)
            values, errors = compute_outcomes(result, table, log=False)
            stored = read_json(folder / OUTCOMES_FILE)
            stored = dict(stored) if isinstance(stored, dict) else {}
            manifest = self.read_manifest(run_id) or {}
            manifest_errors = dict(manifest.get("outcome_errors") or {})
            for name, value in values.items():
                if name not in errors:
                    stored[name] = value
                    manifest_errors.pop(name, None)
                    continue
                failures.setdefault(name, []).append(errors[name])
                if stored.get(name) is not None:
                    kept_values[name] = kept_values.get(name, 0) + 1
                else:  # nothing to keep: a new outcome, or one that failed before
                    stored[name] = None
                    manifest_errors[name] = errors[name]
            write_json_atomic(folder / OUTCOMES_FILE, stored)
            manifest["outcome_errors"] = manifest_errors
            write_json_atomic(folder / MANIFEST_FILE, manifest)
            updated += 1
        for name, messages in failures.items():
            kept = kept_values.get(name, 0)
            hint = (
                " Outcomes that use run.agent() or run.world can only be computed "
                "while the study runs; record the quantity with a recorder to "
                "recompute it later."
                if any("no world" in message for message in messages)
                else ""
            )
            warnings.warn(
                f"Outcome {name!r} could not be recomputed for {len(messages)} of "
                f"{updated} done runs ({messages[0]})"
                + (f"; their stored values were kept for {kept} runs" if kept else "")
                + f".{hint}",
                UserWarning,
                stacklevel=3,
            )
        return updated

    def _scenario(self) -> Any:
        try:
            ref = (self.read_definition() or {}).get("scenario")
        except StudyError:
            return None
        if not isinstance(ref, str) or ":" not in ref:
            return None
        try:
            return load_scenario_ref(ref)
        except StudyError as exc:
            logger.info("Loading runs without typed params: %s", exc)
            return None


def _load_result(folder: Path, scenario: Any) -> RunResult:
    if scenario is not None:
        try:
            return load_run(folder, scenario=scenario)
        except Exception as exc:
            logger.warning(
                "Cannot rebuild typed params of %s (%s); run.params is None, use "
                "run.config.",
                folder,
                exc,
            )
    return load_run(folder)


# --------------------------------------------------------------------------- frame


class _StudyFrameMethods:
    """A ``pandas.DataFrame`` of a study: one row per planned run.

    Columns: ``index, run_id, replication, point, status, wall_s, cpu_s,
    stale, outcome_errors, git_commit, git_dirty``, ``subexperiment`` and
    ``sample`` when the design has them, one column per factor (named by its
    dotted path), ``"<factor>:label"`` for labeled factors, one column per
    override and one column per outcome (see :func:`load_study`).  Slices
    keep the type and the attributes ``study_path`` (the study folder),
    ``study_spec`` (the contents of ``study.yaml``), ``override_columns``
    and ``factor_columns`` where pandas propagates them.

    Analysis: :meth:`summary` (mean and confidence interval over
    replications), :meth:`paired` (paired differences across a lever),
    :meth:`replications_needed` (replications for a target interval, from
    a pilot), :meth:`expand` (dict outcomes to columns), :meth:`timeseries`
    and :meth:`log` (the tidy time series and event logs with the factor
    columns), :meth:`sobol_indices`, :meth:`run` and :meth:`failed`.

    Example::

        df = load_study("results/fleet_comparison")
        df.summary("self_consumption", by=["algorithm", "ev.n"])
        df.paired("algorithm", "peak_import_kw", baseline="greedy")
        df[df["algorithm"] == "greedy"].timeseries(variables="import_kw")
        df.run("3f2a9c1b7d4e").wide("soc_kwh")
    """

    _metadata = ["study_path", "study_spec", "override_columns", "factor_columns"]
    study_path: Path | None = None
    study_spec: dict | None = None
    #: the columns that hold overrides of the study (not outcomes)
    override_columns: tuple[str, ...] = ()
    #: the factor columns, each followed by its ``":label"`` column if labeled
    factor_columns: tuple[str, ...] = ()

    @property
    def removed_overrides(self) -> tuple[str, ...]:
        """Parameter paths the study's overrides remove (``"-phases"`` gives ``"phases"``).

        Removals restore a default for every run, so they have no column;
        they are listed here (from ``study.yaml``).

        Example::

            load_study("results/no_warmup").removed_overrides     # ("phases",)
        """
        overrides = (self.study_spec or {}).get("overrides") or {}
        if not isinstance(overrides, Mapping):
            return ()
        return tuple(str(key)[1:] for key in overrides if str(key).startswith("-"))

    @property
    def _constructor(self):
        return _study_frame_class()

    def __finalize__(self, other, method=None, **kwargs):
        result = super().__finalize__(other, method=method, **kwargs)
        # pandas copies _metadata from one frame only; merge and concat get the
        # operation (pandas 2) or a namespace of their inputs (pandas 3)
        if method == "merge":
            sources = [getattr(other, "left", None)]
        elif method == "concat":
            sources = list(
                getattr(other, "input_objs", None) or getattr(other, "objs", None) or []
            )
        else:
            return result
        frames = [obj for obj in sources if isinstance(obj, _StudyFrameMethods)]
        if (
            frames
            and len(frames) == len(sources)
            and len({str(frame.study_path) for frame in frames}) == 1
        ):
            for name in self._metadata:
                object.__setattr__(result, name, getattr(frames[0], name, None))
        return result

    def _store(self) -> StudyStore:
        if self.study_path is None:
            raise StudyError(
                "This frame does not know its study folder any more (pandas drops "
                "metadata in some operations); call load_study(path) again."
            )
        return StudyStore(self.study_path)

    def failed(self):
        """Return the rows of failed runs with an ``error`` column (the error line).

        The full traceback of a run is in ``runs/<run_id>/error.txt``.

        Example::

            for row in df.failed().itertuples():
                print(row.run_id, row.error)
        """
        rows = self[self["status"] == "failed"].copy()
        store = self._store()
        rows["error"] = [
            (store.read_manifest(run_id) or {}).get("error")
            for run_id in rows["run_id"]
        ]
        return rows

    def _row(self, run: int | str):
        if isinstance(run, str):
            selected = self[self["run_id"] == run]
            what = f"run id {run!r}"
        elif isinstance(run, numbers.Integral) and not isinstance(run, bool):
            selected = self[self["index"] == int(run)]
            what = f"run index {int(run)}"
        else:
            raise TypeError(f"Pass a run id (str) or a run index (int), got {run!r}.")
        if len(selected) == 0:
            raise KeyError(f"No run with {what} in this frame.")
        return selected.iloc[0]

    def run(self, run: int | str) -> RunResult:
        """Load the :class:`~mango.scenario.RunResult` of one done run.

        :param run: the run id or the run index (the ``index`` column)
        :raises KeyError: if the frame has no such run
        :raises StudyError: if the run is not done

        Example::

            df.run(0).ts("import_kw").max()
        """
        row = self._row(run)
        store = self._store()
        if row["status"] != "done":
            folder = store.run_dir(row["run_id"])
            hint = f"; see {folder / ERROR_FILE}" if row["status"] == "failed" else ""
            raise StudyError(
                f"Run {row['index']} ({row['run_id']}) has status {row['status']!r}{hint}."
            )
        return _load_result(store.run_dir(row["run_id"]), store._scenario())

    def timeseries(
        self,
        run_ids: str | Sequence[str] | None = None,
        variables: str | Sequence[str] | None = None,
        *,
        with_factors: bool = True,
        pad: bool = False,
    ) -> pandas.DataFrame:
        """Return the tidy time series of done runs, concatenated in frame order.

        :param run_ids: restrict to these runs (default: every done run of
            the frame)
        :param variables: restrict to these variables (only their rows are
            read: with Parquet the others never leave the file)
        :param with_factors: append the run columns of this frame that
            describe a run: ``replication``, ``point``, ``subexperiment``
            and ``sample`` (when present), the factor and ``":label"``
            columns, the override columns and ``plan`` (string columns as
            categoricals); ``False`` returns the tidy columns only (less
            memory for large studies; join on ``run_id`` yourself)
        :param pad: carry the last values of runs that ended before others
            (``stop_when``, ``ctx.stop()``) forward to the later times the
            selected runs recorded, so means over runs at each time compare
            like with like; adds a bool column ``padded`` (``True`` for the
            added rows).  Only series that were still recorded at the end
            of their run are padded (a retired entity stays ended).
        :return: a ``pandas.DataFrame`` with the tidy run columns (labels
            as categoricals), then the run columns
        :raises KeyError: for run ids not in the frame or variables no run
            recorded
        :raises StudyError: for selected runs that are not done

        Example::

            ts = df.timeseries(variables=["import_kw"])
            ts.groupby(["algorithm", "time"], observed=True)["value"].mean()
            # runs stopped early hold their last value up to the others' end
            ts = df.timeseries(variables=["infected"], pad=True)
        """
        pd = _pandas()
        store = self._store()
        ids = self._done_ids(run_ids)
        wanted = None
        if variables is not None:
            wanted = [variables] if isinstance(variables, str) else list(variables)
        frames = [
            read_run_frame(store.run_dir(run_id), variables=wanted) for run_id in ids
        ]
        if wanted is not None and ids:
            found = set()
            for frame in frames:
                found.update(frame["variable"].unique().tolist())
            missing = [name for name in wanted if name not in found]
            if missing:
                known = list(
                    dict.fromkeys(
                        name
                        for run_id in ids
                        for name in read_run_variables(store.run_dir(run_id))
                    )
                )
                raise KeyError(
                    f"No variable(s) {missing} in these runs; recorded: {known}."
                )
        if pad and frames:
            frames = _padded(frames)
        if not frames:
            columns = [
                "run_id",
                "time",
                "phase",
                "group",
                "entity",
                "variable",
                "value",
            ]
            tidy = pd.DataFrame({name: [] for name in columns})
            if pad:
                tidy["padded"] = pd.Series([], dtype=bool)
        else:
            tidy = concat_frames(frames)
        if not with_factors:
            return tidy
        from .analysis import join_run_columns

        return join_run_columns(tidy, self)

    def summary(
        self,
        outcomes: str | Sequence[str] | None = None,
        *,
        by: str | Sequence[str] | None = None,
        confidence: float | None = 0.95,
    ) -> pandas.DataFrame:
        """Mean and confidence interval of the outcomes over replications.

        Groups the done runs by design point (``by=None``: the ``point``
        column, with the subexperiment, factor and override columns of the
        point beside it) or by the columns *by*, and returns one row per
        group and outcome.  The interval is a Student-t interval over
        *independent units*: runs that share their seed are averaged into
        one unit first.  Within a design point the units are the
        replications.  Grouped more coarsely under
        ``common_random_numbers=True`` (e.g. ``by="algorithm"`` over the
        samples of an Lhs design), the runs of one replication share a seed
        and form one unit, so ``n`` is the number of replications, not of
        runs: their noise is shared, and counting them as independent
        would be pseudo-replication.

        :param outcomes: an outcome column or a list (default: every
            numeric column that is not a run, design, factor or override
            column, i.e. the outcomes and metrics)
        :param by: the grouping column(s) (default: the design point)
        :param confidence: the level of ``ci_low``/``ci_high``; ``None``
            leaves them out
        :return: a ``pandas.DataFrame`` with the group columns and
            ``outcome, runs, missing, n, mean, std, ci_low, ci_high``
            (``runs``: done runs with a value, ``missing``: done runs
            without one (NaN/None, e.g. a censored time to an event; a
            ``UserWarning`` says so, as the mean of the rest is biased when
            values are not missing at random), ``n``: independent units,
            ``std``: their sample standard deviation; NaN interval for
            ``n < 2``)
        :raises KeyError: for unknown columns
        :raises StudyError: when the frame has no numeric outcome

        Example::

            df.summary("self_consumption")                       # per design point
            df.summary(by=["algorithm", "ev.n"], confidence=0.9)
        """
        from .analysis import frame_summary

        return frame_summary(self, outcomes, by, confidence)

    def paired(
        self,
        lever: str,
        outcomes: str | Sequence[str] | None = None,
        *,
        baseline: Any = None,
        by: str | Sequence[str] | None = None,
        confidence: float | None = 0.95,
        pairs: bool = False,
    ) -> pandas.DataFrame:
        """Paired differences of the outcomes between the levels of *lever* and a baseline.

        Runs are paired when they agree in every other factor (and
        override) and in the replication: under common random numbers
        (``True``, or ``"levers"`` with *lever* a lever) they share their
        seed, so the difference removes the noise both runs have in common
        and needs far fewer replications than comparing means.  For every
        level other than *baseline* the method returns the mean difference
        ``level - baseline`` with a Student-t interval over independent
        units (pairs that share their seeds are averaged first, as in
        :meth:`summary`), pooled over the other factors unless *by* splits
        them.  A run is never paired with itself.

        Runs without a value of *lever* (a subexperiment that does not set
        it) are left out with a warning.  Factors that only some levels set
        (the settings of one variant in subexperiments, e.g.
        ``algorithm.rounds`` of ``market`` only) cannot match across
        levels: they become part of the compared level instead (a column of
        their own; the baseline text reads ``"market (algorithm.rounds=3)"``),
        and the other factors and the replication pair the runs.  Runs
        without a counterpart are left out with a warning naming the
        columns they differ in (or the runs that are not done); no pair at
        all is an error.  Pairs whose outcome is NaN/None in either run are
        counted in ``missing``, with a warning.  A warning also says when
        the paired runs share no seeds.

        :param lever: the factor column whose levels are compared (its
            ``"<path>:label"`` column works too)
        :param outcomes: an outcome column or a list (default: every
            numeric outcome, as in :meth:`summary`)
        :param baseline: the reference level, as its value or its label
            (default: the first level in run order); when the level has
            several variants, a mapping naming the variant, e.g.
            ``{"algorithm.kind": "greedy", "algorithm.threshold_kw": 0.2}``
        :param by: column(s) to report separately (e.g. an uncertainty)
        :param confidence: the level of ``ci_low``/``ci_high``; ``None``
            leaves them out
        :param pairs: return the pairs themselves instead (to audit the
            pairing): the other factor columns, ``replication``, *lever*,
            ``outcome, baseline, baseline_value, value, difference, run_id,
            baseline_run_id``
        :return: a ``pandas.DataFrame`` with ``[by...], <lever>,
            [<lever>:label], [variant factors...], baseline, outcome, pairs,
            missing, n, mean, std, ci_low, ci_high`` (``mean``: the mean
            difference; the raw *lever* column is left out when a label
            names its dict or list values)
        :raises KeyError: for unknown columns or a *baseline* that is not a
            level
        :raises StudyError: when the done runs have fewer than two levels,
            no run pairs, the baseline is NaN or covers several variants,
            or two runs of one level share every factor and replication

        Example::

            df.paired("algorithm", "peak_import_kw", baseline="greedy")
            df.paired("algorithm", baseline="greedy", by="ev.n")
            df.paired("algorithm", "peak_import_kw", pairs=True).boxplot("difference")
            df.paired("algorithm.kind", "score", baseline="greedy")   # subexperiments
        """
        from .analysis import frame_paired

        return frame_paired(self, lever, outcomes, baseline, by, confidence, pairs)

    def replications_needed(
        self,
        outcomes: str | Sequence[str] | None = None,
        *,
        half_width: float | None = None,
        relative: float | None = None,
        by: str | Sequence[str] | None = None,
        lever: str | None = None,
        baseline: Any = None,
        confidence: float = 0.95,
    ) -> pandas.DataFrame:
        """How many replications a target confidence interval needs, from a pilot study.

        For each group of :meth:`summary` (or, with *lever*, each paired
        comparison of :meth:`paired`, which usually needs far fewer) the
        sample standard deviation ``s`` of the independent units gives the
        smallest ``n`` with ``t(n - 1) * s / sqrt(n) <= target``.  Run the
        missing ones by raising ``replications`` of the study (same seed and
        folder: the runs already done are reused).  Plan once from a pilot
        and run that number: adding runs until an interval looks narrow
        enough (optional stopping) makes the interval too optimistic.

        :param outcomes: an outcome column or a list (default: every
            numeric outcome)
        :param half_width: the target half-width, in the outcome's unit
        :param relative: alternatively the target as a fraction of the
            group mean (``0.05``: within 5 %)
        :param by: grouping columns, as in :meth:`summary` / :meth:`paired`
        :param lever: plan the paired differences of this lever instead
        :param baseline: the baseline level of *lever*
        :param confidence: the interval's level
        :return: the summary (or paired) table without the interval, plus
            ``half_width`` (now), ``target``, ``needed`` (independent units,
            i.e. replications) and ``additional`` (``needed - n``, at
            least 0)
        :raises ValueError: unless exactly one of *half_width* and
            *relative* is a positive number

        Example::

            pilot = load_study("results/pilot")                 # e.g. 10 replications
            pilot.replications_needed("wq", half_width=0.1)
            pilot.replications_needed("wq", relative=0.05, lever="policy", baseline="fifo")
        """
        from .analysis import frame_replications_needed

        return frame_replications_needed(
            self, outcomes, half_width, relative, by, lever, baseline, confidence
        )

    def expand(self, columns: str | Sequence[str] | None = None, *, sep: str = "."):
        """Turn dict-valued columns (e.g. per-entity outcomes) into one column per key.

        ``{"retailer": 6.0, "factory": 2.5}`` in column ``bullwhip`` becomes
        the columns ``bullwhip.retailer`` and ``bullwhip.factory`` at the
        place of ``bullwhip`` (keys in order of first appearance; a run
        without the key gets ``None``).  The result is a ``StudyFrame``, so
        :meth:`summary`, :meth:`paired` and :meth:`sobol_indices` read the
        new columns as outcomes.

        :param columns: the column(s) to expand (default: every column
            whose values are dicts)
        :param sep: the separator between column name and key
        :return: a new frame
        :raises KeyError: for unknown columns
        :raises StudyError: for columns holding other values than dicts,
            or new names that exist already

        Example::

            df.expand("bullwhip").summary(["bullwhip.retailer", "bullwhip.factory"])
        """
        from .analysis import frame_expand

        return frame_expand(self, columns, sep)

    def log(
        self,
        name: str,
        run_ids: str | Sequence[str] | None = None,
        *,
        with_factors: bool = True,
    ):
        """Return the event log *name* of done runs, concatenated in frame order.

        Runs without rows of the log contribute none.  The rows of one run
        are not independent observations (they share the run's history):
        reduce each run to one number (an outcome) for intervals and tests,
        e.g. ``log.groupby("run_id").price.mean()``, or use batch means.

        :param name: the log name (see :meth:`RunResult.log
            <mango.scenario.RunResult.log>`)
        :param run_ids: restrict to these runs (default: every done run of
            the frame)
        :param with_factors: append the run columns that describe a run,
            as :meth:`timeseries` does: ``replication``, ``point``,
            ``subexperiment`` and ``sample`` (when present), the factor and
            ``":label"`` columns, the override columns and ``plan`` (a
            logged field of the same name keeps its value); ``False``
            returns the logged columns only
        :return: a ``pandas.DataFrame`` with ``run_id, time, [datetime],
            phase``, the logged fields and the run columns
        :raises KeyError: when no selected run has the log (listing the
            logs they have)
        :raises StudyError: for selected runs that are not done

        Example::

            trades = df.log("trades")
            per_run = trades.groupby(["tick_size", "run_id"], observed=True)["price"].std()
            per_run.groupby("tick_size").mean()        # one value per run, then across runs
        """
        pd = _pandas()
        store = self._store()
        frames, known = [], {}
        for run_id in self._done_ids(run_ids):
            entries = (store.read_manifest(run_id) or {}).get("logs") or {}
            known.update(dict.fromkeys(entries))
            if name in entries:
                frames.append(read_run_log(store.run_dir(run_id), name))
        if not frames:
            listing = list(known)
            raise KeyError(
                f"No event log {name!r} in these runs.{did_you_mean(str(name), listing)} "
                f"Logs: {listing}."
            )
        for frame in frames:  # one categorical run_id over all runs
            frame["run_id"] = frame["run_id"].astype(object)
        result = pd.concat(frames, ignore_index=True)
        result["run_id"] = result["run_id"].astype("category")
        if not with_factors:
            return result
        from .analysis import join_run_columns

        return join_run_columns(result, self)

    def _done_ids(self, run_ids: str | Sequence[str] | None) -> list[str]:
        """The selected run ids (default: every done run); all must be done."""
        if run_ids is None:
            ids = self.loc[self["status"] == "done", "run_id"].tolist()
        else:
            ids = [run_ids] if isinstance(run_ids, str) else list(run_ids)
            status = dict(zip(self["run_id"].tolist(), self["status"].tolist()))
            unknown = [run_id for run_id in ids if run_id not in status]
            if unknown:
                raise KeyError(f"Run id(s) {unknown} are not in this frame.")
            not_done = [run_id for run_id in ids if status[run_id] != "done"]
            if not_done:
                raise StudyError(
                    f"Run(s) {not_done} are not done; select done runs, e.g. "
                    "df[df['status'] == 'done'].timeseries()."
                )
        return ids

    def sobol_indices(
        self,
        outcomes: Any = None,
        *,
        second_order: bool = False,
        confidence: float | None = 0.95,
        n_resamples: int = 999,
    ):
        """Sobol' sensitivity indices of the outcomes, from a :class:`~mango.scenario.Sobol` study.

        Replications are averaged per design point first; the indices are
        then computed with ``scipy.stats.sobol_indices`` (Saltelli 2010 for
        the first-order index ``S1``, Jansen for the total index ``ST``) from
        the *A*, *B* and *AB* points of the design.  When discrete factors
        with several levels are crossed with the sample, the indices are
        computed per combination of levels (one column per such factor, plus
        ``"<factor>:label"`` for labeled ones and ``subexperiment`` with
        subexperiments).  Only the rows of this frame are used, so
        ``df[df["replication"] < 2].sobol_indices()`` analyses two
        replications.

        ``S1_conf`` and ``ST_conf`` are half-widths of percentile bootstrap
        confidence intervals (the *n* base samples are resampled
        *n_resamples* times, every outcome of a level combination with the
        same resamples; ``S1 ± S1_conf`` as with SALib), drawn from the
        study seed, so a call gives the same values every time.  The
        outcomes are centred first, as scipy does, so adding a constant to
        an outcome changes neither the indices nor the intervals.  The
        bootstrap treats the base samples as independent draws, so for a
        scrambled Sobol' design (more even than random draws) the intervals
        are conservative.  They cover the sampling of the factor
        space; with ``common_random_numbers=True`` (the default of a Sobol
        design) every point of replication *r* shares one seed, so they do
        not include the variation between noise realisations: compare the
        indices of single replications (``df[df.replication == r]``) for
        that.  A ``UserWarning`` names designs with fewer than 64 base
        samples and estimates outside ``[0, 1]`` by more than 0.05 (at any
        *n*): both mean the indices are dominated by Monte Carlo error.  An
        outcome that is constant over the design has no variance to
        apportion: its indices are NaN, with a warning.

        :param outcomes: an outcome name or a list of names (default: every
            numeric outcome)
        :param second_order: return the second-order indices ``S2`` (with
            their bootstrap confidence ``S2_conf``) instead, computed by
            SALib; needs a ``Sobol(n, second_order=True)`` design
        :param confidence: the confidence level of the ``*_conf`` columns;
            ``None`` skips the bootstrap (and the columns)
        :param n_resamples: bootstrap resamples
        :return: a tidy ``pandas.DataFrame`` with one row per level
            combination, outcome and factor: ``[levels...], outcome,
            factor, S1, S1_conf, ST, ST_conf`` (``second_order=True``: one
            row per factor pair, ``[levels...], outcome, factor, other, S2,
            S2_conf``)
        :raises StudyError: if the frame holds no Sobol design, the design is
            incomplete, runs are not done or an outcome value is missing
        :raises KeyError: for unknown outcome names
        :raises ValueError: for a *confidence* outside ``(0, 1)`` or
            *n_resamples* < 2
        :raises ImportError: without scipy (or SALib for ``second_order``)

        Example::

            df = load_study("results/sobol_pv")
            si = df.sobol_indices()
            si.pivot(index="factor", columns="outcome", values="ST")
            si[si.ST - si.ST_conf > 0.05]          # clearly influential factors
        """
        from .sobol import frame_sobol_indices

        return frame_sobol_indices(
            self,
            outcomes,
            second_order,
            confidence=confidence,
            n_resamples=n_resamples,
        )


_FRAME_CLASS: type | None = None


def _study_frame_class() -> type:
    global _FRAME_CLASS
    if _FRAME_CLASS is None:
        pd = _pandas()
        _FRAME_CLASS = type(
            "StudyFrame",
            (_StudyFrameMethods, pd.DataFrame),
            {
                "__module__": __name__,
                "__qualname__": "StudyFrame",
                "__doc__": _StudyFrameMethods.__doc__,
            },
        )
    return _FRAME_CLASS


def __getattr__(name: str) -> Any:
    if name == "StudyFrame":
        return _study_frame_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), "StudyFrame"})


def _factor_columns(specs: Sequence[Mapping]) -> tuple[list[str], set[str], list[str]]:
    """Factor paths (first seen first), the labeled ones, and their table columns.

    A ``"<factor>:label"`` column follows each labeled factor.
    """
    paths: list[str] = []
    labeled: set[str] = set()
    for spec in specs:
        paths += [path for path in spec.get("factors") or {} if path not in paths]
        labeled.update(spec.get("labels") or {})
    columns: list[str] = []
    for path in paths:
        columns += [path, f"{path}:label"] if path in labeled else [path]
    return paths, labeled, columns


def _cell(value: Any) -> Any:
    """A table cell: dict and list values as canonical JSON text (hashable, groupable)."""
    if isinstance(value, dict | list):
        return canonical_json(value)
    return value


def _factor_cells(spec: Mapping, paths: Sequence[str], labeled: set[str]) -> dict:
    """The factor (and label) cells of one run's table row.

    Dict and list values are canonical JSON text, so the columns support
    ``groupby``, ``drop_duplicates`` and ``pivot``; the run spec keeps the
    value itself.
    """
    factors = spec.get("factors") or {}
    labels = spec.get("labels") or {}
    cells = {}
    for path in paths:
        cells[path] = _cell(factors.get(path))
        if path in labeled:
            cells[f"{path}:label"] = labels.get(path)
    return cells


def _design_cells(
    specs: Sequence[Mapping], plans: Sequence[Any] | None = None
) -> tuple[list[str], list[dict]]:
    """The design columns used by *specs* and the ``point`` and design cells of each.

    ``point`` numbers the distinct design points (factor values within a
    subexperiment and, with *plans*, a plan) in order of first appearance,
    so all replications of a point share it.  ``subexperiment`` and
    ``sample`` (:data:`DESIGN_COLUMNS`) are only returned when a spec has
    them.
    """
    designs = [spec.get("design") or {} for spec in specs]
    columns = [
        name for name in DESIGN_COLUMNS if any(name in design for design in designs)
    ]
    points: dict[str, int] = {}
    cells = []
    for i, (spec, design) in enumerate(zip(specs, designs)):
        key = canonical_json(
            [
                design.get("subexperiment"),
                spec.get("factors") or {},
                None if plans is None else plans[i],
            ]
        )
        row = {"point": points.setdefault(key, len(points))}
        for name in columns:
            row[name] = design.get(name)
        cells.append(row)
    return columns, cells


def _param_value(params: Any, path: str) -> Any:
    """The value at dotted *path* of plain params (``None`` when missing)."""
    value = params
    for part in path.split("."):
        if isinstance(value, Mapping) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.lstrip("-").isdigit():
            try:
                value = value[int(part)]
            except IndexError:
                return None
        else:
            return None
    return value


def _override_paths(
    definitions: Iterable[Mapping], factor_paths: Sequence[str]
) -> list[str]:
    """Dotted override paths of the definitions that no factor covers (first seen first)."""
    paths: list[str] = []
    for definition in definitions:
        overrides = definition.get("overrides") or {}
        if not isinstance(overrides, Mapping):
            continue
        for key in overrides:
            path = str(key)
            if path[:1] in "+-" or path in paths:
                continue
            covered = any(
                path == factor
                or path.startswith(factor + ".")
                or factor.startswith(path + ".")
                for factor in factor_paths
            )
            if not covered:
                paths.append(path)
    return paths


def _plan_specs(
    store: StudyStore, plan: str
) -> tuple[list[dict], dict, list[Mapping], dict[str, str]]:
    """``(specs, definition, definitions, plan of each run id)`` for a ``plan`` selection."""
    specs = store.read_runs()
    definition = store.read_definition() or {}
    if plan == "current":
        return specs, definition, [definition], {}
    if plan == "all":
        current = "current"
        owner = {spec["run_id"]: current for spec in specs}
        definitions: list[Mapping] = [definition]
        for plan_id in store.plan_ids():
            old_definition, old_specs = store.read_plan(plan_id)
            definitions.append(old_definition)
            for spec in old_specs:
                if spec["run_id"] not in owner:
                    owner[spec["run_id"]] = plan_id
                    specs.append(spec)
        return specs, definition, definitions, owner
    old_definition, old_specs = store.read_plan(plan)
    return old_specs, old_definition, [old_definition], {}


def load_study(path: str | os.PathLike, *, outcomes: Any = None, plan: str = "current"):
    """Load a study folder as a :class:`~mango.scenario.StudyFrame` (one row per planned run).

    Columns:

    - ``index, run_id, replication, status, wall_s, cpu_s``: ``status`` is
      ``done``, ``failed`` or ``pending``; ``wall_s`` and ``cpu_s`` are the
      wall-clock and CPU time of the run (``wall_s`` grows with the load of
      the machine, ``cpu_s`` is the figure for sizing);
    - ``point``: the design point (0, 1, ... in order of first appearance;
      all replications of a point share it), the grouping key of
      :meth:`StudyFrame.summary` and :meth:`StudyFrame.paired`;
    - ``stale``: true for a done run whose code or data files changed since
      it was made (see :mod:`mango.scenario.freshness`; ``study.run()``
      runs such runs again unless ``stale="keep"``);
    - ``outcome_errors``: ``{outcome: error}`` of a done run whose outcome
      functions raised (their values are ``None``), else ``None``;
    - ``git_commit``, ``git_dirty``: the git state of the project when the
      run executed (``None`` outside a git repository);
    - ``subexperiment`` (with subexperiments) and ``sample`` (the sample
      number of a sampling design), only when the design has them;
    - one column per factor (dotted path, holding the value; dict and list
      values as canonical JSON text, so the column can be grouped) and a
      ``"<factor>:label"`` column after each labeled factor;
    - one column per override of the study (dotted path, holding the value
      the run used), so a frame of a variant (``run --set``) says so;
      removal overrides (``-path``) have no column, the frame lists them in
      :attr:`StudyFrame.removed_overrides`;
    - one column per outcome (``None``/NaN for runs that are not done);
    - one column per run-level metric (:meth:`ctx.set_metric
      <mango.scenario.BuildContext.set_metric>`, :meth:`Scenario.metric
      <mango.scenario.Scenario.metric>`; ``"metric:<name>"`` when an outcome
      or factor has the name);
    - with ``plan="all"`` also ``plan``: ``"current"`` or the id of the
      earlier plan a run belongs to.

    :param path: the study folder (``out`` of the study)
    :param outcomes: recompute these outcomes from the stored time series
        first (a dict ``{name: function or "module:fn"}`` or a list); each
        done run's ``outcomes.json`` is updated with the values that could
        be computed, and stored values are kept when the recomputation fails
        (see :meth:`StudyStore.recompute_outcomes`)
    :param plan: ``"current"`` (the run table of the last ``study.run()``),
        ``"all"`` (also the runs of earlier plans kept in ``plans/``, e.g.
        the baseline before a ``run --set`` variant) or an earlier plan's id
        (:meth:`StudyStore.plan_ids`)
    :return: the :class:`~mango.scenario.StudyFrame`
    :raises FileNotFoundError: if *path* has no ``runs.jsonl``
    :raises KeyError: for an unknown plan id

    Example::

        df = load_study("results/fleet_comparison")
        df.groupby(["algorithm", "ev.n"])[["self_consumption"]].mean()
        df = load_study("results/fleet_comparison", outcomes=[kpi.peak_import_kw])
        everything = load_study("results/tolerance", plan="all")   # baseline and variants
    """
    store = StudyStore(path)
    specs, definition, definitions, owner = _plan_specs(store, plan)
    factor_paths, labeled, factor_columns = _factor_columns(specs)
    override_columns = [
        name
        for name in _override_paths(definitions, factor_paths)
        if name not in factor_columns
    ]
    recomputed: list[str] = []
    if outcomes is not None:
        recomputed = list(resolve_outcomes(outcomes))
        check_outcome_names(recomputed, [*factor_paths, *override_columns])
        store.recompute_outcomes(outcomes, specs)
    outcome_names = list(definition.get("outcomes") or {})
    outcome_names += [name for name in recomputed if name not in outcome_names]
    rows = []
    metric_names: list[str] = []
    for spec, status in zip(specs, store.status_rows(specs, details=True)):
        values, metrics = {}, {}
        if status["status"] == "done":
            values = store.read_outcomes(spec["run_id"])
            for name in values:
                if name not in outcome_names:
                    outcome_names.append(name)
            metrics = store.read_metrics(spec["run_id"])
            metric_names += [name for name in metrics if name not in metric_names]
        rows.append((spec, status, values, metrics))
    design_columns, design_cells = _design_cells(
        specs, [owner.get(spec["run_id"]) for spec in specs] if owner else None
    )
    columns = [*FRAME_COLUMNS, *design_columns, *factor_columns, *override_columns]
    columns += [name for name in outcome_names if name not in columns]
    metric_columns = _metric_columns(metric_names, [*columns, "plan"])
    columns += metric_columns.values()
    if owner:
        columns.append("plan")
    data: dict[str, list] = {name: [] for name in columns}
    for (spec, status, values, metrics), design in zip(rows, design_cells):
        git = status["git"] if isinstance(status["git"], Mapping) else {}
        done = status["status"] == "done"
        commit = git.get("commit")
        record = {
            "index": spec.get("index"),
            "run_id": spec["run_id"],
            "replication": spec.get("replication"),
            "status": status["status"],
            "wall_s": status["wall_s"],
            "cpu_s": status["cpu_s"],
            "stale": bool(status["stale"]) if done else None,
            "outcome_errors": status["outcome_errors"],
            "git_commit": commit[:12] if isinstance(commit, str) else None,
            "git_dirty": git.get("dirty") if git else None,
            **design,
            **_factor_cells(spec, factor_paths, labeled),
        }
        for name in override_columns:
            record[name] = _cell(_param_value(spec.get("params") or {}, name))
        for name in outcome_names:
            record.setdefault(name, values.get(name))
        for name, column in metric_columns.items():
            record[column] = metrics.get(name)
        if owner:
            record["plan"] = owner.get(spec["run_id"])
        for name in columns:
            data[name].append(record.get(name))
    pd = _pandas()
    frame = _study_frame_class()(
        {
            name: pd.Series(values, dtype=_dtype(values))
            for name, values in data.items()
        },
        columns=columns,
    )
    frame.study_path = store.out
    frame.study_spec = definition
    frame.override_columns = tuple(override_columns)
    frame.factor_columns = tuple(factor_columns)
    return frame


def _metric_columns(names: Sequence[str], taken: Sequence[str]) -> dict[str, str]:
    """Column of each metric: its name, or ``"metric:<name>"`` when that is taken."""
    columns = {}
    for name in names:
        if name in taken:
            column = f"metric:{name}"
            warnings.warn(
                f"Metric {name!r} has the name of another column of the study frame "
                f"(an outcome, factor or override); it is the column {column!r}.",
                UserWarning,
                stacklevel=3,
            )
            columns[name] = column
        else:
            columns[name] = name
    return columns


def _dtype(values: list) -> Any:
    """``None`` (inferred) unless the column holds containers (kept as objects)."""
    if any(isinstance(value, dict | list) for value in values):
        return object
    return None

"""
Run a study as a Slurm job array: :class:`~mango.scenario.SlurmExecutor`.

The executor writes the pending run specs and a batch script into
``<out>/slurm/`` and submits it with ``sbatch``; every array task executes
one spec (or ``runs_per_task`` specs) with ``mango-scenario run-one``
(:func:`~mango.scenario.run_one_from_file`).
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._util import (
    is_positive_int,
    read_json,
    utc_now,
    write_json_atomic,
    write_text_atomic,
)
from .errors import StudyError

__all__ = ["SlurmExecutor", "SlurmSubmission", "active_slurm_job"]

logger = logging.getLogger(__name__)

SLURM_DIR = "slurm"
SLURM_PENDING_FILE = "pending.jsonl"
SLURM_SCRIPT_FILE = "array.sbatch"
SLURM_JOB_FILE = "job.json"
SLURM_LOG_DIR = "logs"
SLURM_SUBMIT_FILE = "submit.sh"
#: Array tasks per job by default (Slurm's default ``MaxArraySize`` is 1001).
DEFAULT_MAX_ARRAY_SIZE = 1000


@dataclass(frozen=True)
class SlurmSubmission:
    """What :meth:`~mango.scenario.SlurmExecutor.dispatch` wrote and submitted.

    :param runs: the number of runs
    :param script: the ``array.sbatch`` file (``None`` without runs)
    :param pending_file: the ``pending.jsonl`` file (``None`` without runs)
    :param job_id: the Slurm job id (``None`` when not submitted; the first
        job when the array was split)
    :param job_ids: every submitted job id (several when the array was split
        to respect ``max_array_size``)
    :param commands: the ``sbatch`` commands that submit the array (to run
        them yourself after ``submit=False``)

    Example::

        submission = SlurmExecutor(submit=False).dispatch(pending_specs, "results/fleet")
        submission.script        # Path("results/fleet/slurm/array.sbatch")
    """

    runs: int
    script: Path | None = None
    pending_file: Path | None = None
    job_id: str | None = None
    job_ids: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()


def _sbatch_value(value: Any, name: str) -> str:
    text = str(value)
    if not text.strip() or "\n" in text or "\r" in text:
        raise StudyError(
            f"SlurmExecutor: {name} must be a non-empty single line, got {value!r}."
        )
    return text.strip()


def _directive(line: Any) -> str:
    """An ``extra_sbatch`` line without its ``#SBATCH`` prefix; shell commands are refused."""
    text = _sbatch_value(line, "every extra_sbatch line")
    option = text[len("#SBATCH") :].strip() if text.startswith("#SBATCH") else text
    if not option.startswith("-"):
        raise StudyError(
            f"SlurmExecutor: extra_sbatch line {text!r} is not an sbatch option "
            "(--name=value); shell commands such as 'module load ...' belong in "
            "setup=[...] (--setup)."
        )
    return option


def _directive_path(path: Path) -> str:
    text = str(path)
    return f'"{text}"' if any(char.isspace() for char in text) else text


def _importable_ref(ref: Any, project_root: str) -> Any:
    """Turn a ``"__main__:x"`` reference into one a Slurm task can import.

    Slurm tasks run ``python -m mango.scenario.cli``, whose ``__main__`` is
    not the script that planned the study; the script's module path
    relative to the project root names it instead.
    """
    if not isinstance(ref, str):
        return ref
    module, sep, attr = ref.partition(":")
    if not sep:
        module, sep, attr = ref.rpartition(".")
    if module != "__main__":
        return ref
    main = sys.modules.get("__main__")
    name = getattr(getattr(main, "__spec__", None), "name", None)
    if name and name not in ("__main__", "__mp_main__"):
        return f"{name}{sep}{attr}"
    main_file = getattr(main, "__file__", None)
    if main_file:
        try:
            relative = (
                Path(main_file).resolve().relative_to(Path(project_root).resolve())
            )
        except ValueError:
            relative = None
        parts = relative.with_suffix("").parts if relative is not None else ()
        if parts and all(part.isidentifier() for part in parts):
            return f"{'.'.join(parts)}{sep}{attr}"
    raise StudyError(
        f"Slurm tasks cannot import {ref!r}: the script that defines it is not a "
        f"module below the project root {project_root}. Move the definition into an "
        "importable module and pass 'module:attr'."
    )


def _slurm_spec(spec: Mapping) -> dict:
    root = spec.get("project_root") or str(Path.cwd())
    result = dict(spec)
    result["scenario"] = _importable_ref(spec.get("scenario"), root)
    outcomes = spec.get("outcomes")
    if isinstance(outcomes, Mapping):
        result["outcomes"] = {
            name: _importable_ref(ref, root) for name, ref in outcomes.items()
        }
    return result


class SlurmExecutor:
    """Run a study as a Slurm job array: one array task per pending run (or per *runs_per_task* runs).

    :meth:`~mango.scenario.Study.run` with this executor (or
    ``executor="slurm"``) plans the study, skips finished runs as usual and
    writes

    - ``<out>/slurm/pending.jsonl``: the pending run specs, one per line;
    - ``<out>/slurm/array.sbatch``: a batch script with ``#SBATCH
      --array=0-<T-1>[%P]`` whose task *i* runs ``<python> -m
      mango.scenario.cli run-one <out>/slurm/pending.jsonl --line
      $SLURM_ARRAY_TASK_ID`` (with *runs_per_task* K: ``--lines`` *i*K* to
      *(i+1)*K*) in the project root, with ``OMP_NUM_THREADS``,
      ``OPENBLAS_NUM_THREADS``, ``MKL_NUM_THREADS``, ``NUMEXPR_NUM_THREADS``
      (``1``) and ``PYTHONHASHSEED`` (``0``) set where unset, like the
      process pool, after the *setup* lines; task logs go to
      ``<out>/slurm/logs/``;

    then submits the script with ``sbatch`` (when *submit*) and returns
    at once: the :class:`~mango.scenario.StudyReport` counts the submitted
    runs as pending and carries the job id.  Each task writes its run
    folder; ``load_study(out)`` and ``mango-scenario status <out>`` show the
    progress, and ``study.run()`` again resubmits only runs that are still
    pending or failed.  A requeued task skips runs that are already done.
    While the job has queued or running tasks, another ``study.run()`` on
    the folder is refused (checked with ``squeue``).

    Clusters limit the size of a job array (``MaxArraySize`` in
    ``slurm.conf``, 1001 by default: indices 0 to 1000).  When a study
    needs more than *max_array_size* tasks, the array is split into several
    jobs of at most *max_array_size* tasks (each task finds its runs from
    ``MANGO_TASK_OFFSET``, which ``sbatch --export`` sets per job), and
    ``<out>/slurm/submit.sh`` holds the ``sbatch`` commands.  ``sbatch``
    passes the submitting shell's environment to the tasks (``--export=ALL``
    is its default); use *setup* for ``module load`` or a virtual
    environment the tasks need.

    :param partition: ``--partition`` (``None``: the cluster default)
    :param time: ``--time`` limit per array task, e.g. ``"01:00:00"``
        (``None``: the cluster default); with *runs_per_task* K it must
        cover K runs
    :param cpus_per_task: ``--cpus-per-task``
    :param mem: ``--mem`` per task, e.g. ``"4G"`` (``None``: the default)
    :param array_parallelism: at most this many tasks at a time (``%P``)
    :param extra_sbatch: further directives, e.g. ``["--constraint=ib",
        "--gres=gpu:1"]`` (written as ``#SBATCH <line>``); every line must
        be an option (``--name=value``) or start with ``#SBATCH``
    :param python: the interpreter the tasks run (default: ``"python"``
        from ``PATH`` when *setup* lines are given, so the environment they
        activate (``source .venv/bin/activate``, ``module load``) is used;
        without *setup* the interpreter of the submitting process)
    :param submit: run ``sbatch``; ``False`` only writes the files
    :param account: ``--account`` (``None``: the default account)
    :param qos: ``--qos`` (``None``: the default quality of service)
    :param setup: shell lines every task runs before the runs, e.g.
        ``["module load python/3.11", "source .venv/bin/activate"]`` (run
        with ``set -e``, so a failing line stops the task)
    :param runs_per_task: runs each array task executes one after another
        (default 1); several runs per task save scheduler overhead for
        short runs and keep large studies below ``MaxArraySize``
    :param max_array_size: the most array tasks per job (default 1000;
        ``None``: no limit); larger arrays are split into several jobs
    :raises StudyError: for invalid options

    Example::

        report = study.run(executor=SlurmExecutor(partition="short", time="00:30:00",
                                                  mem="4G", array_parallelism=50,
                                                  account="grid", runs_per_task=4,
                                                  setup=["module load python/3.11"]))
        print(report)        # "3072 runs: ... 3072 submitted as Slurm job 4242 ..."
        # later: load_study(study.out)["status"].value_counts()
    """

    def __init__(
        self,
        partition: str | None = None,
        time: str | None = "01:00:00",
        cpus_per_task: int = 1,
        mem: str | int | None = None,
        array_parallelism: int | None = None,
        extra_sbatch: Sequence[str] | str = (),
        python: str | None = None,
        submit: bool = True,
        *,
        account: str | None = None,
        qos: str | None = None,
        setup: Sequence[str] | str = (),
        runs_per_task: int = 1,
        max_array_size: int | None = DEFAULT_MAX_ARRAY_SIZE,
    ):
        self.partition = (
            None if partition is None else _sbatch_value(partition, "partition")
        )
        self.time = None if time is None else _sbatch_value(time, "time")
        for name, value in (
            ("cpus_per_task", cpus_per_task),
            (
                "array_parallelism",
                1 if array_parallelism is None else array_parallelism,
            ),
            ("runs_per_task", runs_per_task),
            ("max_array_size", 1 if max_array_size is None else max_array_size),
        ):
            if not is_positive_int(value):
                raise StudyError(
                    f"SlurmExecutor: {name} must be an int >= 1, got {value!r}."
                )
        self.cpus_per_task = cpus_per_task
        self.array_parallelism = array_parallelism
        self.runs_per_task = runs_per_task
        self.max_array_size = max_array_size
        self.mem = None if mem is None else _sbatch_value(mem, "mem")
        self.account = None if account is None else _sbatch_value(account, "account")
        self.qos = None if qos is None else _sbatch_value(qos, "qos")
        if isinstance(extra_sbatch, str):
            extra_sbatch = (extra_sbatch,)
        self.extra_sbatch = tuple(_directive(line) for line in extra_sbatch)
        if isinstance(setup, str):
            setup = (setup,)
        self.setup = tuple(_sbatch_value(line, "every setup line") for line in setup)
        if python is None:
            # an interpreter activated by a setup line (a venv, a module) must be
            # found on PATH; an absolute path of the submitting host would bypass it
            python = "python" if self.setup else sys.executable
        self.python = _sbatch_value(python, "python")
        if not isinstance(submit, bool):
            raise StudyError(
                f"SlurmExecutor: submit must be True or False, got {submit!r}."
            )
        self.submit = submit

    def __repr__(self) -> str:
        return f"<SlurmExecutor: {self.describe()}>"

    def describe(self) -> str:
        """Human-readable form, e.g. ``"Slurm array, partition short"``."""
        details = [f"partition {self.partition}"] if self.partition else []
        if self.runs_per_task > 1:
            details.append(f"{self.runs_per_task} runs per task")
        if not self.submit:
            details.append("not submitted")
        return ", ".join(["Slurm array", *details])

    def check(self) -> None:
        """Fail early when ``sbatch`` is needed but not on ``PATH``.

        :raises StudyError: with a hint to use ``submit=False``
        """
        if self.submit and shutil.which("sbatch") is None:
            raise StudyError(
                "sbatch was not found on PATH: submit the study from a Slurm login "
                "node, or use SlurmExecutor(submit=False) to only write "
                "slurm/array.sbatch and submit it yourself (sbatch <out>/slurm/"
                "array.sbatch)."
            )

    def tasks(self, runs: int) -> int:
        """The number of array tasks for *runs* runs (``ceil(runs / runs_per_task)``)."""
        return -(-runs // self.runs_per_task)

    def _parts(self, tasks: int) -> list[tuple[int, int]]:
        """``(offset, size)`` of each job the array of *tasks* tasks is split into."""
        size = self.max_array_size or tasks
        return [(offset, min(size, tasks - offset)) for offset in range(0, tasks, size)]

    def _array(self, size: int) -> str:
        array = f"0-{size - 1}"
        if self.array_parallelism is not None:
            array += f"%{self.array_parallelism}"
        return array

    def script(
        self, runs: int, out: Path, project_root: str, *, job_name: str = "mango"
    ) -> str:
        """Return the batch script for *runs* runs of the study folder *out*.

        Example::

            print(SlurmExecutor(partition="short").script(120, Path("results/fleet"), "."))
        """
        folder = Path(out) / SLURM_DIR
        pending = folder / SLURM_PENDING_FILE
        tasks = self.tasks(runs)
        parts = self._parts(tasks)
        name = "_".join(str(job_name).split()) or "mango"
        lines = [
            "#!/bin/bash",
            f"#SBATCH --job-name={name}",
            f"#SBATCH --array={self._array(parts[0][1])}",
        ]
        if self.partition:
            lines.append(f"#SBATCH --partition={self.partition}")
        if self.account:
            lines.append(f"#SBATCH --account={self.account}")
        if self.qos:
            lines.append(f"#SBATCH --qos={self.qos}")
        if self.time:
            lines.append(f"#SBATCH --time={self.time}")
        lines.append(f"#SBATCH --cpus-per-task={self.cpus_per_task}")
        if self.mem:
            lines.append(f"#SBATCH --mem={self.mem}")
        lines.append(
            f"#SBATCH --output={_directive_path(folder / SLURM_LOG_DIR / '%A_%a.out')}"
        )
        lines += [f"#SBATCH {line}" for line in self.extra_sbatch]
        k = self.runs_per_task
        what = (
            f"array task i runs line i of {SLURM_PENDING_FILE}"
            if k == 1
            else f"array task i runs lines i*{k} to i*{k}+{k - 1} of {SLURM_PENDING_FILE}"
        )
        split = (
            f" The array is split into {len(parts)} jobs of at most "
            f"{self.max_array_size} tasks (MaxArraySize); {SLURM_SUBMIT_FILE} submits "
            "them, each with its MANGO_TASK_OFFSET."
            if len(parts) > 1
            else ""
        )
        lines += [
            "",
            f"# Written by mango.scenario: {runs} pending run{'s' * (runs != 1)} in "
            f"{tasks} array task{'s' * (tasks != 1)}; {what}.{split}",
            "set -eu",
        ]
        if self.setup:
            lines += ["# setup", "set +u", *self.setup, "set -u"]
        from .runner import WORKER_ENV  # runner imports this module

        lines += [
            f'export {key}="${{{key}:-{value}}}"' for key, value in WORKER_ENV.items()
        ]
        lines += [
            "TASK=$(( ${MANGO_TASK_OFFSET:-0} + SLURM_ARRAY_TASK_ID ))",
            f"cd {shlex.quote(project_root)}",
        ]
        try:  # relative to the project root the task changes to, when below it
            table = str(Path(pending).relative_to(Path(project_root)))
        except ValueError:
            table = str(pending)
        command = (
            f"{shlex.quote(self.python)} -m mango.scenario.cli run-one "
            f"{shlex.quote(table)}"
        )
        if k == 1:
            lines.append(f'{command} --line "$TASK"')
        else:
            lines.append(
                f'{command} --lines "$(( TASK * {k} )):$(( TASK * {k} + {k} ))"'
            )
        lines.append("")
        return "\n".join(lines)

    def submit_commands(self, runs: int, out: Path) -> list[str]:
        """The ``sbatch`` commands that submit the array for *runs* runs (one per job).

        Example::

            SlurmExecutor(max_array_size=1000).submit_commands(3072, Path("results/sobol"))
            # ["sbatch --parsable --array=0-999 --export=ALL,MANGO_TASK_OFFSET=0 ...", ...]
        """
        script = shlex.quote(str(Path(out) / SLURM_DIR / SLURM_SCRIPT_FILE))
        parts = self._parts(self.tasks(runs))
        if len(parts) == 1:
            return [f"sbatch {script}"]
        return [
            f"sbatch --array={self._array(size)} "
            f"--export=ALL,MANGO_TASK_OFFSET={offset} {script}"
            for offset, size in parts
        ]

    def dispatch(
        self,
        specs: Sequence[Mapping],
        out: str | os.PathLike,
        *,
        job_name: str = "mango",
    ) -> SlurmSubmission:
        """Write ``pending.jsonl`` and ``array.sbatch`` for *specs* and submit them.

        :meth:`~mango.scenario.Study.run` calls this with the pending runs.
        Nothing is written for an empty *specs*.  An array larger than
        ``max_array_size`` is split into several jobs (see the class
        docstring), whose ids are all recorded in ``job.json``.

        :param specs: the run specs to run
        :param out: the study folder
        :param job_name: the Slurm job name
        :return: the :class:`~mango.scenario.slurm.SlurmSubmission`
        :raises StudyError: when ``sbatch`` is missing or fails, or a
            ``__main__`` reference cannot be imported by the tasks

        Example::

            triage = StudyStore(out).triage(study.plan(out).specs)
            SlurmExecutor(partition="short").dispatch(triage.pending, out).job_id   # "4242"
        """
        if not specs:
            return SlurmSubmission(0)
        self.check()
        folder = Path(out).absolute() / SLURM_DIR
        project_root = str(specs[0].get("project_root") or Path.cwd())
        lines = [json.dumps(_slurm_spec(spec), ensure_ascii=False) for spec in specs]
        (folder / SLURM_LOG_DIR).mkdir(parents=True, exist_ok=True)
        pending = write_text_atomic(
            folder / SLURM_PENDING_FILE, "".join(line + "\n" for line in lines)
        )
        script = write_text_atomic(
            folder / SLURM_SCRIPT_FILE,
            self.script(len(specs), folder.parent, project_root, job_name=job_name),
        )
        parts = self._parts(self.tasks(len(specs)))
        commands = self.submit_commands(len(specs), folder.parent)
        submit_file = folder / SLURM_SUBMIT_FILE
        if len(parts) > 1:
            write_text_atomic(
                submit_file,
                "#!/bin/sh\n# Submits the array in parts (MaxArraySize); written by "
                "mango.scenario\nset -e\n" + "\n".join(commands) + "\n",
            )
        else:
            submit_file.unlink(missing_ok=True)
        if not self.submit:
            return SlurmSubmission(
                len(specs), script, pending, commands=tuple(commands)
            )
        job_ids = []
        for offset, size in parts:
            options = []
            if len(parts) > 1:
                options = [
                    f"--array={self._array(size)}",
                    f"--export=ALL,MANGO_TASK_OFFSET={offset}",
                ]
            job_ids.append(self._sbatch(script, project_root, options))
            write_json_atomic(
                folder / SLURM_JOB_FILE,
                {
                    "job_id": job_ids[0],
                    "job_ids": job_ids,
                    "runs": len(specs),
                    "submitted": utc_now(),
                },
            )
        return SlurmSubmission(
            len(specs),
            script,
            pending,
            job_ids[0],
            job_ids=tuple(job_ids),
            commands=tuple(commands),
        )

    @staticmethod
    def _sbatch(script: Path, cwd: str, options: Sequence[str] = ()) -> str:
        try:
            completed = subprocess.run(
                ["sbatch", "--parsable", *options, str(script)],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError:
            raise StudyError(
                "sbatch was not found on PATH; use SlurmExecutor(submit=False) and "
                f"submit {script} yourself."
            ) from None
        except subprocess.TimeoutExpired:
            raise StudyError(
                f"sbatch did not answer within 120 s; check the cluster and submit "
                f"{script} yourself (sbatch {script})."
            ) from None
        output = completed.stdout.strip()
        if completed.returncode != 0 or not output:
            message = (completed.stderr or completed.stdout).strip() or "no output"
            hint = (
                "; the array is larger than the cluster allows: lower "
                "max_array_size (--max-array-size) to the cluster's MaxArraySize "
                "or raise runs_per_task (--runs-per-task)"
                if "array" in message.lower() and "size" in message.lower()
                else ""
            )
            raise StudyError(
                f"sbatch {script} failed (exit {completed.returncode}): {message}{hint}"
            )
        return output.splitlines()[0].split(";")[0].strip()


def active_slurm_job(out: str | os.PathLike) -> str | None:
    """Return the id of a Slurm job of this study folder that still has tasks.

    Reads ``<out>/slurm/job.json`` (every job of a split array) and asks
    ``squeue``; returns ``None`` when there is no job, they have finished or
    ``squeue`` is not available.

    Example::

        active_slurm_job("results/fleet")        # "4242" while tasks are queued or running
    """
    data = read_json(Path(out) / SLURM_DIR / SLURM_JOB_FILE)
    if not isinstance(data, dict):
        return None
    job_ids = data.get("job_ids") or ([data["job_id"]] if data.get("job_id") else [])
    job_ids = [str(job_id) for job_id in job_ids if job_id]
    if not job_ids:
        return None
    try:
        completed = subprocess.run(
            ["squeue", "--noheader", "--format=%i", f"--jobs={','.join(job_ids)}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "Cannot check whether Slurm job %s still runs (%s); assuming it finished.",
            ", ".join(job_ids),
            type(exc).__name__,
        )
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    listed = completed.stdout
    for job_id in job_ids:
        if job_id in listed:
            return job_id
    return job_ids[0]

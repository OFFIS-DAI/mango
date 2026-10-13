"""
Pre-flight checks of the data files a study or scenario reads, before anything runs.

``mango-scenario validate`` calls :func:`check_data_files` with the resolved
parameters of every planned run.  It finds every profile (a
:class:`~mango.scenario.ProfileSpec` with a ``csv``/``parquet`` source) and
every :class:`~mango.scenario.FromColumn` in the parameters, loads each
distinct one once through the same cached readers the runs use, and reports
every problem at once: missing files, missing columns (with a did-you-mean
suggestion), non-numeric values, unparsable ``time_column`` values and local
times that cannot be placed in the scenario's time zone.

Columns that agent factories pick at run time (``e.profile("load",
key=f"zone_{i}")``) and recorder attributes exist only once the agents are
built: ``mango-scenario validate --build`` builds run 0 and takes one step.

Example::

    from mango.scenario.preflight import check_data_files

    problems = check_data_files([spec["params"] for spec in study.plan()])
    for problem in problems:
        print(problem)       # "profiles.occupancy (runs 0-23): 'data/occ.csv' has no column ..."
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ._util import canonical_json

__all__ = ["check_data_files", "data_references"]


def _is_profile(value: Mapping) -> bool:
    return value.get("source") in ("csv", "parquet") and isinstance(
        value.get("path"), str
    )


def _is_column(value: Mapping) -> bool:
    return value.get("kind") == "from_column" and isinstance(value.get("path"), str)


def data_references(params: Any, path: str = "") -> list[tuple[str, str, dict]]:
    """Find the profiles and FromColumn tables in plain parameters.

    :param params: resolved parameters (plain data)
    :param path: the dotted path of *params* (for recursion)
    :return: ``[(kind, dotted path, spec), ...]`` with kind ``"profile"`` or
        ``"column"``

    Example::

        data_references({"profiles": {"h0": {"source": "csv", "path": "x.csv"}}})
        # [("profile", "profiles.h0", {...})]
    """
    found: list[tuple[str, str, dict]] = []
    if isinstance(params, Mapping):
        if _is_profile(params):
            return [("profile", path, dict(params))]
        if _is_column(params):
            return [("column", path, dict(params))]
        for key, value in params.items():
            found += data_references(value, f"{path}.{key}" if path else str(key))
    elif isinstance(params, list | tuple):
        for i, value in enumerate(params):
            found += data_references(value, f"{path}.{i}" if path else str(i))
    return found


def _scenario_start(params: Any) -> Any:
    time = params.get("time") if isinstance(params, Mapping) else None
    if not isinstance(time, Mapping):
        return None
    from .timecfg import TimeConfig

    try:
        return TimeConfig.model_validate(time).start
    except Exception:  # the plan validated the params; be lenient here
        return None


def _check(kind: str, path: str, spec: dict, start: Any) -> str | None:
    from .distributions import FromColumn
    from .profiles import ProfileSpec, load_profile

    try:
        if kind == "column":
            FromColumn.model_validate(spec).column_values()
        else:
            profile = ProfileSpec.model_validate(spec)
            load_profile(profile, name=path.rpartition(".")[2], scenario_start=start)
    except Exception as exc:  # report every problem, whatever its type
        return str(exc) or type(exc).__name__
    return None


def _runs_text(runs: Sequence[int], total: int) -> str:
    if total <= 1 or not runs:
        return ""
    if len(runs) == total:
        return " (every run)"
    shown = ", ".join(map(str, runs[:5]))
    more = f" and {len(runs) - 5} more" if len(runs) > 5 else ""
    return f" (run{'s' * (len(runs) != 1)} {shown}{more})"


def check_data_files(params_list: Iterable[Any]) -> list[str]:
    """Load every distinct data table the parameters reference; return the problems.

    :param params_list: the resolved parameters of each run (plain data),
        in run order
    :return: one message per problem (empty when every file loads):
        ``"<dotted path> (runs ...): <error>"``

    Example::

        check_data_files([scenario.resolve_params("presets/base.yaml").model_dump()])
    """
    seen: dict[str, tuple[str, str, dict, Any, list[int]]] = {}
    total = 0
    for index, params in enumerate(params_list):
        total += 1
        start = _scenario_start(params)
        for kind, path, spec in data_references(params):
            key = canonical_json(
                [kind, spec, str(start) if kind == "profile" else None]
            )
            if key not in seen:
                seen[key] = (kind, path, spec, start, [])
            seen[key][4].append(index)
    problems = []
    for kind, path, spec, start, runs in seen.values():
        error = _check(kind, path, spec, start)
        if error is not None:
            problems.append(f"{path}{_runs_text(runs, total)}: {error}")
    return problems

"""First-order, total and second-order Sobol indices of a study's runs.

The analysis behind :meth:`~mango.scenario.StudyFrame.sobol_indices`: it
finds the Saltelli blocks (A, B, AB) of a Sobol design in a study table and
computes the indices with ``scipy.stats.sobol_indices`` (second order with
SALib).
"""

from __future__ import annotations

import json
import math
import numbers
import warnings
from collections.abc import Iterable, Mapping
from typing import Any

from mango.util.seeding import SeedTree, name_key

from ._util import canonical_json, require_pandas
from .designs import SOBOL_BLOCKS
from .errors import StudyError
from .store import DESIGN_COLUMNS, FRAME_COLUMNS

__all__ = ["frame_sobol_indices", "SOBOL_MIN_N", "SOBOL_TOLERANCE"]

#: Fewer base samples than this give a warning (the indices are unreliable).
SOBOL_MIN_N = 64

#: Estimates further than this outside ``[0, 1]`` give a warning.
SOBOL_TOLERANCE = 0.05


def _real(value: Any) -> bool:
    return (
        isinstance(value, numbers.Real)
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _sobol_outcomes(frame: Any, outcomes: Any, specs: Mapping[str, dict]) -> list[str]:
    factor_paths: set[str] = set()
    for spec in specs.values():
        factor_paths.update(spec.get("factors") or {})
    skip = (
        set(FRAME_COLUMNS)
        | set(DESIGN_COLUMNS)
        | factor_paths
        | set(getattr(frame, "override_columns", ()))
        | {"plan"}
    )
    available = [
        name
        for name in frame.columns
        if isinstance(name, str) and name not in skip and not name.endswith(":label")
    ]

    def numeric(name: str) -> bool:
        values = [value for value in frame[name].tolist() if not _missing(value)]
        return bool(values) and all(_real(value) for value in values)

    if outcomes is None:
        names = [name for name in available if numeric(name)]
        if not names:
            raise StudyError(
                f"The frame has no numeric outcome column (outcomes: {available}); "
                "declare outcomes in the study or pass outcomes= to load_study."
            )
        return names
    names = [outcomes] if isinstance(outcomes, str) else list(outcomes)
    unknown = [name for name in names if name not in available]
    if unknown:
        raise KeyError(f"No outcome(s) {unknown} in this frame; outcomes: {available}.")
    for name in names:
        if not numeric(name):
            raise StudyError(
                f"Outcome {name!r} has no numeric values; Sobol' indices need a "
                "number per run."
            )
    return names


def _group_name(values: Mapping[str, Any]) -> str:
    if not values:
        return "the design"
    return "the runs with " + ", ".join(
        f"{path}={json.dumps(value)}" for path, value in values.items()
    )


def _saltelli_scipy(f_a, f_b, f_ab, n: int) -> tuple[Any, Any]:
    try:
        from scipy.stats import sobol_indices
    except ImportError as exc:
        raise ImportError(
            "StudyFrame.sobol_indices needs scipy >= 1.11: pip install "
            "'mango-agents[scenario-all]' (or pip install scipy)"
        ) from exc
    import numpy as np

    # two identical outputs keep scipy's squeezed results 2-d (one factor
    # and one output give a scalar that scipy then fails to index)
    func = {
        "f_A": np.repeat(f_a, 2, axis=0),
        "f_B": np.repeat(f_b, 2, axis=0),
        "f_AB": np.repeat(f_ab, 2, axis=1),
    }
    d = f_ab.shape[0]
    try:
        result = sobol_indices(func=func, n=n)
    except ValueError as exc:
        raise StudyError(
            f"scipy.stats.sobol_indices cannot analyse the design ({exc}); a Sobol "
            "design has a power-of-two number of samples, keep all its samples in "
            "the frame."
        ) from None
    first = np.asarray(result.first_order).reshape(2, d)[0]
    total = np.asarray(result.total_order).reshape(2, d)[0]
    return first, total


def _saltelli_2010(f_a, f_b, f_ab, np: Any) -> tuple[Any, Any]:
    """First-order (Saltelli 2010) and total (Jansen) indices along the last axis.

    The estimators of ``scipy.stats.sobol_indices``; *f_a*/*f_b* have shape
    ``(..., n)``, *f_ab* ``(d, ..., n)``.
    """
    var = np.var(np.stack([f_a, f_b]), axis=(0, -1))
    with np.errstate(divide="ignore", invalid="ignore"):
        first = np.mean(f_b * (f_ab - f_a), axis=-1) / var
        total = 0.5 * np.mean((f_a - f_ab) ** 2, axis=-1) / var
    return first, total


def _bootstrap_conf(
    f_a, f_b, f_ab, *, confidence: float, n_resamples: int, rng: Any, np: Any
) -> tuple[Any, Any]:
    """Half-widths of percentile bootstrap intervals of S1 and ST (like SALib's ``*_conf``).

    The *n* base samples are resampled with replacement *n_resamples*
    times; the half-width is half the distance between the
    ``(1 - confidence) / 2`` and ``(1 + confidence) / 2`` quantiles of the
    resampled indices.  (SALib uses ``z * std`` of the resamples, which a
    few degenerate resamples of a small design inflate without bound.)
    """
    n = f_a.shape[-1]
    picks = rng.integers(0, n, size=(n_resamples, n))
    first, total = _saltelli_2010(
        f_a[..., picks], f_b[..., picks], f_ab[..., picks], np
    )  # shapes (d, 1, n_resamples)
    levels = [50 * (1 - confidence), 50 * (1 + confidence)]
    halves = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for indices in (first, total):
            flat = indices.reshape(len(indices), -1)
            flat = np.where(np.isfinite(flat), flat, np.nan)
            low, high = np.nanpercentile(flat, levels, axis=-1)
            halves.append((high - low) / 2)
    return halves[0], halves[1]


def _second_order_salib(
    y, names: list[str], *, confidence: float, n_resamples: int, seed: int
) -> tuple[Any, Any]:
    try:
        from SALib.analyze import sobol as salib_sobol
    except ImportError as exc:
        raise ImportError(
            "sobol_indices(second_order=True) needs SALib: pip install SALib"
        ) from exc
    import numpy as np

    problem = {
        "num_vars": len(names),
        "names": list(names),
        "bounds": [[0.0, 1.0]] * len(names),
    }
    result = salib_sobol.analyze(
        problem,
        y,
        calc_second_order=True,
        print_to_console=False,
        conf_level=confidence,
        num_resamples=n_resamples,
        seed=seed,
    )
    return np.asarray(result["S2"]), np.asarray(result["S2_conf"])


def frame_sobol_indices(
    frame: Any,
    outcomes: Any,
    second_order: bool,
    *,
    confidence: float | None = 0.95,
    n_resamples: int = 999,
) -> Any:
    """The implementation of :meth:`StudyFrame.sobol_indices`."""
    import numpy as np

    pd = require_pandas("Loading studies needs")
    if confidence is not None and (
        not isinstance(confidence, int | float)
        or isinstance(confidence, bool)
        or not 0 < confidence < 1
    ):
        raise ValueError(
            f"confidence must be a level between 0 and 1 (e.g. 0.95) or None, got "
            f"{confidence!r}."
        )
    if (
        isinstance(n_resamples, bool)
        or not isinstance(n_resamples, numbers.Integral)
        or n_resamples < 2
    ):
        raise ValueError(f"n_resamples must be an int >= 2, got {n_resamples!r}.")
    study_seed = (frame.study_spec or {}).get("seed")
    seeds = SeedTree(study_seed if isinstance(study_seed, int) else 0).child(
        "analysis", "sobol"
    )
    issues: dict[str, list] = {"range": [], "small": [], "constant": []}
    store = frame._store()
    specs = {spec["run_id"]: spec for spec in store.read_runs()}
    names = _sobol_outcomes(frame, outcomes, specs)
    values = {name: frame[name].tolist() for name in names}
    statuses = frame["status"].tolist()
    indices = frame["index"].tolist()
    entries: dict[Any, list] = {}
    columns_of: dict[Any, dict[int, str]] = {}
    for position, run_id in enumerate(frame["run_id"].tolist()):
        spec = specs.get(run_id)
        if spec is None:
            raise StudyError(
                f"Run {run_id} of this frame is not in {store.runs_file} any more; call "
                "load_study again."
            )
        design = spec.get("design") or {}
        for meta in [design, *(design.get("duplicates") or [])]:
            if meta.get("block") not in SOBOL_BLOCKS:
                continue
            sub = meta.get("subexperiment")
            if meta.get("column") is not None:
                column = int(meta["column"])
                columns_of.setdefault(sub, {})[column] = meta.get(
                    "factor", f"column {column}"
                )
            entries.setdefault(sub, []).append((meta, spec, position))
    if not entries:
        design = (frame.study_spec or {}).get("design")
        raise StudyError(
            "sobol_indices needs the runs of a Sobol design (design=Sobol(n), or "
            f"design: {{sobol: {{n: 256}}}} in a study file); this frame has none "
            f"(the study's design: {design!r})."
        )
    with_subs = any(sub is not None for sub in entries)
    rows: list[dict] = []
    group_columns: list[str] = ["subexperiment"] if with_subs else []
    for sub, items in entries.items():
        where = f"subexperiment {sub}: " if with_subs else ""
        columns = columns_of.get(sub, {})
        d = len(columns)
        if d == 0 or sorted(columns) != list(range(d)):
            raise StudyError(
                f"{where}the frame lacks the AB runs of some sampled factors; keep all "
                "runs of the Sobol design in the frame."
            )
        factors = [columns[i] for i in range(d)]
        varying = _crossed_levels(specs.values(), sub, factors)
        groups: dict[tuple, dict] = {}
        for meta, spec, position in items:
            point = spec.get("factors") or {}
            key = tuple(canonical_json(point.get(path)) for path in varying)
            group = groups.get(key)
            if group is None:
                labels = spec.get("labels") or {}
                group = groups[key] = {"values": {}, "slots": {}}
                for path in varying:
                    group["values"][path] = point.get(path)
                    if path in labels:
                        group["values"][f"{path}:label"] = labels[path]
            slot = (meta["block"], meta.get("column"), int(meta["sample"]))
            group["slots"].setdefault(slot, []).append(position)
        for group in groups.values():
            for column in group["values"]:
                if column not in group_columns:
                    group_columns.append(column)
            label = where + _group_name(
                {k: v for k, v in group["values"].items() if not k.endswith(":label")}
            )
            rows += _analyse_group(
                group,
                factors,
                names,
                values,
                statuses,
                indices,
                label=label,
                second_order=second_order,
                fixed={"subexperiment": sub} if with_subs else {},
                np=np,
                confidence=confidence,
                n_resamples=n_resamples,
                # one bootstrap stream per group: every outcome resamples the
                # same base samples (so an outcome + c gives the same intervals)
                rng=seeds.child("bootstrap", name_key(label)),
                issues=issues,
            )
    if second_order:
        stat_columns = ["outcome", "factor", "other", "S2"]
        stat_columns += ["S2_conf"] if confidence is not None else []
    elif confidence is not None:
        stat_columns = ["outcome", "factor", "S1", "S1_conf", "ST", "ST_conf"]
    else:
        stat_columns = ["outcome", "factor", "S1", "ST"]
    _warn_unreliable(issues)
    return pd.DataFrame(rows, columns=group_columns + stat_columns)


def _warn_unreliable(issues: Mapping[str, list[str]]) -> None:
    """One warning for small designs, estimates outside ``[0, 1]`` and constant outcomes."""
    parts = []
    constant = issues.get("constant") or []
    if constant:
        parts.append(
            f"outcome{'s' * (len(constant) != 1)} {', '.join(map(repr, constant[:3]))}"
            f"{' ...' if len(constant) > 3 else ''} "
            f"{'are' if len(constant) != 1 else 'is'} constant over the design, so "
            "there is no variance to apportion: the indices are NaN"
        )
    small = sorted(set(issues["small"]))
    if small:
        parts.append(
            f"n={' and '.join(str(n) for n in small)} base samples (fewer than "
            f"{SOBOL_MIN_N} give unreliable indices)"
        )
    outside = issues["range"]
    if outside:
        parts.append(
            f"{len(outside)} estimate{'s' * (len(outside) != 1)} outside [0, 1] by "
            f"more than {SOBOL_TOLERANCE} "
            f"({'; '.join(outside[:3])}{' ...' if len(outside) > 3 else ''}), which "
            "is Monte Carlo error of the estimators"
        )
    if constant and len(parts) == 1:
        warnings.warn("sobol_indices: " + parts[0] + ".", UserWarning, stacklevel=4)
        return
    if parts:
        warnings.warn(
            "sobol_indices: "
            + "; ".join(parts)
            + ". Increase n of the Sobol design (a few hundred or more) and check the "
            "*_conf columns before reading the indices.",
            UserWarning,
            stacklevel=4,
        )


def _crossed_levels(
    specs: Iterable[Mapping], sub: Any, sampled: list[str]
) -> list[str]:
    """Paths of the not-sampled factors that vary over the Sobol runs of *sub*.

    Taken from the whole run table, so a slice of the frame keeps the
    level columns of the full study.
    """
    paths: list[str] = []
    seen: dict[str, set] = {}
    for spec in specs:
        design = spec.get("design") or {}
        if design.get("block") is None or design.get("subexperiment") != sub:
            continue
        for path, value in (spec.get("factors") or {}).items():
            if path in sampled:
                continue
            if path not in seen:
                paths.append(path)
                seen[path] = set()
            seen[path].add(canonical_json(value))
    return [path for path in paths if len(seen[path]) > 1]


def _analyse_group(
    group: dict,
    factors: list[str],
    names: list[str],
    values: Mapping[str, list],
    statuses: list,
    indices: list,
    *,
    label: str,
    second_order: bool,
    fixed: dict,
    np: Any,
    confidence: float | None = 0.95,
    n_resamples: int = 999,
    rng: Any = None,
    issues: dict | None = None,
) -> list[dict]:
    slots = group["slots"]
    d = len(factors)
    n = 1 + max(sample for _, _, sample in slots)
    issues = issues if issues is not None else {"range": [], "small": []}
    if n < SOBOL_MIN_N:
        issues["small"].append(n)
    order: list[tuple] = []
    for j in range(n):
        order.append(("A", None, j))
        order += [("AB", i, j) for i in range(d)]
        if second_order:
            order += [("BA", i, j) for i in range(d)]
        order.append(("B", None, j))
    missing = [slot for slot in order if slot not in slots]
    if missing:
        if second_order and not any(block == "BA" for block, _, _ in slots):
            raise StudyError(
                f"{label}: the design has no second-order (BA) points; run the study "
                "with Sobol(n, second_order=True) for second-order indices."
            )
        block, column, sample = missing[0]
        point = block if column is None else f"{block}_{column}"
        raise StudyError(
            f"{label}: {len(missing)} of {len(order)} points of the Sobol design have "
            f"no run in this frame (first: {point} of sample {sample}); keep all runs "
            "of the design in the frame."
        )
    positions = sorted({p for slot in order for p in slots[slot]})
    not_done = [indices[p] for p in positions if statuses[p] != "done"]
    if not_done:
        raise StudyError(
            f"{label}: {len(not_done)} run(s) of the Sobol design are not done (run "
            f"indices {not_done[:10]}{' ...' if len(not_done) > 10 else ''}); finish "
            "them with study.run(), or select fewer replications."
        )
    rows = []
    streams = rng
    for name in names:
        rng = streams.generator() if isinstance(streams, SeedTree) else streams
        column_values = values[name]
        bad = [indices[p] for p in positions if not _real(column_values[p])]
        if bad:
            raise StudyError(
                f"{label}: outcome {name!r} has no finite value for run(s) "
                f"{bad[:10]}{' ...' if len(bad) > 10 else ''}; Sobol' indices need a "
                "number per run (recompute it with load_study(path, outcomes=...))."
            )
        means = {
            slot: float(np.mean([column_values[p] for p in slots[slot]]))
            for slot in order
        }
        base = {**fixed, **group["values"], "outcome": name}
        if second_order:
            y = np.array([means[slot] for slot in order])
            s2, conf = _second_order_salib(
                y,
                factors,
                confidence=0.95 if confidence is None else confidence,
                n_resamples=n_resamples,
                seed=int(rng.integers(2**31)) if rng is not None else 0,
            )
            for i in range(d):
                for k in range(i + 1, d):
                    row = {
                        **base,
                        "factor": factors[i],
                        "other": factors[k],
                        "S2": float(s2[i, k]),
                    }
                    if confidence is not None:
                        row["S2_conf"] = float(conf[i, k])
                    rows.append(row)
            continue
        f_a = np.array([[means[("A", None, j)] for j in range(n)]])
        f_b = np.array([[means[("B", None, j)] for j in range(n)]])
        f_ab = np.array([[[means[("AB", i, j)] for j in range(n)]] for i in range(d)])
        both = np.concatenate([f_a, f_b], axis=-1)
        centre = float(np.mean(both))
        spread = float(np.var(both))
        if not spread > 1e-24 * centre**2:  # constant: no variance to apportion
            where = f" in {label}" if group["values"] or fixed else ""
            issues.setdefault("constant", []).append(f"{name}{where}")
            for factor in factors:
                row = {**base, "factor": factor, "S1": math.nan}
                if confidence is not None:
                    row["S1_conf"] = math.nan
                row["ST"] = math.nan
                if confidence is not None:
                    row["ST_conf"] = math.nan
                rows.append(row)
            continue
        # centred as scipy does: the Saltelli 2010 first-order estimator is not
        # shift-invariant, so a bootstrap of raw values inflates S1_conf with
        # the outcome's mean (Wq around 4: 0.42 instead of 0.16; +1000: 230)
        f_a, f_b, f_ab = f_a - centre, f_b - centre, f_ab - centre
        first, total = _saltelli_scipy(f_a, f_b, f_ab, n)
        if confidence is not None:
            conf_first, conf_total = _bootstrap_conf(
                f_a,
                f_b,
                f_ab,
                confidence=confidence,
                n_resamples=n_resamples,
                rng=rng if rng is not None else np.random.default_rng(0),
                np=np,
            )
        for i, factor in enumerate(factors):
            row = {**base, "factor": factor, "S1": float(first[i])}
            if confidence is not None:
                row["S1_conf"] = float(conf_first[i])
            row["ST"] = float(total[i])
            if confidence is not None:
                row["ST_conf"] = float(conf_total[i])
            rows.append(row)
            for index in ("S1", "ST"):
                value = row[index]
                if math.isfinite(value) and not (
                    -SOBOL_TOLERANCE <= value <= 1 + SOBOL_TOLERANCE
                ):
                    where = f" in {label}" if group["values"] or fixed else ""
                    issues["range"].append(
                        f"{index}={value:.2f} of {name}/{factor}{where}"
                    )
    return rows

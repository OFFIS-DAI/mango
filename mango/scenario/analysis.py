"""Replication-aware summaries of a study table.

The implementation behind :meth:`StudyFrame.summary
<mango.scenario.StudyFrame.summary>`, :meth:`StudyFrame.paired
<mango.scenario.StudyFrame.paired>`, :meth:`StudyFrame.expand
<mango.scenario.StudyFrame.expand>` and the factor columns of
:meth:`StudyFrame.timeseries <mango.scenario.StudyFrame.timeseries>`.

Confidence intervals are Student-t intervals over *independent units*:
runs that share their seed are averaged into one unit first.  Within one
design point every replication has its own seed, so the units are the
replications.  A summary over several design points under
``common_random_numbers=True`` (one seed per replication, shared by every
point) has one unit per replication, not one per run, because runs that
share a seed share their noise; counting them as independent would be
pseudo-replication.

Example::

    from mango.scenario.analysis import t_quantile

    round(t_quantile(0.975, 9), 3)     # 2.262
"""

from __future__ import annotations

import math
import numbers
from collections.abc import Mapping, Sequence
from statistics import NormalDist
from typing import Any

from ._util import canonical_json, did_you_mean, require_pandas, warn_user
from .errors import StudyError

__all__ = ["t_quantile", "mean_interval", "SUMMARY_COLUMNS"]

#: Statistic columns of :meth:`StudyFrame.summary` (``ci_low``/``ci_high``
#: only with a confidence level).
SUMMARY_COLUMNS: tuple[str, ...] = (
    "outcome",
    "runs",
    "missing",
    "n",
    "mean",
    "std",
    "ci_low",
    "ci_high",
)


def t_quantile(p: float, df: int) -> float:
    """The *p* quantile of Student's t distribution with *df* degrees of freedom.

    Uses ``scipy.stats.t`` when scipy is installed, else exact formulas for
    ``df`` 1 and 2 and the Cornish-Fisher expansion (Abramowitz and Stegun
    26.7.5; error below 0.001 for ``df >= 3`` at the usual levels).

    Example::

        round(t_quantile(0.975, 2), 3)     # 4.303
    """
    if df < 1:
        return math.nan
    try:
        from scipy.stats import t
    except ImportError:
        return _t_quantile_approx(p, df)
    return float(t.ppf(p, df))


def _t_quantile_approx(p: float, df: int) -> float:
    if df == 1:
        return math.tan(math.pi * (p - 0.5))
    if df == 2:
        return (2 * p - 1) / math.sqrt(2 * p * (1 - p))
    z = NormalDist().inv_cdf(p)
    g1 = (z**3 + z) / 4
    g2 = (5 * z**5 + 16 * z**3 + 3 * z) / 96
    g3 = (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / 384
    g4 = (79 * z**9 + 776 * z**7 + 1482 * z**5 - 1920 * z**3 - 945 * z) / 92160
    return z + g1 / df + g2 / df**2 + g3 / df**3 + g4 / df**4


def mean_interval(
    values: Sequence[float], confidence: float | None
) -> dict[str, float]:
    """``{n, mean, std, ci_low, ci_high}`` of independent *values* (t interval).

    ``std`` is the sample standard deviation; with fewer than two values
    it and the interval are NaN.  Without *confidence* the interval keys
    are left out.

    Example::

        mean_interval([1.0, 2.0, 3.0], 0.95)["ci_high"]     # 5.484...
    """
    n = len(values)
    mean = math.fsum(values) / n if n else math.nan
    if n > 1:
        std = math.sqrt(math.fsum((v - mean) ** 2 for v in values) / (n - 1))
    else:
        std = math.nan
    stats = {"n": n, "mean": mean, "std": std}
    if confidence is not None:
        half = (
            t_quantile(0.5 + confidence / 2, n - 1) * std / math.sqrt(n)
            if n > 1
            else math.nan
        )
        stats["ci_low"] = mean - half
        stats["ci_high"] = mean + half
    return stats


# --------------------------------------------------------------------------- helpers


def _check_confidence(confidence: Any) -> None:
    if confidence is None:
        return
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, numbers.Real)
        or not 0 < confidence < 1
    ):
        raise ValueError(
            f"confidence must be a level between 0 and 1 (e.g. 0.95) or None, got "
            f"{confidence!r}."
        )


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _number(value: Any) -> float | None:
    """*value* as a float (bools as 0/1), ``None`` when missing or not a number."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, numbers.Real) and not _missing(value):
        value = float(value)
        return value if math.isfinite(value) else None
    return None


def _key(values: Sequence[Any]) -> str:
    return canonical_json([None if _missing(v) else v for v in values])


def _structural_columns(frame: Any) -> set[str]:
    """Columns that describe a run, not a result of it."""
    from .store import DESIGN_COLUMNS, FRAME_COLUMNS

    columns = set(FRAME_COLUMNS) | set(DESIGN_COLUMNS) | {"plan"}
    columns |= set(factor_columns(frame))
    columns |= set(getattr(frame, "override_columns", ()) or ())
    columns |= {c for c in frame.columns if isinstance(c, str) and c.endswith(":label")}
    return columns


def factor_columns(frame: Any) -> list[str]:
    """The factor and ``":label"`` columns of a study frame (in frame order)."""
    known = tuple(getattr(frame, "factor_columns", ()) or ())
    if not known and getattr(frame, "study_path", None) is not None:
        from .store import _factor_columns

        known = tuple(_factor_columns(_specs(frame).values())[2])
    return [column for column in frame.columns if column in known]


def _specs(frame: Any) -> dict[str, dict]:
    """Run id -> run spec of every plan of the frame's study folder."""
    from .store import _plan_specs

    store = frame._store()
    specs, _, _, _ = _plan_specs(store, "all" if "plan" in frame.columns else "current")
    return {spec["run_id"]: spec for spec in specs}


def _seed_keys(frame: Any) -> dict[str, str]:
    """Run id -> canonical seed (runs with equal keys share their random numbers)."""
    if getattr(frame, "study_path", None) is None:
        return {}
    return {
        run_id: canonical_json(spec.get("seed"))
        for run_id, spec in _specs(frame).items()
    }


def outcome_columns(frame: Any, outcomes: Any) -> list[str]:
    """The outcome columns to summarise: *outcomes*, or every numeric result column."""
    pd = require_pandas("Summaries need")
    structural = _structural_columns(frame)
    available = [
        c
        for c in frame.columns
        if isinstance(c, str) and c not in structural and c != "outcome_errors"
    ]
    if outcomes is None:
        names = [
            c
            for c in available
            if pd.api.types.is_numeric_dtype(frame[c])
            or pd.api.types.is_bool_dtype(frame[c])
        ]
        if not names:
            raise StudyError(
                f"The frame has no numeric outcome column (columns: {available}); "
                "declare outcomes in the study, pass outcomes= to load_study, or "
                "expand dict outcomes with frame.expand()."
            )
        return names
    names = [outcomes] if isinstance(outcomes, str) else list(outcomes)
    unknown = [name for name in names if name not in frame.columns]
    if unknown:
        raise KeyError(
            f"No column(s) {unknown} in this frame.{did_you_mean(unknown[0], available)} "
            f"Outcomes: {available}."
        )
    return names


def _check_columns(frame: Any, columns: Sequence[str], what: str) -> None:
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        known = [c for c in frame.columns if isinstance(c, str)]
        raise KeyError(
            f"{what}: no column {missing[0]!r} in this frame."
            f"{did_you_mean(str(missing[0]), known)}"
        )


def _units(
    entries: Sequence[tuple[str, float]],
) -> list[float]:
    """Average the values of entries with the same unit key (runs sharing a seed)."""
    sums: dict[str, list[float]] = {}
    for key, value in entries:
        sums.setdefault(key, []).append(value)
    return [math.fsum(values) / len(values) for values in sums.values()]


def _done(frame: Any) -> Any:
    if "status" not in frame.columns:
        return frame
    return frame[frame["status"] == "done"]


# --------------------------------------------------------------------------- summary


def frame_summary(frame: Any, outcomes: Any, by: Any, confidence: float | None) -> Any:
    """The implementation of :meth:`StudyFrame.summary`."""
    pd = require_pandas("Summaries need")
    _check_confidence(confidence)
    names = outcome_columns(frame, outcomes)
    if by is None:
        if "point" not in frame.columns:
            raise KeyError(
                "summary(by=None) groups by the 'point' column, which this frame "
                "lacks; pass by=[columns]."
            )
        by_columns = ["point"]
        described = [
            c
            for c in ("subexperiment", *factor_columns(frame))
            if c in frame.columns and c != "point"
        ]
        described += [
            c
            for c in (*(getattr(frame, "override_columns", ()) or ()), "plan")
            if c in frame.columns and c not in described
        ]
    else:
        by_columns = [by] if isinstance(by, str) else list(by)
        described = []
    _check_columns(frame, by_columns, "summary(by=...)")
    done = _done(frame)
    seeds = _seed_keys(frame)
    groups: dict[str, dict] = {}
    run_ids = (
        done["run_id"].tolist() if "run_id" in done.columns else [None] * len(done)
    )
    key_values = [done[c].tolist() for c in by_columns]
    described_values = [done[c].tolist() for c in described]
    outcome_values = {name: done[name].tolist() for name in names}
    dropped: dict[str, int] = {}
    for i, run_id in enumerate(run_ids):
        values = [column[i] for column in key_values]
        group = groups.setdefault(
            _key(values),
            {
                "by": dict(zip(by_columns, values)),
                "described": {c: col[i] for c, col in zip(described, described_values)},
                "rows": [],
            },
        )
        group["rows"].append(i)
    rows = []
    for group in groups.values():
        for name in names:
            entries = []
            for i in group["rows"]:
                value = _number(outcome_values[name][i])
                if value is not None:
                    run_id = run_ids[i]
                    entries.append((seeds.get(run_id, f"run:{run_id}:{i}"), value))
            lacking = len(group["rows"]) - len(entries)
            if lacking:
                dropped[name] = dropped.get(name, 0) + lacking
            stats = mean_interval(_units(entries), confidence)
            rows.append(
                {
                    **group["by"],
                    **group["described"],
                    "outcome": name,
                    "runs": len(entries),
                    "missing": lacking,
                    **stats,
                }
            )
    for name, count in dropped.items():
        warn_user(
            f"summary: {count} of {len(run_ids)} done runs have no value of {name!r} "
            "(NaN/None, e.g. a censored time to an event) and are left out of the "
            "mean ('missing' column): a mean over the rest is biased when the "
            "missing values are not random; report them, or encode censoring.",
        )
    stat_columns = [
        c
        for c in SUMMARY_COLUMNS
        if confidence is not None or c not in ("ci_low", "ci_high")
    ]
    return pd.DataFrame(rows, columns=[*by_columns, *described, *stat_columns])


# --------------------------------------------------------------------------- paired


def _level_of(baseline: Any, levels: Sequence[Any], labels: Mapping[Any, Any]) -> Any:
    """The cell value of *baseline* (given as a value, its label or a dict/list value)."""
    if baseline is None:
        return levels[0]
    for level in levels:
        if level == baseline or labels.get(level) == baseline:
            return level
        if isinstance(baseline, dict | list) and level == canonical_json(baseline):
            return level
    shown = [labels.get(level, level) for level in levels]
    raise KeyError(
        f"baseline {baseline!r} is not a level of the lever; levels: {shown}."
    )


def _resolve_lever(frame: Any, lever: Any) -> str:
    """The factor column of *lever* (a ``"<path>:label"`` column stands for ``<path>``)."""
    if isinstance(lever, str) and lever.endswith(":label"):
        base = lever[: -len(":label")]
        if base in frame.columns:
            return base
        if lever in frame.columns:
            raise StudyError(
                f"paired: {lever!r} is a label column without its factor column "
                f"{base!r} in this frame; pass the factor path {base!r} with the "
                "frame that has it."
            )
    return lever


def _shown(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():  # 3.0 of a column with NaN
        return str(int(value))
    return value if isinstance(value, str) else canonical_json(value)


class _Identity:
    """A compared level: the lever value plus the values of variant-only factors."""

    __slots__ = ("key", "level", "variants", "variant_labels", "text")

    def __init__(
        self, key: str, level: Any, variants: dict, variant_labels: dict, text: str
    ):
        self.key = key
        self.level = level
        self.variants = variants
        self.variant_labels = variant_labels
        self.text = text


def _variant_columns(
    done: Any,
    rows: Sequence[int],
    level_values: Sequence[Any],
    candidates: Sequence[str],
) -> list[str]:
    """Factor columns that every run of some level leaves unset but other runs set.

    These are the settings of one variant (a subexperiment): they cannot
    match across levels, so they are part of a level's identity instead.
    """
    variants = []
    for column in candidates:
        values = done[column].tolist()
        set_by: dict[str, bool] = {}
        for i in rows:
            key = _key([level_values[i]])
            set_by[key] = set_by.get(key, False) or not _missing(values[i])
        if any(set_by.values()) and not all(set_by.values()):
            variants.append(column)
    return variants


def _paired_reason(
    unpaired: Sequence[int],
    sides: Mapping[int, Sequence[int]],
    context: Sequence[str],
    context_values: Sequence[list],
    not_done: int,
) -> str:
    """Why runs found no partner: a factor the other side never has, or a missing run.

    *sides* maps an unpaired run to the runs it could pair with (the other
    side of the comparison).  A context column whose value the other side
    never takes (a factor one subexperiment sets) keeps the run apart for
    good; otherwise its counterpart is just not in the frame.
    """
    differing: dict[str, int] = {}
    absent = 0
    for i in unpaired:
        others = sides.get(i) or []
        columns = [
            c
            for c, values in zip(context, context_values)
            if others and _key([values[i]]) not in {_key([values[j]]) for j in others}
        ]
        if not columns:
            absent += 1
        for column in columns:
            differing[column] = differing.get(column, 0) + 1
    parts = []
    if differing:
        ranked = sorted(differing, key=lambda c: -differing[c])
        parts.append(
            f"their {', '.join(map(repr, ranked))} never occur{'s' * (len(ranked) == 1)} "
            "with the other level(s) (runs pair only when every other factor and the "
            "replication are equal: compare within one subexperiment, pass the "
            "column as by=, or filter the frame)"
        )
    if absent:
        where = (
            f"; {not_done} run{'s' * (not_done != 1)} of the frame "
            f"{'are' if not_done != 1 else 'is'} not done (failed or pending)"
            if not_done
            else ""
        )
        parts.append(
            f"{absent} {'have' if absent != 1 else 'has'} no counterpart in this frame "
            f"(a filtered frame, or a run that did not finish){where}"
        )
    return "; ".join(parts) or "no counterpart"


def frame_paired(
    frame: Any,
    lever: str,
    outcomes: Any,
    baseline: Any,
    by: Any,
    confidence: float | None,
    pairs: bool,
) -> Any:
    """The implementation of :meth:`StudyFrame.paired`."""
    pd = require_pandas("Summaries need")
    _check_confidence(confidence)
    lever = _resolve_lever(frame, lever)
    _check_columns(frame, [lever], "paired(lever=...)")
    names = outcome_columns(frame, outcomes)
    by_columns = [] if by is None else ([by] if isinstance(by, str) else list(by))
    _check_columns(frame, by_columns, "paired(by=...)")
    label_column = f"{lever}:label"
    labeled = label_column in frame.columns
    if lever in by_columns or label_column in by_columns:
        raise StudyError(f"paired: the lever {lever!r} cannot also be a by= column.")
    factors = [
        c
        for c in factor_columns(frame)
        if c not in (lever, label_column) and not c.endswith(":label")
    ]
    extra = [
        c
        for c in (*(getattr(frame, "override_columns", ()) or ()), "plan")
        if c in frame.columns and c not in factors
    ]
    done = _done(frame)
    not_done = len(frame) - len(done)
    seeds = _seed_keys(frame)
    level_values = done[lever].tolist()
    label_values = done[label_column].tolist() if labeled else [None] * len(done)
    labels: dict[Any, Any] = {}
    for level, label in zip(level_values, label_values):
        if not _missing(level) and not _missing(label):
            labels.setdefault(level, label)
    rows = [i for i, level in enumerate(level_values) if not _missing(level)]
    unset = len(level_values) - len(rows)
    if unset:
        warn_user(
            f"paired: {unset} done run{'s' * (unset != 1)} "
            f"{'have' if unset != 1 else 'has'} no value of {lever!r} (e.g. a "
            "subexperiment that does not set it) and "
            f"{'are' if unset != 1 else 'is'} left out.",
        )
    variants = _variant_columns(
        done, rows, level_values, [c for c in factors if c not in by_columns]
    )
    context = [c for c in factors if c not in variants]
    context += [c for c in extra if c not in context]
    context += [c for c in by_columns if c not in context]
    variant_values = {c: done[c].tolist() for c in variants}
    variant_labels = {
        c: done[f"{c}:label"].tolist() if f"{c}:label" in done.columns else None
        for c in variants
    }
    identities: dict[str, _Identity] = {}
    identity_of: dict[int, str] = {}
    for i in rows:
        level = level_values[i]
        chosen = {
            c: variant_values[c][i]
            for c in variants
            if not _missing(variant_values[c][i])
        }
        key = _key([level, *(variant_values[c][i] for c in variants)])
        if key not in identities:
            names_of = {}
            for c in chosen:
                label = None if variant_labels[c] is None else variant_labels[c][i]
                if label is not None and not _missing(label):
                    names_of[c] = label
            text = str(labels.get(level, _shown(level)))
            if chosen:
                described = [
                    f"{c}={names_of.get(c, _shown(value))}"
                    for c, value in chosen.items()
                ]
                text += f" ({', '.join(described)})"
            identities[key] = _Identity(key, level, chosen, names_of, text)
        identity_of[i] = key
    levels = list(dict.fromkeys(level_values[i] for i in rows))
    if len(identities) < 2:
        raise StudyError(
            f"paired: the done runs have {len(identities)} level(s) of {lever!r}; "
            "pairing needs at least two."
        )
    base = _baseline_identity(baseline, lever, identities, levels, labels, variants)
    replications = done["replication"].tolist()
    run_ids = done["run_id"].tolist()
    context_values = [done[c].tolist() for c in context]
    cells: dict[str, dict] = {}
    for i in rows:
        values = [column[i] for column in context_values]
        key = _key([*values, replications[i]])
        cell = cells.setdefault(
            key,
            {"context": dict(zip(context, values)), "rep": replications[i], "runs": {}},
        )
        cell["runs"].setdefault(identity_of[i], []).append(i)
    for cell in cells.values():
        for key, found in cell["runs"].items():
            if len(found) > 1:
                raise StudyError(
                    f"paired: runs {[run_ids[i] for i in found]} are all "
                    f"{identities[key].text!r} with the same other factors and "
                    f"replication {cell['rep']}, so the pairing is ambiguous; filter "
                    "the frame (e.g. by plan or subexperiment) to one of them."
                )
    outcome_values = {name: done[name].tolist() for name in names}
    pair_rows = []
    missing: dict[str, int] = {}
    unpaired: list[int] = []
    shared = total = 0
    for cell in cells.values():
        base_rows = cell["runs"].get(base.key)
        others = [key for key in cell["runs"] if key != base.key]
        if base_rows is None:
            unpaired += [i for key in others for i in cell["runs"][key]]
            continue
        if not others:
            unpaired += base_rows
            continue
        base_i = base_rows[0]
        for key in others:
            i = cell["runs"][key][0]
            if run_ids[i] == run_ids[base_i]:  # never a run with itself
                continue
            identity = identities[key]
            seed_pair = (seeds.get(run_ids[i]), seeds.get(run_ids[base_i]))
            total += 1
            shared += seed_pair[0] is not None and seed_pair[0] == seed_pair[1]
            for name in names:
                value = _number(outcome_values[name][i])
                base_value = _number(outcome_values[name][base_i])
                row = {
                    **cell["context"],
                    "replication": cell["rep"],
                    lever: identity.level,
                    **({label_column: labels.get(identity.level)} if labeled else {}),
                    **{c: identity.variants.get(c) for c in variants},
                    "outcome": name,
                    "baseline": base.text,
                    "baseline_value": base_value,
                    "value": value,
                    "difference": None
                    if value is None or base_value is None
                    else value - base_value,
                    "run_id": run_ids[i],
                    "baseline_run_id": run_ids[base_i],
                    "_identity": key,
                    "_unit": canonical_json(list(seed_pair))
                    if None not in seed_pair
                    else f"pair:{run_ids[i]}",
                }
                if row["difference"] is None:
                    missing[name] = missing.get(name, 0) + 1
                pair_rows.append(row)
    if unpaired:
        sides = _sides(unpaired, rows, identity_of, base.key)
        reason = _paired_reason(unpaired, sides, context, context_values, not_done)
        message = (
            f"paired: {len(unpaired)} done run{'s' * (len(unpaired) != 1)} of "
            f"{lever!r} {'have' if len(unpaired) != 1 else 'has'} no counterpart "
            f"(the baseline {base.text!r} or another level with the same other "
            f"factors and replication) and {'are' if len(unpaired) != 1 else 'is'} "
            f"left out: {reason}."
        )
        if not pair_rows:
            raise StudyError(message.replace(" and are left out", "", 1))
        warn_user(message)
    for name, count in missing.items():
        warn_user(
            f"paired: {count} pair{'s' * (count != 1)} lack{'s' * (count == 1)} a value "
            f"of {name!r} (NaN/None in the run or its baseline, e.g. a censored "
            f"outcome) and {'are' if count != 1 else 'is'} left out of the mean: "
            "report the count ('missing' column), as dropping them biases the mean.",
        )
    if total and seeds and shared == 0:
        warn_user(
            f"paired: the runs paired across {lever!r} do not share seeds "
            "(common_random_numbers=False, or the factor is an uncertainty under "
            "'levers'), so the differences carry the noise of both runs; declare it "
            "as a lever with common_random_numbers=True or 'levers' for paired "
            "comparisons on common random numbers.",
        )
    raw = _show_raw(lever, labeled, level_values, rows)
    lever_columns = [
        *([lever] if raw else []),
        *([label_column] if labeled else []),
        *variants,
    ]
    if pairs:
        columns = [
            *[c for c in context if c not in variants],
            "replication",
            *lever_columns,
            "outcome",
            "baseline",
            "baseline_value",
            "value",
            "difference",
            "run_id",
            "baseline_run_id",
        ]
        return pd.DataFrame(
            [{c: row.get(c) for c in columns} for row in pair_rows], columns=columns
        )
    groups: dict[str, dict] = {}
    for row in pair_rows:
        head = {c: row[c] for c in by_columns}
        head.update({c: row[c] for c in lever_columns})
        key = _key([*(row[c] for c in by_columns), row["_identity"], row["outcome"]])
        group = groups.setdefault(
            key,
            {
                "head": head,
                "outcome": row["outcome"],
                "baseline": row["baseline"],
                "entries": [],
                "missing": 0,
            },
        )
        if row["difference"] is None:
            group["missing"] += 1
        else:
            group["entries"].append((row["_unit"], row["difference"]))
    result = []
    for group in groups.values():
        stats = mean_interval(_units(group["entries"]), confidence)
        result.append(
            {
                **group["head"],
                "baseline": group["baseline"],
                "outcome": group["outcome"],
                "pairs": len(group["entries"]),
                "missing": group["missing"],
                **stats,
            }
        )
    stat_columns = [
        c
        for c in ("pairs", "missing", "n", "mean", "std", "ci_low", "ci_high")
        if confidence is not None or c not in ("ci_low", "ci_high")
    ]
    columns = [*by_columns, *lever_columns, "baseline", "outcome", *stat_columns]
    return pd.DataFrame(result, columns=columns)


def _show_raw(
    lever: str, labeled: bool, level_values: Sequence[Any], rows: Sequence[int]
) -> bool:
    """Keep the raw lever column unless a label names it and it holds dict/list JSON."""
    if not labeled:
        return True
    return not all(
        isinstance(level_values[i], str) and level_values[i][:1] in ("{", "[")
        for i in rows
    )


def _sides(
    unpaired: Sequence[int],
    rows: Sequence[int],
    identity_of: Mapping[int, str],
    base_key: str,
) -> dict[int, list[int]]:
    """The runs on the other side of the comparison of each unpaired run."""
    base = [j for j in rows if identity_of[j] == base_key]
    other = [j for j in rows if identity_of[j] != base_key]
    return {i: other if identity_of[i] == base_key else base for i in unpaired}


def _baseline_identity(
    baseline: Any,
    lever: str,
    identities: Mapping[str, _Identity],
    levels: Sequence[Any],
    labels: Mapping[Any, Any],
    variants: Sequence[str],
) -> _Identity:
    """The compared level *baseline* stands for (an error when missing or ambiguous)."""
    wanted: dict[str, Any] = {}
    if isinstance(baseline, Mapping) and lever in baseline:
        wanted = {k: v for k, v in baseline.items() if k != lever}
        unknown = [k for k in wanted if k not in variants]
        if unknown:
            raise StudyError(
                f"paired: baseline keys {unknown} are not variant factors of "
                f"{lever!r}; the variant factors are {list(variants)}."
            )
        baseline = baseline[lever]
    if isinstance(baseline, float) and math.isnan(baseline):
        raise StudyError(
            "paired: the baseline is NaN; pass a level of the lever (runs without a "
            "value of the lever are left out)."
        )
    level = _level_of(baseline, levels, labels)
    candidates = [
        identity
        for identity in identities.values()
        if _key([identity.level]) == _key([level])
        and all(
            _key([value])
            in (
                _key([identity.variants.get(c)]),
                _key([identity.variant_labels.get(c)]),
            )
            for c, value in wanted.items()
        )
    ]
    if not candidates:
        raise KeyError(
            f"baseline {baseline!r} with {wanted} matches no compared level; levels: "
            f"{[identity.text for identity in identities.values()]}."
        )
    if len(candidates) > 1:
        raise StudyError(
            f"paired: the baseline {labels.get(level, _shown(level))!r} covers "
            f"{len(candidates)} variants ({'; '.join(c.text for c in candidates)}); "
            f"name one, e.g. baseline={{{lever!r}: {_shown(level)!r}, "
            f"{variants[0]!r}: ...}}, or filter the frame."
        )
    return candidates[0]


# --------------------------------------------------------------------------- planning


def replications_for(std: float, half_width: float, confidence: float) -> float:
    """Independent units for a Student-t interval of *half_width* at *confidence*.

    The smallest ``n >= 2`` with ``t(n - 1) * std / sqrt(n) <= half_width``
    (NaN when *std* is NaN).

    Example::

        replications_for(1.0, 0.5, 0.95)     # 18
    """
    if not math.isfinite(std) or not half_width > 0:
        return math.nan
    if std == 0:
        return 2
    p = 0.5 + confidence / 2
    n = max(2, math.ceil((NormalDist().inv_cdf(p) * std / half_width) ** 2))
    for _ in range(200):
        if t_quantile(p, n - 1) * std / math.sqrt(n) <= half_width:
            if n == 2 or t_quantile(p, n - 2) * std / math.sqrt(n - 1) > half_width:
                return n
            n -= 1
        else:
            n += max(1, n // 50)
    return n


def frame_replications_needed(
    frame: Any,
    outcomes: Any,
    half_width: float | None,
    relative: float | None,
    by: Any,
    lever: str | None,
    baseline: Any,
    confidence: float,
) -> Any:
    """The implementation of :meth:`StudyFrame.replications_needed`."""
    if (half_width is None) == (relative is None):
        raise ValueError(
            "replications_needed: pass exactly one of half_width= (the target "
            "half-width in the outcome's unit) and relative= (a fraction of the "
            "mean, e.g. 0.05)."
        )
    target = half_width if half_width is not None else relative
    if (
        isinstance(target, bool)
        or not isinstance(target, numbers.Real)
        or not math.isfinite(target)
        or target <= 0
    ):
        raise ValueError(
            f"replications_needed: the target must be a positive number, got {target!r}."
        )
    if confidence is None:
        raise ValueError("replications_needed needs a confidence level, e.g. 0.95.")
    if lever is None:
        table = frame_summary(frame, outcomes, by, confidence)
    else:
        table = frame_paired(frame, lever, outcomes, baseline, by, confidence, False)
    current, goals, needed, more = [], [], [], []
    for row in table.to_dict("records"):
        goal = target if half_width is not None else target * abs(row["mean"])
        n = row["n"]
        units = replications_for(float(row["std"]), goal, confidence)
        current.append((row["ci_high"] - row["ci_low"]) / 2)
        goals.append(goal)
        needed.append(units)
        more.append(max(0, units - n) if math.isfinite(units) else math.nan)
    keep = [c for c in table.columns if c not in ("ci_low", "ci_high")]
    result = table[keep].copy()
    result["half_width"] = current
    result["target"] = goals
    result["needed"] = needed
    result["additional"] = more
    return result


# --------------------------------------------------------------------------- expand


def frame_expand(frame: Any, columns: Any, sep: str) -> Any:
    """The implementation of :meth:`StudyFrame.expand`."""
    if columns is None:
        names = [
            c
            for c in frame.columns
            if any(isinstance(v, Mapping) for v in frame[c].tolist())
            and all(isinstance(v, Mapping) or _missing(v) for v in frame[c].tolist())
        ]
    else:
        names = [columns] if isinstance(columns, str) else list(columns)
        _check_columns(frame, names, "expand")
        for name in names:
            bad = [
                v
                for v in frame[name].tolist()
                if not (isinstance(v, Mapping) or _missing(v))
            ]
            if bad:
                raise StudyError(
                    f"expand: column {name!r} holds {type(bad[0]).__name__} values, not "
                    "dicts; expand turns dict-valued outcomes into one column per key."
                )
    result = frame.copy()
    for name in names:
        cells = result[name].tolist()
        keys = list(
            dict.fromkeys(k for v in cells if isinstance(v, Mapping) for k in v)
        )
        new = {
            f"{name}{sep}{key}": [
                v.get(key) if isinstance(v, Mapping) else None for v in cells
            ]
            for key in keys
        }
        clashes = [c for c in new if c in result.columns]
        if clashes:
            raise StudyError(
                f"expand: the column {clashes[0]!r} exists already; pass another sep=."
            )
        position = list(result.columns).index(name)
        result = result.drop(columns=[name])
        for offset, (column, values) in enumerate(new.items()):
            result.insert(position + offset, column, values)
    return result


# --------------------------------------------------------------------------- timeseries


def join_run_columns(tidy: Any, frame: Any) -> Any:
    """Add ``replication``, ``point``, design, factor and override columns of *frame* to *tidy*."""
    pd = require_pandas("Summaries need")
    wanted = [
        c
        for c in (
            "replication",
            "point",
            "subexperiment",
            "sample",
            *factor_columns(frame),
            *(getattr(frame, "override_columns", ()) or ()),
            "plan",
        )
        if c in frame.columns and c not in tidy.columns
    ]
    wanted = list(dict.fromkeys(wanted))
    if not wanted:
        return tidy
    index = pd.Index(frame["run_id"].astype(object).tolist())
    run_ids = tidy["run_id"]
    if isinstance(run_ids.dtype, pd.CategoricalDtype):
        positions = index.get_indexer(run_ids.cat.categories.astype(object))
        rows = positions[run_ids.cat.codes.to_numpy()]
    else:
        rows = index.get_indexer(run_ids.astype(object))
    result = tidy.copy()
    for column in wanted:
        source = frame[column].reset_index(drop=True)
        values = source.take(rows).reset_index(drop=True)
        values.index = result.index
        textual = values.dtype == object or pd.api.types.is_string_dtype(values)
        if textual and all(isinstance(v, str) or _missing(v) for v in source.tolist()):
            values = values.astype("category")
        result[column] = values
    return result

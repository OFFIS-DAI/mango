"""
A self-contained HTML form for writing parameter presets.

:func:`params_form` renders the params JSON Schema of a scenario (or a params
model) as an HTML page: nested models become sections, numbers, strings,
enums and booleans become inputs, lists and dicts get add/remove buttons,
and distributions (and other unions tagged by ``kind``) get a kind selector
with the fields of the chosen kind.  The model's own parameters (and those
of its kits) come first; the run settings every scenario has (seed, time,
communication, profiles, phases, events) follow in a collapsed section.
``Field(description=...)`` becomes the help text of a field, ``ge``/``le``
the bounds of a number input.  The form starts from the scenario's defaults
or from presets and overrides; the page shows the YAML preset as you edit
(by default only the values that differ from the scenario's defaults) and
downloads it.  It needs no network access: styles, script and schema are
inlined.

Example::

    Path("form.html").write_text(params_form(scenario, params="presets/base.yaml"))
    # mango-scenario form my_model:scenario -p presets/base.yaml -o form.html
"""

from __future__ import annotations

import html
import json
import os
from importlib import resources
from typing import Any

from pydantic import BaseModel

from ._util import to_plain
from .configfiles import deep_merge
from .params import dump_params, load_params, params_schema

__all__ = ["params_form"]

_TEMPLATE = "form.html"


def _template() -> str:
    return (
        resources.files("mango.scenario")
        .joinpath("templates", _TEMPLATE)
        .read_text(encoding="utf-8")
    )


def _script_json(data: Any) -> str:
    """JSON that is safe inside a ``<script>`` element."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return (
        text.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def _model_defaults(model: type[BaseModel]) -> dict:
    """The defaults of the fields that have one (factories included), as JSON data."""
    optional = {
        name for name, info in model.model_fields.items() if not info.is_required()
    }
    if not optional:
        return {}
    instance = model.model_construct()
    return to_plain(
        instance.model_dump(mode="json", include=optional, warnings=False), default=str
    )


#: The heading of the collapsed section that holds the fields every
#: ScenarioParams model has (after the model's own parameters).
_RUN_SETTINGS_LABEL = (
    "Run settings (seed, time, communication, profiles, phases, events)"
)


def _framework_fields(model: type[BaseModel]) -> list[str]:
    from .params import ScenarioParams

    if not (isinstance(model, type) and issubclass(model, ScenarioParams)):
        return []
    return [name for name in ScenarioParams.model_fields if name in model.model_fields]


def _describe_start(params: Any, overrides: Any) -> str:
    parts = []
    if params is not None:
        presets = params if isinstance(params, list | tuple) else [params]
        named = [str(p) for p in presets if isinstance(p, str | os.PathLike)]
        parts.append(", ".join(named) if named else f"{len(presets)} preset(s)")
    if overrides:
        count = len(overrides)
        parts.append(f"{count} override{'' if count == 1 else 's'}")
    return " + ".join(parts)


def _canonical_defaults(target: Any) -> dict | None:
    """The defaults as the form's start values are written (``dump_params``), if valid.

    Durations, dates and distributions then compare equal to a start built
    from presets (``50`` and ``"50s"`` are one value), so untouched values
    never show as changed; ``None`` when the defaults alone do not validate
    (required fields).
    """
    try:
        if hasattr(target, "resolve_params"):
            return dump_params(target.resolve_params(None))
        return dump_params(load_params(target))
    except Exception:  # e.g. required fields without a default
        return None


def params_form(
    target: Any,
    *,
    params: Any = None,
    overrides: Any = None,
    title: str | None = None,
    note: str | None = None,
) -> str:
    """Return a self-contained HTML page that edits and downloads a YAML preset.

    The page lists the model's own parameters first and the run settings
    every scenario has (seed, time, communication, profiles, phases,
    events) in a collapsed section after them.  Help texts and number
    bounds come from the model: ``Field(0.5, ge=0, le=1, description="...")``.

    :param target: a scenario (its params model including kits, with the
        scenario's ``time()``/``defaults()`` as defaults) or a
        ``ScenarioParams`` subclass
    :param params: presets the form starts from (as for
        :meth:`~mango.scenario.Scenario.resolve_params`: a path, a dict or a
        list of them); the downloaded preset still lists only the values
        that differ from the scenario's defaults (presets included)
    :param overrides: dotted overrides applied on top, e.g. ``["peers.n=20"]``
    :param title: the page title (default: ``"<scenario> parameters"``)
    :param note: a remark shown under the subtitle, e.g. that a study file
        sets its factors and replications itself (``mango-scenario form
        study.yaml`` passes one)
    :return: the HTML text (no external assets)
    :raises TypeError: for other targets
    :raises ParamsError: for invalid presets or overrides

    Example::

        Path("form.html").write_text(params_form(scenario))
        page = params_form(scenario, params="presets/base.yaml", overrides=["peers.n=20"])
        page = params_form(scenario, params="presets/base.yaml",
                           note="The study file sets the factors and replications.")
    """
    if isinstance(target, type) and issubclass(target, BaseModel):
        name = target.__name__
        defaults: dict = {}
        ref = f"{target.__module__}:{target.__qualname__}"
        model = target
    elif hasattr(target, "params_model") and callable(
        getattr(target, "param_defaults", None)
    ):
        name = getattr(target, "name", type(target).__name__)
        defaults = to_plain(target.param_defaults(), default=str)
        ref = getattr(target, "ref", None) or name
        model = target.params_model
    else:
        raise TypeError(
            "params_form() needs a scenario or a ScenarioParams subclass, got "
            f"{type(target).__name__}."
        )
    schema = params_schema(target)
    canonical = _canonical_defaults(target)
    defaults = (
        canonical
        if canonical is not None
        else deep_merge(_model_defaults(model), defaults)
    )
    heading = title or f"{name} parameters"
    data = {
        "schema": schema,
        "defaults": defaults,
        "framework": _framework_fields(model),
        "framework_label": _RUN_SETTINGS_LABEL,
        "title": heading,
        "subtitle": (
            f"Parameters of {ref}. Edit the fields; the preset on the right updates "
            "as you type."
        ),
        "hint": (
            f"Check a preset before running: mango-scenario validate preset.yaml "
            f"--scenario {ref}"
        ),
        "note": note or "",
    }
    if params is not None or overrides:
        if hasattr(target, "resolve_params"):
            resolved = target.resolve_params(params, overrides=overrides)
        else:
            resolved = load_params(target, *_as_list(params), overrides=overrides)
        data["start"] = dump_params(resolved)
        data["subtitle"] = (
            f"Parameters of {ref}, starting from {_describe_start(params, overrides)}. "
            "Edit the fields; the preset on the right updates as you type."
        )
    page = _template()
    page = page.replace("__TITLE__", html.escape(heading), 1)
    return page.replace("__DATA__", _script_json(data), 1)


def _as_list(params: Any) -> list:
    if params is None:
        return []
    return list(params) if isinstance(params, list | tuple) else [params]

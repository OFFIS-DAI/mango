"""
Exceptions of the scenario layer.

All errors raised for invalid scenario definitions, parameters or studies
derive from :class:`ScenarioError`, so callers can catch one type.

Example::

    from mango.scenario import ParamsError, load_params

    try:
        params = load_params(Params, "presets/base.yaml")
    except ParamsError as err:
        print(err.path, err.source)  # e.g. "ev.capactiy_kwh", "presets/base.yaml"
"""

from __future__ import annotations

__all__ = ["ScenarioError", "ParamsError", "StudyError", "RunWarning"]


class ScenarioError(Exception):
    """Base class of all scenario-layer errors (invalid scenario definitions)."""


class ParamsError(ScenarioError, ValueError):
    """Invalid parameters: unknown keys, invalid values or unreadable presets.

    :param message: the full, human-readable message
    :param path: dotted parameter path of the (first) offending entry, if known
    :param source: where the offending value came from (a file path,
        ``"overrides"``, ...), if known
    :param errors: one dict per problem with the keys ``path``, ``message``
        and ``source``; defaults to a single entry built from the other
        arguments

    Example::

        err = ParamsError("ev.n: must be >= 0", path="ev.n", source="base.yaml")
        err.errors  # [{"path": "ev.n", "message": "ev.n: must be >= 0", "source": "base.yaml"}]
    """

    def __init__(
        self,
        message: str,
        *,
        path: str | None = None,
        source: str | None = None,
        errors: list[dict] | None = None,
    ):
        super().__init__(message)
        self.path = path
        self.source = source
        self.errors = (
            errors
            if errors is not None
            else [{"path": path, "message": message, "source": source}]
        )


class StudyError(ScenarioError):
    """An invalid study definition or a study that cannot be executed."""


class RunWarning(UserWarning):
    """A warning about one run, kept with its result.

    Issued by :meth:`ctx.warn <mango.scenario.BuildContext.warn>` and by the
    run itself (e.g. a run with recorders that recorded no values).  Each
    message is also stored once in :attr:`RunResult.warnings
    <mango.scenario.RunResult.warnings>`, so it survives worker processes.

    Example::

        import warnings
        warnings.simplefilter("error", RunWarning)   # make run warnings fatal
    """

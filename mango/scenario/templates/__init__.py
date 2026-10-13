"""
Templates of ``mango-scenario``: the preset form page and the project templates.

:func:`create_project` copies a project template (``mango-scenario new``),
ready to validate, plan and run:

- ``minimal`` (default): one ``model.py`` with an agent, its parameters, a
  Python scenario and outcomes, plus a preset, a study file and a README.
  A domain-neutral start (agents on a ring averaging their values).
- ``full``: a sensor-field example that uses every concept of the scenario
  layer (``agents/`` and ``kits/`` packages, a scenario file, a calendar
  start, a CSV profile, a warm-up phase, outcomes in their own module),
  meant to be adapted.

Example::

    create_project("my_study")                     # minimal
    create_project("sensors", template="full")
    # cd my_study && mango-scenario run study.yaml
"""

from __future__ import annotations

import os
import re
from importlib import resources
from pathlib import Path
from typing import Any

__all__ = ["TEMPLATES", "create_project", "study_file"]

#: Replaced by the project name in every template file and file name.
NAME_TOKEN = "__PROJECT__"

#: The project templates: name -> one-line description.
TEMPLATES = {
    "minimal": "one model.py (agent, parameters, scenario, outcomes), a preset and "
    "a study; domain-neutral",
    "full": "a sensor-field example with agents/ and kits/ packages, a scenario "
    "file, a CSV profile, calendar time and a warm-up phase",
}

#: The study file of each template (relative to the project, with NAME_TOKEN).
_STUDY_FILES = {"minimal": "study.yaml", "full": f"studies/{NAME_TOKEN}.yaml"}

_SUFFIX = ".tmpl"  # template Python files are not importable or linted in place
_RENAMES = {"gitignore": ".gitignore"}


def _files(root: Any, prefix: tuple[str, ...] = ()):
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith((".", "__pycache__")):
            continue
        if entry.is_dir():
            yield from _files(entry, (*prefix, entry.name))
        else:
            yield (*prefix, entry.name), entry


def _file_stem(project: str) -> str:
    """The project name as it appears in file names (no separators or spaces)."""
    return re.sub(r"[^\w.-]+", "_", project).strip("._") or "project"


def _target_name(name: str, project: str = NAME_TOKEN) -> str:
    if name.endswith(_SUFFIX):
        name = name[: -len(_SUFFIX)]
    name = _RENAMES.get(name, name)
    return name.replace(NAME_TOKEN, _file_stem(project))


def _check_template(template: str) -> None:
    if template not in TEMPLATES:
        known = ", ".join(f"{name!r}" for name in TEMPLATES)
        raise ValueError(
            f"Unknown project template {template!r}; choose one of {known}."
        )


def study_file(template: str = "minimal", name: str = NAME_TOKEN) -> str:
    """The study file :func:`create_project` writes for *template* and project *name*.

    :param template: the template name (see :data:`TEMPLATES`)
    :param name: the project name
    :return: the path relative to the project folder, e.g. ``"study.yaml"``
    :raises ValueError: for an unknown template

    Example::

        study_file("full", "sensors")    # "studies/sensors.yaml"
    """
    _check_template(template)
    parts = _STUDY_FILES[template].split("/")
    return "/".join(_target_name(part, name) for part in parts)


def create_project(
    directory: str | os.PathLike,
    *,
    name: str | None = None,
    template: str = "minimal",
    force: bool = False,
) -> list[Path]:
    """Create a runnable scenario project in *directory* from a template.

    :param directory: the project folder (created if missing)
    :param name: the project name written into the files and file names
        (default: the folder name)
    :param template: ``"minimal"`` (default) or ``"full"``, see
        :data:`TEMPLATES`
    :param force: write into a folder that is not empty (template files
        that exist are overwritten)
    :return: the created files
    :raises FileExistsError: if *directory* is a non-empty folder and not
        *force*, or a file
    :raises ValueError: for an empty name or an unknown template

    Example::

        files = create_project("demo")
        [path.name for path in files]   # ['.gitignore', 'README.md', 'model.py', ...]
    """
    _check_template(template)
    target = Path(directory)
    if target.exists() and not target.is_dir():
        raise FileExistsError(f"{target} exists and is not a folder.")
    if target.is_dir() and any(target.iterdir()) and not force:
        raise FileExistsError(
            f"{target} is not empty; choose a new folder, or write the template into "
            "it with force=True (mango-scenario new --force)."
        )
    project = name if name is not None else target.resolve().name
    if not project or not project.strip():
        raise ValueError("The project name must not be empty.")
    root = resources.files(__name__).joinpath(f"project_{template}")
    created = []
    for parts, entry in _files(root):
        names = [_target_name(part, project) for part in parts]
        path = target.joinpath(*names)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = entry.read_text(encoding="utf-8").replace(NAME_TOKEN, project)
        path.write_text(text, encoding="utf-8")
        created.append(path)
    return created

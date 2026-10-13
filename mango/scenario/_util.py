"""
Internal helpers of the scenario layer: import references, canonical JSON and
content hashes, flexible calls and provenance.

Example::

    from mango.scenario._util import content_hash, import_ref, object_ref

    fn = import_ref("math:sqrt")
    object_ref(fn)                           # "math:sqrt"
    content_hash({"b": 1, "a": [1.5, 2]})    # 12 hex chars, independent of dict order
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as _dt
import difflib
import enum
import functools
import hashlib
import importlib
import importlib.metadata
import inspect
import json
import logging
import os
import platform
import socket
import subprocess
import sys
import tempfile
import threading
import warnings
import weakref
from collections.abc import Callable, Mapping
from pathlib import Path, PurePath
from typing import Any

from .errors import ScenarioError

__all__ = [
    "import_ref",
    "object_ref",
    "stable_ref",
    "canonical_json",
    "content_hash",
    "call_flexible",
    "provenance",
    "git_info",
    "model_json_data",
    "to_plain",
    "write_text_atomic",
    "write_json_atomic",
]

logger = logging.getLogger(__name__)

_REF_HINT = "expected 'package.module:attribute' (or 'package.module.attribute')"


def _import_module(module_name: str, ref: str):
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name and (
            module_name == exc.name or module_name.startswith(exc.name + ".")
        ):
            raise ImportError(
                f"Cannot import {ref!r}: no module named {exc.name!r}. Run from the "
                "project root, add the module's directory to sys.path or install "
                "the package."
            ) from exc
        raise ImportError(
            f"Cannot import {ref!r}: importing module {module_name!r} failed: {exc}"
        ) from exc


def _resolve_attribute(obj: Any, attr_path: str, ref: str, owner: str) -> Any:
    for part in attr_path.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError:
            known = [name for name in dir(obj) if not name.startswith("_")]
            close = difflib.get_close_matches(part, known, n=1)
            hint = f"; did you mean {close[0]!r}?" if close else ""
            raise AttributeError(
                f"Cannot resolve {ref!r}: {owner} has no attribute {part!r}{hint}"
            ) from None
        owner = f"{owner}.{part}"
    return obj


def import_ref(ref: str) -> Any:
    """Import the object an import reference points to.

    The preferred form is ``"package.module:attr.sub"`` (module and attribute
    path separated by a colon); ``"package.module.attr"`` is accepted too and
    resolved against the longest importable module prefix.

    :param ref: the import reference
    :return: the referenced object
    :raises ImportError: if the module cannot be imported (the message names
        the reference)
    :raises AttributeError: if the module has no such attribute (with a
        did-you-mean suggestion)
    :raises ValueError: if *ref* is malformed

    Example::

        sqrt = import_ref("math:sqrt")
        join = import_ref("os.path.join")
    """
    if not isinstance(ref, str):
        raise TypeError(f"An import reference must be a str, got {type(ref).__name__}")
    ref = ref.strip()
    if ":" in ref:
        module_name, _, attr_path = ref.partition(":")
        if not module_name or not attr_path or ":" in attr_path:
            raise ValueError(f"Invalid import reference {ref!r}: {_REF_HINT}.")
        module = _import_module(module_name, ref)
        return _resolve_attribute(module, attr_path, ref, f"module {module_name!r}")
    parts = ref.split(".")
    if len(parts) < 2 or any(not part for part in parts):
        raise ValueError(f"Invalid import reference {ref!r}: {_REF_HINT}.")
    first_error: ImportError | None = None
    for split in range(len(parts) - 1, 0, -1):
        module_name = ".".join(parts[:split])
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name and (
                module_name == exc.name or module_name.startswith(exc.name + ".")
            ):
                continue
            first_error = exc
            break
        return _resolve_attribute(
            module, ".".join(parts[split:]), ref, f"module {module_name!r}"
        )
    if first_error is not None:
        raise ImportError(
            f"Cannot import {ref!r}: importing module {module_name!r} failed: "
            f"{first_error}"
        ) from first_error
    raise ImportError(
        f"Cannot import {ref!r}: no module named {parts[0]!r}. Run from the project "
        "root, add the module's directory to sys.path or install the package."
    )


def import_ref_or_raise(
    ref: str, prefix: str, error: type[Exception] = ScenarioError
) -> Any:
    """:func:`import_ref`, re-raising its errors as *error* (default ``ScenarioError``).

    The message is ``f"{prefix}: {exc}"`` and the original error is chained.
    """
    try:
        return import_ref(ref)
    except (ImportError, AttributeError, ValueError) as exc:
        raise error(f"{prefix}: {exc}") from exc


def object_ref(obj: Any) -> str | None:
    """Return ``"module:qualname"`` for *obj* if it can be imported back.

    Functions and classes are looked up by their ``__qualname__``.  Other
    objects (and functions bound to a different global name) are found by
    searching the globals of the module named by their ``__module__``
    attribute for the identical object; an instance can record its origin by
    setting ``self.__module__`` (a :class:`~mango.scenario.Scenario` does).
    Objects that are not reachable from module globals, such as unassigned
    lambdas and local functions, give ``None``.

    :param obj: any object
    :return: an import reference that :func:`import_ref` resolves to the very
        same object, or ``None``

    Example::

        import math

        object_ref(math.sqrt)    # "math:sqrt"
        object_ref(lambda: 0)    # None
    """
    module_name = getattr(obj, "__module__", None)
    if not isinstance(module_name, str):
        return None
    module = sys.modules.get(module_name)
    if module is None:
        return None
    qualname = getattr(obj, "__qualname__", None)
    if isinstance(qualname, str) and "<" not in qualname:
        target = module
        try:
            for part in qualname.split("."):
                target = getattr(target, part)
        except AttributeError:
            target = None
        if target is obj:
            return f"{module_name}:{qualname}"
    for name, value in list(vars(module).items()):
        if value is obj and not name.startswith("__"):
            return f"{module_name}:{name}"
    return None


def _main_module_name() -> str | None:
    """The module name the running script has when it is imported instead."""
    main = sys.modules.get("__main__")
    name = getattr(getattr(main, "__spec__", None), "name", None)
    if name and name not in ("__main__", "__mp_main__"):
        return name  # python -m package.module
    main_file = getattr(main, "__file__", None)
    if not main_file:
        return None
    path = Path(main_file).resolve()
    names = []
    for entry in sys.path:
        try:
            relative = path.relative_to(Path(entry or ".").resolve())
        except (ValueError, OSError):
            continue
        parts = relative.with_suffix("").parts
        if parts and all(part.isidentifier() for part in parts):
            names.append(".".join(parts))
    return min(names, key=len) if names else path.stem


def stable_ref(ref: str) -> str:
    """Return *ref* with ``__main__`` replaced by the script's importable module name.

    A scenario defined in a script that runs a study is referenced as
    ``"__main__:scenario"``; the same scenario imported from another entry
    point is ``"run:scenario"``.  Run ids use the stable form, so both name the
    same runs.  Interactive sessions keep ``"__main__"``.

    :param ref: an import reference or a scenario name
    :return: the reference to use in content hashes

    Example::

        # python studies/run.py   (studies/ is sys.path[0])
        stable_ref("__main__:scenario")     # "run:scenario"
    """
    module, sep, attr = ref.partition(":")
    if not sep:
        module, sep, attr = ref.rpartition(".")
    if module != "__main__":
        return ref
    name = _main_module_name()
    return ref if name is None else f"{name}{sep}{attr}"


# --------------------------------------------------------------------------- data paths

_PROJECT_ROOT: Path | None = None
# roots of the using_project_root blocks that are active (threads may overlap)
_ACTIVE_ROOTS: list[str] = []
_ADDED_TO_PATH: set[str] = set()
_ROOT_LOCK = threading.Lock()


def project_root() -> Path | None:
    """The project root that relative data and scenario-file paths fall back to."""
    with _ROOT_LOCK:
        if _ACTIVE_ROOTS:
            return Path(_ACTIVE_ROOTS[-1])
    return _PROJECT_ROOT


def set_project_root(root: str | os.PathLike | None) -> None:
    """Make *root* the fallback base of relative paths (``None`` clears it).

    Slurm tasks and ``rerun`` set the study's ``project_root`` for the rest
    of the process; in-process runs use :func:`using_project_root`, which
    restores the previous state.
    """
    global _PROJECT_ROOT
    _PROJECT_ROOT = None if not root else Path(root)


@contextlib.contextmanager
def using_project_root(root: str | os.PathLike | None):
    """Resolve relative paths and imports against *root* inside the block.

    Makes *root* the fallback base of :func:`data_path_candidates` and puts
    it on ``sys.path`` (when missing); both are undone when the last block
    using *root* ends.  ``None`` or ``""`` changes nothing.

    Example::

        with using_project_root(spec["project_root"]):
            scenario = import_ref(spec["scenario"])
            scenario.run(spec["params"])
    """
    if not root:
        yield
        return
    root = os.fspath(root)
    with _ROOT_LOCK:
        _ACTIVE_ROOTS.append(root)
        if root not in sys.path:
            sys.path.insert(0, root)
            _ADDED_TO_PATH.add(root)
    try:
        yield
    finally:
        with _ROOT_LOCK:
            _ACTIVE_ROOTS.remove(root)
            if root not in _ACTIVE_ROOTS and root in _ADDED_TO_PATH:
                _ADDED_TO_PATH.discard(root)
                try:
                    sys.path.remove(root)
                except ValueError:
                    pass


def data_path_candidates(path: str | os.PathLike) -> list[Path]:
    """Where a relative path may live: the working directory, then the project root."""
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return [candidate]
    bases = [Path.cwd()]
    root = project_root()
    if root is not None and root.resolve() != bases[0].resolve():
        bases.append(root)
    return [base / candidate for base in bases]


def did_you_mean(name: Any, known: Any, *, cutoff: float = 0.6) -> str:
    """``" Did you mean 'x'?"`` for the closest of *known* to *name*, else ``""``.

    The one spelling of suggestions in scenario-layer messages; append it to
    a sentence.

    Example::

        f"Unknown group {name!r}.{did_you_mean(name, groups)} Known groups: ..."
    """
    known = [str(k) for k in known]
    same = [k for k in known if k.lower() == str(name).lower() and k != str(name)]
    if same:  # the same name in another case
        return f" Did you mean {same[0]!r}?"
    close = difflib.get_close_matches(str(name), known, n=1, cutoff=cutoff)
    return f" Did you mean {close[0]!r}?" if close else ""


def add_note(exc: BaseException, note: str) -> None:
    """Attach context to *exc* (``BaseException.add_note``; ``__notes__`` before 3.11).

    The CLI and the run folders' ``error.txt`` print the notes on every
    Python version.

    Example::

        try:
            build()
        except Exception as exc:
            add_note(exc, "in Neighbourhood.build() of kit 'grid'")
            raise
    """
    if hasattr(exc, "add_note"):  # Python >= 3.11
        exc.add_note(note)
        return
    try:
        exc.__notes__ = [*getattr(exc, "__notes__", ()), note]
    except AttributeError:  # an exception type with __slots__
        pass


def notes_text(exc: BaseException) -> str:
    """The notes of *exc* as traceback lines, for Python versions that do not print them."""
    if sys.version_info >= (3, 11):
        return ""
    return "".join(f"{note}\n" for note in getattr(exc, "__notes__", ()))


def _json_key(key: Any) -> str:
    if isinstance(key, str):
        return key
    return canonical_json(key)


def _to_json_data(obj: Any, default: Callable[[Any], Any] | None = None) -> Any:
    if obj is None or isinstance(obj, bool | str):
        return obj
    if isinstance(obj, enum.Enum):
        return _to_json_data(obj.value, default)
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        return float(obj)
    if isinstance(obj, Mapping):
        return {
            _json_key(key): _to_json_data(value, default) for key, value in obj.items()
        }
    if isinstance(obj, list | tuple):
        return [_to_json_data(item, default) for item in obj]
    if isinstance(obj, set | frozenset):
        items = [_to_json_data(item, default) for item in obj]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(obj, _dt.datetime | _dt.date | _dt.time):
        return obj.isoformat()
    if isinstance(obj, _dt.timedelta):
        return obj.total_seconds()
    if isinstance(obj, PurePath):
        return str(obj)
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump) and hasattr(type(obj), "model_fields"):
        return _to_json_data(model_json_data(obj), default)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _to_json_data(dataclasses.asdict(obj), default)
    numpy = sys.modules.get("numpy")
    if numpy is not None:
        if isinstance(obj, numpy.generic):
            return _to_json_data(obj.item(), default)
        if isinstance(obj, numpy.ndarray):
            return _to_json_data(obj.tolist(), default)
    if default is not None:
        return _to_json_data(default(obj))
    raise TypeError(
        f"Cannot convert {type(obj).__name__} to canonical JSON: {obj!r}. Use plain "
        "data (dict, list, str, numbers, bool, None, datetimes, paths) or a "
        "pydantic model."
    )


def _sort_key(item: Any) -> str:
    return json.dumps(item, sort_keys=True, default=str)


def _align_sets(python: Any, data: Any) -> Any:
    """Sort the JSON lists in *data* that come from sets in *python* (same shape)."""
    if isinstance(python, set | frozenset) and isinstance(data, list):
        if len(python) != len(data):
            return data
        items = [_align_sets(p, d) for p, d in zip(python, data)]
        return sorted(items, key=_sort_key)
    if isinstance(python, Mapping) and isinstance(data, Mapping):
        if len(python) != len(data):
            return data
        return {
            key: _align_sets(p, d) for p, (key, d) in zip(python.values(), data.items())
        }
    if isinstance(python, list | tuple) and isinstance(data, list):
        if len(python) != len(data):
            return data
        return [_align_sets(p, d) for p, d in zip(python, data)]
    return data


def model_json_data(model: Any, **kwargs: Any) -> Any:
    """Return ``model.model_dump(mode="json", **kwargs)`` with set fields sorted.

    Pydantic writes a ``set``/``frozenset`` as a list in iteration order,
    which depends on the process's hash seed for strings; sorting makes the
    dump (and every hash of it) identical across processes.

    :param model: a pydantic model instance
    :param kwargs: further ``model_dump`` arguments (e.g. ``exclude_unset``)
    :return: plain JSON data

    Example::

        class P(BaseModel):
            tags: set[str] = {"b", "a"}

        model_json_data(P())     # {"tags": ["a", "b"]}
    """
    data = model.model_dump(mode="json", **kwargs)
    return _align_sets(model.model_dump(mode="python", **kwargs), data)


def to_plain(obj: Any, *, default: Callable[[Any], Any] | None = None) -> Any:
    """Return *obj* as plain JSON data, keeping the order of mapping keys.

    Conversions as for :func:`canonical_json` (tuples to lists, models and
    dataclasses to dicts, numpy scalars to Python numbers, sets sorted), but
    keys are not sorted.

    :param obj: the data
    :param default: called with objects that have no plain form (its result
        is converted); without it such objects raise ``TypeError``
    :return: dicts, lists, str, int, float, bool and None
    :raises TypeError: for objects without a plain form (and no *default*)

    Example::

        to_plain({"b": (1, 2), "a": numpy.float64(0.5)})   # {"b": [1, 2], "a": 0.5}
        to_plain({"when": object()}, default=str)          # {"when": "<object ...>"}
    """
    return _to_json_data(obj, default)


@functools.lru_cache(maxsize=1)
def _umask() -> int:
    """The process umask (read once; setting it is the only portable way to read it)."""
    try:
        with open("/proc/self/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("Umask:"):
                    return int(line.split()[1], 8)
    except (OSError, ValueError):
        pass
    with _ROOT_LOCK:
        mask = os.umask(0o022)
        os.umask(mask)
    return mask


def is_positive_int(value: Any) -> bool:
    """True for an int >= 1 (not a bool)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def utc_now() -> str:
    """The current UTC time as ISO 8601 text (manifest timestamps)."""
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    """The JSON content of *path*, or ``None`` when it is missing or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def require_pandas(prefix: str):
    """Import pandas, or raise ImportError ``f"{prefix} pandas: pip install ..."``."""
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            f"{prefix} pandas: pip install 'mango-agents[scenario]'"
        ) from exc
    return pd


def have_pyarrow() -> bool:
    """Whether pyarrow (Parquet support) can be imported."""
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        return False
    return True


def write_text_atomic(path: str | Path, text: str) -> Path:
    """Write *text* to *path* atomically (temporary file + ``os.replace``).

    Readers see the old or the new content, never a partial file; parent
    folders are created.

    :param path: the target file
    :param text: the content (UTF-8, newlines written as given)
    :return: the path

    Example::

        write_text_atomic(folder / "manifest.json", json.dumps(manifest))
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        # mkstemp creates 0600 files; give the result the mode open() would
        os.chmod(tmp, 0o666 & ~_umask())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def write_json_atomic(path: str | Path, data: Any) -> Path:
    """Write *data* as indented JSON atomically (see :func:`write_text_atomic`).

    Objects without a JSON form are written as their ``str``.

    Example::

        write_json_atomic(folder / "outcomes.json", {"peak_kw": 4.2})
    """
    return write_text_atomic(path, json.dumps(to_plain(data, default=str), indent=2))


def canonical_json(obj: Any) -> str:
    """Serialize *obj* to a canonical JSON string (stable across processes).

    Keys are sorted, separators compact, datetimes and dates written in ISO
    format, paths as strings, numpy scalars and arrays as Python values,
    pydantic models via ``model_dump(mode="json")``, dataclasses as dicts,
    tuples and lists as lists and floats via ``repr`` (exact round trip).
    Non-string dict keys are written as their canonical JSON.

    :param obj: plain data, possibly nested
    :return: the canonical JSON text
    :raises TypeError: for objects without a canonical representation

    Example::

        canonical_json({"b": 1, "a": (1.5, None)})  # '{"a":[1.5,null],"b":1}'
    """
    return json.dumps(
        _to_json_data(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def content_hash(obj: Any, length: int = 12) -> str:
    """Return the truncated SHA-256 hex digest of :func:`canonical_json` of *obj*.

    :param obj: plain data (see :func:`canonical_json`)
    :param length: number of hex characters to keep (1..64)
    :return: the hex digest prefix

    Example::

        content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})  # True
    """
    if not 1 <= length <= 64:
        raise ValueError(f"length must be in 1..64, got {length}")
    digest = hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()
    return digest[:length]


_UNLIMITED = -1
_ARITY_CACHE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _positional_capacity(fn: Callable) -> int:
    try:
        return _ARITY_CACHE[fn]
    except (KeyError, TypeError):
        pass
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        capacity = _UNLIMITED
    else:
        capacity = 0
        for parameter in signature.parameters.values():
            if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
                capacity = _UNLIMITED
                break
            if parameter.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ):
                capacity += 1
    try:
        _ARITY_CACHE[fn] = capacity
    except TypeError:
        pass
    return capacity


def call_flexible(fn: Callable, *args: Any) -> Any:
    """Call *fn* with as many leading positional *args* as it accepts.

    A function taking no parameters is called as ``fn()``, one taking one as
    ``fn(args[0])`` and so on; ``*args`` functions get all of them.  The
    signature is inspected once per function object and cached.

    :param fn: the callable
    :param args: candidate positional arguments, most important first
    :return: whatever *fn* returns

    Example::

        call_flexible(lambda: 1, "ctx")       # 1
        call_flexible(lambda c: c, "ctx")     # "ctx"
    """
    capacity = _positional_capacity(fn)
    if capacity == _UNLIMITED:
        return fn(*args)
    return fn(*args[:capacity])


@functools.cache  # metadata parsing costs ~4 ms per call
def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


#: Packages whose versions every manifest records (``"unknown"`` when missing).
PROVENANCE_PACKAGES: tuple[str, ...] = (
    "numpy",
    "pandas",
    "pydantic",
    "pyarrow",
    "scipy",
    "networkx",
    "pyyaml",
)

#: Slurm variables recorded under ``provenance["slurm"]`` when set.
SLURM_VARIABLES: tuple[str, ...] = (
    "SLURM_JOB_ID",
    "SLURM_ARRAY_JOB_ID",
    "SLURM_ARRAY_TASK_ID",
    "SLURMD_NODENAME",
    "SLURM_RESTART_COUNT",
    "MANGO_TASK_OFFSET",
)


@functools.cache
def environment_hash() -> str:
    """A hash of the installed distributions (names and versions) of this interpreter.

    Built from the ``*.dist-info``/``*.egg-info`` entries on ``sys.path``
    (cheap: no metadata is parsed), so two environments with the same
    packages in the same versions get the same hash.

    :return: 16 hex characters

    Example::

        environment_hash()        # "88c506179d94f040"
    """
    entries: set[str] = set()
    for folder in sys.path:
        try:
            names = os.listdir(folder or ".")
        except OSError:
            continue
        entries.update(
            name.lower() for name in names if name.endswith((".dist-info", ".egg-info"))
        )
    text = "\n".join(sorted(entries))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@functools.cache
def environment_list() -> tuple[str, ...]:
    """The installed distributions of this interpreter as sorted ``name==version`` lines.

    The readable counterpart of :func:`environment_hash`: a study writes it
    to ``<out>/environments/<hash>.txt`` (see :func:`write_environment_file`),
    so the environment of every run can be rebuilt (``pip install -r``).

    :return: ``("mango-agents==2.1.0", "numpy==2.1.3", ...)``

    Example::

        "\n".join(environment_list())
    """
    found: dict[str, str] = {}
    for dist in importlib.metadata.distributions():
        try:
            name = dist.metadata["Name"]
        except Exception:  # broken metadata: skip it
            continue
        if name and name.lower() not in found:
            found[name.lower()] = f"{name}=={dist.version}"
    return tuple(found[key] for key in sorted(found))


#: Folder of a study (below ``out``) holding the package list of each environment.
ENVIRONMENTS_DIR = "environments"


def write_environment_file(study_folder: str | os.PathLike) -> Path | None:
    """Write ``<study_folder>/environments/<environment hash>.txt`` once.

    The file lists the installed packages (:func:`environment_list`) of the
    environment whose hash the manifests record under
    ``provenance["environment"]``.  An existing file is left as it is.

    :param study_folder: the study folder (``out``)
    :return: the file, or ``None`` when it could not be written

    Example::

        write_environment_file("results/fleet")
        # results/fleet/environments/88c506179d94f040.txt
    """
    path = Path(study_folder) / ENVIRONMENTS_DIR / f"{environment_hash()}.txt"
    if path.exists():
        return path
    try:
        header = (
            f"# python {platform.python_version()} on {platform.platform()}\n"
            f"# environment {environment_hash()}; pip install -r this file\n"
        )
        write_text_atomic(
            path, header + "".join(f"{line}\n" for line in environment_list())
        )
    except OSError as exc:
        logger.debug("Could not write %s: %s", path, exc)
        return None
    return path


def _module_distribution(module: str | None) -> dict | None:
    """``{"name", "version"}`` of the installed distribution of a top-level module."""
    if not module:
        return None
    top = module.partition(".")[0]
    if top in ("__main__", "__mp_main__", "mango"):
        return None
    for name in dict.fromkeys((top, top.replace("_", "-"))):
        version = _distribution_version(name)
        if version != "unknown":
            return {"name": name, "version": version}
    return None


def provenance(scenario_ref: str | None = None) -> dict:
    """Describe the software environment of this process.

    :param scenario_ref: the scenario's import reference; when its top-level
        module belongs to an installed distribution, that distribution's
        name and version are recorded as ``"package"``
    :return: ``{"versions": {"mango-agents", "python", "numpy", "pandas",
        "pydantic", "pyarrow", "scipy"}, "platform", "hostname",
        "environment"}`` (a hash of all installed distributions, see
        :func:`environment_hash`), plus ``"package"`` (see *scenario_ref*)
        and ``"slurm"`` (the :data:`SLURM_VARIABLES` that are set) where
        they apply; versions of packages that are not installed are
        ``"unknown"``

    Example::

        provenance()["versions"]["python"]  # e.g. "3.13.1"
    """
    versions = {"mango-agents": _distribution_version("mango-agents")}
    versions["python"] = platform.python_version()
    for name in PROVENANCE_PACKAGES:
        versions[name] = _distribution_version(name)
    info: dict[str, Any] = {
        "versions": versions,
        "platform": platform.platform(),
        "hostname": socket.gethostname(),
        "environment": environment_hash(),
    }
    module = None
    if isinstance(scenario_ref, str) and ":" in scenario_ref:
        module = scenario_ref.partition(":")[0]
    package = _module_distribution(module)
    if package is not None:
        info["package"] = package
    slurm = {key: os.environ[key] for key in SLURM_VARIABLES if os.environ.get(key)}
    if slurm:
        info["slurm"] = slurm
    return info


def _run_git(args: list[str], cwd: Path, timeout: float):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def git_info(path: str | Path | None = None, *, timeout: float = 10.0) -> dict | None:
    """Return the git commit and dirty flag of the repository containing *path*.

    ``dirty`` is true when tracked files have uncommitted changes (untracked
    files, such as a results folder, do not count).

    :param path: a directory (or file) inside the repository; default: cwd
    :param timeout: seconds to wait for each git call
    :return: ``{"commit": sha, "dirty": bool}``, or ``None`` when *path* is not
        inside a git work tree, has no commit yet, or git is unavailable

    Example::

        info = git_info(".")
        if info is not None:
            print(info["commit"][:8], "dirty" if info["dirty"] else "clean")
    """
    cwd = Path.cwd() if path is None else Path(path)
    if cwd.is_file():
        cwd = cwd.parent
    if not cwd.is_dir():
        return None
    try:
        head = _run_git(["rev-parse", "HEAD"], cwd, timeout)
        if head.returncode != 0:
            return None
        status = _run_git(
            ["status", "--porcelain", "--untracked-files=no"], cwd, timeout
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("git is unavailable in %s: %s", cwd, exc)
        return None
    if status.returncode != 0:
        return None
    return {"commit": head.stdout.strip(), "dirty": bool(status.stdout.strip())}


def git_dirty_files(
    path: str | Path | None = None, *, relative: bool = False, timeout: float = 10.0
) -> list[str]:
    """Return the tracked files with uncommitted changes (``git diff HEAD``).

    Untracked files do not count (as for :func:`git_info`).

    :param path: a directory inside the repository; default: cwd
    :param relative: only files below *path*, relative to it (else every
        changed file, relative to the repository root)
    :param timeout: seconds to wait for git
    :return: the files (empty outside a work tree or without changes)

    Example::

        git_dirty_files(".", relative=True)        # ["model.py"]
    """
    cwd = Path.cwd() if path is None else Path(path)
    if not cwd.is_dir():
        return []
    args = ["diff", "HEAD", "--name-only", *(["--relative"] if relative else [])]
    try:
        diff = _run_git(args, cwd, timeout)
    except (OSError, subprocess.SubprocessError):
        return []
    if diff.returncode != 0:
        return []
    return [line.strip() for line in diff.stdout.splitlines() if line.strip()]


#: Folder of a study (below ``out``) holding the diffs of runs from a dirty work tree.
GIT_DIFFS_DIR = "git"


def write_git_diff(
    path: str | Path | None, study_folder: str | os.PathLike, git: Mapping | None
) -> str | None:
    """Store ``git diff HEAD`` of a dirty work tree in the study folder.

    The diff goes to ``<study_folder>/git/<commit[:12]>-<sha of diff>.diff``
    (written once per distinct diff), so a run made from uncommitted
    changes can be rebuilt: ``git checkout <commit> && git apply <file>``.

    :param path: a directory inside the repository
    :param study_folder: the study folder (``out``)
    :param git: the run's :func:`git_info` record
    :return: the file relative to *study_folder* (posix), or ``None`` when
        the tree is clean, not a repository, or the diff is unavailable

    Example::

        write_git_diff(".", "results/fleet", git_info("."))   # "git/3f2a9c1b7d4e-77ab01c2d3e4.diff"
    """
    if not isinstance(git, Mapping) or not git.get("dirty") or not git.get("commit"):
        return None
    cwd = Path.cwd() if path is None else Path(path)
    try:
        diff = _run_git(["diff", "HEAD", "--binary"], cwd, 30.0)
    except (OSError, subprocess.SubprocessError):
        return None
    if diff.returncode != 0 or not diff.stdout:
        return None
    digest = hashlib.sha256(diff.stdout.encode("utf-8")).hexdigest()[:12]
    name = f"{GIT_DIFFS_DIR}/{str(git['commit'])[:12]}-{digest}.diff"
    target = Path(study_folder) / name
    if not target.exists():
        try:
            write_text_atomic(target, diff.stdout)
        except OSError as exc:
            logger.debug("Could not write %s: %s", target, exc)
            return None
    return name


#: Folder of the mango package: warnings point at the first frame outside it.
_MANGO_DIR = str(Path(__file__).resolve().parent.parent) + os.sep


def _stdlib_dirs() -> tuple[str, ...]:
    """The standard library folders (asyncio frames sit between a run and its caller)."""
    import sysconfig

    paths = sysconfig.get_paths()
    return tuple(
        os.path.abspath(path) + os.sep
        for key in ("stdlib", "platstdlib")
        if (path := paths.get(key))
    )


def warn_user(message: str, category: type[Warning] = UserWarning) -> None:
    """Issue a warning attributed to the first caller outside the mango package.

    A fixed ``stacklevel`` points into the library when the same check is
    reached through different entry points (``Study(...)``,
    ``Factor.parse(...)``); this walks the stack instead, past mango and the
    standard library (the asyncio frames of a run started by
    ``scenario.run``).

    :param message: the warning text
    :param category: the warning class

    Example::

        warn_user("Factor 'x': {low, high} is read as 2 labeled levels ...")
    """
    frame = sys._getframe(1)
    level = 1
    stdlib = _stdlib_dirs()
    while frame is not None:
        file = frame.f_code.co_filename
        library = file.startswith((_MANGO_DIR, "<frozen")) or (
            file.startswith(stdlib) and "-packages" not in file
        )
        if not library:
            break
        frame = frame.f_back
        level += 1
    warnings.warn(message, category, stacklevel=level + 1 if frame else 2)


def user_frame(exc: BaseException) -> str | None:
    """``file:line in function`` of the innermost frame of *exc* in user code.

    User code is anything outside the mango package, the Python standard
    library and installed third-party packages (site-packages; used only when
    no other frame is found, e.g. for a project installed there); the
    chained causes are searched too (an agent task error wraps the user's
    exception).  Paths below the working directory are relative.

    :return: the location, or ``None`` when no frame lies in user code

    Example::

        user_frame(exc)      # "model.py:9 in on_step"
    """
    import os
    import sysconfig
    import traceback

    package = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths = sysconfig.get_paths()
    stdlib = {
        os.path.abspath(path)
        for key in ("stdlib", "platstdlib")
        if (path := paths.get(key))
    }
    installed = {
        os.path.abspath(path)
        for key in ("purelib", "platlib")
        if (path := paths.get(key))
    }

    def third_party(path: str) -> bool:
        parts = path.split(os.sep)
        return (
            "site-packages" in parts
            or "dist-packages" in parts
            or any(path.startswith(root + os.sep) for root in installed)
        )

    found = fallback = None
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for frame in traceback.extract_tb(current.__traceback__):
            path = os.path.abspath(frame.filename)
            if frame.filename.startswith("<") or path.startswith(package + os.sep):
                continue
            if third_party(path):
                fallback = frame
                continue
            if any(path.startswith(root + os.sep) for root in stdlib):
                continue
            found = frame
        if found is not None:
            break
        current = current.__cause__ or current.__context__
    found = found or fallback
    if found is None:
        return None
    path = os.path.abspath(found.filename)
    try:
        shown = os.path.relpath(path)
    except ValueError:  # another drive on Windows
        shown = path
    if shown.startswith(".."):
        shown = path
    return f"{shown}:{found.lineno} in {found.name}"

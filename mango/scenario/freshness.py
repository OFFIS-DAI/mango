"""
Code and data fingerprints of runs, and the stale check of finished runs.

A run id covers the scenario reference, the resolved parameters and the
seed.  It does not change when the code of the project or a data file the
parameters reference changes, so a finished run may have been made with
other code or data than a resume would use.  Every run therefore records

- ``code_files``: ``{file: digest}`` of the project modules the run used:
  the modules of the scenario object (its factories, kits, params model,
  communication and environment factories) and of the outcome functions,
  and every project module those import (import statements anywhere in
  their source, followed transitively), plus every project module a fresh
  worker process imported while running it; a scenario *file* is listed
  too.  Project modules are those below the project root, except
  installed packages and mango itself (whose version is in the provenance);
- ``data_files``: ``{file: digest}`` of every existing file a string in the
  resolved parameters names (profile and :class:`~mango.scenario.FromColumn`
  tables, files of the user's own parameters).

Code files are digested by meaning (:func:`code_digest`): comments, blank
lines and formatting of Python source, and comments and layout of a YAML or
JSON scenario file, do not count, so documenting a module does not make its
runs stale.  Data files are digested by their bytes (:func:`file_digest`).

A finished run is *stale* when one of these files changed or disappeared
since (:func:`stale_reasons`).  Design CSVs are not part of ``data_files``:
their values become factor values and so enter the run ids.

Keys are paths relative to the project root (posix), or absolute for files
outside it, so the check survives moving the project folder.

Example::

    from mango.scenario.freshness import code_files, stale_reasons

    files = code_files("/home/me/project", objects=[scenario])
    stale_reasons(manifest, roots=["/home/me/project"])   # [] when up to date
"""

from __future__ import annotations

import ast
import functools
import hashlib
import io
import json
import os
import sys
import threading
import tokenize
import types
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ._util import content_hash, data_path_candidates, using_project_root

__all__ = [
    "file_digest",
    "code_digest",
    "code_files",
    "changed_since_import",
    "data_files",
    "files_fingerprint",
    "stale_reasons",
    "describe_stale",
    "current_entries",
    "StaleReason",
]

_DIGEST_CACHE: dict[tuple, str] = {}
_DIGEST_LOCK = threading.Lock()
_INSTALLED_MARKERS = ("site-packages", "dist-packages")
#: mango itself is the framework, not project code (its version is recorded)
_FRAMEWORK_PACKAGES = ("mango",)
_MAX_DEPTH = 8


def file_digest(path: str | os.PathLike) -> str | None:
    """Return the first 16 hex characters of the SHA-256 of a file's bytes.

    Digests are cached per process by path, modification time and size.

    :param path: the file
    :return: the digest, or ``None`` when the file does not exist

    Example::

        file_digest("data/profiles.csv")      # "3f2a9c1b7d4e5f60"
    """
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (os.fspath(path), stat.st_mtime_ns, stat.st_size)
    with _DIGEST_LOCK:
        found = _DIGEST_CACHE.get(key)
    if found is not None:
        return found
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for block in iter(functools.partial(handle.read, 1 << 20), b""):
                digest.update(block)
    except OSError:
        return None
    value = digest.hexdigest()[:16]
    with _DIGEST_LOCK:
        _DIGEST_CACHE[key] = value
    return value


#: token types that carry no meaning: comments and line breaks inside brackets
#: or of blank lines
_LAYOUT_TOKENS = frozenset({tokenize.COMMENT, tokenize.NL})
#: token types compared by type only (their text is whitespace)
_STRUCTURE_TOKENS = frozenset(
    {tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE, tokenize.ENDMARKER}
)
_FSTRING_START = getattr(tokenize, "FSTRING_START", None)  # Python >= 3.12
_FSTRING_END = getattr(tokenize, "FSTRING_END", None)
#: digest kinds of :func:`code_digest`, by file suffix
_CODE_SUFFIXES = {".py": "python", ".yaml": "data", ".yml": "data", ".json": "data"}


def _python_meaning(data: bytes) -> bytes:
    """The token stream of Python source without comments and layout.

    Tokens are written by name, and an f-string is one string token with
    its source text on every Python version (3.12 splits it into parts), so
    a new interpreter does not change the digest of most modules.
    """
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    lines = io.StringIO(data.decode(encoding)).readlines()

    def text(start: tuple[int, int], end: tuple[int, int]) -> str:
        (row, col), (end_row, end_col) = start, end
        if row == end_row:
            return lines[row - 1][col:end_col]
        return (
            lines[row - 1][col:]
            + "".join(lines[row : end_row - 1])
            + lines[end_row - 1][:end_col]
        )

    parts: list[str] = []
    fstring_starts: list[tuple[int, int]] = []
    tokens = tokenize.generate_tokens(io.StringIO("".join(lines)).readline)
    for token in tokens:
        if _FSTRING_START is not None and token.type == _FSTRING_START:
            fstring_starts.append(token.start)
            continue
        if fstring_starts:
            if token.type == _FSTRING_END:
                start = fstring_starts.pop()
                if not fstring_starts:
                    parts.append(f"STRING:{text(start, token.end)}")
            continue
        if token.type in _LAYOUT_TOKENS:
            continue
        if token.type == tokenize.ERRORTOKEN:  # not Python: the bytes count
            raise ValueError("source does not tokenize")
        name = tokenize.tok_name[token.type]
        if token.type in _STRUCTURE_TOKENS:
            parts.append(name)
        else:
            parts.append(f"{name}:{token.string}")
    return "\x00".join(parts).encode("utf-8")


def _data_meaning(data: bytes, suffix: str) -> bytes:
    """The loaded contents of a YAML or JSON file (comments and layout dropped)."""
    if suffix == ".json":
        loaded: Any = json.loads(data)
    else:
        import yaml

        loaded = list(yaml.safe_load_all(data))
    return repr(loaded).encode("utf-8")


def code_digest(path: str | os.PathLike) -> str | None:
    """Digest a code file by meaning: comments and layout do not count.

    For Python source the digest covers the token stream without comments,
    blank lines, line breaks inside brackets and the indentation width, so
    adding a comment or reformatting a module leaves it unchanged; any other
    edit (also of a docstring) changes it.  For YAML and JSON files (scenario
    files) it covers the loaded contents.  Other files, and files that do
    not tokenize or load, are digested by their bytes like
    :func:`file_digest`.  Digests are cached per process like
    :func:`file_digest`.

    :param path: the file
    :return: 16 hex characters, or ``None`` when the file does not exist

    Example::

        code_digest("model.py")    # the same after adding a comment
    """
    suffix = Path(path).suffix.lower()
    kind = _CODE_SUFFIXES.get(suffix)
    if kind is None:
        return file_digest(path)
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (os.fspath(path), stat.st_mtime_ns, stat.st_size, kind)
    with _DIGEST_LOCK:
        found = _DIGEST_CACHE.get(key)
    if found is not None:
        return found
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    try:
        if kind == "python":
            meaning = _python_meaning(data)
        else:
            meaning = _data_meaning(data, suffix)
    except Exception:  # does not tokenize or load: the bytes count
        return file_digest(path)
    value = hashlib.sha256(kind.encode() + b"\x00" + meaning).hexdigest()[:16]
    with _DIGEST_LOCK:
        _DIGEST_CACHE[key] = value
    return value


def _file_key(path: Path, root: Path | None) -> str:
    if root is not None:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def _resolved(path: Any) -> Path | None:
    try:
        return Path(path).resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _is_installed(path: Path) -> bool:
    return any(part in _INSTALLED_MARKERS for part in path.parts)


# --------------------------------------------------------------------------- code


def _objects_modules(objects: Iterable[Any]) -> set[str]:
    """Names of the modules the objects' functions, classes and instances come from."""
    names: set[str] = set()
    seen: set[int] = set()
    stack = [(obj, 0) for obj in objects]
    while stack:
        obj, depth = stack.pop()
        if (
            obj is None
            or isinstance(obj, str | bytes | int | float | complex | bool)
            or id(obj) in seen
            or depth > _MAX_DEPTH
        ):
            continue
        seen.add(id(obj))
        if isinstance(obj, types.ModuleType):
            names.add(obj.__name__)
            continue
        if isinstance(obj, type):
            names.add(obj.__module__)
            continue
        if isinstance(obj, types.FunctionType | types.BuiltinFunctionType):
            if isinstance(getattr(obj, "__module__", None), str):
                names.add(obj.__module__)
            children: list[Any] = list(getattr(obj, "__defaults__", None) or ())
            children += list((getattr(obj, "__kwdefaults__", None) or {}).values())
            for cell in getattr(obj, "__closure__", None) or ():
                try:
                    children.append(cell.cell_contents)
                except ValueError:  # an empty cell
                    pass
            wrapped = getattr(obj, "__wrapped__", None)
            if wrapped is not None:
                children.append(wrapped)
            stack += [(child, depth + 1) for child in children]
            continue
        if isinstance(obj, types.MethodType):
            stack += [(obj.__func__, depth + 1), (obj.__self__, depth + 1)]
            continue
        if isinstance(obj, functools.partial):
            stack.append((obj.func, depth + 1))
            stack += [(item, depth + 1) for item in obj.args]
            stack += [(item, depth + 1) for item in obj.keywords.values()]
            continue
        if isinstance(obj, Mapping):
            stack += [(item, depth + 1) for pair in obj.items() for item in pair]
            continue
        if isinstance(obj, list | tuple | set | frozenset):
            stack += [(item, depth + 1) for item in obj]
            continue
        names.add(type(obj).__module__)
        try:
            attributes = vars(obj)
        except TypeError:
            continue
        own = attributes.get("__module__")  # e.g. a Scenario records its module
        if isinstance(own, str):
            names.add(own)
        stack += [(item, depth + 1) for item in list(attributes.values())]
    return names


class _Project:
    """Decides which loaded modules are project modules (and their files)."""

    def __init__(self, root: Path | None, exclude: Sequence[Path]):
        self.root = root
        self.exclude = [path for path in exclude if path is not None]
        self._files: dict[str, Path | None] = {}

    def file(self, name: str) -> Path | None:
        if name in self._files:
            return self._files[name]
        self._files[name] = path = self._project_file(name)
        return path

    def _project_file(self, name: str) -> Path | None:
        if self.root is None or name.partition(".")[0] in _FRAMEWORK_PACKAGES:
            return None
        module = sys.modules.get(name)
        file = getattr(module, "__file__", None)
        if not isinstance(file, str) or not file.endswith(".py"):
            return None
        path = _resolved(file)
        if path is None or _is_installed(path) or not path.is_relative_to(self.root):
            return None
        if any(path.is_relative_to(folder) for folder in self.exclude):
            return None
        return path


_IMPORTS_CACHE: dict[tuple[str, str], tuple[str, ...]] = {}


def _source_imports(name: str, path: Path) -> tuple[str, ...]:
    """Module names the source of module *name* imports anywhere (also in functions)."""
    digest = file_digest(path) or ""
    key = (str(path), digest)
    with _DIGEST_LOCK:
        found = _IMPORTS_CACHE.get(key)
    if found is not None:
        return found
    try:
        tree = ast.parse(path.read_bytes(), str(path))
    except (OSError, SyntaxError, ValueError):
        return ()
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                keep = len(parts) - (node.level - 1)
                if keep < 0:
                    continue
                prefix = ".".join(parts[:keep])
                base = f"{prefix}.{base}" if prefix and base else (prefix or base)
            if not base:
                continue
            names.append(base)
            names += [f"{base}.{alias.name}" for alias in node.names]
    result = tuple(dict.fromkeys(names))
    with _DIGEST_LOCK:
        _IMPORTS_CACHE[key] = result
    return result


def _module_closure(roots: Iterable[str], project: _Project) -> set[str]:
    """Project modules reachable from *roots* through imports.

    Follows the import statements of each module's source (also imports
    inside functions) and the module-level names that hold modules,
    functions, classes or instances of other project modules.
    """
    found: set[str] = set()
    queue = [name for name in roots if project.file(name) is not None]
    while queue:
        name = queue.pop()
        if name in found:
            continue
        found.add(name)
        parent = name.rpartition(".")[0]
        while parent:  # a package's __init__ runs when a submodule is imported
            if parent not in found and project.file(parent) is not None:
                queue.append(parent)
            parent = parent.rpartition(".")[0]
        others: list[Any] = []
        path = project.file(name)
        if path is not None:
            others += _source_imports(name, path)
        module = sys.modules.get(name)
        for value in list(vars(module).values()) if module is not None else ():
            if isinstance(value, types.ModuleType):
                # a package holds its imported submodules as attributes; they
                # are not imports of the package itself
                if not value.__name__.startswith(name + "."):
                    others.append(value.__name__)
            elif isinstance(value, type | types.FunctionType):
                others.append(getattr(value, "__module__", None))
            else:
                others.append(type(value).__module__)
        queue += [
            other
            for other in others
            if isinstance(other, str)
            and other not in found
            and project.file(other) is not None
        ]
    return found


#: digest of each loaded module's file when it was first hashed in this process,
#: keyed by (file, module object, module spec) so a re-import or reload starts anew
_FIRST_SEEN: dict[tuple[str, int, int], str] = {}


def _module_digests(
    root: str | os.PathLike | None,
    objects: Iterable[Any],
    loaded: bool,
    exclude: Sequence[str | os.PathLike],
) -> tuple[Path | None, list[tuple[Path, str, str]]]:
    """``(root, [(file, digest now, digest when first seen), ...])`` of the project modules."""
    base = _resolved(root) if root else None
    project = _Project(base, [_resolved(path) for path in exclude])
    names = set(_objects_modules(objects))
    if loaded and base is not None:
        prefixes = tuple({str(base), os.path.abspath(os.fspath(root))})
        names |= {
            name
            for name, module in list(sys.modules.items())
            if name not in ("__main__", "__mp_main__")
            and isinstance(getattr(module, "__file__", None), str)
            and module.__file__.startswith(prefixes)  # cheap pre-filters
            and not any(marker in module.__file__ for marker in _INSTALLED_MARKERS)
        }
    found = []
    for name in _module_closure(names, project):
        path = project.file(name)
        digest = code_digest(path) if path is not None else None
        if digest is None:
            continue
        module = sys.modules.get(name)
        key = (str(path), id(module), id(getattr(module, "__spec__", None)))
        with _DIGEST_LOCK:
            first = _FIRST_SEEN.setdefault(key, digest)
        found.append((path, digest, first))
    return base, found


def code_files(
    root: str | os.PathLike | None,
    *,
    objects: Iterable[Any] = (),
    loaded: bool = False,
    files: Iterable[str | os.PathLike] = (),
    exclude: Sequence[str | os.PathLike] = (),
    as_imported: bool = False,
) -> dict[str, str]:
    """Digest the project code a run uses.

    :param root: the project root (``None``: no project code)
    :param objects: objects whose modules (and the project modules those
        import) count, e.g. the scenario and the outcome functions
    :param loaded: also count every project module imported in this process
        (true in a fresh worker process, where nothing else was imported);
        the running script (``__main__``) counts only when *objects* reach it
    :param files: further files that count (e.g. a scenario file)
    :param exclude: folders whose modules do not count (the study folder)
    :param as_imported: digest each module as this process first saw it
        (the code its runs execute even when the file changed since) instead
        of the file on disk now; *files* are always digested as they are now
    :return: ``{file: digest}``, sorted by file

    Example::

        code_files(Path.cwd(), objects=[scenario, kpi.peak_import_kw])
        # {"ev_study/agents.py": "3f2a...", "ev_study/scenario.py": "9b1c..."}
    """
    now, imported = _code_files_both(root, objects, loaded, files, exclude)
    return imported if as_imported else now


def _code_files_both(
    root: Any, objects: Iterable[Any], loaded: bool, files: Iterable[Any], exclude: Any
) -> tuple[dict[str, str], dict[str, str]]:
    """``(digests on disk now, digests as imported)`` of :func:`code_files`, in one pass."""
    base, modules = _module_digests(root, objects, loaded, exclude)
    now = {_file_key(path, base): digest for path, digest, _ in modules}
    imported = {_file_key(path, base): first for path, _, first in modules}
    for item in files:
        path = _resolved(item)
        digest = code_digest(path) if path is not None else None
        if digest is not None:
            now[_file_key(path, base)] = imported[_file_key(path, base)] = digest
    return dict(sorted(now.items())), dict(sorted(imported.items()))


def changed_since_import(
    root: str | os.PathLike | None,
    *,
    objects: Iterable[Any] = (),
    exclude: Sequence[str | os.PathLike] = (),
) -> list[str]:
    """Project module files that changed on disk since this process first hashed them.

    The modules in memory still hold the old code then, so runs executed in
    this process (sequentially, in a notebook) would not match the files
    their manifests record.  The first hash is taken when the module is
    first seen by :func:`code_files` or this function (planning a study
    does that); a re-import or ``importlib.reload`` starts anew.

    :param root: the project root
    :param objects: as for :func:`code_files`
    :param exclude: as for :func:`code_files`
    :return: the changed files (keys as in :func:`code_files`)

    Example::

        changed_since_import(Path.cwd(), objects=[scenario])    # ["model.py"]
    """
    base, modules = _module_digests(root, objects, False, exclude)
    return sorted(
        _file_key(path, base) for path, digest, first in modules if digest != first
    )


# --------------------------------------------------------------------------- data


def _path_strings(data: Any) -> Iterable[str]:
    if isinstance(data, str):
        if 0 < len(data) < 4096 and "\n" not in data and Path(data).suffix:
            yield data
    elif isinstance(data, Mapping):
        for value in data.values():
            yield from _path_strings(value)
    elif isinstance(data, list | tuple):
        for value in data:
            yield from _path_strings(value)


def data_files(params: Any, root: str | os.PathLike | None = None) -> dict[str, str]:
    """Digest every existing file that a string in the parameters names.

    Relative paths resolve like profile paths: against the working
    directory, then the project root.

    :param params: the resolved parameters (plain data, e.g. a run spec's
        ``params``)
    :param root: the project root
    :return: ``{file: digest}``, sorted by file

    Example::

        data_files({"profiles": {"h0": {"path": "data/profiles.csv"}}}, Path.cwd())
        # {"data/profiles.csv": "77ab..."}
    """
    base = _resolved(root) if root else None
    result: dict[str, str] = {}
    with using_project_root(os.fspath(root) if root else None):
        for text in dict.fromkeys(_path_strings(params)):
            if ":" in text and "/" not in text and "\\" not in text:
                continue  # an import reference
            for candidate in data_path_candidates(text):
                try:
                    if not candidate.is_file():
                        continue
                except OSError:
                    continue
                path = _resolved(candidate)
                digest = file_digest(path) if path is not None else None
                if digest is not None:
                    result[_file_key(path, base)] = digest
                break
    return dict(sorted(result.items()))


def files_fingerprint(files: Mapping[str, str] | None) -> str | None:
    """A 12-character hash of a ``{file: digest}`` mapping (``None`` when empty).

    Example::

        files_fingerprint({"model.py": "3f2a9c1b7d4e5f60"})    # "b81c0e..."
    """
    return content_hash(dict(files)) if files else None


# --------------------------------------------------------------------------- stale


class StaleReason(tuple):
    """``(kind, file)``: ``kind`` is ``"code"``, ``"data"`` or ``"scenario"``.

    ``file`` is the changed or removed file (``None`` for ``"scenario"``,
    the changed scenario definition).
    """

    __slots__ = ()

    def __new__(cls, kind: str, file: str | None = None):
        return super().__new__(cls, (kind, file))

    @property
    def kind(self) -> str:
        return self[0]

    @property
    def file(self) -> str | None:
        return self[1]


def _locate(key: str, roots: Sequence[Path]) -> tuple[Path | None, bool]:
    """``(path, known)``: the file of *key*; *known* is false when no root exists."""
    path = Path(key)
    if path.is_absolute():
        return path, True
    known = False
    for root in roots:
        if root.is_dir():
            known = True
            candidate = root / path
            if candidate.exists():
                return candidate, True
    return None, known


def _same_digest(kind: str, path: Path, digest: Any) -> bool:
    if kind == "code" and code_digest(path) == digest:
        return True
    # data files, and code digests of manifests written before code_digest
    return file_digest(path) == digest


def stale_reasons(
    manifest: Mapping,
    *,
    roots: Sequence[str | os.PathLike | None] = (),
    scenario_fingerprint: str | None = None,
) -> list[StaleReason]:
    """Why a finished run no longer matches the current code and data.

    Compares the ``code_files`` and ``data_files`` the manifest recorded
    with the files on disk now, and (when given) the scenario fingerprint.
    Relative files are looked up below the first of *roots* that holds
    them; when none of the roots exists (the project moved) a file counts
    as unknown, not as changed.  Manifests of older runs without
    fingerprints are never stale.

    :param manifest: the run's ``manifest.json`` contents
    :param roots: project roots to look relative files up in, in order
    :param scenario_fingerprint: the current :meth:`~mango.scenario.Scenario.fingerprint`
    :return: the reasons; empty when the run is up to date

    Example::

        stale_reasons(manifest, roots=[Path.cwd()])
        # [StaleReason("code", "dr_kits/aggregator.py")]
    """
    bases = [Path(root) for root in roots if root]
    reasons: list[StaleReason] = []
    for kind, field in (("code", "code_files"), ("data", "data_files")):
        recorded = manifest.get(field)
        if not isinstance(recorded, Mapping):
            continue
        for key, digest in recorded.items():
            path, known = _locate(str(key), bases)
            if not known:
                continue
            if path is None or not _same_digest(kind, path, digest):
                reasons.append(StaleReason(kind, str(key)))
    stored = manifest.get("scenario_fingerprint")
    if (
        scenario_fingerprint is not None
        and stored is not None
        and stored != scenario_fingerprint
    ):
        reasons.append(StaleReason("scenario"))
    return reasons


def current_entries(
    manifest: Mapping, *, roots: Sequence[str | os.PathLike | None] = ()
) -> dict:
    """The ``code_files``/``data_files`` entries of a manifest digested anew from disk.

    Used to re-certify a stale run (see :meth:`~mango.scenario.Study.certify`):
    the recorded files keep their keys, get their current digests and
    fingerprints; files that no longer exist are dropped.  Files that cannot
    be located (no root exists) keep their recorded digest.

    :param manifest: the run's ``manifest.json`` contents
    :param roots: project roots, as for :func:`stale_reasons`
    :return: ``{"code_files", "data_files", "code_fingerprint", "data_fingerprint"}``

    Example::

        manifest.update(current_entries(manifest, roots=[Path.cwd()]))
    """
    bases = [Path(root) for root in roots if root]
    entries: dict[str, Any] = {}
    for kind, field in (("code", "code_files"), ("data", "data_files")):
        recorded = manifest.get(field)
        files: dict[str, str] = {}
        for key, digest in recorded.items() if isinstance(recorded, Mapping) else ():
            path, known = _locate(str(key), bases)
            if not known:
                files[str(key)] = digest
                continue
            if path is None:
                continue
            now = code_digest(path) if kind == "code" else file_digest(path)
            if now is not None:
                files[str(key)] = now
        entries[field] = dict(sorted(files.items()))
        entries[f"{kind}_fingerprint"] = files_fingerprint(files)
    return entries


def describe_stale(
    reasons_per_run: Iterable[Sequence[StaleReason]], limit: int = 5
) -> str:
    """Summarise stale reasons: ``"code changed in a.py, b.py; data changed in x.csv"``.

    Example::

        describe_stale([[StaleReason("code", "model.py")]])   # "code changed in model.py"
    """
    files: dict[str, list[str]] = {"code": [], "data": []}
    scenario = False
    for reasons in reasons_per_run:
        for reason in reasons:
            if reason.kind == "scenario":
                scenario = True
            elif reason.file not in files[reason.kind]:
                files[reason.kind].append(reason.file)
    parts = []
    for kind in ("code", "data"):
        names = files[kind]
        if names:
            shown = ", ".join(names[:limit])
            more = f" and {len(names) - limit} more" if len(names) > limit else ""
            parts.append(f"{kind} changed in {shown}{more}")
    if scenario:
        parts.append("the scenario definition changed")
    return "; ".join(parts)

"""
Turn a scenario into a plain mango script (:meth:`Scenario.to_script
<mango.scenario.Scenario.to_script>`).

The generated script builds the world one run of the scenario builds, with
the mango API written out: ``create_world`` with the run's seed and calendar,
the communication simulation, one registration loop per group calling the
group's factory, every link as explicit topology edges, the recorders, and
the step loop.  It uses the same random streams as the scenario run, so it
records the same values; it is meant for debugging (set a breakpoint in the
loop) and for teaching (what does the scenario layer do?).

The script imports the scenario's own modules (its parameter model,
factories and agent classes) and a few pieces of :mod:`mango.scenario` (the
parameter models, ``Window``/``group_reduce`` for reduced recorders,
``LayeredCommunication`` for link delay/loss), so it needs mango installed
and the project's modules importable: :meth:`GeneratedScript.write` puts the
project folder on the import path when the script is written elsewhere.

Two forms:

- **lean** (the default when the scenario allows it): ``create_world``, the
  registration loops, the topology, the recorders and a plain step loop,
  with a small ``ScriptContext``/``ScriptEntity`` holding only what the
  scenario's code uses of ``ctx`` and ``e``.  Possible when the scenario uses
  no kits, phases, events, communication changes, link delay/loss,
  partitions, ``stop_when``/``ctx.stop``, ``ctx.spawn``/``ctx.retire``/
  ``ctx.relink``, environment behaviors or resources, and steps with a fixed
  size.
- **full**: also writes out phases (``record: false``), events,
  communication switches, link delay/loss and partitions
  (``LayeredCommunication``), kit scoping, kit behaviors, spaces, resources,
  ``stop_when``, and what agents call while the run steps: ``ctx.spawn``,
  ``ctx.retire``, ``ctx.relink``, ``ctx.link_pairs``, ``ctx.stop``,
  ``ctx.log``, ``ctx.set_metric``, ``ctx.phase``.

Recorders are written with their kinds, windows, aggregates and categories;
event logs, metrics and the stop reason are printed (and logs written next to
the values CSV).  The ``ctx``/``e`` methods agents call while the run steps
are included when the scenario's modules call them (``.spawn(``,
``.relink(``, ``.log(``, ...); a method the script left out raises an
``AttributeError`` naming it.

Code is referenced by import where possible: module-level functions and
classes are imported; lambdas and local functions are written out when
their closure variables hold literals (numbers, strings, lists, ...),
parameter models (``ctx.p`` inside a kit becomes ``params.<prefix>``) or
importable objects; objects kits create (behaviors, the space, resources)
are written out as constructor calls when the class is importable and the
object's state is exactly what its constructor makes of the arguments.
Everything else becomes a clearly marked ``Placeholder`` that raises when
the script reaches it; :attr:`GeneratedScript.placeholders` lists them.

Example::

    script = scenario.to_script("presets/base.yaml", seed=7)
    print(script.lean, script.placeholders)
    Path("plain.py").write_text(script)
    # mango-scenario to-script ev_study.scenario:scenario --params presets/base.yaml
"""

from __future__ import annotations

import ast
import builtins
import dis
import importlib
import inspect
import keyword
import linecache
import operator
import pprint
import re
import textwrap
import types
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mango.util.seeding import SeedTree, name_key

from ._util import _main_module_name, _positional_capacity, add_note, object_ref
from .params import dump_params
from .patterns import is_deterministic, pattern_name
from .specs import Entity, link_pairs, links_by_name

if TYPE_CHECKING:
    from .builder import Scenario
    from .context import RunState

__all__ = ["GeneratedScript", "generate_script", "lean_obstacles"]

_WIDTH = 88


class GeneratedScript(str):
    """The source text of a generated script, with what the generator found.

    It is a ``str`` (write it to a file, ``print`` it, compare it) with two
    extra attributes:

    - ``placeholders``: what each ``Placeholder`` in the script stands for,
      in the order they appear (empty when the script runs as it is)
    - ``lean``: ``True`` for the lean form (see :mod:`mango.scenario.script`)

    Example::

        script = scenario.to_script(seed=1)
        if script.placeholders:
            print("replace before running:", *script.placeholders, sep="\\n  ")
        Path("plain.py").write_text(script)
    """

    placeholders: tuple[str, ...]
    lean: bool

    def __new__(
        cls,
        text: str,
        *,
        placeholders: Any = (),
        lean: bool = False,
        project_import: str | None = None,
    ) -> GeneratedScript:
        obj = super().__new__(cls, text)
        obj.placeholders = tuple(placeholders)
        obj.lean = lean
        #: the first import line of the scenario's own modules (None: none)
        obj.project_import = project_import
        return obj

    def for_location(self, path: Any, project_root: Any = None) -> GeneratedScript:
        """The script as it must read when saved at *path*.

        A script saved outside the project folder (*project_root*, default
        the working directory) gets a few lines before the imports of the
        scenario's modules: they put the project folder on the import path
        and make it the working directory (relative data paths such as
        profiles are read from there), so ``python <path>`` works from
        anywhere; a CSV path given on the command line stays relative to
        where the script was started.  Inside the project folder, or when
        the script imports no module of the project, the text is unchanged.

        :param path: where the script will be saved
        :param project_root: the folder the scenario's modules are imported
            from (default: the working directory)
        :return: the script for that location (same ``placeholders`` and
            ``lean``)

        Example::

            script = scenario.to_script(seed=7).for_location("archive/run_7.py")
        """
        root = Path(project_root or Path.cwd()).resolve()
        folder = Path(path).resolve().parent
        if self.project_import is None or folder == root:
            return self
        text = str(self)
        at = text.find(self.project_import + "\n")
        if at < 0:  # pragma: no cover - the generator always writes the line
            return self
        try:
            up = folder.relative_to(root).parts
            where = f"Path(__file__).resolve().parents[{len(up)}]"
            folder_text = f"the project folder ({'/'.join(['..'] * len(up))} from here)"
        except ValueError:  # the script lives outside the project
            where = f"Path({str(root)!r})"
            folder_text = "the project folder"
        block = (
            f"# run from any folder: {folder_text} holds the scenario's\n"
            "# modules and is the base of relative data paths (profiles, tables)\n"
            f"PROJECT = {where}\n"
            "CALLER_DIR = Path.cwd()  # a CSV path on the command line is relative to it\n"
            "sys.path.insert(0, str(PROJECT))\n"
            "os.chdir(PROJECT)\n\n"
        )
        text = text[:at] + block + text[at:]
        text = _add_stdlib_imports(text, ["import os", "from pathlib import Path"])
        text = text.replace(
            "    main(*sys.argv[1:2])",
            "    main(*[str(CALLER_DIR / path) for path in sys.argv[1:2]])",
        )
        text = text.replace(
            "Run (from the project folder): python <this file> [values.csv]",
            "Run (from any folder): python <this file> [values.csv]",
        )
        return GeneratedScript(
            text,
            placeholders=self.placeholders,
            lean=self.lean,
            project_import=self.project_import,
        )

    def write(self, path: Any, *, project_root: Any = None) -> Path:
        """Save the script at *path* (folders are created) so it runs from there.

        See :meth:`for_location`: outside the project folder the script puts
        that folder on the import path itself.

        :param path: the file to write
        :param project_root: the folder of the scenario's modules (default:
            the working directory)
        :return: the path written

        Example::

            scenario.to_script("presets/base.yaml", seed=7).write("archive/run_7.py")
            # python archive/run_7.py values.csv
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.for_location(target, project_root), encoding="utf-8")
        return target


def _add_stdlib_imports(text: str, statements: list[str]) -> str:
    """*text* with *statements* added to its first (stdlib) import block, kept sorted."""
    lines = text.split("\n")
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(("import ", "from "))),
        None,
    )
    if start is None:
        return text
    end = start
    while end < len(lines) and lines[end].startswith(("import ", "from ")):
        end += 1
    block = sorted(
        {*lines[start:end], *statements},
        key=lambda line: (line.startswith("from"), line),
    )
    return "\n".join([*lines[:start], *block, *lines[end:]])


#: names the generated script defines itself (user objects must not shadow them)
_SCRIPT_NAMES = frozenset(
    {
        "SEED",
        "CONFIG",
        "HORIZON_S",
        "STEP_S",
        "N_STEPS",
        "BOUNDARIES",
        "SAMPLE_PERIODS",
        "PHASES",
        "EVENTS",
        "STOP_WHEN",
        "COMMUNICATION",
        "RESOURCES",
        "SHARED",
        "params",
        "tolerance",
        "sample_phase",
        "due",
        "Placeholder",
        "ScriptContext",
        "ScriptEntity",
        "register",
        "add_world_recorder",
        "add_agent_recorder",
        "add_windowed_world_recorder",
        "add_reduced_recorder",
        "reduced_rows",
        "make_communication",
        "follow_communication",
        "next_boundary",
        "stop_now",
        "simulate",
        "main",
        "LINK_COMM",
        "LINK_PATTERNS",
        "STATE_VARIABLES",
        "PROJECT",
        "CALLER_DIR",
        "Path",
        "phase_at",
        "recorded",
        "set_edge_rules",
        "layered",
        "event_targets",
        "uninstall",
        "write_csv",
        "add_group_recorder",
        # locals of simulate(): an import with one of these names would be shadowed
        "seeds",
        "ctx",
        "communication",
        "environment",
        "world",
        "e",
        "index",
        "name",
        "topology",
        "nodes",
        "node",
        "a",
        "b",
        "events",
        "apply_events",
        "k",
        "t",
        "target",
        "remaining",
    }
)

# Fixed mango imports of every script: name -> (module, attribute)
_BASE_IMPORTS = {
    "SeedTree": ("mango.util.seeding", "SeedTree"),
    "create_world": ("mango.simulation", "create_world"),
    "recordings_to_records": ("mango.simulation", "recordings_to_records"),
}


# --------------------------------------------------------------------------- imports


class _Imports:
    """The script's import block: binds local names to objects, aliasing on clashes."""

    def __init__(self) -> None:
        self._bound: dict[str, Any] = {name: _RESERVED for name in _SCRIPT_NAMES}
        for module in _STDLIB_MODULES:
            self._bound[module] = importlib.import_module(module)
        self._lines: dict[str, set[str]] = {}
        self._stdlib: set[str] = set()
        #: the first import line of a module outside mango (set by lines())
        self.project_line: str | None = None

    def stdlib(self, statement: str) -> None:
        self._stdlib.add(statement)

    def local(self, name: str, obj: Any) -> bool:
        """Bind *name* without an import line (script definitions); False on a clash."""
        if name in self._bound:
            return self._bound[name] is obj
        self._bound[name] = obj
        return True

    def module(self, name: str, module: types.ModuleType) -> bool:
        """``import module [as name]``; False on a clash."""
        if not self.local(name, module):
            return False
        if module.__name__ == name:
            self.stdlib(f"import {name}")
        else:
            self.stdlib(f"import {module.__name__} as {name}")
        return True

    def bind(self, name: str, obj: Any, module: str, attr: str) -> bool:
        """Bind *name* to *obj* (``from module import attr as name``); False on a clash."""
        if name in self._bound:
            return self._bound[name] is obj
        self.local(name, obj)
        entry = attr if attr == name else f"{attr} as {name}"
        self._lines.setdefault(module, set()).add(entry)
        return True

    def mango(self, name: str) -> str:
        module, attr = _MANGO_NAMES[name]
        self.bind(name, getattr(importlib.import_module(module), attr), module, attr)
        return name

    def ref(self, ref: str, obj: Any) -> str | None:
        """An expression for the object *ref* names (``module:qualname``)."""
        module, _, qualname = ref.partition(":")
        head, _, rest = qualname.partition(".")
        target = obj
        if rest:
            try:
                target = getattr(importlib.import_module(module), head)
            except (ImportError, AttributeError):
                return None
        tail = f".{rest}" if rest else ""
        if self.bind(head, target, module, head):
            return head + tail
        stem = re.sub(r"\W", "_", module.rsplit(".", 1)[-1])
        for i in range(1, 100):
            alias = f"{stem}_{head}" if i == 1 else f"{stem}_{head}_{i}"
            if self.bind(alias, target, module, head):
                return alias + tail
        return None

    def lines(self) -> list[str]:
        out = sorted(self._stdlib, key=lambda line: (line.startswith("from"), line))
        if out:
            out.append("")
        groups = [
            sorted(m for m in self._lines if m.split(".")[0] == "mango"),
            sorted(m for m in self._lines if m.split(".")[0] != "mango"),
        ]
        self.project_line = None
        for modules in groups:
            for module in modules:
                names = sorted(self._lines[module], key=_import_order)
                line = f"from {module} import {', '.join(names)}"
                if len(line) > _WIDTH:
                    body = "".join(f"    {name},\n" for name in names)
                    line = f"from {module} import (\n{body})"
                if modules is groups[1] and self.project_line is None:
                    self.project_line = line.split("\n")[0]
                out.append(line)
            if modules:
                out.append("")
        return out[:-1] if out and out[-1] == "" else out


def _import_order(entry: str) -> tuple:
    """isort's order by type: CONSTANTS, Classes, functions."""
    name = entry.split(" as ")[0]
    kind = 0 if name.isupper() else 1 if name[:1].isupper() else 2
    return kind, name


_RESERVED = object()
_STDLIB_MODULES = ("asyncio", "copy", "inspect", "json", "os", "sys", "warnings")

_MANGO_NAMES = {
    **_BASE_IMPORTS,
    "DefaultEnvironment": ("mango.simulation", "DefaultEnvironment"),
    "SimpleCommunicationSimulation": (
        "mango.simulation",
        "SimpleCommunicationSimulation",
    ),
    "create_topology": ("mango.express.topology", "create_topology"),
    "build_communication": ("mango.scenario", "build_communication"),
    "CommConfig": ("mango.scenario", "CommConfig"),
    "ScenarioEvent": ("mango.scenario", "ScenarioEvent"),
    "load_profile": ("mango.scenario", "load_profile"),
    "load_params": ("mango.scenario", "load_params"),
    "load_scenario": ("mango.scenario", "load_scenario"),
    "compose_behavior": ("mango.scenario.components", "compose_behavior"),
    "Window": ("mango.scenario.reducers", "Window"),
    "group_reduce": ("mango.scenario.reducers", "group_reduce"),
    "LayeredCommunication": ("mango.scenario", "LayeredCommunication"),
    "LinkComm": ("mango.scenario", "LinkComm"),
    "FromColumn": ("mango.scenario", "FromColumn"),
    "EdgeRule": ("mango.scenario.comm", "EdgeRule"),
    "partition_blocks": ("mango.scenario.comm", "partition_blocks"),
    "name_key": ("mango.util.seeding", "name_key"),
}


# --------------------------------------------------------------------------- callables


def _identifier(text: str) -> str:
    name = re.sub(r"\W+", "_", text).strip("_").lower() or "fn"
    if name[0].isdigit() or keyword.iskeyword(name):
        name = f"_{name}"
    return name


def _importable_ref(obj: Any) -> str | None:
    """``module:qualname`` of *obj* with ``__main__`` mapped to the script's module name."""
    ref = object_ref(obj)
    if ref is None:
        return None
    module, _, qualname = ref.partition(":")
    if module == "__main__":
        name = _main_module_name()
        if name is None:
            return None
        ref = f"{name}:{qualname}"
    return ref


def _global_names(code: types.CodeType) -> set[str]:
    names: set[str] = set()
    for instruction in dis.get_instructions(code):
        if instruction.opname in ("LOAD_GLOBAL", "LOAD_NAME"):
            names.add(instruction.argval)
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= _global_names(const)
    return names


def _signature(code: types.CodeType, free: frozenset = frozenset()) -> tuple:
    """What identifies a lambda's code among those on one line (bytecode details vary).

    Names in *free* are left out: compiled alone, a closure variable is a global.
    """
    consts = tuple(
        _signature(c, free) if isinstance(c, types.CodeType) else repr(c)
        for c in code.co_consts
    )
    return (
        code.co_argcount,
        code.co_kwonlyargcount,
        tuple(name for name in code.co_names if name not in free),
        code.co_varnames,
        consts,
    )


def _lambda_node(fn: Callable) -> tuple[ast.Lambda, str] | None:
    """The AST node and source text of lambda *fn* (``None`` if not found unambiguously)."""
    code = fn.__code__
    try:
        filename = inspect.getsourcefile(fn)
    except TypeError:
        return None
    if not filename:
        return None
    source = "".join(linecache.getlines(filename))
    if not source:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Lambda) or node.lineno != code.co_firstlineno:
            continue
        text = ast.get_source_segment(source, node)
        if text is None:
            continue
        try:
            compiled = compile(f"({text}\n)", "<lambda>", "eval")
        except SyntaxError:
            continue
        inner = next(
            (c for c in compiled.co_consts if isinstance(c, types.CodeType)), None
        )
        free = frozenset(code.co_freevars)
        if inner is not None and _signature(inner, free) == _signature(code, free):
            found.append((node, source))
    texts = {ast.get_source_segment(src, node) for node, src in found}
    return found[0] if len(texts) == 1 else None


def _source_excerpt(fn: Any, limit: int = 12) -> list[str]:
    """The source lines of *fn* (or its class) for a placeholder comment."""
    target = fn if callable(fn) else type(fn)
    try:
        lines, _ = inspect.getsourcelines(target)
    except (OSError, TypeError):
        return []
    text = textwrap.dedent("".join(lines)).rstrip().splitlines()
    if len(text) > limit:
        text = [*text[:limit], "..."]
    return text


def _location(fn: Any) -> str:
    target = fn if callable(fn) else type(fn)
    try:
        filename = inspect.getsourcefile(target)
        _, line = inspect.getsourcelines(target)
    except (OSError, TypeError):
        return ""
    if not filename:
        return ""
    path = Path(filename)
    try:
        shown = path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        shown = path.name
    return f"{shown}:{line}"


def _describe(fn: Any) -> str:
    if isinstance(fn, types.FunctionType | types.MethodType | type):
        name = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", "?")
    else:
        name = f"{type(fn).__qualname__} instance"
    where = _location(fn)
    return f"{name} ({where})" if where else name


# --------------------------------------------------------------------------- script


def _function_node(fn: Callable) -> ast.FunctionDef | None:
    """The AST of local function *fn* (plain ``def``, no decorators), else ``None``."""
    try:
        source = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    node = tree.body[0] if tree.body else None
    if (
        not isinstance(node, ast.FunctionDef)
        or node.name != fn.__name__
        or node.decorator_list
    ):
        return None
    return node


def _function_def(
    fn: Callable, node: ast.FunctionDef, name: str, bindings: list[str]
) -> list[str]:
    """The source of local function *fn* renamed to *name*, with *bindings* first."""
    source = textwrap.dedent(inspect.getsource(fn)).splitlines()
    first = node.body[0]
    docstring = (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )
    at = (first.end_lineno if docstring else first.lineno - 1) or 0
    if first.lineno == node.lineno or len(node.body) == 1 and docstring:
        # 'def f(e): return x' (one line): rebuild from the syntax tree
        body = [ast.parse(line).body[0] for line in bindings]
        node.body[int(docstring) : int(docstring)] = body
        node.name = name
        return ast.unparse(node).splitlines()
    pad = " " * first.col_offset
    head = re.sub(
        rf"def\s+{re.escape(fn.__name__)}\b", f"def {name}", source[0], count=1
    )
    return [head, *source[1:at], *(pad + line for line in bindings), *source[at:]]


def _snake(name: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", "_", name).lower()


def _same_value(a: Any, b: Any, depth: int = 0) -> bool:
    if a is b:
        return True
    if type(a) is not type(b):
        return False
    if getattr(a, "__dict__", None) is not None and type(a).__eq__ is object.__eq__:
        return depth < 3 and _same_state(a, b, depth + 1)  # plain objects: by state
    try:
        return bool(a == b)
    except Exception:  # e.g. arrays: ambiguous truth value
        return False


def _same_state(a: Any, b: Any, depth: int = 0) -> bool:
    """True when *a* and *b* are of one type with equal attributes."""
    if type(a) is not type(b):
        return False
    state_a, state_b = getattr(a, "__dict__", None), getattr(b, "__dict__", None)
    if state_a is None or state_b is None:
        return _same_value(a, b, depth)
    return state_a.keys() == state_b.keys() and all(
        _same_value(value, state_b[key], depth) for key, value in state_a.items()
    )


def _code_key(fn: Any) -> tuple | None:
    """Identifies functions with one code object and the same closure values."""
    if not isinstance(fn, types.FunctionType):
        return None
    try:
        cells = tuple(id(cell.cell_contents) for cell in fn.__closure__ or ())
    except ValueError:
        return None
    return ("code", id(fn.__code__), cells, fn.__defaults__ is None)


class _Script:
    """Collects imports, function definitions, placeholders and body code."""

    def __init__(self, params: Any = None) -> None:
        self.imports = _Imports()
        self.functions: list[list[str]] = []
        self.placeholders: list[list[str]] = []
        self.placeholder_whats: list[str] = []
        self._names: set[str] = set(_SCRIPT_NAMES)
        self._exprs: dict[Any, str] = {}
        self._keep: list[Any] = []  # keeps ids in _exprs valid
        self._params = params
        self._model_paths: dict[int, str] | None = None
        self._inlining: set[int] = set()
        #: objects simulate() creates (contexts, kit objects): id -> local name;
        #: written-out closures reach them as SHARED[name]
        self.shared: dict[int, str] = {}
        self.shared_used: dict[str, None] = {}

    @property
    def placeholder_count(self) -> int:
        return len(self.placeholder_whats)

    def unique(self, base: str) -> str:
        name = _identifier(base)
        candidate, i = name, 2
        while candidate in self._names or not self.imports.local(candidate, _RESERVED):
            candidate = f"{name}_{i}"
            i += 1
        self._names.add(candidate)
        return candidate

    # -- objects -------------------------------------------------------------------

    def callable_expr(self, fn: Any, what: str, base: str) -> str:
        """An expression for callable *fn*: import, written-out function or placeholder."""
        expr = self._callable_or_none(fn, base)
        if expr is None:
            expr = self.placeholder(fn, what, base)
            self._exprs[id(fn)] = expr
            self._keep.append(fn)
        return expr

    def _callable_or_none(self, fn: Any, base: str) -> str | None:
        cached = self._exprs.get(id(fn))
        if cached is not None:
            return cached
        expr = self._attrgetter(fn) or self._import(fn)
        if expr is None:
            twin = _code_key(fn)  # the same lambda created again, e.g. in a loop
            expr = self._exprs.get(twin) if twin is not None else None
            if expr is None:
                expr = self._inline(fn, base)
                if expr is not None and twin is not None:
                    self._exprs[twin] = expr
        if expr is not None:
            self._exprs[id(fn)] = expr
            self._keep.append(fn)
        return expr

    def _attrgetter(self, fn: Any) -> str | None:
        if not isinstance(fn, operator.attrgetter):
            return None
        _, args = fn.__reduce__()
        if not self.imports.local("attrgetter", operator.attrgetter):
            return None
        self.imports.stdlib("from operator import attrgetter")
        return f"attrgetter({', '.join(repr(a) for a in args)})"

    def _import(self, obj: Any) -> str | None:
        ref = _importable_ref(obj)
        if ref is None:
            return None
        return self.imports.ref(ref, obj)

    def params_path(self, obj: Any) -> str | None:
        """``params.<path>`` when *obj* is the run's params or a model inside them."""
        if self._params is None:
            return None
        if self._model_paths is None:
            self._model_paths = {}
            pending = [(self._params, "params")]
            while pending:
                model, path = pending.pop(0)
                if id(model) in self._model_paths or path.count(".") > 5:
                    continue
                self._model_paths[id(model)] = path
                for name in type(model).model_fields:
                    child = getattr(model, name, None)
                    if hasattr(type(child), "model_fields") and name.isidentifier():
                        pending.append((child, f"{path}.{name}"))
        return self._model_paths.get(id(obj))

    def plain_expr(self, value: Any) -> str | None:
        """A literal, a ``params.<path>`` or an import for *value*, else ``None``."""
        if _is_literal(value):
            return _literal(value)
        return self.params_path(value) or self._import(value)

    def _closure_bindings(self, fn: types.FunctionType, base: str) -> list[str] | None:
        """``name = expr`` lines for the closure variables of *fn* (``None``: not possible)."""
        lines, used = [], []
        for name, cell in zip(fn.__code__.co_freevars, fn.__closure__ or ()):
            try:
                value = cell.cell_contents
            except ValueError:  # an empty cell
                return None
            local = self.shared.get(id(value))
            if local is not None:
                used.append(local)
                expr = f"SHARED[{local!r}]"
            else:
                expr = self.plain_expr(value)
            if expr is None and isinstance(value, types.FunctionType):
                if id(value) in self._inlining:
                    return None
                expr = self._callable_or_none(value, f"{base}_{name}")
            if expr is None:
                return None
            if expr == name:
                continue
            if re.match(rf"{re.escape(name)}\b", expr):
                return None  # 'params = params.north' would read the local
            lines.append(f"{name} = {expr}")
        self.shared_used.update(dict.fromkeys(used))
        return lines

    def _inline(self, fn: Any, base: str) -> str | None:
        if not isinstance(fn, types.FunctionType) or id(fn) in self._inlining:
            return None
        if fn.__defaults__ or fn.__kwdefaults__:
            return None
        is_lambda = fn.__name__ == "<lambda>"
        if not is_lambda and "<locals>" not in fn.__qualname__:
            return None  # a module-level function that could not be imported
        self._inlining.add(id(fn))
        try:
            return self._write_out(fn, base, is_lambda)
        finally:
            self._inlining.discard(id(fn))

    def _write_out(
        self, fn: types.FunctionType, base: str, is_lambda: bool
    ) -> str | None:
        code = fn.__code__
        module = fn.__module__
        if module == "__main__":
            module = _main_module_name()
            if module is None:
                return None
        if is_lambda:
            found = _lambda_node(fn)
            if found is None:
                return None
            node, source = found
        else:
            node = _function_node(fn)
            if node is None:
                return None
        bindings = self._closure_bindings(fn, base)
        if bindings is None:
            return None
        globals_ = fn.__globals__
        for name in sorted(_global_names(code)):
            if name in globals_:
                if not self._bind_global(name, globals_[name], module):
                    return None
            elif not hasattr(builtins, name):
                return None
        name = self.unique(base)
        if is_lambda:
            text = self._lambda_def(node, source, name, bindings)
            if text is None:
                return None
        else:
            text = _function_def(fn, node, name, bindings)
        where = _location(fn)
        what = "lambda" if is_lambda else f"local function {fn.__qualname__}"
        comment = f"# {what} at {where}" if where else f"# {what}"
        if bindings:
            comment += " (closure variables written out)"
        self.functions.append([comment, *text])
        return name

    @staticmethod
    def _lambda_def(
        node: ast.Lambda, source: str, name: str, bindings: list[str]
    ) -> list[str] | None:
        args = ast.unparse(node.args)
        body = ast.get_source_segment(source, node.body)
        if body is None:
            return None
        lines = body.splitlines()
        text = [f"def {name}({args}):", *(f"    {line}" for line in bindings)]
        if len(lines) == 1:
            return [*text, f"    return {body}"]
        rest = textwrap.dedent("\n".join(lines[1:])).splitlines()
        return [
            *text,
            f"    return ({lines[0]}",
            *(f"        {line}" for line in rest),
            "    )",
        ]

    def _bind_global(self, name: str, obj: Any, module: str) -> bool:
        """Make the global *name* of a lambda's module available in the script."""
        if isinstance(obj, types.ModuleType):
            return self.imports.module(name, obj)
        ref = _importable_ref(obj)
        if ref is not None:
            home, _, qualname = ref.partition(":")
            if qualname == name:
                return self.imports.bind(name, obj, home, name)
        return self.imports.bind(name, obj, module, name)

    def placeholder(self, obj: Any, what: str, base: str) -> str:
        """A module-level ``Placeholder`` standing for *obj* (raises when used)."""
        self.placeholder_whats.append(what)
        name = self.unique(f"{base}_placeholder")
        detail = "" if obj is None else f": {_describe(obj)}"
        lines = [f"# PLACEHOLDER {what}{detail}"]
        excerpt = _source_excerpt(obj) if inspect.isfunction(obj) else []
        lines += [f"#     {line}" if line else "#" for line in excerpt]
        lines.append(f"{name} = Placeholder({what!r})")
        self.placeholders.append(lines)
        return name

    def value_expr(self, value: Any, what: str, base: str) -> str:
        """A literal for plain data, an import for module-level objects, else a placeholder."""
        if _is_literal(value):
            return _literal(value)
        expr = self._import(value)
        if expr is not None:
            return expr
        return self.placeholder(value, what, base)

    def ctx_call(self, fn: Any, expr: str, ctx_expr: str) -> str:
        """A zero-argument callable calling *fn* like ``call_flexible(fn, ctx)``."""
        return expr if _takes_no_args(fn) else f"lambda: {expr}({ctx_expr})"

    def ctx_arg(self, fn: Any, expr: str) -> str:
        """A callable taking ctx, for *fn* called like ``call_flexible(fn, ctx)``."""
        return f"lambda ctx: {expr}()" if _takes_no_args(fn) else expr

    def construction(self, obj: Any) -> str | None:
        """``Cls(arg=..., ...)`` rebuilding *obj*, or ``None``.

        Only when the class is importable, every constructor argument is
        stored under its name (or ``_name``) as a literal, a parameter model
        or an importable object, and constructing the class from those
        arguments gives an object with exactly *obj*'s state.
        """
        cls = type(obj)
        if getattr(obj, "__dict__", None) is None:
            return None
        try:
            signature = inspect.signature(cls)
        except (TypeError, ValueError):
            return None
        state = vars(obj)
        kwargs: dict[str, Any] = {}
        exprs: list[str] = []
        for parameter in signature.parameters.values():
            if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                continue
            if parameter.kind is parameter.POSITIONAL_ONLY:
                return None
            key = next(
                (k for k in (parameter.name, f"_{parameter.name}") if k in state), None
            )
            if key is None:
                if parameter.default is parameter.empty:
                    return None
                continue
            value = state[key]
            if parameter.default is not parameter.empty and _same_value(
                value, parameter.default
            ):
                kwargs[parameter.name] = value
                continue
            expr = self.plain_expr(value)
            if expr is None:
                return None
            kwargs[parameter.name] = value
            exprs.append(f"{parameter.name}={expr}")
        try:
            rebuilt = cls(**kwargs)
        except Exception:
            return None
        if not _same_state(rebuilt, obj):
            return None
        head = self._import(cls)
        if head is None:
            return None
        return f"{head}({', '.join(exprs)})"


def _takes_no_args(fn: Any) -> bool:
    """True when ``call_flexible(fn, ctx)`` calls *fn* without arguments."""
    try:
        return _positional_capacity(fn) == 0
    except (TypeError, ValueError):
        return False


def _is_literal(value: Any) -> bool:
    try:
        return ast.literal_eval(repr(value)) == value and _plain(value)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return False


def _plain(value: Any) -> bool:
    if value is None or isinstance(value, bool | int | float | str):
        return type(value) in (bool, int, float, str, type(None))
    if isinstance(value, list | tuple):
        return type(value) in (list, tuple) and all(_plain(v) for v in value)
    if isinstance(value, dict):
        return type(value) is dict and all(
            _plain(k) and _plain(v) for k, v in value.items()
        )
    return False


def _literal(value: Any, indent: int = 0) -> str:
    text = pprint.pformat(value, width=max(40, _WIDTH - indent), sort_dicts=False)
    pad = " " * indent
    return text.replace("\n", "\n" + pad)


def _names_expr(names: list[str], group: str) -> str:
    default = names == [f"{group}_{i}" for i in range(len(names))]
    if default and len(names) > 1 and re.fullmatch(r"[\w.]+", group):
        return f'[f"{group}_{{i}}" for i in range({len(names)})]'
    return _literal(names, 8)


# --------------------------------------------------------------------------- edges


def _pattern_needs_agents(spec: Any) -> bool:
    if spec.where is not None:
        return True
    if is_deterministic(spec.pattern):
        return False
    module = getattr(spec.pattern, "__module__", None)
    return not (
        module == "mango.scenario.patterns"
        and pattern_name(spec.pattern).startswith("random(")
    )


def _link_edges(specs: list, by_group: Mapping[str, list], seeds: SeedTree):
    """Participants (node order) and edges (insertion order, deduplicated) of one link."""
    rng = seeds.rng("link", name_key(specs[0].name))
    participants: list[str] = []
    seen_nodes: set[str] = set()
    edges: list[tuple[str, str]] = []
    seen_edges: set = set()
    directed = specs[0].directed
    for spec in specs:
        src = by_group[spec.source]
        dst = src if spec.target == spec.source else by_group[spec.target]
        for entity in (*src, *dst):
            if entity.name not in seen_nodes:
                seen_nodes.add(entity.name)
                participants.append(entity.name)
        for a, b in link_pairs(spec, src, dst, rng):
            key = (a.name, b.name) if directed else frozenset((a.name, b.name))
            if key not in seen_edges:
                seen_edges.add(key)
                edges.append((a.name, b.name))
    return participants, edges


async def _built_state(scenario: Scenario, params: Any, seed: dict) -> RunState:
    """Build the run's agents (no start) so attribute-based links can be evaluated."""
    from .runtime import ScenarioRun

    run = ScenarioRun(scenario, params, SeedTree(seed))
    state = scenario._declaration_stage(params, run.seeds)
    state.run = run
    run._state = state
    run._adopt_schedule(state.schedule)
    world = run._create_world(state)
    state.world = world
    try:
        positioned = run._instantiate(state)
        run._apply_positions(state, positioned)
    finally:
        await world.shutdown()
    return state


def _all_edges(scenario: Scenario, state: RunState, params: Any, seed: dict):
    links = links_by_name(state.links)
    if any(_pattern_needs_agents(spec) for spec in state.links):
        from .runtime import run_sync

        try:
            built = run_sync(lambda: _built_state(scenario, params, seed))
        except Exception as exc:
            add_note(
                exc,
                "while building the agents once to compute the link edges for "
                "to_script (links with where= or attribute patterns need them)",
            )
            raise
        by_group = built.by_group
        links = links_by_name(built.links)
    else:
        by_group = {
            spec.name: [Entity(n, spec.name, i) for i, n in enumerate(spec.names)]
            for spec in state.groups.values()
        }
    seeds = SeedTree(seed)
    return {
        name: (specs, *_link_edges(specs, by_group, seeds))
        for name, specs in links.items()
    }


# --------------------------------------------------------------------------- templates


def _select(text: str, flags: Mapping[str, Any]) -> str:
    """The lines of template *text* whose conditional blocks hold for *flags*.

    A line ``#[if name]`` (or ``#[if a or b]``, ``#[if not name]``) starts a
    block, ``#[else]`` flips it and ``#[end]`` closes it; blocks nest.  The
    directive lines themselves are dropped.
    """
    out: list[str] = []
    stack: list[bool] = []
    for line in text.split("\n"):
        directive = line.strip()
        if directive.startswith("#[") and directive.endswith("]"):
            word = directive[2:-1].strip()
            if word.startswith("if "):
                condition = word[3:].strip()
                negate = condition.startswith("not ")
                names = condition[4:] if negate else condition
                value = any(bool(flags.get(n.strip())) for n in names.split(" or "))
                stack.append(value != negate)
            elif word == "else":
                stack[-1] = not stack[-1]
            elif word == "end":
                stack.pop()
            else:  # pragma: no cover - a typo in a template
                raise ValueError(f"unknown template directive {directive!r}")
            continue
        if all(stack):
            out.append(line)
    if stack:  # pragma: no cover - a typo in a template
        raise ValueError("unclosed template block")
    return "\n".join(out)


# --------------------------------------------------------------------------- code scan

#: Attributes of ctx/e that agents use while the run steps, by script feature.
_RUNTIME_FEATURES = {
    "population": ("spawn", "retire"),
    "relink": ("relink", "link_pairs"),
    "stop": ("stop",),
    "logs": ("log",),
    "metrics": ("set_metric", "get_metric"),
    "scratch": ("state",),
    "phase": ("phase", "recording"),
    "warn": ("warn",),
}


def _is_project_module(module: Any) -> bool:
    """True for a module of the user's project (not mango, the stdlib or site-packages)."""
    import sysconfig

    filename = getattr(module, "__file__", None)
    if not filename or not str(filename).endswith(".py"):
        return False
    path = Path(filename).resolve()
    excluded = [Path(__file__).resolve().parents[1]]  # the mango package
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        location = sysconfig.get_paths().get(key)
        if location:
            excluded.append(Path(location).resolve())
    return not any(path.is_relative_to(folder) for folder in excluded)


def _module_of(obj: Any) -> types.ModuleType | None:
    import sys

    if isinstance(obj, types.ModuleType):
        return obj
    name = getattr(obj, "__module__", None)
    if not isinstance(name, str):
        name = getattr(type(obj), "__module__", None)
    return sys.modules.get(name) if isinstance(name, str) else None


def _code_roots(scenario: Scenario, state: RunState) -> list[Any]:
    """The objects whose modules hold the scenario's code."""
    roots: list[Any] = [scenario]
    ref = getattr(scenario, "ref", None)
    if isinstance(ref, str) and ":" in ref:
        import sys

        roots.append(sys.modules.get(ref.partition(":")[0]))
    for spec in state.groups.values():
        roots += [spec.factory, spec.install, spec.position, spec.naming]
    for rec in state.records:
        roots += [rec.getter, getattr(rec.getter, "fn", None), rec.aggregate]
    roots += [hook for hook, _ in state.build_hooks]
    roots += [spec.action for spec in state.schedule.events]
    roots += [condition.fn for condition in state.stop_conditions]
    roots += [spec.fn for spec in state.metric_specs]
    roots += [scenario._communication_factory, scenario._environment_factory]
    roots += [behavior for _, behavior in state.behaviors]
    roots += [obj for obj, _ in state.resources.values()]
    roots += [state.space, *(kit for _, kit in scenario._kits)]
    roots += [link.pattern for link in state.links]
    try:
        refs = list(scenario.factory_refs() or ())
    except Exception:  # only a hint for finding modules
        refs = []
    for ref in refs:
        import sys

        roots.append(sys.modules.get(str(ref).partition(":")[0]))
    out = []
    for root in roots:
        if root is None:
            continue
        out.append(root)
        for cell in getattr(root, "__closure__", None) or ():
            try:
                out.append(cell.cell_contents)
            except ValueError:
                continue
    return out


def code_attributes(scenario: Scenario, state: RunState) -> set[str]:
    """Attribute names the scenario's modules use (``x.spawn(...)`` gives ``"spawn"``).

    The modules are those of the scenario, its factories, recorders, hooks,
    events, kits and their closures, and the project modules they import
    (transitively); mango, the standard library and installed packages are
    left out.  The script includes the ``ctx``/``e`` methods agents call
    while the run steps only when their names occur here.

    :param scenario: the scenario
    :param state: the run's declaration state
    :return: the attribute names
    """
    pending = [_module_of(root) for root in _code_roots(scenario, state)]
    seen: set[str] = set()
    names: set[str] = set()
    while pending:
        module = pending.pop()
        if module is None or module.__name__ in seen:
            continue
        seen.add(module.__name__)
        if not _is_project_module(module):
            continue
        try:
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            continue
        names |= {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        for value in list(vars(module).values()):
            if isinstance(value, types.ModuleType | type | types.FunctionType):
                pending.append(_module_of(value))
    return names


def _runtime_features(attrs: set[str]) -> set[str]:
    return {
        feature
        for feature, methods in _RUNTIME_FEATURES.items()
        if any(method in attrs for method in methods)
    }


# --------------------------------------------------------------------------- generator


def lean_obstacles(scenario: Scenario, state: RunState) -> list[str]:
    """What keeps the script of a run from the lean form (empty: lean is possible).

    :param scenario: the scenario
    :param state: the run's declaration state
    :return: one short reason per feature, e.g. ``"events"``
    """
    schedule = state.schedule
    reasons = []
    features = _runtime_features(code_attributes(scenario, state))
    if scenario._kits:
        reasons.append("kits")
    phases = schedule.phases
    if len(phases) > 1 or (phases and not phases[0].record):
        reasons.append("phases")
    if schedule.events:
        reasons.append("events")
    if schedule.has_comm_changes:
        reasons.append("communication changes")
    partition = getattr(state.params.comm, "partition", None)
    if partition or any(spec.comm is not None for spec in state.links):
        reasons.append("link delay/loss or comm.partition")
    if state.stop_conditions or "stop" in features:
        reasons.append("stop_when or ctx.stop()")
    if "population" in features:
        reasons.append("ctx.spawn()/ctx.retire()")
    if "relink" in features:
        reasons.append("ctx.relink()/ctx.link_pairs()")
    if state.behaviors or state.space is not None:
        reasons.append("environment behaviors or a space set with ctx")
    if state.resources:
        reasons.append("resources (ctx.provide)")
    if state.time.is_event_driven:
        reasons.append("event-driven time")
    return reasons


def generate_script(
    scenario: Scenario,
    params: Any = None,
    *,
    seed: Any = None,
    overrides: Any = None,
    lean: bool | None = None,
) -> GeneratedScript:
    """Return a plain mango script equivalent to one run of *scenario*.

    See :meth:`Scenario.to_script <mango.scenario.Scenario.to_script>`.

    :param scenario: the scenario
    :param params: as for :meth:`~mango.scenario.Scenario.resolve_params`
    :param seed: the run seed (as for :meth:`~mango.scenario.Scenario.build`):
        an int or a seed dict as a run spec stores it (``{"entropy": ...,
        "spawn_key": [...]}``); without it ``params.seed`` is used, else
        fresh entropy is drawn and written into the script
    :param overrides: dotted overrides
    :param lean: ``None`` (default) writes the lean form when the scenario
        allows it, ``True`` requires it, ``False`` always writes the full form
    :return: the script source (a ``str`` with ``placeholders`` and ``lean``)
    :raises ScenarioError: for ``lean=True`` when the scenario uses features
        only the full form writes out (the message names them)

    Example::

        script = generate_script(scenario, "presets/base.yaml", seed=7)
        Path("ev_script.py").write_text(script)
    """
    from .builder import _resolve_seed

    resolved = scenario.resolve_params(params, overrides=overrides)
    seed_dict = _resolve_seed(seed, resolved).to_dict()
    state = scenario._declaration_stage(resolved, SeedTree(seed_dict))
    obstacles = lean_obstacles(scenario, state)
    if lean and obstacles:
        from .errors import ScenarioError

        raise ScenarioError(
            f"Scenario {scenario.name!r} cannot be written as a lean script: it uses "
            f"{', '.join(obstacles)}; write the full script (lean=False, or "
            "mango-scenario to-script without --lean)."
        )
    links = _all_edges(scenario, state, resolved, seed_dict)
    generator = _Generator(scenario, resolved, seed_dict, state, links)
    is_lean = not obstacles if lean is None else bool(lean)
    text = generator.render_lean() if is_lean else generator.render()
    return GeneratedScript(
        text,
        placeholders=generator.out.placeholder_whats,
        lean=is_lean,
        project_import=generator.out.imports.project_line,
    )


class _Generator:
    def __init__(
        self,
        scenario: Scenario,
        params: Any,
        seed: dict,
        state: RunState,
        links: dict,
    ):
        self.scenario = scenario
        self.params = params
        self.seed = seed
        self.state = state
        self.links = links
        self.time = params.time
        self.schedule = state.schedule
        self.out = _Script(params)
        self.ctx_names: dict[str, str] = {"": "ctx"}
        self.objects: dict[int, str] = {}  # id -> local name in simulate()
        self.lean = False
        #: reduced recorders used: "world" (window), "agent", "aggregate",
        #: "group" (lean: plain aggregates over groups)
        self.reductions: set[str] = set()
        self.attrs = code_attributes(scenario, state)
        self.features = _runtime_features(self.attrs)
        self.flags = self._flags()
        self.out.shared[id(state.root)] = "ctx"
        for prefix, _ in scenario._kits:  # the kit contexts simulate() creates
            if prefix in state.kit_contexts:
                self.out.shared[id(state.kit_contexts[prefix])] = self.ctx_expr(prefix)

    def _flags(self) -> dict[str, bool]:
        """Which optional parts of the script templates this run needs."""
        state, attrs, features = self.state, self.attrs, self.features
        records = state.records
        layered = bool(self.link_comm()) or any(
            settings.get("partition") for _, settings in self.comm_entries()
        )
        population = "population" in features
        relink = "relink" in features
        logs = "logs" in features
        phase_api = "phase" in features
        is_state = any(rec.value_kind == "state" for rec in records)
        events = state.schedule.events
        return {
            "every": any(rec.every is not None for rec in records),
            "state": is_state,
            "state_rows": any(rec.row_kind == "state" for rec in records),
            "hold": any(rec.holds for rec in records),
            "categories": any(rec.categories for rec in records),
            "population": population,
            "relink": relink,
            "links": bool(state.links) and (layered or population or relink),
            "layered": layered,
            "logs": logs,
            "metrics": "metrics" in features or bool(state.metric_specs),
            "scratch": "scratch" in features,
            "stops": "stop" in features or bool(state.stop_conditions),
            "phase": phase_api,
            "phase_at": is_state or logs or phase_api,
            "warn": "warn" in features,
            "tables": _has_sample_table(self.params) or "row" in attrs,
            "profiles": bool(self.params.profiles),
            "dynamic_targets": any(spec.selector is not None for spec in events)
            or (population and any(spec.targets is not None for spec in events)),
            "names": population or relink,
            # what the lean ScriptContext/ScriptEntity offer besides the basics
            "comm": "comm" in attrs,
            "rng": "rng" in attrs,
            "space": "space" in attrs,
            "draw": "draw" in attrs,
            "rng_for": "rng_for" in attrs,
            "world": "world" in attrs,
        }

    def link_comm(self) -> dict[str, Any]:
        """Link name -> its active LinkComm (delay/loss), in declaration order."""
        found: dict[str, Any] = {}
        for name, specs in links_by_name(self.state.links).items():
            comm = next((spec.comm for spec in specs if spec.comm is not None), None)
            if comm is not None and comm.active:
                found[name] = comm
        return found

    def comm_entries(self) -> list[tuple[float, dict]]:
        """``(offset, settings)``: the comm settings from each offset on."""
        schedule = self.schedule
        if not schedule.has_comm_changes:
            return [(0.0, self.params.comm.model_dump(mode="json"))]
        entries: list[tuple[float, dict]] = []
        last = None
        for t in [0.0, *schedule.boundaries]:
            config, key = schedule.comm_at(t)
            if key != last:
                entries.append((t, config.model_dump(mode="json")))
                last = key
        return entries

    def template(self, text: str) -> str:
        """*text* with its conditional blocks resolved for this script."""
        return _select(
            text,
            {
                **self.flags,
                "lean": self.lean,
                "phases": not self.lean,
                "aggregate": "aggregate" in self.reductions,
            },
        )

    def shared_lines(self) -> list[str]:
        """``SHARED.update(...)``: what written-out closures read from simulate()."""
        used = list(self.out.shared_used)
        if not used:
            return []
        return [
            "    # what written-out functions read (their closure variables)",
            f"    SHARED.update({', '.join(f'{name}={name}' for name in used)})",
        ]

    # -- pieces --------------------------------------------------------------------

    def ctx_expr(self, prefix: str) -> str:
        found = self.ctx_names.get(prefix)
        if found is None:
            found = self.out.unique(f"{prefix}_ctx")
            self.ctx_names[prefix] = found
        return found

    def params_lines(self) -> list[str]:
        out = self.out
        scenario = self.scenario
        from .yaml_scenario import FileScenario

        model = scenario._params_model
        if not scenario._kits:
            expr = out._import(model)
            if expr is not None:
                return [f"params = {expr}.model_validate(CONFIG)"]
        # the scenario builds the params model (kit parameters, a scenario file's
        # parameters: block): load the values through the scenario
        if isinstance(scenario, FileScenario):
            loader = out.imports.mango("load_scenario")
            source = f"{loader}({scenario.ref!r})"
        else:
            source = out._import(scenario) if scenario.ref else None
        if source is None:
            if not scenario._kits:
                expr = out.placeholder(
                    model, f"params model {model.__qualname__}", "params_model"
                )
                return [f"params = {expr}(CONFIG)"]
            source = out.placeholder(
                None,
                f"scenario {scenario.name!r} (its params model includes kit "
                "parameters)",
                "scenario",
            )
        out.imports.mango("load_params")
        what = "its kits" if scenario._kits else "the scenario"
        return [
            f"# the params model is built by {what}: load the values through the scenario",
            f"params = load_params({source}, CONFIG)",
        ]

    def communication(self) -> tuple[list[tuple[float, dict]], list[str]]:
        entries = self.comm_entries()
        out = self.out
        factory = self.scenario._communication_factory
        doc = '    """The communication simulation for *settings* (the "comm" stream continues)."""'
        if factory is not None:
            out.imports.mango("CommConfig")
            expr = out.callable_expr(
                factory, "communication factory", "communication_factory"
            )
            body = [
                "def make_communication(ctx, settings):",
                doc,
                "    ctx.comm = CommConfig.model_validate(settings)",
                f"    return {expr}(ctx)",
            ]
        elif all(_simple_constant(settings) for _, settings in entries):
            out.imports.mango("SimpleCommunicationSimulation")
            body = [
                "def make_communication(ctx, settings):",
                doc,
                '    if settings["kind"] == "ideal":',
                '        return SimpleCommunicationSimulation(0.0, 0.0, rng=ctx.seeds.rng("comm"))',
                "    return SimpleCommunicationSimulation(",
                '        settings["loss_percent"], settings["delay_s"], rng=ctx.seeds.rng("comm")',
                "    )",
            ]
        else:
            out.imports.mango("build_communication")
            body = [
                "def make_communication(ctx, settings):",
                doc,
                '    return build_communication(settings, ctx.seeds.rng("comm"))',
            ]
        if self.flags["layered"]:
            for name in ("LayeredCommunication", "EdgeRule", "partition_blocks"):
                out.imports.mango(name)
            body += ["", "", self.template(_LAYERED)]
        return entries, body

    def link_comm_lines(self) -> list[str]:
        """``LINK_COMM``: the delay and loss of the links that have them."""
        comm = self.link_comm()
        if not self.flags["layered"]:
            return []
        if not comm:
            return ["LINK_COMM = {}  # no link has delay/loss (comm.partition only)"]
        self.out.imports.mango("LinkComm")
        lines = [
            "# delay and loss of the messages along links (scenario.link(delay=, loss=)):",
            "# every directed edge draws from its own stream comm-link/<link>/<from>/<to>",
            "LINK_COMM = {",
        ]
        for name, link in comm.items():
            settings = _literal(link.model_dump(mode="json"), 4)
            lines.append(f"    {name!r}: LinkComm.model_validate({settings}),")
        return [*lines, "}"]

    def link_pattern_lines(self) -> list[str]:
        """``LINK_PATTERNS``: what ``ctx.relink(name)`` applies again to the members."""
        if not self.flags["relink"] or not self.state.links:
            return []
        out = self.out
        lines = [
            "# the declared patterns of each link, applied again by ctx.relink(name):",
            "# (source group, target group, pattern(src, dst, rng), where(a, b) or None)",
            "LINK_PATTERNS = {",
        ]
        for name, specs in links_by_name(self.state.links).items():
            base = _identifier(f"{name}_pattern")
            parts = []
            for spec in specs:
                pattern = out.callable_expr(
                    spec.pattern, f"pattern of link {name!r}", base
                )
                where = (
                    "None"
                    if spec.where is None
                    else out.callable_expr(
                        spec.where, f"where of link {name!r}", f"{base}_where"
                    )
                )
                parts.append(f"({spec.source!r}, {spec.target!r}, {pattern}, {where})")
            lines.append(f"    {name!r}: [{', '.join(parts)}],")
        return [*lines, "}"]

    def events(self) -> list[str]:
        out = self.out
        rows = []
        index_of = {id(spec): index for index, spec in enumerate(self.schedule.events)}
        for firing in self.schedule.firings:
            spec = firing.spec
            base = _identifier(spec.name or f"event_{index_of[id(spec)]}")
            payload = (
                "None"
                if not firing.emits
                else out.value_expr(spec.emit, f"emit payload of {spec.label}", base)
            )
            if not firing.acts:
                action = "None"
            else:
                expr = out.callable_expr(
                    spec.action, f"action of {spec.label}", f"{base}_action"
                )
                action = out.ctx_arg(spec.action, expr)
            until = spec.until if firing.stage is not None else None
            targets = repr(spec.targets)
            if self.flags["dynamic_targets"] and firing.emits:
                if spec.selector is not None:  # matched when the event fires
                    targets = repr({"emit_to": None, "selector": spec.selector})
                elif spec.targets is not None:  # the members when the event fires
                    targets = repr({"emit_to": spec.emit_to, "selector": None})
            rows.append(
                f"    ({firing.time!r}, {spec.name!r}, {payload}, {targets}, "
                f"{action}, {firing.stage!r}, {until!r}),"
            )
        return rows

    # -- render --------------------------------------------------------------------

    def render(self) -> str:
        out = self.out
        state = self.state
        time = self.time
        flags = self.flags
        for name in _BASE_IMPORTS:
            out.imports.mango(name)
        out.imports.mango("CommConfig")
        out.imports.stdlib("import asyncio")
        out.imports.stdlib("import sys")
        params_lines = self.params_lines()
        comm_entries, comm_def = self.communication()
        follow = len(comm_entries) > 1
        if follow:
            out.imports.stdlib("import json")
        body = self.simulate_body(follow_communication=follow)
        event_rows = self.events()
        if event_rows:
            out.imports.stdlib("import copy")
        if flags["warn"]:
            out.imports.stdlib("import warnings")
        sections = [
            "# -- the run: parameters, seed and time ------------------------------------------",
            f"SEED = {self.seed_literal()}",
            f"CONFIG = {_literal(dump_params(self.params), 9)}",
            *params_lines,
            f"HORIZON_S = {time.horizon_s!r}",
        ]
        if time.is_event_driven:
            boundaries = sorted({*self.schedule.boundaries, time.horizon_s})
            periods = sorted(
                {rec.every for rec in state.records if rec.every is not None}
                | {c.every for c in state.stop_conditions if c.every is not None}
            )
            sections += [
                "STEP_S = None  # event-driven: steps go to the next message, task or boundary",
                f"BOUNDARIES = {_literal(boundaries, 13)}",
                f"SAMPLE_PERIODS = {_literal(periods, 17)}  # recorders and stop_when with every=",
            ]
        else:
            sections += [
                f"STEP_S = {time.step_s!r}",
                f"N_STEPS = {time.n_steps!r}",
            ]
        sections += [
            "# phases: (start offset s, name, recorded)",
            f"PHASES = {_literal([(p.start, p.name, p.record) for p in self.schedule.phases], 9)}",
            "# communication settings from an offset on: params.comm <- phase comm <- active events",
            f"COMMUNICATION = {_literal(comm_entries, 16)}",
            *self.link_comm_lines(),
        ]
        tables = self.link_pattern_lines()
        if flags["state_rows"]:
            names = sorted({r.name for r in state.records if r.row_kind == "state"})
            sections += [
                "# recorders of states (kind='state'): a row holds from its time on",
                f"STATE_VARIABLES = {_literal(set(names), 18)}",
            ]
        if event_rows:
            tables += [
                "# event deliveries (emits, actions; ends of emit windows) in time order:",
                "# (offset s, name, payload, target entities, action(ctx), stage, until)",
                "EVENTS = [",
                *event_rows,
                "]",
            ]
        else:
            tables.append("EVENTS = []")
        stop_rows = self.stop_rows()
        if stop_rows:
            tables += [
                "# stop_when conditions: (condition(ctx), check every s or None, label)",
                "STOP_WHEN = [",
                *stop_rows,
                "]",
            ]
        elif flags["stops"]:
            tables.append("STOP_WHEN = []  # ctx.stop() only")
        self._table_imports()
        sections += ["", "", self.template(_HELPERS)]
        sections += [
            "",
            "",
            self.template(_CONTEXT_CLASSES).replace("__MISSING__", _MISSING_FULL),
        ]
        if flags["dynamic_targets"]:
            sections += ["", "", _EVENT_TARGETS]
        sections += ["", "", "\n".join(comm_def)]
        if follow:
            sections += ["", "", self.template(_FOLLOW_COMMUNICATION)]
        if time.is_event_driven:
            sections += ["", "", _NEXT_BOUNDARY]
        return self.assemble(sections, body, self.template(_MAIN), tables)

    def assemble(
        self,
        sections: list[str],
        body: list[str],
        main: str,
        tables: list[str] | None = None,
    ) -> str:
        """Put the script together.

        Order: header, imports, placeholders (they may stand for the params
        model), *sections* (the run's data, helpers, classes), the
        written-out functions, *tables* (module-level data that names those
        functions: events, stop conditions, link patterns), simulate() and
        main().
        """
        out = self.out
        for helper in self.reduction_helpers():
            sections += ["", "", helper]
        top: list[str] = []
        if out.placeholders or not self.lean:
            top += [_PLACEHOLDER_CLASS]
        if out.placeholders:
            top += [
                "",
                "",
                "# -- PLACEHOLDERS: code that could not be imported; replace it to run the script --",
            ]
            for lines in out.placeholders:
                top += ["", "\n".join(lines)]
        if top:
            top += ["", ""]
        if out.shared_used:
            sections += [
                "",
                "",
                "# contexts and objects simulate() creates that written-out functions read",
                "SHARED = {}",
            ]
        if out.functions:
            sections += [
                "",
                "",
                "# -- the scenario's functions, written out ------------------------------------------",
            ]
            for fn in out.functions:
                sections += ["", "", "\n".join(fn)]
        if tables:
            sections += [
                "",
                "",
                "# -- the run's events, stop conditions and link patterns -------------------------",
                *tables,
            ]
        sections += ["", "", "\n".join(body), "", "", main]
        sections = [self.header(), "", *out.imports.lines(), "", "", *top, *sections]
        text = "\n".join(sections)
        text = re.sub(r"\n{4,}", "\n\n\n", text)
        return text.rstrip() + "\n"

    # -- lean form -----------------------------------------------------------------

    def render_lean(self) -> str:
        """The lean script: no phases, events, comm switches, kit scoping or placeholders class."""
        self.lean = True
        out = self.out
        time = self.time
        flags = self.flags
        for name in _BASE_IMPORTS:
            out.imports.mango(name)
        out.imports.stdlib("import asyncio")
        out.imports.stdlib("import sys")
        params_lines = self.params_lines()
        body = self.lean_body()
        every = flags["every"]
        kinds = {  # recorders the plain helpers write (reduced ones have their own)
            rec.kind == "world"
            for rec in self.state.records
            if rec.window is None and rec.aggregate is None
        }
        phase = self.schedule.phases[0].name if self.schedule.phases else "main"
        classes = (
            self.template(_LEAN_CONTEXT) + "\n\n\n" + self.template(_LEAN_ENTITY)
        ).replace("__MISSING__", _MISSING_LEAN)
        sections = [
            "# -- the run: parameters, seed and time ------------------------------------------",
            f"SEED = {self.seed_literal()}",
            f"CONFIG = {_literal(dump_params(self.params), 9)}",
            *params_lines,
            f"HORIZON_S = {time.horizon_s!r}",
            f"STEP_S = {time.step_s!r}",
            f"N_STEPS = {time.n_steps!r}",
            "",
            "",
            classes.replace("__PHASE__", repr(phase)),
        ]
        if flags["draw"]:
            out.imports.stdlib("import inspect")  # ScriptEntity.draw
        if flags["profiles"]:
            out.imports.mango("load_profile")
        self._table_imports()
        if flags["warn"]:
            out.imports.stdlib("import warnings")
        helpers = [_LEAN_REGISTER]
        if every or self.reductions & {"world", "agent"}:
            helpers.insert(0, _LEAN_DUE)
        if True in kinds:
            helpers.append(
                _LEAN_WORLD_RECORDER_EVERY if every else _LEAN_WORLD_RECORDER
            )
        if False in kinds:
            helpers.append(
                _LEAN_AGENT_RECORDER_EVERY if every else _LEAN_AGENT_RECORDER
            )
        for helper in helpers:
            sections += ["", "", helper]
        main = self.template(_LEAN_MAIN).replace("__PHASE__", repr(phase))
        return self.assemble(sections, body, main)

    def _table_imports(self) -> None:
        if self.flags["tables"]:
            self.out.imports.mango("FromColumn")
            self.out.imports.mango("name_key")
            self.out.imports.stdlib("import os")

    def lean_communication(self) -> str:
        out = self.out
        factory = self.scenario._communication_factory
        if factory is not None:
            expr = out.callable_expr(
                factory, "communication factory", "communication_factory"
            )
            return f"    communication = {expr}(ctx)"
        settings = self.params.comm.model_dump(mode="json")
        if _simple_constant(settings):
            out.imports.mango("SimpleCommunicationSimulation")
            loss, delay = (
                (0.0, 0.0)
                if settings["kind"] == "ideal"
                else (settings["loss_percent"], settings["delay_s"])
            )
            return (
                f"    communication = SimpleCommunicationSimulation({loss!r}, {delay!r}, "
                'rng=seeds.rng("comm"))'
            )
        out.imports.mango("build_communication")
        return '    communication = build_communication(params.comm, seeds.rng("comm"))'

    def lean_body(self) -> list[str]:
        out = self.out
        lines = [
            "async def simulate():",
            '    """Build the world like the scenario runtime does, then step it to the horizon."""',
            "    seeds = SeedTree(SEED)",
            "    ctx = ScriptContext(params, seeds)",
        ]
        shared_at = len(lines)
        lines += [
            "    # communication simulation (stream 'comm')",
            self.lean_communication(),
        ]
        factory = self.scenario._environment_factory
        if factory is not None:
            expr = out.callable_expr(factory, "environment factory", "make_environment")
            lines.append(f"    environment = {expr}(ctx)")
        else:
            out.imports.mango("DefaultEnvironment")
            lines.append("    environment = DefaultEnvironment()")
        calendar = "None" if self.time.start is None else "params.time.start"
        lines += [
            "    world = create_world(",
            "        communication_sim=communication,",
            "        environment=environment,",
            "        seed=seeds,  # agents draw from world.rng('agent', aid) = e.rng",
            f"        start_datetime={calendar},",
            "        record_messages=False,  # count messages, do not keep them",
            "    )",
            "    ctx.world = world",
        ]
        lines += self.group_lines()
        lines += self.link_lines()
        lines += self.recorder_lines()
        for hook, _ in self.state.build_hooks:
            expr = out.callable_expr(hook, "on_build hook", "on_build")
            lines += ["", f"    {expr}(ctx)  # on_build hook"]
        lines += [
            "",
            "    async with world:  # starts the agents (on_ready) and records t=0",
            "        for k in range(1, N_STEPS + 1):",
            "            target = HORIZON_S if k == N_STEPS else k * STEP_S",
            "            await world.step(step_size_s=target - world.clock.time)",
        ]
        lines += self.metric_lines()
        lines.append("    return world, ctx")
        lines[shared_at:shared_at] = self.shared_lines()
        return lines

    def seed_literal(self) -> str:
        if not self.seed.get("spawn_key"):
            return repr(self.seed["entropy"])
        return _literal(self.seed, 7)

    def header(self) -> str:
        scenario = self.scenario
        source = scenario.ref or scenario.name
        agents = sum(spec.count for spec in self.state.groups.values())
        lines = [
            '"""',
            f"Plain mango script of scenario {scenario.name!r} ({source}).",
            "",
            "Generated by Scenario.to_script() / mango-scenario to-script. It builds the world",
            "of scenario.run(CONFIG, seed=SEED) with the mango API: the same "
            f"{agents} agent{'' if agents == 1 else 's'},",
            "links, recorders and random streams, so it records the same values.",
            "Editing it does not change the scenario. It imports the scenario's own",
            "modules (parameters, factories, agent classes) and a few helpers of",
            "mango.scenario, so it needs mango installed.",
        ]
        if self.lean:
            lines += [
                "",
                "Lean form: the scenario uses no kits, phases, events or communication",
                "changes, so the script is create_world, registration loops, topology,",
                "recorders and a plain step loop.",
            ]
        whats = self.out.placeholder_whats
        if whats:
            lines += [
                "",
                f"{len(whats)} PLACEHOLDER(s) stand for code that could not be written out",
                "(closures over other objects, objects kits create from other state);",
                "replace them before running, the script raises NotImplementedError",
                "when it reaches one:",
                *(f"  - {what}" for what in whats),
            ]
        lines += [
            "",
            "Run (from the project folder): python <this file> [values.csv]",
            '"""',
        ]
        return "\n".join(lines)

    def simulate_body(self, follow_communication: bool) -> list[str]:
        out = self.out
        state = self.state
        scenario = self.scenario
        lines = [
            "async def simulate():",
            '    """Build the world like the scenario runtime does, then step it to the horizon."""',
            "    seeds = SeedTree(SEED)",
            "    ctx = ScriptContext(params, seeds)",
        ]
        for prefix, _ in scenario._kits:
            lines.append(f"    {self.ctx_expr(prefix)} = ctx.scoped({prefix!r})")
        lines += self.object_lines()
        shared_at = len(lines)
        lines += [
            "",
            "    # communication simulation for the settings at t=0 (stream 'comm')",
            "    ctx.comm = CommConfig.model_validate(COMMUNICATION[0][1])",
            "    communication = make_communication(ctx, COMMUNICATION[0][1])",
        ]
        factory = scenario._environment_factory
        if factory is not None:
            expr = out.callable_expr(factory, "environment factory", "make_environment")
            lines.append(f"    environment = {expr}(ctx)")
        else:
            out.imports.mango("DefaultEnvironment")
            if state.behaviors or state.space is not None:
                lines.append(
                    "    environment = DefaultEnvironment(space=ctx.space, "
                    "behavior=ctx.behavior)"
                )
            else:
                lines.append("    environment = DefaultEnvironment()")
        start = self.time.start
        calendar = "None" if start is None else "params.time.start"
        lines += [
            "    world = create_world(",
            "        communication_sim=communication,",
            "        environment=environment,",
            "        seed=seeds,  # agents draw from world.rng('agent', aid) = e.rng",
            f"        start_datetime={calendar},",
            "        record_messages=False,  # count messages, do not keep them",
            "    )",
            "    ctx.world = world",
        ]
        if follow_communication:
            lines.append("    follow_communication(world, ctx, communication)")
        lines += self.group_lines()
        lines += self.link_lines()
        if self.flags["layered"]:
            lines += [
                "",
                "    # comm.partition and link delay/loss on top (they need the entities and links)",
                "    world.communication_sim = layered(ctx, communication, COMMUNICATION[0][1])",
            ]
        lines += self.recorder_lines()
        for hook, ctx in state.build_hooks:
            expr = out.callable_expr(hook, "on_build hook", "on_build")
            lines += ["", f"    {expr}({self.ctx_expr(ctx.prefix)})  # on_build hook"]
        lines += self.loop_lines()
        lines += self.metric_lines()
        lines += ["    return world, ctx"]
        lines[shared_at:shared_at] = self.shared_lines()
        return lines

    def metric_lines(self) -> list[str]:
        """The metrics declared with scenario.metric, computed when the run ended."""
        specs = self.state.metric_specs
        if not specs:
            return []
        lines = ["", "    # metrics computed when the run ended (scenario.metric)"]
        for spec in specs:
            expr = self.out.callable_expr(
                spec.fn, f"metric {spec.name!r}", f"metric_{spec.name}"
            )
            call = self.out.ctx_call(spec.fn, expr, self.ctx_expr(spec.ctx.prefix))
            value = (
                f"{call}()"
                if call == expr
                else f"{expr}({self.ctx_expr(spec.ctx.prefix)})"
            )
            lines.append(f"    ctx.metrics[{spec.name!r}] = {value}")
        return lines

    def object_expr(
        self, obj: Any, what: str, base: str, *, call: bool
    ) -> tuple[str, list[str]]:
        """An expression for an object a kit created, and the lines that create it.

        The same object always gets the same local variable, so a behavior
        that is also a resource stays one object.  A placeholder is called
        right away when *call*, else stored (it raises when used).
        """
        known = self.objects.get(id(obj))
        if known is not None:
            return known, []
        expr = self.out.plain_expr(obj)
        if expr is not None:
            return expr, []
        construction = self.out.construction(obj)
        if construction is None:
            name = self.out.placeholder(obj, what, base)
            if not call:
                return name, []
            construction = f"{name}()"
        local = self.out.unique(_snake(type(obj).__name__))
        self.objects[id(obj)] = local
        self.out.shared[id(obj)] = local
        return local, [f"    {local} = {construction}"]

    def object_lines(self) -> list[str]:
        """Behaviors, the space and resources that kits created, written out."""
        state = self.state
        if not (state.behaviors or state.space is not None or state.resources):
            return []
        lines = [
            "",
            "    # objects kits created in their hooks (behaviors, space, resources)",
        ]
        entries = []
        for prefix, behavior in state.behaviors:
            what = f"environment behavior {type(behavior).__name__} ({prefix or 'scenario'})"
            expr, made = self.object_expr(behavior, what, "behavior", call=True)
            lines += made
            entries.append(f"({prefix!r}, {expr})")
        if state.space is not None:
            what = f"space set by {state.space_owner!r}"
            expr, made = self.object_expr(state.space, what, "space", call=True)
            lines += made
            lines.append(f"    ctx.space = {expr}")
        if entries:
            compose = self.out.imports.mango("compose_behavior")
            lines.append(
                f"    ctx.behavior = {compose}([{', '.join(entries)}], seeds)"
                "  # binds behavior rngs"
            )
        for name, (obj, owner) in state.resources.items():
            what = f"resource {name!r} (provided by {owner!r})"
            expr, made = self.object_expr(obj, what, "resource", call=False)
            lines += made
            lines.append(f"    ctx.provide({name!r}, {expr})")
        return lines

    def group_lines(self) -> list[str]:
        out = self.out
        lines: list[str] = []
        positioned = []
        for spec in self.state.groups.values():
            base = spec.name.replace(".", "_")
            ctx = self.ctx_expr(spec.ctx.prefix)
            factory = out.callable_expr(
                spec.factory, f"factory of group {spec.name!r}", f"{base}_factory"
            )
            install = (
                None
                if spec.install is None
                else out.callable_expr(
                    spec.install, f"install of group {spec.name!r}", f"{base}_install"
                )
            )
            position = (
                None
                if spec.position is None
                else out.callable_expr(
                    spec.position,
                    f"position of group {spec.name!r}",
                    f"{base}_position",
                )
            )
            lines += [
                "",
                f"    # group {spec.name!r}: {spec.count} "
                f"entit{'y' if spec.count == 1 else 'ies'}, factory {_short(spec.factory)}",
            ]
            if self.flags["population"]:  # ctx.spawn() builds later entities with these
                naming = spec.naming
                if callable(naming):
                    naming = out.callable_expr(
                        naming, f"naming of group {spec.name!r}", f"{base}_naming"
                    )
                elif naming is not None:
                    naming = repr(naming)
                lines.append(
                    f"    {ctx}.new_group({spec.name!r}, {factory}, install={install}, "
                    f"position={position}, naming={naming})"
                )
            else:
                lines.append(f"    ctx.new_group({spec.name!r})")
            if not spec.names:
                continue
            names = _names_expr(spec.names, spec.name)
            lines.append(f"    for index, name in enumerate({names}):")
            lines.append(
                f"        e = ScriptEntity({ctx}, name, {spec.name!r}, index, {spec.count})"
            )
            if install is not None:
                lines.append(f"        e.attrs = dict({install}(e) or {{}})")
            lines.append(f"        register(world, e, {factory}(e))")
            if position is not None:
                positioned.append((spec, position))
        if positioned:
            lines += ["", "    # explicit positions (after every group is built)"]
            for spec, position in positioned:
                lines += [
                    f"    for e in ctx.entities({spec.name!r}):",
                    f"        world.environment.space.move(e.agent, {position}(e))",
                ]
        return lines

    def link_lines(self) -> list[str]:
        if not self.links:
            return []
        self.out.imports.mango("create_topology")
        lines: list[str] = []
        for name, (specs, participants, edges) in self.links.items():
            parts = ", ".join(
                f"{s.source} -> {s.target} ({s.pattern_name})" for s in specs
            )
            directed = specs[0].directed
            lines += [
                "",
                f"    # link {name!r}: {parts}; {len(edges)} "
                f"{'directed ' if directed else ''}edge{'s' * (len(edges) != 1)}",
                f"    with create_topology(tid={name!r}"
                + (", directed=True" if directed else "")
                + ") as topology:",
                f"        nodes = {_literal(participants, 16)}",
                "        node = {n: topology.add_node(ctx.entity(n).agent) for n in nodes}",
            ]
            if edges:
                lines += [
                    f"        for a, b in {_literal(edges, 20)}:",
                    "            topology.add_edge(node[a], node[b])",
                ]
            if self.flags[
                "links"
            ]:  # ctx.relink/retire and the edge rules change it later
                lines.append(f"    ctx.links[{name!r}] = (topology, node)")
            if name in self.link_comm():
                lines.append(f"    set_edge_rules(ctx, {name!r})  # its delay and loss")
        return lines

    def recorder_lines(self) -> list[str]:
        records = self.state.records
        if not records:
            return []
        every_used = any(rec.every is not None for rec in records)
        if self.lean:
            lines = [
                "",
                "    # recorders: sampled at t=0 and after every step (a sample is the state",
                "    # after the step that ended at t)",
            ]
        else:
            lines = [
                "",
                "    # recorders: sampled at t=0 and after every step (a sample is the state after",
                "    # the step that ended at t, so it belongs to that step's phase), not for",
                "    # phases with record: false",
            ]
        # per-agent recorders by name in the runtime's order (first declaration first)
        agent_recs: dict[str, list[tuple[Any, str]]] = {}
        for rec in records:
            base = f"record_{rec.name}"
            if rec.kind == "world":
                lines.append(self.world_recorder_line(rec, base, every_used))
            else:
                agent_recs.setdefault(rec.name, []).append(
                    (rec, self.agent_getter(rec, base))
                )
        calls = []
        for name, entries in agent_recs.items():
            calls.append(self.agent_recorder_call(name, entries, every_used))
        if "aggregate" in self.reductions:
            lines.append(
                "    ctx.reduced = {}  # rows of aggregated recorders: (variable, group, entity)"
            )
        series = "ctx.reduced" if "aggregate" in self.reductions else "None"
        for head, parts, tail in calls:
            lines.append(self._call_line(head.replace("@SERIES@", series), parts, tail))
        return lines

    def agent_recorder_call(
        self, name: str, entries: list[tuple[Any, str]], every_used: bool
    ) -> tuple[str, list[str], str]:
        """The call installing the per-agent recorder *name*: (head, parts, tail)."""
        first = entries[0][0]  # one aggregate, kind and categories per variable
        is_state = first.value_kind == "state"
        if len({rec.categories for rec, _ in entries}) > 1:
            from .errors import ScenarioError

            raise ScenarioError(
                f"Recorder {name!r} declares different categories= on its groups; "
                "to_script writes one list per variable. Declare the same categories "
                "on every group (or record them under different names)."
            )
        reduced = any(
            rec.aggregate is not None or rec.window is not None for rec, _ in entries
        )
        if not reduced:
            parts = []
            for rec, expr in entries:
                every = "None" if rec.every is None else repr(rec.every)
                every_part = f", {every}" if every_used or not self.lean else ""
                parts.append(f"({rec.group!r}, {expr}{every_part})")
            tail = ", state=True" if is_state and not self.lean else ""
            return f"add_agent_recorder(world, ctx, {name!r}, ", parts, tail
        aggregate = first.aggregate
        if (
            self.lean
            and aggregate in ("sum", "mean", "min", "max")
            and first.categories is None
            and all(rec.window is None and rec.every is None for rec, _ in entries)
        ):  # a plain reduction over each group at every step
            self.reductions.update({"group", "aggregate"})
            parts = [f"({rec.group!r}, {expr})" for rec, expr in entries]
            head = f"add_group_recorder(world, ctx, @SERIES@, {name!r}, {aggregate!r}, "
            return head, parts, ""
        self.reductions.add("agent")
        if aggregate is None or isinstance(aggregate, str):
            how = repr(aggregate)
        else:
            how = self.out.callable_expr(
                aggregate,
                f"aggregate of recorder {name!r}",
                f"record_{name}_aggregate",
            )
        if aggregate is not None:
            self.reductions.add("aggregate")
        parts = [
            f"({rec.group!r}, {expr}, {rec.every!r}, {rec.window!r})"
            for rec, expr in entries
        ]
        tail = ", state=True" if is_state else ""
        if first.categories is not None:
            tail += f", categories={list(first.categories)!r}"
        head = f"add_reduced_recorder(world, ctx, @SERIES@, {name!r}, {how}, "
        return head, parts, tail

    def world_recorder_line(self, rec: Any, base: str, every_used: bool) -> str:
        """The line installing one world recorder."""
        out = self.out
        every = "None" if rec.every is None else repr(rec.every)
        expr = out.callable_expr(rec.getter, f"world recorder {rec.name!r}", base)
        fn = out.ctx_call(rec.getter, expr, self.ctx_expr(rec.ctx.prefix))
        key = "" if rec.key is None else f", key={rec.key!r}"
        state = ", state=True" if rec.value_kind == "state" else ""
        window = rec.window
        if window is not None:
            self.reductions.add("world")
            return (
                f"    add_windowed_world_recorder(world, {rec.name!r}, "
                f"{rec.entity!r}, {fn}, {every}, {window!r}{key}{state})"
            )
        every_arg = f", every={every}" if every_used or not self.lean else ""
        if self.lean:
            state = ""  # one phase: a state is recorded like a flow
        return (
            f"    add_world_recorder(world, {rec.name!r}, {rec.entity!r}, {fn}"
            f"{every_arg}{key}{state})"
        )

    @staticmethod
    def _call_line(head: str, parts: list[str], tail: str = "") -> str:
        """``    head[part, ...]tail)`` on one line, or one part per line when long."""
        line = f"    {head}[{', '.join(parts)}]{tail})"
        if len(line) <= _WIDTH:
            return line
        inner = "".join(f"        {part},\n" for part in parts)
        return f"    {head}[\n{inner}    ]{tail})"

    def agent_getter(self, rec: Any, base: str) -> str:
        """The expression of a per-agent recorder's getter (``fn(agent)``)."""
        from .context import _AgentCtxGetter

        getter = rec.getter
        if isinstance(getter, _AgentCtxGetter):  # fn(agent, ctx)
            expr = self.out.callable_expr(getter.fn, f"recorder {rec.name!r}", base)
            return f"lambda agent: {expr}(agent, {self.ctx_expr(rec.ctx.prefix)})"
        return self.out.callable_expr(getter, f"recorder {rec.name!r}", base)

    def reduction_helpers(self) -> list[str]:
        """Helper functions of reduced recorders (only when the scenario has any)."""
        if not self.reductions:
            return []
        out = self.out
        helpers = []
        if self.reductions & {"world", "agent"}:
            out.imports.mango("Window")
        if "world" in self.reductions:
            helpers.append(self.template(_WINDOWED_WORLD_RECORDER))
        if self.reductions & {"agent", "group"}:
            out.imports.mango("group_reduce")
        if "group" in self.reductions:
            helpers.append(_GROUP_RECORDER)
        if "agent" in self.reductions:
            extra = ", state=False" if self.flags["state"] else ""
            if self.flags["categories"]:
                extra += ", categories=None"
            helpers.append(self.template(_REDUCED_RECORDER).replace("__ARGS__", extra))
        if "aggregate" in self.reductions:
            helpers.append(_REDUCED_ROWS)
        return helpers

    def stop_rows(self) -> list[str]:
        out = self.out
        rows = []
        for index, condition in enumerate(self.state.stop_conditions):
            expr = out.callable_expr(
                condition.fn, f"stop_when {condition.label}", f"stop_when_{index}"
            )
            rows.append(
                f"    ({out.ctx_arg(condition.fn, expr)}, {condition.every!r}, "
                f"{condition.label!r}),"
            )
        return rows

    def loop_lines(self) -> list[str]:
        if not any(
            spec.emit is not None or spec.action is not None
            for spec in self.schedule.events
        ):  # no emits: keep the loop readable
            lines = [
                "",
                "    def apply_events(t):",
                '        """No events with an emit or an action."""',
                "",
                "    async with world:  # starts the agents (on_ready) and records t=0",
            ]
        else:
            self.out.imports.mango("ScenarioEvent")
            lines = [
                "",
                "    events = list(EVENTS)",
                "",
                "    def apply_events(t):",
                '        """Emit and run the events due at t (before the step that starts at t)."""',
                "        while events and events[0][0] <= t + tolerance(t):",
                "            _, name, payload, targets, action, stage, until = events.pop(0)",
                "            now = float(world.clock.time)",
                "            if payload is not None:",
                "                if isinstance(payload, Placeholder):",
                "                    payload()",
                "                moment = world.clock.datetime if world.clock.has_calendar else None",
                "                event = ScenarioEvent(",
                "                    name, copy.deepcopy(payload), now, moment, stage, until",
                "                )",
                "                if targets is None:",
                "                    world.environment.emit_global_event(event)",
                "                else:",
                "                    for target in targets:",
                "                        world.environment.emit_agent_event(event, target)",
                "            if action is not None:",
                "                action(ctx)",
                "",
                "    async with world:  # starts the agents (on_ready) and records t=0",
                "        apply_events(0.0)",
            ]
        if self.flags["dynamic_targets"]:
            at = lines.index("                if targets is None:")
            lines[at:at] = [
                "                if isinstance(targets, dict):  # resolved when it fires",
                "                    targets = event_targets(ctx, **targets)",
            ]
        stops = self.flags["stops"]
        retiring = self.flags["population"]
        if stops:
            at = next(
                i for i, line in enumerate(lines) if line.startswith("    async with")
            )
            lines[at:at] = [
                "    def stop_now(t):",
                '        """True once the run ends early: ctx.stop() or a stop_when condition."""',
                "        for condition, every, label in STOP_WHEN:",
                "            if ctx.stop_reason is not None:",
                "                break",
                "            if due(t, every):",
                "                value = condition(ctx)",
                "                if value:",
                '                    ctx.stop(value if isinstance(value, str) else f"stop_when {label}")',
                "        return ctx.stop_reason is not None",
                "",
            ]
        early = (
            [
                "            if ctx.stop_reason is not None:",
                "                break  # an event action stopped the run (ctx.stop)",
            ]
            if stops
            else []
        )
        if self.time.is_event_driven:
            lines += [
                "        while world.clock.time < HORIZON_S - tolerance(HORIZON_S):",
                "            t = world.clock.time",
                "            apply_events(t)",
                *early,
                "            remaining = next_boundary(t) - t",
                "            # to the next message or task wakeup, never past a boundary",
                "            if await world.step(max_advance_time_s=remaining) is None:",
                "                await world.step(step_size_s=remaining)  # nothing before it",
            ]
        else:
            lines += [
                "        for k in range(1, N_STEPS + 1):",
                "            t = world.clock.time",
                "            apply_events(t)",
                *early,
                "            target = HORIZON_S if k == N_STEPS else k * STEP_S",
                "            await world.step(step_size_s=target - t)",
            ]
        if retiring:
            lines.append(
                "            await ctx.finish_retiring()  # on_stop of agents retired in the step"
            )
        if stops:
            lines += [
                "            if stop_now(world.clock.time):",
                "                break  # the run ends early (stop_when, ctx.stop)",
            ]
        if retiring:
            lines.append("        await ctx.finish_retiring()")
        return lines


def _short(fn: Any) -> str:
    if isinstance(fn, operator.attrgetter):
        return repr(fn)
    name = getattr(fn, "__qualname__", None) or type(fn).__qualname__
    return name


def _simple_constant(settings: Mapping) -> bool:
    kind = settings.get("kind")
    if kind == "ideal":
        return True
    return kind == "simple" and isinstance(settings.get("delay_s"), int | float)


def _has_sample_table(params: Any) -> bool:
    """True when *params* hold a ``FromColumn`` in sample mode (``e.draw`` reads rows)."""
    from pydantic import BaseModel

    from .distributions import FromColumn

    pending, seen = [params], set()
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, FromColumn):
            if value.mode == "sample":
                return True
        elif isinstance(value, BaseModel):
            pending += [getattr(value, name, None) for name in type(value).model_fields]
        elif isinstance(value, Mapping):
            pending += list(value.values())
        elif isinstance(value, list | tuple):
            pending += list(value)
    return False


# --------------------------------------------------------------------------- script text

#: What a ctx/e attribute the script left out raises (full form: declarations only)
_MISSING_FULL = """(
            f"{type(self).__name__} has no {name!r}: to-script writes out what the "
            "scenario's declarations did (ctx.group, ctx.link, ctx.record, ... do not "
            "exist while the script runs) and includes ctx.spawn/retire/relink/"
            "link_pairs/stop/log/set_metric/phase/warn only when the scenario's "
            "modules call them (calls in modules outside the project are not seen)"
        )"""
_MISSING_LEAN = """(
            f"{type(self).__name__} has no {name!r}: the lean script holds only what "
            "to-script found in the scenario's modules; write the full form "
            "(mango-scenario to-script --full, or scenario.to_script(lean=False))"
        )"""

_HELPERS = '''\
def tolerance(t):
    """Relative tolerance for comparing offsets (absolute 1e-9 near zero)."""
    return 1e-9 * max(1.0, abs(t))


def sample_phase(t):
    """The phase (start, name, recorded) of a sample at t: the one active during the
    step that ended at t (the first phase for t=0)."""
    active = PHASES[0]
    for phase in PHASES:
        if phase[0] < t - tolerance(t):
            active = phase
    return active
#[if phase_at]


def phase_at(t):
    """The phase (start, name, recorded) active from t on: a state recorded at t holds
    in it, and ctx.log rows belong to it."""
    active = PHASES[0]
    for phase in PHASES:
        if phase[0] <= t + tolerance(t):
            active = phase
    return active
#[end]
#[if state]


def recorded(t, state):
    """Whether a sample at t is recorded: a flow by the phase of the step that ended
    at t, a state (kind='state') by the phase it holds in."""
    return (phase_at(t) if state else sample_phase(t))[2]
#[end]


def due(t, every):
    """True when a recorder with every= samples at offset t."""
    if every is None:
        return True
    return abs(t - round(t / every) * every) <= tolerance(t)


#[if state]
def add_world_recorder(world, name, entity, fn, every=None, key=None, state=False):
#[else]
def add_world_recorder(world, name, entity, fn, every=None, key=None):
#[end]
    """Record fn() after every step (world rows owned by entity)."""
    recording = world.new_world_recording(key or name, entity=entity, variable=name)

    def collect(w):
        t = w.clock.time
#[if state]
        if recorded(t, state) and due(t, every):
#[else]
        if sample_phase(t)[2] and due(t, every):
#[end]
            recording.timeseries.append(fn())
            recording.time.append(t)

    world.add_data_collector(collect)


#[if state]
def add_agent_recorder(world, ctx, name, parts, state=False):
#[else]
def add_agent_recorder(world, ctx, name, parts):
#[end]
    """Record getter(agent) for every agent of each (group, getter, every) part."""
    recording = world.new_agents_recording(name)

    def collect(w):
        t = w.clock.time
#[if state]
        if not recorded(t, state):
#[else]
        if not sample_phase(t)[2]:
#[end]
            return
        sampled = False
        for group, getter, every in parts:
            if not due(t, every):
                continue
            for e in ctx.entities(group):
                recording.timeseries.setdefault(e.name, []).append(getter(e.agent))
                recording.agent_time.setdefault(e.name, []).append(t)
            sampled = True
        if sampled:
            recording.time.append(t)

    world.add_data_collector(collect)


def register(world, e, agent):
    """Register the agent under the entity name (its aid) with the install attributes."""
    world.register(agent, suggested_aid=e.name, **e.attrs)
    if agent.description.category == "agent":
        agent.update_description(category=e.group)
    e.agent = agent
    e.ctx.add_entity(e)
#[if population]


def uninstall(environment, agent):
    """Remove a retired agent from the environment and its space (like the scenario run)."""
    remove = getattr(environment, "uninstall", None)
    if callable(remove):
        remove(agent)
        return
    index = getattr(environment, "_id_to_agent", None)
    if isinstance(index, dict):
        for key in [key for key, value in index.items() if value is agent]:
            del index[key]
    space = getattr(environment, "space", None)
    remove = getattr(space, "uninstall", None)
    if callable(remove):
        remove(agent)
        return
    for attribute in ("_positions", "_agents"):
        mapping = getattr(space, attribute, None)
        if isinstance(mapping, dict):
            mapping.pop(agent.aid, None)
#[end]


def write_csv(path, rows):
    """Write dict rows to a CSV file (columns in order of first appearance)."""
    import csv

    fields = list(dict.fromkeys(key for row in rows for key in row)) or ["time"]
    with open(path, "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)'''

_PLACEHOLDER_CLASS = '''\
class Placeholder:
    """Stands for code the generator could not import; calling it raises."""

    def __init__(self, what):
        self.what = what

    def __call__(self, *args, **kwargs):
        raise NotImplementedError(
            f"PLACEHOLDER: {self.what} could not be imported into this script; "
            "replace the placeholder with real code."
        )'''

_CONTEXT_CLASSES = '''\
class ScriptContext:
    """What factories, hooks, recorders and agents get as ctx (the scenario's BuildContext)."""

    def __init__(self, params, seeds, prefix="", shared=None):
        self.params = params
        self.seeds = seeds
        self.prefix = prefix
        self.p = getattr(params, prefix, None) if prefix else params
        self.time = params.time
        # the state all contexts of the run share (a kit's ctx is a scoped copy)
        self._shared = shared if shared is not None else {
            "world": None, "comm": params.comm, "behavior": None, "space": None,
            "groups": {}, "entities": {}, "profiles": {}, "resources": {},
#[if population]
            "specs": {}, "names": {}, "retiring": [],
#[end]
#[if links]
            "links": {},  # link name -> (topology, entity name -> node)
#[end]
#[if relink]
            "relinked": {},  # link name -> recomputations with ctx.relink(name)
#[end]
#[if layered]
            "edge_rules": {},  # (sender, receiver) -> EdgeRule of links with delay/loss
#[end]
#[if logs]
            "logs": {},  # event logs: name -> [(time, phase, fields), ...]
#[end]
#[if metrics]
            "metrics": {},
#[end]
#[if scratch]
            "scratch": {},  # ctx.state dicts by kit prefix
#[end]
#[if stops]
            "stop_reason": None,
#[end]
#[if tables]
            "table_rows": {},  # sample-mode table rows: (entity, table, size) -> row(s)
#[end]
        }

    def scoped(self, prefix):
        """The context of a kit (ctx.p is the kit's params)."""
        return ScriptContext(self.params, self.seeds, prefix, self._shared)

    world = property(lambda self: self._shared["world"],
                     lambda self, value: self._shared.__setitem__("world", value))
    comm = property(lambda self: self._shared["comm"],
                    lambda self, value: self._shared.__setitem__("comm", value))
    behavior = property(lambda self: self._shared["behavior"],
                        lambda self, value: self._shared.__setitem__("behavior", value))
    space = property(  # the kit's space, else the environment's (like ctx.space)
        lambda self: self._shared["space"]
        or getattr(getattr(self.world, "environment", None), "space", None),
        lambda self, value: self._shared.__setitem__("space", value),
    )
#[if links]
    links = property(lambda self: self._shared["links"])
#[end]
#[if layered]
    edge_rules = property(lambda self: self._shared["edge_rules"])
#[end]
#[if logs]
    logs = property(lambda self: self._shared["logs"])
#[end]
#[if metrics]
    metrics = property(lambda self: self._shared["metrics"])
#[end]
#[if stops]
    stop_reason = property(lambda self: self._shared["stop_reason"])
#[end]

    def rng(self, *names):
        return self.seeds.rng(self.prefix, *names) if self.prefix else self.seeds.rng(*names)

    def _group(self, group):
        """The full name of group (a kit's own group first, like the scenario)."""
        name = str(group)
        local = f"{self.prefix}.{name}" if self.prefix and isinstance(group, str) else None
        return local if local in self._shared["groups"] else name

#[if population]
    def new_group(self, group, factory, install=None, position=None, naming=None):
        """Start a group; ctx.spawn() builds its later entities with these functions."""
        self._shared["groups"][group] = []
        self._shared["names"][group] = []  # every name the group used (never reused)
        self._shared["specs"][group] = (self, factory, install, position, naming)
#[else]
    def new_group(self, group):
        self._shared["groups"][group] = []
#[end]

    def add_entity(self, e):
        self._shared["groups"][e.group].append(e)
        self._shared["entities"][e.name] = e
#[if population]
        self._shared["names"][e.group].append(e.name)
#[end]

    def entities(self, group):
        return list(self._shared["groups"][self._group(group)])

    def agents(self, group):
        return [e.agent for e in self.entities(group)]

    def entity(self, name):
        return self._shared["entities"][name]

    def provide(self, name, obj):
        self._shared["resources"][name] = obj

    def resource(self, name):
        # like ctx.resource: the kit's own, the exact name, the one kit's
        resources = self._shared["resources"]
        key = f"{self.prefix}.{name}" if self.prefix else name
        if key not in resources:
            key = name
        if key not in resources:
            matches = [k for k in resources if k.endswith("." + name)]
            key = matches[0] if len(matches) == 1 else name
        found = resources[key]
        return found() if isinstance(found, Placeholder) else found

    def profile(self, name, key=None):
        return self._profile(name, key, None)

    def _profile(self, name, key, entity):
        from mango.scenario import load_profile

        spec = self.params.profiles[name]
        noisy = entity is not None and spec.noise > 0
        cache_key = (name, key, entity if noisy else None)
        found = self._shared["profiles"].get(cache_key)
        if found is None:
            rng = None
            if noisy:  # the entity's profile noise stream, independent of e.rng
                rng = self.seeds.child("agent", entity, "profile", name).generator()
            found = load_profile(spec, name=name, key=key,
                                 scenario_start=self.time.start, rng=rng)
            self._shared["profiles"][cache_key] = found
        return found
#[if phase]

    @property
    def phase(self):
        """The phase active now (like ctx.phase)."""
        return phase_at(self.world.clock.time)[1]

    @property
    def recording(self):
        """Whether a sample now is recorded (like ctx.recording)."""
        return sample_phase(self.world.clock.time)[2]
#[end]
#[if logs]

    def log(self, name, /, **fields):
        """Append a row to event log name (like ctx.log; dropped in record: false phases)."""
        t = float(self.world.clock.time)
        _, phase, recorded_now = phase_at(t)
        if recorded_now:
            key = f"{self.prefix}.{name}" if self.prefix else name
            self._shared["logs"].setdefault(key, []).append((t, phase, fields))
#[end]
#[if metrics]

    def set_metric(self, name, value):
        """Set a run metric (like ctx.set_metric; the last call wins)."""
        self._shared["metrics"][f"{self.prefix}.{name}" if self.prefix else name] = value

    def get_metric(self, name, default=None):
        """A run metric set so far (like ctx.get_metric)."""
        return self._shared["metrics"].get(
            f"{self.prefix}.{name}" if self.prefix else name, default
        )
#[end]
#[if scratch]

    @property
    def state(self):
        """This run's own bookkeeping dict (like ctx.state)."""
        return self._shared["scratch"].setdefault(self.prefix, {})
#[end]
#[if stops]

    def stop(self, reason="stopped"):
        """End the run after the current step (like ctx.stop; the first reason stays)."""
        if self._shared["stop_reason"] is None:
            self._shared["stop_reason"] = reason
#[end]
#[if warn]

    def warn(self, message):
        """Issue a run warning once (like ctx.warn)."""
        issued = self._shared.setdefault("warnings", [])
        if message not in issued:
            issued.append(message)
            warnings.warn(message, UserWarning, stacklevel=2)
#[end]
#[if population]

    def spawn(self, group, *, attrs=None):
        """Add the next entity of group while the run steps (like ctx.spawn)."""
        group = self._group(group)
        ctx, factory, install, position, naming = self._shared["specs"][group]
        index = len(self._shared["names"][group])
        if naming is None:
            name = f"{group}_{index}"
        elif isinstance(naming, str):
            name = naming.format(group=group, index=index)
        else:
            name = naming(ScriptEntity(ctx, None, group, index, index + 1))
        e = ScriptEntity(ctx, name, group, index, index + 1)
        if install is not None:
            e.attrs = dict(install(e) or {})
        e.attrs.update(attrs or {})
        register(self.world, e, factory(e))
        if position is not None:
            self.world.environment.space.move(e.agent, position(e))
        return e

    def retire(self, entity):
        """Remove an entity from the run (like ctx.retire): its group, links and agent."""
        e = self.entity(self._name(entity))
        if not e.alive:
            raise RuntimeError(f"ctx.retire(): entity {e.name!r} is already retired.")
        e.alive = False
        self._shared["groups"][e.group].remove(e)
#[if links]
        for link, (topology, node) in self._shared["links"].items():
            if e.name not in node:
                continue
            graph = topology.graph
            here = node[e.name]
            if graph.is_directed():
                incident = [*graph.in_edges(here), *graph.out_edges(here)]
            else:
                incident = list(graph.edges(here))
            if incident:
                graph.remove_edges_from(incident)
                topology.inject()
#[if layered]
                set_edge_rules(self, link)
#[end]
#[end]
        world = self.world
        world.deregister(e.agent.aid)
        uninstall(world.environment, e.agent)
        task = asyncio.get_running_loop().create_task(e.agent.shutdown())
        self._shared["retiring"].append(task)

    async def finish_retiring(self):
        """Await the shutdown (on_stop) of the agents retired since the last call."""
        tasks, self._shared["retiring"] = self._shared["retiring"], []
        await asyncio.gather(*tasks)
#[end]
#[if relink]

    def relink(self, name, pairs=None):
        """Replace the edges of link name while the run steps (like ctx.relink)."""
        topology, node = self._shared["links"][name]
        if pairs is None:  # the link's declared patterns on the current members
            pairs = self._recomputed_pairs(name)
        entities = self._shared["entities"]
        edges = []
        for pair in pairs:
            a, b = (self._name(item) for item in pair)
            for end in (a, b):
                if not entities[end].alive:
                    raise RuntimeError(f"ctx.relink({name!r}): entity {end!r} is retired.")
            if a != b:
                edges.append((a, b))
        graph = topology.graph
        graph.remove_edges_from(list(graph.edges))
        for a, b in edges:
            for end in (a, b):
                if end not in node:
                    node[end] = topology.add_node(entities[end].agent)
            topology.add_edge(node[a], node[b])
        topology.inject()
#[if layered]
        set_edge_rules(self, name)
#[end]
        return graph.number_of_edges()

    def link_pairs(self, name):
        """The current edges of link name as (source, target) entity names."""
        topology, node = self._shared["links"][name]
        names = {index: entity for entity, index in node.items()}
        return [(names[a], names[b]) for a, b in topology.graph.edges]

    def _recomputed_pairs(self, name):
        """The declared patterns of link name on the current members of its groups."""
        count = self._shared["relinked"][name] = self._shared["relinked"].get(name, 0) + 1
        rng = self.seeds.rng("link", name, "relink", count)
        groups = self._shared["groups"]
        pairs = []
        for source, target, pattern, where in LINK_PATTERNS[name]:
            src = groups[source]
            dst = src if target == source else groups[target]
            for a, b in pattern(src, dst, rng):
                if a.name != b.name and (where is None or where(a, b)):
                    pairs.append((a.name, b.name))
        return pairs
#[end]
#[if names]

    @staticmethod
    def _name(item):
        """The entity name of an entity, an agent or a name."""
        if isinstance(item, str):
            return item
        return item.name if isinstance(item, ScriptEntity) else item.aid
#[end]

    def __getattr__(self, name):
        raise AttributeError__MISSING__


class ScriptEntity:
    """What group factories get as e (the scenario's EntityContext)."""

    alive = True  # False once retired

    def __init__(self, ctx, name, group, index, count):
        self.ctx = ctx
        self.name = name
        self.group = group
        self.index = index
        self.count = count
        self.attrs = {}
        self.agent = None
        self.p = ctx.p
        self.params = ctx.params
        self.time = ctx.time

    @property
    def world(self):
        return self.ctx.world

    @property
    def rng(self):
        """The entity's stream; after registration it is the agent's agent.rng."""
        return self.ctx.seeds.rng("agent", self.name)

    def rng_for(self, name):
        return self.ctx.seeds.rng("agent", self.name, name)

    def draw(self, dist, size=None):
#[if tables]
        if isinstance(dist, FromColumn) and dist.mode == "sample":
            return dist.values_at(self.table_rows(dist, size))  # the entity's row
#[end]
        import inspect

        try:
            takes_index = "index" in inspect.signature(dist.draw).parameters
        except (TypeError, ValueError):
            takes_index = False
        if takes_index:
            return dist.draw(self.rng, size, index=self.index)
        return dist.draw(self.rng, size)
#[if tables]

    def row(self, dist):
        """The row of a per-entity table this entity reads (like e.row)."""
        if dist.mode == "sample":
            return int(self.table_rows(dist, None))
        return self.index % len(dist.column_values())

    def table_rows(self, dist, size):
        """The sample-mode row(s) of the entity for dist's table (drawn once per table)."""
        rows = self.ctx._shared["table_rows"]
        table = os.path.normpath(dist.path)
        shape = size if size is None or isinstance(size, tuple) else (size,)
        key = (self.name, table, shape)
        if key not in rows:
            n = len(dist.column_values())
            stream = self.ctx.seeds.rng("agent", self.name, "table", name_key(table))
            rows[key] = int(stream.integers(n)) if shape is None else stream.integers(0, n, shape)
        return rows[key]
#[end]

    def resource(self, name):
        return self.ctx.resource(name)

    def profile(self, name, key=None):
        return self.ctx._profile(name, self.index if key is None else key, self.name)

    def __getattr__(self, name):
        raise AttributeError__MISSING__'''

_LEAN_CONTEXT = """\
class ScriptContext:
    \"\"\"What factories, hooks and recorders get as ctx (the scenario's BuildContext).\"\"\"

    def __init__(self, params, seeds):
        self.params = self.p = params
        self.seeds = seeds
        self.time = params.time
#[if comm]
        self.comm = params.comm
#[end]
        self.world = None
        self.groups = {}  # group -> its entities, in index order
        self.by_name = {}  # entity name -> entity
#[if logs]
        self.logs = {}  # event logs (ctx.log): name -> [(time, phase, fields), ...]
#[end]
#[if metrics]
        self.metrics = {}  # run metrics (ctx.set_metric, scenario.metric)
#[end]
#[if scratch]
        self.state = {}  # the run's own bookkeeping (like ctx.state)
#[end]
#[if tables]
        self.table_rows = {}  # sample-mode table rows: (entity, table, size) -> row(s)
#[end]
#[if profiles]
        self.profiles = {}  # loaded profiles: (name, key, entity) -> profile
#[end]

    def new_group(self, group):
        self.groups[group] = []

    def add_entity(self, e):
        self.groups[e.group].append(e)
        self.by_name[e.name] = e

    def entities(self, group):
        return list(self.groups[str(group)])

    def agents(self, group):
        return [e.agent for e in self.groups[str(group)]]

    def entity(self, name):
        return self.by_name[name]
#[if rng]

    def rng(self, *names):
        return self.seeds.rng(*names)
#[end]
#[if space]

    @property
    def space(self):
        \"\"\"The environment's space (like ctx.space).\"\"\"
        return getattr(getattr(self.world, "environment", None), "space", None)
#[end]
#[if phase]

    phase = __PHASE__  # the scenario's only phase (like ctx.phase)
    recording = True  # (like ctx.recording)
#[end]
#[if logs]

    def log(self, name, /, **fields):
        \"\"\"Append a row to event log name (like ctx.log).\"\"\"
        self.logs.setdefault(name, []).append((float(self.world.clock.time), __PHASE__, fields))
#[end]
#[if metrics]

    def set_metric(self, name, value):
        \"\"\"Set a run metric (like ctx.set_metric; the last call wins).\"\"\"
        self.metrics[name] = value

    def get_metric(self, name, default=None):
        \"\"\"A run metric set so far (like ctx.get_metric).\"\"\"
        return self.metrics.get(name, default)
#[end]
#[if warn]

    def warn(self, message):
        \"\"\"Issue a run warning once (like ctx.warn).\"\"\"
        issued = self.__dict__.setdefault("warnings", [])
        if message not in issued:
            issued.append(message)
            warnings.warn(message, UserWarning, stacklevel=2)
#[end]
#[if profiles]

    def profile(self, name, key=None, entity=None):
        \"\"\"The profile *name*; with an entity and noise, the entity's noisy copy.\"\"\"
        spec = self.params.profiles[name]
        noisy = entity is not None and spec.noise > 0
        cache_key = (name, key, entity if noisy else None)
        if cache_key not in self.profiles:
            rng = None
            if noisy:  # the entity's profile noise stream, independent of e.rng
                rng = self.seeds.child("agent", entity, "profile", name).generator()
            self.profiles[cache_key] = load_profile(
                spec, name=name, key=key, scenario_start=self.time.start, rng=rng
            )
        return self.profiles[cache_key]
#[end]

    def __getattr__(self, name):
        raise AttributeError__MISSING__"""

_LEAN_ENTITY = """\
class ScriptEntity:
    \"\"\"What group factories get as e (the scenario's EntityContext).\"\"\"

    def __init__(self, ctx, name, group, index, count):
        self.ctx, self.name, self.group, self.index, self.count = (
            ctx, name, group, index, count
        )
        self.p, self.params, self.time = ctx.p, ctx.params, ctx.time
        self.attrs = {}  # from the group's install function
        self.agent = None
#[if world]

    @property
    def world(self):
        return self.ctx.world
#[end]

    @property
    def rng(self):
        \"\"\"The entity's stream; after registration it is the agent's agent.rng.\"\"\"
        return self.ctx.seeds.rng("agent", self.name)
#[if rng_for]

    def rng_for(self, name):
        return self.ctx.seeds.rng("agent", self.name, name)
#[end]
#[if draw]

    def draw(self, dist, size=None):
        \"\"\"A value of a distribution parameter, from the entity's stream.\"\"\"
#[if tables]
        if isinstance(dist, FromColumn) and dist.mode == "sample":
            return dist.values_at(self.table_rows(dist, size))  # the entity's row
#[end]
        if "index" in inspect.signature(dist.draw).parameters:  # e.g. FromColumn
            return dist.draw(self.rng, size, index=self.index)
        return dist.draw(self.rng, size)
#[end]
#[if tables]

    def row(self, dist):
        \"\"\"The row of a per-entity table this entity reads (like e.row).\"\"\"
        if dist.mode == "sample":
            return int(self.table_rows(dist, None))
        return self.index % len(dist.column_values())

    def table_rows(self, dist, size):
        \"\"\"The sample-mode row(s) of the entity for dist's table (drawn once per table).\"\"\"
        table = os.path.normpath(dist.path)
        shape = size if size is None or isinstance(size, tuple) else (size,)
        key = (self.name, table, shape)
        if key not in self.ctx.table_rows:
            n = len(dist.column_values())
            stream = self.ctx.seeds.rng("agent", self.name, "table", name_key(table))
            self.ctx.table_rows[key] = (
                int(stream.integers(n)) if shape is None else stream.integers(0, n, shape)
            )
        return self.ctx.table_rows[key]
#[end]
#[if profiles]

    def profile(self, name, key=None):
        return self.ctx.profile(name, self.index if key is None else key, self.name)
#[end]

    def __getattr__(self, name):
        raise AttributeError__MISSING__"""

_LEAN_REGISTER = """\
def register(world, e, agent):
    \"\"\"Register the agent under the entity name (its aid) with the install attributes.\"\"\"
    world.register(agent, suggested_aid=e.name, **e.attrs)
    if agent.description.category == "agent":
        agent.update_description(category=e.group)
    e.agent = agent
    e.ctx.add_entity(e)"""

_LEAN_DUE = """\
def due(t, every):
    \"\"\"True when a recorder with every= samples at offset t.\"\"\"
    if every is None:
        return True
    return abs(t - round(t / every) * every) <= 1e-9 * max(1.0, abs(t))"""

_LEAN_WORLD_RECORDER = """\
def add_world_recorder(world, name, entity, fn, key=None):
    \"\"\"Record fn() at t=0 and after every step (world rows owned by entity).\"\"\"
    recording = world.new_world_recording(key or name, entity=entity, variable=name)

    def collect(w):
        recording.timeseries.append(fn())
        recording.time.append(w.clock.time)

    world.add_data_collector(collect)"""

_LEAN_WORLD_RECORDER_EVERY = """\
def add_world_recorder(world, name, entity, fn, every=None, key=None):
    \"\"\"Record fn() at t=0 and after every step, or every *every* seconds.\"\"\"
    recording = world.new_world_recording(key or name, entity=entity, variable=name)

    def collect(w):
        if due(w.clock.time, every):
            recording.timeseries.append(fn())
            recording.time.append(w.clock.time)

    world.add_data_collector(collect)"""

_LEAN_AGENT_RECORDER = """\
def add_agent_recorder(world, ctx, name, parts):
    \"\"\"Record getter(agent) for every agent of each (group, getter) part.\"\"\"
    recording = world.new_agents_recording(name)

    def collect(w):
        t = w.clock.time
        for group, getter in parts:
            for e in ctx.entities(group):
                recording.timeseries.setdefault(e.name, []).append(getter(e.agent))
                recording.agent_time.setdefault(e.name, []).append(t)
        recording.time.append(t)

    world.add_data_collector(collect)"""

_LEAN_AGENT_RECORDER_EVERY = """\
def add_agent_recorder(world, ctx, name, parts):
    \"\"\"Record getter(agent) for every agent of each (group, getter, every) part.\"\"\"
    recording = world.new_agents_recording(name)

    def collect(w):
        t = w.clock.time
        sampled = False
        for group, getter, every in parts:
            if not due(t, every):
                continue
            for e in ctx.entities(group):
                recording.timeseries.setdefault(e.name, []).append(getter(e.agent))
                recording.agent_time.setdefault(e.name, []).append(t)
            sampled = True
        if sampled:
            recording.time.append(t)

    world.add_data_collector(collect)"""

_GROUP_RECORDER = """\
def add_group_recorder(world, ctx, series, name, aggregate, parts):
    \"\"\"Record aggregate ("sum", "mean", "min" or "max") of getter(agent) over the
    members of each (group, getter) part at t=0 and after every step: one row per
    group (entity = the group name).\"\"\"

    def collect(w):
        for group, getter in parts:
            members = ctx.entities(group)
            values = [getter(e.agent) for e in members]
            value = group_reduce(aggregate, values, [e.name for e in members], name)
            times, column = series.setdefault((name, group, group), ([], []))
            times.append(w.clock.time)
            column.append(value)

    world.add_data_collector(collect)"""

_WINDOWED_WORLD_RECORDER = """\
#[if state]
def add_windowed_world_recorder(world, name, entity, fn, every, window, key=None, state=False):
#[else]
def add_windowed_world_recorder(world, name, entity, fn, every, window, key=None):
#[end]
    \"\"\"Record fn() reduced over each window (t - every, t], like record_world(window=).

    fn() is sampled at t=0 and after every step; at each multiple of every the
    window's "max", "min", "mean" (weighted by the step lengths), "sum" or "last"
    is written (the row at t=0 is the initial value).
#[if state]
    For a state (state=True) "mean", "min" and "max" weight each level with the
    time it held, and "last" is the value at t.
#[end]
    \"\"\"
    recording = world.new_world_recording(key or name, entity=entity, variable=name)
#[if state]
    holds = state and window in ("mean", "min", "max")
    reducer = (
        None  # a state's "last": its value at t
        if state and window == "last"
        else Window(window, f"record_world({name!r})", hold=holds)
    )
#[else]
    reducer = Window(window, f"record_world({name!r})")
#[end]
    last = []  # the previous sample time

    def collect(w):
        t = w.clock.time
#[if hold]
        first = not last
#[end]
        weight = t - last[0] if last else 0.0  # the interval this sample stands for
        last[:] = [t]
#[if hold]
        if holds:  # a level holds until the next sample
#[if phases]
            on = phase_at(t)[2]
#[else]
            on = True
#[end]
            value = fn() if on else None
            reducer.add(weight, {None: value} if on else None)
            if not due(t, every):
                return
            if not reducer.empty:
                value = reducer.emit()[None]
            elif not (first and on):  # nothing recorded yet
                return
            recording.timeseries.append(value)
            recording.time.append(t)
            return
#[end]
#[if phases]
#[if state]
        if not recorded(t, state):
#[else]
        if not sample_phase(t)[2]:
#[end]
            return
#[end]
#[if state]
        if reducer is None:
            if due(t, every):
                recording.timeseries.append(fn())
                recording.time.append(t)
            return
#[end]
        reducer.add(weight, {None: fn()})
        if due(t, every):
            recording.timeseries.append(reducer.emit()[None])
            recording.time.append(t)

    world.add_data_collector(collect)"""

_REDUCED_RECORDER = """\
def add_reduced_recorder(world, ctx, series, name, aggregate, parts__ARGS__):
    \"\"\"Record a variable like scenario.record(..., aggregate=, window=).

    parts are (group, getter, every, window) tuples.  aggregate reduces the values
    of a group at each sample to one row per group ("sum", "mean", "min", "max" or
    fn(values)) or per category ("count": entity = the category, categories that
    appear later counted 0 before); window reduces the samples in (t - every, t].
#[if categories]
    categories (of "count") are written from the first sample on, 0 while absent.
#[end]
#[if state]
    For a state (state=True) "mean", "min" and "max" windows weight each level
    with the time it held, and "last" is the value at t.
#[end]
    Aggregated rows go to series {(variable, group, entity): (times, values)},
    the others to a per-agent recording.
    \"\"\"
    what = f"record({name!r})"
    recording = None if aggregate is not None else world.new_agents_recording(name)
    slots = []  # per part: its window and the categories and times written so far
    for _, _, _, window in parts:
#[if state]
        holds = state and window in ("mean", "min", "max")
        if window is None or (state and window == "last"):
            reducer = None
        else:
            reducer = Window(window, what, zero_fill=aggregate == "count", hold=holds)
#[else]
        reducer = (
            None if window is None else Window(window, what, zero_fill=aggregate == "count")
        )
#[end]
        slots.append({
            "window": reducer,
#[if state]
            "holds": holds,
#[end]
#[if categories]
            "labels": dict.fromkeys(categories or ()),
#[else]
            "labels": {},
#[end]
            "emitted": [],
        })
    last = []  # the previous sample time

    def sample(group, getter, slot):
        values = {e.name: getter(e.agent) for e in ctx.entities(group)}
        if aggregate is None:
            return values
        reduced = group_reduce(aggregate, list(values.values()), list(values), what)
        if aggregate == "count":
            slot["labels"].update(dict.fromkeys(reduced))
            return {label: reduced.get(label, 0) for label in slot["labels"]}
        if hasattr(reduced, "items"):
            return {str(label): value for label, value in reduced.items()}
        return {group: reduced}

    def write(t, group, values, slot):
        \"\"\"Append the values at t; True when they went to the per-agent recording.\"\"\"
        if recording is None:
            for entity, value in values.items():
                if (name, group, entity) not in series:
                    fill = slot["emitted"] if aggregate == "count" else []
                    series[name, group, entity] = (list(fill), [0] * len(fill))
                times, column = series[name, group, entity]
                times.append(t)
                column.append(value)
            slot["emitted"].append(t)
            return False
        for entity, value in values.items():
            recording.timeseries.setdefault(entity, []).append(value)
            recording.agent_time.setdefault(entity, []).append(t)
        return True

    def collect(w):
        t = w.clock.time
#[if hold]
        first = not last
#[end]
        weight = t - last[0] if last else 0.0  # the interval this sample stands for
        last[:] = [t]
#[if phases]
#[if state]
        on = recorded(t, state)
#[else]
        on = sample_phase(t)[2]
#[end]
#[else]
#[if hold]
        on = True
#[end]
#[end]
        sampled = False
        for (group, getter, every, _), slot in zip(parts, slots):
            now = due(t, every)
            reducer = slot["window"]
#[if hold]
            if slot["holds"]:  # a level holds until the next sample
                values = sample(group, getter, slot) if on else None
                reducer.add(weight, values)
                if not now:
                    continue
                if not reducer.empty:
                    values = reducer.emit()
                elif not (first and on):  # nothing recorded yet
                    continue
                sampled |= write(t, group, values, slot)
                continue
#[end]
#[if phases]
            if not on:
                continue
#[end]
            if reducer is None and not now:
                continue
            values = sample(group, getter, slot)
            if reducer is not None:
                reducer.add(weight, values)
                if not now:
                    continue
                values = reducer.emit()
            sampled |= write(t, group, values, slot)
        if sampled:
            recording.time.append(t)

    world.add_data_collector(collect)"""

_REDUCED_ROWS = """\
def reduced_rows(world, series):
    \"\"\"The rows of aggregated recorders, after the world's recordings.\"\"\"
    clock = world.clock
    rows = []
    for (variable, group, entity), (times, values) in series.items():
        for t, value in zip(times, values):
            row = {"time": t}
            if clock.has_calendar:
                row["datetime"] = clock.to_datetime(t)
            row.update(group=group, entity=entity, variable=variable, value=value)
            rows.append(row)
    return rows"""

_LAYERED = '''\
#[if links]
def set_edge_rules(ctx, name):
    """(Re)build the delay and loss rules of link name's current edges."""
    comm = LINK_COMM.get(name)
    if comm is None:
        return
    rules = ctx.edge_rules
    for key in [key for key, rule in rules.items() if rule.link == name]:
        del rules[key]
    topology, node = ctx.links[name]
    names = {index: entity for entity, index in node.items()}
    graph = topology.graph
    fixed = isinstance(comm.delay, (int, float))
    for a, b in graph.edges:
        pairs = [(names[a], names[b])]
        if not graph.is_directed():
            pairs.append((names[b], names[a]))
        for src, dst in pairs:
            if (src, dst) in rules:
                continue  # the first link with settings for an edge wins
            rules[src, dst] = EdgeRule(
                link=name,
                loss=comm.loss,
                delay=float(comm.delay) if fixed else 0.0,
                provider=None if fixed else lambda rng, d=comm.delay: max(0.0, float(d.draw(rng))),
                rng=ctx.seeds.rng("comm-link", name, src, dst),
            )


#[end]
def layered(ctx, inner, settings):
    """inner with comm.partition and the link delay/loss on top (like the scenario run)."""
    partition = settings.get("partition")
    if not partition and not LINK_COMM:
        return inner
    entities = ctx._shared["entities"]
    groups = {
        group: [e.name for e in members] for group, members in ctx._shared["groups"].items()
    }
    attrs = {name: e.attrs for name, e in entities.items()}
    block_of = partition_blocks(partition, entities, groups, attrs)
    return LayeredCommunication(inner, block_of=block_of, edges=ctx.edge_rules)'''

_EVENT_TARGETS = '''\
def event_targets(ctx, emit_to, selector):
    """The entities an emit goes to when it fires: the named entities and the current
    members of the named groups, or the entities a selector {group, by_attr} matches."""
    entities, groups = ctx._shared["entities"], ctx._shared["groups"]
    if selector is not None:
        group = selector.get("group")
        if group is None:
            candidates = [e for e in entities.values() if e.alive]
        else:
            names = [group] if isinstance(group, str) else group
            candidates = [e for name in names for e in groups[name]]
        missing = object()
        found = []
        for e in candidates:
            for key, wanted in selector["by_attr"].items():
                value = e.attrs.get(key, missing)
                if value is missing or value not in (wanted if isinstance(wanted, list) else [wanted]):
                    break
            else:
                found.append(e.name)
        return tuple(found)
    names = []
    for item in [emit_to] if isinstance(emit_to, str) else emit_to:
        if item in entities:
            members = [item] if entities[item].alive else []
        else:
            members = [e.name for e in groups[item]]
        names += [name for name in members if name not in names]
    return tuple(names)'''

_LEAN_MAIN = """\
def main(csv_path=None):
    \"\"\"Run the simulation and return the recorded values as tidy rows.\"\"\"
    world, ctx = asyncio.run(simulate())
    groups = {name: e.group for name, e in ctx.by_name.items()}
    rows = recordings_to_records(world, groups=groups)
#[if aggregate]
    rows += reduced_rows(world, ctx.reduced)
#[end]
    for row in rows:
        row["phase"] = __PHASE__  # the scenario's only phase
    if csv_path:
        import csv

        with open(csv_path, "w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]) if rows else ["time"])
            writer.writeheader()
            writer.writerows(rows)
#[if logs]
        for name, entries in ctx.logs.items():  # event logs next to it: <csv>.<log>.csv
            with open(f"{csv_path}.{name}.csv", "w", newline="") as file:
                fields = list(dict.fromkeys(k for _, _, row in entries for k in row))
                writer = csv.DictWriter(file, fieldnames=["time", "phase", *fields])
                writer.writeheader()
                writer.writerows({"time": t, "phase": p, **row} for t, p, row in entries)
#[end]
    print(f"{len(rows)} values recorded" + (f" -> {csv_path}" if csv_path else ""))
#[if logs]
    for name, entries in ctx.logs.items():
        print(f"event log {name!r}: {len(entries)} rows")
#[end]
#[if metrics]
    if ctx.metrics:
        print("metrics:", ctx.metrics)
#[end]
    return rows


if __name__ == "__main__":
    main(*sys.argv[1:2])"""

_FOLLOW_COMMUNICATION = '''\
def follow_communication(world, ctx, communication):
    """Switch the communication simulation the moment the clock reaches a boundary.

    A simulation with on_comm_change(comm) is told the new settings; otherwise
    one is built per settings and reused when they return (state included).
    """
    current = [0, communication]
    built = {json.dumps(COMMUNICATION[0][1], sort_keys=True): communication}

    def switch(now):
        index = max(
            i for i, (start, _) in enumerate(COMMUNICATION) if start <= now + tolerance(now)
        )
        if index == current[0]:
            return
        current[0] = index
        settings = COMMUNICATION[index][1]
        ctx.comm = CommConfig.model_validate(settings)
        inner = current[1]
        if callable(getattr(inner, "on_comm_change", None)):
            inner.on_comm_change(ctx.comm)
        else:
            key = json.dumps(settings, sort_keys=True)
            if key not in built:
                built[key] = make_communication(ctx, settings)
            inner = built[key]
        current[1] = inner
        world.communication_sim = inner
#[if layered]
        world.communication_sim = layered(ctx, inner, settings)
#[end]

    world.clock.add_time_listener(switch)'''

_NEXT_BOUNDARY = '''\
def next_boundary(t):
    """The next offset an event-driven step must stop at (boundaries, recorder samples)."""
    import math

    nearest = HORIZON_S
    for boundary in BOUNDARIES:
        if boundary > t + tolerance(t):
            nearest = min(nearest, boundary)
            break
    for every in SAMPLE_PERIODS:
        nearest = min(nearest, (math.floor(t / every + 1e-9) + 1) * every)
    return nearest'''

_MAIN = '''\
def main(csv_path=None):
    """Run the simulation and return the recorded values as tidy rows."""
    world, ctx = asyncio.run(simulate())
    groups = {name: e.group for name, e in ctx._shared["entities"].items()}
    rows = recordings_to_records(world, groups=groups)
#[if aggregate]
    rows += reduced_rows(world, ctx.reduced)
#[end]
    for row in rows:
#[if state_rows]
        # a state holds from its row on, so it belongs to the phase starting at its time
        phase_of = phase_at if row["variable"] in STATE_VARIABLES else sample_phase
        row["phase"] = phase_of(row["time"])[1]
#[else]
        row["phase"] = sample_phase(row["time"])[1]
#[end]
    if csv_path:
        write_csv(csv_path, rows)
#[if logs]
        for name, entries in ctx.logs.items():  # event logs next to it: <csv>.<log>.csv
            write_csv(
                f"{csv_path}.{name}.csv",
                [{"time": t, "phase": phase, **row} for t, phase, row in entries],
            )
#[end]
    print(f"{len(rows)} values recorded" + (f" -> {csv_path}" if csv_path else ""))
#[if logs]
    for name, entries in ctx.logs.items():
        print(f"event log {name!r}: {len(entries)} rows")
#[end]
#[if metrics]
    if ctx.metrics:
        print("metrics:", ctx.metrics)
#[end]
#[if stops]
    if ctx.stop_reason is not None:
        print(f"stopped early at t={world.clock.time:g} s: {ctx.stop_reason}")
#[end]
    return rows


if __name__ == "__main__":
    main(*sys.argv[1:2])'''

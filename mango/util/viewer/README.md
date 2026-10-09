# Trace viewer: working on the page

`mango-trace` (`mango/util/trace_viewer.py`) writes one self-contained HTML page per trace:
`page.html` with `viewer.css`, the scripts in `js/`, the record schema
(`VIEWER_SCHEMA` in `mango/util/tracing.py`) and the records as JSON.

The scripts are ES modules: they import what they use, so an editor can jump to definitions and
rename across files. `trace_viewer.py` joins them into one script without a build step. It removes the
`import` lines and `export` keywords and puts each module after the modules it imports. That has three
consequences:

- **Top-level names are unique across all modules**, because the joined modules share one scope.
  The bundler stops with an error that names both modules.
- **Imports never go round in a circle.** The bundler stops with an error that names the cycle.
- Only `import { a, b } from "./x.js"` and `export` in front of a declaration are supported.

## Layout of `js/`

Imports point up this table: a module imports from rows above its own, and from modules in its own
folder.

| Where | What |
| --- | --- |
| `core/` | No browser API, tested in Node: the record index (`trace-index`), the time line (`time-axis`), who messaged whom (`message-graph`), search, the Lanes and map layouts. Each module is a function of the records. |
| `ui/dom.js`, `ui/events.js`, `ui/html.js` | Small helpers: element lookup and storage, notifications between views, markup. |
| `model.js` | Reads the records and the schema from the page and runs the core once. The rest of the page imports the results from here (`laneOf`, `EDGES`, …). |
| `state.js` | The view state, `update()` / `touch()`, and the one-shot `pending` requests. |
| `selection.js` | What the selection is linked to: its cause chain and its effects. |
| `visibility.js` | Which records the filters show. |
| `ui/elements.js`, `ui/feedback.js`, `ui/popover.js`, `ui/describe.js` | The fixed page elements, toast and tooltip, popovers, and how records and connections are named. |
| `ui/lanes/layout.js`, `ui/lanes/geometry.js` | The current Lanes layout and its size on screen, which actions read too. |
| `ui/actions.js` | Everything the user can do (select, filter, follow lanes, switch the view, …), as changes of the state. |
| `ui/table.js`, `ui/lanes/`, `ui/topo/`, `ui/panel.js`, `ui/rail.js`, `ui/filters.js`, `ui/header.js` | The views. They call actions; they do not import each other. |
| `ui/view.js`, `ui/goto.js`, `ui/buttons.js`, `ui/url.js` | What spans the views: scrolling to a record, go to time, button actions, the URL. |
| `ui/keys.js` | The keyboard shortcuts. |
| `main.js` | Calls each view's `init…()` in order, redraws after each change (`renderAll`), and opens the page in the state a link or the last visit chose. |

## How a change reaches the screen

1. An action calls `update({ sel: i })` (or `touch("width")` for causes from outside the state).
2. In the next frame `renderAll(changed)` in `main.js` redraws what depends on the changed keys, in a
   fixed order: derived data, controls, the current view, the map, the panel, the rail and the URL.

New state keys go into `state.js`. A new view gets a line in `renderAll` saying which keys it depends on.

## Conventions

- **Modules only declare things.** Listeners, initial markup and anything else that touches another
  module at load time go into the module's `init…()`, which `main.js` calls.
- **Markup is written with `` html`…` ``.** Every `${}` is escaped unless it is markup itself (another
  `` html`…` `` or `raw(…)`), so record data cannot break the page.
- **Views notify each other through `ui/events.js`** (`emit("peek", i)`, `on("map-hover", …)`) instead of
  importing each other.
- **Buttons written into markup** get `data-act="name"`. `ui/buttons.js` maps the name to its action.
- **Keyboard shortcuts** are entries of `SHORTCUTS` in `ui/keys.js`; the `?` sheet is built from them.
- **A new kind of record** gets its role in `VIEWER_SCHEMA` (Python) and a code in `ROLE_CODES` (`core/schema.js`).
  `traced_run_test.py` fails if mango writes a record name the schema does not cover.

## Checks

- `pre-commit` formats the scripts with Prettier and lints them with ESLint (`eslint.config.mjs`); it
  installs both itself.
- `pytest tests/unit_tests/util` runs:
  - the core unit tests in Node (`viewer_core_test.mjs`)
  - the page tests in Chromium (`trace_viewer_browser_test.py`). They need
    `pip install -e ".[test]"` and `python -m playwright install chromium`.

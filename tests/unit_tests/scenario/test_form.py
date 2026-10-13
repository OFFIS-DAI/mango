"""params_form: the self-contained HTML preset form."""

import json
import re
import shutil
import subprocess
from typing import Annotated, Literal

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

import yaml  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from mango.scenario import (  # noqa: E402
    Constant,
    Dist,
    Scenario,
    ScenarioParams,
    Uniform,
    load_params,
    params_schema,
)
from mango.scenario.form import params_form  # noqa: E402

from . import ev_tutorial  # noqa: E402


class Greedy(BaseModel, extra="forbid"):
    kind: Literal["greedy"] = "greedy"
    threshold: float = 0.5


class Market(BaseModel, extra="forbid"):
    kind: Literal["market"] = "market"
    price: float = 1.0


class Area(BaseModel, extra="forbid"):
    width: float = 10.0
    height: float = 5.0


class FormParams(ScenarioParams):
    n: int = Field(5, ge=0, le=100)
    share: float = 0.5
    label: str = "base"
    mode: Literal["a", "b"] = "a"
    enabled: bool = True
    weights: list[float] = [1.0, 2.0]
    tags: dict[str, int] = {}
    soc: Uniform = Uniform(0.2, 0.8)
    delay: Dist = Constant(0.0)
    algorithm: Annotated[Greedy | Market, Field(discriminator="kind")] = Greedy()
    note: str | None = None
    area: Area = Area()


form_scenario = Scenario("form_demo", params=FormParams)
form_scenario.time(horizon="2h", step="15min")


def form_data(page):
    match = re.search(
        r'<script type="application/json" id="mango-form-data">(.*?)</script>',
        page,
        re.S,
    )
    assert match, "the form data is embedded"
    return json.loads(match.group(1))


def test_form_embeds_schema_and_scenario_defaults():
    page = params_form(form_scenario)
    data = form_data(page)

    assert data["schema"] == json.loads(json.dumps(params_schema(form_scenario)))
    assert data["title"] == "form_demo parameters"
    assert data["defaults"]["time"]["horizon"] == "2h"  # scenario.time()
    assert data["defaults"]["phases"] == []  # default factories are included
    assert data["defaults"]["algorithm"] == {"kind": "greedy", "threshold": 0.5}
    assert "<title>form_demo parameters</title>" in page


def test_form_is_self_contained():
    page = params_form(ev_tutorial.scenario)

    assert "<script src" not in page
    assert "<link" not in page
    assert "http://" not in page and "https://" not in page.replace(
        "https://json-schema.org", ""
    )
    assert "@media (prefers-color-scheme: dark)" in page
    assert '<meta name="viewport"' in page


def test_form_data_cannot_close_the_script_element():
    class Tricky(ScenarioParams):
        text: str = Field("</script><script>alert(1)</script>", description="a & b")

    page = params_form(Tricky, title="<Tricky & co>")
    assert "<title>&lt;Tricky &amp; co&gt;</title>" in page
    assert page.count("</script>") == 2  # the data block and the page script
    assert form_data(page)["schema"]["properties"]["text"]["default"].startswith(
        "</script>"
    )


def test_form_targets():
    page = params_form(FormParams)
    assert form_data(page)["title"] == "FormParams parameters"
    with pytest.raises(
        TypeError, match="needs a scenario or a ScenarioParams subclass"
    ):
        params_form(object())


NODE_TEST = r"""
const Core = require(process.argv[2]);
const data = require(process.argv[3]);
const tree = Core.buildTree(data.schema);
const widgets = {};
function walk(f) {
  widgets[f.path.join(".") || "(root)"] = widgets[f.path.join(".")] || f.widget;
  for (const c of f.fields || []) walk(c);
  if (f.item) walk(f.item);
  if (f.value) walk(f.value);
  for (const v of f.variants || []) { for (const c of v.fields || []) walk(c); if (v.field) walk(v.field); }
}
walk(tree);
const find = (path) => {
  let found = null;
  const visit = (f) => { if (f.path.join(".") === path && !found) found = f; (f.fields || []).forEach(visit);
    if (f.item) visit(f.item); };
  visit(tree);
  return found;
};
const base = Core.baseline(data.schema, data.defaults);
const state = Core.clone(base);
Core.setPath(state, ["n"], 7);
Core.setPath(state, ["mode"], "b");
Core.setPath(state, ["enabled"], false);
Core.setPath(state, ["soc", "high"], 0.9);
Core.setPath(state, ["delay"], { kind: "uniform", low: 0.01, high: 0.05 });
Core.setPath(state, ["algorithm"], { kind: "market", price: 2.5 });
Core.setPath(state, ["area", "width"], 12);
Core.setPath(state, ["tags"], { a: 1 });
Core.setPath(state, ["weights"], [1, 2, 3]);
Core.setPath(state, ["note"], "a: b # c");
Core.setPath(state, ["label"], "yes");
Core.setPath(state, ["time", "step"], "30min");
Core.setPath(state, ["comm", "loss_percent"], 1e-7);
state.events.push({ name: "outage", at: "1h", until: "90min", comm: { loss_percent: 1.0 } });
const changed = Core.diff(state, base, tree);
// defaults with dict entries (e.g. profiles); the Remove button deletes the key
const withTags = Core.clone(base);
withTags.tags = { x: 3, "y z": 4 };
const removed = Core.clone(withTags);
delete removed.tags.x;
delete removed.tags["y z"];
removed.tags.w = 5;
// a new list item keeps its optional fields as null: the preview leaves them out
const withNulls = Core.clone(base);
withNulls.events.push({ name: "pulse", at: "2h", until: null, comm: null, emit: { kind: "x" },
                        emit_to: null, action: null });
const pruned = Core.prune(Core.diff(withNulls, base, tree), tree);
console.log(JSON.stringify({
  prunedYaml: Core.toYaml(pruned),
  scalarObjects: [Core.isScalarObject({ kind: "campaign", strength: 0.3, on: true, x: null }),
                  Core.isScalarObject({}), Core.isScalarObject({ k: { p: 1 } }),
                  Core.isScalarObject([1, 2]), Core.isScalarObject("text")],
  removedYaml: Core.toYaml(Core.diff(removed, withTags, tree)),
  numbers: [Core.parseNumber("3", true), Core.parseNumber("3.5", true), Core.parseNumber("3.5", false),
            Core.parseNumber(" ", true), Core.parseNumber("abc", false)].map((v) => v === undefined ? "invalid" : v),
  widgets,
  areaWidget: find("area").widget,
  nRange: [find("n").minimum, find("n").maximum],
  modeOptions: find("mode").options,
  socKinds: find("soc").variants.map((v) => v.kind),
  delayKinds: find("delay").variants.map((v) => v.kind),
  algorithmKinds: find("algorithm").variants.map((v) => v.kind),
  changedYaml: Core.toYaml(changed),
  fullYaml: Core.toYaml(state),
  unchanged: Core.diff(Core.clone(base), base, tree) === undefined,
}));
"""


@pytest.fixture
def node_form(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    page = params_form(form_scenario)
    script = re.search(r"<script>\n(.*)</script>", page, re.S).group(1)
    (tmp_path / "form_core.js").write_text(script, encoding="utf-8")
    (tmp_path / "data.json").write_text(json.dumps(form_data(page)), encoding="utf-8")
    (tmp_path / "test.js").write_text(NODE_TEST, encoding="utf-8")
    completed = subprocess.run(
        [node, "test.js", "./form_core.js", "./data.json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_form_core_renders_every_field_type(node_form):
    widgets = node_form["widgets"]
    assert widgets["n"] == "integer"
    assert node_form["nRange"] == [0, 100]
    assert widgets["share"] == "number"
    assert widgets["label"] == "text"
    assert widgets["mode"] == "enum" and node_form["modeOptions"] == ["a", "b"]
    assert widgets["enabled"] == "boolean"
    assert widgets["weights"] == "array" and widgets["weights.0"] == "number"
    assert widgets["tags"] == "dict" and widgets["tags.*"] == "integer"
    assert widgets["soc"] == "union" and node_form["socKinds"] == ["uniform"]
    assert widgets["delay"] == "union"
    assert {"constant", "uniform", "normal", "choice"} <= set(node_form["delayKinds"])
    assert widgets["algorithm"] == "union"
    assert node_form["algorithmKinds"] == ["greedy", "market"]
    assert widgets["note"] == "text"  # str | None
    assert node_form["areaWidget"] == "object" and widgets["area.width"] == "number"
    assert widgets["time.horizon"] == "text"  # a duration: number or "2h"
    assert widgets["comm.delay_s"] == "choice"  # seconds or a distribution
    assert widgets["phases"] == "array" and widgets["phases.0"] == "object"
    assert widgets["phases.0.record"] == "enum"  # bool | None
    assert widgets["events.0.emit"] == "any"
    assert widgets["profiles"] == "dict" and widgets["profiles.*"] == "object"


def test_form_core_writes_a_valid_preset_of_changed_values(node_form):
    assert node_form["unchanged"] is True
    preset = yaml.safe_load(node_form["changedYaml"])
    assert preset == {
        "time": {"step": "30min"},
        "comm": {"loss_percent": 1e-7},
        "events": [
            {
                "name": "outage",
                "at": "1h",
                "until": "90min",
                "comm": {"loss_percent": 1.0},
            }
        ],
        "n": 7,
        "label": "yes",
        "mode": "b",
        "enabled": False,
        "weights": [1, 2, 3],
        "tags": {"a": 1},
        "soc": {"kind": "uniform", "low": 0.2, "high": 0.9},
        "delay": {"kind": "uniform", "low": 0.01, "high": 0.05},
        "algorithm": {"kind": "market", "price": 2.5},
        "note": "a: b # c",
        "area": {"width": 12},
    }
    params = load_params(form_scenario, preset)
    assert params.algorithm == Market(price=2.5)
    assert params.soc == Uniform(0.2, 0.9)
    assert params.area == Area(width=12.0, height=5.0)
    assert params.time.step_s == 1800.0
    # the full preset loads as well
    load_params(form_scenario, yaml.safe_load(node_form["fullYaml"]))


def test_form_core_removes_default_dict_entries_with_minus_keys(node_form):
    from mango.scenario.params import deep_merge

    preset = yaml.safe_load(node_form["removedYaml"])
    assert preset == {"tags": {"w": 5, "-x": None, "-y z": None}}
    defaults = {"tags": {"x": 3, "y z": 4}}
    assert deep_merge(defaults, preset) == {"tags": {"w": 5}}


def test_form_core_removal_survives_a_preset_file_round_trip(node_form, tmp_path):
    # the preset the form writes removes the default entries when it is
    # loaded as a file, not only as a dict
    scenario = Scenario("form_defaults", params=FormParams)
    scenario.defaults({"tags": {"x": 3, "y z": 4}})
    preset = tmp_path / "removed.yaml"
    preset.write_text(node_form["removedYaml"], encoding="utf-8")
    assert load_params(scenario).tags == {"x": 3, "y z": 4}
    assert load_params(scenario, preset).tags == {"w": 5}


def test_form_core_leaves_unset_optional_fields_of_list_items_out(node_form):
    preview = node_form["prunedYaml"]
    assert "null" not in preview
    assert yaml.safe_load(preview) == {
        "events": [{"name": "pulse", "at": "2h", "emit": {"kind": "x"}}]
    }


def test_form_core_edits_objects_of_scalars_as_key_value_rows(node_form):
    # payloads like {kind: campaign, strength: 0.3} get key/value rows, not JSON
    assert node_form["scalarObjects"] == [True, True, False, False, False]


def test_untouched_values_do_not_count_as_changed(tmp_path):
    scenario = Scenario("rounds", params=FormParams)
    scenario.time(horizon=50, step=1, start="2024-01-01")
    preset = tmp_path / "base.yaml"
    preset.write_text("time: {horizon: 50, step: 1}\nn: 5\n", encoding="utf-8")
    data = form_data(params_form(scenario, params=str(preset)))
    # the defaults are written like the start values ("50s", ISO dates), so a
    # preset that repeats them differs in nothing
    assert data["start"] == data["defaults"]
    assert data["defaults"]["time"]["horizon"] == "50s"


def test_a_study_form_says_what_it_edits():
    page = params_form(form_scenario, note="The study file sets its factors.")
    assert form_data(page)["note"] == "The study file sets its factors."
    assert '<p id="note" class="note"></p>' in page
    assert form_data(params_form(form_scenario))["note"] == ""


def test_form_core_parses_numbers_strictly(node_form):
    # integer fields reject decimals instead of truncating them
    assert node_form["numbers"] == [3, "invalid", 3.5, None, "invalid"]


def _chrome():
    for name in ("google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def test_form_page_renders_in_a_browser(tmp_path):
    chrome = _chrome()
    if chrome is None:
        pytest.skip("no headless Chrome/Chromium installed")
    page = tmp_path / "form.html"
    page.write_text(params_form(form_scenario), encoding="utf-8")
    completed = subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--dump-dom",
            page.as_uri(),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0 or "<html" not in completed.stdout:
        pytest.skip(f"headless Chrome did not start: {completed.stderr[-300:]}")
    dom = completed.stdout
    widgets = set(re.findall(r'data-widget="([a-z]+)"', dom))
    assert {
        "object",
        "number",
        "integer",
        "text",
        "enum",
        "boolean",
        "array",
        "dict",
        "union",
        "choice",
    } <= widgets
    assert re.search(r'<pre id="yaml">\{\}\n</pre>', dom)  # nothing changed yet
    assert "<label" in dom and ">time<" in dom


def test_form_lists_the_run_settings_after_the_model_parameters():
    data = form_data(params_form(form_scenario))
    assert data["framework"] == ["seed", "time", "comm", "profiles", "phases", "events"]
    assert data["framework_label"].startswith("Run settings")
    assert "start" not in data  # without presets the form starts from the defaults
    assert form_data(params_form(FormParams))["framework"][0] == "seed"


def test_form_starts_from_presets_and_overrides(tmp_path):
    preset = tmp_path / "base.yaml"
    preset.write_text("n: 9\ntime: {horizon: 4h}\n", encoding="utf-8")
    data = form_data(
        params_form(form_scenario, params=str(preset), overrides=["share=0.25"])
    )

    assert data["start"]["n"] == 9 and data["start"]["share"] == 0.25
    assert data["start"]["time"]["horizon"] == "4h"
    assert data["defaults"]["time"]["horizon"] == "2h"  # the diff baseline stays
    assert "starting from" in data["subtitle"] and "1 override" in data["subtitle"]
    with pytest.raises(Exception, match="nn"):
        params_form(form_scenario, overrides=["nn=3"])


def test_form_page_with_a_preset_in_a_browser(tmp_path):
    chrome = _chrome()
    if chrome is None:
        pytest.skip("no headless Chrome/Chromium installed")
    preset = tmp_path / "base.yaml"
    preset.write_text("n: 9\n", encoding="utf-8")
    page = tmp_path / "form.html"
    page.write_text(params_form(form_scenario, params=str(preset)), encoding="utf-8")
    completed = subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--dump-dom",
            page.as_uri(),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0 or "<html" not in completed.stdout:
        pytest.skip(f"headless Chrome did not start: {completed.stderr[-300:]}")
    dom = completed.stdout
    assert re.search(r'<pre id="yaml">n: 9\n</pre>', dom)  # the preset's value
    settings = dom.index('data-widget="settings"')
    assert dom.index('for="f-share"') < settings < dom.index("<summary>time</summary>")
    assert re.search(
        r'<details class="settings" data-widget="settings">', dom
    )  # closed

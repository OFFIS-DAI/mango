import json
import textwrap

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from mango.scenario import (  # noqa: E402
    ParamsError,
    Scenario,
    ScenarioError,
    StudyError,
    dump_params,
    load_params,
    load_scenario,
    params_schema,
    scenario_file_schema,
)
from mango.scenario.runner import load_scenario_ref  # noqa: E402
from mango.scenario.yaml_scenario import (  # noqa: E402
    SCENARIO_FILE_KEYS,
    FileScenario,
    is_scenario_file,
)

from . import yaml_fixtures as fx  # noqa: E402

REF = fx.REF


def write(folder, name, text):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return path


def small(folder, name="s.yaml", extra=""):
    """A tiny valid scenario file (one coordinator, ev.n EVs) plus *extra* lines."""
    base = f"""
        params: {REF}:Params
        time: {{horizon: 2h, step: 1h}}
        groups:
          coordinator: {{factory: "{REF}:make_coordinator"}}
          ev: {{factory: "{REF}:make_ev", count: ev.n}}
        record:
          - {{name: soc_kwh, group: ev, attr: soc}}
        """
    return write(folder, name, textwrap.dedent(base) + textwrap.dedent(extra))


# ----------------------------------------------------------------- C5 shape


def test_the_concept_example_shape_compiles_and_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path, "scenarios/c5.yaml", fx.C5_SCENARIO)
    scenario = load_scenario("scenarios/c5.yaml")
    assert isinstance(scenario, FileScenario)
    assert isinstance(scenario, Scenario)
    assert scenario.name == "c5"
    assert scenario.ref == "scenarios/c5.yaml"
    assert scenario.path == (tmp_path / "scenarios" / "c5.yaml").resolve()
    assert scenario.params_model.__name__ == "Params"
    assert list(scenario.kits) == ["grid"]
    assert scenario.kits["grid"].params.n_households == 4

    plan = scenario.plan()
    assert [(g.name, g.count) for g in plan.groups] == [
        ("grid.hh", 4),
        ("coordinator", 1),
        ("ev", 3),
    ]
    assert [link.name for link in plan.links] == ["reports", "dispatch", "peers"]
    recorders = {r.name: (r.kind, r.group, r.entity) for r in plan.recorders}
    assert recorders["soc_kwh"] == ("agent", "ev", None)
    assert recorders["import_kw"][0] == "world"
    assert recorders["peak_kw"][2] == "grid"
    assert recorders["n_agents"][2] == "world"
    assert plan.resources == {"grid.feeder": "grid"}  # kit resources are scoped

    result = scenario.run()
    assert result.seed["entropy"] == 42  # top-level seed is a scenario default
    assert set(result.variables()) == {
        "pv_kw",
        "soc_kwh",
        "ev_power_kw",
        "import_kw",
        "peak_kw",
        "n_agents",
    }
    feeder = result.world.environment.behavior
    assert result.ts("import_kw").iloc[-1] == pytest.approx(feeder.import_kw)
    assert result.ts("peak_kw", entity="grid").iloc[-1] == pytest.approx(
        feeder.stats.peak_kw
    )
    assert result.ts("n_agents").tolist() == [8.0] * 4
    assert result.wide("soc_kwh").shape == (4, 3)


def test_a_scenario_file_gives_the_same_results_as_the_python_scenario(tmp_path):
    path = write(tmp_path, "c5.yaml", fx.C5_SCENARIO)
    from_file = load_scenario(path)
    python = fx.python_c5_scenario()
    assert from_file.param_defaults() == python.param_defaults()
    assert str(from_file.plan()).replace("c5.yaml", "") == str(python.plan())
    assert from_file.run().checksum() == python.run().checksum()
    overrides = ["ev.n=5", "grid.n_households=2", "algorithm=proportional"]
    preset = write(tmp_path, "preset.yaml", "gain: 0.3\ngrid: {pv_share: 0.5}\n")
    left = from_file.run(preset, seed=7, overrides=overrides)
    right = python.run(preset, seed=7, overrides=overrides)
    assert left.checksum() == right.checksum()
    assert left.checksum() != from_file.run(seed=7).checksum()


def test_fingerprint_and_ref_are_stable_and_cover_the_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = small(tmp_path, extra="# a comment\n")
    first = load_scenario("s.yaml")
    assert first.ref == "s.yaml"
    assert load_scenario(path).fingerprint() == first.fingerprint()
    assert load_scenario("./s.yaml").ref == "s.yaml"
    path.write_text(path.read_text() + "# another comment\n")
    assert load_scenario(path).fingerprint() == first.fingerprint()
    small(tmp_path, extra="ev: {n: 3}\n")
    assert load_scenario(path).fingerprint() != first.fingerprint()
    outside = tmp_path.parent / f"{tmp_path.name}_outside.yaml"
    try:
        outside.write_text(path.read_text())
        assert load_scenario(outside).ref == outside.resolve().as_posix()
    finally:
        outside.unlink()
    assert repr(first) == "FileScenario('s', path='s.yaml')"
    assert first.document["groups"]["ev"]["count"] == "ev.n"


def test_name_defaults_to_the_file_name_and_params_to_scenario_params(tmp_path):
    path = write(
        tmp_path,
        "plain-name.yaml",
        f"""
        time: {{horizon: 1h, step: 1h}}
        groups:
          c: {{factory: "{REF}:make_coordinator", count: 2}}
        """,
    )
    scenario = load_scenario(path)
    assert scenario.name == "plain-name"
    assert scenario.params_model.__name__ == "ScenarioParams"
    assert scenario.plan().total_agents == 2


# ----------------------------------------------------------------- the mapping


def test_group_link_and_record_options_map_to_the_python_api(tmp_path):
    path = write(
        tmp_path,
        "full.yaml",
        f"""
        params: {REF}:Params
        time: {{horizon: 2h, step: 1h}}
        environment: {REF}:make_environment
        components:
          grid: {{kind: "{REF}:ToyGrid", n_households: 2}}
          network: {{kind: communication_preset, preset: lan}}
        groups:
          coordinator: {{factory: "{REF}:make_coordinator", naming: "boss_{{index}}"}}
          ev:
            factory: "{REF}:make_ev"
            count: "{REF}:ev_count"
            install: "{REF}:ev_install"
            naming: "{REF}:ev_name"
        links:
          - {{name: local, from: grid.hh, to: ev, pattern: all_to_all,
              where: "{REF}:same_bus", directed: true}}
          - {{name: chain, from: ev, pattern: line}}
        record:
          - {{name: power, group: ev, fn: "{REF}:ev_power", every: 2h}}
          - {{name: n_agents, fn: "{REF}:count_agents", entity: counter}}
          - {{name: feeder_peak, resource: feeder.stats.peak_kw, every: 1h}}
        on_build: ["{REF}:mark_leader"]
        """,
    )
    scenario = load_scenario(path)
    plan = scenario.plan()
    assert plan.group("coordinator").names == ["boss_0"]
    assert plan.group("ev").names == ["car_0", "car_1", "car_2"]
    local = plan.link("local")
    assert local.directed is True
    assert plan.link("chain").edges == 2
    result = scenario.run(seed=3)
    assert result.world.agents  # environment factory installed the behavior
    coordinator = result.agent("boss_0")
    assert coordinator.leader is True
    assert sorted(set(result.frame.loc[result.frame.variable == "power", "time"])) == [
        0.0,
        7200.0,
    ]
    assert set(result.frame.loc[result.frame.variable == "n_agents", "entity"]) == {
        "counter"
    }
    params = scenario.resolve_params()
    assert params.comm.delay_s == 0.001  # the communication_preset kit
    assert result.stats["link_edges"]["local"] == 2  # hh_i -- car_i on the same bus
    assert result.world.environment.behavior.buses["car_1"] == 1


def test_count_ref_and_communication_factory(tmp_path):
    path = write(
        tmp_path,
        "comm.yaml",
        f"""
        params: {REF}:Params
        time: {{horizon: 1h, step: 1h}}
        communication: {REF}:make_communication
        groups:
          ev: {{factory: "{REF}:make_ev", count: "{REF}:ev_count"}}
        """,
    )
    fx.COMM_CALLS.clear()
    scenario = load_scenario(path)
    result = scenario.run(seed=1)
    assert result.groups() == ["ev"]
    assert (
        len(result.wide("soc_kwh").columns) == 3
        if "soc_kwh" in (result.variables())
        else result.stats["agents"] == 3
    )
    assert fx.COMM_CALLS == [scenario.resolve_params().comm.kind]


def test_top_level_params_fields_and_defaults_block(tmp_path):
    path = write(
        tmp_path,
        "named.yaml",
        f"""
        name: scenario_name
        params: {REF}:NamedParams
        seed: 3
        comm: {{kind: simple, loss_percent: 0.2}}
        algorithm: proportional
        defaults:
          name: field_value
          ev.n: 4
        groups:
          ev: {{factory: "{REF}:make_ev", count: ev.n}}
        """,
    )
    scenario = load_scenario(path)
    assert scenario.name == "scenario_name"
    params = scenario.resolve_params()
    assert params.name == "field_value"
    assert params.ev.n == 4
    assert params.seed == 3
    assert params.algorithm == "proportional"
    assert params.comm.loss_percent == 0.2
    assert scenario.resolve_params(overrides=["ev.n=1"]).ev.n == 1  # still defaults


def test_phases_and_events_are_checked_against_the_run_time_only(tmp_path):
    path = small(
        tmp_path,
        extra="""
        events:
          - {name: outage, at: 2024-06-01T01:00, until: 2024-06-01T02:00,
             comm: {loss_percent: 1.0}}
        phases:
          - {name: warmup, until: 1h, record: false}
        """,
    )
    scenario = load_scenario(path)  # time.start comes from the preset
    with pytest.raises(ParamsError, match="time.start"):
        scenario.plan()
    preset = write(tmp_path, "p.yaml", "time: {start: 2024-06-01T00:00}\n")
    plan = scenario.plan(preset)
    assert [event.name for event in plan.events] == ["outage"]
    assert [phase.name for phase in plan.phases] == ["warmup", "main"]
    result = scenario.run(preset, seed=1)
    assert set(result.frame["phase"]) == {"main"}


def test_profile_paths_resolve_relative_to_the_scenario_file(tmp_path, monkeypatch):
    project = tmp_path / "project"
    write(project, "scenarios/data/load.csv", "h0\n1.0\n2.0\n")
    small(
        project / "scenarios",
        extra="profiles:\n  h0: {path: data/load.csv, column: h0, resolution: 1h}\n",
    )
    monkeypatch.chdir(project)
    scenario = load_scenario("scenarios/s.yaml")
    params = scenario.resolve_params()
    assert params.profiles["h0"].path == "scenarios/data/load.csv"


# ----------------------------------------------------------------- extends


def test_extends_merges_with_plus_and_minus(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(
        tmp_path,
        "base/neighbourhood.yaml",
        f"""
        name: base
        params: {REF}:Params
        time: {{horizon: 2h, step: 1h}}
        ev: {{n: 2}}
        components:
          grid: {{kind: yaml_test_grid, n_households: 2}}
        groups:
          coordinator: {{factory: "{REF}:make_coordinator"}}
          spare: {{factory: "{REF}:make_coordinator", count: 2}}
          ev: {{factory: "{REF}:make_ev", count: ev.n}}
        links:
          - {{name: reports, from: grid.hh, to: coordinator, pattern: many_to_one}}
        record:
          - {{name: soc_kwh, group: ev, attr: soc}}
        """,
    )
    write(
        tmp_path,
        "scenarios/ev.yaml",
        f"""
        extends: base/neighbourhood.yaml
        name: child
        time: {{horizon: 3h}}
        components:
          grid: {{n_households: 3}}
        groups:
          -spare: null
        +links:
          - {{name: dispatch, from: ev, to: coordinator, pattern: many_to_one}}
        +record:
          - {{name: n_agents, fn: "{REF}:count_agents"}}
        """,
    )
    scenario = load_scenario("scenarios/ev.yaml")
    assert scenario.name == "child"
    assert scenario.ref == "scenarios/ev.yaml"
    plan = scenario.plan()
    assert [g.name for g in plan.groups] == ["grid.hh", "coordinator", "ev"]
    assert plan.group("grid.hh").count == 3
    assert [link.name for link in plan.links] == ["reports", "dispatch"]
    assert [r.name for r in plan.recorders][-2:] == ["soc_kwh", "n_agents"]
    assert plan.time.horizon_s == 3 * 3600 and plan.time.step_s == 3600
    assert "extends" not in scenario.document


def test_extends_a_relative_parent_next_to_the_file(tmp_path, monkeypatch):
    small(tmp_path / "deep" / "dir", name="parent.yaml", extra="name: parent\n")
    write(tmp_path, "deep/dir/child.yaml", "extends: parent.yaml\nev: {n: 4}\n")
    monkeypatch.chdir(tmp_path)
    scenario = load_scenario("deep/dir/child.yaml")
    assert scenario.name == "child"  # names are not inherited
    assert scenario.plan().group("ev").count == 4
    assert load_scenario("deep/dir/parent.yaml").name == "parent"
    write(tmp_path, "loop_a.yaml", "extends: loop_b.yaml\n")
    write(tmp_path, "loop_b.yaml", "extends: loop_a.yaml\n")
    with pytest.raises(ParamsError, match="Cycle in 'extends'"):
        load_scenario("loop_a.yaml")


def test_a_component_kind_switch_replaces_the_parent_component(tmp_path):
    write(
        tmp_path,
        "parent.yaml",
        """
        components:
          net: {kind: communication_preset, preset: lte}
        """,
    )
    write(
        tmp_path,
        "child.yaml",
        f"""
        extends: parent.yaml
        components:
          net: {{kind: "{REF}:ToyGrid", n_households: 1}}
        """,
    )
    scenario = load_scenario(tmp_path / "child.yaml")
    assert type(scenario.kits["net"]).__name__ == "ToyGrid"


# ----------------------------------------------------------------- errors


def test_unknown_keys_fail_with_suggestions_and_file_names(tmp_path):
    path = small(tmp_path, extra="gropus: {}\nev: {n: 2}\n")
    with pytest.raises(ParamsError) as info:
        load_scenario(path)
    text = str(info.value)
    assert "Invalid scenario file" in text and "s.yaml" in text
    assert "gropus: unknown key" in text and "did you mean 'groups'" in text
    assert info.value.path == "gropus"

    write(
        tmp_path,
        "nested.yaml",
        f"""
        groups:
          ev: {{factroy: "{REF}:make_ev"}}
        links:
          - {{name: l, form: ev}}
        record:
          - {{name: r, group: ev}}
        recrod: []
        """,
    )
    with pytest.raises(ParamsError) as info:
        load_scenario(tmp_path / "nested.yaml")
    text = str(info.value)
    assert "groups.ev.factroy: unknown key; did you mean 'factory'?" in text
    assert "groups.ev.factory: required key missing" in text
    assert "links.0.form: unknown key; did you mean 'from'?" in text
    assert "record.0: a per-agent recorder needs exactly one of attr and fn" in text
    assert "recrod: unknown key; neither a scenario file key nor a field" in text
    assert "did you mean 'record'?" in text
    assert len(info.value.errors) == 6


def test_a_typo_in_a_parent_file_names_that_file(tmp_path):
    write(tmp_path, "parent.yaml", "links:\n  - {name: l, from: a, patern: ring}\n")
    write(tmp_path, "child.yaml", "extends: parent.yaml\n")
    with pytest.raises(ParamsError) as info:
        load_scenario(tmp_path / "child.yaml")
    assert "did you mean 'pattern'" in str(info.value)
    assert "[from " in str(info.value) and "parent.yaml]" in str(info.value)


def test_params_errors_name_the_file_and_path(tmp_path):
    path = small(tmp_path, extra="ev: {nn: 3}\ngain: lots\n")
    with pytest.raises(ParamsError) as info:
        load_scenario(path)
    text = str(info.value)
    assert "Invalid parameters in scenario file" in text
    assert "ev.nn: unknown parameter" in text and "did you mean 'ev.n'" in text
    assert "gain:" in text
    assert {entry["source"] for entry in info.value.errors} == {info.value.source}
    assert info.value.source.endswith("s.yaml")

    path = small(
        tmp_path,
        name="ev.yaml",
        extra="events:\n  - {at: 1h, comm: {los_percent: 1}}\n",
    )
    with pytest.raises(
        ParamsError, match="events.0.comm.los_percent: .*'loss_percent'"
    ):
        load_scenario(path)
    path = small(tmp_path, name="ph.yaml", extra="phases:\n  - {nmae: x}\n")
    with pytest.raises(ParamsError, match="phases.0.nmae: unknown key; did you mean"):
        load_scenario(path)


def test_required_fields_of_the_params_model_may_come_from_presets(tmp_path):
    path = write(
        tmp_path,
        "req.yaml",
        f"""
        params: {REF}:RequiredParams
        time: {{horizon: 1h, step: 1h}}
        """,
    )
    scenario = load_scenario(path)
    with pytest.raises(ParamsError, match="site"):
        scenario.resolve_params()
    assert scenario.resolve_params({"site": "x"}).site == "x"


@pytest.mark.parametrize(
    "extra, error, message",
    [
        (
            "components:\n  grid: {kind: yaml_test_gird}\n",
            ScenarioError,
            "components.grid.kind: Unknown component kind 'yaml_test_gird'. Did you "
            "mean 'yaml_test_grid'?",
        ),
        (
            "components:\n  grid: {kind: yaml_test_grid, n_houshold: 3}\n",
            ParamsError,
            "components.grid: ToyGrid\\(\\) got unknown parameters",
        ),
        (
            f"environment: {REF}:make_envirnoment\n",
            ScenarioError,
            "environment: .*did you mean 'make_environment'",
        ),
        (
            f"on_build: [{REF}:NOT_CALLABLE]\n",
            ScenarioError,
            "on_build.0: .*is a int, not a function",
        ),
        (
            "environment: not a ref\n",
            ParamsError,
            "environment: expected an import reference",
        ),
        (
            "links:\n  - {name: l, from: ev, pattern: rnig}\n",
            ScenarioError,
            "links.0 \\(l\\): .*Did you mean 'ring'",
        ),
        (
            "record:\n  - {name: r, resource: feeder, group: ev}\n",
            ParamsError,
            "a resource recorder takes no group",
        ),
        (
            "record:\n  - {name: r, attr: soc}\n",
            ParamsError,
            "attr needs a group",
        ),
        (
            "record:\n  - {name: r, every: 1h}\n",
            ParamsError,
            "a recorder needs group with attr or fn",
        ),
        ("record:\n  - {name: r, resource: 'a..b'}\n", ScenarioError, "resource must"),
    ],
)
def test_invalid_declarations_name_file_and_key(tmp_path, extra, error, message):
    with pytest.raises(error, match=message) as info:
        load_scenario(small(tmp_path, extra=extra))
    assert "s.yaml" in str(info.value)


def test_factories_must_be_functions_not_agent_classes(tmp_path):
    path = write(
        tmp_path, "cls.yaml", f'groups:\n  ev: {{factory: "{REF}:EV", count: 2}}\n'
    )
    with pytest.raises(ScenarioError, match="is an Agent class.*agent_population"):
        load_scenario(path)
    path = write(tmp_path, "par.yaml", f"params: {REF}:EVParams\n")
    with pytest.raises(ScenarioError, match="not a ScenarioParams subclass"):
        load_scenario(path)
    path = write(tmp_path, "mod.yaml", "params: no_such_module_xyz:Params\n")
    with pytest.raises(ScenarioError, match="params: .*no_such_module_xyz"):
        load_scenario(path)
    path = write(tmp_path, "count.yaml", "groups:\n  g: {factory: a.b:c, count: [1]}\n")
    with pytest.raises(ParamsError, match="groups.g.count: expected an int"):
        load_scenario(path)


def test_unknown_resources_fail_when_planning(tmp_path):
    path = small(tmp_path, extra="record:\n  - {name: r, resource: grid.import_kw}\n")
    scenario = load_scenario(path)
    with pytest.raises(ScenarioError, match="Unknown resource 'grid'.*none"):
        scenario.plan()


def test_dotted_resource_names_use_the_longest_provided_name(tmp_path):
    from mango.scenario.yaml_scenario import _resource_getter

    class Ctx:  # resolves names like BuildContext._resource_key
        provided = {"grid", "grid.feeder", "north.tariff"}

        def _resource_key(self, name):
            if name in self.provided:
                return name
            found = [key for key in self.provided if key.endswith(f".{name}")]
            return found[0] if len(found) == 1 else None

    ctx = Ctx()
    assert _resource_getter("grid.feeder.import_kw", ctx) == (
        "grid.feeder",
        "import_kw",
    )
    assert _resource_getter("grid.import_kw", ctx) == ("grid", "import_kw")
    assert _resource_getter("tariff.price", ctx) == ("tariff", "price")  # a kit's
    assert _resource_getter("x.y", ctx) == ("x", "y")


def test_missing_and_malformed_files(tmp_path):
    with pytest.raises(ParamsError, match="not found"):
        load_scenario(tmp_path / "missing.yaml")
    write(tmp_path, "list.yaml", "- a\n- b\n")
    with pytest.raises(ParamsError, match="mapping at the top level"):
        load_scenario(tmp_path / "list.yaml")
    with pytest.raises(TypeError, match="needs a file path"):
        load_scenario(3)


# ----------------------------------------------------------------- refs in studies


def test_load_scenario_ref_accepts_scenario_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    small(tmp_path)
    scenario = load_scenario_ref("s.yaml")
    assert isinstance(scenario, FileScenario) and scenario.ref == "s.yaml"
    with pytest.raises(StudyError, match="Cannot load the scenario file 'nope.yml'"):
        load_scenario_ref("nope.yml")
    write(tmp_path, "bad.yaml", "gropus: {}\n")
    with pytest.raises(StudyError, match="did you mean 'groups'"):
        load_scenario_ref("bad.yaml")


def test_relative_scenario_files_fall_back_to_the_project_root(tmp_path, monkeypatch):
    from mango.scenario import _util

    project = tmp_path / "project"
    small(project / "scenarios")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(_util, "_PROJECT_ROOT", project)
    scenario = load_scenario_ref("scenarios/s.yaml")
    assert scenario.path == (project / "scenarios" / "s.yaml").resolve()


def test_is_scenario_file():
    assert is_scenario_file("a/b.yaml") and is_scenario_file("B.YML")
    assert is_scenario_file("x.json")
    assert not is_scenario_file("mod:scenario") and not is_scenario_file(None)


# ----------------------------------------------------------------- schema


def _check_refs_resolve(schema):
    defs = schema.get("$defs", {})

    def visit(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if ref is not None:
                assert ref.startswith("#/$defs/"), ref
                assert ref.removeprefix("#/$defs/") in defs, ref
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)


def test_scenario_file_schema_is_a_valid_json_schema(tmp_path):
    schema = scenario_file_schema()
    json.dumps(schema)
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is True
    for key in SCENARIO_FILE_KEYS:
        assert key in schema["properties"]
    for key in ("seed", "time", "comm", "profiles", "phases", "events"):
        assert key in schema["properties"]
    link = schema["$defs"]["ScenarioFileLink"]
    assert {"from", "to"} <= set(link["properties"])
    _check_refs_resolve(schema)

    scenario = load_scenario(write(tmp_path, "c5.yaml", fx.C5_SCENARIO))
    typed = scenario_file_schema(scenario)
    assert typed["additionalProperties"] is False
    assert {"ev", "algorithm", "grid"} <= set(typed["properties"])
    _check_refs_resolve(typed)
    assert (
        scenario_file_schema(fx.Params)["properties"]["ev"]
        == (params_schema(fx.Params)["properties"]["ev"])
    )

    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator.check_schema(schema)
    import yaml

    document = yaml.safe_load(fx.C5_SCENARIO)
    document["time"]["start"] = str(document["time"]["start"])
    jsonschema.Draft202012Validator(typed).validate(document)


def test_python_and_file_defaults_validate_the_same(tmp_path):
    path = write(tmp_path, "c5.yaml", fx.C5_SCENARIO)
    params = load_params(load_scenario(path))
    assert dump_params(params) == dump_params(load_params(fx.python_c5_scenario()))


def test_plan_time_errors_name_the_scenario_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    small(
        tmp_path, "links.yaml", "links:\n  - {name: l, from: evv, pattern: complete}\n"
    )
    with pytest.raises(ScenarioError, match="^links.yaml: Unknown group 'evv'"):
        load_scenario("links.yaml").plan()
    small(tmp_path, "late.yaml", "phases:\n  - {name: w, until: 3h}\n")
    with pytest.raises(ParamsError, match="from scenario defaults of late.yaml"):
        load_scenario("late.yaml").plan()


# ----------------------------------------------------------------- structure fixes


def test_a_kit_used_twice_provides_scoped_resources(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = write(
        tmp_path,
        "twice.yaml",
        f"""
        time: {{horizon: 2h, step: 1h}}
        components:
          north: {{kind: "{REF}:ToyGrid", n_households: 2}}
          south: {{kind: "{REF}:ToyGrid", n_households: 3}}
        record:
          - {{name: import_kw, resource: north.feeder.import_kw, entity: north}}
          - {{name: import_kw, resource: south.feeder.import_kw, entity: south}}
        """,
    )
    scenario = load_scenario(path)
    plan = scenario.plan()
    assert plan.resources == {"north.feeder": "north", "south.feeder": "south"}
    frame = scenario.run(seed=1).frame
    assert set(frame[frame.variable == "import_kw"].entity) == {"north", "south"}
    ambiguous = write(
        tmp_path,
        "ambiguous.yaml",
        f"""
        time: {{horizon: 2h, step: 1h}}
        components:
          north: {{kind: "{REF}:ToyGrid"}}
          south: {{kind: "{REF}:ToyGrid"}}
        record:
          - {{name: import_kw, resource: feeder.import_kw}}
        """,
    )
    with pytest.raises(ScenarioError, match="several kits \\('north.feeder'"):
        load_scenario(ambiguous).plan()


def test_record_group_lists_and_every_paths_in_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = small(
        tmp_path,
        extra="""
        parameters:
          period: 2h
        """,
    )
    text = (
        path.read_text()
        .replace(
            "  - {name: soc_kwh, group: ev, attr: soc}",
            "  - {name: soc_kwh, group: [ev], attr: soc}\n"
            f'  - {{name: one, group: [ev, coordinator], fn: "{REF}:ev_power", every: period}}',
        )
        .replace("horizon: 2h", "horizon: 4h")
    )
    path.write_text(text)
    scenario = load_scenario(path)
    plan = scenario.plan()
    assert [(r.name, r.group, r.every) for r in plan.recorders] == [
        ("soc_kwh", "ev", None),
        ("one", "ev", 7200.0),
        ("one", "coordinator", 7200.0),
    ]
    assert scenario.plan(overrides=["period=1h"]).recorders[1].every == 3600.0


def test_parameters_block_compiles_to_a_typed_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = write(
        tmp_path,
        "opinions.yaml",
        f"""
        parameters:
          epsilon: 0.2
          n: 3
          active: true
          label: base
          weights: [1, 2]
          noise: {{normal: [0, 0.1]}}
          free: {{}}
          net: {{k: 2, rewire: 0.0, topology: ring}}
        time: {{horizon: 2h, step: 1h}}
        epsilon: 0.25
        groups:
          ev: {{factory: "{REF}:make_ev", count: n}}
        links:
          - {{name: a, from: ev, pattern: net.topology}}
          - {{name: b, from: ev, pattern: {{small_world: {{k: $net.k, p: $net.rewire}}}}}}
        """,
    )
    scenario = load_scenario(path)
    model = scenario.params_model
    assert model.__name__ == "OpinionsParameters"
    params = scenario.resolve_params()
    assert params.epsilon == 0.25 and params.n == 3 and params.active is True
    assert params.weights == [1, 2] and params.free == {}
    assert type(params.noise).__name__ == "Normal"
    plan = scenario.plan(overrides=["n=4", "net.topology=line"])
    assert plan.groups[0].count == 4 and plan.link("a").edges == 3
    assert plan.link("b").edges == 4
    for override, message in [
        ("epsilon=high", "valid number"),
        ("epsilonn=0.3", "did you mean 'epsilon'"),
        ("net.kk=3", "did you mean 'net.k'"),
        ("n=1.5", "valid integer"),
    ]:
        with pytest.raises(ParamsError, match=message):
            scenario.resolve_params(overrides=[override])


def test_parameters_block_errors(tmp_path):
    clash = write(
        tmp_path,
        "clash.yaml",
        f"""
        params: {REF}:Params
        parameters: {{ev: 1}}
        """,
    )
    with pytest.raises(ScenarioError, match="already has a field 'ev'"):
        load_scenario(clash)
    bad = write(tmp_path, "bad.yaml", "parameters: {'1x': 2}\n")
    with pytest.raises(ScenarioError, match="not a valid parameter name"):
        load_scenario(bad)
    extended = write(
        tmp_path,
        "extended.yaml",
        f"""
        params: {REF}:Params
        parameters: {{boost: 1.5}}
        """,
    )
    params = load_scenario(extended).resolve_params()
    assert params.boost == 1.5 and params.ev.n == 2


def test_unknown_top_level_keys_say_how_to_declare_parameters(tmp_path):
    bare = write(tmp_path, "bare.yaml", "time: {horizon: 2h, step: 1h}\nepsilon: 0.2\n")
    with pytest.raises(
        ParamsError, match="declares no parameters of its own.*parameters:"
    ):
        load_scenario(bare)
    typed = write(tmp_path, "typed.yaml", f"params: {REF}:Params\nzeta: 1\n")
    with pytest.raises(ParamsError, match="add the field to Params"):
        load_scenario(typed)
    plain = Scenario("plain")
    with pytest.raises(ParamsError, match="declares no parameters of its own"):
        plain.resolve_params(overrides=["epsilon=0.3"])


def test_edge_list_files_are_found_next_to_the_scenario_file(tmp_path, monkeypatch):
    project = tmp_path / "project"
    write(project, "scenarios/net.csv", "source,target\nev_0,ev_1\n")
    write(
        project,
        "scenarios/edges.yaml",
        f"""
        params: {REF}:Params
        time: {{horizon: 1h, step: 1h}}
        groups:
          ev: {{factory: "{REF}:make_ev", count: 3}}
        links:
          - {{name: given, from: ev, pattern: {{edges: {{file: net.csv}}}}}}
          - {{name: back, from: ev, pattern: {{reverse: {{edges: {{file: net.csv}}}}}}, directed: true}}
        """,
    )
    monkeypatch.chdir(project)
    plan = load_scenario("scenarios/edges.yaml").plan()
    assert plan.link("given").edges == 1 and plan.link("back").edges == 1

"""mango-scenario run --until: quick test runs that stop early, and their replay."""

import json
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")
pytest.importorskip("pandas")

from . import script_fixtures as sfx  # noqa: E402
from .cli_helpers import cli, isolated_fixture, write  # noqa: E402, F401

SCRIPT_REF = sfx.__name__


def test_until_stops_a_single_run_early_and_rerun_replays_it(capsys, isolated):
    # script_demo has events up to 5h of a 6h horizon: --until leaves them valid
    code, out, err = cli(
        capsys, "run", f"{SCRIPT_REF}:scenario", "--seed", "3", "--until", "2h"
    )
    assert code == 0, err
    assert "truncated: stopped at 2h (7200 s) of the 21600 s horizon" in out
    (folder,) = Path("results/script_demo").iterdir()
    assert folder.name.endswith("_until_7200s")
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["until"] == "2h"
    assert manifest["stats"]["stop_reason"] == "until"
    assert manifest["stats"]["stop_time_s"] == 7200.0

    code, out, err = cli(capsys, "rerun", folder)
    assert code == 0, err
    assert out.startswith("identical: ")


def test_until_is_checked_before_the_run(capsys, isolated):
    code, _, err = cli(capsys, "run", f"{SCRIPT_REF}:scenario", "--until", "9h")
    assert code == 2 and "at most at the horizon (21600 s)" in err
    code, _, err = cli(capsys, "run", f"{SCRIPT_REF}:scenario", "--until", "soon")
    assert code == 2 and "--until soon: Invalid time point 'soon'" in err
    assert not Path("results").exists()


def test_until_is_for_single_runs(capsys, isolated):
    write(
        isolated,
        "studies/s.yaml",
        f"scenario: {SCRIPT_REF}:scenario\nreplications: 1\nout: results/s\n",
    )
    code, _, err = cli(capsys, "run", "studies/s.yaml", "--until", "1h")
    assert code == 2 and "studies always run to the horizon" in err

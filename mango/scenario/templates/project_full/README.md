# __PROJECT__

A mango scenario project: sensors measure a temperature with noise and report to a
collector, which estimates the temperature as the mean of the latest readings. The study
asks whether more sensors make up for noisier ones.

This is the `full` template of `mango-scenario new`: an example meant to be adapted that
uses kits, a scenario file, a CSV profile, calendar time and a warm-up phase. For a
one-file start, `mango-scenario new <folder>` writes the `minimal` template.

## Layout

| Path | What |
| --- | --- |
| `agents/` | agent classes and the factories `fn(e) -> Agent` that build them |
| `kits/` | reusable parts of scenarios (`SensorField`: sensors, their parameters and recorders) |
| `scenario.yaml` | the scenario: kits, groups, links and recorders |
| `presets/base.yaml` | parameter values: time, communication, profiles, phases, kit parameters |
| `data/` | input tables (`temperature.csv`, read as the profile `temperature`) |
| `outcomes.py` | one number per run, computed from the recorded values |
| `studies/__PROJECT__.yaml` | the study: varied factors, replications, outcomes, output folder |

## Commands

Run them in this folder (it is put on the Python path):

```bash
mango-scenario validate studies/__PROJECT__.yaml     # check everything before running
mango-scenario plan studies/__PROJECT__.yaml         # runs, agents and links
mango-scenario run studies/__PROJECT__.yaml           # short runs: sequential is fastest
mango-scenario status results/__PROJECT__
mango-scenario rerun results/__PROJECT__/run_0       # replay a run and compare checksums

mango-scenario run scenario.yaml -p presets/base.yaml --set field.n=20 --seed 1
mango-scenario to-script scenario.yaml -p presets/base.yaml -o script.py
mango-scenario form scenario.yaml                # an HTML form for presets
mango-scenario schema scenario.yaml -o presets/schema.json
```

Many runs profit from `--workers N`: the pool reuses each worker process for
short runs, since starting one costs about 1.5-3 s of CPU (`--runs-per-worker 1`
gives every run a fresh process). A few short runs are fastest sequentially.

Analyse the results in Python:

```python
from mango.scenario import load_study

frame = load_study("results/__PROJECT__")
print(frame.groupby(["field.n", "field.noise_std"])["mean_abs_error"].mean())
```

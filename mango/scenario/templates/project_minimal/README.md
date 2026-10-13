# __PROJECT__

A minimal mango scenario project: agents on a ring move their value toward the values
of their neighbours. The study asks whether stubborn agents slow down agreement.

## Layout

| Path | What |
| --- | --- |
| `model.py` | the agent, its parameters, the scenario and the outcomes |
| `presets/base.yaml` | parameter values of every run (time, number of agents, ...) |
| `study.yaml` | the study: varied factors, replications, outcomes, output folder |

## Commands

Run them in this folder (it is put on the Python path):

```bash
python model.py                               # one run, printed
mango-scenario validate study.yaml            # check everything before running
mango-scenario plan study.yaml                # runs, agents and links
mango-scenario run study.yaml                 # short runs: sequential is fastest
mango-scenario status results/study
mango-scenario rerun results/study/run_0     # replay a run, compare checksums

mango-scenario run model:scenario -p presets/base.yaml --set peers.n=50 --seed 1
mango-scenario run model:scenario -p presets/base.yaml --until 10 --seed 1   # quick test
mango-scenario to-script model:scenario -p presets/base.yaml -o plain.py     # plain mango
mango-scenario form model:scenario -p presets/base.yaml                      # HTML form
```

Analyse the results in Python:

```python
from mango.scenario import load_study

frame = load_study("results/study")
print(frame.groupby(["peers.stubbornness", "peers.n"])["mean_spread"].mean())
```

Grow the project when you need to: put agents into an `agents/` package, reusable
parts into kits, and calendar time, profiles, phases and events into presets
(`mango-scenario new demo --template full` shows all of them).

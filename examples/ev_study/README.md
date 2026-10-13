# EV study: greedy or proportional charging on a PV feeder?

Does proportional EV dispatch beat greedy dispatch on a 12-bus feeder with 30 PV
households, across 3 fleet sizes, 2 communication models and a 2-hour communication
outage, with 10 seeds each? That is 120 runs, written with the `mango.scenario` layer:
typed parameters, a reusable kit, YAML presets and study files, and the `mango-scenario`
command line tool. There is no run plumbing in this folder.

## Install and run

```bash
pip install "mango-agents[scenario-all]"   # pydantic, pyyaml, pandas, scipy, pyarrow
cd examples/ev_study                       # the project root: ev_study/ is imported from here
python data/make_profiles.py               # optional: rewrites data/profiles.csv (same bytes)
sh run.sh                                  # validate, plan, run both studies, replay one run
python analyze.py                          # tables, paired comparisons, Sobol, debugging
```

`run.sh` runs these commands:

```bash
mango-scenario validate studies/fleet_comparison.yaml   # schema + factor names checked before any run
mango-scenario plan     studies/fleet_comparison.yaml   # 120 runs, 36 to 51 agents each, 2 links
mango-scenario run      studies/fleet_comparison.yaml --workers 16
mango-scenario run      studies/sobol_pv.yaml --workers 16
mango-scenario rerun    results/fleet_comparison/run_0037   # replays one run, compares checksums
```

`python -m mango.scenario ...` is the same tool when the `mango-scenario` script is not on
your `PATH`. On a cluster, `--executor slurm --partition short` writes a Slurm array job
and submits it. `--no-submit` only writes the script. `mango-scenario status
results/fleet_comparison` shows the progress. The runs are short, so for the 3072-run
Sobol study use `--runs-per-task 4` on Slurm (768 array tasks); on a workstation
`--workers` alone reuses each worker process for several short runs (the default
`--runs-per-worker auto` measures the start-up and run times; `--runs-per-worker 1` gives
every run a fresh process).

Some more commands for this project:

```bash
mango-scenario plan ev_study.scenario:scenario -p presets/base.yaml       # groups, links, phases, events
mango-scenario run  ev_study.scenario:scenario -p presets/base.yaml --set ev.n=20 --seed 7
mango-scenario run  studies/fleet_comparison.yaml --set grid.pv_share=0.8  # new run ids, old runs kept
mango-scenario form ev_study.scenario:scenario                            # HTML form for presets
mango-scenario schema ev_study.scenario:scenario -o presets/schema.json   # editor completion
```

## What each file shows

| File | What it shows |
| --- | --- |
| `ev_study/params.py` | Typed parameters: `EVParams` with distributions (`Uniform`, `Normal`), and `Params(ScenarioParams)`, which adds `seed`, `time`, `phases`, `events`, `comm` and `profiles`. A typo in a preset fails at `validate`. |
| `ev_study/kits.py` | The `Neighbourhood` kit: the feeder behavior, 30 households with their own typed `Params`, profiles and recorders. `scenario.use(Neighbourhood(), prefix="grid")` puts its parameters under `grid`, its group at `grid.hh` and its world variables at entity `grid`. |
| `ev_study/scenario.py` | The scenario: the kit, the coordinator and EV groups, two links, two recorders (the state of charge is a `state`, the charging power a `flow`). Every EV draws from its own stream `e.rng`, keyed by its name (`ev_0`), so adding a household never changes an EV. |
| `ev_study/agents.py` | Plain mango agents and messages. Households report their net power, EVs report their state and charge with the dispatched power, and the coordinator dispatches. They find each other with `self.neighbors(tid="reports" / "dispatch")`. |
| `ev_study/grid.py` | `FeederBehavior`: a radial feeder with a linear voltage drop, computing the substation import and the bus voltages each step. |
| `ev_study/kpi.py` | The outcomes. Each is a formula over `run.ts(...)` or `run.integrate(...)`, which hold the evaluated day only; `integrate` reads the `kind="flow"` the recorders declare. |
| `presets/base.yaml` | Time (2 days, 15 min, calendar start), the unrecorded `warmup` phase, the `comm_outage` event, the communication model and the two profiles. |
| `studies/fleet_comparison.yaml` | 3 fleet sizes x 2 communication models x 2 algorithms x 10 replications = 120 runs, with common random numbers and three outcomes. |
| `studies/sobol_pv.yaml` | The follow-up study. It extends the first one, fixes two factors, samples PV share and battery size with a Sobol design (n = 256, 3072 runs) and keeps the seed, folders and outcomes. |
| `data/make_profiles.py`, `data/profiles.csv` | Synthetic, deterministic 15-minute profiles for one week: `h0` (household load in kW per 1000 kWh/a) and `pv` (kW per kWp). |
| `run.sh`, `analyze.py` | The commands above. `analyze.py` loads the study into one table, compares the algorithms pair by pair with confidence intervals, prints the Sobol indices and steps one configuration to the outage, with the same seed tree as the study. |

`results/<study>/` gets `study.yaml`, `runs.jsonl` (one fully resolved run per line),
`status.csv` and one folder per run under `runs/<run id>/`. Each run folder holds
`spec.json`, `config.resolved.yaml`, `manifest.json` (seed, versions, git commit, wall
and CPU time, warnings, checksum), `outcomes.json` and a tidy `timeseries.parquet` (CSV
without pyarrow). `environments/<hash>.txt` lists the installed packages of the runs.
The run id is a hash of the resolved configuration and the seed. A changed parameter
therefore gives new runs, finished runs are skipped before any process starts, and
`rerun` replays a run and compares checksums. After an edit of the code or the profiles
the finished runs are stale and run again; `mango-scenario certify results/fleet_comparison
--reason "..."` keeps them when the edit cannot change the results.

## The model

- **Households** read their load (`annual_kwh` x `h0`) and PV (`pv_peak_kw` x `pv`, for
  the 60 % of households that have PV) from the profiles each step. They report the net
  power to the coordinator.
- **EVs** come home at `arrival_h` with `soc` x `capacity_kwh` and leave at
  `departure_h`. While plugged in they charge with the last dispatched power, at most
  `max_power_kw`, until the battery is full. A lost dispatch keeps the previous power.
  Each EV reports its plug-in time and the energy it still needs.
- **The coordinator** shares the feeder headroom among the plugged-in EVs: the limit
  (20 kW) minus the households' net load. Reports are one step old and a dispatch acts
  one step later, so it extrapolates the load and subtracts what each EV charged since
  its last status. *greedy* serves EVs first come, first served, each up to its maximum
  power. *proportional* splits the headroom by the energy each EV still needs.
- **The feeder** puts every agent with a `bus` install attribute on bus `bus % 12`. Each
  step it computes the import at the substation and the voltages from a linear drop of
  `r_pu_per_kw` per kW and segment.

## What the study finds

The table shows the means over 10 replications of `fleet_comparison`, all 120 runs done
in 18 s with 12 worker processes:

| EVs | comm | self-consumption greedy | proportional | peak import kW greedy | proportional | min voltage pu greedy | proportional |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5 | ideal | 0.500 | 0.500 | 20.48 | 20.55 | 0.936 | 0.936 |
| 5 | lossy | 0.494 | 0.494 | 22.51 | 22.52 | 0.935 | 0.936 |
| 10 | ideal | 0.539 | 0.540 | 22.80 | 22.80 | 0.891 | 0.902 |
| 10 | lossy | 0.535 | 0.537 | 23.77 | 25.38 | 0.887 | 0.898 |
| 20 | ideal | 0.577 | 0.582 | 23.15 | 23.15 | 0.882 | 0.893 |
| 20 | lossy | 0.574 | 0.579 | 23.58 | 27.09 | 0.882 | 0.885 |

Both algorithms of a replication see the same households and EVs (common random
numbers), so `analyze.py` also compares them pair by pair: `df.paired("algorithm", ...,
baseline="greedy")` gives the mean difference, proportional minus greedy, with a 95 %
confidence interval over the 10 replications:

| EVs | comm | min voltage pu | 95 % CI | peak import kW | 95 % CI |
| --- | --- | --- | --- | --- | --- |
| 10 | ideal | +0.011 | 0.003 to 0.019 | 0.00 | 0.00 to 0.00 |
| 10 | lossy | +0.011 | -0.003 to 0.025 | +1.60 | -0.05 to 3.25 |
| 20 | ideal | +0.011 | 0.003 to 0.019 | 0.00 | 0.00 to 0.00 |
| 20 | lossy | +0.003 | -0.005 to 0.011 | +3.51 | 0.34 to 6.69 |

- **Voltage.** Proportional dispatch keeps the lowest voltage about 0.01 pu higher once
  10 or more EVs compete for the headroom; with ideal communication the interval
  excludes zero. Greedy dispatch puts the full 11 kW on a few EVs, and the worst
  moment is when those EVs sit at the far end of the feeder.
  Proportional dispatch spreads the same power over all EVs. The 5 EVs of the smallest
  fleet sit on the first five buses, near the substation, so there it hardly matters
  where the power goes.
- **Peak import.** Both algorithms use the same headroom, so the peaks are equal under
  ideal communication. The peaks above the 20 kW limit come from forecast errors in
  the evening ramp, when the PV output falls. Under 10 % message loss, proportional
  dispatch overshoots more, by 1.6 kW with 10 EVs and 3.5 kW with 20 EVs; only the
  second difference is clear with 10 replications. Proportional dispatch changes
  every EV's power every step, and while the headroom falls a lost dispatch leaves an
  EV at its previous, higher power. Greedy EVs mostly stop because their battery is
  full, so a lost "stop" message rarely matters.
- **Self-consumption** rises with the fleet size and is slightly higher with
  proportional dispatch (by at most 0.005). Greedy dispatch leaves late arrivals
  without any charge on busy evenings (see `analyze.py`).

The outcomes use the recorded evaluated day: 96 rows per entity for the flows (powers,
voltage), each describing the 15-minute step it ends. Energies are power x step length
over these rows (`run.integrate("pv_kw")`, see `ev_study/kpi.py`).

A small Sobol run of `sobol_pv.yaml` (n = 32, one replication, 128 runs) attributes
almost all variance of the three outcomes to `grid.pv_share` (total indices `ST` of 0.86
to 0.99; the battery size gets at most 0.09). The battery size barely matters, because
the 20 kW headroom, not the battery, limits how much the EVs can charge. With 32 base
samples the bootstrap intervals of the first-order indices are wide and
`sobol_indices()` warns; the study file's n = 256 narrows them.

These numbers come from a small synthetic model. They show the workflow; they are not
a grid study.

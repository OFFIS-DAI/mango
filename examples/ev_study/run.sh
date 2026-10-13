#!/usr/bin/env sh
mango-scenario validate studies/fleet_comparison.yaml   # schema + factor names checked before any run
mango-scenario plan     studies/fleet_comparison.yaml   # 120 runs, 36 to 51 agents each, 2 links
mango-scenario run      studies/fleet_comparison.yaml --workers 16
# mango-scenario run    studies/fleet_comparison.yaml --executor slurm --partition short   # on a cluster
mango-scenario run      studies/sobol_pv.yaml --workers 16
mango-scenario rerun    results/fleet_comparison/run_0037   # replays one run, compares checksums

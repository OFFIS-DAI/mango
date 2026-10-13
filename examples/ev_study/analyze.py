"""Analyse the EV studies and debug one configuration (run the studies first: run.sh).

Run from this folder: ``python analyze.py``.
"""

import asyncio
from pathlib import Path

from ev_study.scenario import scenario

from mango.scenario import load_params, load_study

df = load_study("results/fleet_comparison")  # params + outcomes, one row per run
variant = ["algorithm", "ev.n", "comm.loss_percent"]
outcomes = ["self_consumption", "peak_import_kw", "min_voltage_pu"]
print(df.groupby(variant)[outcomes].mean().unstack("algorithm").round(3).to_string())
# proportional minus greedy, paired by replication (common random numbers), 95 % CI
paired = df.paired("algorithm", outcomes, baseline="greedy", by=variant[1:])
print(paired[[*variant[1:], "outcome", "n", "mean", "ci_low", "ci_high"]].round(3))
if Path("results/sobol_pv").exists():  # after: mango-scenario run studies/sobol_pv.yaml
    print(load_study("results/sobol_pv").sobol_indices())

# Debug one configuration interactively, same seed tree as in the study
params = load_params(
    scenario, "presets/base.yaml", overrides=["ev.n=20", "algorithm=greedy"]
)
run = scenario.build(params, seed=7)


async def debug(run):
    """Step to the start of the outage and look at the coordinator."""
    async with run:
        await run.run_until("2024-06-02T12:00")
        coordinator = run.ctx.agents("coordinator")[0]
        received = coordinator.reports_received
        print(f"{run.world.clock.datetime}: the coordinator got {received} reports")
        await run.run()
    return run.result()


result = asyncio.run(debug(run))
charged_kwh = result.integrate("ev_power_kw", per_entity=True)  # the evaluated day
print(f"charged per EV on the evaluated day (kWh):\n{charged_kwh.round(1).to_string()}")

"""Outcomes of a run: one number each, computed from the recorded time series.

The warm-up day is not recorded, so ``run.ts(...)`` holds the evaluated day only.
The powers and the voltage are flows (``kind="flow"``): 96 rows per entity, each
describing the 15-minute step it ends.  Energies are therefore power x step length
summed over these rows, which ``run.integrate`` does for a recorded variable (it
reads the kind the recorder declared).
"""


def self_consumption(run):
    """Share of the PV energy of the evaluated day used within the feeder."""
    pv_kwh = run.integrate("pv_kw")
    export_kw = run.ts("import_kw").clip(upper=0).abs()  # feed-in at the substation
    export_kwh = export_kw.sum() * run.step_h  # the same rule for a derived series
    return 1 - export_kwh / pv_kwh if pv_kwh else 1.0


def peak_import_kw(run):
    return run.ts("import_kw").max()


def min_voltage_pu(run):
    return run.ts("min_voltage_pu").min()

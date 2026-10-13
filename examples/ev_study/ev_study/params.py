"""Typed parameters of the EV study: every number has a name, a type and a default."""

# The values of a run come from presets/base.yaml and the study files.

from typing import Literal

from pydantic import BaseModel

from mango.scenario import Normal, ScenarioParams, Uniform


class EVParams(BaseModel):
    n: int = 10
    capacity_kwh: float = 60.0
    max_power_kw: float = 11.0
    soc: Uniform = Uniform(0.2, 0.8)
    arrival_h: Normal = Normal(16.0, 1.5)
    departure_h: Normal = Normal(7.5, 0.5)


class Params(ScenarioParams):  # adds seed, time, phases, events, comm, profiles
    ev: EVParams = EVParams()
    algorithm: Literal["greedy", "proportional"] = "greedy"

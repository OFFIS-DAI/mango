"""Importable scenario and outcomes for the study tests.

Process pools import the scenario and the outcomes by reference
(``"tests.unit_tests.scenario.study_fixtures:scenario"``), so they live at
module level here.
"""

import concurrent.futures
import os
import time
from typing import Literal

from pydantic import BaseModel

from mango import Agent
from mango.scenario import Scenario, ScenarioParams, Uniform

REF = "tests.unit_tests.scenario.study_fixtures"

#: Runs with ``fail=True`` raise while this is true (tests flip it in-process).
FAIL_ENABLED = True


class EVParams(BaseModel, extra="forbid"):
    n: int = 2
    soc: Uniform = Uniform(0.2, 0.8)


class Params(ScenarioParams):
    ev: EVParams = EVParams()
    algorithm: Literal["greedy", "proportional", "market"] = "greedy"
    gain: float = 0.05
    fail: bool = False
    crash: bool = False
    sleep_s: float = 0.0
    tags: list[str] = []


class EV(Agent):
    def __init__(self, soc, gain, crash=False):
        super().__init__()
        self.soc = soc
        self.gain = gain
        self.crash = crash

    def handle_message(self, content, meta):
        pass

    def on_step(self, env, clock, step_size_s):
        if self.crash:  # like a segfault or an OOM kill: no Python cleanup
            os._exit(3)
        self.soc = min(1.0, self.soc + self.gain * float(self.rng.random()))


def make_ev(e):
    if e.p.fail and FAIL_ENABLED:
        raise ValueError(f"{e.name} is configured to fail")
    if e.p.sleep_s and e.index == 0:
        time.sleep(e.p.sleep_s)
    return EV(e.p.ev.soc.draw(e.rng), e.p.gain, crash=e.p.crash)


def make_scenario():
    built = Scenario("study_toy", params=Params)
    built.time(horizon="3h", step="1h")
    built.group("ev", "ev.n", make_ev)
    built.record("soc", "ev", attr="soc")
    built.record_world("n_agents", lambda ctx: len(ctx.world.agents))
    return built


scenario = make_scenario()


def mean_soc(run):
    return float(run.ts("soc").mean())


def first_soc(run):
    return float(run.ts("soc", entity="ev_0").iloc[0])


def omp_threads(run):
    return os.environ.get("OMP_NUM_THREADS")


def mkl_threads(run):
    return os.environ.get("MKL_NUM_THREADS")


def hash_seed(run):
    return os.environ.get("PYTHONHASHSEED")


def worker_pid(run):
    return os.getpid()


def broken(run):
    raise ZeroDivisionError("no outcome today")


class SpyExecutor(concurrent.futures.Executor):
    """Runs submitted calls immediately and records submissions and shutdowns."""

    def __init__(self, fail_with=None):
        self.submitted = []
        self.shutdowns = 0
        self.fail_with = fail_with

    def submit(self, fn, /, *args, **kwargs):
        self.submitted.append(args)
        future = concurrent.futures.Future()
        if self.fail_with is not None:
            future.set_exception(self.fail_with)
            return future
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, wait=True, *, cancel_futures=False):
        self.shutdowns += 1

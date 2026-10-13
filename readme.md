<p align="center">
  <img src="docs/source/_static/Logo_mango_ohne_sub.svg#gh-light-mode-only" alt="mango" width="320"/>
  <img src="docs/source/_static/Logo_mango_ohne_sub_white.svg#gh-dark-mode-only" alt="mango" width="320"/>
</p>

<p align="center">
  <b>Modular Python Agent Framework</b><br>
  <i>A lightweight, asyncio-based library for research and development of multi-agent systems</i>
</p>

<p align="center">
  <a href="https://pypi.org/project/mango-agents/">PyPI</a> |
  <a href="https://mango-agents.readthedocs.io">Read the Docs</a> |
  <a href="https://github.com/OFFIS-DAI/mango">GitHub</a> |
  <a href="mailto:mango@offis.de">mango@offis.de</a>
</p>

<p align="center">
  <a href="https://pypi.org/project/mango-agents/"><img src="https://img.shields.io/pypi/v/mango-agents?color=blue&label=PyPI" alt="PyPI"></a>
  <a href="https://mango-agents.readthedocs.io"><img src="https://img.shields.io/badge/docs-readthedocs-informational" alt="Docs"></a>
  <a href="https://github.com/OFFIS-DAI/mango/actions/workflows/test-mango.yml"><img src="https://github.com/OFFIS-DAI/mango/actions/workflows/test-mango.yml/badge.svg" alt="Tests"></a>
  <a href="https://codecov.io/gh/OFFIS-DAI/mango"><img src="https://codecov.io/gh/OFFIS-DAI/mango/graph/badge.svg?token=6KVKBICGYG" alt="Coverage"></a>
  <a href="https://github.com/OFFIS-DAI/mango/blob/development/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License"></a>
  <img src="https://img.shields.io/badge/lifecycle-maturing-blue.svg" alt="Lifecycle">
</p>

---

**mango** (**m**odul**a**r pytho**n** a**g**ent framew**o**rk) is a Python library for building and simulating multi-agent systems (MAS) on top of `asyncio`. It targets researchers and engineers who need reproducible agent behaviour, structured agent architectures, and controlled experimental conditions, without sacrificing usability for prototyping.

---

## Features

| Feature | Description |
|---|---|
| **Container-based messaging** | Agents are co-located in containers that short-circuit local message exchange without serialisation overhead |
| **FIPA ACL message format** | Standardised message envelope with built-in JSON and protobuf codecs |
| **Role decomposition** | Complex agent behaviour is factored into loosely coupled, independently testable role objects |
| **TCP and MQTT transport** | Agents communicate across process and machine boundaries via TCP sockets or an external MQTT broker |
| **Discrete-event simulation** | A clock-driven `SimulationWorld` provides deterministic, reproducible runs with configurable communication delay and loss, spatial environments, and structured data recording |
| **Scenarios and studies** | Declare a simulation once with typed parameters, run variants and seeds in parallel or on a Slurm cluster, resumable and replayable, from Python or YAML files and the `mango-scenario` command line |

---

## Installation

```bash
pip install mango-agents
```

Requires Python 3.10 or later.

---

## Quick Start

Every agent is a subclass of `Agent`. `@on_message` subscribes a method to a message type, and the agent receives every message of that type there; two lifecycle hooks signal when the agent has joined its container (`on_register`) and when all containers are active and external messaging is safe (`on_ready`).

The following complete script creates two agents, starts them in a shared container, sends a message from one to the other, and shuts everything down cleanly:

```python
import asyncio
from mango import Agent, create_tcp_container, activate, on_message


class ReportingAgent(Agent):
    def on_ready(self):
        print(f"{self.aid}: ready")

    @on_message(str)
    def handle_text(self, content, meta):
        print(f"{self.aid}: received '{content}' from {meta['sender_id']}")


async def main():
    # All agents on a single host share one container.
    container = create_tcp_container(addr=("127.0.0.1", 5555))

    sender = container.register(ReportingAgent())
    receiver = container.register(ReportingAgent())

    async with activate(container):
        await sender.send_message("Hello!", receiver.addr)
        await asyncio.sleep(0.1)  # allow time for message processing


asyncio.run(main())
# agent0: ready
# agent1: ready
# agent1: received 'Hello!' from agent0
```

`activate` starts the container and guarantees a clean shutdown when its block exits, even if an exception is raised.

### Proactive behavior

Agents are not limited to reacting to messages. `schedule_periodic_task` registers a coroutine that is called repeatedly at a fixed interval, useful for polling, broadcasting, or any time-driven behavior:

```python
class SensorAgent(Agent):
    def __init__(self, target_addr):
        super().__init__()
        self.target_addr = target_addr

    def on_ready(self):
        # Broadcast a reading every 5 seconds of (simulated) time.
        self.schedule_periodic_task(self._broadcast, delay=5.0)

    async def _broadcast(self):
        await self.send_message("temperature: 22.5 C", self.target_addr)
```

---

## Simulation

Running agents over a real network introduces timing variability that makes experiments hard to reproduce. The `SimulationWorld` eliminates this by replacing the network layer with a controlled, clock-driven execution model: time advances only when the simulation explicitly steps forward, there are no open sockets, and every run with the same inputs produces the same outputs.

```
┌──────────────────────────────────────────────────────┐
│                    SimulationWorld                   │
│                                                      │
│  ExternalClock ──► step_simulation()                 │
│                           |                          │
│         ┌─────────────────┼─────────────────┐        │
│         v                 v                 v        │
│     on_step()     message delivery    environment    │
│    (all agents)  (delay / loss model)   .step()      │
└──────────────────────────────────────────────────────┘
```

Each step proceeds in order:

1. All agents receive `on_step(env, clock, step_size_s)`.
2. The environment behavior advances by `step_size_s`.
3. The internal clock is set to the new time, waking any sleeping scheduled tasks.
4. A convergence loop delivers all messages whose simulated arrival time has passed and waits for agents to settle between deliveries.

### Complete example

The following self-contained script runs two agents for 60 simulated seconds and records how many readings the monitor received over time:

```python
import asyncio
from mango import Agent, create_world, discrete_step_until, on_message, record_agent


class SensorAgent(Agent):
    """Periodically sends a reading to a monitor agent."""

    def __init__(self):
        super().__init__()
        self.monitor_addr = None  # set after both agents are registered
        self.readings_sent = 0

    def on_ready(self):
        self.schedule_periodic_task(self._broadcast, delay=10.0)

    async def _broadcast(self):
        await self.send_message(f"reading {self.readings_sent}", self.monitor_addr)
        self.readings_sent += 1


class MonitorAgent(Agent):
    """Counts incoming readings."""

    def __init__(self):
        super().__init__()
        self.received = 0

    @on_message(str)
    def handle_reading(self, content, meta):
        self.received += 1
        print(f"  t={self.current_timestamp:.0f} s  monitor received: {content}")


async def run():
    world = create_world(start_time=0.0)
    sensor = world.register(SensorAgent())
    monitor = world.register(MonitorAgent())

    # Set the target address before on_ready is called (inside async with).
    sensor.monitor_addr = monitor.addr

    # Record the running count of received messages after every step.
    record_agent(
        world,
        "received",
        lambda a: a.received,
        filter_fn=lambda a: isinstance(a, MonitorAgent),
    )

    async with world:
        await discrete_step_until(world, max_advance_time_s=60.0)

    print(f"\nTotal readings received: {monitor.received}")
    # world.data_agent_collections["received"].timeseries holds the full time series.


asyncio.run(run())
#   t=0 s  monitor received: reading 0
#   t=10 s  monitor received: reading 1
#   t=20 s  monitor received: reading 2
#   t=30 s  monitor received: reading 3
#   t=40 s  monitor received: reading 4
#   t=50 s  monitor received: reading 5
#   t=60 s  monitor received: reading 6
#
# Total readings received: 7
```

`discrete_step_until` automatically determines each step size as the time until the next scheduled event (a message arrival or a task wakeup) and stops when no further events remain within the time budget.

For experiment designs that require fixed, uniform time increments, call `step_simulation` directly:

```python
from mango import step_simulation

async with world:
    for _ in range(10):
        result = await step_simulation(world, step_size_s=1.0)
        print(
            f"t = {world.clock.time:.1f} s    messages delivered: {result.messages_delivered}"
        )
```

### Communication modelling

By default, messages are delivered instantly with no loss. `SimpleCommunicationSimulation` adds configurable one-way latency and independent packet loss, with optional per-link overrides:

```python
from mango import create_world, SimpleCommunicationSimulation

world = create_world(
    start_time=0.0,
    communication_sim=SimpleCommunicationSimulation(
        default_delay_s=0.1,  # baseline one-way latency
        loss_percent=0.01,  # independent loss probability per message
        delay_s_directed_edge_dict={
            ("agent0", "agent1"): 0.5,  # directed per-link override
        },
    ),
)
```

For delays drawn from a distribution at runtime, use `DelayProviderCommunicationSimulation`:

```python
from mango import create_world, DelayProviderCommunicationSimulation

world = create_world(
    start_time=0.0,
    seed=42,  # reproducible: the provider draws from the world's "comm" stream
    communication_sim=DelayProviderCommunicationSimulation(
        default_delay_s_provider=lambda rng: max(0.0, rng.normal(0.1, 0.01)),
    ),
)
```

Custom communication models are implemented by subclassing `CommunicationSimulation` and overriding `calculate_communication`.

### Spatial environments

Agents can be embedded in a continuous 2-D area. Agents without a pre-assigned position are placed uniformly at random within the area during initialisation, and their positions can be recorded as a time series:

```python
from mango import (
    create_world,
    Area2D,
    DefaultEnvironment,
    record_position,
    position_history,
    discrete_step_until,
)

env = DefaultEnvironment(space=Area2D(width=100.0, height=100.0))
world = create_world(start_time=0.0, environment=env)

# ... register agents ...
record_position(world)

async with world:
    await discrete_step_until(world, max_advance_time_s=10.0)

history = position_history(world)
# history.timeseries["agent0"]  ->  [Position2D(x=..., y=...), ...]
# history.time                  ->  [0.0, 5.0, 10.0, ...]
```

Custom spaces and environment behaviors are implemented by subclassing `Space` and `Behavior` respectively.

### Data recording

`record_world` and `record_agent` capture any scalar as a time series after every simulation step, without modifying agent code. Results are plain Python lists compatible with numpy, pandas, and matplotlib:

```python
from mango import create_world, record_world, record_agent

world = create_world(start_time=0.0)
# ... register agents ...

# World-level: total messages delivered so far
record_world(world, "msg_count", lambda: len(world.recorded_messages))

# Per-agent: an arbitrary attribute
record_agent(world, "energy_kwh", lambda a: a.energy_kwh)

async with world:
    await discrete_step_until(world, max_advance_time_s=3600.0)

world.data_collections["msg_count"].timeseries  # list of scalars
world.data_agent_collections["energy_kwh"].timeseries  # dict[aid -> list]
world.data_agent_collections["energy_kwh"].time  # shared time axis
```

All exchanged messages are also logged automatically in `world.recorded_messages` as `MessageTransaction` objects, each carrying sender, receiver, send time, and arrival time.

### Agent hooks for simulation

Any `Agent` or `Role` subclass may override these methods to participate in the simulation lifecycle:

```python
class MyAgent(Agent):
    def on_step(self, env, clock, step_size_s: float):
        """Invoked at the start of every simulation step, before the clock advances."""

    def on_global_event(self, event):
        """Invoked when the environment broadcasts an event to all agents."""

    def on_agent_event(self, event):
        """Invoked when the environment targets this specific agent."""
```

---

## Scenarios and studies

The optional scenario layer (`pip install "mango-agents[scenario]"`) declares a simulation once, as groups of agents, links and recorders with typed parameters, and runs it as a study: variants and seeds, in parallel, resumable, with one self-describing folder per run. It builds an ordinary `SimulationWorld`, so agents stay unchanged, and every entity draws from its own random stream of one seed.

```python
# batteries.py
from pydantic import BaseModel

from mango import Agent
from mango.scenario import Scenario, ScenarioParams, Study, Uniform, load_study


class Battery(Agent):
    def __init__(self, soc_kwh, charge_kw):
        super().__init__()
        self.soc_kwh, self.charge_kw = soc_kwh, charge_kw

    def on_step(self, env, clock, step_size_s):
        self.soc_kwh += self.charge_kw * step_size_s / 3600


class BatteryParams(BaseModel):
    n: int = 3
    soc_kwh: Uniform = Uniform(2.0, 6.0)
    charge_kw: float = 1.5


class Params(ScenarioParams):  # adds seed, time, comm, profiles, phases, events
    battery: BatteryParams = BatteryParams()


scenario = Scenario("batteries", params=Params)
scenario.time(horizon="4h", step="1h")
scenario.group("battery", "battery.n", lambda e: Battery(
    soc_kwh=e.p.battery.soc_kwh.draw(e.rng), charge_kw=e.p.battery.charge_kw))
scenario.record("soc_kwh", "battery", attr="soc_kwh", kind="state")  # a level, not a flow


def final_soc(run):
    return float(run.wide("soc_kwh").iloc[-1].mean())


if __name__ == "__main__":
    result = scenario.run(seed=42, overrides=["battery.n=5"])
    print(result.wide("soc_kwh").round(2))  # time x entity: battery_0 ... battery_4

    study = Study(scenario, levers={"battery.charge_kw": [1.0, 2.0]}, replications=3,
                  seed=2026, outcomes=[final_soc], out="results/batteries")
    study.run()  # 6 runs, one folder each; a second call skips finished runs
    print(load_study("results/batteries").groupby("battery.charge_kw")["final_soc"].mean())
```

The same study as a file, run from the project folder with the `mango-scenario` command line (worker processes import the scenario and outcomes by reference):

```yaml
# studies/charging.yaml
scenario: batteries:scenario
levers: {battery.charge_kw: [1.0, 2.0]}
replications: 3
seed: 2026
outcomes: [batteries:final_soc]
out: results/charging
```

```bash
mango-scenario validate studies/charging.yaml          # parameters, references and data files, before any run
mango-scenario run studies/charging.yaml               # sequential; --workers 4 for a process pool (short runs
                                                       # share workers), --executor slurm on a cluster
mango-scenario rerun results/charging/run_0            # replays one run and compares checksums
mango-scenario new my_study                            # a runnable project to start from
```

Kits (reusable scenario components), time series profiles, events and phases (warm-up, outages), flows and states with time integrals and averages, Latin hypercube and Sobol designs with Sobol indices, paired comparisons under common random numbers and how many replications they need, YAML scenario files, stale-run detection and Slurm job arrays are described in the [scenario guide](https://mango-agents.readthedocs.io/en/latest/scenario.html); its table "Which feature for which question" maps research questions to features.

---

## Documentation & Support

| Resource | Link |
|---|---|
| Full documentation | [mango-agents.readthedocs.io](https://mango-agents.readthedocs.io) |
| Issue tracker | [github.com/OFFIS-DAI/mango/issues](https://github.com/OFFIS-DAI/mango/issues) |
| Contact | [mango@offis.de](mailto:mango@offis.de) |

---

## License

Distributed under the [MIT License](https://github.com/OFFIS-DAI/mango/blob/development/LICENSE).

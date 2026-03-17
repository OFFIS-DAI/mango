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

**mango** (**m**odul**a**r pytho**n** a**g**ent framew**o**rk) is a Python library for building and simulating multi-agent systems (MAS) on top of `asyncio`. It targets researchers and engineers who need reproducible agent behaviour, structured agent architectures, and controlled experimental conditions — without sacrificing usability for prototyping.

---

## Features

| Feature | Description |
|---|---|
| **Container-based messaging** | Agents are co-located in containers that short-circuit local message exchange without serialisation overhead |
| **FIPA ACL message format** | Standardised message envelope with built-in JSON and protobuf codecs |
| **Role decomposition** | Complex agent behaviour is factored into loosely coupled, independently testable role objects |
| **TCP and MQTT transport** | Agents communicate across process and machine boundaries via TCP sockets or an external MQTT broker |
| **Discrete-event simulation** | A clock-driven `SimulationWorld` provides deterministic, reproducible runs with configurable communication delay and loss, spatial environments, and structured data recording |

---

## Installation

```bash
pip install mango-agents
```

Requires Python 3.10 or later.

---

## Quick Start

### 1 — Define an agent

Subclass `Agent` and implement the relevant lifecycle hooks:

```python
from mango import Agent

class GreetingAgent(Agent):
    def on_register(self):
        # Called when the agent is registered to a container.
        # Scheduling and internal messaging are available from this point.
        pass

    def on_ready(self):
        # Called once all containers have been activated.
        # External message dispatch is safe from this point.
        print(f"Agent {self.aid} is ready at {self.addr}")

    def handle_message(self, content, meta):
        print(f"[{self.aid}] received: {content!r}  sender: {meta['sender_id']}")
```

### 2 — Create a container and run

```python
import asyncio
from mango import create_tcp_container, activate

async def main():
    c = create_tcp_container(addr=('127.0.0.1', 5555))
    a1 = c.register(GreetingAgent())
    a2 = c.register(GreetingAgent())

    async with activate(c):
        await a1.send_message("Hello!", a2.addr)
        await asyncio.sleep(0.05)

asyncio.run(main())
```

`activate` starts the container and guarantees a clean shutdown when its block exits, regardless of exceptions.

### 3 — Proactive message dispatch

```python
class PingAgent(Agent):
    async def ping(self, other_addr):
        await self.send_message("ping", other_addr)

    def handle_message(self, content, meta):
        print(f"Got: {content}")
```

`schedule_instant_message` can be used instead of `send_message` to hand off dispatch to the asyncio event loop without an explicit `await`.

---

## Simulation

The `SimulationWorld` replaces real network containers with a deterministic, clock-driven execution model. There are no open sockets and no wall-clock dependency — time advances only when the simulation explicitly steps forward. This makes experiments fully reproducible and allows simulated time to run faster or slower than real time at will.

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

Each step proceeds as follows:

1. All registered agents receive `on_step(env, clock, step_size_s)`.
2. The environment behavior advances by `step_size_s`.
3. The `ExternalClock` is set to the new time, waking any sleeping scheduled tasks.
4. A convergence loop delivers all messages whose simulated arrival time has passed and waits for agents to settle between deliveries.

### Minimal example

```python
import asyncio
from mango import Agent
from mango.simulation import create_world, discrete_step_until

class PingAgent(Agent):
    other = None
    received = 0

    def on_ready(self):
        # Register a periodic task that fires every 5 simulated seconds.
        self.schedule_periodic_task(self._ping, delay=5.0)

    async def _ping(self):
        if self.other:
            await self.send_message("ping", self.other.addr)

    def handle_message(self, content, meta):
        self.received += 1

async def run():
    world = create_world(start_time=0.0)
    a1 = world.register(PingAgent())
    a2 = world.register(PingAgent())
    a1.other, a2.other = a2, a1

    async with world:
        results = await discrete_step_until(world, max_advance_time_s=60.0)

    print(f"{len(results)} steps  |  messages received: a1={a1.received}, a2={a2.received}")

asyncio.run(run())
```

`discrete_step_until` automatically determines the size of each step as the minimum time until the next scheduled event (message arrival or task wakeup), stopping when no further events remain within the time budget.

### Manual stepping

For experiment designs that require uniform time increments or explicit step control:

```python
async with world:
    for _ in range(10):
        result = await step_simulation(world, step_size_s=1.0)
        print(f"t = {world.clock.time:.1f} s    messages delivered: {result.messages_delivered}")
```

### Communication modelling

`SimpleCommunicationSimulation` supports per-link delay assignment and stochastic packet loss:

```python
from mango.simulation import create_world, SimpleCommunicationSimulation

world = create_world(
    start_time=0.0,
    communication_sim=SimpleCommunicationSimulation(
        default_delay_s=0.1,            # baseline one-way latency
        loss_percent=0.01,              # independent loss probability per message
        delay_s_directed_edge_dict={
            ("agent0", "agent1"): 0.5,  # directed per-link override
        },
    ),
)
```

For delay distributions drawn at runtime, use `DelayProviderCommunicationSimulation`:

```python
import random
from mango.simulation import create_world, DelayProviderCommunicationSimulation

world = create_world(
    start_time=0.0,
    communication_sim=DelayProviderCommunicationSimulation(
        default_delay_s_provider=lambda: random.gauss(0.1, 0.01),
    ),
)
```

Custom communication models are implemented by subclassing `CommunicationSimulation` and overriding `calculate_communication`.

### Spatial environments

Agents can be embedded in a continuous 2-D space. Agents without a pre-assigned position are placed uniformly at random within the area during initialisation.

```python
from mango.simulation import (
    create_world, Area2D, DefaultEnvironment,
    record_position, position_history, discrete_step_until,
)

env = DefaultEnvironment(space=Area2D(width=100.0, height=100.0))
world = create_world(start_time=0.0, environment=env)

# ... register agents ...
record_position(world)

async with world:
    await discrete_step_until(world, max_advance_time_s=10.0)

history = position_history(world)
# history.timeseries["agent0"]  ->  [Position2D(x=..., y=...), ...]
# history.time                  ->  [0.0, 1.0, ...]
```

Custom spaces and environment behaviors are implemented by subclassing `Space` and `Behavior` respectively.

### Data recording

Built-in recording helpers capture time-series data after every simulation step without modifying agent code:

```python
from mango.simulation import record_world, record_agent, record_agent_having

# World-level scalar: total messages delivered across all agents
record_world(world, "msg_count", lambda: len(world.recorded_messages))

# Per-agent scalar: an arbitrary attribute on each agent
record_agent(world, "energy_kwh", lambda a: a.energy_kwh)

# Role-filtered recording: only agents carrying a specific role type
record_agent_having(world, "soc", BatteryRole, lambda a: a.roles[0].soc)

async with world:
    await discrete_step_until(world, max_advance_time_s=3600.0)

# Results are plain Python lists, compatible with numpy, pandas, matplotlib, etc.
world.data_collections["msg_count"].timeseries          # list of scalars
world.data_agent_collections["energy_kwh"].timeseries   # dict[aid -> list]
world.data_agent_collections["energy_kwh"].time         # shared time axis
```

All recorded messages are additionally available as `world.recorded_messages` — a list of `MessageTransaction` objects carrying sender, receiver, send time, and arrival time.

### Agent hooks for simulation

Any `Agent` or `Role` subclass may override these methods to participate in the simulation lifecycle:

```python
class MyAgent(Agent):
    def on_step(self, env, clock, step_size_s: float):
        """Invoked at the beginning of every simulation step, before the clock advances."""

    def on_global_event(self, event):
        """Invoked when the environment broadcasts an event to all agents."""

    def on_agent_event(self, event):
        """Invoked when the environment targets this specific agent."""
```

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

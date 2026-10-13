"""A simple scenario in a module of its own: its script is lean (test_script_runtime_features).

The lean script holds only the ``ctx``/``e`` methods the scenario's modules use,
so this module must not call ``spawn``, ``log`` or the like.
"""

from mango import Agent
from mango.scenario import Scenario


class Plain(Agent):
    def __init__(self, value):
        super().__init__()
        self.value = value

    def on_step(self, env, clock, step_size_s):
        self.value += 1


lean = Scenario("lean_demo")
lean.time(horizon=5, step=1)
items = lean.group("item", 3, lambda e: Plain(e.index))
lean.record("value", items, attr="value")
lean.record("total", items, attr="value", aggregate="sum")

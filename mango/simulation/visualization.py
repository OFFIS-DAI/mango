"""
Visualization utilities for SimulationWorld recordings.

Requires ``matplotlib`` (``pip install matplotlib``).

Functions
---------
- :func:`plot_world` – line chart of a world-level recording
- :func:`plot_agents` – multi-line chart of a per-agent recording
- :func:`plot_recordings` – grid of all recordings in a world
- :func:`show_communication_data` – message-flow timeline diagram
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .world import SimulationWorld


def _require_matplotlib():
    try:
        import matplotlib.pyplot as plt

        return plt
    except ImportError as exc:
        raise ImportError(
            "matplotlib is required for visualization. "
            "Install it with: pip install matplotlib"
        ) from exc


def plot_world(
    world: SimulationWorld,
    recording_key: str,
    *,
    title: str | None = None,
    xlabel: str = "Time (s)",
    ylabel: str = "Value",
    color: str | None = None,
    write_to: str | None = None,
) -> Any:
    """Plot a world-level recording as a line chart.

    :param world: the simulation world
    :param recording_key: key of the :class:`~mango.simulation.world.WorldRecording`
        to plot
    :param title: plot title (defaults to the recording key)
    :param xlabel: x-axis label
    :param ylabel: y-axis label
    :param color: line colour (any matplotlib colour string)
    :param write_to: if given, save the figure to this file path instead of
        returning it
    :return: the matplotlib ``Figure``

    Example::

        record_world(world, "msg_count", lambda: len(world.recorded_messages))
        async with world:
            await discrete_step_until(world, 60.0)
        fig = plot_world(world, "msg_count", ylabel="# messages")
        fig.show()
    """
    plt = _require_matplotlib()

    rec = world.data_collections.get(recording_key)
    if rec is None:
        raise KeyError(f"No world recording found for key '{recording_key}'")

    fig, ax = plt.subplots()
    kwargs: dict[str, Any] = {}
    if color is not None:
        kwargs["color"] = color
    ax.plot(rec.time, rec.timeseries, **kwargs)
    ax.set_title(title or recording_key)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    if write_to:
        fig.savefig(write_to)
    return fig


def plot_agents(
    world: SimulationWorld,
    recording_key: str,
    *,
    title: str | None = None,
    xlabel: str = "Time (s)",
    ylabel: str = "Value",
    color: str | list[str] | None = None,
    write_to: str | None = None,
) -> Any:
    """Plot a per-agent recording with one line per agent.

    :param world: the simulation world
    :param recording_key: key of the
        :class:`~mango.simulation.world.AgentsRecording` to plot
    :param title: plot title
    :param xlabel: x-axis label
    :param ylabel: y-axis label
    :param color: single colour or list of colours (one per agent)
    :param write_to: if given, save the figure to this file path
    :return: the matplotlib ``Figure``

    Example::

        record_agent(world, "soc", lambda a: a.soc)
        async with world:
            await discrete_step_until(world, 3600.0)
        plot_agents(world, "soc", ylabel="State of charge (kWh)")
    """
    plt = _require_matplotlib()

    rec = world.data_agent_collections.get(recording_key)
    if rec is None:
        raise KeyError(f"No agent recording found for key '{recording_key}'")

    fig, ax = plt.subplots()
    colors = color if isinstance(color, list) else None
    default_cycle = plt.rcParams["axes.prop_cycle"].by_key()["color"]

    for i, (aid, values) in enumerate(rec.timeseries.items()):
        t = rec.agent_time.get(aid, rec.time[: len(values)])
        c = (
            colors[i % len(colors)]
            if colors
            else (color if color else default_cycle[i % len(default_cycle)])
        )
        label = _agent_label(world, aid)
        ax.plot(t, values, label=label, color=c)

    ax.set_title(title or recording_key)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.legend(fontsize="small")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    if write_to:
        fig.savefig(write_to)
    return fig


def plot_recordings(
    world: SimulationWorld,
    *,
    figsize: tuple[float, float] | None = None,
    colormap: str | None = None,
    write_to: str | None = None,
) -> Any:
    """Plot all recordings in a grid layout.

    World-level recordings appear as single-line charts.  Per-agent
    recordings show one line per agent.

    :param world: the simulation world
    :param figsize: optional ``(width, height)`` override
    :param colormap: optional matplotlib colormap name for agent colours
    :param write_to: if given, save the figure to this file path
    :return: the matplotlib ``Figure``

    Example::

        record_world(world, "msgs", lambda: len(world.recorded_messages))
        record_agent(world, "state", lambda a: a.state)
        async with world:
            await discrete_step_until(world, 60.0)
        plot_recordings(world, write_to="results.png")
    """
    plt = _require_matplotlib()

    world_keys = list(world.data_collections.keys())
    agent_keys = list(world.data_agent_collections.keys())
    all_keys = world_keys + agent_keys

    if not all_keys:
        raise ValueError("No recordings available to plot")

    ncols = min(len(all_keys), 3)
    nrows = (len(all_keys) + ncols - 1) // ncols
    default_size = (6 * ncols, 4 * nrows)
    fig, axes = plt.subplots(
        nrows, ncols, figsize=figsize or default_size, squeeze=False
    )
    axes_flat = [axes[r][c] for r in range(nrows) for c in range(ncols)]

    cycle = plt.rcParams["axes.prop_cycle"].by_key()["color"]

    for i, key in enumerate(all_keys):
        ax = axes_flat[i]
        if key in world.data_collections:
            rec = world.data_collections[key]
            ax.plot(rec.time, rec.timeseries)
        else:
            rec = world.data_agent_collections[key]
            if colormap:
                cmap = plt.get_cmap(colormap, max(len(rec.timeseries), 1))
                color_list = [cmap(j) for j in range(len(rec.timeseries))]
            else:
                color_list = cycle

            for j, (aid, values) in enumerate(rec.timeseries.items()):
                t = rec.agent_time.get(aid, rec.time[: len(values)])
                c = color_list[j % len(color_list)]
                ax.plot(t, values, label=_agent_label(world, aid), color=c)
            ax.legend(fontsize="x-small")

        ax.set_title(key)
        ax.set_xlabel("Time (s)")
        ax.grid(True, alpha=0.3)

    for j in range(len(all_keys), len(axes_flat)):
        axes_flat[j].set_visible(False)

    fig.tight_layout()
    if write_to:
        fig.savefig(write_to)
    return fig


def show_communication_data(
    world: SimulationWorld,
    *,
    aid_to_name: dict[str, str] | None = None,
    aid_to_color: dict[str, str] | None = None,
    write_to: str | None = None,
) -> Any:
    """Plot a message-flow timeline for all recorded messages.

    Each agent appears as a labelled horizontal lane.  Every
    :class:`~mango.simulation.world.MessageTransaction` is drawn as an arrow
    from the sender lane at *sent_time* to the receiver lane at
    *arriving_time*, with a short content label at the midpoint.

    :param world: the simulation world (reads ``world.recorded_messages``)
    :param aid_to_name: optional mapping of AID → display label
    :param aid_to_color: optional mapping of AID → matplotlib colour string
    :param write_to: if given, save the figure to this file path
    :return: the matplotlib ``Figure``

    Example::

        async with world:
            await discrete_step_until(world, 10.0)
        show_communication_data(
            world,
            aid_to_name={"agent0": "Producer", "agent1": "Consumer"},
            write_to="comms.png",
        )
    """
    plt = _require_matplotlib()

    messages = world.recorded_messages
    if not messages:
        raise ValueError("No recorded messages to visualize")

    # Collect AIDs in order of first appearance
    seen: dict[str, None] = {}
    for msg in messages:
        if msg.sender_id is not None:
            seen[msg.sender_id] = None
        seen[msg.receiver_id] = None
    aids = list(seen.keys())
    aid_to_y = {aid: i for i, aid in enumerate(aids)}

    aid_to_name = aid_to_name or {}
    aid_to_color = aid_to_color or {}
    default_colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]

    fig_w = max(10.0, len(messages) * 0.6)
    fig_h = max(3.0, len(aids) * 1.4)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    # Draw agent lanes
    for i, aid in enumerate(aids):
        ax.axhline(i, color="lightgray", linewidth=1, zorder=0)
        color = aid_to_color.get(aid, default_colors[i % len(default_colors)])
        label = aid_to_name.get(aid, aid)
        ax.text(
            -0.01,
            i,
            label,
            ha="right",
            va="center",
            fontsize=9,
            transform=ax.get_yaxis_transform(),
            color=color,
        )

    # Draw message arrows
    for msg in messages:
        if msg.sender_id is None or msg.sender_id not in aid_to_y:
            continue
        sy = aid_to_y[msg.sender_id]
        ry = aid_to_y.get(msg.receiver_id)
        if ry is None:
            continue
        color = aid_to_color.get(
            msg.sender_id, default_colors[sy % len(default_colors)]
        )
        ax.annotate(
            "",
            xy=(msg.arriving_time, ry),
            xytext=(msg.sent_time, sy),
            arrowprops=dict(arrowstyle="->", color=color, lw=1.5),
        )
        mid_t = (msg.sent_time + msg.arriving_time) / 2
        mid_y = (sy + ry) / 2
        snippet = str(msg.content)[:24]
        ax.text(
            mid_t,
            mid_y,
            snippet,
            fontsize=7,
            color=color,
            ha="center",
            va="bottom",
            rotation=30 if sy != ry else 0,
        )

    ax.set_xlabel("Simulation time (s)")
    ax.set_yticks(range(len(aids)))
    ax.set_yticklabels([])
    ax.set_title("Message communication timeline")
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()

    if write_to:
        fig.savefig(write_to)
    return fig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_label(world: SimulationWorld, aid: str) -> str:
    """Return display label for an agent: name if set, else AID."""
    agent = world._agents.get(aid)
    if agent is not None and agent.name:
        return f"{agent.name} ({aid})"
    return aid

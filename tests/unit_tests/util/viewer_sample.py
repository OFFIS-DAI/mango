"""A small trace for the viewer tests: three agents, two lost sends, one still in flight."""

AID = "AgentAddress(protocol_addr='simulation', aid='{}')"


def _send(i, sender, receiver, t, with_id=True):
    record = {
        "event": "message.sent",
        "category": "message",
        "id": f"msg-{i}",
        "agent": sender,
        "sender": sender,
        "receiver": AID.format(receiver),
        "content": {"type": "Ping"},
        "sim_time": t,
        "level": "debug",
        "timestamp": "2026-10-07T18:15:08.000000Z",
    }
    if with_id:
        record["receiver_id"] = receiver
    return record


def _receive(i, agent, sender, t):
    return {
        "event": "message.received",
        "category": "message",
        "id": f"message-{i}",
        "agent": agent,
        "cause": f"msg-{i}",
        "sender": sender,
        "content": {"type": "Ping"},
        "sim_time": t,
        "level": "debug",
        "timestamp": "2026-10-07T18:15:08.000000Z",
    }


TRACE = [
    {
        "event": "trace.started",
        "category": "run",
        "level": "debug",
        "timestamp": "2026-10-07T18:15:08.000000Z",
    },
    _send(1, "a", "b", 0.0),
    _receive(1, "b", "a", 1.0),
    _send(2, "a", "c", 1.0),
    _send(3, "b", "a", 2.0),
    _receive(3, "a", "b", 3.0),
    _send(4, "a", "c", 3.0),
    _receive(4, "c", "a", 4.0),
    _send(5, "c", "b", 4.0, with_id=False),
    _send(6, "b", "a", 8.5),
    {
        "event": "note",
        "agent": "c",
        "level": "info",
        "sim_time": 9.0,
        "timestamp": "2026-10-07T18:15:08.000000Z",
    },
]

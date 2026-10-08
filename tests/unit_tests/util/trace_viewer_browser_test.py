import pytest

from mango.util.trace_viewer import render_html
from mango.util.tracing import message_topology

sync_api = pytest.importorskip("playwright.sync_api")

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


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        launched = None
        for kwargs in ({}, {"channel": "chrome"}):
            try:
                launched = pw.chromium.launch(**kwargs)
                break
            except Exception:
                continue
        if launched is None:
            pytest.skip("no Chromium can be launched")
        yield launched
        launched.close()


@pytest.fixture
def page(browser, tmp_path):
    path = tmp_path / "trace.html"
    path.write_text(render_html(TRACE), encoding="utf-8")
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(path.as_uri())
    page.wait_for_timeout(300)
    page.errors = errors
    yield page
    page.close()


def test_no_console_errors(page):
    page.click("#tab-lanes")
    page.wait_for_timeout(200)
    page.click("#tab-records")
    page.wait_for_timeout(200)

    assert page.errors == []


def test_list_view_matches_message_topology(page):
    page.click("#tm-list")
    rows = page.eval_on_selector_all(
        "#topo-list tbody tr",
        """rows => rows.map(tr => [tr.dataset.from, tr.dataset.to, +tr.dataset.sent,
            +tr.dataset.received, JSON.parse(tr.dataset.types), JSON.parse(tr.dataset.first),
            JSON.parse(tr.dataset.last)])""",
    )
    graph = message_topology(TRACE)
    expected = {
        (u, v): [d["messages"], d["received"], d["types"], d["first"], d["last"]]
        for u, v, d in graph.edges(data=True)
    }
    # message_topology cannot resolve msg-5 (no receiver_id, no receipt); the viewer reads aid='b'
    expected[("c", "b")] = [1, 0, {"Ping": 1}, 4.0, 4.0]

    got = {
        (f, t): [s, r, dict(map(tuple, types)), first, last]
        for f, t, s, r, types, first, last in rows
    }

    assert got == expected
    assert sorted(graph.nodes) == ["a", "b", "c"]


def test_lanes_and_hash_round_trip(page):
    page.click("#tab-lanes")
    page.wait_for_function(
        "document.querySelectorAll('#plane .rec[data-i]').length > 0"
    )
    lanes = page.eval_on_selector_all(
        "#lhead .lh .nm", "els => els.map(e => e.textContent)"
    )

    assert lanes == ["no agent", "a", "b", "c"]
    assert page.eval_on_selector_all("#plane .rec[data-i]", "els => els.length") > 0

    page.click("#o-file")
    page.wait_for_function("location.hash.includes('o=file')")
    url = page.url

    assert "v=lanes" in url and "o=file" in url and "a=~,a,b,c" in url

    page.goto("about:blank")
    page.goto(url)
    page.wait_for_function(
        "document.querySelectorAll('#plane .rec[data-i]').length > 0"
    )

    assert page.get_attribute("#tab-lanes", "aria-selected") == "true"
    assert page.get_attribute("#o-file", "aria-pressed") == "true"
    assert page.evaluate("location.hash") == "#" + url.split("#", 1)[1]


def _index(event_id):
    return next(i for i, r in enumerate(TRACE) if r.get("id") == event_id)


def test_lost_and_in_flight_split(page):
    page.click("#tm-list")
    rows = page.eval_on_selector_all(
        "#topo-list tbody tr",
        """rows => rows.map(tr => [tr.dataset.from, tr.dataset.to, +tr.dataset.sent,
            +tr.dataset.received, +tr.dataset.lost, +tr.dataset.inflight])""",
    )
    got = {(f, t): [s, r, lost, fl] for f, t, s, r, lost, fl in rows}

    # msg-2: c received later messages, so it was lost; msg-5: sent long before the
    # end; msg-6: sent within one median latency of the end, so still in flight
    assert got[("a", "c")] == [2, 1, 1, 0]
    assert got[("c", "b")] == [1, 0, 1, 0]
    assert got[("b", "a")] == [2, 1, 1, 1]


def test_panel_explains_undelivered_sends(page):
    notes = {}
    for event_id in ["msg-2", "msg-5", "msg-6"]:
        page.evaluate(f"location.hash = '#r{_index(event_id)}'")
        page.wait_for_timeout(250)
        notes[event_id] = page.inner_text("#panel .note")

    assert notes["msg-2"].startswith("Lost: no receipt, although c received later")
    assert notes["msg-5"].startswith("Lost: no receipt; b received nothing afterwards")
    assert notes["msg-6"].startswith("In flight when the trace ended")


def test_lanes_connectors_render(page):
    page.evaluate(f"location.hash = '#r{_index('msg-6')}&v=lanes'")
    page.wait_for_function("document.querySelectorAll('#plane svg.cx path').length > 0")
    hidden = page.eval_on_selector_all(
        "#plane svg.cx path, #plane svg.hl path",
        "els => els.filter(e => getComputedStyle(e).display === 'none').map(e => e.getAttribute('class'))",
    )
    kinds = page.eval_on_selector_all(
        "#plane svg.cx path",
        "els => [...new Set(els.map(e => e.getAttribute('class')))]",
    )

    assert hidden == []
    assert {"msg", "stub", "sth", "lost"} <= set(kinds)
    assert page.locator("#plane .rec.g-x").count() == 2

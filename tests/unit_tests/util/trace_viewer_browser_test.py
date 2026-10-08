import pytest

from mango.util.trace_viewer import render_html
from mango.util.tracing import message_topology

from .viewer_sample import TRACE

sync_api = pytest.importorskip("playwright.sync_api")


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


def _rows(page):
    return page.eval_on_selector_all(
        "#rows tr[data-i]", "trs => trs.map(tr => +tr.dataset.i)"
    )


def _lanes(page):
    return page.eval_on_selector_all(
        "#lhead .lh .nm", "els => els.map(e => e.textContent)"
    )


def test_text_field_and_category_filters(page):
    page.fill("#q", "msg-3")
    page.wait_for_timeout(100)

    # the send of msg-3 and its receipt, which names msg-3 as its cause
    assert _rows(page) == [4, 5]
    assert page.inner_text("#status") == "2 of 11 records"

    page.fill("#q", "receiver_id=c")
    page.wait_for_timeout(100)

    assert _rows(page) == [3, 6]

    page.fill("#q", "")
    page.click("[data-cat=message]")
    page.wait_for_timeout(100)

    assert _rows(page) == [0, 10]

    page.click("[data-cat=message]")
    page.wait_for_timeout(100)

    assert len(_rows(page)) == len(TRACE)


def test_selection_shows_cause_chain(page):
    page.click("#rows tr[data-i='5']")
    page.wait_for_function("location.hash === '#r5'")
    chain = page.eval_on_selector_all(
        "#panel ol.chain button[data-go]", "bs => bs.map(b => +b.dataset.go)"
    )

    assert chain == [4, 5]
    assert "anc" in page.get_attribute("#rows tr[data-i='4']", "class").split()
    assert "Sent by b" in page.inner_text("#panel .note")


def test_keyboard_moves_selects_and_follows_causes(page):
    page.focus("#records-box")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    page.wait_for_function("location.hash === '#r1'")

    page.click("#rows tr[data-i='5']")
    page.keyboard.press("[")
    page.wait_for_function("location.hash === '#r4'")
    page.keyboard.press("]")
    page.wait_for_function("location.hash === '#r5'")
    page.keyboard.press("Escape")
    page.wait_for_function("location.hash === ''")


def test_topology_graph_and_connection_filter(page):
    page.wait_for_function("document.querySelectorAll('#topo-svg .nd').length === 3")

    assert page.locator("#topo-svg .lk").count() == 4

    # edges are numbered in send order: a->b, a->c, b->a, c->b
    page.locator("#topo-svg .lk[data-e='1'] path.hit").dispatch_event("click")
    page.wait_for_function("location.hash.includes('link=a%3Ec')")

    assert _rows(page) == [3, 6, 7]
    assert "a → c" in page.inner_text("#linkchip")

    page.click("#linkchip [data-act=clear-link]")
    page.wait_for_timeout(150)

    assert len(_rows(page)) == len(TRACE)
    assert page.is_hidden("#linkchip")


def test_follow_chain_opens_lanes(page):
    page.click("#rows tr[data-i='5']")
    page.keyboard.press("f")
    page.wait_for_function(
        "document.querySelectorAll('#plane .rec[data-i]').length > 0"
    )

    assert page.get_attribute("#tab-lanes", "aria-selected") == "true"
    assert _lanes(page) == ["b", "a"]
    assert "Following 2 agents" in page.inner_text("#toast")


def test_lane_picker_toggles_and_undo(page):
    page.click("#tab-lanes")
    page.wait_for_function(
        "document.querySelectorAll('#plane .rec[data-i]').length > 0"
    )

    assert _lanes(page) == ["no agent", "a", "b", "c"]

    page.click("#pick")
    page.click("#pk-list li[data-l='1']")
    page.wait_for_timeout(100)

    assert _lanes(page) == ["no agent", "a", "c"]

    page.keyboard.press("Escape")
    page.wait_for_timeout(100)

    assert page.locator(".pop").count() == 0

    page.click("#lhead .lh[data-l='2'] .x")
    page.click("#toast button")
    page.wait_for_timeout(100)

    assert _lanes(page) == ["no agent", "a", "c"]


def test_go_to_time(page):
    page.keyboard.press("t")
    page.keyboard.type("soon")
    page.keyboard.press("Enter")

    assert page.inner_text("#goto-err").startswith("Not a time")

    page.fill("#goto-q", "+8s")
    page.keyboard.press("Enter")
    page.wait_for_function(
        "document.getElementById('sr').textContent.startsWith('Went to')"
    )

    assert page.locator(".pop").count() == 0


def test_markup_in_records_is_shown_as_text(browser, tmp_path):
    evil = '<img src=x class="injected">&"'
    trace = [
        {
            "event": "message.sent",
            "category": "message",
            "id": f"msg-{evil}",
            "agent": f"a{evil}",
            "receiver_id": f"b{evil}",
            "content": {"type": evil},
            "note": evil,
            "sim_time": 0.0,
            "level": "error",
            "timestamp": "2026-10-07T18:15:08.000000Z",
        },
        {
            "event": "message.received",
            "category": "message",
            "id": "rcv-1",
            "agent": f"b{evil}",
            "cause": f"msg-{evil}",
            "content": {"type": evil},
            "sim_time": 1.0,
            "level": "debug",
            "timestamp": "2026-10-07T18:15:08.000000Z",
        },
        {
            "event": f"note{evil}",
            "agent": f"a{evil}",
            "cause": "rcv-1",
            "sim_time": 2.0,
            "level": "info",
            "timestamp": "2026-10-07T18:15:08.000000Z",
        },
    ]
    path = tmp_path / "evil.html"
    path.write_text(render_html(trace), encoding="utf-8")
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(path.as_uri())
    page.wait_for_function("document.querySelectorAll('#topo-svg .nd').length === 2")
    page.click("#rows tr[data-i='1']")
    page.click("#tab-lanes")
    page.wait_for_function(
        "document.querySelectorAll('#plane .rec[data-i]').length > 0"
    )
    page.hover("#plane .rec[data-i='0']")
    page.click("#tm-list")
    page.wait_for_timeout(200)

    assert page.locator(".injected").count() == 0
    assert evil in page.inner_text("#panel")
    assert f"a{evil}" in page.inner_text("#lhead")
    assert errors == []
    page.close()

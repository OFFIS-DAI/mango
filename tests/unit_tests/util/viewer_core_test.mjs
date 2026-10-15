// Unit tests of the trace viewer's core modules, which use no browser API.
// viewer_core_test.py runs them with node, the trace and schema in $VIEWER_FIXTURE.
import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";

import { lanesLayout } from "../../../mango/util/viewer/js/core/lanes-layout.js";
import { messageGraph } from "../../../mango/util/viewer/js/core/message-graph.js";
import { E_RECV, E_SENT, S_FLIGHT, S_LOST } from "../../../mango/util/viewer/js/core/schema.js";
import { createSearch } from "../../../mango/util/viewer/js/core/search.js";
import { timeAxis } from "../../../mango/util/viewer/js/core/time-axis.js";
import { layoutTopology } from "../../../mango/util/viewer/js/core/topo-layout.js";
import { indexTrace } from "../../../mango/util/viewer/js/core/trace-index.js";
import { html, raw } from "../../../mango/util/viewer/js/ui/html.js";

const { trace, schema } = JSON.parse(fs.readFileSync(process.env.VIEWER_FIXTURE, "utf8"));
const at = id => trace.findIndex(r => r.id === id);
const index = indexTrace(trace, schema);
const axis = timeAxis(trace, index);
const graph = messageGraph(trace, index, axis);

test("records are indexed by agent, kind and cause", () => {
  assert.deepEqual(index.AGENTS, ["a", "b", "c"]);
  assert.equal(index.laneOf[0], index.WORLD);
  assert.equal(index.evOf[at("msg-1")], E_SENT);
  assert.equal(index.evOf[at("message-1")], E_RECV);
  assert.equal(index.causeIx[at("message-3")], at("msg-3"));
  assert.equal(index.recvOf(at("msg-3")), at("message-3"));
  assert.deepEqual(index.FAILS, []);
});

test("records without an agent of their own go to the sender's lane or the no-agent lane", () => {
  assert.equal(index.laneOf[at("msg-1")], index.aIx.get("a"));
  assert.equal(index.hasWorldRecs, true);
});

test("the time line uses sim time and groups times into bands", () => {
  assert.equal(axis.CLOCK, "sim");
  // 11 records at 7 different times: the bands are wider than one instant
  assert.ok(axis.quantum > 0);
  assert.equal(axis.bandOf[at("message-1")], axis.bandOf[at("msg-2")]);
  assert.notEqual(axis.bandOf[at("msg-1")], axis.bandOf[at("msg-6")]);
  assert.equal(axis.dur(90), "+2 min");
  assert.equal(axis.dur(0.25), "+250 ms");
});

test("the graph has one edge per sender and receiver, with receipts, losses and sends in flight", () => {
  const edges = Object.fromEntries(
    graph.EDGES.map(e => [`${e.from}>${e.to}`, [e.sent, e.received, e.lost, e.inFlight]]),
  );
  // msg-5 has no receiver_id and no receipt: its receiver comes from aid='b' in its address
  assert.deepEqual(edges, { "a>b": [1, 1, 0, 0], "a>c": [2, 1, 1, 0], "b>a": [2, 1, 1, 1], "c>b": [1, 0, 1, 0] });
  assert.equal(graph.sendStatus[at("msg-2")], S_LOST);
  assert.equal(graph.sendStatus[at("msg-6")], S_FLIGHT);
});

test("search finds free text in any field, and field=value in one field", () => {
  const { textMask } = createSearch(trace, index.keySet, f => f(null));
  const hits = mask => [...mask.keys()].filter(i => mask[i]);
  assert.deepEqual(hits(textMask("msg-3")), [at("msg-3"), at("message-3")]);
  assert.deepEqual(hits(textMask("receiver_id=c")), [at("msg-2"), at("msg-4")]);
  assert.equal(textMask("  "), null);
});

test("in lanes a receipt sits below its send", () => {
  const { rowOf, layoutLanes } = lanesLayout(index, axis, graph);
  const all = [index.WORLD, 0, 1, 2];
  const layout = layoutLanes(all, false, true, new Uint8Array(trace.length).fill(1));
  assert.equal(layout.placed, trace.length);
  for (const id of ["1", "3", "4"]) assert.ok(rowOf[at(`message-${id}`)] > rowOf[at(`msg-${id}`)]);
  // in file order every record has a row of its own
  const file = layoutLanes(all, true, true, new Uint8Array(trace.length).fill(1));
  assert.equal(file.maxRow >= trace.length, true);
});

test("the map puts three agents on fixed spots inside the unit square", () => {
  const map = layoutTopology(graph.NODES, graph.EDGES, () => false);
  assert.equal(map.mode, "fixed");
  for (const i of map.linked) assert.ok(Math.abs(map.x[i]) <= 1 && Math.abs(map.y[i]) <= 1);
});

test("html escapes what it puts in, unless it is markup", () => {
  const name = '<b>"x"</b> & y';
  assert.equal(
    String(html`<i title="${name}">${name}</i>`),
    '<i title="&lt;b&gt;&quot;x&quot;&lt;/b&gt; &amp; y">&lt;b&gt;&quot;x&quot;&lt;/b&gt; &amp; y</i>',
  );
  assert.equal(
    String(html`<p>${html`<b>${"<"}</b>`}${raw("<br>")}${[html`<i></i>`, "&"]}</p>`),
    "<p><b>&lt;</b><br><i></i>&amp;</p>",
  );
});

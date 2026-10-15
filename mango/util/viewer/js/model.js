// The trace embedded in the page, and everything derived from it once at load.
import { lanesLayout } from "./core/lanes-layout.js";
import { messageGraph } from "./core/message-graph.js";
import { CATS } from "./core/schema.js";
import { createSearch } from "./core/search.js";
import { timeAxis } from "./core/time-axis.js";
import { indexTrace } from "./core/trace-index.js";
import { idle } from "./ui/dom.js";

export const RECORDS = JSON.parse(document.getElementById("trace-data").textContent);
const SCHEMA = JSON.parse(document.getElementById("trace-schema").textContent);
const traceIndex = indexTrace(RECORDS, SCHEMA);
export const {
  AGENTS,
  FAILS,
  SENDS,
  WORLD,
  aIx,
  agentOf,
  catCount,
  causeIx,
  eachEffect,
  eachId,
  evOf,
  failOf,
  firstOf,
  hasWorldRecs,
  idOf,
  keySet,
  kindOf,
  laneFailed,
  laneName,
  laneOf,
  laneTotal,
  lastRecvOfAgent,
  levelCount,
  levelIx,
  levelNames,
  levelOf,
  nAgentRecs,
  nAgentSim,
  nAgents,
  nEvRecv,
  nHandler,
  nIds,
  nRecords,
  nRecv,
  nSched,
  presentLevels,
  recvOf,
  simMax,
  simMin,
  simTime,
} = traceIndex;
const traceTime = timeAxis(RECORDS, traceIndex);
export const {
  ABSOLUTE,
  CLOCK,
  MED_STEP,
  TMAX,
  TMIN,
  UNIT,
  UNIT_IX,
  bandOf,
  bandTime,
  bandTimesAll,
  dayOf,
  dur,
  finer,
  inferred,
  parseTs,
  quantum,
  recTime,
  time0,
  timeBits,
  timeHTML,
  timeLabel,
  timeOf,
  timeParts,
  wall0,
} = traceTime;
const traceGraph = messageGraph(RECORDS, traceIndex, traceTime);
export const {
  EDGES,
  IN_FLIGHT,
  LOSSY,
  MED_LAT,
  NODES,
  degreeOf,
  edgeIx,
  edgeOfRec,
  recvAgentOf,
  reverseEdge,
  sendStatus,
  shortAid,
  typeOf,
  unresolved,
} = traceGraph;
export const { prebuildText, textMask } = createSearch(RECORDS, keySet, idle);
export const { rowOf, anchorIx, vrowOf, ghostRow, layoutLanes } = lanesLayout(traceIndex, traceTime, traceGraph);

export const presentCats = CATS.filter((c, k) => catCount[k] > 0);

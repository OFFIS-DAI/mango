export const CORE = new Set(["event", "category", "id", "cause", "agent", "timestamp", "level"]);

export const CATS = ["message", "event", "task", "wait", "run", "app"];

export const CAT_LABEL = {
  message: "Messages",
  event: "Events",
  task: "Tasks",
  wait: "Waiting",
  run: "Run",
  app: "Your logs",
};

export const C_WAIT = CATS.indexOf("wait");

export const LEVELS = ["debug", "info", "warning", "error", "critical"];

export const levelColor = l => `var(--l-${l === "critical" ? "error" : LEVELS.includes(l) ? l : "info"})`;

export const E_SENT = 1;

export const E_RECV = 2;

export const E_EMIT = 3;

export const E_EVRECV = 4;

export const E_ROLE = 5;

export const E_HANDLER = 6;

export const E_SCHED = 7;

export const E_START = 8;

export const E_CYCLE = 9;

export const E_WAIT = 10;

export const E_RESUME = 11;

export const E_FIN = 12;

export const E_FAIL = 13;

export const E_CANCEL = 14;

// the code of each role a record name can have; VIEWER_SCHEMA in tracing.py gives the roles of the names
export const ROLE_CODES = {
  sent: E_SENT,
  received: E_RECV,
  emitted: E_EMIT,
  role_event: E_ROLE,
  handler: E_HANDLER,
  scheduled: E_SCHED,
  started: E_START,
  cycle: E_CYCLE,
  waiting: E_WAIT,
  resumed: E_RESUME,
  finished: E_FIN,
  failed: E_FAIL,
  cancelled: E_CANCEL,
};

export const S_LOST = 1;

export const S_FLIGHT = 2;

// How records and connections are named in text, wherever they show.
import { CLOCK, RECORDS, recvOf, shortAid, timeLabel } from "../model.js";

export const receiverName = i => {
  const r = RECORDS[i];
  return r.receiver_id || (recvOf(i) >= 0 ? RECORDS[recvOf(i)].agent : null) || shortAid(r.receiver) || "?";
};

export const edgeRange = E => {
  const f = typeof E.first === "number" ? E.first : null;
  const l = typeof E.last === "number" ? E.last : null;
  if (f == null || CLOCK !== "sim") return "";
  return f === l ? timeLabel(f) : `${timeLabel(f)}–${timeLabel(l)}`;
};

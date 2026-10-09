// Markup written as html`...`: what goes in with ${} is escaped, unless it is markup itself (another
// html`...` or raw(...)); an array goes in item by item. The result works wherever a string does.
import { esc } from "../core/util.js";

class Html extends String {}

export const raw = s => new Html(s);

const put = v => (v instanceof Html ? v : Array.isArray(v) ? v.map(put).join("") : esc(v));

export const html = (strings, ...values) => new Html(strings.reduce((out, s, k) => out + put(values[k - 1]) + s));

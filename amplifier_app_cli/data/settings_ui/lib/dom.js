// DOM helpers. All text enters the DOM as text nodes; `md` turns `backticks` into <code>.
import { MASKED } from "./api.js";

// Icons: 24px grid, 2px stroke. Strings are paths; [cx, cy, r] are circles.
const ICONS = {
  home: ["M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"],
  chip: ["M6 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z", "M9 9h6v6H9z", "M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 15h3M1 9h3M1 15h3"],
  route: ["M6 3v12", [6, 18, 3], [18, 6, 3], "M18 9a9 9 0 0 1-9 9"],
  package: ["M21 8 12 3 3 8l9 5z", "M3 8v8l9 5 9-5V8", "M12 13v8"],
  bell: ["M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9", "M10.3 21a1.94 1.94 0 0 0 3.4 0"],
  key: [[7.5, 15.5, 4.5], "M10.7 12.3 21 2", "M16 7l3 3", "M19 4l2 2"],
  refresh: ["M21 12a9 9 0 1 1-2.6-6.4L21 8", "M21 3v5h-5"],
  undo: ["M3 12a9 9 0 1 0 2.6-6.4L3 8", "M3 3v5h5"],
  file: ["M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z", "M14 3v6h6"],
  up: ["M12 19V5", "M5 12l7-7 7 7"],
  down: ["M12 5v14", "M19 12l-7 7-7-7"],
  trash: ["M3 6h18", "M8 6V4h8v2", "M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"],
  eye: ["M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z", [12, 12, 3]],
  eyeOff: ["M3 3l18 18", "M10.6 5.1A10.8 10.8 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-2.2 3.2", "M6.6 6.6A17 17 0 0 0 2 12s3.5 7 10 7a9.7 9.7 0 0 0 5.4-1.6", "M9.9 9.9a3 3 0 0 0 4.2 4.2"],
  pencil: ["M17 3l4 4L8 20H4v-4z"],
  plus: ["M12 5v14M5 12h14"],
  x: ["M6 6l12 12M18 6 6 18"],
  check: ["M20 6 9 17l-5-5"],
  pin: ["M12 17v5", "M9 3h6l-1 6 3 3v2H7v-2l3-3z"],
  plug: ["M9 2v6M15 2v6", "M6 8h12v4a6 6 0 0 1-12 0z", "M12 18v4"],
  play: ["M7 4l13 8-13 8z"],
  chevron: ["M9 6l6 6-6 6"],
  save: ["M5 3h11l5 5v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z", "M7 3v5h8", "M7 21v-7h10v7"],
  copy: ["M9 9h11v11H9z", "M5 15H4V4h11v1"],
  pulse: ["M22 12h-4l-3 9L9 3l-3 9H2"],
};

// h(tag, props, ...kids). `text` is plain text; `md` is UI copy; `vars` sets CSS custom properties.
export function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = String(v);
    else if (k === "md") el.append(h("span", {}, md(String(v)))); // one inline run, even inside flex containers
    else if (k === "vars") for (const [n, val] of Object.entries(v)) el.style.setProperty(n, val);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid !== null && kid !== undefined && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  return el;
}
const md = (s) => s.split("`").map((part, i) => (i % 2 ? h("code", { text: part }) : document.createTextNode(part)));
export const $ = (id) => document.getElementById(id);
export const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
export const show = (v) => (typeof v === "string" ? v : JSON.stringify(v));

export function icon(name) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", class: "icon", "aria-hidden": "true", fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round" })) svg.setAttribute(k, v);
  for (const s of ICONS[name] || []) {
    const el = document.createElementNS(NS, Array.isArray(s) ? "circle" : "path");
    if (Array.isArray(s)) ["cx", "cy", "r"].forEach((a, i) => el.setAttribute(a, s[i]));
    else el.setAttribute("d", s);
    svg.append(el);
  }
  return svg;
}

// btn("Save", fn, { icon, kind: "primary" | "ghost" | "danger", small, only: icon-only, ...attrs })
export function btn(label, onclick, { icon: ic, kind = "", small, only, ...attrs } = {}) {
  return h("button", {
    type: "button",
    class: ["btn", kind, small && "small", only && "icon-only"].filter(Boolean).join(" "),
    onclick,
    "aria-label": only ? label : null,
    title: only ? label : attrs.title,
    ...attrs,
  }, ic ? icon(ic) : null, only ? null : h("span", { text: label }));
}
export const link = (label, href, { icon: ic, kind = "" } = {}) => h("a", { class: `btn ${kind}`, href }, ic ? icon(ic) : null, h("span", { text: label }));
export const lamp = (cls, i) => h("span", { class: `lamp ${cls}`, "aria-hidden": "true", vars: i === undefined ? null : { "--i": String(i) } });
export const tag = (text, cls = "") => h("span", { class: `tag ${cls}`, text });
export const field = (label, input, hint) => h("label", { class: "field" }, h("span", { text: label }), input, hint ? h("small", { class: "hint", md: hint }) : null);
export const textIn = (value, onchange, props = {}) => h("input", { type: "text", value: value ?? "", onchange: (e) => onchange(e.target.value), ...props });
export const switchRow = (label, hint, checked, onchange) =>
  h("label", { class: "switch-row" }, h("span", {}, h("span", { text: label }), hint ? h("small", { class: "hint", md: hint }) : null), h("input", { type: "checkbox", role: "switch", checked, onchange: (e) => onchange(e.target.checked) }));
export const lede = (...lines) => h("div", { class: "lede" }, lines.map((l) => h("p", { md: l })));
export const panel = (title, hint, ...body) =>
  h("section", { class: "panel" }, title ? h("header", { class: "panel-head" }, h("h3", { text: title }), hint ? h("p", { class: "hint", md: hint }) : null) : null, body);

// a list with remove buttons and an add row; `two` adds a name column
export function listEditor({ items, show: showItem, onRemove, onAdd, placeholder, label, two }) {
  const inputs = two
    ? [h("input", { type: "text", placeholder: placeholder[0], "aria-label": `${label} name` }), h("input", { type: "text", class: "code", placeholder: placeholder[1], "aria-label": `${label} address` })]
    : [h("input", { type: "text", class: "code", placeholder, "aria-label": label })];
  return [
    items.length ? h("ul", { class: "list" }, items.map((it, i) => h("li", {}, showItem(it), btn("Remove", () => onRemove(it, i), { icon: "trash", only: true, kind: "ghost danger" })))) : null,
    h("div", { class: `adder${two ? " two" : ""}` }, inputs, btn("Add", () => { const v = inputs.map((x) => x.value.trim()); if (v.every(Boolean)) onAdd(...v); }, { icon: "plus" })),
  ];
}

// typed text back to a value: keep numbers numbers, booleans booleans, objects objects
export function parseValue(text, like) {
  const t = text.trim();
  if (like !== undefined && typeof like === "object") { try { return JSON.parse(t); } catch { return text; } }
  if (typeof like === "string") return text;
  if (t === "true" || t === "false") return t === "true";
  return t !== "" && Number.isFinite(Number(t)) ? Number(t) : text;
}

// key/value rows with remove buttons and an add row
export function kvEditor(entries, { onSet, onDel, onAdd, addLabel = "Add option" }) {
  const nk = h("input", { type: "text", class: "code", placeholder: "name", "aria-label": "New option name" });
  const nv = h("input", { type: "text", placeholder: "value", "aria-label": "New option value" });
  return h("div", { class: "kv" },
    entries.map(([k, v]) => [
      h("span", { class: "k" }, h("code", { text: k })),
      v === MASKED // a secret in the file: never edited here, the server keeps it as it is
        ? h("code", { class: "value", title: "A secret written into this settings file", text: MASKED })
        : typeof v === "boolean"
        ? h("span", {}, h("input", { type: "checkbox", role: "switch", checked: v, "aria-label": k, onchange: (e) => onSet(k, e.target.checked) }))
        : textIn(v === null || v === undefined ? "" : show(v), (val) => onSet(k, parseValue(val, v)), { class: "code", "aria-label": k }),
      btn(`Remove ${k}`, () => onDel(k), { icon: "trash", only: true, kind: "ghost danger" }),
    ]),
    nk, nv, btn(addLabel, () => { if (nk.value.trim()) onAdd(nk.value.trim(), parseValue(nv.value)); }, { icon: "plus" }));
}

export function toast(msg) {
  const t = h("div", { md: msg });
  $("toast")?.append(t);
  setTimeout(() => t.remove(), 4000);
}

export function fmtBytes(n) {
  if (n === null || n === undefined) return "large";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${i && n < 10 ? n.toFixed(1) : Math.round(n)} ${u[i]}`;
}
export function fmtAgo(hrs) {
  if (hrs === null || hrs === undefined) return null;
  if (hrs < 1) return `${plural(Math.max(1, Math.round(hrs * 60)), "minute")} ago`;
  return hrs < 48 ? `${plural(Math.round(hrs), "hour")} ago` : `${plural(Math.round(hrs / 24), "day")} ago`;
}

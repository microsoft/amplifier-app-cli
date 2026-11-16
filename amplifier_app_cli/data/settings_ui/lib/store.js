// Page state. One scope document (global / project / local) is edited at a time:
// pages read and write `S.draft`, and "Save changes" writes it back. Pages never
// import app.js; they ask for a repaint through paint() and listen with on().
import { api } from "./api.js";
import { $, h, btn, toast } from "./dom.js";

// `raw` is the File page's text for one scope: {scope, yaml, version, text}, loaded on demand
export const S = { state: null, scope: "global", tab: "home", draft: {}, original: "{}", raw: null };
export const SCOPES = {
  global: ["Global", "Your defaults, everywhere"],
  project: ["Project", "Shared with everyone in this project"],
  local: ["Local", "Only you, only this project"],
};

// ---------------------------------------------------------------- events + repaint
const listeners = {};
export const on = (evt, fn) => (listeners[evt] ||= []).push(fn);
export const emit = (evt, arg) => (listeners[evt] || []).forEach((fn) => fn(arg));
// app.js binds these; pages call paint() / paint("routing", ...) / paintNav()
const view = { main() {}, nav() {}, all() {} };
export const bindView = (fns) => Object.assign(view, fns);
export const paint = (...tabs) => { if (!tabs.length || tabs.includes(S.tab)) view.main(); };
export const paintNav = () => view.nav();

// ---------------------------------------------------------------- paths in documents
export const clone = (v) => JSON.parse(JSON.stringify(v ?? {}));
export function getIn(obj, path, fallback) {
  let o = obj;
  for (const k of path) {
    if (o === null || typeof o !== "object" || !(k in o)) return fallback;
    o = o[k];
  }
  return o;
}
export function setIn(obj, path, value) {
  let o = obj;
  for (const k of path.slice(0, -1)) {
    if (o[k] === null || typeof o[k] !== "object") o[k] = {};
    o = o[k];
  }
  o[path.at(-1)] = value;
}
export function delIn(obj, path) {
  const parent = getIn(obj, path.slice(0, -1));
  if (parent && typeof parent === "object") delete parent[path.at(-1)];
  prune(obj);
}
// drop empty containers so a save never leaves `routing: {overrides: {}}` behind
export function prune(o) {
  for (const [k, v] of Object.entries(o)) {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      prune(v);
      if (!Object.keys(v).length) delete o[k];
    }
  }
}
export const truthy = (v) => v === true || v === "true";

// ---------------------------------------------------------------- the draft
export const cur = () => S.state.scopes.find((s) => s.name === S.scope);
export const globalScope = () => S.state.scopes.find((s) => s.name === "global");
export const docDirty = () => JSON.stringify(S.draft) !== S.original;
export const rawDirty = () => Boolean(S.raw && S.raw.text !== S.raw.yaml);
export const dirty = () => docDirty() || rawDirty();
// the CLI's own defaults, from /api/state
export const defaults = () => S.state?.defaults || {};
export const changed = () => { $("savebar").hidden = !dirty(); };
export const get = (path, fallback) => getIn(S.draft, path, fallback);
export const set = (path, value) => { setIn(S.draft, path, value); changed(); };
export const del = (path) => { delIn(S.draft, path); changed(); };

// what a session sees: the last scope that sets it, using the draft for this scope
export function layered(path, { upTo } = {}) {
  let val;
  let from = null;
  for (const sc of S.state.scopes) {
    if (sc.name === upTo) break;
    const v = getIn(sc.name === S.scope ? S.draft : sc.doc, path);
    if (v !== undefined) [val, from] = [v, sc.name];
  }
  return { val, from };
}
export function layeredOverrides() {
  const overrides = {};
  const owner = {};
  for (const sc of S.state.scopes) {
    const o = getIn(sc.name === S.scope ? S.draft : sc.doc, ["routing", "overrides"], {});
    if (o && typeof o === "object") for (const [role, v] of Object.entries(o)) [overrides[role], owner[role]] = [v, sc.name];
  }
  return { overrides, owner };
}

// ---------------------------------------------------------------- providers + keys
export const providerName = (p) => p.id || String(p.module).replace(/^provider-/, "");
const prio = (p) => Number(p.config?.priority ?? 999) || 999;
export const providersIn = (doc) => [...(getIn(doc, ["config", "providers"], []) || [])].filter((p) => p && typeof p === "object").sort((a, b) => prio(a) - prio(b));
export const renumber = (list) => list.forEach((p, i) => { p.config = { ...(p.config || {}), priority: i + 1 }; });
// ${NAME} or ${NAME:default}, as lib/env_vars.py ENV_PATTERN reads them
export const PLACEHOLDER = /^\$\{([^}:]+)(?::[^}]*)?\}$/;
export const keyInfo = (name) => S.state.keys.find((k) => k.name === name);

// live model lists, same call as `amplifier provider test`; "loading" while in flight
export const models = {};
export const clearModels = () => { for (const k of Object.keys(models)) delete models[k]; };
export async function loadModels(provider) {
  if (models[provider]) return;
  models[provider] = "loading";
  try {
    models[provider] = await api(`/api/routing/models?provider=${encodeURIComponent(provider)}`);
  } catch (e) {
    models[provider] = { models: null, error: e.message };
  }
  paintNav();
  paint("routing", "providers", "home");
}

// ---------------------------------------------------------------- server round trips
// take a server snapshot; `fromServer` means files may have changed (model lists, routing)
export function adopt(next, fromServer = false) {
  S.state = next;
  S.draft = clone(cur().doc);
  S.original = JSON.stringify(S.draft);
  S.raw = null; // the File page fetches the text again when it's shown
  if (fromServer) clearModels();
  view.all();
  emit("adopt", fromServer);
}
// re-read everything
export async function reload({ message } = {}) {
  try {
    adopt(await api("/api/state"), true);
    if (message) toast(message);
  } catch (e) { toast(e.message); }
}
// fresh status and keys without touching unsaved edits
export async function refreshStatus() {
  try {
    const next = await api("/api/state");
    Object.assign(S.state, { status: next.status, keys: next.keys });
  } catch { /* keep the last status */ }
  paintNav();
  paint("home", "update");
}
// adopt the {keys, status} a key write returns
export function adoptKeys(res) {
  Object.assign(S.state, { keys: res.keys, status: res.status });
  clearModels();
  paintNav();
  paint();
}

export function showError(e) {
  if (e.halt) return; // the page is showing why it stopped
  const box = h("div", { class: "notice stop" }, h("div", {}, h("strong", { md: e.message }), e.data?.errors ? h("ul", {}, e.data.errors.map((m) => h("li", { text: m }))) : null));
  if (e.status === 409 && !e.data?.exists) box.append(btn("Reload and drop my changes", () => reload(), { icon: "refresh" }));
  $("main").prepend(box);
  box.scrollIntoView({ block: "nearest" });
}
// write one scope: {doc} or {yaml, version}; returns true when saved
export async function writeScope(name, body, done) {
  const sc = S.state.scopes.find((s) => s.name === name);
  try {
    const res = await api("/api/scope", { scope: name, version: sc.version, ...body });
    adopt(res, true);
    toast(done);
    for (const k of res.moved_keys || []) toast(`Moved a pasted key into keys.env as \`${k}\`.`);
    return true;
  } catch (e) {
    showError(e);
    return false;
  }
}
// shown on scoped pages when editing a project file
export const scopeNote = () => (S.scope === "global" ? null : h("p", { class: "notice", text: `Changes here apply only in this project${S.scope === "project" ? ", for everyone who uses it" : ", and only for you"}. Anything you don't set comes from your global settings.` }));
export const saveDraft = () => (rawDirty()
  ? writeScope(S.scope, { yaml: S.raw.text, version: S.raw.version }, "Saved the settings file.")
  : writeScope(S.scope, { doc: S.draft }, `Saved ${SCOPES[S.scope][0].toLowerCase()} settings.`));
// Home's quick settings save straight to the global file
export function quickSave(mutate, done = "Saved.") {
  if (dirty()) return toast("Save or discard your other changes first.");
  const doc = clone(globalScope().doc);
  mutate(doc);
  prune(doc);
  return writeScope("global", { doc }, done);
}

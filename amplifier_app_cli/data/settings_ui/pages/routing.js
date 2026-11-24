// Routing: preview a matrix, see which model gets each role, pin models, save as a matrix.
import { api } from "../lib/api.js";
import { h, icon, btn, link, lamp, tag, field, switchRow, lede, toast } from "../lib/dom.js";
import { S, SCOPES, set, del, clone, prune, layered, layeredOverrides, models, loadModels, paint, on, defaults } from "../lib/store.js";

// view state, not settings
// `loadError`: the matrix list couldn't be read; it is fetched again only on Retry
export const R = { matrices: null, preview: null, result: null, live: true, busy: false, open: new Set(), pinning: null, pinForm: null, arrive: true, seq: 0, loadError: null };
const inUse = () => layered(["routing", "matrix"]).val || defaults().matrix;

export async function refreshRouting({ matrices = false } = {}) {
  const seq = ++R.seq;
  R.busy = true;
  try {
    if (matrices || !R.matrices) R.matrices = (await api("/api/routing/matrices")).matrices;
    if (!R.preview || !R.matrices.some((m) => m.name === R.preview)) R.preview = inUse();
    const { overrides } = layeredOverrides();
    paint("routing");
    // fast offline pass first, then the live check (a few seconds per provider at most).
    // Live checks send each provider its key, so they only run on this page.
    for (const live of R.live && S.tab === "routing" ? [false, true] : [false]) {
      const res = await api("/api/routing/resolve", { matrix: R.preview, overrides, live });
      if (seq !== R.seq) return;
      R.result = res;
      paint("routing");
    }
  } catch (e) {
    if (seq !== R.seq) return;
    if (R.matrices) R.result = { error: e.message, matrix: R.preview };
    else R.loadError = e.message;
  }
  R.busy = false;
  paint("routing", "providers", "home");
}
// new files or a different draft: work it out again once the board has been opened
on("adopt", (fromServer) => { if (R.matrices) refreshRouting({ matrices: fromServer }); });
export const ensureRouting = () => { if (!R.matrices && !R.busy && !R.loadError) refreshRouting(); };

function previewMatrix(name) {
  Object.assign(R, { preview: name, pinning: null, pinForm: null, arrive: true });
  R.open.clear();
  refreshRouting();
}

function plates(inUseName) {
  return h("div", { class: "plates", role: "group", "aria-label": "Routing matrices" },
    R.matrices.map((m) => {
      const pct = m.total ? Math.round((m.covered / m.total) * 100) : 0;
      return h("button", { type: "button", class: "plate", "aria-pressed": String(m.name === R.preview), title: m.description, onclick: () => previewMatrix(m.name) },
        h("span", { class: "plate-top" }, h("span", { class: "plate-name", text: m.name }), m.name === inUseName ? tag("in use", "lit") : m.origin === "yours" ? tag("yours") : null),
        h("span", { class: `meter${pct === 100 ? "" : pct ? " partial" : ""}` }, h("i", { vars: { "--fill": `${pct}%` } })),
        h("span", { class: "plate-foot", text: m.total ? `${m.covered}/${m.total} roles covered` : "no roles" }));
    }));
}

function board(inUseName, inUseFrom) {
  const res = R.result;
  const m = R.matrices.find((x) => x.name === R.preview);
  const isInUse = R.preview === inUseName;
  const head = h("div", { class: "board-head" },
    h("h2", {}, R.preview, isInUse ? tag(inUseFrom ? `in use, set in ${inUseFrom} settings` : "in use by default", "lit") : null),
    m?.description ? h("p", { class: "desc", text: m.description }) : null,
    h("p", { class: "meta" }, m?.updated ? h("span", { text: `Updated ${m.updated}` }) : null, h("span", { text: m?.origin === "yours" ? "Your matrix" : "Built in" }), m?.replaces ? h("span", { text: "Replaces the built-in matrix with the same name" }) : null),
    h("div", { class: "head-actions" },
      isInUse ? null : btn(`Use ${R.preview}`, () => { set(["routing", "matrix"], R.preview); refreshRouting(); toast(`Save to start using ${R.preview}.`); }, { icon: "check", kind: "primary" }),
      switchRow("Check live model lists", null, R.live, (live) => { R.live = live; refreshRouting(); })));
  const legend = h("div", { class: "legend" },
    [["winner", "Gets the work"], ["winner unverified", "Gets the work, model list not checked"], ["fallback", "Backup you can use"], ["unconfigured", "Provider not set up"], ["no-match", "No model by that name"]]
      .map(([cls, text]) => h("span", {}, lamp(`sm ${cls}`), text)));

  if (!res || res.matrix !== R.preview) return h("section", { class: "board" }, head, legend, h("p", { class: "pad quiet-note", text: "Working out which model serves each role…" }));
  if (res.error) return h("section", { class: "board" }, head, h("div", { class: "pad" }, h("p", { class: "notice stop", md: res.error })));

  const notes = [];
  if (R.busy && R.live) notes.push(h("p", { class: "notice", md: "Checking each provider's live model list, so names like `claude-sonnet-*` show the exact model they pick." }));
  if (!R.busy && res.live && res.unverified.length) notes.push(h("p", { class: "notice wait", text: `Couldn't fetch the model list from ${res.unverified.join(", ")}, so those roles aren't confirmed. Test the connection on the Providers page to see why.` }));
  const unserved = res.roles.filter((r) => r.problem);
  if (unserved.length) notes.push(h("p", { class: "notice stop", text: `${unserved.length === 1 ? "One role has" : `${unserved.length} roles have`} no model you can use: ${unserved.map((r) => r.name).join(", ")}. Pin a model, add a provider, or choose another matrix.` }));

  const { owner } = layeredOverrides();
  const el = h("section", { class: `board${R.arrive ? " arrive" : ""}` },
    head, legend,
    notes.length ? h("div", { class: "pad notes" }, notes) : null,
    h("div", { role: "list" }, res.roles.map((role, i) => roleLine(role, i, res, owner[role.name]))),
    boardFoot(res, m));
  if (!R.busy) R.arrive = false;
  return el;
}

function flatConfig(cfg) {
  const flat = [];
  const walk = (o, p) => Object.entries(o || {}).forEach(([k, v]) => (v && typeof v === "object" ? walk(v, `${p}${k}.`) : flat.push([`${p}${k}`, v])));
  walk(cfg, "");
  return flat;
}
const configTags = (cfg) => flatConfig(cfg).map(([k, v]) => h("span", { class: "tag", title: k, text: `${k.split(".").at(-1).replaceAll("_", " ")}: ${v}` }));
const isUnverified = (c, res) => res.live && !c.verified && res.unverified.includes(c.provider);
const WHY = { winner: "Gets this role's work", fallback: "Backup if the one above fails", unconfigured: "Skipped: this provider isn't set up", "no-match": "Skipped: the provider lists no model by this name" };

function roleLine(role, i, res, ownerScope) {
  const open = R.open.has(role.name);
  const w = role.winner !== null ? role.candidates[role.winner] : null;
  const toggle = () => { R.open[open ? "delete" : "add"](role.name); paint(); };
  const serving = w
    ? h("div", { class: "serving" }, h("code", { class: "serving-model" }, w.model, w.resolved ? h("span", { class: "resolved", text: ` → ${w.resolved}` }) : null), h("div", { class: "serving-meta" }, h("span", { text: `via ${w.provider}` }), configTags(w.config)))
    : h("div", { class: "serving problem", text: role.problem === "no-match" ? "No listed model matches" : "No provider set up for this role" });
  return h("div", { class: `line${open ? " open" : ""}`, role: "listitem" },
    h("div", { class: "line-row", role: "button", tabindex: "0", "aria-expanded": String(open), onclick: toggle, onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } } },
      lamp(w ? `winner${isUnverified(w, res) ? " unverified" : ""}` : "dead", i),
      h("div", {}, h("div", { class: "role-name" }, role.name, role.overridden ? tag(ownerScope ? `pinned in ${ownerScope}` : "pinned", "act") : null), role.description ? h("div", { class: "role-desc", text: role.description }) : null),
      serving,
      h("div", { class: "chain", title: `${role.candidates.length} models in this role's list` }, role.candidates.slice(0, 8).map((c) => lamp(`sm ${c.state}`)), role.candidates.length > 8 ? h("span", { class: "hint", text: `+${role.candidates.length - 8}` }) : null),
      h("span", { class: "chev" }, icon("chevron"))),
    open ? lineDetail(role, res, ownerScope) : null);
}

function lineDetail(role, res, ownerScope) {
  // fold runs of skipped candidates from one provider (gemini ships six patterns)
  const groups = [];
  for (const c of role.candidates) {
    const last = groups.at(-1);
    if (last && c.state === "unconfigured" && last[0].state === "unconfigured" && last[0].provider === c.provider) last.push(c);
    else groups.push([c]);
  }
  const step = (g) => {
    const c = g[0];
    const many = g.length > 1;
    return h("li", { class: c.state },
      lamp(`${c.state}${c.state === "winner" && isUnverified(c, res) ? " unverified" : ""}`),
      h("div", {},
        h("code", { class: "wf-model", title: many ? g.map((x) => x.model).join("\n") : null, text: many ? `${c.provider} / ${g.length} model patterns` : `${c.provider} / ${c.model}${c.resolved ? ` → ${c.resolved}` : ""}` }),
        h("div", { class: "serving-meta" }, h("span", { class: "hint", text: WHY[c.state] || c.state }), many ? null : configTags(c.config))));
  };
  const steps = role.candidates.length ? h("ol", { class: "waterfall", "aria-label": `Models tried for ${role.name}, in order` }, groups.map(step)) : h("p", { class: "hint", text: "This matrix has no models for this role yet." });
  const pinBox = (title, hint, ...actions) => h("div", { class: "pin" }, h("h4", { text: title }), h("p", { class: "hint", text: hint }), h("div", { class: "actions" }, actions));
  let side;
  if (R.pinning === role.name) side = pinEditor(role, res);
  else if (role.overridden && ownerScope && ownerScope !== S.scope) side = pinBox(`Pinned in ${ownerScope} settings`, `Switch to ${SCOPES[ownerScope][0]} at the top to change it, or pin a different model here to override it for this file.`, btn("Pin a different model here", () => openPin(role.name), { icon: "pin" }));
  else if (role.overridden) side = pinBox("You've pinned this role", "Your model is tried first; the list shows what follows it.", btn("Change pin", () => openPin(role.name), { icon: "pencil" }), btn("Remove pin", () => { del(["routing", "overrides", role.name]); refreshRouting(); }, { icon: "trash", kind: "danger" }));
  else side = pinBox("Want a different model here?", "Pin one of your providers' models to this role. It applies to whichever matrix is in use.", btn("Pin a model", () => openPin(role.name), { icon: "pin", kind: "primary" }));
  return h("div", { class: "line-detail" }, h("div", {}, steps), side);
}

function openPin(roleName) {
  R.pinning = roleName;
  R.open.add(roleName);
  paint();
}

function setFlat(obj, dotted, value) {
  const parts = dotted.split(".");
  let o = obj;
  for (const p of parts.slice(0, -1)) { if (!o[p] || typeof o[p] !== "object") o[p] = {}; o = o[p]; }
  if (value === "") delete o[parts.at(-1)];
  else o[parts.at(-1)] = value === "true" ? true : value === "false" ? false : value;
  prune(obj);
}

function pinEditor(role, res) {
  const providers = res.providers;
  if (!providers.length) return h("div", { class: "pin" }, h("p", { text: "Add a provider first, then come back to pin a model." }), link("Go to Providers", "#providers", { icon: "chip" }));
  const existing = (role.override?.candidates || []).find((c) => c && typeof c === "object");
  const w = role.winner !== null ? role.candidates[role.winner] : null;
  const start = existing || (w && providers.some((p) => p.name === w.provider) ? w : null) || { provider: providers[0].name, model: "", config: {} };
  const form = R.pinForm?.role === role.name ? R.pinForm : (R.pinForm = { role: role.name, provider: start.provider, model: start.model, config: clone(start.config), fallback: existing ? role.override.candidates.includes("base") : true });
  const ml = models[form.provider];
  if (!ml) loadModels(form.provider);
  const ptype = providers.find((p) => p.name === form.provider)?.type;
  const knobs = Object.entries(res.known_config[form.provider] || res.known_config[ptype] || {});
  const current = Object.fromEntries(flatConfig(form.config).map(([k, v]) => [k, String(v)]));
  const close = () => Object.assign(R, { pinning: null, pinForm: null });
  const commit = () => {
    if (!form.model.trim()) return toast("Choose a model to pin.");
    const cand = { provider: form.provider, model: form.model.trim(), ...(Object.keys(form.config).length ? { config: clone(form.config) } : {}) };
    set(["routing", "overrides", role.name], { ...(role.description ? { description: role.description } : {}), candidates: form.fallback ? [cand, "base"] : [cand] });
    close();
    toast(`Pinned ${cand.model} to ${role.name}. Save to keep it.`);
    refreshRouting();
  };
  return h("div", { class: "pin" },
    h("h4", { text: `Pin a model to ${role.name}` }),
    field("Provider", h("select", { onchange: (e) => { Object.assign(form, { provider: e.target.value, config: {} }); paint(); } }, providers.map((p) => h("option", { value: p.name, selected: p.name === form.provider, text: p.name === p.type ? p.name : `${p.name} (${p.type})` })))),
    field("Model",
      h("input", { type: "text", class: "code", value: form.model, list: `models-${role.name}`, placeholder: "exact name, or a pattern like claude-sonnet-*", oninput: (e) => { form.model = e.target.value; } }),
      ml === "loading" ? "Loading this provider's models…" : ml?.models ? `${ml.models.length} models available. Start typing to filter.` : ml ? "This provider's model list isn't available here, so type an exact name or a pattern like `claude-sonnet-*`." : null),
    h("datalist", { id: `models-${role.name}` }, (ml?.models || []).map((m) => h("option", { value: m }))),
    knobs.length ? h("div", { class: "fields" }, knobs.map(([k, values]) => field(k.split(".").at(-1).replaceAll("_", " "), h("select", { onchange: (e) => setFlat(form.config, k, e.target.value) }, h("option", { value: "", text: "Provider default" }), values.map((v) => h("option", { value: v, selected: current[k] === v, text: v })))))) : null,
    switchRow("Fall back to the matrix's list", "If your model isn't available.", form.fallback, (fb) => { form.fallback = fb; }),
    h("div", { class: "actions" }, btn("Pin model", commit, { icon: "pin", kind: "primary" }), btn("Cancel", () => { close(); paint(); }, { kind: "ghost" })));
}

function boardFoot(res, m) {
  const have = new Set(res.roles.map((r) => r.name));
  const roleIn = h("input", { type: "text", list: "known-roles", placeholder: "e.g. security-audit", "aria-label": "Role to add" });
  const nameIn = h("input", { type: "text", placeholder: `${R.preview}-mine`, "aria-label": "Name for the new matrix" });
  const addRole = () => {
    const name = roleIn.value.trim();
    if (!name) return;
    if (!have.has(name)) res.roles.push({ name, description: res.known_roles[name] || "", candidates: [], winner: null, problem: "no-provider", overridden: false });
    openPin(name);
  };
  const saveAs = async (overwrite = false) => {
    const roles = Object.fromEntries(res.roles.filter((r) => r.candidates.length).map((r) => [r.name, { description: r.description, candidates: r.candidates.map(({ provider, model, config }) => ({ provider, model, config })) }]));
    const n = nameIn.value.trim() || `${R.preview}-mine`;
    try {
      await api("/api/routing/matrix", { name: n, description: `Based on ${R.preview}${res.roles.some((r) => r.overridden) ? ", with your pins" : ""}.`, roles, overwrite });
      toast(`Saved ${n} to your matrices.`);
      Object.assign(R, { preview: n, arrive: true });
      await refreshRouting({ matrices: true });
    } catch (e) {
      if (e.data?.exists && confirm(`${e.message} Replace it?`)) return saveAs(true);
      toast(e.message);
    }
  };
  const remove = async () => {
    if (!confirm(`Delete your matrix ${R.preview}? This removes ${m.path}.`)) return;
    try {
      await api("/api/routing/matrix/delete", { name: R.preview });
      toast(`Deleted ${R.preview}.`);
      R.preview = null;
      refreshRouting({ matrices: true });
    } catch (e) { toast(e.message); }
  };
  return h("div", { class: "board-foot" },
    h("div", { class: "foot-item" }, field("Add a role this matrix doesn't have", roleIn, "Agents that ask for it get the model you pin."), btn("Add role", addRole, { icon: "plus" })),
    h("datalist", { id: "known-roles" }, Object.entries(res.known_roles).filter(([r]) => !have.has(r)).map(([r, d]) => h("option", { value: r, label: d }))),
    h("div", { class: "foot-item" }, field("Save this view as your own matrix", nameIn, "Includes your pins. Same result as `amplifier routing create`."), btn("Save as matrix", () => saveAs(false), { icon: "save" })),
    m?.origin === "yours" ? btn("Delete this matrix", remove, { icon: "trash", kind: "danger" }) : null);
}

export default {
  id: "routing", title: "Routing", crumb: "Which model does each kind of work.", group: "Models", icon: "route", scoped: true,
  arrive() { R.arrive = true; if (R.matrices && R.live) setTimeout(() => refreshRouting()); }, // after S.tab is set: the live check runs here only
  view() {
    const intro = lede("Amplifier gives every task a role, like coding, quick lookups or deep reasoning. A routing matrix lists the models to try for each role, in order; the first one you can use gets the work.");
    if (R.loadError) return [intro, h("div", { class: "notice stop" }, h("p", { md: R.loadError }), btn("Try again", () => { R.loadError = null; refreshRouting(); }, { icon: "refresh", small: true }))];
    if (!R.matrices) {
      ensureRouting();
      return [intro, h("p", { class: "quiet-note", text: "Reading your routing matrices…" })];
    }
    if (!R.matrices.length) return [intro, h("p", { class: "notice wait", md: "No routing matrices are downloaded yet. Run `amplifier routing list` once to fetch them, then reload this page." })];
    const { from } = layered(["routing", "matrix"]);
    return [intro, plates(inUse()), board(inUse(), from)];
  },
};

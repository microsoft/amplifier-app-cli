// Providers: the AI services in priority order. Each shows the fields its module
// declares (from /api/provider-schema) and every other config key as name/value rows.
import { api, MASKED } from "../lib/api.js";
import { h, btn, tag, field, textIn, switchRow, panel, kvEditor, plural, show, toast } from "../lib/dom.js";
import { S, get, set, layered, providerName, providersIn, renumber, PLACEHOLDER, truthy, models, loadModels, paint, scopeNote, defaults, dirty, saveDraft } from "../lib/store.js";
import { secretWidget, newKeyWidget, envName } from "../lib/secret.js";
import { openDiagnose } from "../lib/diagnose.js";
import { R, ensureRouting } from "./routing.js";

const schemas = {};
async function loadSchema(module) {
  if (schemas[module]) return;
  schemas[module] = "loading";
  try {
    schemas[module] = await api(`/api/provider-schema?module=${encodeURIComponent(module)}`);
  } catch {
    schemas[module] = { fields: [] };
  }
  paint("providers");
}

// provider warnings from the model check, as text
export const warningList = (warnings) => h("ul", { class: "warnings" }, warnings.map((w) => h("li", { text: w })));

// a secret written into the file comes back as MASKED; the server puts the real value
// back on save, matched to its provider by id (or module)
const replacing = new Set();
// saving the file unchanged lets the server move its pasted keys to keys.env
const moveKeys = () => (dirty() ? toast("Save or discard your other changes first.") : saveDraft());

function providerItem(p, i, ordered, commit, serves, inUseMatrix) {
  const name = providerName(p);
  const cfg = p.config || {};
  const touch = () => set(["config", "providers"], get(["config", "providers"])); // edited in place, same order
  const setCfg = (k, v) => { p.config = { ...(p.config || {}), [k]: v }; touch(); };
  const delCfg = (k) => { const c = { ...(p.config || {}) }; delete c[k]; p.config = c; touch(); paint(); };
  const schema = schemas[p.module];
  if (!schema) loadSchema(p.module);
  const fields = Array.isArray(schema?.fields) ? schema.fields : [];
  const label = (f) => f.display_name || f.id;
  const ofType = (t) => fields.filter((f) => f.field_type === t);
  const ml = models[name];

  const secretField = (f) => {
    const v = cfg[f.id];
    const m = typeof v === "string" && v.match(PLACEHOLDER);
    if (m) return field(label(f), secretWidget(m[1]), `Stored in keys.env as \`${m[1]}\`.`);
    const id = `${S.scope}:${i}:${f.id}`;
    if (v === MASKED && !replacing.has(id)) return maskedField(f, id);
    const place = (n) => { replacing.delete(id); setCfg(f.id, `\${${n}}`); paint(); toast(`Saved the key as \`${n}\`. Save changes to use it.`); };
    return h("div", { class: "field" }, h("span", { text: label(f) }),
      newKeyWidget(id, envName(name, f.id === "api_key" ? "API_KEY" : f.id), place),
      h("small", { class: "hint", text: "The key goes to keys.env; the settings file only gets its name." }));
  };
  const maskedField = (f, id) => field(label(f),
    h("div", { class: "secret" }, h("code", { class: "value", text: MASKED }),
      btn("Move to keys.env", moveKeys, { icon: "key", small: true }),
      btn("Replace", () => { replacing.add(id); paint(); }, { icon: "pencil", only: true, kind: "ghost" })),
    "Written into this settings file, so it's hidden here.");
  const textField = (f) => cfg[f.id] === MASKED ? secretField(f) : field(label(f), textIn(cfg[f.id] === undefined ? "" : show(cfg[f.id]), (val) => (val.trim() ? setCfg(f.id, val.trim()) : delCfg(f.id)), { class: "code", placeholder: f.default ? `${f.default} (default)` : "default" }));
  const boolField = (f) => {
    const v = cfg[f.id];
    return switchRow(label(f), f.prompt, v === undefined ? truthy(f.default) : truthy(v), (enabled) => setCfg(f.id, typeof v === "boolean" ? enabled : String(enabled)));
  };
  const choiceField = (f) => field(label(f), h("select", { onchange: (e) => (e.target.value ? setCfg(f.id, e.target.value) : delCfg(f.id)) },
    h("option", { value: "", text: f.default ? `Provider default (${f.default})` : "Provider default" }),
    (f.choices || []).map((c) => h("option", { value: c, selected: String(cfg[f.id]) === c, text: c }))));

  const conn = [...ofType("secret").map(secretField), ...ofType("text").map(textField)];
  const behave = [...ofType("boolean").map(boolField), ...ofType("choice").map(choiceField)];
  const shown = new Set(["default_model", "priority", ...fields.map((f) => f.id)]);
  const rest = Object.entries(cfg).filter(([k]) => !shown.has(k));
  const connTag = ml === "loading" ? tag("checking…") : ml?.models ? tag(`connected, ${ml.models.length} models`, "lit") : ml ? tag("models not listed", "wait") : null;
  const type = String(p.module).replace(/^provider-/, "");
  const move = (d) => { const a = [...ordered]; [a[i + d], a[i]] = [a[i], a[i + d]]; commit(a); };

  return h("li", {},
    h("div", { class: "rank", text: String(i + 1) }),
    h("div", { class: "prov-body" },
      h("div", { class: "prov-head" },
        h("h3", { text: name }),
        type !== name ? h("code", { class: "type", text: type }) : null,
        i === 0 ? tag("default", "act") : null,
        connTag,
        h("span", { class: "spacer" }),
        btn("Test connection", () => { delete models[name]; loadModels(name); paint(); }, { icon: "plug", small: true }),
        btn("Diagnose", () => openDiagnose(name), { icon: "pulse", small: true }),
        btn(`Move ${name} up`, () => move(-1), { icon: "up", only: true, kind: "ghost", disabled: i === 0 }),
        btn(`Move ${name} down`, () => move(1), { icon: "down", only: true, kind: "ghost", disabled: i === ordered.length - 1 }),
        btn(`Remove ${name}`, () => { if (confirm(`Remove ${name}? Roles it serves fall back to the next model in their list.`)) commit(ordered.filter((x) => x !== p)); }, { icon: "trash", only: true, kind: "ghost danger" })),
      ml && ml !== "loading" && !ml.models && ml.error ? h("p", { class: "notice stop", text: ml.error }) : null,
      ml && ml !== "loading" && ml.warnings?.length ? h("div", { class: "notice wait" }, warningList(ml.warnings)) : null,
      serves[name]?.length ? h("p", { class: "hint", text: `Gets the work for ${plural(serves[name].length, "role")} in ${inUseMatrix}: ${serves[name].join(", ")}.` }) : null,
      h("div", { class: "fields" },
        field("Name", textIn(p.id, (v) => { if (v.trim()) p.id = v.trim(); else delete p.id; touch(); }), "How routing and `amplifier provider edit` refer to it."),
        field("Default model", textIn(cfg.default_model, (v) => setCfg("default_model", v), { class: "code", list: `pm-${i}`, onfocus: () => loadModels(name) }), ml?.models ? `${ml.models.length} models available. Start typing to filter.` : "Used when routing doesn't choose one."),
        h("datalist", { id: `pm-${i}` }, (ml?.models || []).map((m) => h("option", { value: m })))),
      conn.length ? h("div", { class: "group" }, h("h4", { text: "Connection" }), h("div", { class: "fields" }, conn)) : null,
      behave.length ? h("div", { class: "group" }, h("h4", { text: "Behaviour" }), behave) : null,
      h("div", { class: "group" },
        h("h4", { text: rest.length ? `Other options (${rest.length})` : "Other options" }),
        kvEditor(rest, { onSet: setCfg, onDel: delCfg, onAdd: (k, v) => { setCfg(k, v); paint(); } }),
        p.source ? h("p", { class: "hint", md: `Installed from \`${p.source}\`` }) : null)));
}

export default {
  id: "providers", title: "Providers", crumb: "The AI services Amplifier can call, in the order it tries them.", group: "Models", icon: "chip", scoped: true,
  view() {
    const ordered = providersIn(S.draft);
    const commit = (arr) => {
      renumber(arr);
      set(["config", "providers"], arr);
      paint();
    };
    const inUseMatrix = layered(["routing", "matrix"]).val || defaults().matrix;
    const serves = {};
    if (R.result?.roles && R.result.matrix === inUseMatrix) for (const r of R.result.roles) if (r.winner !== null) (serves[r.candidates[r.winner].provider] ||= []).push(r.name);
    ensureRouting();

    const known = S.state.known_providers;
    const mod = h("select", { "aria-label": "Provider type" }, known.map((k) => h("option", { value: k.module, text: k.module.replace(/^provider-/, "") })));
    const id = h("input", { type: "text", placeholder: "e.g. anthropic-work", "aria-label": "Name" });
    const model = h("input", { type: "text", class: "code", placeholder: "optional", "aria-label": "Default model" });
    const add = () => {
      const k = known.find((x) => x.module === mod.value);
      const nm = id.value.trim() || k.module.replace(/^provider-/, "");
      if (ordered.some((p) => providerName(p) === nm)) return toast(`You already have a provider called ${nm}.`);
      commit([...ordered, { id: nm, module: k.module, source: k.source, config: { ...(model.value.trim() ? { default_model: model.value.trim() } : {}) } }]);
      toast(`Added ${nm}. Add its key, then save.`);
    };
    return [
      scopeNote(),
      ordered.length
        ? h("ol", { class: "panel chainlist" }, ordered.map((p, i) => providerItem(p, i, ordered, commit, serves, inUseMatrix)))
        : h("p", { class: "notice", text: S.scope === "global" ? "No providers yet. Add your first one below." : "This file doesn't change any providers." }),
      panel("Add a provider", "Amplifier installs it the next time it starts. For sign-in providers like ChatGPT or Copilot, use `amplifier provider add`.",
        h("div", { class: "pad" }, h("div", { class: "fields" }, field("Type", mod), field("Name", id), field("Default model", model)), h("div", { class: "actions" }, btn("Add provider", add, { icon: "plus", kind: "primary" })))),
    ];
  },
};

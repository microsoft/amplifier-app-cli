// Home, built for someone new: what's wrong and the button that fixes it, whether
// each provider actually answers (checked on request, since it sends the key), the
// few settings people change most, and upkeep.
import { h, icon, btn, link, lamp, tag, field, panel, plural, fmtAgo } from "../lib/dom.js";
import { S, getIn, setIn, layered, globalScope, providerName, providersIn, renumber, models, loadModels, quickSave, defaults } from "../lib/store.js";
import { openDiagnose } from "../lib/diagnose.js";
import { jobs, startJob, confirmUpdate, jobConsole, jobsOff } from "../lib/jobs.js";
import { R, ensureRouting } from "./routing.js";
import { warningList } from "./providers.js";
import { notifyControls } from "./notifications.js";

// what needs the user, worst first; also drives the lamps in the nav
export function attention() {
  const st = S.state?.status;
  if (!st || st.error) return [];
  const out = [];
  const stop = (text, go, action, ic) => out.push({ level: "stop", text, go, action, icon: ic });
  if (!st.providers.length) stop("No providers are set up, so Amplifier can't start a session.", "providers", "Add a provider", "plus");
  for (const p of st.providers) for (const k of p.shadowed_keys || []) stop(`${p.name} is using a different \`${k}\` from your shell or system environment, not the one you saved. Amplifier never overrides an existing environment variable.`, "keys", "See how to fix", "key");
  for (const p of st.providers) for (const k of p.missing_keys) stop(`${p.name} has no API key (\`${k}\` is empty).`, "keys", "Add the key", "key");
  for (const p of st.providers) {
    const ml = models[p.name];
    if (ml && ml !== "loading" && !ml.models && !p.missing_keys.length && !(p.shadowed_keys || []).length) stop(`${p.name} isn't answering: ${ml.error}`, "providers", "Fix the connection", "plug");
  }
  if (st.routing.missing) stop(`The routing matrix \`${st.routing.matrix}\` isn't on this machine.`, "routing", "Choose a matrix", "route");
  else if (st.routing.unserved?.length) out.push({ level: "wait", text: `${plural(st.routing.unserved.length, "role")} in ${st.routing.matrix} have no model you can use: ${st.routing.unserved.join(", ")}.`, go: "routing", action: "Fix routing", icon: "route" });
  const every = st.updates.every_hours || defaults().update_check_hours;
  if (st.updates.hours_ago === null || st.updates.hours_ago > every * 6) out.push({ level: "wait", text: st.updates.hours_ago === null ? "Amplifier has never checked for updates." : `Amplifier last checked for updates ${fmtAgo(st.updates.hours_ago)}.`, go: "update", action: "Check now", icon: "refresh" });
  return out;
}

// a provider entry from the project's own settings file: checking it sends your key to
// whatever address that file (often from a cloned repo) names
const fromProject = (name) => (S.state.scopes.find((sc) => sc.name === "project")?.doc?.config?.providers || []).some((p) => p && providerName(p) === name);
const checkProvider = (name) => { delete models[name]; loadModels(name); };

function connectionsPanel(st) {
  const rows = st.providers.map((p) => {
    const ml = models[p.name];
    const checking = ml === "loading";
    const ok = ml && !checking && ml.models;
    const failed = ml && !checking && !ml.models;
    const shadow = (p.shadowed_keys || [])[0];
    const project = fromProject(p.name);
    const detail = !ml ? (p.missing_keys.length ? "No API key yet." : "Not checked yet.") : checking ? "Checking…" : ok ? `Connected. ${plural(ml.models.length, "model")} available (${ml.seconds}s).` : p.missing_keys.length ? "No API key yet." : shadow ? `${ml.error.replace(/\.$/, "")}. The ${shadow} in your environment is overriding the saved key.` : ml.error;
    return h("div", { class: "row-item" },
      lamp(!ml || checking ? (p.missing_keys.length ? "dead" : "unconfigured") : ok ? "winner" : "dead"),
      h("div", { class: "row-main" },
        h("div", { class: "row-title" }, h("strong", { text: p.name }), p.default ? tag("default", "act") : null, project ? tag("from this project", "wait") : null, p.model ? h("code", { class: "hint", text: p.model }) : null),
        h("div", { class: `hint${failed ? " bad" : ""}`, text: detail }),
        project && !ml ? h("div", { class: "hint", text: "Set in this project's settings file. Checking sends your key to the address that file names." }) : null,
        ok && ml.warnings?.length ? warningList(ml.warnings) : null),
      h("div", { class: "row-actions" },
        p.missing_keys.length ? link("Add key", "#keys", { icon: "key", kind: "small primary" })
          : failed ? btn("Diagnose", () => openDiagnose(p.name), { icon: "pulse", small: true, kind: "primary" }) : null,
        ok ? btn(`Diagnose ${p.name}`, () => openDiagnose(p.name), { icon: "pulse", only: true, kind: "ghost" }) : null,
        p.missing_keys.length ? null : ml ? btn(`Check ${p.name} again`, () => checkProvider(p.name), { icon: "refresh", only: true, kind: "ghost", disabled: checking }) : btn("Check connection", () => checkProvider(p.name), { icon: "plug", small: true })));
  });
  return h("section", { class: "panel" },
    h("header", { class: "panel-head row-head" }, h("div", {}, h("h3", { text: "Connections" }), h("p", { class: "hint", text: "Checking asks a provider for its model list. That proves the key works without spending tokens." })),
      st.providers.length > 1 ? btn("Check all", () => st.providers.forEach((p) => checkProvider(p.name)), { icon: "plug", small: true }) : null),
    rows.length ? h("div", { class: "rows" }, rows) : h("div", { class: "pad" }, h("p", { class: "hint", text: "No providers yet." }), link("Add a provider", "#providers", { icon: "plus", kind: "primary" })),
    h("div", { class: "pad tight" }, link("Manage providers", "#providers", { icon: "chip", kind: "ghost small" })));
}

function quickSettingsPanel(st) {
  const g = globalScope().doc;
  ensureRouting();
  const current = getIn(g, ["routing", "matrix"]) || defaults().matrix;
  const names = R.matrices?.length ? R.matrices.map((m) => m.name) : [current];
  const routed = layered(["routing", "matrix"]);
  const ordered = providersIn(g);
  const makeDefault = (name) => quickSave((d) => {
    const list = providersIn(d);
    list.unshift(...list.splice(list.findIndex((p) => providerName(p) === name), 1));
    renumber(list);
    setIn(d, ["config", "providers"], list);
  }, `${name} is now the default provider.`);
  return panel("Quick settings", "These save as soon as you change them.",
    h("div", { class: "pad" },
      ordered.length > 1 ? field("Default provider", h("select", { onchange: (e) => makeDefault(e.target.value) }, ordered.map((p, i) => h("option", { value: providerName(p), selected: i === 0, text: providerName(p) }))), "Used whenever routing doesn't pick one.") : null,
      field("Routing", h("select", { onchange: (e) => quickSave((d) => setIn(d, ["routing", "matrix"], e.target.value), `Now using ${e.target.value}.`) }, names.map((n) => h("option", { value: n, selected: n === current, text: n }))),
        routed.from && routed.from !== "global" ? `This project uses ${routed.val} instead (set in ${routed.from} settings).` : st.routing.missing ? "This matrix isn't on this machine." : `${st.routing.roles - st.routing.unserved.length} of ${plural(st.routing.roles, "role")} have a model. Open Routing to see which.`),
      notifyControls(g, (mutate) => quickSave(mutate))));
}

function maintenancePanel(st) {
  const check = jobs["update-check"];
  const run = jobs.update;
  const off = jobsOff();
  const busy = check?.running || run?.running;
  const upd = st.updates;
  return panel("Maintenance", null,
    h("div", { class: "rows" },
      h("div", { class: "row-item" }, icon("refresh"),
        h("div", { class: "row-main" }, h("strong", { text: "Updates" }), h("div", { class: "hint", text: `Version ${st.version.display}. ${upd.hours_ago === null ? "Never checked." : `Checked ${fmtAgo(upd.hours_ago)}.`}` })),
        h("div", { class: "row-actions" },
          btn(check?.running ? "Checking…" : "Check", () => startJob("update-check"), { icon: "refresh", small: true, disabled: busy }),
          btn(run?.running ? "Updating…" : "Update", confirmUpdate, { icon: "play", small: true, disabled: busy || off, title: off }))),
      h("div", { class: "row-item" }, icon("undo"),
        h("div", { class: "row-main" }, h("strong", { text: "Reset" }), h("div", { class: "hint", text: "Clear caches or start over. You choose what to keep, and preview it first." })),
        h("div", { class: "row-actions" }, link("Reset…", "#reset", { icon: "undo", kind: "small" })))),
    run || check ? h("div", { class: "pad" }, jobConsole(run || check)) : null);
}

function setupSteps() {
  const step = (n, title, hint, go, action) => h("li", {}, h("span", { class: "rank", text: String(n) }), h("div", { class: "row-main" }, h("strong", { text: title }), h("div", { class: "hint", md: hint })), link(action, `#${go}`, { kind: n === 1 ? "primary small" : "small" }));
  return panel("Get set up", "Five minutes, in this order. You can change any of it later.",
    h("ol", { class: "steps" },
      step(1, "Add a provider", "The AI service Amplifier will call, such as Anthropic or OpenAI.", "providers", "Add a provider"),
      step(2, "Add its API key", "Paste the key from your provider's dashboard. It's stored in keys.env, never in settings.", "keys", "Add a key"),
      step(3, "Check the connection", "Come back here and check each provider's connection. It doesn't spend tokens.", "home", "Check"),
      step(4, "Choose routing", "Pick which models handle coding, quick tasks and deep reasoning.", "routing", "Choose"),
      step(5, "Turn on notifications", "Optional: get told when Amplifier is waiting for you.", "notifications", "Set up")));
}

function morePanel(st) {
  const rows = [
    ["bundles", "package", "Bundles", `${st.bundle.active}${st.bundle.always ? `, plus ${st.bundle.always} always loaded` : ""}`],
    ["keys", "key", "API keys", "Masked; reveal one at a time"],
    ["file", "file", "Settings file", "Edit the YAML directly"],
  ];
  return panel("More", null, h("nav", { class: "rows" }, rows.map(([go, ic, title, hint]) =>
    h("a", { class: "row-item link", href: `#${go}` }, icon(ic), h("div", { class: "row-main" }, h("strong", { text: title }), h("div", { class: "hint", text: hint })), icon("chevron")))));
}

export default {
  id: "home", title: "Home", crumb: null, group: "", icon: "home",
  view() {
    const st = S.state.status;
    if (!st) return [h("p", { class: "quiet-note", text: "Checking Amplifier…" })];
    if (st.error) return [h("p", { class: "notice stop", text: st.error })];
    const att = attention();
    const worst = att.some((a) => a.level === "stop") ? "dead" : att.length ? "winner unverified" : "winner";
    const top = att[0];
    return [
      h("section", { class: "hero" },
        h("div", { class: "hero-status" }, lamp(worst),
          h("div", {},
            h("h2", { text: !st.providers.length ? "Welcome to Amplifier" : att.length ? `${plural(att.length, "thing needs", "things need")} your attention` : "Amplifier is ready" }),
            att.length ? h("ul", { class: "issues" }, att.map((a) => h("li", { md: a.text }))) : h("p", { text: "Your providers, routing and settings are in order." }))),
        top ? h("div", { class: "hero-actions" }, link(top.action, `#${top.go}`, { icon: top.icon, kind: "primary" })) : null),
      !st.providers.length ? setupSteps() : null,
      h("div", { class: "home-grid" },
        h("div", { class: "col" }, connectionsPanel(st), maintenancePanel(st)),
        h("div", { class: "col" }, quickSettingsPanel(st), morePanel(st))),
    ];
  },
};

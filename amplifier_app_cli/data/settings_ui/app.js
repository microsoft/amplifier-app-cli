// Amplifier settings: boot, navigation, hash routing, the save bar and change detection.
// Each page module exports {id, title, crumb, group, icon, scoped?, arrive?, view(scope)}.
import { api, onHalt } from "./lib/api.js";
import { $, h, icon, btn, link, lamp, toast } from "./lib/dom.js";
import { S, SCOPES, cur, dirty, rawDirty, changed, adopt, reload, saveDraft, bindView, on } from "./lib/store.js";
import home, { attention } from "./pages/home.js";
import providers from "./pages/providers.js";
import routing from "./pages/routing.js";
import bundles from "./pages/bundles.js";
import notifications from "./pages/notifications.js";
import keys from "./pages/keys.js";
import update from "./pages/update.js";
import reset from "./pages/reset.js";
import file from "./pages/file.js";

const PAGES = Object.fromEntries([home, providers, routing, bundles, notifications, keys, update, reset, file].map((p) => [p.id, p]));
const GROUPS = ["", "Models", "Sessions", "Access", "Maintenance", "Advanced"];
const page = () => PAGES[S.tab];

// ---------------------------------------------------------------- rendering
function renderNav() {
  const att = attention();
  const flagFor = (id) => att.find((a) => a.go === id || (id === "providers" && a.go === "keys"));
  $("nav").replaceChildren(...GROUPS.map((group) => h("div", { class: "nav-group" },
    group ? h("span", { text: group }) : null,
    Object.values(PAGES).filter((p) => p.group === group).map((p) => {
      const flag = flagFor(p.id);
      return h("a", { href: `#${p.id}`, "aria-current": p.id === S.tab ? "page" : null }, icon(p.icon), h("span", { text: p.title }), flag ? lamp(`sm ${flag.level === "stop" ? "dead" : "winner unverified"}`) : null);
    }))));
  const v = S.state?.status?.version;
  if (v) $("version").textContent = `Version ${v.display}`;
}

function renderHead() {
  const { title, crumb, scoped } = page();
  const s = S.state && cur();
  const away = S.state?.scopes.find((sc) => !sc.available && sc.unavailable_reason);
  const scopebox = scoped && s
    ? h("div", { class: "scopebox" },
        h("div", { class: "scope", role: "radiogroup", "aria-label": "Which settings file to edit" },
          S.state.scopes.map((sc) => h("button", {
            type: "button", role: "radio", "aria-checked": String(sc.name === S.scope), disabled: !sc.available,
            title: sc.available ? SCOPES[sc.name][1] : sc.unavailable_reason || "Run amplifier settings inside a project folder to edit project settings.",
            onclick: () => switchScope(sc.name), text: SCOPES[sc.name][0],
          }))),
        h("span", { class: "hint", text: `${SCOPES[s.name][1]}: ${s.path}` }),
        away ? h("span", { class: "hint", text: `${SCOPES[away.name][0]} isn't available: ${away.unavailable_reason}` }) : null)
    : null;
  $("pagehead").replaceChildren(h("div", { class: "pagehead-inner" }, h("div", {}, h("h1", { text: title }), crumb ? h("p", { class: "crumb", text: crumb }) : null), scopebox));
}

function renderMain() {
  if (!S.state) return;
  const s = cur();
  const p = page();
  const banner = HALTED === "restart" ? restartBanner()
    : LIVE.stale ? staleBanner() : null;
  const kids = p.scoped && s.error && !p.readsBrokenFile
    ? [h("div", { class: "notice stop" }, h("p", {}, h("strong", { text: "This settings file can't be read, so editing is turned off. " }), s.error), link("Fix the file", "#file", { icon: "file" }))]
    : p.scoped && p.id !== "file" && rawDirty()
    ? [h("div", { class: "notice wait" }, h("p", { text: "You have unsaved changes in the settings file. Save or discard them before editing here." }), link("Back to the file", "#file", { icon: "file" }))]
    : p.view(s);
  $("main").replaceChildren(...[banner, ...kids].flat().filter(Boolean));
}
const restartBanner = () => h("div", { class: "notice stop" }, h("strong", { md: "Amplifier was updated or reset. Run `amplifier settings` again." }));
const staleBanner = () => h("div", { class: "notice wait", id: "stale" }, h("span", { text: "Settings changed outside this page while you were editing." }),
  h("div", { class: "actions" },
    btn("Load the new settings", () => { LIVE.stale = false; reload({ message: "Loaded the new settings. Your unsaved edits were discarded." }); }, { icon: "refresh", small: true }),
    btn("Keep editing", () => { LIVE.stale = false; $("stale")?.remove(); }, { small: true, kind: "ghost" })));

function render() {
  renderNav();
  renderHead();
  changed();
  renderMain();
}
bindView({ main: renderMain, nav: renderNav, all: render });

function switchScope(name) {
  if (name === S.scope || (dirty() && !confirm("Switch files and lose your unsaved changes?"))) return;
  S.scope = name;
  adopt(S.state);
}

function onHash() {
  const id = location.hash.slice(1);
  const next = PAGES[id] ? id : "home";
  if (next !== S.tab) PAGES[next].arrive?.();
  S.tab = next;
  if (S.state) { render(); $("main").focus({ preventScroll: true }); window.scrollTo(0, 0); }
}

// ---------------------------------------------------------------- change detection
// Settings files and keys.env can change while this page is open (the CLI, an editor).
// /api/changes is a cheap fingerprint, polled while visible and on return to the page.
// Unsaved edits are never dropped: a change found while editing shows a banner.
const LIVE = { fingerprint: null, stale: false, checking: false, timer: null };
const typing = () => ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
const markStale = () => { if (!LIVE.stale) { LIVE.stale = true; $("main").prepend(staleBanner()); } };

async function fingerprint() {
  try { return (await api("/api/changes")).fingerprint; } catch { return null; } // server gone: keep the page
}
async function checkForChanges() {
  if (HALTED || LIVE.checking || document.visibilityState !== "visible" || !S.state) return;
  LIVE.checking = true;
  try {
    const fp = await fingerprint();
    if (fp === null) return;
    if (LIVE.fingerprint === null || fp === LIVE.fingerprint) { LIVE.fingerprint = fp; return; }
    if (dirty()) return markStale();
    if (typing()) return; // try again on the next tick, after the field is left
    const next = await api("/api/state").catch(() => null);
    if (!next) return;
    if (dirty() || typing()) return markStale(); // edits started while it loaded
    adopt(next, true);
    toast("Updated: something changed outside this page.");
  } finally { LIVE.checking = false; }
}
// after anything this page loads or saves, the next fingerprint is the new baseline
on("adopt", (fromServer) => { if (fromServer) Object.assign(LIVE, { stale: false, fingerprint: null }); });

// ---------------------------------------------------------------- stopping
// "expired": the session isn't valid (401), so nothing else can work. "restart": an
// update or reset replaced Amplifier (503); job output still polls /api/jobs.
let HALTED = null;
onHalt((why) => {
  if (HALTED === "expired") return;
  HALTED = why;
  clearInterval(LIVE.timer);
  if (why === "restart") return S.state ? renderMain() : $("main").replaceChildren(restartBanner());
  S.state = null; // nothing left to save
  document.body.replaceChildren(h("main", { class: "halted" },
    h("section", { class: "panel pad" }, h("h1", { text: "This settings page has expired." }), h("p", { md: "Run `amplifier settings` again to open a new one." }))));
});

// ---------------------------------------------------------------- boot
async function save() {
  $("save").disabled = true;
  await saveDraft();
  $("save").disabled = false;
}

window.addEventListener("hashchange", onHash);
window.addEventListener("beforeunload", (e) => { if (S.state && dirty()) e.preventDefault(); });
window.addEventListener("focus", checkForChanges);
document.addEventListener("visibilitychange", checkForChanges);
document.addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); if (S.state && dirty()) save(); } });
$("save").addEventListener("click", save);
$("discard").addEventListener("click", () => adopt(S.state));

onHash();
api("/api/state")
  .then((st) => { adopt(st, true); LIVE.timer = setInterval(checkForChanges, 4000); checkForChanges(); })
  .catch((e) => { if (!e.halt) $("main").replaceChildren(h("p", { class: "notice stop", md: e.message })); });

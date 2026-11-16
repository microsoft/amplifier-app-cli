// Reset: choose what to clear, preview the plan, then reset.
import { api } from "../lib/api.js";
import { h, btn, lede, panel, switchRow, plural, fmtBytes } from "../lib/dom.js";
import { paint } from "../lib/store.js";
import { jobs, startJob, jobConsole, jobsOff } from "../lib/jobs.js";

const V = { info: null, remove: null, full: false, loading: false, error: null };
async function measure() {
  V.loading = true;
  try {
    V.info = await api("/api/reset/categories");
    V.remove ||= new Set(V.info.categories.filter((c) => c.default_remove).map((c) => c.name));
  } catch (e) { V.error = e.message; }
  V.loading = false;
  paint("reset");
}
const WARN = { projects: "Your session history. Clearing it can't be undone.", keys: "You'd need to add every API key again.", settings: "You'd need to set Amplifier up again." };

export default {
  id: "reset", title: "Reset", crumb: "Start fresh without losing what you want to keep.", group: "Maintenance", icon: "undo",
  arrive() { V.info = null; V.error = null; }, // measure again on every visit
  view() {
    if (!V.info) {
      if (V.error) return [h("div", { class: "notice stop" }, h("p", { text: V.error }), btn("Try again", () => { V.error = null; paint("reset"); }, { icon: "refresh", small: true }))];
      if (!V.loading) measure();
      return [h("p", { class: "quiet-note", text: "Measuring what's in your Amplifier folder…" })];
    }
    const { categories, unmanaged, home } = V.info;
    const opts = V.full ? { full: true } : { remove: [...V.remove] };
    const plan = jobs["reset-plan"];
    const previewed = plan && plan.options === JSON.stringify(opts) && !plan.running && plan.exit_code === 0;
    const busy = plan?.running || jobs.reset?.running;
    const off = jobsOff();
    const removing = categories.filter((c) => V.full || V.remove.has(c.name));
    const total = removing.reduce((n, c) => (n === null || c.bytes === null ? null : n + c.bytes), 0);
    const resetNow = () => {
      if (confirm(`Reset Amplifier now?\n\nThis clears ${V.full ? "your entire Amplifier folder" : removing.map((c) => c.name).join(", ")}${total ? ` (${fmtBytes(total)})` : ""} and reinstalls Amplifier. This page stops while it reinstalls; reopen it with amplifier settings.`)) startJob("reset", { ...opts, plan_id: plan.id }); // the server checks it previewed these options
    };
    return [
      off ? h("p", { class: "notice wait", text: off }) : null,
      lede("Reset reinstalls Amplifier and clears what you choose from your Amplifier folder. The safe default only clears things that rebuild themselves."),
      panel("What to clear", `From ${home}`,
        h("ul", { class: "choices" }, categories.map((c) => h("li", {},
          h("input", { type: "checkbox", id: `rc-${c.name}`, checked: V.full || V.remove.has(c.name), disabled: V.full, onchange: (e) => { V.remove[e.target.checked ? "add" : "delete"](c.name); paint(); } }),
          h("label", { for: `rc-${c.name}` }, h("strong", { text: c.name[0].toUpperCase() + c.name.slice(1) }), h("span", { class: "hint", text: ` ${c.description}` }), WARN[c.name] ? h("div", { class: "hint", text: WARN[c.name] }) : null),
          h("span", { class: "size", text: c.exists ? fmtBytes(c.bytes) : "empty" })))),
        h("details", { class: "pad" }, h("summary", { text: `Always kept: ${plural(unmanaged.length, "other item")} reset doesn't manage` }), h("p", { class: "hint", text: unmanaged.join(", ") || "Nothing else." }))),
      h("section", { class: "panel danger-zone" }, h("div", { class: "pad" },
        switchRow("Full reset", "Deletes the whole Amplifier folder, including the items listed as always kept.", V.full, (full) => { V.full = full; paint(); }))),
      h("div", { class: "actions" },
        btn("Preview reset", () => startJob("reset-plan", opts), { icon: "eye", disabled: busy || (!V.full && !V.remove.size) }),
        btn("Reset now", resetNow, { icon: "undo", kind: "danger primary", disabled: busy || !previewed || off, title: off || (previewed ? null : "Preview first, so you can see exactly what will happen.") }),
        h("span", { class: "hint", text: previewed ? `Clears ${fmtBytes(total)}.` : "Preview first to see exactly what will happen." })),
      jobConsole(jobs.reset || plan),
    ];
  },
};

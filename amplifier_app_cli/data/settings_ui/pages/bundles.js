// Bundles: the main bundle, bundles always loaded on top, and short names.
import { h, field, textIn, panel, listEditor } from "../lib/dom.js";
import { S, get, set, del, layered, paint, scopeNote, defaults } from "../lib/store.js";

const short = (u) => u.replace(/^git\+https:\/\/github\.com\//, "").replace(/@main$/, "");

export default {
  id: "bundles", title: "Bundles", crumb: "The tools, agents and behaviours every session starts with.", group: "Sessions", icon: "package", scoped: true,
  view() {
    const added = get(["bundle", "added"], {}) || {};
    const app = get(["bundle", "app"], []) || [];
    const inherited = layered(["bundle", "active"], { upTo: S.scope }).val;
    const fallback = defaults().bundle;
    const names = new Set([fallback, ...S.state.scopes.flatMap((sc) => Object.keys(sc.doc?.bundle?.added || {})), ...Object.keys(added)]);
    const setApp = (a) => { a.length ? set(["bundle", "app"], a) : del(["bundle", "app"]); paint(); };
    return [
      scopeNote(),
      panel("Main bundle", null, h("div", { class: "pad" },
        field("Bundle", textIn(get(["bundle", "active"], ""), (v) => (v.trim() ? set(["bundle", "active"], v.trim()) : del(["bundle", "active"])), { list: "bundle-names", placeholder: inherited ? `inherits ${inherited}` : fallback ? `${fallback} (default)` : "default" }), "Leave empty for the default. Same as `amplifier bundle use`."),
        h("datalist", { id: "bundle-names" }, [...names].filter(Boolean).map((n) => h("option", { value: n }))))),
      panel(`Always loaded (${app.length})`, "Added on top of the main bundle in every session, in this order.",
        listEditor({ items: app, label: "Bundle address", placeholder: "git+https://github.com/org/repo@main", show: (u) => h("code", { title: u, text: short(u) }),
          onRemove: (_, i) => setApp(app.filter((__, j) => j !== i)),
          onAdd: (u) => { if (!app.includes(u)) setApp([...app, u]); } })),
      panel("Short names", "Names you can switch to with `amplifier bundle use <name>`.",
        listEditor({ items: Object.entries(added), two: true, label: "Bundle", placeholder: ["short name", "git+https://github.com/org/repo@main"], show: ([k, v]) => h("div", { class: "two" }, h("strong", { text: k }), h("code", { title: v, text: short(v) })),
          onRemove: ([k]) => { del(["bundle", "added", k]); paint(); },
          onAdd: (k, v) => { set(["bundle", "added", k], v); paint(); } })),
    ];
  },
};

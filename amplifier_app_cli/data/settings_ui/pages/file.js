// Settings file: the YAML itself, for anything the other pages don't cover. The text
// is fetched only here (it can hold secrets) and is part of the unsaved-changes draft.
import { api } from "../lib/api.js";
import { h, btn } from "../lib/dom.js";
import { S, docDirty, changed, paint, writeScope, on } from "../lib/store.js";

const F = { loading: null, error: null, seq: 0 }; // error: {scope, message}
on("adopt", () => { F.seq++; F.loading = null; }); // a text read before this is out of date

async function load(scope) {
  const seq = F.seq;
  Object.assign(F, { loading: scope, error: null });
  try {
    const { yaml, version } = await api(`/api/scope/raw?scope=${encodeURIComponent(scope)}`);
    if (seq === F.seq && S.scope === scope) S.raw = { scope, yaml, version, text: yaml };
  } catch (e) {
    if (seq === F.seq) F.error = { scope, message: e.message };
  }
  if (seq !== F.seq) return paint("file"); // read again for the new state
  F.loading = null;
  paint("file");
}

export default {
  id: "file", title: "Settings file", crumb: "The file itself, for anything the other pages don't cover.", group: "Advanced", icon: "file", scoped: true, readsBrokenFile: true,
  view(s) {
    const locked = docDirty();
    const notes = [
      locked ? h("p", { class: "notice wait", text: "You have unsaved changes on other pages. Save or discard them before editing the file directly." }) : null,
      s.error ? h("p", { class: "notice stop", text: s.error }) : null,
    ];
    if (S.raw?.scope !== S.scope) {
      if (F.error?.scope === S.scope) return [...notes, h("div", { class: "notice stop" }, h("p", { text: F.error.message }), btn("Try again", () => load(S.scope), { icon: "refresh", small: true }))];
      if (F.loading !== S.scope) load(S.scope);
      return [...notes, h("p", { class: "quiet-note", text: "Reading the file…" })];
    }
    const ta = h("textarea", { class: "yaml", spellcheck: false, disabled: locked, "aria-label": `Contents of ${s.path}`, oninput: (e) => { S.raw.text = e.target.value; changed(); } });
    ta.value = S.raw.text;
    return [
      ...notes,
      h("section", { class: "panel" }, ta,
        h("div", { class: "adder" }, h("span", { class: "hint", text: "Amplifier sorts keys and drops comments whenever it saves this file." }),
          btn(s.exists ? "Save file" : "Create file", () => writeScope(S.scope, { yaml: S.raw.text, version: S.raw.version }, "Saved the settings file."), { icon: "save", kind: "primary", disabled: locked }))),
    ];
  },
};

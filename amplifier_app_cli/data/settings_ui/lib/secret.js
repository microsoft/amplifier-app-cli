// A masked keys.env value with show / copy / change, used on Providers, API keys
// and Notifications. Values are only fetched when the user asks to see one.
// newKeyWidget adds a key the same way: the value goes to keys.env under an explicit
// name, and the settings document only ever gets ${NAME}.
import { api } from "./api.js";
import { h, btn, toast } from "./dom.js";
import { keyInfo, adoptKeys, paint } from "./store.js";

const revealed = {};
let editing = null;

export const reveal = (name, value) => { revealed[name] = value; };
export function editKey(name) { editing = name; paint(); }

export async function saveKey(name, value) {
  try {
    adoptKeys(await api("/api/key", { name, value }));
    return true;
  } catch (e) {
    toast(e.message);
    return false;
  }
}

export function secretWidget(name, emptyText = "Not set") {
  const info = keyInfo(name) || { stored: false, in_env: false, preview: null, secret: true };
  const has = info.stored || info.in_env;
  const shown = revealed[name];
  if (editing === name) {
    const input = h("input", { type: "password", autocomplete: "off", placeholder: "Paste the new value", "aria-label": `New value for ${name}` });
    const saveIt = async () => {
      editing = null;
      delete revealed[name];
      if (await saveKey(name, input.value)) toast(`Saved \`${name}\`.`);
      else { editing = name; }
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") saveIt(); });
    setTimeout(() => input.focus(), 0);
    return h("div", { class: "secret" }, input, btn("Save", saveIt, { icon: "check", kind: "primary" }), btn("Cancel", () => { editing = null; paint(); }, { icon: "x", only: true, kind: "ghost" }));
  }
  const toggleShow = async () => {
    if (shown !== undefined) delete revealed[name];
    else {
      try { revealed[name] = (await api("/api/key/reveal", { name })).value; } catch (e) { return toast(e.message); }
    }
    paint();
  };
  return h("div", { class: "secret" },
    h("code", { class: `value${has ? "" : " empty"}`, title: has ? (info.stored ? "Saved in keys.env" : "Set in the shell that started this page") : null, text: shown ?? (has ? info.preview : emptyText) }),
    has ? btn(shown !== undefined ? "Hide" : "Show", toggleShow, { icon: shown !== undefined ? "eyeOff" : "eye", only: true, kind: "ghost", "aria-pressed": String(shown !== undefined) }) : null,
    shown !== undefined ? btn("Copy", () => navigator.clipboard.writeText(shown).then(() => toast("Copied.")), { icon: "copy", only: true, kind: "ghost" }) : null,
    btn(has ? "Change" : info.secret ? "Add key" : "Set", () => editKey(name), has ? { icon: "pencil", only: true, kind: "ghost" } : { icon: "plus", kind: info.secret ? "primary" : "" }));
}

// A key for a settings field that has none yet. `id` keeps this form's state across
// repaints; `place(name)` writes ${name} into the document once keys.env has it.
const adding = {};
export const envName = (...parts) => parts.join("_").toUpperCase().replace(/[^A-Z0-9]+/g, "_");
export function newKeyWidget(id, suggested, place) {
  const st = (adding[id] ||= { name: suggested, clash: null });
  const done = (name) => { delete adding[id]; place(name); };
  if (st.clash !== null) {
    const name = st.name;
    return h("div", { class: "notice wait" },
      h("p", { md: `A saved value for \`${name}\` already exists — use it, or replace it with the one you pasted?` }),
      h("div", { class: "actions" },
        btn("Use the saved one", () => done(name), { small: true, kind: "primary" }),
        btn("Replace it", async () => { if (await saveKey(name, st.clash)) done(name); }, { small: true }),
        btn("Cancel", () => { st.clash = null; paint(); }, { small: true, kind: "ghost" })));
  }
  const nameIn = h("input", { type: "text", class: "code", value: st.name, "aria-label": "Name in keys.env", oninput: (e) => { st.name = e.target.value.trim(); } });
  const valueIn = h("input", { type: "password", autocomplete: "off", placeholder: "Paste a key", "aria-label": "Key" });
  const add = async () => {
    const { name } = st;
    const value = valueIn.value.trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) return toast("Use letters, digits and underscores for the name, like `MY_API_KEY`.");
    if (!value) return toast("Paste the key first.");
    if (keyInfo(name)?.stored) { st.clash = value; return paint(); }
    if (await saveKey(name, value)) done(name);
  };
  valueIn.addEventListener("keydown", (e) => { if (e.key === "Enter") add(); });
  return h("div", { class: "newkey" },
    h("div", { class: "secret" }, valueIn, btn("Save key", add, { icon: "check", kind: "primary" })),
    h("label", { class: "newkey-name" }, h("span", { class: "hint", text: "Saved in keys.env as" }), nameIn));
}

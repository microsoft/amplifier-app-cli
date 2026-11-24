// The provider diagnosis dialog. Steps are rendered generically:
// {name, status, summary, sources?, results?, config?, log?}.
import { api } from "./api.js";
import { h, btn, lamp, tag, show, toast } from "./dom.js";

const LAMP = { ok: "winner", warn: "winner unverified", fail: "dead", skip: "unconfigured" };
const resultText = (x) => [x.models != null ? `${x.models} models` : null, x.message].filter(Boolean).join(" - ");
const resolved = (v) => (v.resolved != null ? show(v.resolved) || "(not set)" : null);

function asText(r) {
  const lines = [`Amplifier provider diagnosis: ${r.provider} (${r.module || "?"}), ${r.seconds}s`, ""];
  for (const s of r.steps) {
    lines.push(`[${String(s.status).toUpperCase()}] ${s.name}: ${s.summary}`);
    for (const x of s.sources || []) lines.push(`    key from ${x.where}: ${x.preview} (${x.length} chars)`);
    for (const x of s.results || []) lines.push(`    ${x.key}: HTTP ${x.http ?? "none"} in ${x.seconds}s${resultText(x) ? `, ${resultText(x)}` : ""}`);
    for (const [k, v] of Object.entries(s.config || {})) lines.push(`    ${k}: ${show(v.value)}${resolved(v) ? ` -> ${resolved(v)}` : ""}`);
    for (const l of s.log || []) lines.push(`    log: ${l}`);
  }
  if (r.tips?.length) lines.push("", "What to try:", ...r.tips.map((t) => `- ${t}`));
  return lines.join("\n");
}

const table = (rows) => h("table", { class: "mini" }, h("tbody", {}, rows.map((cells) => h("tr", {}, cells.map((c) => h("td", {}, c))))));

function step(s) {
  return h("li", {},
    lamp(LAMP[s.status] || "fallback"),
    h("div", { class: "row-main" },
      h("strong", { text: s.name }),
      h("p", { md: s.summary }),
      s.sources?.length ? table(s.sources.map((x) => [`From ${x.where}`, h("code", { text: x.preview }), `${x.length} characters`])) : null,
      s.results?.length ? table(s.results.map((x) => [`Key ${x.key}`, tag(x.http ? `HTTP ${x.http}` : "no answer", x.http >= 200 && x.http < 300 ? "lit" : "stop"), `${x.seconds}s`, resultText(x)])) : null,
      s.config && Object.keys(s.config).length
        ? h("details", {}, h("summary", { text: "Settings it uses" }), table(Object.entries(s.config).map(([k, v]) => [h("code", { text: k }), h("code", { text: show(v.value) }), resolved(v) ? `→ ${resolved(v)}` : ""])))
        : null,
      s.log?.length ? h("details", { open: s.status === "fail" }, h("summary", { text: `Provider log (${s.log.length} lines)` }), h("pre", { class: "log", text: s.log.join("\n") })) : null));
}

export function openDiagnose(name) {
  const dlg = h("dialog", { class: "diag", "aria-label": `Diagnosis for ${name}` });
  dlg.addEventListener("close", () => dlg.remove());
  document.body.append(dlg);
  const head = () => h("header", { class: "diag-head" }, h("h2", { text: `Diagnosing ${name}` }), btn("Close", () => dlg.close(), { icon: "x", only: true, kind: "ghost" }));
  const paint = (r) => {
    if (!r) return dlg.replaceChildren(head(), h("p", { class: "pad quiet-note", text: "Running checks on the provider's code, settings, key and connection. This can take up to 45 seconds." }));
    if (r.error) return dlg.replaceChildren(head(), h("p", { class: "notice stop", md: r.error }));
    const tips = r.tips || [];
    dlg.replaceChildren(head(),
      tips.length ? h("section", { class: `notice ${r.steps.some((s) => s.status === "fail") ? "stop" : "wait"} tips` }, h("strong", { text: "What to try" }), h("ul", {}, tips.map((t) => h("li", { md: t })))) : null,
      h("ol", { class: "diag-steps" }, r.steps.map(step)),
      h("footer", { class: "actions diag-foot" },
        btn("Copy report", () => navigator.clipboard.writeText(asText(r)).then(() => toast("Copied. Secrets are masked.")), { icon: "copy" }),
        btn("Run again", run, { icon: "refresh" }),
        h("span", { class: "spacer" }),
        h("span", { class: "hint", text: `Took ${r.seconds}s` })));
  };
  const run = async () => {
    paint(null);
    try { paint(await api("/api/providers/diagnose", { name })); } catch (e) { paint({ error: e.message }); }
  };
  dlg.showModal();
  run();
}

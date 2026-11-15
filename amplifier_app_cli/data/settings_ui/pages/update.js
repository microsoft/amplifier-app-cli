// Update: check for and install the latest Amplifier, with the command's output.
import { h, btn, fmtAgo } from "../lib/dom.js";
import { S } from "../lib/store.js";
import { jobs, startJob, confirmUpdate, jobConsole, jobsOff } from "../lib/jobs.js";

export default {
  id: "update", title: "Update", crumb: "Get the latest Amplifier and its bundles.", group: "Maintenance", icon: "refresh",
  view() {
    const check = jobs["update-check"];
    const run = jobs.update;
    const busy = check?.running || run?.running;
    const st = S.state.status;
    const off = jobsOff();
    return [
      off ? h("p", { class: "notice wait", text: off }) : null,
      h("section", { class: "hero" },
        h("div", {},
          h("h2", { text: `Version ${st?.version?.display || "…"}` }),
          h("p", { text: st?.updates?.hours_ago == null ? "Amplifier hasn't checked for updates yet." : `Checked ${fmtAgo(st.updates.hours_ago)}. Updating pulls the latest Amplifier, its bundles and modules; your settings, keys and sessions stay.` })),
        h("div", { class: "hero-actions" },
          btn(check?.running ? "Checking…" : "Check for updates", () => startJob("update-check"), { icon: "refresh", kind: "primary", disabled: busy }),
          btn(run?.running ? "Updating…" : "Update now", confirmUpdate, { icon: "play", disabled: busy || off }))),
      jobConsole(run || check) || h("p", { class: "hint", md: "Same as `amplifier update --check-only` and `amplifier update -y`. Output appears here as it runs." }),
    ];
  },
};

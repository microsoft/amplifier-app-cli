// Notifications: the settings `amplifier notify desktop|ntfy` writes. The ntfy topic lives in keys.env.
import { api } from "../lib/api.js";
import { h, btn, field, switchRow, panel, toast } from "../lib/dom.js";
import { S, getIn, setIn, delIn, prune, changed, truthy, keyInfo, paint, scopeNote } from "../lib/store.js";
import { secretWidget, saveKey, reveal } from "../lib/secret.js";

const NTFY_TOPIC = "AMPLIFIER_NTFY_TOPIC";
const NOTIFY = ["config", "notifications"];
export const isNtfyTopic = (name) => name === NTFY_TOPIC;

// `apply(mutate)` changes a settings document: the draft here, the global file on Home
export function notifyControls(doc, apply) {
  const desktopOn = truthy(getIn(doc, [...NOTIFY, "desktop", "enabled"]));
  const pushOn = truthy(getIn(doc, [...NOTIFY, "ntfy", "enabled"])) || truthy(getIn(doc, [...NOTIFY, "push", "enabled"]));
  const topic = keyInfo(NTFY_TOPIC);
  const hasTopic = Boolean(topic && (topic.stored || topic.in_env));
  const setPush = (enabled) => apply((d) => { setIn(d, [...NOTIFY, "ntfy", "enabled"], enabled); delIn(d, [...NOTIFY, "push", "enabled"]); });
  // ntfy delivers to a channel name you make up; whoever knows the name gets the messages
  const createChannel = async () => {
    const letters = "abcdefghijkmnpqrstuvwxyz23456789";
    const name = `amplifier-${Array.from(crypto.getRandomValues(new Uint8Array(14)), (b) => letters[b % letters.length]).join("")}`;
    if (!(await saveKey(NTFY_TOPIC, name))) return;
    reveal(NTFY_TOPIC, name); // show it, so it can be copied into the app
    toast("Created your private channel. Now subscribe to it in the ntfy app.");
    setPush(true);
  };
  const sendTest = async () => {
    try {
      await api("/api/notify/test", {});
      toast("Sent. It should arrive on your phone within a few seconds.");
    } catch (e) { toast(e.message); }
  };
  return [
    switchRow("Desktop notifications", "A notification on this computer when the assistant finishes and is waiting for you.", desktopOn, (enabled) => apply((d) => setIn(d, [...NOTIFY, "desktop", "enabled"], enabled))),
    switchRow("Phone notifications", "A push notification on your phone, through the free ntfy app.", pushOn, (enabled) => (enabled && !hasTopic ? createChannel() : setPush(enabled))),
    pushOn || hasTopic
      ? h("div", { class: "phone-setup" },
          h("ol", { class: "howto" },
            h("li", {}, "Install the free ntfy app for ", h("a", { href: "https://apps.apple.com/app/ntfy/id1625396347", target: "_blank", rel: "noreferrer", text: "iPhone" }), " or ", h("a", { href: "https://play.google.com/store/apps/details?id=io.heckel.ntfy", target: "_blank", rel: "noreferrer", text: "Android" }), "."),
            h("li", { text: "In the app, tap + and subscribe to your private channel name below. Use the eye to see it, then copy it." }),
            h("li", { text: "Send a test to check it arrives." })),
          hasTopic ? field("Your private channel", secretWidget(NTFY_TOPIC, "No channel yet")) : btn("Create a private channel", createChannel, { icon: "plus", kind: "primary" }),
          h("p", { class: "hint", text: "Treat the channel name like a password: anyone who knows it can read your notifications. It's kept in keys.env, never in your settings." }),
          hasTopic ? h("div", { class: "actions" }, btn("Send a test notification", sendTest, { icon: "bell" })) : null)
      : null,
  ];
}

export default {
  id: "notifications", title: "Notifications", crumb: "How Amplifier tells you it's waiting for you.", group: "Sessions", icon: "bell", scoped: true,
  view: () => [
    scopeNote(),
    panel(null, null, h("div", { class: "pad" }, notifyControls(S.draft, (mutate) => { mutate(S.draft); prune(S.draft); changed(); paint(); }))),
  ],
};

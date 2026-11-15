// Talking to the settings server. The launch URL carries a one-time token in its
// fragment (#t=...); it is exchanged once for a session secret kept in this tab's
// sessionStorage and sent as X-Amplifier-Settings on every /api request.

// what /api/state puts in place of a secret written into a settings file
export const MASKED = "•••• hidden";

const KEY = "amplifier-settings-secret";
let secret = sessionStorage.getItem(KEY);

// take the token out of the address bar before anything else reads the hash
const token = location.hash.match(/^#t=([^&]+)/)?.[1];
if (token) history.replaceState(null, "", location.pathname + location.search);
const ready = token
  ? fetch("/api/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: decodeURIComponent(token) }) })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => { if (data?.secret) { secret = data.secret; sessionStorage.setItem(KEY, secret); } })
      .catch(() => {})
  : Promise.resolve();

// app.js shows the expired page (401 relaunch) or the restart banner (503 restart)
let onHaltFn = () => {};
export const onHalt = (fn) => { onHaltFn = fn; };

// GET when there is no body, POST JSON otherwise. Errors carry the server's message,
// HTTP status and payload (e.g. `errors`, `exists`).
export async function api(path, body) {
  await ready;
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: { "X-Amplifier-Settings": secret || "", ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({ error: `The settings page answered ${res.status}.` }));
  const halt = res.status === 401 && data.relaunch ? "expired" : res.status === 503 && data.restart ? "restart" : null;
  if (halt) onHaltFn(halt);
  if (!res.ok) throw Object.assign(new Error(data.error || "Request failed."), { status: res.status, data, halt });
  return data;
}

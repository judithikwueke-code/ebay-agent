// Sends the captured Terapeak numbers to the agent. Done here (not in the page) because the
// Terapeak page is HTTPS and Chrome blocks it from calling an HTTP server directly.
const SERVER = "http://204.168.194.157:8765/terapeak";
const API_KEY = "b1e7ff746dd94281ffac3ae6856f89c9";

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== "send") return;
  fetch(SERVER, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
    body: JSON.stringify(msg.payload),
  })
    .then((r) => sendResponse({ ok: r.ok, status: r.status }))
    .catch((e) => sendResponse({ ok: false, error: String(e && e.message ? e.message : e) }));
  return true; // keep the channel open for the async reply
});

// Sends captured data to the agent. Done here (not in the page) because eBay pages are HTTPS and Chrome
// blocks them from calling an HTTP server directly.
const BASE = "http://204.168.194.157:8765";
const API_KEY = "b1e7ff746dd94281ffac3ae6856f89c9";
const ROUTES = { send: "/terapeak", item: "/ebay_item" };

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !ROUTES[msg.type]) return;
  fetch(BASE + ROUTES[msg.type], {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
    body: JSON.stringify(msg.payload),
  })
    .then((r) => sendResponse({ ok: r.ok, status: r.status }))
    .catch((e) => sendResponse({ ok: false, error: String(e && e.message ? e.message : e) }));
  return true; // keep the channel open for the async reply
});

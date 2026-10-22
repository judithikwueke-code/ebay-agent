/**
 * Terapeak Research Collector - content script (v1.1).
 *
 * Runs on ebay.co.uk/sh/research pages. Waits for the results to render, reads the stat cards
 * (the figure can sit above OR below its label), and hands the numbers to the background worker,
 * which POSTs them to the agent (a page on HTTPS may not call an HTTP server directly).
 */

const LABELS = {
  avg_sold_price: ["avg sold price", "average sold price", "avg. sold price"],
  sell_through_pct: ["sell-through", "sell-through rate", "sell through", "sell through rate"],
  total_sold: ["total sold", "items sold", "sold items", "total items sold", "quantity sold"],
  total_listings: ["total listings", "active listings", "listings"],
  avg_shipping: ["avg postage", "average postage", "avg shipping", "average shipping", "avg. postage"],
};

function waitForElement(selector, timeout = 15000) {
  return new Promise((resolve, reject) => {
    const el = document.querySelector(selector);
    if (el) return resolve(el);
    const observer = new MutationObserver(() => {
      const found = document.querySelector(selector);
      if (found) { observer.disconnect(); resolve(found); }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    setTimeout(() => { observer.disconnect(); reject("timeout"); }, timeout);
  });
}

function getKeyword() {
  const params = new URLSearchParams(window.location.search);
  const kw = params.get("keywords") || params.get("keyword") || params.get("q");
  if (kw) return decodeURIComponent(kw.replace(/\+/g, " ")).trim();
  const input = document.querySelector('input[name="keywords"], input[placeholder*="keyword" i], input[aria-label*="search" i]');
  return input ? input.value.trim() : "";
}

const clean = (t) => (t || "").replace(/\s+/g, " ").replace(/[ⓘℹ]/g, "").trim();
const looksLikeValue = (t) => /^[£$]?\s?[\d,]+(\.\d+)?\s?%?$/.test(t) || /^[£$]\s?[\d,]+(\.\d+)?\s?[kKmM]?$/.test(t);

function parseNum(text) {
  if (!text) return null;
  const m = text.replace(/,/g, "").match(/[\d.]+/);
  if (!m) return null;
  let n = parseFloat(m[0]);
  if (/[kK]\b/.test(text)) n *= 1000;
  if (/[mM]\b/.test(text)) n *= 1000000;
  return n;
}

/**
 * Build {label -> value text} by finding each short text node that is not itself a number and
 * looking for a number-like text in the same small container (up to 3 levels up).
 */
function scanStatCards() {
  const map = {};
  const leaves = Array.from(document.querySelectorAll("*")).filter(
    (el) => el.children.length === 0 && clean(el.textContent).length >= 3 && clean(el.textContent).length <= 32
  );
  for (const el of leaves) {
    const label = clean(el.textContent);
    if (looksLikeValue(label)) continue;
    let node = el;
    for (let up = 0; up < 3 && node.parentElement; up++) {
      node = node.parentElement;
      const rest = clean(node.textContent.replace(label, ""));
      if (rest.length > 0 && rest.length <= 18 && looksLikeValue(rest)) {
        if (!(label.toLowerCase() in map)) map[label.toLowerCase()] = rest;
        break;
      }
      if (clean(node.textContent).length > 80) break;
    }
  }
  return map;
}

function extractStats() {
  const map = scanStatCards();
  const data = {
    keyword: getKeyword(),
    avg_sold_price: null, sell_through_pct: null, total_sold: null, total_listings: null, avg_shipping: null,
    date_range: new URLSearchParams(window.location.search).get("dayRange") || "90d",
    source_url: window.location.href,
  };
  for (const [field, names] of Object.entries(LABELS)) {
    for (const name of names) {
      if (name in map) { data[field] = parseNum(map[name]); break; }
    }
  }
  // kept for the popup's debug line so we can see what the page really contains
  data._seen = Object.entries(map).slice(0, 14).map(([k, v]) => `${k}=${v}`).join("; ");
  return data;
}

async function run() {
  try {
    await waitForElement('[class*="stat"], [class*="research"], [class*="summary"], [class*="metric"]', 12000);
  } catch {
    // keep going - the page may still have rendered
  }
  await new Promise((r) => setTimeout(r, 2500));   // let the cards finish loading

  const stats = extractStats();
  if (!stats.keyword) {
    console.log("[Terapeak Collector] No keyword found - not a results page");
    return;
  }

  chrome.storage.local.set({ last_attempt: stats });
  // Only send a record that has the figures the agent needs (never a guess)
  if (!(stats.avg_sold_price && (stats.sell_through_pct != null || stats.total_sold != null))) {
    chrome.storage.local.set({ last_attempt: { ...stats, error: "missing_stats" }, last_send_status: "missing stats - see debug" });
    return;
  }
  chrome.storage.local.set({ last_success: stats });

  chrome.runtime.sendMessage({ type: "send", payload: stats }, (resp) => {
    const err = chrome.runtime.lastError;
    if (err || !resp) {
      chrome.storage.local.set({ last_send_status: `network_error: ${err ? err.message : "no response"}` });
    } else if (resp.ok) {
      chrome.storage.local.set({ last_send_status: "ok", last_send_time: new Date().toISOString() });
    } else {
      chrome.storage.local.set({ last_send_status: resp.error ? `network_error: ${resp.error}` : `server_${resp.status}` });
    }
  });
}

run();

// Terapeak is a single-page app: searches change the URL without reloading
let lastUrl = window.location.href;
new MutationObserver(() => {
  if (window.location.href !== lastUrl) {
    lastUrl = window.location.href;
    setTimeout(run, 1500);
  }
}).observe(document, { childList: true, subtree: true });

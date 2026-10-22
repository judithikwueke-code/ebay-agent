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
function ownText(el) {
  // text sitting directly in this element (ignores child elements such as the little (i) info icon)
  let t = "";
  for (const n of el.childNodes) if (n.nodeType === 3) t += n.textContent;
  return clean(t);
}

let shadowRoots = 0;

function allElements(root) {
  // every element, including those inside open shadow roots
  const out = [];
  const walk = (node) => {
    for (const el of node.querySelectorAll("*")) {
      out.push(el);
      if (el.shadowRoot) { shadowRoots++; walk(el.shadowRoot); }
    }
  };
  walk(root);
  return out;
}

function scanDom() {
  const map = {};
  const cands = allElements(document).filter((el) => {
    const t = ownText(el);
    return t.length >= 3 && t.length <= 32 && !looksLikeValue(t) && !/^\d/.test(t);
  });
  for (const el of cands) {
    const label = ownText(el);
    const key = label.toLowerCase();
    if (key in map) continue;
    let node = el;
    for (let up = 0; up < 3 && node; up++) {
      node = node.parentElement || (node.getRootNode && node.getRootNode().host) || null;
      if (!node) break;
      const full = clean(node.textContent);
      if (full.length > 90) break;
      const rest = clean(full.replace(label, ""));
      if (rest.length > 0 && rest.length <= 20 && looksLikeValue(rest)) { map[key] = rest; break; }
    }
  }
  return map;
}

// Read the page as the plain text lines a person sees: "value" then "label" (or the other way round)
function scanText() {
  const map = {};
  const add = (key, val) => { (map[key] = map[key] || []).includes(val) || map[key].push(val); };
  const lines = (document.body.innerText || "").split("\n").map(clean).filter(Boolean);
  const isLabel = (t) => t.length >= 3 && t.length <= 32 && !looksLikeValue(t) && !/^\d/.test(t);
  for (let i = 0; i + 1 < lines.length; i++) {
    if (looksLikeValue(lines[i]) && isLabel(lines[i + 1])) add(lines[i + 1].toLowerCase(), lines[i]);   // value above label
    if (isLabel(lines[i]) && looksLikeValue(lines[i + 1])) add(lines[i].toLowerCase(), lines[i + 1]);   // label above value
  }
  return map;
}

function scanStatCards() {
  const dom = scanDom();
  const text = scanText();
  const map = {};
  for (const [k, v] of Object.entries(dom)) map[k] = [v];
  for (const [k, vs] of Object.entries(text)) {
    map[k] = (map[k] || []).concat(vs.filter((v) => !(map[k] || []).includes(v)));
  }
  return map;
}

// each field only accepts a value of the right kind (a percentage is never a price, etc.)
const FIELD_TYPE = {
  avg_sold_price: (v) => v.includes("£"),
  sell_through_pct: (v) => v.includes("%"),
  total_sold: (v) => !v.includes("£") && !v.includes("%"),
  total_listings: (v) => !v.includes("£") && !v.includes("%"),
  avg_shipping: (v) => v.includes("£"),
};

function extractStats() {
  const map = scanStatCards();
  const data = {
    keyword: getKeyword(),
    avg_sold_price: null, sell_through_pct: null, total_sold: null, total_listings: null, avg_shipping: null,
    date_range: new URLSearchParams(window.location.search).get("dayRange") || "90d",
    source_url: window.location.href,
  };
  for (const [field, names] of Object.entries(LABELS)) {
    outer: for (const name of names) {
      for (const val of map[name] || []) {
        if (FIELD_TYPE[field](val)) { data[field] = parseNum(val); break outer; }
      }
    }
  }
  // kept for the popup's debug line so we can see what the page really contains
  const wanted = /sold|sell|price|postage|shipping|sellers|listings|sales/;
  const entries = Object.entries(map);
  const rel = entries.filter(([k]) => wanted.test(k) && !/free postage/.test(k));
  const rest = entries.filter(([k]) => !(wanted.test(k) && !/free postage/.test(k)));
  const frames = document.querySelectorAll("iframe").length;
  const idx = (document.body.innerText || "").toLowerCase().indexOf("avg sold price");
  const hasText = idx >= 0 ? "text-has-avg-sold-price" : "no-avg-sold-price-text";
  data._seen = `[${entries.length} labels | shadow ${shadowRoots} | iframes ${frames} | ${hasText} | ${window === window.top ? "top" : "frame"}] ` + rel.concat(rest).slice(0, 12).map(([k, v]) => `${k}=${[].concat(v).join('|')}`).join("; ");
  return data;
}

const haveEnough = (st) => st.avg_sold_price && (st.sell_through_pct != null || st.total_sold != null);
let running = false;

async function run() {
  if (running) return;
  running = true;
  try {
    const keyword = getKeyword();
    if (!keyword) return;
    let stats = null;
    // The summary cards load after the listing table - keep trying for about 30 seconds
    for (let attempt = 0; attempt < 12; attempt++) {
      await new Promise((r) => setTimeout(r, attempt === 0 ? 3000 : 2500));
      stats = extractStats();
      if (haveEnough(stats)) break;
    }
    const inTop = window === window.top;
    if (!stats || !haveEnough(stats)) {
      // record the failure for the popup, but only from the top frame and only if nothing was sent for this keyword
      chrome.storage.local.get(["last_success", "last_send_time"], (d) => {
        const recent = d.last_success && d.last_success.keyword === keyword && d.last_send_time &&
          (Date.now() - new Date(d.last_send_time).getTime() < 120000);
        if (!recent && inTop) {
          chrome.storage.local.set({ last_attempt: { ...(stats || { keyword }), error: "missing_stats" },
                                     last_send_status: "missing stats - see debug" });
        }
      });
      return;
    }
    chrome.storage.local.set({ last_attempt: stats, last_success: stats });
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
  } finally {
    running = false;
  }
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

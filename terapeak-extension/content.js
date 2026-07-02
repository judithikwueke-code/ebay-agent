/**
 * Terapeak Research Collector — content script.
 *
 * Runs on ebay.co.uk/sh/research pages. Waits for the results to render,
 * then extracts: keyword, avg sold price, sell-through %, total sold,
 * total listings. POSTs to the agent's VPS receiver.
 */

const SERVER = "http://204.168.194.157:8765/terapeak";
const API_KEY = "b1e7ff746dd94281ffac3ae6856f89c9";

// eBay renders Terapeak as a React SPA — we need to wait for the stats
// to appear in the DOM after the page loads.
function waitForElement(selector, timeout = 15000) {
  return new Promise((resolve, reject) => {
    const el = document.querySelector(selector);
    if (el) return resolve(el);
    const observer = new MutationObserver(() => {
      const el = document.querySelector(selector);
      if (el) { observer.disconnect(); resolve(el); }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    setTimeout(() => { observer.disconnect(); reject("timeout"); }, timeout);
  });
}

function getKeyword() {
  // Try URL param first (most reliable)
  const params = new URLSearchParams(window.location.search);
  const kw = params.get("keywords") || params.get("keyword") || params.get("q");
  if (kw) return decodeURIComponent(kw).trim();

  // Fallback: read search input
  const input = document.querySelector('input[name="keywords"], input[placeholder*="keyword" i], input[aria-label*="search" i]');
  return input ? input.value.trim() : "";
}

function parseGBP(text) {
  if (!text) return null;
  const match = text.replace(/,/g, "").match(/[\d.]+/);
  return match ? parseFloat(match[0]) : null;
}

function parsePct(text) {
  if (!text) return null;
  const match = text.match(/[\d.]+/);
  return match ? parseFloat(match[0]) : null;
}

function parseCount(text) {
  if (!text) return null;
  const match = text.replace(/,/g, "").match(/[\d]+/);
  return match ? parseInt(match[0]) : null;
}

/**
 * Try multiple selector strategies to find Terapeak stat cards.
 * Terapeak's DOM class names are minified and can change — we hunt by
 * the visible label text instead of class names.
 */
function extractStats() {
  const data = {
    keyword: getKeyword(),
    avg_sold_price: null,
    sell_through_pct: null,
    total_sold: null,
    total_listings: null,
    avg_shipping: null,
    date_range: "90d",
    source_url: window.location.href,
  };

  // Strategy 1: walk all stat-card-like elements looking for known labels
  const allText = Array.from(document.querySelectorAll("*")).filter(el => {
    const t = (el.textContent || "").trim();
    return t.length > 0 && t.length < 60 && el.children.length === 0;
  });

  const labelMap = {};
  allText.forEach(el => {
    const t = el.textContent.trim().toLowerCase();
    labelMap[t] = el;
  });

  // Find label elements and grab sibling/parent value
  function findValueNear(labelEl) {
    if (!labelEl) return null;
    // Try next sibling
    let sib = labelEl.nextElementSibling;
    if (sib) return sib.textContent.trim();
    // Try parent's next sibling
    if (labelEl.parentElement) {
      sib = labelEl.parentElement.nextElementSibling;
      if (sib) return sib.textContent.trim();
    }
    // Try grandparent children
    if (labelEl.parentElement && labelEl.parentElement.parentElement) {
      const gp = labelEl.parentElement.parentElement;
      const children = Array.from(gp.children);
      const idx = children.indexOf(labelEl.parentElement);
      if (idx >= 0 && children[idx + 1]) return children[idx + 1].textContent.trim();
    }
    return null;
  }

  // Average sold price
  for (const label of ["average sold price", "avg. sold price", "average price", "avg price"]) {
    const el = labelMap[label];
    if (el) {
      const val = findValueNear(el);
      if (val) { data.avg_sold_price = parseGBP(val); break; }
    }
  }

  // Sell-through rate
  for (const label of ["sell-through rate", "sell through rate", "sell-through", "sell through"]) {
    const el = labelMap[label];
    if (el) {
      const val = findValueNear(el);
      if (val) { data.sell_through_pct = parsePct(val); break; }
    }
  }

  // Total sold
  for (const label of ["sold listings", "total sold", "items sold", "sales"]) {
    const el = labelMap[label];
    if (el) {
      const val = findValueNear(el);
      if (val) { data.total_sold = parseCount(val); break; }
    }
  }

  // Total listings
  for (const label of ["total listings", "listings", "active listings"]) {
    const el = labelMap[label];
    if (el) {
      const val = findValueNear(el);
      if (val) { data.total_listings = parseCount(val); break; }
    }
  }

  // Average shipping
  for (const label of ["average shipping price", "avg. shipping", "avg shipping", "average shipping"]) {
    const el = labelMap[label];
    if (el) {
      const val = findValueNear(el);
      if (val) { data.avg_shipping = parseGBP(val); break; }
    }
  }

  // Strategy 2: look for £ values in large-font elements (stat card numbers)
  // as a fallback if Strategy 1 found nothing
  if (!data.avg_sold_price) {
    const bigNums = Array.from(document.querySelectorAll("h1, h2, h3, [class*='value'], [class*='stat'], [class*='price'], [class*='amount']"))
      .map(el => ({ el, text: el.textContent.trim() }))
      .filter(({ text }) => /£[\d,.]+/.test(text) && text.length < 30);

    if (bigNums.length > 0) {
      // The first £ number on a Terapeak page is usually avg sold price
      data.avg_sold_price = parseGBP(bigNums[0].text);
    }
  }

  return data;
}

async function run() {
  // Wait for the stats to render (Terapeak loads async)
  try {
    await waitForElement('[class*="stat"], [class*="research"], [class*="summary"]', 12000);
  } catch {
    // Page might still have data — try anyway after a short delay
    await new Promise(r => setTimeout(r, 3000));
  }

  const stats = extractStats();

  if (!stats.keyword) {
    console.log("[Terapeak Collector] No keyword found — not on a results page");
    return;
  }

  // Only send if we got at least one real metric
  const hasData = stats.avg_sold_price || stats.sell_through_pct || stats.total_sold;
  if (!hasData) {
    console.warn("[Terapeak Collector] Could not extract stats — page structure may have changed. Check popup for debug info.");
    // Still store in chrome.storage for popup debug view
    chrome.storage.local.set({ last_attempt: { ...stats, error: "no_stats_found" } });
    return;
  }

  console.log("[Terapeak Collector] Extracted:", stats);
  chrome.storage.local.set({ last_attempt: stats, last_success: stats });

  // Send to VPS
  try {
    const resp = await fetch(SERVER, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
      body: JSON.stringify(stats),
    });
    if (resp.ok) {
      console.log(`[Terapeak Collector] Saved: '${stats.keyword}' avg_sold=£${stats.avg_sold_price}`);
      chrome.storage.local.set({ last_send_status: "ok", last_send_time: new Date().toISOString() });
    } else {
      console.error("[Terapeak Collector] Server error:", resp.status);
      chrome.storage.local.set({ last_send_status: `server_${resp.status}` });
    }
  } catch (err) {
    console.error("[Terapeak Collector] Network error:", err);
    chrome.storage.local.set({ last_send_status: `network_error: ${err.message}` });
  }
}

// Run on initial page load
run();

// Re-run whenever the URL changes (Terapeak is a SPA — searches don't reload the page)
let lastUrl = window.location.href;
const urlObserver = new MutationObserver(() => {
  const newUrl = window.location.href;
  if (newUrl !== lastUrl) {
    lastUrl = newUrl;
    setTimeout(run, 1500); // give the page time to load new results
  }
});
urlObserver.observe(document, { childList: true, subtree: true });

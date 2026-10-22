/**
 * Item Collector - content script for eBay product pages (ebay.co.uk/itm/...).
 *
 * Reads the product's title, price, seller, "sold today / sold / in baskets / watchers" figures,
 * item specifics (brand, MPN, EAN, model) and photos, then hands them to the background worker,
 * which sends them to the agent. The agent decides whether the item is worth sourcing.
 */

const cleanTxt = (t) => (t || "").replace(/\s+/g, " ").trim();
const toInt = (s) => {
  if (!s) return null;
  const m = String(s).replace(/,/g, "").match(/([\d.]+)\s*([kKmM]?)/);
  if (!m) return null;
  let n = parseFloat(m[1]);
  if (/k/i.test(m[2])) n *= 1000;
  if (/m/i.test(m[2])) n *= 1000000;
  return Math.round(n);
};

function itemIdFromUrl() {
  const m = window.location.pathname.match(/\/itm\/(?:[^/]+\/)?(\d{9,14})/);
  return m ? m[1] : "";
}

function firstText(selectors) {
  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (el && cleanTxt(el.textContent)) return cleanTxt(el.textContent);
  }
  return "";
}

function metaContent(prop) {
  const el = document.querySelector(`meta[property="${prop}"], meta[name="${prop}"]`);
  return el ? el.getAttribute("content") || "" : "";
}

/** "label ... value" pairs from the item-specifics block, read as plain text lines. */
function specificsFromText(lines) {
  const wanted = { brand: "brand", mpn: "mpn", "manufacturer part number": "mpn", ean: "ean", upc: "ean",
                   gtin: "ean", model: "model", colour: "colour", color: "colour" };
  const out = {};
  for (let i = 0; i + 1 < lines.length; i++) {
    const key = lines[i].toLowerCase().replace(/:$/, "");
    if (key in wanted && !(wanted[key] in out)) {
      const val = lines[i + 1];
      if (val && val.length < 80 && !/^does not apply$/i.test(val)) out[wanted[key]] = val;
    }
  }
  return out;
}

function extractItem() {
  const body = document.body ? document.body.innerText || "" : "";
  const lines = body.split("\n").map(cleanTxt).filter(Boolean);

  const title = firstText(["h1.x-item-title__mainTitle", "h1[class*='item-title']", "h1"]) || metaContent("og:title");

  // price: the main price block, e.g. "GBP 4.25" or the pound figure
  let priceText = firstText([".x-price-primary", "[class*='price-primary']", "[itemprop='price']"]);
  const pm = priceText.replace(/,/g, "").match(/[\d]+(?:\.\d{1,2})?/);
  const price = pm ? parseFloat(pm[0]) : null;

  // demand signals (case-insensitive; the page styles "SOLD TODAY" in capitals)
  const soldToday = toInt((body.match(/(\d[\d,.]*\s?[kK]?)\s+sold today/i) || [])[1]);
  const soldTotal = toInt((body.match(/(?:^|[\s·|,])(\d[\d,.]*\s?[kK]?)\s+sold\b(?!\s+today)/im) || [])[1]);
  const inBaskets = toInt((body.match(/(\d[\d,.]*)\s+(?:people\s+)?(?:have\s+this\s+in\s+their\s+)?(?:baskets?|carts?)/i) || [])[1]);
  const available = toInt((body.match(/(\d[\d,.]*)\s+available/i) || [])[1]);
  let watchers = toInt((body.match(/(\d[\d,.]*)\s+(?:watchers?|watching)/i) || [])[1]);
  if (watchers == null) {
    const w = document.querySelector("[aria-label*='atch' i], button[class*='watch' i], [class*='watch-count' i], [data-testid*='watch' i]");
    if (w) watchers = toInt((w.getAttribute("aria-label") || "") + " " + (w.textContent || ""));
  }

  // seller
  const sellerBlock = firstText([".x-sellercard-atf__info__about-seller", "[class*='sellercard']", "[class*='seller-card']"]);
  const sellerName = (document.querySelector(".x-sellercard-atf__info__about-seller a, [class*='sellercard'] a") || {}).textContent || "";
  const sellerType = /\bbusiness\b/i.test(sellerBlock) ? "business" : (/\bprivate\b/i.test(sellerBlock) ? "private" : "");
  const fb = (sellerBlock.match(/\((\d[\d,]*)\)/) || [])[1];

  const condition = (body.match(/Condition:?\s*\n?\s*([A-Za-z ]{3,30})/) || [])[1] || "";
  const specs = specificsFromText(lines);

  // photos: Open Graph first, then the gallery
  const imgs = [];
  const og = metaContent("og:image");
  if (og) imgs.push(og);
  document.querySelectorAll(".ux-image-carousel img, [class*='image-carousel'] img, .ux-image-grid img").forEach((im) => {
    const src = im.getAttribute("src") || im.getAttribute("data-src") || im.getAttribute("data-zoom-src") || "";
    if (src.startsWith("http")) imgs.push(src.replace(/s-l\d+/, "s-l1600"));
  });
  const images = Array.from(new Set(imgs)).slice(0, 6);

  return {
    item_id: itemIdFromUrl(),
    url: window.location.origin + window.location.pathname,
    title: cleanTxt(title),
    price,
    seller: cleanTxt(sellerName),
    seller_type: sellerType,
    feedback: toInt(fb),
    sold_today: soldToday,
    sold_total: soldTotal,
    in_baskets: inBaskets,
    watchers,
    available,
    condition: cleanTxt(condition),
    brand: specs.brand || "",
    mpn: specs.mpn || "",
    ean: specs.ean || "",
    model: specs.model || "",
    images,
  };
}

async function runItem() {
  if (!itemIdFromUrl()) return;
  await new Promise((r) => setTimeout(r, 3500));            // let the page finish rendering
  const item = extractItem();
  chrome.storage.local.set({ last_item: { ...item, captured: new Date().toISOString() } });
  if (!item.title || item.price == null) {
    chrome.storage.local.set({ last_item_status: "item: title/price not found" });
    return;
  }
  chrome.runtime.sendMessage({ type: "item", payload: item }, (resp) => {
    const err = chrome.runtime.lastError;
    chrome.storage.local.set({
      last_item_status: err || !resp ? `item network_error: ${err ? err.message : "no response"}`
                       : resp.ok ? "item sent" : `item server_${resp.status}`,
    });
  });
}

runItem();

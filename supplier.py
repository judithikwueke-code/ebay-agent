"""
Queries UK-first suppliers for a product keyword.
Returns best match: lowest cost + fastest UK delivery + highest margin.

Supplier priority: Avasam → Costway → Wholesale Domestic → Birlea → BigBuy → CJ
Furniture is restricted to UK-warehouse suppliers only.
"""

import logging
import time
import requests
import wholesalebeds_catalog
from config import (
    COSTWAY_API_KEY, BIGBUY_API_KEY, CJ_EMAIL, CJ_API_KEY,
    get_avasam_token,
    MIN_NET_MARGIN_PCT, SUPPLIER_PRIORITY, FURNITURE_ALLOWED_SUPPLIERS,
    calculate_ebay_fees, total_cost_with_vat,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Avasam  (UK's largest dropship aggregator — covers 100+ UK suppliers)
# Auth: consumer_key + secret_key → access_token via /api/auth/request-token
# All endpoints: POST to https://app.avasam.com/apiseeker/...
# ---------------------------------------------------------------------------

def _search_avasam(keyword: str, max_price_gbp: float) -> list[dict]:
    """
    Avasam requires a paid subscription to source products. Disabled until
    a subscription is active and products have been sourced at app.avasam.com.
    Re-enable by removing the early return below.
    """
    return []
    token = get_avasam_token()  # noqa: unreachable
    if not token:
        return []
    headers = {"Authorization": token, "Content-Type": "application/json"}
    payload = {
        "ProductType": [],
        "Supplier": "",
        "Sortby": "SKU",
        "SortStatus": "down",
        "limit": "50",
        "PriceDelimeter": "0",
        "PriceValue": 0,
        "StockValue": "0",
        "Stock": 0,
        "Variation": "true",
        "Showchild": "true",
        "Category": "",
        "CategoryName": "",
        "IsMapped": "",
        "PriceMaxValue": max_price_gbp,
        "PriceMaxDelimeter": "1",
        "page": 0,
    }
    try:
        resp = requests.post(
            "https://app.avasam.com/apiseeker/ProductModule/GetInventoryListWithFilter",
            headers=headers,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        items = (body.get("body") or {}).get("data") or body.get("products") or body.get("data") or []
        log.info(f"Avasam raw: status={resp.status_code}, items_returned={len(items)}, "
                 f"first={items[0].get('name','?')[:40] if items else 'EMPTY — source products at app.avasam.com'}")
        kw_lower = keyword.lower()
        results = []
        for i in items:
            title = i.get("name") or i.get("title") or i.get("SKU") or ""
            # Filter by keyword relevance within sourced inventory
            if not any(w in title.lower() for w in kw_lower.split() if len(w) > 3):
                continue
            price = float(i.get("price") or i.get("tradePrice") or i.get("Price") or 0)
            if not price or price > max_price_gbp:
                continue
            results.append({
                "supplier": "avasam",
                "product_id": str(i.get("sku") or i.get("SKU") or i.get("id") or ""),
                "title": title,
                "cost_gbp": price,
                "shipping_gbp": float(i.get("shippingCost") or i.get("shipping_cost") or 0),
                "delivery_days": int(i.get("deliveryDays") or i.get("delivery_days") or 3),
                "images": i.get("images") or [],
                "description": i.get("description") or "",
                "uk_warehouse": True,
            })
        return results
    except Exception as e:
        log.warning(f"Avasam search failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Costway UK  (UK warehouse — furniture, fitness, home)
# ---------------------------------------------------------------------------

def _search_costway(keyword: str, max_price_gbp: float) -> list[dict]:
    if not COSTWAY_API_KEY:
        return []
    url = "https://openapi.costway.co.uk/api/product/search"
    headers = {"Authorization": f"Bearer {COSTWAY_API_KEY}", "Content-Type": "application/json"}
    payload = {"keyword": keyword, "currency": "GBP", "pageSize": 5}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        items = resp.json().get("data", {}).get("list", [])
        return [
            {
                "supplier": "costway",
                "product_id": str(i.get("sku", i.get("productId", ""))),
                "title": i.get("productName", keyword),
                "cost_gbp": float(i.get("price", 0)),
                "shipping_gbp": float(i.get("shippingFee", 0)),
                "delivery_days": 3,
                "images": [i.get("mainImage", "")],
                "description": i.get("description", ""),
                "uk_warehouse": True,
            }
            for i in items
            if i.get("price") and float(i.get("price", 0)) <= max_price_gbp
        ]
    except Exception as e:
        log.warning(f"Costway search failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Wholesale Beds  (wholesalebeds.co.uk — Yagz Ltd trade account)
# No public API; ordering is by phone/email against their price list.
# Catalog mirrored locally in wholesalebeds_catalog.py — update it whenever
# they issue a new price list.
# ---------------------------------------------------------------------------

def _search_wholesalebeds(keyword: str, max_price_gbp: float, category: str = "") -> list[dict]:
    return wholesalebeds_catalog.search(keyword, max_price_gbp, category=category)


# ---------------------------------------------------------------------------
# Wholesale Domestic  (UK furniture specialist — trade account required)
# Authentic API is bespoke; we use their product feed CSV + search endpoint.
# ---------------------------------------------------------------------------

def _search_wholesale_domestic(keyword: str, max_price_gbp: float) -> list[dict]:
    # Wholesale Domestic provides a trade price list feed rather than a REST API.
    # This stub structure matches what we'd parse from their XML/CSV trade feed.
    # Replace with actual feed URL once trade account is set up.
    log.debug("Wholesale Domestic: trade feed integration pending trade account approval")
    return []


# ---------------------------------------------------------------------------
# Birlea Furniture  (UK manufacturer, direct trade dropship)
# ---------------------------------------------------------------------------

def _search_birlea(keyword: str, max_price_gbp: float) -> list[dict]:
    # Birlea trade account application rejected. Not available.
    return []


# ---------------------------------------------------------------------------
# BigBuy  (EU warehouse, ships to UK — fallback for non-furniture)
# ---------------------------------------------------------------------------

def _search_bigbuy(keyword: str, max_price_gbp: float) -> list[dict]:
    if not BIGBUY_API_KEY:
        return []
    url = "https://api.bigbuy.eu/rest/catalog/productsinfo.json"
    headers = {"Authorization": f"Bearer {BIGBUY_API_KEY}"}
    params = {"q": keyword, "language": "en", "pageSize": 5}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        items = resp.json()
        return [
            {
                "supplier": "bigbuy",
                "product_id": str(i.get("id", "")),
                "title": i.get("description", [{}])[0].get("name", keyword) if i.get("description") else keyword,
                "cost_gbp": float(i.get("retailPrice", 0)) * 0.85,  # approx wholesale
                "shipping_gbp": 5.0,
                "delivery_days": 7,
                "images": [i.get("images", [{}])[0].get("url", "")] if i.get("images") else [],
                "description": i.get("description", [{}])[0].get("description", "") if i.get("description") else "",
                "uk_warehouse": False,
            }
            for i in (items if isinstance(items, list) else [])
            if i.get("retailPrice") and float(i.get("retailPrice", 0)) * 0.85 <= max_price_gbp
        ]
    except Exception as e:
        log.warning(f"BigBuy search failed: {e}")
        return []


# ---------------------------------------------------------------------------
# CJ Dropshipping  (global, all categories)
# Auth is a two-step OAuth-style flow: exchange CJ_EMAIL + CJ_API_KEY for a
# temporary accessToken (valid ~6 months), then use that token on every call.
# Cached in-memory for the process lifetime — CJ_API_KEY itself is NOT a
# valid header value, unlike the other suppliers' static API keys.
# ---------------------------------------------------------------------------

_cj_access_token = None


def _get_cj_access_token() -> str | None:
    global _cj_access_token
    if _cj_access_token:
        return _cj_access_token
    if not CJ_EMAIL or not CJ_API_KEY:
        return None
    try:
        resp = requests.post(
            "https://developers.cjdropshipping.com/api2.0/v1/authentication/getAccessToken",
            json={"email": CJ_EMAIL, "password": CJ_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        _cj_access_token = data.get("data", {}).get("accessToken")
        if not _cj_access_token:
            log.error(f"CJ auth returned no access token: {data}")
        return _cj_access_token
    except Exception as e:
        log.error(f"CJ authentication failed: {e}")
        return None


def _parse_cj_price(price) -> float | None:
    """
    CJ's sellPrice is a string, either a single value ("12.99") or a
    variant range ("2.46 -- 2.95"). Use the upper bound for cost estimates
    so margin calculations aren't optimistic about which variant sells.
    """
    if not price:
        return None
    try:
        parts = str(price).split("--")
        return max(float(p.strip()) for p in parts if p.strip())
    except (ValueError, TypeError):
        return None


def _parse_cj_image(val) -> list[str]:
    """
    CJ sometimes returns imageUrl fields as a JSON-encoded array string
    like '["https://...","https://..."]' instead of a plain URL.
    Normalise to a list of valid https:// URLs regardless of format.
    """
    import json as _json
    if not val:
        return []
    if isinstance(val, list):
        urls = []
        for v in val:
            urls.extend(_parse_cj_image(v))
        return urls
    if not isinstance(val, str):
        return []
    val = val.strip()
    if val.startswith("["):
        try:
            parsed = _json.loads(val)
            if isinstance(parsed, list):
                return [u for u in parsed if isinstance(u, str) and u.startswith("http")]
        except Exception:
            pass
    return [val] if val.startswith("http") else []


def _get_cj_cheapest_variant(pid: str) -> dict | None:
    """
    CJ's list endpoint returns a parent product; ordering and freight both
    need a specific variant ID (vid), not the parent pid. Resolve to the
    cheapest variant so search results carry an order-able, freight-able ID.
    """
    token = _get_cj_access_token()
    if not token:
        return None
    headers = {"CJ-Access-Token": token, "Content-Type": "application/json"}
    try:
        resp = requests.get(
            "https://developers.cjdropshipping.com/api2.0/v1/product/query",
            headers=headers, params={"pid": pid}, timeout=15,
        )
        if resp.status_code == 429:
            time.sleep(30)
            resp = requests.get(
                "https://developers.cjdropshipping.com/api2.0/v1/product/query",
                headers=headers, params={"pid": pid}, timeout=15,
            )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        variants = data.get("variants", [])
        if not variants:
            return None
        cheapest = min(variants, key=lambda v: float(v.get("variantSellPrice", "inf") or "inf"))
        # Collect all product images from the detail response
        seen = set()
        product_imgs = []
        def _add(urls):
            for u in _parse_cj_image(urls):
                if u not in seen:
                    seen.add(u)
                    product_imgs.append(u)

        _add(cheapest.get("variantImage", ""))
        for img in data.get("productImages", []):
            if isinstance(img, dict):
                _add(img.get("imageUrl") or img.get("url") or "")
            else:
                _add(img)
        _add(data.get("productImage", ""))
        return {
            "vid": cheapest.get("vid"),
            "sku": cheapest.get("variantSku"),
            "cost_usd": float(cheapest.get("variantSellPrice", 0)),
            "weight_g": float(cheapest.get("variantWeight", 0)),
            "image": cheapest.get("variantImage", ""),
            "product_images": product_imgs[:8],
        }
    except Exception as e:
        log.warning(f"CJ variant lookup failed for pid {pid}: {e}")
        return None


def _get_cj_freight(vid: str, uk_warehouse: bool = False) -> dict | None:
    """
    Real freight quote for one unit.
    UK warehouse items: GB→GB (1-3 day domestic).
    China warehouse items: CN→GB — but we block these at search time, so
    this path only runs if uk_warehouse is explicitly True.
    """
    token = _get_cj_access_token()
    if not token:
        return None
    headers = {"CJ-Access-Token": token, "Content-Type": "application/json"}
    payload = {
        "startCountryCode": "GB" if uk_warehouse else "CN",
        "endCountryCode": "GB",
        "products": [{"quantity": 1, "vid": vid}],
    }
    try:
        resp = requests.post(
            "https://developers.cjdropshipping.com/api2.0/v1/logistic/freightCalculate",
            headers=headers, json=payload, timeout=15,
        )
        if resp.status_code == 429:
            time.sleep(30)
            resp = requests.post(
                "https://developers.cjdropshipping.com/api2.0/v1/logistic/freightCalculate",
                headers=headers, json=payload, timeout=15,
            )
        resp.raise_for_status()
        all_options = resp.json().get("data") or []
        if not all_options:
            return None
        # CJ UK-warehouse domestic shipping often has totalPostageFee=None (included in product cost)
        options_with_fee = [o for o in all_options if o.get("totalPostageFee") is not None]
        if options_with_fee:
            cheapest = min(options_with_fee, key=lambda o: o.get("totalPostageFee") or float("inf"))
        else:
            cheapest = all_options[0]  # shipping included in product price
        return {
            "cost_usd": cheapest.get("totalPostageFee") or 0.0,
            "carrier": cheapest.get("logisticName"),
            "days": cheapest.get("logisticAging"),
        }
    except Exception as e:
        log.warning(f"CJ freight lookup failed for vid {vid}: {e}")
        return None


def _cj_is_relevant(product_title: str, keyword: str) -> bool:
    """
    Two-level relevance check to prevent keyword-product misfires.

    Level 1 (core-term check): the first two significant words of the keyword
    (≥3 chars, not generic) must BOTH appear in the CJ product title.
    This catches cases like "dash cam front rear" matching a bicycle light
    ("front" and "rear" both appear, but "dash" and "cam" don't).

    Level 2 (density check): at least half the keyword words must appear in title.
    """
    _CORE_SKIP = {"and", "or", "for", "of", "in", "to", "the", "from", "with", "set", "sets"}
    stop = {"a", "an", "the", "and", "or", "for", "with", "of", "in", "to", "set"}
    kw_words = [w for w in keyword.lower().split() if w not in stop]
    title_lower = product_title.lower()

    core_terms = [w for w in kw_words if len(w) >= 3 and w not in _CORE_SKIP][:2]
    if len(core_terms) >= 2 and not all(ct in title_lower for ct in core_terms):
        return False

    matches = sum(1 for w in kw_words if w in title_lower)
    return matches >= max(2, len(kw_words) // 2)


def _cj_has_uk_stock(item: dict) -> bool:
    """
    Check CJ product response for confirmed UK (GB) warehouse stock.
    CJ's product list API does not embed warehouse details when countryCode=GB
    is used as a search filter — it returns an empty productWarehouseList.
    Trust the API-level countryCode=GB filter; only reject if warehouse data
    is explicitly present AND shows no GB/UK entry.
    """
    warehouses = item.get("productWarehouseList") or item.get("warehouseList") or []
    if not warehouses:
        return True  # no warehouse detail returned — trust countryCode=GB filter
    for w in warehouses:
        country = (w.get("countryCode") or w.get("country") or "").upper()
        if country in ("GB", "UK"):
            return True
    return False


def _search_cj(keyword: str, max_price_gbp: float) -> list[dict]:
    """
    Search CJ Dropshipping. Tries UK warehouse first (1-5 day delivery).
    If UK warehouse returns nothing, falls back to China warehouse (7-14 day
    delivery) using the CJ Dropship International fulfillment policy.
    """
    token = _get_cj_access_token()
    if not token:
        return []
    url = "https://developers.cjdropshipping.com/api2.0/v1/product/list"
    headers = {"CJ-Access-Token": token, "Content-Type": "application/json"}

    def _do_search(params, uk_only: bool) -> list[dict]:
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code == 429:
                log.debug("CJ rate limit hit — waiting 10s before retry")
                time.sleep(10)
                resp = requests.get(url, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            items = resp.json().get("data", {}).get("list", [])
            results = []
            lookups = 0
            for i in items:
                if lookups >= 8:
                    break
                product_title = i.get("productNameEn", "")
                if not _cj_is_relevant(product_title, keyword):
                    continue
                if uk_only and not _cj_has_uk_stock(i):
                    log.debug(f"CJ UK stock skip: '{product_title[:50]}'")
                    continue
                pid = str(i.get("pid", ""))
                lookups += 1
                if lookups > 1:
                    time.sleep(0.8)
                variant = _get_cj_cheapest_variant(pid)
                if not variant or not variant.get("vid"):
                    continue
                cost_gbp = variant["cost_usd"] * 0.80
                if cost_gbp > max_price_gbp:
                    continue
                freight = _get_cj_freight(variant["vid"], uk_warehouse=uk_only)
                if not freight:
                    continue
                shipping_gbp = (freight["cost_usd"] or 0) * 0.80
                img_list = variant.get("product_images") or []
                if not img_list and i.get("productImage"):
                    img_list = [i["productImage"]]
                results.append({
                    "supplier": "cj",
                    "product_id": variant["vid"],
                    "title": product_title,
                    "cost_gbp": cost_gbp,
                    "shipping_gbp": shipping_gbp,
                    "delivery_days": freight["days"],
                    "images": img_list[:6],
                    "description": "",
                    "category": i.get("categoryName", ""),
                    "listed_count": i.get("listedNum", 0),
                    "uk_warehouse": uk_only,
                })
            return results
        except Exception as e:
            log.warning(f"CJ search failed ({params}): {e}")
            return []

    # Pass 1: UK warehouse only — fast domestic delivery
    uk_results = _do_search(
        {"productNameEn": keyword, "countryCode": "GB", "pageNum": 1, "pageSize": 50},
        uk_only=True,
    )
    if uk_results:
        return uk_results

    # Pass 2: China warehouse fallback — opens the full CJ catalog
    log.debug(f"CJ: no UK warehouse match for '{keyword}' — trying China warehouse")
    return _do_search(
        {"productNameEn": keyword, "pageNum": 1, "pageSize": 50},
        uk_only=False,
    )


# ---------------------------------------------------------------------------
# Margin calculator + minimum viable price
# ---------------------------------------------------------------------------

def _calc_margin(cost_gbp: float, shipping_gbp: float, sell_price_gbp: float,
                  fee_key: str = "everything_else") -> float:
    if sell_price_gbp == 0:
        return 0.0
    total_cost = total_cost_with_vat(cost_gbp, shipping_gbp)
    ebay_fee = calculate_ebay_fees(sell_price_gbp, fee_key)
    net_revenue = sell_price_gbp - ebay_fee - total_cost
    return round((net_revenue / sell_price_gbp) * 100, 2)


def _min_viable_price(cost_gbp: float, shipping_gbp: float, fee_key: str) -> float:
    """Lowest sell price (£X.99 format) that hits MIN_NET_MARGIN_PCT after fees and VAT costs."""
    total_cost = total_cost_with_vat(cost_gbp, shipping_gbp)
    sell = total_cost * 1.2
    for _ in range(2000):
        fees = calculate_ebay_fees(sell, fee_key)
        net = sell - fees
        if sell > 0 and (net - total_cost) / sell >= MIN_NET_MARGIN_PCT / 100:
            break
        sell += 0.50
    import math
    return max(math.ceil(sell) - 0.01, round(total_cost * 1.35, 2))


# ---------------------------------------------------------------------------
# Main matcher
# ---------------------------------------------------------------------------

SUPPLIER_FNS = {
    "wholesalebeds":      _search_wholesalebeds,
    "avasam":             _search_avasam,
    "costway":            _search_costway,
    "wholesale_domestic": _search_wholesale_domestic,
    "birlea":             _search_birlea,
    "bigbuy":             _search_bigbuy,
    "cj":                 _search_cj,
}


def find_best_supplier(keyword: str, target_sell_price_gbp: float, is_furniture: bool = False,
                        fee_key: str = "everything_else", category: str = "") -> dict | None:
    """
    Queries all suppliers in priority order and returns the best match dict, or None.

    fee_key: which EBAY_FVF_TIERS_BY_CATEGORY entry to use for margin calculation.
    category: opportunity category key — forwarded to suppliers that support catalog filtering.

    Returned dict:
    {supplier, product_id, title, cost_gbp, shipping_gbp, total_cost_gbp,
     margin_pct, delivery_days, images, description, uk_warehouse}
    """
    allowed = FURNITURE_ALLOWED_SUPPLIERS if is_furniture else SUPPLIER_PRIORITY
    best = None

    for supplier_key in SUPPLIER_PRIORITY:
        if supplier_key not in allowed:
            continue
        search_fn = SUPPLIER_FNS.get(supplier_key)
        if not search_fn:
            continue

        if supplier_key == "wholesalebeds":
            candidates = search_fn(keyword, target_sell_price_gbp * 0.6, category=category)
        else:
            candidates = search_fn(keyword, target_sell_price_gbp * 0.6)  # cost must be <60% of sell price
        for c in candidates:
            margin = _calc_margin(c["cost_gbp"], c["shipping_gbp"], target_sell_price_gbp, fee_key)
            if margin < MIN_NET_MARGIN_PCT:
                # For UK-warehouse furniture suppliers, don't discard when the market avg
                # is below our minimum viable price. Instead compute the actual floor price
                # and return it flagged — main.py will list above market avg for quality positioning.
                if is_furniture and supplier_key in FURNITURE_ALLOWED_SUPPLIERS:
                    min_price = _min_viable_price(c["cost_gbp"], c["shipping_gbp"], fee_key)
                    if min_price <= target_sell_price_gbp * 1.30:
                        floor_margin = _calc_margin(c["cost_gbp"], c["shipping_gbp"], min_price, fee_key)
                        c["margin_pct"] = round(floor_margin, 2)
                        c["total_cost_gbp"] = round(c["cost_gbp"] + c["shipping_gbp"], 2)
                        c["min_sell_price_gbp"] = min_price
                        c["price_floor_exceeded"] = True
                        if best is None or (best.get("price_floor_exceeded") and floor_margin > best["margin_pct"]):
                            best = c
                            log.info(f"  Furniture floor-price match: {supplier_key} | min_sell=£{min_price:.2f} | margin={c['margin_pct']}%")
                    else:
                        log.debug(f"  {supplier_key}: min viable £{min_price:.2f} > 130% of market £{target_sell_price_gbp} — skip")
                else:
                    log.debug(f"  {supplier_key}: margin {margin:.1f}% < {MIN_NET_MARGIN_PCT}% — skip")
                continue

            c["margin_pct"] = margin
            c["total_cost_gbp"] = round(c["cost_gbp"] + c["shipping_gbp"], 2)

            if best is None or (not best.get("price_floor_exceeded") and margin > best["margin_pct"]) \
                    or best.get("price_floor_exceeded"):
                best = c
                log.info(f"  Best so far: {supplier_key} | margin={margin:.1f}% | cost=£{c['total_cost_gbp']}")

    if best:
        log.info(f"Selected supplier: {best['supplier']} | margin={best['margin_pct']}% | delivery={best['delivery_days']}d")
    else:
        log.info(f"No supplier found for '{keyword}' with ≥{MIN_NET_MARGIN_PCT}% margin at £{target_sell_price_gbp}")

    return best


def place_supplier_order(supplier: str, product_id: str, buyer_address: dict) -> dict | None:
    """
    Place an order with the supplier's API.
    Returns: {supplier_order_id} or None on failure.
    """
    if supplier == "wholesalebeds":
        return _place_wholesalebeds_order(product_id, buyer_address)
    elif supplier == "avasam":
        return _place_avasam_order(product_id, buyer_address)
    elif supplier == "costway":
        return _place_costway_order(product_id, buyer_address)
    elif supplier == "cj":
        return _place_cj_order(product_id, buyer_address)
    elif supplier == "bigbuy":
        return _place_bigbuy_order(product_id, buyer_address)
    else:
        log.warning(f"No order API implemented for supplier '{supplier}' — manual fulfilment required")
        return None


def _place_wholesalebeds_order(product_id: str, addr: dict) -> dict | None:
    """
    Wholesale Beds has no order API — orders go via email to their DHD
    (Direct Home Delivery) desk. This sends that email and returns a
    placeholder order ID so the rest of the pipeline (DB, order status)
    behaves the same as an API-placed order. Tracking has to be entered
    manually once Wholesale Beds dispatches — see get_tracking() below.
    """
    from datetime import datetime, timezone
    from monitor import send_alert

    order_ref = f"WB-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{product_id}"
    body = (
        f"New eBay sale needs a Wholesale Beds order placed manually.\n\n"
        f"Order before 11am for next-day collection.\n"
        f"Email: DHD@WHOLESALEBEDS.CO.UK\n\n"
        f"Internal reference: {order_ref}\n"
        f"Product SKU: {product_id}\n\n"
        f"Deliver to:\n"
        f"{addr.get('name', '')}\n"
        f"{addr.get('line1', '')}\n"
        f"{addr.get('line2', '')}\n"
        f"{addr.get('city', '')}\n"
        f"{addr.get('postcode', '')}\n"
        f"Phone: {addr.get('phone', '')}\n\n"
        f"Remember to supply an email address and mobile number per their DHD process."
    )
    send_alert(subject=f"[ACTION NEEDED] Place Wholesale Beds order {order_ref}", body=body)
    log.warning(f"Wholesale Beds order {order_ref} requires manual placement — alert email sent")
    return {"supplier_order_id": order_ref}


def _place_avasam_order(product_id: str, addr: dict) -> dict | None:
    token = get_avasam_token()
    if not token:
        return None
    headers = {"Authorization": token, "Content-Type": "application/json"}
    payload = {
        "items": [{"sku": product_id, "quantity": 1}],
        "shippingAddress": {
            "fullName": addr.get("name", ""),
            "addressLine1": addr.get("line1", ""),
            "addressLine2": addr.get("line2", ""),
            "city": addr.get("city", ""),
            "postcode": addr.get("postcode", ""),
            "country": addr.get("country", "GB"),
            "phone": addr.get("phone", ""),
        },
    }
    try:
        resp = requests.post(
            "https://app.avasam.com/apiseeker/Order/CreateSellerOrder",
            json=payload, headers=headers, timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        order_id = data.get("orderId") or data.get("order_id") or data.get("id")
        return {"supplier_order_id": str(order_id)}
    except Exception as e:
        log.error(f"Avasam order failed: {e}")
        return None


def _place_costway_order(product_id: str, addr: dict) -> dict | None:
    url = "https://openapi.costway.co.uk/api/order/create"
    headers = {"Authorization": f"Bearer {COSTWAY_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "products": [{"sku": product_id, "qty": 1}],
        "shippingAddress": addr,
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        return {"supplier_order_id": str(resp.json().get("orderId", ""))}
    except Exception as e:
        log.error(f"Costway order failed: {e}")
        return None


def _place_cj_order(product_id: str, addr: dict) -> dict | None:
    token = _get_cj_access_token()
    if not token:
        return None
    url = "https://developers.cjdropshipping.com/api2.0/v1/shopping/order/createOrderV2"
    headers = {"CJ-Access-Token": token, "Content-Type": "application/json"}
    payload = {
        "orderNumber": f"ebay-{product_id[:8]}",
        "shippingAddress": {
            "firstName": addr.get("name", "").split()[0],
            "lastName": " ".join(addr.get("name", "").split()[1:]),
            "phone": addr.get("phone", ""),
            "address": addr.get("line1", ""),
            "address2": addr.get("line2", ""),
            "city": addr.get("city", ""),
            "province": addr.get("county", ""),
            "zip": addr.get("postcode", ""),
            "country": "GB",
        },
        "products": [{"vid": product_id, "quantity": 1}],
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        body = resp.json()
        if not body.get("success"):
            log.error(
                f"CJ order rejected — code={body.get('code')} "
                f"msg={body.get('message')} | payload={payload}"
            )
            return None
        order_id = str((body.get("data") or {}).get("orderId", ""))
        return {"supplier_order_id": order_id}
    except Exception as e:
        log.error(f"CJ order failed: {e}")
        return None


def _place_bigbuy_order(product_id: str, addr: dict) -> dict | None:
    url = "https://api.bigbuy.eu/rest/order/create.json"
    headers = {"Authorization": f"Bearer {BIGBUY_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "order": {
            "internalReference": f"ebay-{product_id[:8]}",
            "language": "en",
            "paymentMethod": "moneybox",
            "carriers": [{"name": "CORREOS"}],
            "shippingAddress": {
                "firstName": addr.get("name", "").split()[0],
                "lastName": " ".join(addr.get("name", "").split()[1:]),
                "postcode": addr.get("postcode", ""),
                "town": addr.get("city", ""),
                "address": addr.get("line1", ""),
                "phone": addr.get("phone", ""),
                "vatNumber": "",
                "country": "GB",
            },
            "products": [{"reference": product_id, "quantity": 1}],
        }
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        return {"supplier_order_id": str(resp.json().get("id", ""))}
    except Exception as e:
        log.error(f"BigBuy order failed: {e}")
        return None


def get_tracking(supplier: str, supplier_order_id: str) -> str | None:
    """Poll supplier for tracking number. Returns tracking string or None."""
    try:
        if supplier == "wholesalebeds":
            # No API to poll — tracking number must be entered manually
            # (db.update_order) once Wholesale Beds emails dispatch confirmation.
            log.debug(f"Wholesale Beds order {supplier_order_id}: tracking is manual, not polled")
            return None
        if supplier == "avasam":
            token = get_avasam_token()
            if not token:
                return None
            resp = requests.post(
                "https://app.avasam.com/apiseeker/OrdersView/SeekerGetOrdersListWithFilter",
                headers={"Authorization": token, "Content-Type": "application/json"},
                json={"orderId": supplier_order_id},
                timeout=10,
            )
            resp.raise_for_status()
            orders = resp.json().get("orders") or resp.json().get("data") or []
            if orders:
                return orders[0].get("trackingNumber") or orders[0].get("tracking_number")
            return None

        elif supplier == "costway":
            url = f"https://openapi.costway.co.uk/api/order/{supplier_order_id}"
            headers = {"Authorization": f"Bearer {COSTWAY_API_KEY}"}
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            return resp.json().get("data", {}).get("trackingNumber")

        elif supplier == "cj":
            token = _get_cj_access_token()
            if not token:
                return None
            url = "https://developers.cjdropshipping.com/api2.0/v1/logistic/track/queryByOrderNumber"
            headers = {"CJ-Access-Token": token}
            resp = requests.get(url, headers=headers, params={"orderNum": supplier_order_id}, timeout=10)
            resp.raise_for_status()
            return resp.json().get("data", {}).get("trackNumber")

        elif supplier == "bigbuy":
            url = f"https://api.bigbuy.eu/rest/order/carriers/{supplier_order_id}.json"
            headers = {"Authorization": f"Bearer {BIGBUY_API_KEY}"}
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            carriers = resp.json()
            if carriers:
                return carriers[0].get("trackingNumber")

    except Exception as e:
        log.warning(f"Tracking fetch failed for {supplier} order {supplier_order_id}: {e}")

    return None

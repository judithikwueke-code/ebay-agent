"""
CJ UK Warehouse Catalogue Sweep.

Instead of keyword searching (which keeps hitting the same ~30 products),
this pages through ALL 10,000+ CJ UK warehouse products, filters by price
viability, and lists anything we haven't listed yet.

Run directly or called from main.py's aggressive growth scheduler.
"""
import logging
import sqlite3
import time

import requests

from db import DB_PATH

log = logging.getLogger(__name__)

CJ_EMAIL = ""
CJ_KEY = ""

TELEGRAM_TOKEN = "8583121219:AAFbpza_GbFcfzjp8_mDZAGgWbZ5sAS9Z14"
TELEGRAM_CHAT = "7681216735"

# Price range: CJ sell price in GBP (we'll mark up 2.2–3x)
MIN_CJ_PRICE = 3.0
MAX_CJ_PRICE = 80.0
TARGET_MARGIN = 0.25
PAGE_SIZE = 50
MAX_PAGES = 60   # 60 * 50 = 3000 products per sweep run
MAX_LISTINGS_PER_SWEEP = 20  # Don't flood eBay in one shot


def _tg(msg: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": msg},
            timeout=10,
        )
    except Exception:
        pass


def _get_cj_token() -> str:
    import os
    email = os.getenv("CJ_EMAIL", CJ_EMAIL)
    key = os.getenv("CJ_API_KEY", CJ_KEY)
    actual_pass = key.split("@api@")[1] if "@api@" in key else key
    resp = requests.post(
        "https://developers.cjdropshipping.com/api2.0/v1/authentication/getAccessToken",
        json={"email": email, "password": actual_pass},
        timeout=10,
    )
    return (resp.json().get("data") or {}).get("accessToken", "")


def _already_listed(cj_product_id: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT 1 FROM products WHERE supplier='cj' AND supplier_product_id=?",
        (cj_product_id,)
    ).fetchone()
    conn.close()
    return row is not None


# Map CJ category keywords → our eBay categories
_CAT_MAP = {
    "hair": "health_beauty",
    "beauty": "health_beauty",
    "skin": "health_beauty",
    "nail": "health_beauty",
    "massage": "health_beauty",
    "yoga": "sports_leisure",
    "fitness": "sports_leisure",
    "exercise": "sports_leisure",
    "gym": "sports_leisure",
    "bike": "sports_leisure",
    "sport": "sports_leisure",
    "garden": "home_living",
    "home": "home_living",
    "kitchen": "home_appliances",
    "cook": "home_appliances",
    "purifier": "home_appliances",
    "cleaner": "home_appliances",
    "vacuum": "home_appliances",
    "pet": "pet_supplies",
    "dog": "pet_supplies",
    "cat": "pet_supplies",
    "baby": "baby_kids",
    "kids": "baby_kids",
    "child": "baby_kids",
    "phone": "tech_accessories",
    "cable": "tech_accessories",
    "charger": "tech_accessories",
    "wireless": "tech_accessories",
    "bluetooth": "tech_accessories",
    "laptop": "tech_accessories",
    "office": "office_stationery",
    "desk": "office_stationery",
    "stationery": "office_stationery",
}


def _guess_category(name: str) -> str:
    name_low = name.lower()
    for kw, cat in _CAT_MAP.items():
        if kw in name_low:
            return cat
    return "home_living"


def _market_check(product_name: str, category_id: str, cj_cost: float, our_est_price: float) -> dict | None:
    """
    Quick eBay market check: get bestMatch competitor count and median price.
    Returns None if product is too saturated or no market found.
    """
    try:
        import os
        from config import get_app_token, EBAY_BROWSE_BASE
        headers = {
            "Authorization": f"Bearer {get_app_token()}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
        }
        min_p = max(cj_cost * 1.5, 5)
        max_p = our_est_price * 3
        resp = requests.get(
            f"{EBAY_BROWSE_BASE}/item_summary/search",
            headers=headers,
            params={
                "q": product_name,
                "category_ids": category_id,
                "filter": f"price:[{min_p:.0f}..{max_p:.0f}],priceCurrency:GBP,buyingOptions:{{FIXED_PRICE}},conditionIds:{{1000}}",
                "sort": "bestMatch",
                "limit": 10,
            },
            timeout=12,
        )
        data = resp.json()
        total = data.get("total", 0)
        items = data.get("itemSummaries", [])
        prices = sorted([float(i["price"]["value"]) for i in items if i.get("price", {}).get("value")])
        if not prices:
            return None
        mid = len(prices) // 2
        median_price = round((prices[mid] + prices[~mid]) / 2, 2)
        return {"price": median_price, "competitors": total}
    except Exception as e:
        log.debug(f"Market check failed for '{product_name}': {e}")
        return None


def run_sweep(dry_run: bool = False) -> int:
    """
    Sweep CJ UK catalogue, list new products.
    Returns count of new listings created.
    """
    from dotenv import load_dotenv
    import os
    load_dotenv()

    from supplier import find_best_supplier
    from lister import create_listing
    from research import _is_policy_blocked, _is_vero
    from config import CATEGORIES

    token = _get_cj_token()
    if not token:
        log.error("CJ sweep: could not get token")
        return 0

    headers = {"CJ-Access-Token": token}
    listed = 0
    checked = 0
    skipped_dup = 0
    skipped_price = 0
    skipped_policy = 0

    log.info(f"=== CJ CATALOGUE SWEEP starting (max {MAX_PAGES} pages, {MAX_LISTINGS_PER_SWEEP} listings) ===")

    for page in range(1, MAX_PAGES + 1):
        if listed >= MAX_LISTINGS_PER_SWEEP:
            break

        try:
            resp = requests.get(
                "https://developers.cjdropshipping.com/api2.0/v1/product/list",
                headers=headers,
                params={
                    "countryCode": "GB",
                    "pageNum": page,
                    "pageSize": PAGE_SIZE,
                },
                timeout=15,
            )
            data = resp.json()
            items = (data.get("data") or {}).get("list") or []
            if not items:
                log.info(f"  Page {page}: no items, stopping")
                break
        except Exception as e:
            log.warning(f"  Page {page} fetch failed: {e}")
            time.sleep(5)
            continue

        for item in items:
            if listed >= MAX_LISTINGS_PER_SWEEP:
                break

            checked += 1
            pid = item.get("pid") or item.get("productId") or item.get("productSku", "")
            name = item.get("productNameEn", "")
            raw_price = str(item.get("sellPrice") or "0")
            # CJ returns ranges like "53.33 -- 65.30" for variant products — take the lower end
            sell_price = float(raw_price.split("--")[0].split("-")[0].strip() or 0)

            if not pid or not name:
                continue

            if sell_price < MIN_CJ_PRICE or sell_price > MAX_CJ_PRICE:
                skipped_price += 1
                continue

            if _already_listed(str(pid)):
                skipped_dup += 1
                continue

            if _is_policy_blocked(name):
                skipped_policy += 1
                continue

            if _is_vero(name):
                skipped_policy += 1
                continue

            cat = _guess_category(name)
            cat_info = CATEGORIES.get(cat, CATEGORIES["home_living"])
            min_price = cat_info.get("min_price", 8.0)
            max_price = cat_info.get("max_price", 200.0)

            # Estimate sell price (markup) — just for supplier matching
            est_sell = round(sell_price * 2.5, 2)
            if est_sell < min_price or est_sell > max_price:
                skipped_price += 1
                continue

            # Try to get a supplier match (will verify stock etc.)
            # Check real market price and competition before spending API calls on supplier match
            market = _market_check(name[:50], cat_info.get("id", "11700"), sell_price, est_sell)
            if market is None:
                skipped_price += 1
                continue
            market_price = market["price"]
            market_competitors = market["competitors"]

            # Too competitive → skip
            if market_competitors > 300:
                log.debug(f"  Skip '{name[:40]}': {market_competitors} sellers")
                skipped_price += 1
                continue

            match = find_best_supplier(
                keyword=name[:40],
                target_sell_price_gbp=market_price,
                is_furniture=False,
                fee_key=cat_info.get("fee_key", "everything_else"),
                category=cat,
            )
            if not match:
                continue

            # Price competitiveness gate: our min viable price must be within 25% of market median
            min_price = match.get("min_sell_price_gbp", 0)
            if min_price > market_price * 1.25:
                log.debug(f"  Price gate FAIL '{name[:40]}': min £{min_price:.2f} vs market £{market_price:.2f}")
                skipped_price += 1
                continue

            est_sell = market_price  # use real market price for listing

            opp = {
                "keyword": name[:60],
                "category": cat,
                "category_id": cat_info["id"],
                "avg_price_gbp": est_sell,
                "active_listings": 0,
                "score": 1.0,
                "sample_title": name,
                "sample_image_url": item.get("productImage", ""),
                "competitor_titles": [name],
            }

            if dry_run:
                log.info(f"  DRY-RUN would list: '{name[:50]}' @ £{est_sell}")
                listed += 1
                continue

            lid = create_listing(opp, match, dry_run=False, target_sell_price_gbp=est_sell)
            if lid:
                listed += 1
                log.info(f"  Sweep listed [{listed}]: '{name[:50]}' @ £{match.get('min_sell_price_gbp', est_sell):.2f}")

        time.sleep(0.5)

    msg = (
        f"CJ SWEEP COMPLETE\n"
        f"  Checked: {checked} products\n"
        f"  New listings: {listed}\n"
        f"  Skipped (duplicate): {skipped_dup}\n"
        f"  Skipped (price/policy): {skipped_price + skipped_policy}\n"
        f"  Pages scanned: {page}"
    )
    log.info(msg)
    _tg(msg)
    return listed


if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from dotenv import load_dotenv
    load_dotenv()
    run_sweep(dry_run=False)

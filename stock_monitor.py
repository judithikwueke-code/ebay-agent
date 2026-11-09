"""
Stock monitor — runs every 6 hours via the scheduler.

For each active CJ listing:
  1. Search CJ by title keywords to find the product
  2. Check inventory count for our stored vid
  3. If not found OR out of stock: pause the eBay listing (qty→0) + Telegram alert
  4. If previously paused and now back in stock: re-enable listing (qty→5)
"""
import os
import re
import sqlite3
import random
import time
import logging
import requests

from config import EBAY_SELL_BASE, get_user_token
from supplier import _get_cj_access_token
from db import DB_PATH

log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

LOW_STOCK_THRESHOLD = 3
RESTOCK_QUANTITY = 5

_STOP = {
    "and","or","for","with","in","of","the","a","an","to","by","from","set",
    "usb","led","uk","pro","new","mini","plus","type","dual","multi","anti",
    "1","2","3","4","5","8","12","20","360","5in1","4in1","2in1","18in1",
}


def _send_telegram(msg: str):
    try:
        from telegram_bot import _send        # retries 3x, checks Telegram's reply, adds the account label
        _send(msg)
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")


def _keywords(title: str) -> str:
    words = re.sub(r"[^a-zA-Z0-9 ]", " ", title).split()
    core = [w for w in words if w.lower() not in _STOP and len(w) >= 4]
    return " ".join(core[:4])


def _cj_headers():
    return {"CJ-Access-Token": _get_cj_access_token()}


def _sell_headers():
    return {
        "Authorization": f"Bearer {get_user_token()}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
        "Content-Language": "en-GB",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _check_cj_stock(vid: str, title: str) -> dict:
    """
    Returns: {found, in_stock, stock_count, pid}
    Searches CJ catalog by title keywords, then verifies the vid exists with stock.
    """
    keyword = _keywords(title)
    headers = _cj_headers()

    def _search(params):
        try:
            r = requests.get(
                "https://developers.cjdropshipping.com/api2.0/v1/product/list",
                headers=headers, params=params, timeout=15,
            )
            if r.status_code == 429:
                log.info("CJ rate limit — waiting 30s")
                time.sleep(30)
                r = requests.get(
                    "https://developers.cjdropshipping.com/api2.0/v1/product/list",
                    headers=headers, params=params, timeout=15,
                )
            return r.json().get("data", {}).get("list", []) or []
        except Exception as e:
            log.warning(f"CJ search error: {e}")
            return []

    for params in [
        {"productNameEn": keyword, "countryCode": "GB", "pageNum": 1, "pageSize": 20},
        {"productNameEn": keyword, "pageNum": 1, "pageSize": 20},
    ]:
        for product in _search(params):
            pid = str(product.get("pid", ""))
            if not pid:
                continue
            time.sleep(0.4)
            try:
                dr = requests.get(
                    "https://developers.cjdropshipping.com/api2.0/v1/product/query",
                    headers=_cj_headers(), params={"pid": pid}, timeout=15,
                )
                if dr.status_code == 429:
                    time.sleep(30)
                    dr = requests.get(
                        "https://developers.cjdropshipping.com/api2.0/v1/product/query",
                        headers=_cj_headers(), params={"pid": pid}, timeout=15,
                    )
                data = dr.json().get("data") or {}
                for v in data.get("variants", []):
                    if str(v.get("vid", "")) == vid:
                        stock = int(v.get("inventoryNum", 0) or 0)
                        return {"found": True, "in_stock": stock > 0,
                                "stock_count": stock, "pid": pid}
            except Exception as e:
                log.debug(f"CJ detail fetch error pid={pid}: {e}")
            time.sleep(0.4)

    return {"found": False, "in_stock": False, "stock_count": 0, "pid": ""}


def _set_ebay_quantity(sku: str, quantity: int) -> bool:
    """Set the available quantity on an eBay inventory item. quantity=0 pauses the listing."""
    url = f"{EBAY_SELL_BASE}/inventory_item/{sku}"
    try:
        existing = requests.get(url, headers=_sell_headers(), timeout=15).json()
        ep = existing.get("product", {})
        payload = {
            "availability": {
                "shipToLocationAvailability": {"quantity": quantity}
            },
            "condition": existing.get("condition", "NEW"),
            "product": {
                "title": ep.get("title", ""),
                "description": ep.get("description", ""),
                "imageUrls": ep.get("imageUrls", []),
                "aspects": ep.get("aspects", {}),
            },
        }
        resp = requests.put(url, headers=_sell_headers(), json=payload, timeout=15)
        ok = resp.status_code in (200, 201, 204)
        # eBay sells from the OFFER's available quantity, so keep that in step too
        offers = requests.get(f"{EBAY_SELL_BASE}/offer", headers=_sell_headers(), params={"sku": sku}, timeout=15).json().get("offers", [])
        if offers:
            ok = True          # the offer update below also sets the item's quantity, so it decides the outcome
        for o in offers:
            r2 = requests.post(
                f"{EBAY_SELL_BASE}/bulk_update_price_quantity", headers=_sell_headers(), timeout=20,
                json={"requests": [{"sku": sku, "shipToLocationAvailability": {"quantity": quantity},
                                    "offers": [{"offerId": o["offerId"], "availableQuantity": quantity}]}]})
            ok = ok and r2.status_code in (200, 201, 204)
        return ok
    except Exception as e:
        log.error(f"eBay quantity update failed for {sku}: {e}")
        return False


def _get_sku(vid: str) -> str:
    return f"cj-{vid}"


def run_stock_monitor():
    """Main entry point — called by the scheduler every 6 hours."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, supplier_product_id, title, ebay_listing_id, status "
        "FROM products WHERE supplier='cj' ORDER BY id"
    ).fetchall()
    conn.close()

    active = [(r[0], r[1], r[2], r[3]) for r in rows if r[4] == "active"]
    paused = [(r[0], r[1], r[2], r[3]) for r in rows if r[4] == "paused_oos"]

    log.info(f"Stock monitor: checking {len(active)} active + {len(paused)} paused listings")

    issues = []

    # --- Check active listings — alert only, never touch the listing ---
    for product_id, vid, title, ebay_id in active:
        log.info(f"Checking: {title[:50]}")
        result = _check_cj_stock(vid, title)

        if not result["found"]:
            log.warning(f"NOT FOUND in CJ (listing kept live): {title[:50]}")
            issues.append(f"⚠️ NOT FOUND in CJ (still live): {title[:50]}\neBay: {ebay_id}")

        elif not result["in_stock"]:
            log.warning(f"OUT OF STOCK at CJ (listing kept live): {title[:50]}")
            issues.append(f"⚠️ OUT OF STOCK at CJ (still live): {title[:50]}\neBay: {ebay_id}")

        elif result["stock_count"] < LOW_STOCK_THRESHOLD:
            log.warning(f"LOW STOCK ({result['stock_count']} units) — {title[:50]}")
            issues.append(f"⚠️ LOW STOCK ({result['stock_count']} left): {title[:50]}\neBay: {ebay_id}")

        else:
            log.info(f"  OK — {result['stock_count']} units in stock")

        time.sleep(1.5)

    if issues:
        msg = (
            f"STOCK ALERT — {len(issues)} listing(s) need attention:\n\n"
            + "\n\n".join(issues)
        )
        _send_telegram(msg)
        log.warning(f"Stock issues found: {len(issues)}")
    else:
        log.info("Stock monitor complete — all listings OK")

    run_amazon_monitor()


def run_amazon_monitor():
    """
    Re-check every live Amazon-sourced listing in a browser. Amazon stock and price move
    constantly and there is no automatic fulfilment, so protect the account:
      - out of stock / page gone  -> eBay quantity 0 (status paused_oos) + alert
      - price rise leaves margin < AMAZON_MIN_MARGIN_PCT -> quantity 0 (paused_price) + alert
      - back in stock at a viable price -> quantity restored (status active) + alert
    """
    import hashlib
    from amazon_source import check_amazon_listing, close as close_amazon
    from supplier import _calc_margin
    from config import DEFAULT_LISTING_QUANTITY, VAT_RATE

    min_margin = float(os.getenv("AMAZON_MIN_MARGIN_PCT", "10"))
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, supplier_product_id, title, listed_price_gbp, status, cost_gbp "
        "FROM products WHERE supplier='amazon' AND status IN ('active','paused_oos','paused_price') ORDER BY id"
    ).fetchall()
    conn.close()
    if not rows:
        return
    log.info(f"Amazon monitor: checking {len(rows)} listing(s)")

    notes = []
    try:
        for pid, asin, title, listed_price, status, cost_exvat in rows:
            sku = hashlib.md5(f"amazon-{asin}".encode()).hexdigest()[:20]
            res = check_amazon_listing(asin)
            if res.get("blocked"):
                log.warning("Amazon monitor stopped early - robot check")
                break
            if res["in_stock"] is None:
                continue  # unreadable page - change nothing
            if res["in_stock"] and res["price"] is not None and status in ("active", "paused_price"):
                new_price, note = _auto_reprice(pid, asin, title, listed_price, cost_exvat, res["price"], VAT_RATE)
                if new_price:
                    if _set_ebay_price(sku, new_price):
                        c2 = sqlite3.connect(DB_PATH)
                        c2.execute("UPDATE products SET listed_price_gbp=?, cost_gbp=? WHERE id=?",
                                   (new_price, round(res["price"] / (1 + VAT_RATE), 2), pid))
                        c2.commit()
                        c2.close()
                        listed_price = new_price          # the viability check below now uses the new price
                        notes.append("Repriced: " + note)
                    else:
                        notes.append("Wanted to reprice but eBay refused: " + note)
                elif note:
                    notes.append(note)
            viable = res["in_stock"] and res["price"] is not None and (
                _calc_margin(res["price"] / (1 + VAT_RATE), 0.0, listed_price or 0) >= min_margin  # shelf price is VAT-inclusive
            )
            if status == "active" and not viable:
                reason = "OUT OF STOCK / gone" if not res["in_stock"] else f"price now £{res['price']:.2f} (margin too low)"
                new_status = "paused_oos" if not res["in_stock"] else "paused_price"
                if _set_ebay_quantity(sku, 0):
                    _update_status(pid, new_status)
                    notes.append(f"Paused on eBay - {reason}: {title[:50]} | https://www.amazon.co.uk/dp/{asin}?th=1&psc=1")
                else:
                    notes.append(f"{reason} but eBay pause FAILED: {title[:50]} | https://www.amazon.co.uk/dp/{asin}?th=1&psc=1")
            elif status != "active" and viable:
                if _set_ebay_quantity(sku, DEFAULT_LISTING_QUANTITY):
                    _update_status(pid, "active")
                    notes.append(f"Back in stock at GBP {res['price']:.2f}, re-enabled: {title[:50]}")
            time.sleep(random.uniform(2, 4))
    finally:
        close_amazon()
    if notes:
        _send_telegram("AMAZON STOCK/PRICE: " + " || ".join(notes))


def replenish_amazon_stock():
    """
    Hourly: any live Amazon-sourced listing whose eBay stock is below STOCK_TARGET is topped back up,
    as far as Amazon really has it (never above its 'only N left'), and never when Amazon is out of stock
    or the price has moved so far that the margin is gone.
    """
    import hashlib
    from amazon_source import check_amazon_listing, close as close_amazon
    from supplier import _calc_margin
    from config import VAT_RATE

    target = int(os.getenv("STOCK_TARGET", "5"))
    min_margin = float(os.getenv("AMAZON_MIN_MARGIN_PCT", "3"))
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, supplier_product_id, title, listed_price_gbp FROM products "
        "WHERE supplier='amazon' AND status='active' AND ebay_listing_id IS NOT NULL").fetchall()
    conn.close()
    low = []
    for pid, asin, title, listed in rows:
        sku = hashlib.md5(f"amazon-{asin}".encode()).hexdigest()[:20]
        try:
            offers = requests.get(f"{EBAY_SELL_BASE}/offer", headers=_sell_headers(), params={"sku": sku}, timeout=15).json().get("offers", [])
        except Exception:
            continue
        qty = offers[0].get("availableQuantity") if offers else None
        if qty is not None and qty < target:
            low.append((pid, asin, title, listed, sku, qty))
    if not low:
        return
    log.info(f"Replenish: {len(low)} listing(s) below {target}")
    try:
        for pid, asin, title, listed, sku, qty in low:
            res = check_amazon_listing(asin)
            if res.get("blocked"):
                log.warning("Replenish stopped early - Amazon robot check")
                break
            if res["in_stock"] is None:
                log.info(f"Replenish: {title[:40]} - could not read the Amazon page this time, left at {qty}")
                continue
            if not res["in_stock"]:
                log.info(f"Replenish: {title[:40]} - Amazon out of stock, left at {qty}")
                continue
            if res["price"] is None or _calc_margin(res["price"] / (1 + VAT_RATE), 0.0, listed or 0) < min_margin:
                log.info(f"Replenish: {title[:40]} - Amazon price {res['price']} leaves no margin, left at {qty}")
                continue
            n = min(target, res.get("max_qty") or target)
            if n > qty:
                if _set_ebay_quantity(sku, n):
                    log.info(f"Replenish: {title[:40]} {qty} -> {n}")
                else:
                    log.warning(f"Replenish: {title[:40]} - eBay refused the quantity change")
            time.sleep(random.uniform(2, 4))
    finally:
        close_amazon()


def _update_status(product_id: int, status: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE products SET status=? WHERE id=?", (status, product_id))
    conn.commit()
    conn.close()


def _set_ebay_price(sku: str, price: float) -> bool:
    """Change the live eBay price of a SKU's offer(s), leaving the quantity exactly as it is."""
    try:
        offers = requests.get(f"{EBAY_SELL_BASE}/offer", headers=_sell_headers(), params={"sku": sku},
                              timeout=15).json().get("offers", [])
        if not offers:
            return False
        ok = True
        for o in offers:
            r = requests.post(
                f"{EBAY_SELL_BASE}/bulk_update_price_quantity", headers=_sell_headers(), timeout=20,
                json={"requests": [{"sku": sku, "offers": [{
                    "offerId": o["offerId"], "availableQuantity": o.get("availableQuantity", 0),
                    "price": {"value": f"{price:.2f}", "currency": "GBP"}}]}]})
            ok = ok and r.status_code in (200, 201, 204) and \
                all(x.get("statusCode", 200) in (200, 201, 204) for x in (r.json().get("responses") or []))
        return ok
    except Exception as e:
        log.error(f"eBay price update failed for {sku}: {e}")
        return False


def _auto_reprice(pid, asin, title, listed_price, cost_exvat, amazon_shelf, vat_rate):
    """
    Amazon's price rose -> raise the eBay price by the same amount (grossed up for the eBay/ad fee that is taken as a
    % of the sale) so the profit we planned at listing time is kept, instead of pausing the listing.
    The recorded cost is the baseline: it only moves UP with Amazon (when Amazon later dips we keep the extra profit,
    and a later return to the old price changes nothing).
    Returns (new_price_or_None, note_or_None). Guards: a rise above AUTO_REPRICE_MAX_PCT % or AUTO_REPRICE_MAX_GBP is more
    likely a wrong variation / one-off spike than a real price change, so it is left to the normal pause logic + alert.
    """
    if os.getenv("AUTO_REPRICE", "1") != "1" or not cost_exvat or not listed_price or amazon_shelf is None:
        return None, None
    base = cost_exvat * (1 + vat_rate)                       # shelf (VAT-inclusive) price we priced from
    delta = amazon_shelf - base
    if delta <= max(0.05, base * 0.005):
        return None, None                                    # unchanged, cheaper, or noise
    max_pct = float(os.getenv("AUTO_REPRICE_MAX_PCT", "25"))
    max_gbp = float(os.getenv("AUTO_REPRICE_MAX_GBP", "8"))
    if delta > max_gbp or (delta > base * max_pct / 100 and delta > 2.0):   # small £ rises on cheap items are fine
        return None, (f"Amazon price jump too big to auto-reprice (£{base:.2f} -> £{amazon_shelf:.2f}) - please check it is "
                      f"the same variation: {title[:50]} | https://www.amazon.co.uk/dp/{asin}?th=1&psc=1")
    ad = float(os.getenv("PROMOTE_RATE", "2.0")) / 100 * 1.2 if os.getenv("PROMOTE_CAMPAIGN_ID") else 0.0
    fee = ad + (0.0 if os.getenv("EBAY_NO_SELLER_FEES") == "1" else 0.15)
    new_price = round(listed_price + delta / (1 - fee), 2)
    return new_price, (f"Amazon £{base:.2f} -> £{amazon_shelf:.2f} (+£{delta:.2f}): eBay price £{listed_price:.2f} -> "
                       f"£{new_price:.2f}, profit kept | {title[:50]}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    run_stock_monitor()

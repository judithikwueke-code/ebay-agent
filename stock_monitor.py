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
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10,
        )
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
        return resp.status_code in (200, 201, 204)
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


def _update_status(product_id: int, status: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE products SET status=? WHERE id=?", (status, product_id))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    run_stock_monitor()

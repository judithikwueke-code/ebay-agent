"""
One-off stock check: verify every active CJ listing is still sourceable.
Searches CJ by title keywords, confirms our vid is still in catalog with stock.
"""
import re
import sqlite3
import time
import requests
import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
log = logging.getLogger(__name__)

from supplier import _get_cj_access_token

DB = "ebay_agent.db"

_STOP = {
    "and","or","for","with","in","of","the","a","an","to","by","from","set",
    "usb","led","uk","pro","new","mini","plus","type","dual","multi","anti",
    "1","2","3","4","5","8","12","20","360","5in1","4in1","2in1","18in1",
}

def _keywords(title: str) -> str:
    words = re.sub(r"[^a-zA-Z0-9 ]", " ", title).split()
    core = [w for w in words if w.lower() not in _STOP and len(w) >= 4]
    return " ".join(core[:4])

def _cj_headers():
    return {"CJ-Access-Token": _get_cj_access_token()}

def check_vid_available(vid: str, title: str) -> dict:
    """
    Returns dict with keys: found (bool), in_stock (bool), stock_count (int), pid (str)
    """
    keyword = _keywords(title)
    headers = _cj_headers()

    def _search(params):
        try:
            r = requests.get(
                "https://developers.cjdropshipping.com/api2.0/v1/product/list",
                headers=headers, params=params, timeout=15
            )
            if r.status_code == 429:
                log.warning("Rate limited — waiting 30s")
                time.sleep(30)
                r = requests.get(
                    "https://developers.cjdropshipping.com/api2.0/v1/product/list",
                    headers=headers, params=params, timeout=15
                )
            return r.json().get("data", {}).get("list", []) or []
        except Exception as e:
            log.warning(f"Search failed: {e}")
            return []

    # Try GB warehouse first, then global
    for params in [
        {"productNameEn": keyword, "countryCode": "GB", "pageNum": 1, "pageSize": 20},
        {"productNameEn": keyword, "pageNum": 1, "pageSize": 20},
    ]:
        products = _search(params)
        for p in products:
            pid = str(p.get("pid", ""))
            if not pid:
                continue
            time.sleep(0.4)
            try:
                dr = requests.get(
                    "https://developers.cjdropshipping.com/api2.0/v1/product/query",
                    headers=_cj_headers(), params={"pid": pid}, timeout=15
                )
                if dr.status_code == 429:
                    time.sleep(30)
                    dr = requests.get(
                        "https://developers.cjdropshipping.com/api2.0/v1/product/query",
                        headers=_cj_headers(), params={"pid": pid}, timeout=15
                    )
                data = dr.json().get("data") or {}
                for v in data.get("variants", []):
                    if str(v.get("vid", "")) == vid:
                        stock = v.get("inventoryNum", 0) or 0
                        return {
                            "found": True,
                            "in_stock": stock > 0,
                            "stock_count": stock,
                            "pid": pid,
                        }
            except Exception as e:
                log.debug(f"Detail fetch failed for pid {pid}: {e}")
            time.sleep(0.3)

    return {"found": False, "in_stock": False, "stock_count": 0, "pid": ""}


def main():
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT id, supplier_product_id, title, ebay_listing_id "
        "FROM products WHERE supplier='cj' AND status='active' ORDER BY id"
    ).fetchall()
    conn.close()

    log.info(f"Checking {len(rows)} active CJ listings...\n")

    ok, low_stock, out_of_stock, not_found = [], [], [], []

    for product_id, vid, title, ebay_id in rows:
        log.info(f"Checking: {title[:55]}")
        result = check_vid_available(vid, title)

        entry = {
            "id": product_id, "vid": vid, "title": title,
            "ebay_id": ebay_id, **result
        }

        if not result["found"]:
            not_found.append(entry)
            log.warning(f"  NOT FOUND in CJ catalog — vid={vid}")
        elif not result["in_stock"]:
            out_of_stock.append(entry)
            log.warning(f"  OUT OF STOCK — pid={result['pid']} stock=0")
        elif result["stock_count"] < 5:
            low_stock.append(entry)
            log.info(f"  LOW STOCK — {result['stock_count']} units | pid={result['pid']}")
        else:
            ok.append(entry)
            log.info(f"  OK — {result['stock_count']} units | pid={result['pid']}")

        time.sleep(1)

    print("\n" + "="*60)
    print(f"RESULTS: {len(ok)} OK | {len(low_stock)} low stock | {len(out_of_stock)} out of stock | {len(not_found)} NOT FOUND")
    print("="*60)

    if not_found or out_of_stock:
        print("\nACTION REQUIRED:")
        for e in not_found:
            print(f"  [NOT FOUND]   eBay {e['ebay_id']} — {e['title'][:55]}")
        for e in out_of_stock:
            print(f"  [OUT OF STOCK] eBay {e['ebay_id']} — {e['title'][:55]}")

    if low_stock:
        print("\nWATCH:")
        for e in low_stock:
            print(f"  [LOW {e['stock_count']}]  eBay {e['ebay_id']} — {e['title'][:55]}")

    return not_found, out_of_stock, low_stock, ok

if __name__ == "__main__":
    main()

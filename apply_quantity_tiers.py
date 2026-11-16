"""One-off: bring the quantity of every live Amazon-sourced listing to config.quantity_for_price(price), so the monthly selling
limit has headroom again. Quantity only - never a price, never a status.   python apply_quantity_tiers.py [--dry]
"""
import hashlib
import os
import sqlite3
import sys
import time

os.chdir("/var/www/ebay-agent-2")
sys.path.insert(0, "/var/www/ebay-agent-2")
import requests  # noqa: E402
import stock_monitor as sm  # noqa: E402
from config import EBAY_SELL_BASE, quantity_for_price  # noqa: E402

DRY = "--dry" in sys.argv
c = sqlite3.connect("ebay_agent2.db", timeout=20)
changed = same = failed = 0
freed = 0.0
for pid, asin, price in c.execute("SELECT id, supplier_product_id, listed_price_gbp FROM products "
                                  "WHERE supplier='amazon' AND status='active' AND ebay_listing_id IS NOT NULL ORDER BY listed_price_gbp DESC").fetchall():
    want = quantity_for_price(price)
    sku = hashlib.md5(f"amazon-{asin}".encode()).hexdigest()[:20]
    try:
        offers = requests.get(f"{EBAY_SELL_BASE}/offer", headers=sm._sell_headers(), params={"sku": sku}, timeout=20).json().get("offers", [])
    except Exception as e:
        failed += 1
        print("  FAIL read", pid, e)
        continue
    for o in offers:
        have = o.get("availableQuantity") or 0
        if have <= want:                       # never RAISE a quantity here (0 = paused; leave it)
            same += 1
            continue
        if DRY:
            changed += 1
            freed += (have - want) * price
            continue
        r = requests.post(f"{EBAY_SELL_BASE}/bulk_update_price_quantity", headers=sm._sell_headers(), timeout=20,
                          json={"requests": [{"sku": sku, "offers": [{"offerId": o["offerId"], "availableQuantity": want}]}]})
        ok = r.status_code == 200 and all(x.get("statusCode", 200) == 200 for x in r.json().get("responses", []))
        if ok:
            changed += 1
            freed += (have - want) * price
            if price > 60:
                print(f"  id {pid:3d} £{price:7.2f}  qty {have} -> {want}")
        else:
            failed += 1
            print("  FAIL", pid, r.status_code, r.text[:160])
        time.sleep(0.3)
print(f"{'WOULD CHANGE' if DRY else 'changed'} {changed}, already at or below target {same}, failed {failed}; listed value freed £{freed:,.0f}")

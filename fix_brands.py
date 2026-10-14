"""
Set each Amazon-sourced listing's Brand / Model item specifics from the real Amazon product page
(earlier listings were all published with Brand = "Unbranded"). Safe to re-run.
    python fix_brands.py [--dry-run]
"""
import hashlib
import sqlite3
import sys

import requests

import amazon_source
from config import EBAY_SELL_BASE
from db import DB_PATH
from lister import _sell_headers


def main(dry_run: bool):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT supplier_product_id, title FROM products WHERE supplier='amazon' "
                        "AND status IN ('active','paused_oos','paused_price')").fetchall()
    conn.close()
    try:
        for asin, title in rows:
            prod = amazon_source.get_product(asin)
            found = (prod or {}).get("aspects") or {}
            if not found.get("Brand"):
                print(f"{asin}: no brand found on Amazon page - unchanged ({title[:40]})")
                continue
            sku = hashlib.md5(f"amazon-{asin}".encode()).hexdigest()[:20]
            url = f"{EBAY_SELL_BASE}/inventory_item/{sku}"
            item = requests.get(url, headers=_sell_headers(), timeout=20).json()
            aspects = item.setdefault("product", {}).setdefault("aspects", {})
            old_brand = aspects.get("Brand")
            aspects["Brand"] = found["Brand"]
            if found.get("Model"):
                aspects["Model"] = found["Model"]
            for k in ("sku", "locale"):
                item.pop(k, None)
            if dry_run:
                print(f"{asin}: Brand {old_brand} -> {found['Brand']} Model {found.get('Model')}")
                continue
            r = requests.put(url, headers=_sell_headers(), json=item, timeout=30)
            print(f"{asin}: Brand {old_brand} -> {found['Brand']} Model {found.get('Model')} [{r.status_code}]")
    finally:
        amazon_source.close()


if __name__ == "__main__":
    main("--dry-run" in sys.argv)

"""
Re-read each Amazon-sourced listing's product page and give the eBay inventory item all its photos
(search-result scraping only captured one). Safe to re-run.
    python fix_images.py [--dry-run]
"""
import hashlib
import logging
import sqlite3
import sys

import requests

import amazon_source
from config import EBAY_SELL_BASE
from db import DB_PATH
from lister import _sell_headers

log = logging.getLogger("fix_images")
MAX_IMAGES = 8


def _good(urls: list[str]) -> list[str]:
    out = []
    for u in urls:
        if not u or "play-icon" in u or "sprite" in u or "transparent-pixel" in u:
            continue
        if u not in out:
            out.append(u)
    return out[:MAX_IMAGES]


def main(dry_run: bool):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT supplier_product_id, title FROM products WHERE supplier='amazon' "
                        "AND status IN ('active','paused_oos','paused_price')").fetchall()
    conn.close()
    try:
        for asin, title in rows:
            prod = amazon_source.get_product(asin)
            imgs = _good(prod["images"]) if prod else []
            if len(imgs) < 2:
                print(f"{asin}: only {len(imgs)} image(s) found on Amazon page - skipped ({title[:40]})")
                continue
            sku = hashlib.md5(f"amazon-{asin}".encode()).hexdigest()[:20]
            url = f"{EBAY_SELL_BASE}/inventory_item/{sku}"
            item = requests.get(url, headers=_sell_headers(), timeout=20).json()
            before = len(item.get("product", {}).get("imageUrls", []))
            item.setdefault("product", {})["imageUrls"] = imgs
            for k in ("sku", "locale", "groupIds", "inventoryItemGroupKeys"):
                item.pop(k, None)
            if dry_run:
                print(f"{asin}: would set {len(imgs)} images (had {before})")
                continue
            r = requests.put(url, headers=_sell_headers(), json=item, timeout=30)
            print(f"{asin}: {before} -> {len(imgs)} images [{r.status_code}] {'' if r.status_code < 300 else r.text[:200]}")
    finally:
        amazon_source.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main("--dry-run" in sys.argv)

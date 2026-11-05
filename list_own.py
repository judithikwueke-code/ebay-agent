"""
List the user's OWN products (their own web store pages) on eBay at the price shown on the page.
No supplier sourcing, no margin/competitiveness gates, no minimum-price floor - the page price is used as-is.

Usage:
    python list_own.py <product-url> <category_key> "<ebay search keyword>" [--price 9.99] [--title "exact eBay title"] [--dry-run]

Reads title / description / images / price from the page's schema.org Product JSON-LD (works for
fabdealz.co.uk). Records the product with supplier 'fabdealz'; orders for it raise a 'manual fulfilment' alert.
"""
import json
import logging
import os
import re
import sys
from urllib.parse import urlsplit

import requests

os.environ.setdefault("SEASONAL_ONLY", "0")
from dotenv import load_dotenv

load_dotenv()
from config import CATEGORIES
from db import init_db, is_product_already_listed
from lister import create_listing
from research import _market_snapshot

log = logging.getLogger("list_own")


def read_product(url: str) -> dict:
    html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=40).text
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            j = json.loads(m.group(1))
        except Exception:
            continue
        if j.get("@type") == "Product":
            return j
    raise RuntimeError("No schema.org Product data found on that page")


def list_one(url: str, category: str, keyword: str, price: float | None = None, dry_run: bool = False,
             title: str | None = None):
    host = (urlsplit(url).netloc.lower().removeprefix("www.").split(".")[0]) or "own"
    p = read_product(url)
    offer = p.get("offers") or {}
    price = price or float(offer.get("price"))
    sku = p.get("sku") or re.sub(r"\W+", "-", p["name"])[:40]
    if is_product_already_listed(host, sku):
        log.warning(f"{sku}: already listed - skipping")
        return None
    images = p.get("image") or []
    images = [images] if isinstance(images, str) else images
    cat = CATEGORIES[category]
    snap = _market_snapshot(keyword, cat["id"], 1.0, 500.0)
    opp = {"keyword": keyword, "category": category, "category_id": cat["id"], "avg_price_gbp": price,
           "active_listings": snap["active_listings"], "score": 0,
           "sample_title": (snap["sample"][0].get("title", keyword) if snap["sample"] else keyword),
           "sample_image_url": images[0] if images else "",
           "competitor_titles": [s.get("title", "") for s in snap["sample"][:8] if s.get("title")]}
    brand = (p.get("brand") or {}).get("name") if isinstance(p.get("brand"), dict) else p.get("brand")
    match = {"supplier": host, "product_id": sku, "title": p["name"], "cost_gbp": 0.0, "shipping_gbp": 0.0,
             "total_cost_gbp": 0.0, "margin_pct": 100.0, "product_url": url.split("?")[0], "images": images[:12],
             "description": p.get("description", ""), "uk_warehouse": True, "delivery_days": 3,
             "aspects": {"Brand": [brand]} if brand else {}, "own_stock": True}
    if title:
        match["title_override"] = title
    lid = create_listing(opp, match, dry_run=dry_run, target_sell_price_gbp=price)
    log.info(f"{sku}: {'would list' if dry_run else 'listed'} at £{price} -> {lid}")
    return lid


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    title = sys.argv[sys.argv.index("--title") + 1] if "--title" in sys.argv else None
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if title:
        args = [a for a in args if a != title]
    dry = "--dry-run" in sys.argv
    price = float(sys.argv[sys.argv.index("--price") + 1]) if "--price" in sys.argv else None
    if price is not None:
        args = [a for a in args if a != str(price)]
    if len(args) < 3:
        print(__doc__)
        sys.exit(1)
    init_db()
    lid = list_one(args[0], args[1], args[2], price, dry, title)
    if lid and not dry:
        try:
            from promote import promote_new_listings
            promote_new_listings()
        except Exception as e:
            log.warning(f"Promotion step skipped: {e}")
    print(f"RESULT {lid}")

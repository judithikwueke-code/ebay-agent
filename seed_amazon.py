"""
List an eBay item from an Amazon UK product YOU choose.

Usage:
    python seed_amazon.py <amazon-url-or-ASIN> "<eBay search keyword>" [category_key] [--dry-run]
    python seed_amazon.py --file amazon_urls.txt [--dry-run]

File format, one per line:  <amazon-url-or-ASIN> | <eBay search keyword> | <category_key (optional)>

The eBay price is set from the live market for the keyword (same rules as the automatic
pipeline); the item is skipped if it can't hit the margin floor or is already listed.
"""
import logging
import re
import sys

from config import CATEGORIES, VAT_RATE
from db import init_db, is_product_already_listed
from lister import create_listing
from research import _market_snapshot, _is_vero, _is_policy_blocked
from supplier import _calc_margin, _min_viable_price
import amazon_source

log = logging.getLogger("seed_amazon")


def _asin(text: str) -> str | None:
    m = re.search(r"(?:/dp/|/gp/product/|^)([A-Z0-9]{10})(?:[/?]|$)", text.strip())
    return m.group(1) if m else None


def seed(url_or_asin: str, keyword: str, category: str = "", dry_run: bool = False) -> bool:
    asin = _asin(url_or_asin)
    if not asin:
        log.error(f"No ASIN found in '{url_or_asin}'")
        return False
    cat_key = category if category in CATEGORIES else "home_living"
    cat = CATEGORIES[cat_key]
    fee_key = cat.get("fee_key", "everything_else")

    if is_product_already_listed("amazon", asin):
        log.info(f"{asin}: already listed - skipping")
        return False
    if _is_policy_blocked(keyword):
        log.warning(f"'{keyword}' matches an eBay restricted term - skipping")
        return False

    prod = amazon_source.get_product(asin)
    if not prod or not prod["cost_gbp"]:
        log.error(f"{asin}: could not read the Amazon page/price")
        return False
    if not prod["in_stock"]:
        log.warning(f"{asin}: not in stock on Amazon - skipping")
        return False
    if _is_vero(prod["title"]) or _is_policy_blocked(prod["title"]):
        log.warning(f"{asin}: brand/policy block on '{prod['title'][:60]}' - skipping")
        return False

    # Same VAT convention as supplier._search_amazon: store the ex-VAT figure.
    shelf = prod["cost_gbp"]
    prod["cost_gbp"] = round(prod["cost_gbp"] / (1 + VAT_RATE), 2)
    prod["shipping_gbp"] = round(prod["shipping_gbp"] / (1 + VAT_RATE), 2)

    snap = _market_snapshot(keyword, cat["id"], cat.get("min_price", 5.0), cat.get("max_price", 400.0))
    market = snap["avg_price_gbp"]
    if not market:
        log.warning(f"'{keyword}': no eBay market data - skipping")
        return False

    min_price = _min_viable_price(prod["cost_gbp"], prod["shipping_gbp"], fee_key)
    margin = _calc_margin(prod["cost_gbp"], prod["shipping_gbp"], market, fee_key)
    prod["margin_pct"] = margin
    prod["total_cost_gbp"] = round(prod["cost_gbp"] + prod["shipping_gbp"], 2)
    prod["min_sell_price_gbp"] = min_price
    log.info(f"{asin}: Amazon £{shelf:.2f} inc VAT | eBay market £{market} | "
             f"floor £{min_price} | margin at market {margin}%")
    if min_price > market * 1.25:
        log.warning(f"{asin}: floor price £{min_price} is >25% above market £{market} - not competitive, skipping")
        return False

    samples = snap["sample"]
    opp = {
        "keyword": keyword, "category": cat_key, "category_id": cat["id"],
        "avg_price_gbp": market, "active_listings": snap["active_listings"], "score": 0,
        "sample_title": samples[0].get("title", keyword) if samples else keyword,
        "sample_image_url": (samples[0].get("image", {}) or {}).get("imageUrl", "") if samples else "",
        "competitor_titles": [s.get("title", "") for s in samples[:8] if s.get("title")],
    }
    target = max(market, min_price)
    listing_id = create_listing(opp, prod, dry_run=dry_run, target_sell_price_gbp=target)
    log.info(f"{asin}: {'would list' if dry_run else 'listed'} -> {listing_id}")
    return bool(listing_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    init_db()
    jobs = []
    if args and args[0] == "--file":
        for line in open(args[1], encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 2:
                    jobs.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    elif len(args) >= 2:
        jobs.append((args[0], args[1], args[2] if len(args) > 2 else ""))
    else:
        print(__doc__)
        sys.exit(1)
    done = sum(seed(u, k, c, dry_run=dry) for u, k, c in jobs)
    amazon_source.close()
    print(f"{done}/{len(jobs)} item(s) {'validated (dry run)' if dry else 'listed'}")

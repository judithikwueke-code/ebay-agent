"""
List the user's OWN stock from a hand-written spec (no product page, no Claude credits needed).

    python list_own_manual.py spec.json [--dry-run]

spec.json: {"sku_id": "BONIO-5X650-BALLS", "title": "...(<=80)", "description_html": "...", "category": "pet_supplies",
            "keyword": "bonio original dog biscuits", "price": 21.99, "cost_inc_vat": 10.90, "quantity": 14, "brand": "Bonio",
            "images": ["/path/a.jpg", ...]}     images are uploaded to eBay's picture hosting (EPS)
Records supplier 'own'; cost stored ex-VAT so order alerts show the real profit.
"""
import json
import logging
import os
import sys

os.environ.setdefault("SEASONAL_ONLY", "0")
from dotenv import load_dotenv

load_dotenv()
import requests
from config import CATEGORIES, get_user_token
from db import init_db, is_product_already_listed
from lister import create_listing
from research import _market_snapshot

log = logging.getLogger("list_own_manual")


def upload_images(paths):
    tok = {"Authorization": "Bearer " + get_user_token()}
    urls = []
    for p in paths:
        with open(p, "rb") as f:
            r = requests.post("https://apim.ebay.com/commerce/media/v1_beta/image/create_image_from_file", headers=tok,
                              files={"image": (os.path.basename(p), f, "image/jpeg")}, timeout=90)
        r.raise_for_status()
        urls.append(requests.get(r.headers["Location"], headers=tok, timeout=30).json()["imageUrl"])
    return urls


def main():
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    dry = "--dry-run" in sys.argv
    init_db()
    sku_id = spec["sku_id"]
    if is_product_already_listed("own", sku_id):
        print("already listed:", sku_id)
        return
    cat = CATEGORIES[spec["category"]]
    snap = _market_snapshot(spec["keyword"], cat["id"], 1.0, 200.0)
    opp = {"keyword": spec["keyword"], "category": spec["category"], "category_id": cat["id"], "avg_price_gbp": spec["price"],
           "active_listings": snap["active_listings"], "score": 0,
           "sample_title": snap["sample"][0].get("title", spec["keyword"]) if snap["sample"] else spec["keyword"],
           "sample_image_url": "", "competitor_titles": [s.get("title", "") for s in snap["sample"][:8] if s.get("title")]}
    images = spec["images"] if dry else upload_images(spec["images"])
    cost_ex = round(spec["cost_inc_vat"] / 1.2, 2)
    match = {"supplier": "own", "product_id": sku_id, "title": spec["title"], "cost_gbp": cost_ex, "shipping_gbp": 0.0,
             "total_cost_gbp": cost_ex, "margin_pct": 0.0, "images": images, "description": spec["description_html"],
             "uk_warehouse": True, "delivery_days": 3, "aspects": {"Brand": [spec.get("brand", "Unbranded")]},
             "own_stock": True, "title_override": spec["title"], "description_override": spec["description_html"]}
    if spec.get("category_id"):
        match["category_id_override"] = spec["category_id"]
    lid = create_listing(opp, match, dry_run=dry, target_sell_price_gbp=spec["price"])
    print("RESULT", lid)
    if lid and not dry:
        qty = spec.get("quantity")
        if qty:
            import hashlib
            from stock_monitor import _set_ebay_quantity
            sku = hashlib.md5(f"own-{sku_id}".encode()).hexdigest()[:20]
            print("quantity set to", qty, "->", _set_ebay_quantity(sku, int(qty)))
        try:
            from promote import promote_new_listings
            promote_new_listings()
        except Exception as e:
            log.warning(f"Promotion step skipped: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    main()

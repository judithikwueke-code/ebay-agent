"""
Turn eBay product pages you opened (captured by the Chrome extension) into listings.

For every captured item that shows real demand (sold today / sold / in baskets / watchers), find the EXACT
same product on Amazon UK (barcode first, then brand+model, then title), check we can undercut the
cheapest quarter of eBay sellers and still earn the minimum profit, and list it.

    python watch_scan.py            # process up to 5 new items (lists for real)
    python watch_scan.py --dry      # show what it would do, list nothing
"""
import json
import logging
import math
import os
import re
import sqlite3
import sys

import amazon_source
from config import VAT_RATE
from db import DB_PATH

log = logging.getLogger("watch_scan")

MIN_SOLD_TODAY = int(os.getenv("WATCH_MIN_SOLD_TODAY", "3"))
MIN_SOLD_TOTAL = int(os.getenv("WATCH_MIN_SOLD", "50"))
MIN_BASKETS = int(os.getenv("WATCH_MIN_BASKETS", "5"))
MIN_WATCHERS = int(os.getenv("WATCH_MIN_WATCHERS", "100"))
MIN_PROFIT = float(os.getenv("MIN_PROFIT_AT_P25", "2"))

_STOP = {"the", "and", "for", "with", "new", "set", "pack", "of", "in", "to", "a", "an", "uk", "free", "fast",
         "delivery", "colours", "colour", "color", "assorted", "mixed"}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) >= 3 and t not in _STOP}


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text.lower()))


def similarity(ebay_title: str, amazon_title: str) -> float:
    a, b = _tokens(ebay_title), _tokens(amazon_title)
    return len(a & b) / len(a | b) if a and b else 0.0


def pack_count(title: str) -> int:
    """How many units the title sells: 'pack of 6', '6 x', '6 pack', '100 pcs'. No wording means 1."""
    t = title.lower()
    for pat in (r"(?:pack|set) of (\d+)", r"pack of (\d+)", r"(\d+)\s?(?:x|pack|pk|pcs|pieces|piece|pairs|pair)\b", r"\bx\s?(\d+)\b"):
        m = re.search(pat, t)
        if m:
            return int(m.group(1))
    return 1


def same_numbers(ebay_title: str, amazon_title: str) -> bool:
    """Sizes / pack counts in the eBay title (18ml, 2x, 100 pcs) must show up in the Amazon title too,
    and both must sell the same number of units."""
    if pack_count(ebay_title) != pack_count(amazon_title):
        return False
    need = _numbers(ebay_title)
    if not need:
        return True
    have = _numbers(amazon_title)
    return len(need & have) / len(need) >= 0.6


def demand_ok(it: dict) -> tuple[bool, str]:
    checks = [("sold today", it.get("sold_today"), MIN_SOLD_TODAY), ("sold", it.get("sold_total"), MIN_SOLD_TOTAL),
              ("in baskets", it.get("in_baskets"), MIN_BASKETS), ("watchers", it.get("watchers"), MIN_WATCHERS)]
    for label, val, need in checks:
        if val is not None and val >= need:
            return True, f"{val} {label}"
    return False, "demand below thresholds"


def find_amazon_match(it: dict) -> tuple[dict | None, str]:
    """Best Amazon candidate for this eBay item, with the reason it was accepted."""
    title = it["title"]
    price = it.get("price") or 20.0
    ceiling = max(price * 3, 15.0) * (1 + VAT_RATE)
    queries = []
    if it.get("ean"):
        queries.append(("barcode", it["ean"]))
    code = next((c for c in (it.get("mpn"), it.get("model"))
                 if c and len(c) >= 4 and any(ch.isdigit() for ch in c)), None)   # ignore junk like "SN"
    if it.get("brand") and code:
        queries.append(("brand+model", f"{it['brand']} {code}"))
    queries.append(("title", " ".join(re.sub(r"[^A-Za-z0-9 ]", " ", title).split()[:10])))

    for how, q in queries:
        cands = amazon_source.search_amazon(q, ceiling, limit=8, strict=False, min_reviews=5)
        best, best_sim = None, 0.0
        for c in cands:
            sim = similarity(title, c["title"])
            if how == "barcode":
                ok = True                                   # Amazon matched the barcode itself
            else:
                ok = sim >= 0.45 and same_numbers(title, c["title"])
                if ok and it.get("brand") and it["brand"].lower() not in c["title"].lower():
                    ok = False
            if ok and (best is None or sim > best_sim):
                best, best_sim = c, sim
        if best:
            return best, f"{how} match (similarity {best_sim:.2f})"
    return None, "no exact match on Amazon"


def _set(item_id: str, status: str, note: str = "", listing_id: str = ""):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE watch_items SET status=?, note=?, listing_id=COALESCE(NULLIF(?,''), listing_id) WHERE item_id=?",
                 (status, note[:300], listing_id, item_id))
    conn.commit()
    conn.close()


def process_item(it: dict, dry: bool = False) -> str:
    ok, why = demand_ok(it)
    if not ok:
        _set(it["item_id"], "low_demand", why)
        return f"low demand ({why})"

    match, how = find_amazon_match(it)
    if not match:
        _set(it["item_id"], "no_match", how)
        return how

    from deal_scan import _ebay_market, _nice
    mk = _ebay_market(it["title"])
    ref = min(mk["p25"], it["price"]) if mk and it.get("price") else (mk["p25"] if mk else it.get("price"))
    if not ref:
        _set(it["item_id"], "error", "no eBay price reference")
        return "no price reference"
    target = (ref - 0.36) / 1.0413
    cost = match["cost_gbp"]                                    # Amazon shelf price (inc VAT, Prime assumed free delivery)
    if target < cost + MIN_PROFIT:
        note = f"Amazon GBP {cost:.2f} vs eBay price to match GBP {target:.2f}: under GBP {MIN_PROFIT:.0f} profit"
        _set(it["item_id"], "not_viable", note)
        return note
    price = max(_nice(target, False), _nice(cost + MIN_PROFIT, True))
    keyword = " ".join(re.sub(r"[^A-Za-z0-9 ]", " ", it["title"]).split()[:6])
    if dry:
        return f"WOULD LIST {match['product_id']} Amazon GBP {cost:.2f} -> eBay GBP {price:.2f} ({how}; {why})"

    from seed_amazon import seed
    listed = seed(match["product_id"], keyword, "any", price=price)
    if listed:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT ebay_listing_id FROM products WHERE supplier='amazon' AND supplier_product_id=?",
                           (match["product_id"],)).fetchone()
        conn.close()
        _set(it["item_id"], "listed", f"{how}; {why}", row[0] if row else "")
        return f"LISTED {match['product_id']} at GBP {price:.2f} ({how}; {why})"
    _set(it["item_id"], "not_listed", f"{match['product_id']}: seed refused (already listed, blocked brand, out of stock or price check)")
    return f"not listed ({match['product_id']}: seed refused)"


def process_watch_items(limit: int = 5, dry: bool = False) -> list[str]:
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM watch_items WHERE status='new' ORDER BY COALESCE(sold_today,0) DESC, "
            "COALESCE(watchers,0) DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
    except Exception as e:                       # table not created yet (nothing captured)
        log.debug(f"no watch items: {e}")
        return []
    out = []
    try:
        for r in rows:
            it = dict(r)
            try:
                res = process_item(it, dry=dry)
            except Exception as e:
                _set(it["item_id"], "error", str(e))
                res = f"error: {e}"
            log.info(f"watch item {it['item_id']} '{it['title'][:40]}': {res}")
            out.append(f"{it['item_id']} {it['title'][:40]}: {res}")
    finally:
        amazon_source.close()
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s - %(message)s")
    for line in process_watch_items(limit=int(os.getenv("WATCH_LIMIT", "5")), dry="--dry" in sys.argv):
        print(line)

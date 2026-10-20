"""
Reverse search: start from Amazon UK best-sellers and keep only the products that eBay buyers
pay clearly more for than Amazon charges - i.e. real room to undercut the cheapest quarter of eBay
rivals and still earn the minimum profit. Then list them through seed_amazon.

    python deal_scan.py [--list N] [--categories kitchen,diy,...]     (default: report only)
"""
import logging
import math
import os
import random
import re
import statistics
import sys
import time

import requests

import amazon_source
from config import EBAY_BROWSE_BASE, get_app_token, CATEGORIES

log = logging.getLogger("deal_scan")

SLUGS = ["kitchen", "diy", "garden", "pet-supplies", "toys", "sports", "beauty", "electronics",
         "officeproduct", "automotive", "lighting"]
MIN_PRICE, MAX_PRICE = 5.0, 80.0
MIN_PROFIT = float(os.getenv("MIN_PROFIT_AT_P25", "2"))
MIN_SOLD_PER_WEEK = float(os.getenv("DEAL_MIN_SOLD_PER_WEEK", "1"))
BLOCK_WORDS = ("capsule", "tablet", "supplement", "vitamin", "knife", "battery pack", "thermometer",
               "oximeter", "baby", "infant", "nappy", "e-cigarette", "vape")


def _nice(x: float, up: bool) -> float:
    n = math.floor(x)
    c = [n - 1 + 0.99, n + 0.49, n + 0.99, n + 1.49]
    c = [v for v in c if (v >= x - 1e-9 if up else v <= x + 1e-9)]
    return round(min(c) if up else max(c), 2)


def scrape_bestsellers(slug: str, limit: int = 40) -> list[dict]:
    if time.time() < amazon_source._blocked_until:
        return []
    out = []
    with amazon_source._lock:
        page = None
        try:
            page = amazon_source._get_context().new_page()
            if not amazon_source._goto(page, f"https://www.amazon.co.uk/gp/bestsellers/{slug}/"):
                return []
            for _ in range(4):  # lazy-loaded grid
                page.mouse.wheel(0, 2500)
                time.sleep(0.8)
            for card in page.locator("div#gridItemRoot, div.zg-grid-general-faceout").all()[:limit * 2]:
                try:
                    href = card.locator("a[href*='/dp/']").first.get_attribute("href") or ""
                    m = re.search(r"/dp/([A-Z0-9]{10})", href)
                    text = card.inner_text()
                    p = re.search(r"£\s*([\d,]+\.\d{2})", text)
                    if not (m and p):
                        continue
                    img = card.locator("img").first
                    title = (img.get_attribute("alt") or "").strip() if img.count() else ""
                    if not title:
                        title = text.split("\n")[1] if "\n" in text else text
                    out.append({"asin": m.group(1), "title": title[:140], "price": float(p.group(1).replace(",", ""))})
                except Exception:
                    continue
        except Exception as e:
            log.warning(f"bestsellers/{slug} failed: {e}")
            amazon_source.close()
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass
    seen, uniq = set(), []
    for o in out:
        if o["asin"] not in seen:
            seen.add(o["asin"])
            uniq.append(o)
    return uniq[:limit]


def _ebay_market(title: str) -> dict | None:
    h = {"Authorization": f"Bearer {get_app_token()}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB"}
    q = " ".join(re.sub(r"[^A-Za-z0-9 ]", " ", title).split()[:7])
    try:
        r = requests.get(f"{EBAY_BROWSE_BASE}/item_summary/search", headers=h, timeout=30,
                         params={"q": q, "limit": 20, "sort": "bestMatch",
                                 "filter": "buyingOptions:{FIXED_PRICE},conditionIds:{1000}"}).json()
    except Exception:
        return None
    items = [i for i in r.get("itemSummaries", []) if i.get("seller", {}).get("username") != "yagz-emporium"]
    if len(items) < 5:
        return None

    def tot(i):
        so = (i.get("shippingOptions") or [{}])[0]
        return float(i["price"]["value"]) + float((so.get("shippingCost") or {}).get("value") or 0)

    prices = sorted(tot(i) for i in items)
    return {"q": q, "p25": prices[len(prices) // 4], "median": statistics.median(prices), "items": items}


def _velocity(items) -> float:
    from research import _sales_velocity
    return _sales_velocity(items, "|".join(i["itemId"] for i in items[:3])) or 0.0


def scan(slugs=None, per_category=40):
    found = []
    for slug in (slugs or SLUGS):
        cands = scrape_bestsellers(slug, per_category)
        log.info(f"{slug}: {len(cands)} best-sellers read")
        for c in cands:
            if not (MIN_PRICE <= c["price"] <= MAX_PRICE):
                continue
            if any(w in c["title"].lower() for w in BLOCK_WORDS):
                continue
            mk = _ebay_market(c["title"])
            if not mk:
                continue
            target = (mk["p25"] - 0.36) / 1.0413
            if target < c["price"] + MIN_PROFIT:
                continue
            price = max(_nice(target, False), _nice(c["price"] + MIN_PROFIT, True))
            vel = _velocity(mk["items"])
            if vel < MIN_SOLD_PER_WEEK:
                continue
            found.append({**c, "keyword": mk["q"], "ebay_p25": round(mk["p25"], 2), "ebay_median": round(mk["median"], 2),
                          "price_to_list": price, "profit": round(price - c["price"], 2), "sold_per_week": round(vel, 1)})
            log.info(f"  VIABLE: {c['title'][:50]} Amazon £{c['price']} -> list £{price} (profit £{price - c['price']:.2f}, ~{vel:.1f}/wk)")
        time.sleep(random.uniform(2, 4))
    return sorted(found, key=lambda f: -f["sold_per_week"])


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    args = sys.argv[1:]
    n_list = int(args[args.index("--list") + 1]) if "--list" in args else 0
    slugs = args[args.index("--categories") + 1].split(",") if "--categories" in args else None
    try:
        found = scan(slugs)
    finally:
        amazon_source.close()
    print(f"\n{len(found)} viable product(s):")
    for f in found:
        print(f"  {f['asin']} | £{f['price']} -> £{f['price_to_list']} (+£{f['profit']}) | {f['sold_per_week']}/wk | {f['title'][:60]}")
    if n_list and found:
        from seed_amazon import seed
        from db import init_db
        init_db()
        done = 0
        for f in found:
            if done >= n_list:
                break
            if seed(f["asin"], f["keyword"], "any", price=f["price_to_list"]):
                done += 1
        amazon_source.close()
        print(f"listed {done}")


if __name__ == "__main__":
    main()

"""
Traffic for this account's listings, from eBay's Analytics API (the numbers behind Seller Hub > Performance > Traffic).

  traffic.py [days]      daily totals for the account, then every listing: impressions, page views, click-through, sales

Also writes traffic_latest.json (one row per listing) so other tools can act on it.
Answers "are buyers not SEEING the listings, not CLICKING them, or not BUYING once they look?"
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

from config import EBAY_ANALYTICS_BASE, get_user_token
from db import get_conn

METRICS = ["LISTING_IMPRESSION_TOTAL", "LISTING_IMPRESSION_SEARCH_RESULTS_PAGE", "LISTING_VIEWS_TOTAL",
           "CLICK_THROUGH_RATE", "SALES_CONVERSION_RATE", "TRANSACTION"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "traffic_latest.json")


def _report(dimension: str, start, end, listing_ids=None) -> list[tuple[str, dict]]:
    flt = f"marketplace_ids:{{EBAY_GB}},date_range:[{start:%Y%m%d}..{end:%Y%m%d}]"
    if listing_ids:
        flt += ",listing_ids:{" + "|".join(listing_ids) + "}"
    r = requests.get(f"{EBAY_ANALYTICS_BASE}/traffic_report",
                     headers={"Authorization": f"Bearer {get_user_token()}", "Accept": "application/json"},
                     params={"dimension": dimension, "filter": flt, "metric": ",".join(METRICS)}, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"traffic_report {r.status_code}: {r.text[:400]}")
    data = r.json()
    keys = [m["key"] for m in data.get("header", {}).get("metrics", [])]
    rows = []
    for rec in data.get("records") or []:
        dim = rec["dimensionValues"][0].get("value")
        vals = {k: (mv.get("value") if mv.get("applicable", True) else None)
                for k, mv in zip(keys, rec.get("metricValues") or [])}
        rows.append((str(dim), vals))
    return rows


def _window(days: int):
    """eBay rejects an end date it has no data for yet - step back a day at a time until it accepts."""
    today = datetime.now(timezone.utc).date()
    for back in (0, 1, 2, 3):
        end = today - timedelta(days=back)
        start = end - timedelta(days=days - 1)
        try:
            _report("DAY", end, end)
            return start, end
        except RuntimeError as e:
            last = e
    raise last


def listing_traffic(days: int = 30) -> list[dict]:
    """One dict per listing in the database (any status), with its traffic over the last `days` days."""
    start, end = _window(days)
    with get_conn() as c:
        prods = [dict(r) for r in c.execute(
            "SELECT id, ebay_listing_id, supplier, supplier_product_id, title, listed_price_gbp, cost_gbp, status, "
            "created_at FROM products WHERE ebay_listing_id IS NOT NULL AND ebay_listing_id != ''")]
        sold = {r[0]: r[1] for r in c.execute(
            "SELECT product_id, COUNT(*) FROM orders WHERE status != 'cancelled' AND created_at >= ? GROUP BY product_id",
            (start.strftime("%Y-%m-%d"),))}
    ids = [str(p["ebay_listing_id"]) for p in prods if str(p["ebay_listing_id"]).isdigit()]
    traffic = {}
    for i in range(0, len(ids), 150):
        for lid, vals in _report("LISTING", start, end, ids[i:i + 150]):
            traffic[lid] = vals
    out = []
    for p in prods:
        t = traffic.get(str(p["ebay_listing_id"]), {})
        out.append({**p, "impressions": int(t.get("LISTING_IMPRESSION_TOTAL") or 0),
                    "search_impressions": int(t.get("LISTING_IMPRESSION_SEARCH_RESULTS_PAGE") or 0),
                    "views": int(t.get("LISTING_VIEWS_TOTAL") or 0),
                    "ctr": float(t.get("CLICK_THROUGH_RATE") or 0),
                    "conversion": float(t.get("SALES_CONVERSION_RATE") or 0),
                    "transactions": int(t.get("TRANSACTION") or 0),
                    "orders_in_db": sold.get(p["id"], 0)})
    return out


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 30
    start, end = _window(days)
    print(f"=== Account traffic by day, {start} .. {end} ===")
    print("day        impressions  views  ctr%   sales")
    for day, v in sorted(_report("DAY", start, end)):
        print(f"{day[:10]:<10} {int(v.get('LISTING_IMPRESSION_TOTAL') or 0):>11} {int(v.get('LISTING_VIEWS_TOTAL') or 0):>6} "
              f"{float(v.get('CLICK_THROUGH_RATE') or 0) * 100:>5.2f} {int(v.get('TRANSACTION') or 0):>6}")

    rows = listing_traffic(days)
    json.dump({"start": str(start), "end": str(end), "rows": rows}, open(OUT, "w"), default=str)
    live = [r for r in rows if r["status"] == "active"]
    print(f"\n=== {len(live)} active listings over the same window ===")
    buckets = [("0", 0, 0), ("1-49", 1, 49), ("50-199", 50, 199), ("200-999", 200, 999), ("1000+", 1000, 10 ** 9)]
    for name, lo, hi in buckets:
        b = [r for r in live if lo <= r["impressions"] <= hi]
        print(f"impressions {name:<8} {len(b):>4} listings   views {sum(r['views'] for r in b):>5}   "
              f"sales {sum(r['transactions'] for r in b):>3}")
    ti, tv, tt = (sum(r[k] for r in live) for k in ("impressions", "views", "transactions"))
    print(f"TOTAL impressions {ti}, views {tv} ({tv / ti * 100 if ti else 0:.2f}% click-through), sales {tt} "
          f"({tt / tv * 100 if tv else 0:.1f}% of views)")
    print("\n=== Most-viewed active listings ===")
    print("views  impr   ctr%  sold  price    listed      title")
    for r in sorted(live, key=lambda r: -r["views"])[:40]:
        print(f"{r['views']:>5} {r['impressions']:>6} {r['ctr'] * 100:>5.2f} {r['transactions']:>4}  {r['listed_price_gbp'] or 0:>7.2f}  "
              f"{str(r['created_at'])[:10]}  {(r['title'] or '')[:58]}")


if __name__ == "__main__":
    main()

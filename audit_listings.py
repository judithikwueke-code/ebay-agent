"""
Audit every ACTIVE Amazon-sourced listing against the live Amazon page (variation-selected, ?th=1&psc=1):
  - current Amazon price vs the cost the agent recorded  -> real profit at the listed eBay price
  - pack-count mismatch between the eBay title ("2 Pack", "pack of 3", "40 Pcs") and the Amazon title
  - out of stock
Report only: writes audit_listings.csv + a Telegram digest. Changes NOTHING on eBay.

Usage:  python audit_listings.py [--limit N]        (run under systemd-run --scope -p MemoryMax=1000M)
"""
import csv
import os
import re
import sqlite3
import sys
import time

os.environ.setdefault("SEASONAL_ONLY", "0")
from dotenv import load_dotenv

load_dotenv()
import amazon_source
from db import DB_PATH

AD_RATE = float(os.getenv("PROMOTE_RATE", "2.0")) / 100 * 1.2          # ad fee incl. VAT, on the sale price
MIN_PROFIT = float(os.getenv("AUDIT_MIN_PROFIT", "1.5"))


def pack_count(title: str):
    t = title.lower()
    for pat in (r"(\d+)\s*-?\s*(?:pack|pk|pcs|pieces|piece|count|ct)\b", r"pack of\s*(\d+)", r"\b(\d+)\s*x\b", r"\bset of\s*(\d+)",
                r"\b(\d+)\s*(?:pairs?|sets?)\b"):
        m = re.search(pat, t)
        if m:
            n = int(m.group(1))
            if 1 < n < 500:
                return n
    if re.search(r"\b(twin|double)\s*pack\b", t):
        return 2
    return None


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT id, ebay_listing_id, supplier_product_id, title, listed_price_gbp, cost_gbp FROM products "
                        "WHERE supplier='amazon' AND status='active' AND ebay_listing_id IS NOT NULL ORDER BY id").fetchall()
    conn.close()
    if limit:
        rows = rows[:limit]
    out = []
    for n, (pid, lid, asin, title, price, cost_exvat) in enumerate(rows, 1):
        prod = None
        for attempt in range(2):                       # one retry: timeouts are common when the box is busy
            prod = amazon_source.get_product(asin)
            if prod:
                break
            time.sleep(3)
        row = {"id": pid, "listing": lid, "asin": asin, "ebay_title": title[:70], "ebay_price": price,
               "db_cost_incl_vat": round((cost_exvat or 0) * 1.2, 2)}
        if not prod:
            row.update(status="UNREADABLE", note="Amazon page could not be read")
        else:
            now = float(prod["cost_gbp"] or 0)
            profit = round(price - now - price * AD_RATE, 2)
            row.update(amazon_price_now=now, in_stock=prod.get("in_stock"), real_profit=profit,
                       margin_pct=round(profit / price * 100, 1) if price else None, amazon_title=prod["title"][:70])
            flags = []
            if not prod.get("in_stock"):
                flags.append("OUT OF STOCK")
            if profit < 0:
                flags.append("LOSS")
            elif profit < MIN_PROFIT:
                flags.append(f"THIN (<£{MIN_PROFIT})")
            if row["db_cost_incl_vat"] and abs(now - row["db_cost_incl_vat"]) / max(now, 0.01) > 0.05:
                flags.append(f"COST DRIFT (db £{row['db_cost_incl_vat']} vs now £{now})")
            pe, pa = pack_count(title), pack_count(prod["title"])
            if pe != pa and (pe or pa):
                flags.append(f"PACK MISMATCH (eBay {pe or 1} vs Amazon {pa or 1})")
            row["status"] = "; ".join(flags) or "OK"
        out.append(row)
        print(f"[{n}/{len(rows)}] {asin} {row['status']}", flush=True)
        time.sleep(1.5)
    amazon_source.close()

    here = os.path.dirname(os.path.abspath(__file__))
    cols = ["id", "listing", "asin", "ebay_title", "ebay_price", "amazon_price_now", "db_cost_incl_vat", "real_profit",
            "margin_pct", "in_stock", "status", "amazon_title"]
    with open(os.path.join(here, "audit_listings.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)

    bad = [r for r in out if r["status"] != "OK"]
    lines = [f"LISTING AUDIT: {len(out)} checked, {len(bad)} need attention"]
    for r in sorted(bad, key=lambda r: (r.get("real_profit") if r.get("real_profit") is not None else -99))[:25]:
        lines.append(f"  {r['asin']} {r['ebay_title'][:38]} | £{r['ebay_price']} | profit {r.get('real_profit')} | {r['status']}")
    from telegram_bot import _send
    _send(chr(10).join(lines))
    print(chr(10).join(lines))


if __name__ == "__main__":
    main()

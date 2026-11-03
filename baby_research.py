"""
Targeted baby/kids toy research at 20+ price band.
Run: python3 baby_research.py
"""
import logging
import sys
import os
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s - %(message)s',
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger('baby_research')

from research import _browse_search, _market_snapshot
from supplier import find_best_supplier
from db import is_product_already_listed
from config import CATEGORIES

TARGET_KEYWORDS = [
    "baby doll with accessories set",
    "wooden dolls house large",
    "kids kitchen playset toy",
    "baby alive doll feeding",
    "remote control car kids toy",
    "ride on car electric kids",
    "toy cash register kids",
    "marble run set kids large",
    "kids art easel double sided",
    "toy doctor set kids",
    "baby walker activity centre",
    "wooden train set kids",
    "kids microscope science set",
    "magnetic tiles building blocks kids",
    "kids walkie talkie set long range",
    "dance mat kids interactive",
    "toy kitchen accessories set large",
    "kids metal detector beach",
    "baby monitors video wifi",
    "kids learning tablet educational",
]

cat_info = CATEGORIES["baby_kids"]
cat_id = cat_info["id"]
results = []

print("Scanning baby/kids toy keywords (target 20+ GBP)...")
print("-" * 60)

for kw in TARGET_KEYWORDS:
    try:
        snap = _market_snapshot(kw, cat_id, min_price=18, max_price=120)
        avg = snap.get("avg_price_gbp", 0)
        active = snap.get("active_listings", 0)
        if avg < 18 or active == 0:
            print(f"  SKIP '{kw}' avg=GBP{avg} active={active}")
            continue
        if active > 500:
            print(f"  CROWDED '{kw}' GBP{avg} {active} sellers")
            continue
        print(f"  OK '{kw}' GBP{avg} {active} sellers")
        results.append({"keyword": kw, "avg_price_gbp": avg, "active": active})
    except Exception as e:
        print(f"  ERROR '{kw}': {e}")

results.sort(key=lambda x: x["avg_price_gbp"] / max(x["active"], 1), reverse=True)
print()
print("=== TOP OPPORTUNITIES ===")
for r in results[:15]:
    score = round(r["avg_price_gbp"] / max(r["active"], 1), 4)
    print(f"  GBP{r['avg_price_gbp']:6.2f} | {r['active']:4d} sellers | score={score:.4f} | {r['keyword']}")

print(f"\nTotal viable keywords: {len(results)}")

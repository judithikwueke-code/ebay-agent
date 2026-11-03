import sys, os
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import search_amazon, check_amazon_listing
from research import _market_snapshot
from config import CATEGORIES

cat_id = CATEGORIES['sports_fitness']['id']

# eBay prices from screenshots
MOUNTAIN_BIKE_EBAY = 259.90
KIDS_BIKE_EBAY = 75.00

# Check Amazon prices for these bikes
searches = [
    ('26 inch fat tire mountain bike 7 speed', 300.0),
    ('fat tyre mountain bike adults 26 inch', 300.0),
    ('pink girls bike kids 14 inch', 100.0),
    ('pink princess girls bicycle kids 12 14 16 inch', 100.0),
]

print("Amazon search results for bikes:")
all_found = {}
for kw, max_p in searches:
    results = search_amazon(kw, max_p)
    for r in results:
        asin = r.get('product_id','')
        cost = r.get('cost_gbp', 0)
        title = r.get('title','')
        if asin and asin not in all_found:
            all_found[asin] = r
            print(f"  {asin} GBP{cost} | {title[:65]}")

print(f"\nTotal: {len(all_found)} unique bikes")

VAT = 1.20

print("\n--- Margin check ---")
for asin, r in all_found.items():
    retail = r.get('cost_gbp', 0)
    title = r.get('title','')[:60]
    is_kids = any(w in title.lower() for w in ['girl', 'kids', 'child', 'princess', 'pink'])
    ebay_price = KIDS_BIKE_EBAY if is_kids else MOUNTAIN_BIKE_EBAY
    ex_vat = round(retail / VAT, 2)
    sell = ebay_price * 0.97  # just under market
    # eBay fee ~10.9% + £0.30
    # Large item: assume extra delivery cost ~£15-25
    delivery_cost = 20
    margin = round(sell - retail - delivery_cost - sell * 0.109 - 0.30, 2)
    tag = 'OK' if margin >= 5 else 'THIN/LOSS'
    print(f"  [{tag}] GBP{retail} Amazon | sell GBP{sell} | delivery cost est GBP{delivery_cost} | margin GBP{margin} | {title}")

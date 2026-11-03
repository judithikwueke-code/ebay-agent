import sys, os
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import search_amazon, check_amazon_listing
from research import _market_snapshot
from config import CATEGORIES, VERO_BRANDS
import re

cat_id = CATEGORIES['baby_kids']['id']

# More targeted searches based on what's visible in the screenshot
searches = [
    ('Daisy Chain dolls pram', 60.0),
    ('Molly Dolly dolls pram', 40.0),
    ('girls toy pram doll stroller', 55.0),
    ('toy doll pram set 5 in 1', 60.0),
    ('pink doll pram pushchair toy', 50.0),
    ('toy pram for dolls girls', 45.0),
    ('dolls travel system toy', 50.0),
    ('baby doll pram accessories set', 45.0),
]

all_products = {}
for kw, max_p in searches:
    results = search_amazon(kw, max_p)
    print(f"'{kw}' -> {len(results)} results")
    for r in results:
        asin = r.get('product_id', '')
        cost = r.get('cost_gbp', 0)
        title = r.get('title', '')
        if asin and asin not in all_products:
            all_products[asin] = r
            print(f"  {asin} GBP{cost} | {title[:65]}")

print(f"\nTotal unique: {len(all_products)}")

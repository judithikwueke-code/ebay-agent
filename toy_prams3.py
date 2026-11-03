import sys, os
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import search_amazon, check_amazon_listing
from research import _market_snapshot
from config import CATEGORIES, VERO_BRANDS
import re

cat_id = CATEGORIES['baby_kids']['id']

# Check eBay market for these toy pram keywords
toy_pram_keywords = [
    'dolls pram toy girls',
    'toy doll stroller set',
    'doll pushchair girls toy',
    'girls doll pram set accessories',
    'dolls pram and accessories',
]

print("eBay market snapshot for toy prams:")
for kw in toy_pram_keywords:
    snap = _market_snapshot(kw, cat_id, min_price=10, max_price=80)
    avg = snap.get('avg_price_gbp', 0)
    active = snap.get('active_listings', 0)
    print(f"  GBP{avg} | {active} sellers | '{kw}'")

print()
# Now check the specific Bayer products we found - Bayer is VeRO, but check eBay market for generic equivalent
# The branded versions sell well but we can't list Bayer branded
# What we need: unbranded/white-label equivalent toy prams

# Check what unbranded/generic toy prams sell for on eBay
unbranded_snaps = [
    ('baby doll pram set girls toy christmas', 'baby_kids'),
    ('doll pram pushchair accessories toy', 'baby_kids'),
    ('girls dolls stroller toy set pink', 'baby_kids'),
]
print("eBay market - unbranded toy prams:")
for kw, cat in unbranded_snaps:
    snap = _market_snapshot(kw, cat_id, min_price=10, max_price=60)
    avg = snap.get('avg_price_gbp', 0)
    active = snap.get('active_listings', 0)
    print(f"  GBP{avg} | {active} sellers | '{kw}'")

# Check the Bayer ASINs we found for stock (even if we can't list Bayer branded, 
# this tells us what the market will bear)
bayer_asins = [
    ('B09HQS7CZG', 39.99, 'Bayer Pink Dolls Stroller Set'),
    ('B08KQ9Q22C', 36.99, 'Bayer Cosy Baby Doll Pram'),
    ('B0BVW42WT6', 33.24, 'deAO Baby Doll Stroller Set'),
]
print()
print("Amazon product page checks:")
for asin, cost, name in bayer_asins:
    check = check_amazon_listing(asin)
    print(f"  {asin} in_stock={check.get('in_stock')} | GBP{cost} | {name[:50]}")

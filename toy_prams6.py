import sys, os, re
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import check_amazon_listing
from research import _market_snapshot
from config import CATEGORIES, VERO_BRANDS

cat_id = CATEGORIES['baby_kids']['id']

# Check if bayer/daisy chain/molly dolly are in VeRO list
for brand in ['bayer', 'daisy chain', 'molly dolly', 'molly']:
    hit = next((b for b in VERO_BRANDS if brand in b.lower() or b.lower() in brand), None)
    print(f"VeRO check '{brand}': {'BLOCKED - ' + hit if hit else 'OK (not in VeRO list)'}")

print()

# Try known toy pram ASINs directly - including Molly Dolly
# Common Molly Dolly ASINs from Amazon.co.uk
test_asins = [
    ('B09V1YCFVQ', 'Molly Dolly My First Pram candidate'),
    ('B0BNK3XPQL', 'Molly Dolly candidate'),
    ('B0DR2SWTZJ', 'Bayer Go Baby Dolls Pram GBP27.99'),
    ('B0CW9PQ9L4', 'Bayer Baby Dolls Pram XEO GBP59.99'),
]

EBAY_AVG_TOY_PRAM = 35.0

print("Checking specific ASINs:")
for asin, note in test_asins:
    check = check_amazon_listing(asin)
    in_stock = check.get('in_stock')
    price = check.get('price_gbp')
    title = check.get('title', '')[:60]
    imgs = check.get('images', [])
    if in_stock:
        cost = price or 0
        sell = round(EBAY_AVG_TOY_PRAM * 0.95, 2)
        margin = round(sell - cost - sell * 0.109 - 0.30, 2) if cost else 0
        print(f"  {asin} | in_stock=True | GBP{price} | margin GBP{margin} | {title}")
    else:
        print(f"  {asin} | in_stock={in_stock} | {note}")

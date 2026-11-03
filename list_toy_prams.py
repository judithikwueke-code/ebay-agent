import sys, os, logging
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

from lister import create_listing
from db import is_product_already_listed
from config import CATEGORIES
from amazon_source import search_amazon, check_amazon_listing

cat_info = CATEGORIES['baby_kids']

VAT = 1.20  # lister adds VAT back internally, so we pass ex-VAT (retail / 1.2)

# Amazon retail price — lister will add 20% VAT back so pass retail/1.2 as cost_gbp
# sell = (retail_cost + 0.30 + 3) / (1 - 0.109)  — target £3 actual profit
def target_price(retail):
    return round((retail + 0.30 + 3) / 0.891, 2)

prams_retail = [
    ('B0DR2SWTZJ', 27.99, 'Bayer Go Baby Dolls Pram Foldable Pushchair for 46cm Dolls Polka Dot Design'),
    ('B08KQ9Q22C', 36.99, 'Bayer Cosy Baby Doll Pram and Matching Baby Doll Accessories for 46cm Dolls'),
    ('B09HQS7CZG', 39.99, 'Bayer Pink Dolls Stroller Set Baby Doll Accessories Pram Set 10 Pieces Girls Toy'),
    ('B0BVW42WT6', 33.24, 'deAO Baby Doll Stroller Set with 13 inch Doll and 18 Piece Accessories Girls Toy'),
    ('B01EYKXD28', 38.49, 'Bayer Trendy Baby Dolls Pram Foldable Pushchair for 46cm Doll'),
    ('B0CW9PQ9L4', 59.99, 'Bayer Baby Dolls Pram XEO Convertible Doll Pushchair for 46cm Dolls'),
]
# Convert to ex-VAT so lister doesn't double-count VAT
prams = [(asin, round(retail/VAT, 2), retail, title) for asin, retail, title in prams_retail]

# Get images from Amazon searches
print("Fetching images from Amazon search...")
img_cache = {}
for kw, max_p in [('Bayer dolls pram toy', 70.0), ('deAO baby doll stroller', 50.0)]:
    for r in search_amazon(kw, max_p):
        asin = r.get('product_id', '')
        imgs = r.get('images', [])
        if asin and imgs:
            img_cache[asin] = imgs

# Also check product pages for better images
for asin, ex_vat_cost, retail, title in prams:
    if asin not in img_cache:
        check = check_amazon_listing(asin)
        imgs = check.get('images', [])
        if imgs:
            img_cache[asin] = imgs

print(f"Images cached for: {list(img_cache.keys())}")
print()

listed = []
for asin, ex_vat_cost, retail, title in prams:
    if is_product_already_listed('amazon', asin):
        print(f"Already listed: {asin}")
        continue

    sell = target_price(retail)       # priced for £3 actual profit on retail cost
    margin = round(sell - retail - sell * 0.109 - 0.30, 2)
    imgs = img_cache.get(asin, [])

    if not imgs:
        print(f"SKIP (no image): {asin} | {title[:50]}")
        continue

    opportunity = {
        'keyword': 'girls dolls pram toy christmas gift',
        'category': 'baby_kids',
        'category_id': cat_info['id'],
        'avg_price_gbp': sell,
        'active_listings': 50,
    }
    supplier_match = {
        'supplier': 'amazon',
        'product_id': asin,
        'title': title,
        'cost_gbp': ex_vat_cost,      # ex-VAT — lister adds 20% back internally
        'shipping_gbp': 0.0,
        'total_cost_gbp': ex_vat_cost,
        'product_url': 'https://www.amazon.co.uk/dp/' + asin,
        'images': imgs,
        'description': title + '. Great Christmas gift for girls. Suitable for ages 3 years and above.',
        'margin_pct': round(margin / sell * 100, 1),
        'uk_warehouse': True,
        'delivery_days': 2,
    }

    print(f"Listing: {title[:60]}")
    print(f"  GBP{retail} -> GBP{sell} | margin GBP{margin} | imgs={len(imgs)}")
    result = create_listing(opportunity, supplier_match, target_sell_price_gbp=sell)
    print(f"  Result: {result}")
    if result:
        listed.append((asin, result, title[:50], retail, sell))
    print()

print(f"\n=== Listed {len(listed)} toy prams ===")
for asin, ebay_id, title, cost, sell in listed:
    print(f"  {ebay_id} | GBP{cost}->GBP{sell} | {title}")

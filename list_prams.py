import sys, os, logging
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

from lister import create_listing
from db import is_product_already_listed
from config import CATEGORIES
from amazon_source import search_amazon

cat_info = CATEGORIES['baby_kids']

# Get images from search
search_results = {}
for kw in ['pushchair travel system baby', 'baby pram pushchair lightweight']:
    for r in search_amazon(kw, 200.0):
        asin = r.get('product_id', '')
        imgs = r.get('images', [])
        if asin and imgs and asin not in search_results:
            search_results[asin] = imgs

print("Images from search:", {k: v[:1] for k, v in search_results.items()})

prams = [
    {
        'asin': 'B0G2LYWGPZ',
        'title': 'YAZOCO 3 in 1 Pram Pushchair with Height Adjustable Handle Travel System',
        'cost': 137.99,
        'sell_price': 192.27,
        'ebay_avg': 187.27,
        'active': 7,
        'description': '3-in-1 pram travel system with height-adjustable handle. Suitable from birth. Includes carrycot, toddler seat and car seat adapter. Lightweight and easy to fold.',
    },
    {
        'asin': 'B0D3PTLYBP',
        'title': 'GLOKID 3 in 1 Prams Travel System Foldable Lightweight Stroller Pushchair',
        'cost': 137.99,
        'sell_price': 192.34,
        'ebay_avg': 187.34,
        'active': 2,
        'description': '3-in-1 travel system pram. Foldable and lightweight pushchair suitable from birth to toddler age. Comes with carrycot and toddler seat.',
    },
    {
        'asin': 'B0GF2T3477',
        'title': 'Bebeconfort Snow Compact Baby Buggy Lightweight Travel Pushchair 0-15kg',
        'cost': 49.99,
        'sell_price': 91.99,
        'ebay_avg': 86.99,
        'active': 5,
        'description': 'Bebeconfort Snow compact baby buggy. Suitable from 0-15kg. Lightweight and compact with easy fold. Great for travel.',
    },
]

listed = []
for p in prams:
    asin = p['asin']
    if is_product_already_listed('amazon', asin):
        print("Already listed:", asin)
        continue
    
    imgs = search_results.get(asin, [])
    if not imgs:
        # Use a placeholder Amazon image URL pattern
        imgs = [f'https://m.media-amazon.com/images/I/{asin}._AC_SL1500_.jpg']
    
    opportunity = {
        'keyword': p['title'][:50],
        'category': 'baby_kids',
        'category_id': cat_info['id'],
        'avg_price_gbp': p['ebay_avg'],
        'active_listings': p['active'],
    }
    supplier_match = {
        'supplier': 'amazon',
        'product_id': asin,
        'title': p['title'],
        'cost_gbp': p['cost'],
        'shipping_gbp': 0.0,
        'total_cost_gbp': p['cost'],
        'product_url': 'https://www.amazon.co.uk/dp/' + asin,
        'images': imgs,
        'description': p['description'],
        'margin_pct': round((p['sell_price'] - p['cost'] - p['sell_price'] * 0.109 - 0.30) / p['sell_price'] * 100, 1),
        'uk_warehouse': True,
        'delivery_days': 2,
    }
    print("Listing:", p['title'][:60], "at GBP"+str(p['sell_price']))
    result = create_listing(opportunity, supplier_match, target_sell_price_gbp=p['sell_price'])
    print("  Result:", result)
    if result:
        listed.append((asin, result, p['title'][:50]))

print()
print("Listed", len(listed), "prams:")
for asin, ebay_id, title in listed:
    print(" ", ebay_id, asin, title)

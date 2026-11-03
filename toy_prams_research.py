import sys, os
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import search_amazon, check_amazon_listing
from research import _market_snapshot
from config import CATEGORIES, VERO_BRANDS
import re

cat_id = CATEGORIES['baby_kids']['id']

searches = [
    ('dolls pram toy girls christmas gift', 50.0),
    ('toy dolls stroller pushchair set', 50.0),
    ('girls dolls pram set accessories', 45.0),
    ('kids toy dolls buggy christmas', 40.0),
]

all_products = {}
for kw, max_p in searches:
    results = search_amazon(kw, max_p)
    for r in results:
        asin = r.get('product_id', '')
        cost = r.get('cost_gbp', 0)
        if asin and asin not in all_products and cost >= 12:
            all_products[asin] = r

print(f"Found {len(all_products)} unique toy pram candidates")
print()

# Check eBay market price and margin for each
viable = []
for asin, r in all_products.items():
    cost = r.get('cost_gbp', 0)
    title = r.get('title', '')[:70]
    imgs = r.get('images', [])
    
    # Skip VeRO brands - word boundary check
    lower = title.lower()
    vero_hit = next((b for b in VERO_BRANDS if re.search(r'\b' + re.escape(b) + r'\b', lower)), None)
    if vero_hit:
        print(f"  VERO '{vero_hit}' | {title[:50]}")
        continue
    
    snap = _market_snapshot(title[:45], cat_id, min_price=8, max_price=80)
    ebay_avg = snap.get('avg_price_gbp', 0)
    active = snap.get('active_listings', 0)
    
    if ebay_avg < cost * 1.1:
        print(f"  THIN margin | Amazon GBP{cost} vs eBay avg GBP{ebay_avg} | {title[:50]}")
        continue
    
    sell_price = round(ebay_avg * 0.95, 2)  # price slightly under market avg
    margin = round(sell_price - cost - sell_price * 0.109 - 0.30, 2)
    margin_pct = round(margin / sell_price * 100, 1)
    
    if margin < 3:
        print(f"  LOW margin GBP{margin} | Amazon GBP{cost} eBay avg GBP{ebay_avg} | {title[:50]}")
        continue
    
    print(f"  OK | Amazon GBP{cost} | eBay avg GBP{ebay_avg} | {active} sellers | margin GBP{margin} ({margin_pct}%) | {title[:55]}")
    viable.append({
        'asin': asin,
        'title': title,
        'cost': cost,
        'sell_price': sell_price,
        'ebay_avg': ebay_avg,
        'active': active,
        'margin': margin,
        'margin_pct': margin_pct,
        'images': imgs,
    })

viable.sort(key=lambda x: x['margin'], reverse=True)
print()
print(f"=== {len(viable)} VIABLE TOY PRAMS ===")
for v in viable:
    print(f"  {v['asin']} | GBP{v['cost']} -> GBP{v['sell_price']} | margin GBP{v['margin']} | {v['title'][:60]}")

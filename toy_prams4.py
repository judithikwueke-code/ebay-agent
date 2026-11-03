import sys, os, re
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import search_amazon, check_amazon_listing
from research import _market_snapshot
from config import CATEGORIES, VERO_BRANDS

cat_id = CATEGORIES['baby_kids']['id']

# Search with lower price ceiling to find sourceable options
searches = [
    ('girls doll pram toy set', 28.0),
    ('baby doll pushchair toy girls', 28.0),
    ('toy dolls buggy pram set accessories', 28.0),
    ('kids doll stroller toy christmas', 28.0),
    ('doll pram toy pink', 28.0),
]

all_products = {}
for kw, max_p in searches:
    results = search_amazon(kw, max_p)
    for r in results:
        asin = r.get('product_id', '')
        cost = r.get('cost_gbp', 0)
        if asin and asin not in all_products and cost >= 10:
            all_products[asin] = r

print(f"Candidates under GBP28: {len(all_products)}")
print()

EBAY_AVG = 34.0  # conservative eBay market avg for toy prams

viable = []
for asin, r in all_products.items():
    cost = r.get('cost_gbp', 0)
    title = r.get('title', '')
    imgs = r.get('images', [])
    
    lower = title.lower()
    vero = next((b for b in VERO_BRANDS if re.search(r'\b' + re.escape(b) + r'\b', lower)), None)
    if vero:
        print(f"  VeRO '{vero}' | GBP{cost} | {title[:55]}")
        continue

    sell = round(EBAY_AVG * 0.95, 2)
    margin = round(sell - cost - sell * 0.109 - 0.30, 2)
    margin_pct = round(margin / sell * 100, 1)

    status = 'OK' if margin >= 4 else 'LOW'
    print(f"  [{status}] {asin} | Amazon GBP{cost} | sell GBP{sell} | margin GBP{margin} ({margin_pct}%) | {title[:55]}")
    if margin >= 4:
        viable.append({'asin': asin, 'title': title, 'cost': cost, 'sell': sell,
                       'margin': margin, 'margin_pct': margin_pct, 'images': imgs})

print()
print(f"=== VIABLE: {len(viable)} ===")
for v in viable:
    print(f"  {v['asin']} GBP{v['cost']} -> GBP{v['sell']} margin GBP{v['margin']} | {v['title'][:60]}")

print()
print("Checking stock on viable items...")
for v in viable:
    check = check_amazon_listing(v['asin'])
    page_imgs = check.get('images', [])
    v['in_stock'] = check.get('in_stock')
    if page_imgs:
        v['images'] = page_imgs
    print(f"  {v['asin']} in_stock={v['in_stock']} images={len(v['images'])}")

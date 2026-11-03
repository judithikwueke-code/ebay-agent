import sys, os, re
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import search_amazon, check_amazon_listing
from research import _market_snapshot
from config import CATEGORIES, VERO_BRANDS

cat_id = CATEGORIES['baby_kids']['id']

# Products we found on Amazon - check eBay market price for each specific title
found = [
    ('B0DR2SWTZJ', 27.99, 'Bayer Go Baby Dolls Pram Foldable Pushchair 46cm Dolls Polka Dot'),
    ('B08KQ9Q22C', 36.99, 'Bayer Cosy Baby Doll Pram Matching Baby Doll Accessories 46cm'),
    ('B09HQS7CZG', 39.99, 'Bayer Pink Dolls Stroller Set Baby Doll Accessories Pram Set 10 Pieces'),
    ('B0BVW42WT6', 33.24, 'deAO Baby Doll Stroller Set 13 inch Doll 18 Piece Accessories'),
    ('B01EYKXD28', 38.49, 'Bayer Trendy Baby Dolls Pram Foldable Pushchair 46cm Doll Fair'),
    ('B0CW9PQ9L4', 59.99, 'Bayer Baby Dolls Pram XEO Convertible Doll Pushchair 46cm Dolls'),
]

print("Checking eBay market price per product + stock:")
print()
viable = []
for asin, amz_cost, title in found:
    # Get eBay market snapshot with the product title as keyword
    snap = _market_snapshot(title[:50], cat_id, min_price=10, max_price=90)
    ebay_avg = snap.get('avg_price_gbp', 0)
    active = snap.get('active_listings', 0)
    
    # Use eBay avg or a flat uplift if no market data
    sell = round(max(ebay_avg * 0.97, amz_cost + 8), 2) if ebay_avg > amz_cost else round(amz_cost * 1.35, 2)
    margin = round(sell - amz_cost - sell * 0.109 - 0.30, 2)
    margin_pct = round(margin / sell * 100, 1)
    
    tag = 'OK' if margin >= 4 else 'THIN'
    print(f"[{tag}] {asin}")
    print(f"  Amazon: GBP{amz_cost} | eBay avg: GBP{ebay_avg} ({active} sellers) | Sell: GBP{sell} | Margin: GBP{margin} ({margin_pct}%)")
    print(f"  {title[:65]}")
    print()
    if margin >= 4:
        viable.append({'asin': asin, 'cost': amz_cost, 'sell': sell,
                       'margin': margin, 'margin_pct': margin_pct, 'title': title, 'ebay_avg': ebay_avg, 'active': active})

print(f"Viable: {len(viable)}")
for v in viable:
    check = check_amazon_listing(v['asin'])
    v['in_stock'] = check.get('in_stock')
    v['images'] = check.get('images', [])
    print(f"  {v['asin']} in_stock={v['in_stock']} imgs={len(v['images'])} | GBP{v['cost']} -> GBP{v['sell']} (GBP{v['margin']})")

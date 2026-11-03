import sys, os
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import search_amazon, check_amazon_listing
from research import _market_snapshot
from config import CATEGORIES

candidates = [
    ('B0G2LYWGPZ', 137.99, 'YAZOCO 3 in 1 Prams Pushchair Travel System'),
    ('B0D3PTLYBP', 137.99, 'GLOKID 3 in 1 Prams Travel System'),
    ('B0GF2T3477', 49.99, 'Bebeconfort Snow Compact Baby Buggy'),
]

cat_id = CATEGORIES['baby_kids']['id']

print("Checking stock + eBay market prices...")
print()

for asin, cost, title in candidates:
    check = check_amazon_listing(asin)
    in_stock = check.get('in_stock')
    images = check.get('images', [])
    
    snap = _market_snapshot(title[:40], cat_id, min_price=30, max_price=300)
    ebay_avg = snap.get('avg_price_gbp', 0)
    ebay_active = snap.get('active_listings', 0)
    
    sell_price = round(ebay_avg + 5, 2) if ebay_avg > 0 else round(cost * 1.4, 2)
    margin = round(sell_price - cost - sell_price * 0.109 - 0.30, 2)
    
    print("ASIN:", asin)
    print("  Title:", title[:60])
    print("  Cost: GBP"+str(cost), "| In stock:", in_stock)
    print("  eBay avg: GBP"+str(ebay_avg), "| Active sellers:", ebay_active)
    print("  Sell at: GBP"+str(sell_price), "| Est margin: GBP"+str(margin))
    print("  Images:", len(images))
    print()

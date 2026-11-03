import sys, os
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import search_amazon, check_amazon_listing

# Search for quality prams/pushchairs that Amazon sells (not the cheap import ones)
searches = [
    ('pushchair travel system baby', 120.0),
    ('baby pram pushchair lightweight', 80.0),
    ('travel system pram and pushchair', 150.0),
    ('baby stroller foldable lightweight', 80.0),
]

all_results = []
seen = set()
for kw, max_price in searches:
    results = search_amazon(kw, max_price)
    for r in results:
        asin = r.get('product_id', '')
        if asin and asin not in seen and r.get('cost_gbp', 0) >= 25:
            seen.add(asin)
            all_results.append((kw, r))

print("Found", len(all_results), "unique pram/pushchair candidates")
print()
for kw, r in all_results[:10]:
    asin = r.get('product_id', '')
    cost = r.get('cost_gbp', 0)
    title = r.get('title', '')[:70]
    print("ASIN:", asin, "GBP"+str(cost))
    print("  ", title[:70])
    print()

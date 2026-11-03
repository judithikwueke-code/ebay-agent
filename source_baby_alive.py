import sys, os
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import search_amazon, check_amazon_listing

print("Searching Amazon for Baby Alive doll...")
results = search_amazon('baby alive doll feeding', 35.0)
print("Found", len(results), "results")
for r in results[:8]:
    asin = r.get('product_id','')
    cost = r.get('cost_gbp', 0)
    title = r.get('title','')[:80]
    print(" ", asin, "GBP"+str(cost), "|", title)

print()
print("Checking stock on top candidates...")
for r in results[:3]:
    asin = r.get('product_id','')
    if not asin:
        continue
    check = check_amazon_listing(asin)
    print(" ", asin, "in_stock="+str(check.get('in_stock')), "blocked="+str(check.get('blocked')))

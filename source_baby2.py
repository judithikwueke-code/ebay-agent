import sys, os
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import search_amazon, check_amazon_listing

# Try broader search terms
keywords = [
    'baby alive feeding doll',
    'baby alive sweet tears doll',
    'interactive baby doll feeding',
    'baby doll feeding bottle',
    'baby alive doll uk',
]

for kw in keywords:
    results = search_amazon(kw, 40.0)
    print("'" + kw + "' ->", len(results), "results")
    for r in results[:3]:
        asin = r.get('product_id','')
        cost = r.get('cost_gbp', 0)
        title = r.get('title','')[:70]
        print("  ", asin, "GBP"+str(cost), "|", title)

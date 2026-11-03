import sys, os
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import search_amazon, check_amazon_listing

# Try exact brand + product name searches like a customer would
searches = [
    'Molly Dolly dolls pram toy',
    'Bayer dolls pram toy',
    'deAO dolls stroller toy set',
    'Hey Play doll pram',
    'girls toy pram christmas gift',
    'childrens toy pram pushchair gift',
    'kids play pram buggy toy',
]

for kw in searches:
    results = search_amazon(kw, 55.0)
    if results:
        print(f"'{kw}' -> {len(results)} results:")
        for r in results[:4]:
            print(f"  {r.get('product_id')} GBP{r.get('cost_gbp')} | {r.get('title','')[:65]}")
    else:
        print(f"'{kw}' -> 0 results")

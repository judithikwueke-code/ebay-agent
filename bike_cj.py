import sys, os
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()

# Check CJ Dropshipping for bikes
from supplier import _search_cj

print("CJ Dropshipping search for bikes...")
for kw in ['mountain bike', 'fat tire bicycle', 'kids bike girls', 'pink bicycle kids']:
    results = _search_cj(kw, 300.0)
    if results:
        print(f"  '{kw}' -> {len(results)} results")
        for r in results[:3]:
            print(f"    {r.get('product_id','')} GBP{r.get('cost_gbp','')} | {r.get('title','')[:60]}")
    else:
        print(f"  '{kw}' -> 0 results")

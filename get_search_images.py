import sys, os
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import search_amazon

results = search_amazon('baby alive doll uk', 40.0)
for r in results:
    print(r.get('product_id'), r.get('cost_gbp'), r.get('images', [])[:2])
